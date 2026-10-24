# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Rotation function of the API origin verification secret (issue #714).

Secrets Manager drives the function through createSecret, setSecret, testSecret and finishSecret.
The properties pinned here:

- createSecret writes ``{current: <new>, previous: <old current>}`` as the pending version, and only
  once the distribution is Deployed, so a value CloudFront edges still send is never dropped by a
  rotation that follows too soon.
- setSecret sets the regional web ACL's two origin verification matches to the new and the old
  value, waits for the change to settle, and only then points the distribution's origin headers at
  the new value. UpdateWebACL replaces the whole web ACL, so every other setting is passed back.
- finishSecret applies the setSecret changes again before it moves AWSCURRENT, so a stack update
  that landed during the rotation (and put either back on the old value) is undone first.
- Every step is idempotent and no step logs a value.

The CloudFront and AWS WAF clients are real botocore clients behind a Stubber, so every request
the function sends is validated against the service model; Secrets Manager is an in-memory fake
that keeps version stages the way the service does during a rotation.
"""
import copy
import json

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from backend.backend.handlers.auth import apiOriginVerifyRotation as rotation

HEADER = "x-vams-origin-verify"
SECRET_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams-origin-verify-AbCdEf"
DISTRIBUTION_ID = "E2EXAMPLE12345"
WEB_ACL_NAME = "webacl-EXAMPLE"
WEB_ACL_ID = "11111111-2222-3333-4444-555555555555"
WEB_ACL_ARN = "arn:aws:wafv2:us-east-1:123456789012:regional/webacl/" + WEB_ACL_NAME + "/" + WEB_ACL_ID

OLD = "oldValue0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRS"
NEW = "newValue0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRS"
CURRENT_TOKEN = "aaaaaaaa-0000-0000-0000-000000000001"
TOKEN = "bbbbbbbb-0000-0000-0000-000000000002"


# ---------------------------------------------------------------------------------------------
# Fakes and fixtures
# ---------------------------------------------------------------------------------------------


def _not_found():
    return ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "none"}}, "GetSecretValue")


class FakeSecretsManager:
    """Version stages as Secrets Manager keeps them while it drives a rotation."""

    def __init__(self, current_secret, rotation_enabled=True, pending_value=None):
        self.rotation_enabled = rotation_enabled
        # The service creates the rotation version, staged AWSPENDING with no value, before it
        # invokes createSecret.
        self.versions = {
            CURRENT_TOKEN: {"stages": ["AWSCURRENT"], "value": json.dumps(current_secret)},
            TOKEN: {"stages": ["AWSPENDING"], "value": pending_value},
        }
        self.calls = []

    def describe_secret(self, SecretId):
        self.calls.append(("describe_secret",))
        return {
            "ARN": SecretId,
            "RotationEnabled": self.rotation_enabled,
            "VersionIdsToStages": {k: list(v["stages"]) for k, v in self.versions.items()},
        }

    def get_secret_value(self, SecretId, VersionId=None, VersionStage=None):
        self.calls.append(("get_secret_value", VersionId, VersionStage))
        for version_id, version in self.versions.items():
            if VersionId and version_id != VersionId:
                continue
            if VersionStage and VersionStage not in version["stages"]:
                continue
            if version["value"] is None:
                raise _not_found()
            return {"ARN": SecretId, "VersionId": version_id, "SecretString": version["value"]}
        raise _not_found()

    def get_random_password(self, PasswordLength, ExcludePunctuation):
        self.calls.append(("get_random_password", PasswordLength, ExcludePunctuation))
        return {"RandomPassword": NEW}

    def put_secret_value(self, SecretId, ClientRequestToken, SecretString, VersionStages):
        self.calls.append(("put_secret_value", ClientRequestToken, VersionStages))
        self.versions[ClientRequestToken] = {"stages": list(VersionStages), "value": SecretString}

    def update_secret_version_stage(self, SecretId, VersionStage, MoveToVersionId, RemoveFromVersionId):
        self.calls.append(("update_secret_version_stage", MoveToVersionId, RemoveFromVersionId))
        self.versions[RemoveFromVersionId]["stages"].remove(VersionStage)
        self.versions[RemoveFromVersionId]["stages"].append("AWSPREVIOUS")
        self.versions[MoveToVersionId]["stages"].append(VersionStage)

    def writes(self):
        return [c for c in self.calls if c[0] in ("put_secret_value", "update_secret_version_stage")]


def _byte_match(value):
    return {
        "ByteMatchStatement": {
            "SearchString": value.encode("utf-8"),
            "FieldToMatch": {"SingleHeader": {"Name": HEADER}},
            "TextTransformations": [{"Priority": 0, "Type": "NONE"}],
            "PositionalConstraint": "EXACTLY",
        }
    }


def _web_acl(first, second):
    """A GetWebACL WebACL shaped like the regional ACL VAMS deploys from the shipped policy."""
    return {
        "Name": WEB_ACL_NAME,
        "Id": WEB_ACL_ID,
        "ARN": WEB_ACL_ARN,
        "DefaultAction": {"Allow": {}},
        "Description": "Basic WAF",
        "Rules": [
            {
                "Name": "AWS-AWSManagedRulesCommonRuleSet",
                "Priority": 1,
                "Statement": {
                    "ManagedRuleGroupStatement": {
                        "VendorName": "AWS",
                        "Name": "AWSManagedRulesCommonRuleSet",
                        "RuleActionOverrides": [
                            {"Name": "SizeRestrictions_BODY", "ActionToUse": {"Count": {}}}
                        ],
                    }
                },
                "OverrideAction": {"None": {}},
                "VisibilityConfig": {
                    "SampledRequestsEnabled": True,
                    "CloudWatchMetricsEnabled": True,
                    "MetricName": "AWS-AWSManagedRulesCommonRuleSet",
                },
            },
            {
                "Name": "VAMS-RateLimit",
                "Priority": 10,
                "Statement": {
                    "RateBasedStatement": {
                        "Limit": 10000,
                        "AggregateKeyType": "IP",
                        "ScopeDownStatement": {
                            "NotStatement": {
                                "Statement": {
                                    "OrStatement": {"Statements": [_byte_match(first), _byte_match(second)]}
                                }
                            }
                        },
                    }
                },
                "Action": {
                    "Block": {
                        "CustomResponse": {
                            "ResponseCode": 429,
                            "CustomResponseBodyKey": "VamsRateLimitBody",
                            "ResponseHeaders": [
                                {"Name": "Access-Control-Expose-Headers", "Value": "Retry-After"},
                                {"Name": "Retry-After", "Value": "1"},
                            ],
                        }
                    }
                },
                "VisibilityConfig": {
                    "SampledRequestsEnabled": True,
                    "CloudWatchMetricsEnabled": True,
                    "MetricName": "VAMS-RateLimit",
                },
            },
        ],
        "VisibilityConfig": {
            "SampledRequestsEnabled": True,
            "CloudWatchMetricsEnabled": True,
            "MetricName": "WAFACLGlobal",
        },
        "Capacity": 931,
        "LabelNamespace": "awswaf:123456789012:webacl:" + WEB_ACL_NAME + ":",
        "CustomResponseBodies": {
            "VamsRateLimitBody": {
                "ContentType": "APPLICATION_JSON",
                "Content": '{"message": "Rate limit exceeded. Please retry shortly."}',
            }
        },
        "ManagedByFirewallManager": False,
    }


def _expected_update(web_acl, lock_token="lock-1"):
    """What UpdateWebACL must receive: every setting of the web ACL, read-only fields left out."""
    expected = {
        k: v
        for k, v in web_acl.items()
        if k not in ("ARN", "Capacity", "LabelNamespace", "ManagedByFirewallManager")
    }
    expected.update(Scope="REGIONAL", LockToken=lock_token)
    return expected


def _origin(origin_id, path, headers):
    return {
        "Id": origin_id,
        "DomainName": "abc123.execute-api.us-east-1.amazonaws.com",
        "OriginPath": path,
        "CustomHeaders": {"Quantity": len(headers), "Items": headers},
        "CustomOriginConfig": {
            "HTTPPort": 80,
            "HTTPSPort": 443,
            "OriginProtocolPolicy": "https-only",
            "OriginReadTimeout": 30,
        },
    }


def _distribution_config(value):
    return {
        "CallerReference": "vams-test",
        "Comment": "",
        "Enabled": True,
        "Origins": {
            "Quantity": 3,
            "Items": [
                {
                    "Id": "webapp-s3",
                    "DomainName": "webapp.s3.us-east-1.amazonaws.com",
                    "OriginPath": "",
                    "CustomHeaders": {"Quantity": 0},
                    "S3OriginConfig": {"OriginAccessIdentity": ""},
                },
                _origin("api-stage", "/api", [{"HeaderName": HEADER, "HeaderValue": value}]),
                _origin(
                    "api-catch-all",
                    "",
                    [
                        {"HeaderName": "x-other", "HeaderValue": "unchanged"},
                        {"HeaderName": HEADER, "HeaderValue": value},
                    ],
                ),
            ],
        },
        "DefaultCacheBehavior": {"TargetOriginId": "webapp-s3", "ViewerProtocolPolicy": "redirect-to-https"},
        "WebACLId": "arn:aws:wafv2:us-east-1:123456789012:global/webacl/cf/22222222-3333-4444-5555-666666666666",
    }


def _distribution(status):
    return {
        "Distribution": {
            "Id": DISTRIBUTION_ID,
            "ARN": "arn:aws:cloudfront::123456789012:distribution/" + DISTRIBUTION_ID,
            "Status": status,
            "LastModifiedTime": "2026-10-24T00:00:00Z",
            "InProgressInvalidationBatches": 0,
            "DomainName": "d111111abcdef8.cloudfront.net",
            "ActiveTrustedSigners": {"Enabled": False, "Quantity": 0},
            "DistributionConfig": _distribution_config(OLD),
        },
        "ETag": "E1",
    }


def _client(service):
    return boto3.client(
        service,
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )


@pytest.fixture
def services(monkeypatch):
    cloudfront = _client("cloudfront")
    waf = _client("wafv2")
    cloudfront_stub = Stubber(cloudfront)
    waf_stub = Stubber(waf)
    sleeps = []
    order = []
    monkeypatch.setattr(rotation, "DISTRIBUTION_ID", DISTRIBUTION_ID)
    monkeypatch.setattr(rotation, "WEB_ACL_ARN", WEB_ACL_ARN)
    monkeypatch.setattr(rotation.time, "sleep", lambda seconds: sleeps.append(seconds))
    cloudfront.meta.events.register(
        "before-parameter-build.cloudfront.UpdateDistribution", lambda **kw: order.append("cloudfront")
    )
    waf.meta.events.register(
        "before-parameter-build.wafv2.UpdateWebACL", lambda **kw: order.append("waf")
    )
    monkeypatch.setattr(rotation, "cloudfront_client", cloudfront)
    monkeypatch.setattr(rotation, "waf_client", waf)

    def use_secrets(fake):
        monkeypatch.setattr(rotation, "secrets_client", fake)
        return fake

    with cloudfront_stub, waf_stub:
        yield {
            "use_secrets": use_secrets,
            "cloudfront": cloudfront_stub,
            "waf": waf_stub,
            "sleeps": sleeps,
            "order": order,
        }
        cloudfront_stub.assert_no_pending_responses()
        waf_stub.assert_no_pending_responses()


def _use_secrets(services, fake):
    return services["use_secrets"](fake)


def _event(step):
    return {"SecretId": SECRET_ARN, "ClientRequestToken": TOKEN, "Step": step}


# ---------------------------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------------------------


@pytest.mark.unit
class TestWebAclRules:
    def test_sets_both_matches_in_order(self):
        rules = _web_acl(OLD, OLD)["Rules"]
        assert rotation.set_web_acl_origin_verify_values(rules, (NEW, OLD)) == (1, True)
        statements = rules[1]["Statement"]["RateBasedStatement"]["ScopeDownStatement"]["NotStatement"][
            "Statement"
        ]["OrStatement"]["Statements"]
        assert [s["ByteMatchStatement"]["SearchString"] for s in statements] == [
            NEW.encode(),
            OLD.encode(),
        ]

    def test_already_set_reports_no_change(self):
        rules = _web_acl(NEW, OLD)["Rules"]
        assert rotation.set_web_acl_origin_verify_values(rules, (NEW, OLD)) == (1, False)

    def test_other_rules_are_untouched(self):
        rules = _web_acl(OLD, OLD)["Rules"]
        managed = copy.deepcopy(rules[0])
        rotation.set_web_acl_origin_verify_values(rules, (NEW, OLD))
        assert rules[0] == managed

    def test_a_rate_rule_without_the_scope_down_is_not_counted(self):
        rules = _web_acl(OLD, OLD)["Rules"]
        del rules[1]["Statement"]["RateBasedStatement"]["ScopeDownStatement"]
        assert rotation.set_web_acl_origin_verify_values(rules, (NEW, OLD)) == (0, False)


@pytest.mark.unit
class TestDistributionConfig:
    def test_sets_every_origin_header_and_nothing_else(self):
        config = _distribution_config(OLD)
        assert rotation.set_distribution_origin_verify_value(config, NEW) == (2, True)
        expected = _distribution_config(NEW)
        assert config == expected
        assert config["Origins"]["Items"][2]["CustomHeaders"]["Items"][0]["HeaderValue"] == "unchanged"

    def test_already_set_reports_no_change(self):
        assert rotation.set_distribution_origin_verify_value(_distribution_config(NEW), NEW) == (2, False)


@pytest.mark.unit
class TestWebAclArn:
    def test_regional_arn(self):
        assert rotation.parse_web_acl_arn(WEB_ACL_ARN) == (WEB_ACL_NAME, WEB_ACL_ID, "REGIONAL")

    @pytest.mark.parametrize("arn", ["", "arn:aws:wafv2:us-east-1:123456789012:regional/ipset/a/b", "not-an-arn"])
    def test_other_arns_are_rejected(self, arn):
        with pytest.raises(rotation.RotationError):
            rotation.parse_web_acl_arn(arn)

    def test_update_arguments_keep_every_setting_and_drop_read_only_fields(self):
        web_acl = _web_acl(NEW, OLD)
        members = _client("wafv2").meta.service_model.operation_model("UpdateWebACL").input_shape.members
        assert rotation.update_web_acl_arguments(web_acl, "lock-1", "REGIONAL", members.keys()) == (
            _expected_update(web_acl)
        )


# ---------------------------------------------------------------------------------------------
# Rotation steps
# ---------------------------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateSecret:
    def test_writes_new_current_and_old_as_previous(self, services):
        fake = _use_secrets(services, FakeSecretsManager({"current": OLD}))
        services["cloudfront"].add_response("get_distribution", _distribution("Deployed"), {"Id": DISTRIBUTION_ID})
        rotation.lambda_handler(_event("createSecret"), None)
        assert ("get_random_password", 64, True) in fake.calls
        assert json.loads(fake.versions[TOKEN]["value"]) == {"current": NEW, "previous": OLD}
        assert fake.versions[TOKEN]["stages"] == ["AWSPENDING"]

    def test_waits_while_an_earlier_distribution_change_propagates(self, services):
        fake = _use_secrets(services, FakeSecretsManager({"current": OLD}))
        services["cloudfront"].add_response("get_distribution", _distribution("InProgress"), {"Id": DISTRIBUTION_ID})
        with pytest.raises(rotation.RotationError):
            rotation.lambda_handler(_event("createSecret"), None)
        assert fake.writes() == []

    def test_an_existing_pending_version_is_kept(self, services):
        pending = json.dumps({"current": NEW, "previous": OLD})
        fake = _use_secrets(services, FakeSecretsManager({"current": OLD}, pending_value=pending))
        rotation.lambda_handler(_event("createSecret"), None)
        assert fake.writes() == []


@pytest.mark.unit
class TestSetSecret:
    def _pending(self, services):
        return _use_secrets(
            services,
            FakeSecretsManager({"current": OLD}, pending_value=json.dumps({"current": NEW, "previous": OLD})),
        )

    def test_web_acl_first_then_settle_then_distribution(self, services):
        self._pending(services)
        get_response = {"WebACL": _web_acl(OLD, OLD), "LockToken": "lock-1"}
        services["waf"].add_response(
            "get_web_acl", get_response, {"Name": WEB_ACL_NAME, "Scope": "REGIONAL", "Id": WEB_ACL_ID}
        )
        services["waf"].add_response(
            "update_web_acl", {"NextLockToken": "lock-2"}, _expected_update(_web_acl(NEW, OLD))
        )
        services["cloudfront"].add_response(
            "get_distribution_config",
            {"DistributionConfig": _distribution_config(OLD), "ETag": "E1"},
            {"Id": DISTRIBUTION_ID},
        )
        services["cloudfront"].add_response(
            "update_distribution",
            _distribution("InProgress"),
            {"Id": DISTRIBUTION_ID, "IfMatch": "E1", "DistributionConfig": _distribution_config(NEW)},
        )
        rotation.lambda_handler(_event("setSecret"), None)
        assert services["order"] == ["waf", "cloudfront"]
        assert services["sleeps"] == [rotation.WAF_SETTLE_SECONDS]

    def test_a_retried_step_changes_nothing_already_changed(self, services):
        self._pending(services)
        services["waf"].add_response(
            "get_web_acl",
            {"WebACL": _web_acl(NEW, OLD), "LockToken": "lock-2"},
            {"Name": WEB_ACL_NAME, "Scope": "REGIONAL", "Id": WEB_ACL_ID},
        )
        services["cloudfront"].add_response(
            "get_distribution_config",
            {"DistributionConfig": _distribution_config(NEW), "ETag": "E2"},
            {"Id": DISTRIBUTION_ID},
        )
        rotation.lambda_handler(_event("setSecret"), None)
        assert services["order"] == []
        assert services["sleeps"] == []

    def test_a_concurrent_web_acl_change_is_retried(self, services):
        self._pending(services)
        for lock in ("lock-1", "lock-3"):
            services["waf"].add_response(
                "get_web_acl",
                {"WebACL": _web_acl(OLD, OLD), "LockToken": lock},
                {"Name": WEB_ACL_NAME, "Scope": "REGIONAL", "Id": WEB_ACL_ID},
            )
            if lock == "lock-1":
                services["waf"].add_client_error("update_web_acl", service_error_code="WAFOptimisticLockException")
        services["waf"].add_response(
            "update_web_acl", {"NextLockToken": "lock-4"}, _expected_update(_web_acl(NEW, OLD), "lock-3")
        )
        services["cloudfront"].add_response(
            "get_distribution_config",
            {"DistributionConfig": _distribution_config(NEW), "ETag": "E2"},
            {"Id": DISTRIBUTION_ID},
        )
        rotation.lambda_handler(_event("setSecret"), None)
        assert services["sleeps"] == [rotation.WAF_SETTLE_SECONDS]

    def test_without_a_web_acl_only_the_distribution_is_updated(self, services, monkeypatch):
        self._pending(services)
        monkeypatch.setattr(rotation, "WEB_ACL_ARN", "")
        services["cloudfront"].add_response(
            "get_distribution_config",
            {"DistributionConfig": _distribution_config(OLD), "ETag": "E1"},
            {"Id": DISTRIBUTION_ID},
        )
        services["cloudfront"].add_response(
            "update_distribution",
            _distribution("InProgress"),
            {"Id": DISTRIBUTION_ID, "IfMatch": "E1", "DistributionConfig": _distribution_config(NEW)},
        )
        rotation.lambda_handler(_event("setSecret"), None)
        assert services["order"] == ["cloudfront"]
        assert services["sleeps"] == []

    def test_a_distribution_without_the_header_fails_the_step(self, services, monkeypatch):
        self._pending(services)
        monkeypatch.setattr(rotation, "WEB_ACL_ARN", "")
        config = _distribution_config(OLD)
        for origin in config["Origins"]["Items"][1:]:
            origin["CustomHeaders"] = {"Quantity": 0}
        services["cloudfront"].add_response(
            "get_distribution_config", {"DistributionConfig": config, "ETag": "E1"}, {"Id": DISTRIBUTION_ID}
        )
        with pytest.raises(rotation.RotationError):
            rotation.lambda_handler(_event("setSecret"), None)


@pytest.mark.unit
class TestTestAndFinishSecret:
    def test_test_step_calls_no_service(self, services):
        fake = _use_secrets(
            services,
            FakeSecretsManager({"current": OLD}, pending_value=json.dumps({"current": NEW, "previous": OLD})),
        )
        rotation.lambda_handler(_event("testSecret"), None)
        assert fake.writes() == []
        assert services["order"] == []

    def test_finish_moves_awscurrent_to_the_new_version(self, services):
        fake = _pending_with_promotion_recorded(services)
        _stub_finish_reads(services, waf=(NEW, OLD), origins=NEW)
        rotation.lambda_handler(_event("finishSecret"), None)
        assert "AWSCURRENT" in fake.versions[TOKEN]["stages"]
        assert "AWSCURRENT" not in fake.versions[CURRENT_TOKEN]["stages"]
        # setSecret left both on the new values, so nothing is updated and nothing waits.
        assert services["order"] == ["promote"]
        assert services["sleeps"] == []
        # A repeated call finds the version current and changes nothing.
        before = list(fake.writes())
        rotation.lambda_handler(_event("finishSecret"), None)
        assert fake.writes() == before


def _pending_with_promotion_recorded(services):
    """A pending rotation whose AWSCURRENT move is recorded in services["order"]."""
    fake = _use_secrets(
        services,
        FakeSecretsManager({"current": OLD}, pending_value=json.dumps({"current": NEW, "previous": OLD})),
    )
    promote = fake.update_secret_version_stage

    def recorded(**kwargs):
        services["order"].append("promote")
        return promote(**kwargs)

    fake.update_secret_version_stage = recorded
    return fake


def _stub_finish_reads(services, waf, origins, lock_token="lock-5", etag="E5"):
    """The web ACL and distribution state finishSecret reads before it promotes the version."""
    if waf is not None:
        services["waf"].add_response(
            "get_web_acl",
            {"WebACL": _web_acl(*waf), "LockToken": lock_token},
            {"Name": WEB_ACL_NAME, "Scope": "REGIONAL", "Id": WEB_ACL_ID},
        )
    services["cloudfront"].add_response(
        "get_distribution_config",
        {"DistributionConfig": _distribution_config(origins), "ETag": etag},
        {"Id": DISTRIBUTION_ID},
    )


@pytest.mark.unit
class TestFinishSecretAfterAnOverlappingDeploy:
    """A stack update between setSecret and finishSecret resolves the templates to the version that is
    still AWSCURRENT, so the web ACL matches and the origin headers can be back on the old value.
    finishSecret applies the pending values again before it moves AWSCURRENT."""

    def test_a_web_acl_reset_to_the_old_value_is_set_again_before_promotion(self, services):
        fake = _pending_with_promotion_recorded(services)
        _stub_finish_reads(services, waf=(OLD, OLD), origins=NEW)
        services["waf"].add_response(
            "update_web_acl", {"NextLockToken": "lock-6"}, _expected_update(_web_acl(NEW, OLD), "lock-5")
        )
        rotation.lambda_handler(_event("finishSecret"), None)
        assert services["order"] == ["waf", "promote"]
        assert services["sleeps"] == [rotation.WAF_SETTLE_SECONDS]
        assert "AWSCURRENT" in fake.versions[TOKEN]["stages"]

    def test_origins_reset_to_the_old_value_are_set_again_before_promotion(self, services):
        fake = _pending_with_promotion_recorded(services)
        _stub_finish_reads(services, waf=(NEW, OLD), origins=OLD)
        services["cloudfront"].add_response(
            "update_distribution",
            _distribution("InProgress"),
            {"Id": DISTRIBUTION_ID, "IfMatch": "E5", "DistributionConfig": _distribution_config(NEW)},
        )
        rotation.lambda_handler(_event("finishSecret"), None)
        assert services["order"] == ["cloudfront", "promote"]
        assert services["sleeps"] == []
        assert "AWSCURRENT" in fake.versions[TOKEN]["stages"]

    def test_both_reset_are_set_again_in_order_before_promotion(self, services):
        _pending_with_promotion_recorded(services)
        _stub_finish_reads(services, waf=(OLD, OLD), origins=OLD)
        services["waf"].add_response(
            "update_web_acl", {"NextLockToken": "lock-6"}, _expected_update(_web_acl(NEW, OLD), "lock-5")
        )
        services["cloudfront"].add_response(
            "update_distribution",
            _distribution("InProgress"),
            {"Id": DISTRIBUTION_ID, "IfMatch": "E5", "DistributionConfig": _distribution_config(NEW)},
        )
        rotation.lambda_handler(_event("finishSecret"), None)
        # The web ACL accepts the new value before CloudFront starts sending it.
        assert services["order"] == ["waf", "cloudfront", "promote"]
        assert services["sleeps"] == [rotation.WAF_SETTLE_SECONDS]

    def test_without_a_web_acl_only_the_origins_are_checked(self, services, monkeypatch):
        _pending_with_promotion_recorded(services)
        monkeypatch.setattr(rotation, "WEB_ACL_ARN", "")
        _stub_finish_reads(services, waf=None, origins=NEW)
        rotation.lambda_handler(_event("finishSecret"), None)
        assert services["order"] == ["promote"]

    def test_a_failed_update_leaves_awscurrent_on_the_old_version(self, services):
        fake = _pending_with_promotion_recorded(services)
        _stub_finish_reads(services, waf=(NEW, OLD), origins=OLD)
        services["cloudfront"].add_client_error("update_distribution", service_error_code="PreconditionFailed")
        with pytest.raises(ClientError):
            rotation.lambda_handler(_event("finishSecret"), None)
        assert "promote" not in services["order"]
        assert "AWSCURRENT" in fake.versions[CURRENT_TOKEN]["stages"]
        assert "AWSCURRENT" not in fake.versions[TOKEN]["stages"]


@pytest.mark.unit
class TestHandlerGuards:
    def test_rotation_disabled(self, services):
        _use_secrets(services, FakeSecretsManager({"current": OLD}, rotation_enabled=False))
        with pytest.raises(rotation.RotationError):
            rotation.lambda_handler(_event("createSecret"), None)

    def test_unknown_step(self, services):
        _use_secrets(services, FakeSecretsManager({"current": OLD}))
        with pytest.raises(rotation.RotationError):
            rotation.lambda_handler(_event("rotateEverything"), None)

    def test_version_without_a_stage(self, services):
        _use_secrets(services, FakeSecretsManager({"current": OLD}))
        event = dict(_event("createSecret"), ClientRequestToken="cccccccc-0000-0000-0000-000000000003")
        with pytest.raises(rotation.RotationError):
            rotation.lambda_handler(event, None)

    def test_current_version_is_a_no_op(self, services):
        fake = _use_secrets(services, FakeSecretsManager({"current": OLD}))
        event = dict(_event("setSecret"), ClientRequestToken=CURRENT_TOKEN)
        rotation.lambda_handler(event, None)
        assert fake.writes() == []


@pytest.mark.unit
class TestNoValueIsLogged:
    def test_a_full_rotation_logs_neither_value(self, services, monkeypatch):
        messages = []
        for level in ("debug", "info", "warning", "error", "exception"):
            monkeypatch.setattr(
                rotation.logger, level, lambda message, *a, **k: messages.append(str(message))
            )
        fake = _use_secrets(services, FakeSecretsManager({"current": OLD}))
        services["cloudfront"].add_response("get_distribution", _distribution("Deployed"), {"Id": DISTRIBUTION_ID})
        services["waf"].add_response(
            "get_web_acl",
            {"WebACL": _web_acl(OLD, OLD), "LockToken": "lock-1"},
            {"Name": WEB_ACL_NAME, "Scope": "REGIONAL", "Id": WEB_ACL_ID},
        )
        services["waf"].add_response(
            "update_web_acl", {"NextLockToken": "lock-2"}, _expected_update(_web_acl(NEW, OLD))
        )
        services["cloudfront"].add_response(
            "get_distribution_config",
            {"DistributionConfig": _distribution_config(OLD), "ETag": "E1"},
            {"Id": DISTRIBUTION_ID},
        )
        services["cloudfront"].add_response(
            "update_distribution",
            _distribution("InProgress"),
            {"Id": DISTRIBUTION_ID, "IfMatch": "E1", "DistributionConfig": _distribution_config(NEW)},
        )
        # finishSecret reads both again and finds them already on the new values.
        _stub_finish_reads(services, waf=(NEW, OLD), origins=NEW)
        for step in ("createSecret", "setSecret", "testSecret", "finishSecret"):
            rotation.lambda_handler(_event(step), None)
        assert "AWSCURRENT" in fake.versions[TOKEN]["stages"]
        assert services["order"] == ["waf", "cloudfront"]
        assert messages
        assert all(OLD not in m and NEW not in m for m in messages)
