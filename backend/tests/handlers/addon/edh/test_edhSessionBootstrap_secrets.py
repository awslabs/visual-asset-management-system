# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Secret half of edhSessionBootstrap: the per-session secret's creation under the dedicated key with
no read grant, the resource policy that binds it to the desktop's own EC2 instance, the revoke
sequence (the key the row names, then every other key whose name encodes the bootstrap, found by
listing, then the overwrite -- skipped without a session-secrets key that can encrypt -- then optional
force-delete), its form that deletes only the key the row names, and its secret half and its listing half
each on their own, the bootstrap_session
orchestration -- its four outcomes, its budget, and the rollback that runs when the budget or
Secrets Manager fails after a key exists -- the write of a re-issued key into the session secret, and
the key-record helpers: the paged session-key listing, the deletion of one key record with no secret
touched, and the deletion of an expired key that no session row names."""

import io
import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap

USER = "user@example.com"
BOOTSTRAP_ID = "5d1c8a2e-0b1f-4a6c-9d3e-7f8a9b0c1d2e"
SECRET_NAME = f"vams/edh-session/{BOOTSTRAP_ID}"
SECRET_ARN = f"arn:aws:secretsmanager:us-east-1:123456789012:secret:{SECRET_NAME}-AbCdEf"
KMS_ARN = "arn:aws:kms:us-east-1:123456789012:key/11111111-2222-3333-4444-555555555555"
ROLE_ARN = "arn:aws:iam::123456789012:role/EdhDesktopInstanceRole"
INSTANCE_ID = "i-0123456789abcdef0"
INSTANCE_ARN = f"arn:aws:ec2:us-east-1:123456789012:instance/{INSTANCE_ID}"
SESSION_ARN = f"arn:aws:sts::123456789012:assumed-role/EdhDesktopInstanceRole/{INSTANCE_ID}"
# An allowlisted role that no longer exists (an EDH reinstall replaces its desktop roles), and the answer
# Secrets Manager gives a policy naming a session of it.
STALE_ROLE_ARN = "arn:aws:iam::123456789012:role/edh/EdhDesktopInstanceRole-Replaced"
STALE_SESSION_ARN = f"arn:aws:sts::123456789012:assumed-role/EdhDesktopInstanceRole-Replaced/{INSTANCE_ID}"
UNSUPPORTED_PRINCIPAL = "This resource policy contains an unsupported principal."
RAW_KEY = "vams_" + "k" * 64
EVENT = {
    "requestContext": {"http": {"method": "POST", "path": "/addon/edh/workstations/sess-1/stop"}},
    "pathParameters": {"sessionId": "sess-1"},
    "queryStringParameters": None,
}
PAYLOAD = {"apiKey": RAW_KEY, "userId": USER, "baseUrl": "https://vams.example.com",
           "expiresAt": "2026-09-08T20:00:00Z"}
MINTED = {"apiKeyId": "key-2", "apiKey": RAW_KEY, "expiresAt": "2026-09-08T20:00:00Z"}
CLEARED_KEY = {"apiKeyId": "", "keyExpiresAt": ""}
CLEARED_ALL = {**CLEARED_KEY, "secretArn": "", "secretName": "", "secretBoundInstanceId": ""}
# apiKeyService's answer to a delete of a key id it holds no record of (general_error, 400).
KEY_NOT_FOUND = {"message": "API key not found"}
# apiKeyService's other 400 answers to a key delete: the route template did not match, the id is not a
# UUID, a general refusal, and a body carrying no message at all.
REFUSALS_OTHER_THAN_NOT_FOUND = [
    {"message": "apiKeyId is required"},
    {"message": "apiKeyId is invalid. Must follow the regexp ^[0-9a-f-]{36}$"},
    {"message": "VAMS General Error"},
    {},
]


def _row(**overrides):
    row = {"bootstrapId": BOOTSTRAP_ID, "sessionId": "sess-1", "userId": USER, "edhUid": "vabcdefghij23456",
           "state": "ACTIVE", "apiKeyId": "key-1", "secretName": SECRET_NAME,
           "secretArn": SECRET_ARN, "keyExpiresAt": "2026-09-08T20:00:00Z"}
    row.update(overrides)
    return row


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


def _invoke_response(status_code, body):
    return {"Payload": io.BytesIO(
        json.dumps({"statusCode": status_code, "body": json.dumps(body)}).encode("utf-8"))}


def _log_lines(recorder):
    return [repr(call) for call in recorder.mock_calls]


def _sent(client):
    """Every apiKeyService event the Lambda client was handed, in order."""
    return [json.loads(c.kwargs["Payload"].decode("utf-8")) for c in client.invoke.call_args_list]


def _sent_by_method(client, method):
    return [sent for sent in _sent(client) if sent["requestContext"]["http"]["method"] == method]


def _deleted_ids(client):
    """The key ids the Lambda client was asked to delete, in order."""
    return [sent["pathParameters"]["apiKeyId"] for sent in _sent_by_method(client, "DELETE")]


def _enabled(**more):
    """Patches shared by every bootstrap_session test: the feature on, a static base URL, a fresh
    budget clock, and whatever collaborators the test replaces."""
    patches = [
        patch.object(bootstrap, "session_bootstrap_enabled", True),
        patch.object(bootstrap, "vams_base_url", "https://vams.example.com"),
        patch.object(bootstrap, "_resolved_base_url", None),
        patch.object(bootstrap, "_budget_deadline", None),
    ]
    patches.extend(patch.object(bootstrap, name, value) for name, value in more.items())
    return patches


class _Patched:
    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


@pytest.mark.unit
class TestCreateSessionSecret:
    def test_creates_under_the_dedicated_key_with_the_payload_and_tags(self):
        sm = MagicMock()
        sm.create_secret.return_value = {"ARN": SECRET_ARN, "Name": SECRET_NAME}
        with patch.object(bootstrap, "secretsmanager_client", sm):
            assert bootstrap.create_session_secret(BOOTSTRAP_ID, PAYLOAD) == SECRET_ARN
        kwargs = sm.create_secret.call_args.kwargs
        assert kwargs["Name"] == SECRET_NAME
        assert kwargs["KmsKeyId"] == KMS_ARN
        assert json.loads(kwargs["SecretString"]) == PAYLOAD
        assert kwargs["Tags"] == [{"Key": "bootstrapId", "Value": BOOTSTRAP_ID},
                                  {"Key": "userId", "Value": USER}]
        sm.put_secret_value.assert_not_called()

    def test_the_secret_is_created_without_a_read_grant(self):
        """Nobody can read a fresh secret: the binding to the desktop's instance is what grants it."""
        sm = MagicMock()
        sm.create_secret.return_value = {"ARN": SECRET_ARN}
        with patch.object(bootstrap, "secretsmanager_client", sm):
            bootstrap.create_session_secret(BOOTSTRAP_ID, PAYLOAD)
        sm.put_resource_policy.assert_not_called()
        assert "ResourcePolicy" not in sm.create_secret.call_args.kwargs

    def test_an_existing_secret_is_overwritten(self):
        sm = MagicMock()
        sm.create_secret.side_effect = _client_error("ResourceExistsException")
        sm.put_secret_value.return_value = {"ARN": SECRET_ARN}
        with patch.object(bootstrap, "secretsmanager_client", sm):
            assert bootstrap.create_session_secret(BOOTSTRAP_ID, PAYLOAD) == SECRET_ARN
        put = sm.put_secret_value.call_args.kwargs
        assert put["SecretId"] == SECRET_NAME
        assert json.loads(put["SecretString"]) == PAYLOAD
        sm.put_resource_policy.assert_not_called()

    def test_any_other_secrets_manager_error_raises(self):
        sm = MagicMock()
        sm.create_secret.side_effect = _client_error("AccessDeniedException")
        with patch.object(bootstrap, "secretsmanager_client", sm):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.create_session_secret(BOOTSTRAP_ID, PAYLOAD)
        sm.put_resource_policy.assert_not_called()

    def test_a_failure_below_the_service_layer_raises_the_bootstrap_error(self):
        from botocore.exceptions import ConnectTimeoutError
        sm = MagicMock()
        sm.create_secret.side_effect = ConnectTimeoutError(endpoint_url="https://secretsmanager.example")
        with patch.object(bootstrap, "secretsmanager_client", sm):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.create_session_secret(BOOTSTRAP_ID, PAYLOAD)

    def test_an_exhausted_budget_stops_before_the_create(self):
        sm = MagicMock()
        with patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "_budget_deadline", 100.0), \
                patch.object(bootstrap, "_monotonic", MagicMock(return_value=104.5)):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.create_session_secret(BOOTSTRAP_ID, PAYLOAD)
        sm.create_secret.assert_not_called()


@pytest.mark.unit
class TestBindSessionSecret:
    def _policy(self, sm):
        kwargs = sm.put_resource_policy.call_args.kwargs
        return kwargs, json.loads(kwargs["ResourcePolicy"])

    def test_binding_grants_the_role_sessions_on_the_instance_alone(self):
        sm = MagicMock()
        with patch.object(bootstrap, "secretsmanager_client", sm):
            bound = bootstrap.bind_session_secret(_row(), INSTANCE_ID)
        assert bound == {"secretBoundInstanceId": INSTANCE_ID}
        kwargs, policy = self._policy(sm)
        assert kwargs["SecretId"] == SECRET_NAME
        assert kwargs["BlockPublicPolicy"] is True
        assert policy["Version"] == "2012-10-17"
        assert policy["Statement"] == [{
            "Effect": "Allow",
            "Principal": {"AWS": [SESSION_ARN]},
            "Action": "secretsmanager:GetSecretValue",
            "Resource": "*",
        }]

    def test_the_policy_carries_only_condition_keys_a_secret_policy_accepts(self):
        # Secrets Manager refuses a resource policy whose Condition names another service's key
        # (MalformedPolicyDocumentException: "The Condition element can contain only secretsmanager
        # context keys"); a mocked client accepts anything, so the rule is held here.
        sm = MagicMock()
        with patch.object(bootstrap, "secretsmanager_client", sm):
            bootstrap.bind_session_secret(_row(), INSTANCE_ID)
        _kwargs, policy = self._policy(sm)
        for statement in policy["Statement"]:
            for operator in statement.get("Condition", {}).values():
                for key in operator:
                    assert key.split(":", 1)[0].lower() in ("secretsmanager", "aws"), key

    def test_the_partition_and_account_are_the_secrets_own(self):
        sm = MagicMock()
        row = _row(secretArn=f"arn:aws-us-gov:secretsmanager:us-gov-west-1:210987654321:secret:{SECRET_NAME}-XyZ")
        with patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "workstation_instance_role_arns",
                             ["arn:aws-us-gov:iam::210987654321:role/EdhDesktopInstanceRole"]):
            bootstrap.bind_session_secret(row, INSTANCE_ID)
        _kwargs, policy = self._policy(sm)
        assert policy["Statement"][0]["Principal"] == {"AWS": [
            f"arn:aws-us-gov:sts::210987654321:assumed-role/EdhDesktopInstanceRole/{INSTANCE_ID}"]}

    def test_a_role_path_is_dropped_from_the_session_arn(self):
        sm = MagicMock()
        with patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "workstation_instance_role_arns",
                             [ROLE_ARN, "arn:aws:iam::123456789012:role/edh/nodes/VdiNodeRole"]):
            bootstrap.bind_session_secret(_row(), INSTANCE_ID)
        _kwargs, policy = self._policy(sm)
        assert policy["Statement"][0]["Principal"]["AWS"] == [
            SESSION_ARN, f"arn:aws:sts::123456789012:assumed-role/VdiNodeRole/{INSTANCE_ID}"]

    @pytest.mark.parametrize("role_arns", [
        [],
        ["arn:aws:iam::999999999999:role/EdhDesktopInstanceRole"],
        ["arn:aws-us-gov:iam::123456789012:role/EdhDesktopInstanceRole"],
        ["arn:aws:iam::123456789012:user/someone"],
        ["arn:aws:iam::123456789012:role/"],
        ["not-an-arn"],
    ])
    def test_a_role_that_is_not_a_role_of_the_secrets_account_is_refused_before_any_write(self, role_arns):
        sm = MagicMock()
        with patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "workstation_instance_role_arns", role_arns):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.bind_session_secret(_row(), INSTANCE_ID)
        sm.put_resource_policy.assert_not_called()

    def test_a_row_without_a_secret_name_derives_it_from_the_bootstrap_id(self):
        sm = MagicMock()
        row = _row()
        del row["secretName"]
        with patch.object(bootstrap, "secretsmanager_client", sm):
            bootstrap.bind_session_secret(row, INSTANCE_ID)
        assert sm.put_resource_policy.call_args.kwargs["SecretId"] == SECRET_NAME

    def test_a_failed_policy_write_raises(self):
        sm = MagicMock()
        sm.put_resource_policy.side_effect = _client_error("MalformedPolicyDocumentException")
        with patch.object(bootstrap, "secretsmanager_client", sm):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.bind_session_secret(_row(), INSTANCE_ID)

    @pytest.mark.parametrize("secret_arn", ["", "not-an-arn", "arn:aws:kms:us-east-1:123456789012:key/abc"])
    def test_a_row_whose_secret_arn_is_unreadable_raises_before_any_write(self, secret_arn):
        sm = MagicMock()
        with patch.object(bootstrap, "secretsmanager_client", sm):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.bind_session_secret(_row(secretArn=secret_arn), INSTANCE_ID)
        sm.put_resource_policy.assert_not_called()

    @pytest.mark.parametrize("instance_id", [
        "*", "i-*", "i-?", "i-abc", "i-0123456789ABCDEF0", "arn:aws:ec2:us-east-1:123456789012:instance/*", "", None,
    ])
    def test_a_value_that_is_not_an_instance_id_is_refused_before_any_policy_is_written(self, instance_id):
        # An unexpected upstream value must never reach the principal, where it could name some other
        # session of a workstation role.
        sm = MagicMock()
        with patch.object(bootstrap, "secretsmanager_client", sm):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.bind_session_secret(_row(), instance_id)
        sm.put_resource_policy.assert_not_called()

    @pytest.mark.parametrize("instance_id", ["i-01234567", "i-0123456789abcdef0"])
    def test_an_instance_id_of_either_length_is_bound(self, instance_id):
        sm = MagicMock()
        with patch.object(bootstrap, "secretsmanager_client", sm):
            assert bootstrap.bind_session_secret(_row(), instance_id) == {"secretBoundInstanceId": instance_id}
        _kwargs, policy = self._policy(sm)
        assert policy["Statement"][0]["Principal"]["AWS"][0].endswith(f":assumed-role/EdhDesktopInstanceRole/{instance_id}")

    @staticmethod
    def _refusing(*refused_principals):
        """A put_resource_policy that refuses, as Secrets Manager does, any policy naming one of
        ``refused_principals``, and records every policy it was asked to write."""
        written = []

        def put(**kwargs):
            principals = json.loads(kwargs["ResourcePolicy"])["Statement"][0]["Principal"]["AWS"]
            written.append(list(principals))
            if any(p in refused_principals for p in principals):
                raise ClientError({"Error": {"Code": "MalformedPolicyDocumentException",
                                             "Message": UNSUPPORTED_PRINCIPAL}}, "PutResourcePolicy")
            return {}

        return put, written

    def test_a_role_that_no_longer_exists_is_left_out_of_the_binding(self):
        # One stale allowlist entry must not cost every desktop its binding.
        sm = MagicMock()
        put, written = self._refusing(STALE_SESSION_ARN)
        sm.put_resource_policy.side_effect = put
        with patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "workstation_instance_role_arns", [STALE_ROLE_ARN, ROLE_ARN]):
            assert bootstrap.bind_session_secret(_row(), INSTANCE_ID) == {"secretBoundInstanceId": INSTANCE_ID}
        _kwargs, policy = self._policy(sm)
        assert policy["Statement"][0]["Principal"] == {"AWS": [SESSION_ARN]}
        # No write ever named a principal outside the allowlisted sessions on this instance, and every
        # write blocked a public policy.
        assert all(set(principals) <= {SESSION_ARN, STALE_SESSION_ARN} for principals in written)
        assert sm.put_resource_policy.call_args_list, "no policy was put"
        assert all(c.kwargs["BlockPublicPolicy"] is True for c in sm.put_resource_policy.call_args_list)

    def test_every_live_role_stays_in_the_binding_beside_a_stale_one(self):
        other_role = "arn:aws:iam::123456789012:role/VdiNodeRole"
        other_session = f"arn:aws:sts::123456789012:assumed-role/VdiNodeRole/{INSTANCE_ID}"
        sm = MagicMock()
        put, _written = self._refusing(STALE_SESSION_ARN)
        sm.put_resource_policy.side_effect = put
        with patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "workstation_instance_role_arns", [ROLE_ARN, STALE_ROLE_ARN, other_role]):
            bootstrap.bind_session_secret(_row(), INSTANCE_ID)
        _kwargs, policy = self._policy(sm)
        assert policy["Statement"][0]["Principal"] == {"AWS": [SESSION_ARN, other_session]}

    def test_a_binding_no_role_of_which_is_accepted_raises_without_naming_one(self):
        sm = MagicMock()
        put, _written = self._refusing(SESSION_ARN, STALE_SESSION_ARN)
        sm.put_resource_policy.side_effect = put
        with patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "workstation_instance_role_arns", [ROLE_ARN, STALE_ROLE_ARN]):
            with pytest.raises(bootstrap.SessionBootstrapError) as raised:
                bootstrap.bind_session_secret(_row(), INSTANCE_ID)
        assert "arn:" not in str(raised.value) and "123456789012" not in str(raised.value)

    def test_the_role_left_out_is_named_in_the_log(self):
        sm = MagicMock()
        put, _written = self._refusing(STALE_SESSION_ARN)
        sm.put_resource_policy.side_effect = put
        recorder = MagicMock()
        with patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "logger", recorder), \
                patch.object(bootstrap, "workstation_instance_role_arns", [ROLE_ARN, STALE_ROLE_ARN]):
            bootstrap.bind_session_secret(_row(), INSTANCE_ID)
        assert any("EdhDesktopInstanceRole-Replaced" in line for line in _log_lines(recorder))

    def test_a_policy_refused_for_another_reason_is_not_retried(self):
        sm = MagicMock()
        sm.put_resource_policy.side_effect = ClientError(
            {"Error": {"Code": "MalformedPolicyDocumentException",
                       "Message": "The Condition element can contain only secretsmanager context keys"}},
            "PutResourcePolicy")
        with patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "workstation_instance_role_arns", [ROLE_ARN, STALE_ROLE_ARN]):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.bind_session_secret(_row(), INSTANCE_ID)
        sm.put_resource_policy.assert_called_once()

    def test_a_single_role_write_that_fails_for_another_reason_raises(self):
        sm = MagicMock()
        sm.put_resource_policy.side_effect = [
            ClientError({"Error": {"Code": "MalformedPolicyDocumentException", "Message": UNSUPPORTED_PRINCIPAL}},
                        "PutResourcePolicy"),
            _client_error("AccessDeniedException"),
        ]
        with patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "workstation_instance_role_arns", [ROLE_ARN, STALE_ROLE_ARN]):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.bind_session_secret(_row(), INSTANCE_ID)
        assert sm.put_resource_policy.call_count == 2

    def test_the_binding_runs_outside_any_mint_budget(self):
        sm = MagicMock()
        with patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "_budget_deadline", 100.0), \
                patch.object(bootstrap, "_monotonic", MagicMock(return_value=104.5)):
            bootstrap.bind_session_secret(_row(), INSTANCE_ID)
        sm.put_resource_policy.assert_called_once()


@pytest.mark.unit
class TestRevokeSession:
    def _wired(self, delete_status=200, delete_body=None, listed=(), listing=None, answers=None):
        """apiKeyService and Secrets Manager behind the revoke. A key delete answers ``answers[id]`` when
        given, else ``(delete_status, delete_body)``; the session-key listing answers ``listing`` -- a
        (status, body) pair, or the exception the invoke raises -- or else lists ``listed``. Each invoke
        gets a fresh response: a Payload stream is read once."""
        default = (delete_status, delete_body if delete_body is not None else {"message": "deleted"})

        def invoke(**kwargs):
            sent = json.loads(kwargs["Payload"].decode("utf-8"))
            if sent["requestContext"]["http"]["method"] == "GET":
                if isinstance(listing, Exception):
                    raise listing
                return _invoke_response(*(listing or (200, {"Items": list(listed)})))
            return _invoke_response(*(answers or {}).get(sent["pathParameters"]["apiKeyId"], default))

        client = MagicMock()
        client.invoke.side_effect = invoke
        sm = MagicMock()
        manager = MagicMock()
        manager.attach_mock(client.invoke, "invoke")
        manager.attach_mock(sm.put_secret_value, "put_secret_value")
        manager.attach_mock(sm.delete_secret, "delete_secret")
        return client, sm, manager

    def test_deletes_the_key_then_overwrites_then_force_deletes_the_secret(self):
        client, sm, manager = self._wired()
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", audit):
            attrs = bootstrap.revoke_session(EVENT, _row(), delete_secret=True, actor=USER)

        # The key the row names, the listing of the bootstrap's other keys, then the secret.
        assert [c[0] for c in manager.mock_calls] == ["invoke", "invoke", "put_secret_value", "delete_secret"]
        (sent,) = _sent_by_method(client, "DELETE")
        assert sent["requestContext"]["http"] == {"method": "DELETE", "path": "/auth/api-keys/key-1"}
        assert sent["pathParameters"] == {"apiKeyId": "key-1"}
        assert sent["lambdaCrossCall"] == {"userName": USER, "mfaEnabled": False}
        assert "body" not in sent
        assert sm.put_secret_value.call_args.kwargs == {
            "SecretId": SECRET_NAME, "SecretString": bootstrap.REVOKED_SECRET_VALUE}
        assert sm.delete_secret.call_args.kwargs == {
            "SecretId": SECRET_NAME, "ForceDeleteWithoutRecovery": True}
        assert attrs == CLEARED_ALL
        assert audit.call_args.args[0] is EVENT
        assert audit.call_args.args[1] == "edhSessionApiKeyRevoke"
        assert audit.call_args.args[2] == {"bootstrapId": BOOTSTRAP_ID, "apiKeyId": "key-1",
                                           "userId": USER, "operation": "revoke"}

    def test_stop_keeps_the_secret_container(self):
        client, sm, _ = self._wired()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            attrs = bootstrap.revoke_session(EVENT, _row(), delete_secret=False, actor=USER)
        sm.put_secret_value.assert_called_once()
        sm.delete_secret.assert_not_called()
        assert attrs == CLEARED_KEY

    def test_a_force_deleted_container_takes_its_binding_with_it(self):
        client, sm, _ = self._wired()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            attrs = bootstrap.revoke_session(EVENT, _row(secretBoundInstanceId=INSTANCE_ID),
                                             delete_secret=True, actor=USER)
        assert attrs["secretBoundInstanceId"] == ""

    def test_a_kept_container_keeps_its_binding(self):
        client, sm, _ = self._wired()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            attrs = bootstrap.revoke_session(EVENT, _row(secretBoundInstanceId=INSTANCE_ID),
                                             delete_secret=False, actor=USER)
        assert "secretBoundInstanceId" not in attrs

    def test_the_actor_is_carried_on_the_cross_call(self):
        client, sm, _ = self._wired()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            bootstrap.revoke_session(EVENT, _row(), delete_secret=False, actor="SYSTEM_USER")
        (sent,) = _sent_by_method(client, "DELETE")
        assert sent["lambdaCrossCall"]["userName"] == "SYSTEM_USER"

    def test_an_already_deleted_key_is_tolerated_and_not_audited(self):
        client, sm, _ = self._wired(delete_status=400, delete_body=KEY_NOT_FOUND)
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", audit):
            attrs = bootstrap.revoke_session(EVENT, _row(), delete_secret=True, actor=USER)
        assert attrs == CLEARED_ALL
        assert sm.delete_secret.call_args.kwargs == {"SecretId": SECRET_NAME, "ForceDeleteWithoutRecovery": True}
        audit.assert_not_called()

    def test_a_failed_key_delete_raises_before_the_secret_is_touched(self):
        client, sm, _ = self._wired(delete_status=500)
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.revoke_session(EVENT, _row(), delete_secret=True, actor=USER)
        sm.put_secret_value.assert_not_called()
        sm.delete_secret.assert_not_called()

    @pytest.mark.parametrize("body", REFUSALS_OTHER_THAN_NOT_FOUND,
                             ids=["route-mismatch", "invalid-id", "general-error", "no-message"])
    def test_a_400_that_is_not_the_not_found_answer_raises_before_the_secret_is_touched(self, body):
        client, sm, _ = self._wired(delete_status=400, delete_body=body)
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", audit):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.revoke_session(EVENT, _row(), delete_secret=True, actor=USER)
        sm.put_secret_value.assert_not_called()
        sm.delete_secret.assert_not_called()
        audit.assert_not_called()

    def test_a_missing_secret_is_tolerated(self):
        client, sm, _ = self._wired()
        sm.put_secret_value.side_effect = _client_error("ResourceNotFoundException")
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            assert bootstrap.revoke_session(EVENT, _row(), delete_secret=True, actor=USER) == CLEARED_ALL

    def test_a_secret_already_scheduled_for_deletion_is_tolerated(self):
        client, sm, _ = self._wired()
        sm.put_secret_value.side_effect = _client_error("InvalidRequestException")
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            assert bootstrap.revoke_session(EVENT, _row(), delete_secret=True, actor=USER) == CLEARED_ALL

    def test_another_secrets_manager_error_raises(self):
        client, sm, _ = self._wired()
        sm.put_secret_value.side_effect = _client_error("AccessDeniedException")
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.revoke_session(EVENT, _row(), delete_secret=True, actor=USER)

    def test_without_a_session_secrets_key_the_overwrite_is_skipped_and_the_container_force_deleted(self):
        # Session bootstrap off: the key left the stack with the feature, so there is nothing to encrypt
        # the revoked marker under; the container, whose value nothing can read, is removed outright.
        client, sm, manager = self._wired()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "session_secret_kms_key_arn", ""), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            attrs = bootstrap.revoke_session(EVENT, _row(), delete_secret=True, actor=USER)
        assert [c[0] for c in manager.mock_calls] == ["invoke", "invoke", "delete_secret"]
        assert sm.delete_secret.call_args.kwargs == {"SecretId": SECRET_NAME, "ForceDeleteWithoutRecovery": True}
        assert attrs == CLEARED_ALL

    def test_without_a_session_secrets_key_a_kept_container_is_left_as_it_is(self):
        client, sm, _ = self._wired()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "session_secret_kms_key_arn", ""), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            attrs = bootstrap.revoke_session(EVENT, _row(), delete_secret=False, actor=USER)
        assert _deleted_ids(client) == ["key-1"]
        sm.put_secret_value.assert_not_called()
        sm.delete_secret.assert_not_called()
        assert attrs == CLEARED_KEY

    def test_an_overwrite_the_secrets_key_can_no_longer_encrypt_falls_through_to_the_force_delete(self):
        # The key the secret was written under is pending deletion since a redeploy replaced it.
        client, sm, manager = self._wired()
        sm.put_secret_value.side_effect = _client_error("EncryptionFailure")
        recorder = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "logger", recorder), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            attrs = bootstrap.revoke_session(EVENT, _row(), delete_secret=True, actor=USER)
        assert [c[0] for c in manager.mock_calls] == ["invoke", "invoke", "put_secret_value", "delete_secret"]
        assert sm.delete_secret.call_args.kwargs == {"SecretId": SECRET_NAME, "ForceDeleteWithoutRecovery": True}
        assert attrs == CLEARED_ALL
        recorder.warning.assert_called_once()
        recorder.exception.assert_not_called()

    def test_an_overwrite_the_secrets_key_can_no_longer_encrypt_leaves_a_kept_container_without_raising(self):
        client, sm, _ = self._wired()
        sm.put_secret_value.side_effect = _client_error("EncryptionFailure")
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            attrs = bootstrap.revoke_session(EVENT, _row(), delete_secret=False, actor=USER)
        sm.delete_secret.assert_not_called()
        assert attrs == CLEARED_KEY

    def test_a_row_without_a_key_still_neutralises_its_secret(self):
        client, sm, _ = self._wired()
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", audit):
            bootstrap.revoke_session(EVENT, _row(apiKeyId=""), delete_secret=True, actor=USER)
        assert _sent_by_method(client, "DELETE") == []
        audit.assert_not_called()
        sm.put_secret_value.assert_called_once()
        sm.delete_secret.assert_called_once()

    def test_a_row_without_a_secret_name_derives_it_from_the_bootstrap_id(self):
        client, sm, _ = self._wired()
        row = _row()
        del row["secretName"]
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            bootstrap.revoke_session(EVENT, row, delete_secret=False, actor=USER)
        assert sm.put_secret_value.call_args.kwargs["SecretId"] == SECRET_NAME

    def test_the_revoke_runs_even_when_a_mint_budget_has_run_out(self):
        client, sm, _ = self._wired()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()), \
                patch.object(bootstrap, "_budget_deadline", 100.0), \
                patch.object(bootstrap, "_monotonic", MagicMock(return_value=104.5)):
            assert bootstrap.revoke_session(EVENT, _row(), delete_secret=False, actor=USER) == CLEARED_KEY
        assert _deleted_ids(client) == ["key-1"]

    @staticmethod
    def _listed():
        """The session keys the deployment holds: the row's own key (a re-issue), the key it replaced and
        an earlier re-issue of the same bootstrap, and a launch key and a re-issue of another desktop."""
        other = "9f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a"
        return [_key("key-1", f"temporary-session-edh-{BOOTSTRAP_ID}-r20260928180000"),
                _key("key-0", f"temporary-session-edh-{BOOTSTRAP_ID}"),
                _key("key-r", f"temporary-session-edh-{BOOTSTRAP_ID}-r20260928170000"),
                _key("key-9", f"temporary-session-edh-{other}"),
                _key("key-9r", f"temporary-session-edh-{other}-r20260928170000")]

    def test_every_other_key_of_the_bootstrap_is_deleted_before_the_secret_is_overwritten(self):
        # A running desktop keeps every key it was given until the key's own expiry; a revoke signs out
        # all of them, and no key of another desktop.
        client, sm, manager = self._wired(listed=self._listed())
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", audit):
            attrs = bootstrap.revoke_session(EVENT, _row(), delete_secret=False, actor=USER)
        deleted = _deleted_ids(client)
        assert deleted[0] == "key-1"
        assert set(deleted) == {"key-1", "key-0", "key-r"} and len(deleted) == len(set(deleted))
        # Every deletion lands before the secret is overwritten.
        steps = [c[0] for c in manager.mock_calls]
        assert "put_secret_value" in steps
        assert max(i for i, step in enumerate(steps) if step == "invoke") < steps.index("put_secret_value")
        revoked = {(c.args[2]["apiKeyId"], c.args[2]["bootstrapId"], c.args[2]["operation"])
                   for c in audit.call_args_list}
        assert revoked == {("key-1", BOOTSTRAP_ID, "revoke"), ("key-0", BOOTSTRAP_ID, "revoke"),
                           ("key-r", BOOTSTRAP_ID, "revoke")}
        assert attrs == CLEARED_KEY

    def test_the_keys_are_listed_as_the_system_user_and_deleted_as_the_actor(self):
        client, sm, _ = self._wired(listed=self._listed())
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            bootstrap.revoke_session(EVENT, _row(), delete_secret=False, actor=USER)
        listings = _sent_by_method(client, "GET")
        assert listings, "the session keys were never listed"
        assert all(sent["lambdaCrossCall"]["userName"] == "SYSTEM_USER" for sent in listings)
        removals = _sent_by_method(client, "DELETE")
        assert {sent["pathParameters"]["apiKeyId"] for sent in removals} == {"key-1", "key-0", "key-r"}
        assert all(sent["lambdaCrossCall"]["userName"] == USER for sent in removals)

    def test_a_row_that_names_no_key_still_has_its_bootstraps_keys_deleted(self):
        client, sm, _ = self._wired(listed=self._listed())
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            bootstrap.revoke_session(EVENT, _row(apiKeyId=""), delete_secret=False, actor=USER)
        assert set(_deleted_ids(client)) == {"key-1", "key-0", "key-r"}

    @pytest.mark.parametrize("listing", [(500, {"message": "Internal Server Error"}), RuntimeError("boom")],
                             ids=["refused", "unreachable"])
    def test_a_listing_that_fails_leaves_the_other_keys_to_the_orphan_sweep(self, listing):
        client, sm, _ = self._wired(listing=listing)
        recorder = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "logger", recorder), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            attrs = bootstrap.revoke_session(EVENT, _row(), delete_secret=True, actor=USER)
        # The key the row names is deleted and the secret neutralised all the same.
        assert _deleted_ids(client) == ["key-1"]
        assert sm.put_secret_value.call_args.kwargs == {
            "SecretId": SECRET_NAME, "SecretString": bootstrap.REVOKED_SECRET_VALUE}
        assert sm.delete_secret.call_args.kwargs == {"SecretId": SECRET_NAME, "ForceDeleteWithoutRecovery": True}
        assert attrs == CLEARED_ALL
        assert any("orphan sweep" in line for line in _log_lines(recorder))

    def test_a_listed_key_already_gone_is_tolerated_and_not_audited(self):
        client, sm, _ = self._wired(listed=self._listed(), answers={"key-0": (400, KEY_NOT_FOUND)})
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", audit):
            assert bootstrap.revoke_session(EVENT, _row(), delete_secret=True, actor=USER) == CLEARED_ALL
        assert {c.args[2]["apiKeyId"] for c in audit.call_args_list} == {"key-1", "key-r"}

    def test_a_listed_key_whose_delete_fails_raises_before_the_secret_is_touched(self):
        client, sm, _ = self._wired(listed=self._listed(),
                                    answers={"key-r": (500, {"message": "Internal Server Error"})})
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.revoke_session(EVENT, _row(), delete_secret=True, actor=USER)
        sm.put_secret_value.assert_not_called()
        sm.delete_secret.assert_not_called()

    def test_a_listed_key_is_audited_under_its_own_user(self):
        client, sm, _ = self._wired(listed=[_key("key-0", f"temporary-session-edh-{BOOTSTRAP_ID}", user="former@example.com")])
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", audit):
            bootstrap.revoke_session(EVENT, _row(), delete_secret=False, actor=USER)
        users = {c.args[2]["apiKeyId"]: c.args[2]["userId"] for c in audit.call_args_list}
        assert users == {"key-1": USER, "key-0": "former@example.com"}

    def test_without_every_key_only_the_key_the_row_names_is_deleted_and_nothing_is_listed(self):
        # The form a start runs ahead of its mint: the listing waits until the fresh key exists.
        client, sm, _ = self._wired(listed=self._listed())
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            attrs = bootstrap.revoke_session(EVENT, _row(), delete_secret=False, actor=USER, every_key=False)
        assert _deleted_ids(client) == ["key-1"]
        assert _sent_by_method(client, "GET") == []
        assert sm.put_secret_value.call_args.kwargs == {
            "SecretId": SECRET_NAME, "SecretString": bootstrap.REVOKED_SECRET_VALUE}
        assert attrs == CLEARED_KEY

    def test_the_listing_half_deletes_every_other_key_of_the_bootstrap_and_touches_no_secret(self):
        # What a start runs once EDH has accepted it, keeping the key it just minted.
        client, sm, _ = self._wired(listed=self._listed())
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", audit):
            bootstrap.delete_other_session_keys(EVENT, _row(), keep="key-1", actor=USER)
        assert set(_deleted_ids(client)) == {"key-0", "key-r"}
        assert all(sent["lambdaCrossCall"]["userName"] == USER for sent in _sent_by_method(client, "DELETE"))
        assert {(c.args[2]["apiKeyId"], c.args[2]["operation"]) for c in audit.call_args_list} == {
            ("key-0", "revoke"), ("key-r", "revoke")}
        assert sm.mock_calls == []

    def test_the_listing_half_with_nothing_kept_deletes_every_key_of_the_bootstrap(self):
        client, sm, _ = self._wired(listed=self._listed())
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            bootstrap.delete_other_session_keys(EVENT, _row(), keep=None, actor=USER)
        assert set(_deleted_ids(client)) == {"key-1", "key-0", "key-r"}

    def test_the_listing_half_runs_even_when_a_mint_budget_has_run_out(self):
        client, sm, _ = self._wired(listed=self._listed())
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()), \
                patch.object(bootstrap, "_budget_deadline", 100.0), \
                patch.object(bootstrap, "_monotonic", MagicMock(return_value=104.5)):
            bootstrap.delete_other_session_keys(EVENT, _row(), keep="key-1", actor=USER)
        assert set(_deleted_ids(client)) == {"key-0", "key-r"}

    def test_the_listing_half_starts_no_listing_its_deadline_no_longer_fits(self):
        client, sm, _ = self._wired(listed=self._listed())
        recorder = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "logger", recorder), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()), \
                patch.object(bootstrap, "_monotonic", MagicMock(return_value=100.0)):
            bootstrap.delete_other_session_keys(EVENT, _row(), keep="key-1", actor=USER, deadline=105.0)
        client.invoke.assert_not_called()
        # A skip the budget forces is expected, so it is a warning without a traceback.
        assert any("orphan sweep" in repr(call) for call in recorder.warning.call_args_list)
        recorder.exception.assert_not_called()
        recorder.error.assert_not_called()
        assert bootstrap._budget_deadline is None

    def test_a_delete_the_listing_halfs_deadline_no_longer_fits_raises_before_it_is_sent(self):
        client, sm, _ = self._wired(listed=self._listed())
        readings = iter([100.0])
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()), \
                patch.object(bootstrap, "_monotonic", lambda: next(readings, 105.0)):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.delete_other_session_keys(EVENT, _row(), keep="key-1", actor=USER, deadline=112.0)
        assert _sent_by_method(client, "GET")
        assert _sent_by_method(client, "DELETE") == []
        assert bootstrap._budget_deadline is None

    @pytest.mark.parametrize("listing", [(500, {"message": "Internal Server Error"}), RuntimeError("boom")],
                             ids=["refused", "unreachable"])
    def test_a_listing_half_whose_listing_fails_leaves_the_keys_without_raising(self, listing):
        client, sm, _ = self._wired(listing=listing)
        recorder = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "logger", recorder), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            bootstrap.delete_other_session_keys(EVENT, _row(), keep="key-1", actor=USER)
        assert _sent_by_method(client, "DELETE") == []
        assert any("orphan sweep" in repr(call) for call in recorder.exception.call_args_list)

    def test_a_listing_half_whose_delete_fails_raises(self):
        client, sm, _ = self._wired(listed=self._listed(),
                                    answers={"key-r": (500, {"message": "Internal Server Error"})})
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.delete_other_session_keys(EVENT, _row(), keep="key-1", actor=USER)
        assert sm.mock_calls == []


@pytest.mark.unit
class TestNeutraliseSessionSecret:
    """The secret half of a revoke on its own: the value overwritten with the revoked marker, and with
    ``delete_secret`` the container removed after it; no key record is read or deleted."""

    @staticmethod
    def _neutralise(sm, *, delete_secret):
        client = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), patch.object(bootstrap, "secretsmanager_client", sm):
            bootstrap.neutralise_session_secret(BOOTSTRAP_ID, delete_secret=delete_secret)
        return client

    def test_the_value_is_overwritten_and_the_container_kept(self):
        sm = MagicMock()
        client = self._neutralise(sm, delete_secret=False)
        assert sm.put_secret_value.call_args.kwargs == {
            "SecretId": SECRET_NAME, "SecretString": bootstrap.REVOKED_SECRET_VALUE}
        sm.delete_secret.assert_not_called()
        client.invoke.assert_not_called()

    def test_with_delete_secret_the_container_goes_after_the_overwrite(self):
        sm = MagicMock()
        manager = MagicMock()
        manager.attach_mock(sm.put_secret_value, "put_secret_value")
        manager.attach_mock(sm.delete_secret, "delete_secret")
        client = self._neutralise(sm, delete_secret=True)
        steps = [c[0] for c in manager.mock_calls]
        assert "delete_secret" in steps and steps.index("put_secret_value") < steps.index("delete_secret")
        assert sm.delete_secret.call_args.kwargs == {"SecretId": SECRET_NAME, "ForceDeleteWithoutRecovery": True}
        client.invoke.assert_not_called()

    @pytest.mark.parametrize("code", ["ResourceNotFoundException", "InvalidRequestException"])
    def test_a_container_already_gone_needs_nothing_more(self, code):
        sm = MagicMock()
        sm.put_secret_value.side_effect = _client_error(code)
        self._neutralise(sm, delete_secret=True)
        sm.delete_secret.assert_not_called()

    def test_another_secrets_manager_error_raises(self):
        sm = MagicMock()
        sm.put_secret_value.side_effect = _client_error("AccessDeniedException")
        with pytest.raises(bootstrap.SessionBootstrapError):
            self._neutralise(sm, delete_secret=False)


@pytest.mark.unit
class TestWriteReissuedSecret:
    """The second half of a re-issue: the key the row now names goes into the session secret. An existing
    container keeps its resource policy and binding; one the row had none of is created unbound."""

    MINTED = {"apiKeyId": "key-2", "apiKey": RAW_KEY, "expiresAt": "2026-09-28T20:00:00Z"}

    @staticmethod
    def _patched(sm, **more):
        patches = [patch.object(bootstrap, "secretsmanager_client", sm),
                   patch.object(bootstrap, "vams_base_url", "https://vams.example.com"),
                   patch.object(bootstrap, "_resolved_base_url", None)]
        patches.extend(patch.object(bootstrap, name, value) for name, value in more.items())
        return _Patched(patches)

    def test_an_existing_container_has_its_value_overwritten_and_keeps_its_binding(self):
        sm = MagicMock()
        sm.create_secret.side_effect = _client_error("ResourceExistsException")
        sm.put_secret_value.return_value = {"ARN": SECRET_ARN}
        with self._patched(sm):
            attrs = bootstrap.write_reissued_secret(_row(secretBoundInstanceId=INSTANCE_ID), dict(self.MINTED))
        assert attrs == {"secretArn": SECRET_ARN, "secretName": SECRET_NAME}
        written = json.loads(sm.put_secret_value.call_args.kwargs["SecretString"])
        assert written == {"apiKey": RAW_KEY, "userId": USER, "baseUrl": "https://vams.example.com",
                           "expiresAt": "2026-09-28T20:00:00Z"}
        # The value alone changes: the policy that binds the secret to the desktop's instance stays.
        sm.put_resource_policy.assert_not_called()
        sm.delete_secret.assert_not_called()

    def test_a_container_the_write_creates_reads_as_unbound(self):
        sm = MagicMock()
        sm.create_secret.return_value = {"ARN": SECRET_ARN}
        with self._patched(sm):
            attrs = bootstrap.write_reissued_secret(_row(secretName="", secretArn=""), dict(self.MINTED))
        assert attrs == {"secretArn": SECRET_ARN, "secretName": SECRET_NAME, "secretBoundInstanceId": ""}
        assert sm.create_secret.call_args.kwargs["KmsKeyId"] == KMS_ARN
        sm.put_resource_policy.assert_not_called()

    def test_a_container_gone_from_under_a_row_that_names_it_is_created_unbound(self):
        # The binding the row records belonged to the container that is gone.
        sm = MagicMock()
        sm.create_secret.return_value = {"ARN": SECRET_ARN}
        with self._patched(sm):
            attrs = bootstrap.write_reissued_secret(_row(secretBoundInstanceId=INSTANCE_ID), dict(self.MINTED))
        assert attrs["secretBoundInstanceId"] == ""

    @pytest.mark.parametrize("code", ["AccessDeniedException", "EncryptionFailure", "InternalServiceError"])
    def test_a_write_that_fails_raises_and_deletes_nothing(self, code):
        sm = MagicMock()
        sm.create_secret.side_effect = _client_error("ResourceExistsException")
        sm.put_secret_value.side_effect = _client_error(code)
        client = MagicMock()
        with self._patched(sm, lambda_client=client):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.write_reissued_secret(_row(), dict(self.MINTED))
        # Both keys the row names stay valid, and the secret holds one of them whatever the write did.
        client.invoke.assert_not_called()
        sm.delete_secret.assert_not_called()

    def test_an_unresolvable_base_url_raises_before_anything_is_written(self):
        sm = MagicMock()
        with self._patched(sm, vams_base_url=""):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.write_reissued_secret(_row(), dict(self.MINTED))
        assert not sm.mock_calls

    def test_the_key_reaches_no_log_line(self):
        sm = MagicMock()
        sm.create_secret.side_effect = _client_error("ResourceExistsException")
        sm.put_secret_value.side_effect = _client_error("AccessDeniedException")
        recorder = MagicMock()
        with self._patched(sm, logger=recorder):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.write_reissued_secret(_row(), dict(self.MINTED))
        assert not any("vams_" in line for line in _log_lines(recorder))
        recorder.info("control vams_control")
        assert any("vams_" in line for line in _log_lines(recorder))


@pytest.mark.unit
class TestBootstrapSession:
    def test_disabled_returns_the_disabled_outcome_and_calls_nothing(self):
        mint = MagicMock()
        with patch.object(bootstrap, "session_bootstrap_enabled", False), \
                patch.object(bootstrap, "mint_session_key", mint):
            assert bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True) == ("disabled", {})
        mint.assert_not_called()

    def test_success_returns_provisioned_and_the_row_attributes(self):
        mint = MagicMock(return_value=dict(MINTED))
        create = MagicMock(return_value=SECRET_ARN)
        with _Patched(_enabled(mint_session_key=mint, create_session_secret=create)):
            outcome, attrs = bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=False)
        mint.assert_called_once_with(EVENT, USER, BOOTSTRAP_ID, False)
        create.assert_called_once_with(BOOTSTRAP_ID, {
            "apiKey": RAW_KEY, "userId": USER, "baseUrl": "https://vams.example.com",
            "expiresAt": "2026-09-08T20:00:00Z"})
        assert outcome == bootstrap.BOOTSTRAP_PROVISIONED == "provisioned"
        assert attrs == {"apiKeyId": "key-2", "secretArn": SECRET_ARN, "secretName": SECRET_NAME,
                         "keyExpiresAt": "2026-09-08T20:00:00Z", "secretBoundInstanceId": ""}

    def test_a_fresh_credential_reads_as_unbound_until_bound(self):
        with _Patched(_enabled(mint_session_key=MagicMock(return_value=dict(MINTED)),
                               create_session_secret=MagicMock(return_value=SECRET_ARN))):
            _outcome, attrs = bootstrap.bootstrap_session(
                EVENT, _row(apiKeyId="", secretBoundInstanceId="i-0old"), mfa_enabled=False)
        assert attrs["secretBoundInstanceId"] == ""

    def test_a_skipped_mint_is_the_skipped_outcome_and_writes_no_secret(self):
        create = MagicMock()
        with _Patched(_enabled(mint_session_key=MagicMock(return_value=None), create_session_secret=create)):
            assert bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True) == ("skipped", {})
        create.assert_not_called()

    def test_a_mint_failure_is_the_unavailable_outcome(self):
        mint = MagicMock(side_effect=bootstrap.SessionBootstrapError("down"))
        with _Patched(_enabled(mint_session_key=mint)):
            assert bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True) == ("unavailable", {})

    def test_an_unexpected_error_is_the_unavailable_outcome(self):
        with _Patched(_enabled(mint_session_key=MagicMock(side_effect=RuntimeError("boom")))):
            assert bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True) == ("unavailable", {})

    def test_a_secret_failure_revokes_the_key_it_just_minted_and_removes_the_container(self):
        revoke = MagicMock(return_value=dict(CLEARED_ALL))
        create = MagicMock(side_effect=bootstrap.SessionBootstrapError("denied"))
        with _Patched(_enabled(mint_session_key=MagicMock(return_value=dict(MINTED)),
                               create_session_secret=create, revoke_session=revoke)):
            outcome, attrs = bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True)
        # The container the rollback removed is a name the row must stop carrying.
        assert (outcome, attrs) == ("unavailable", dict(bootstrap.CLEARED_SECRET_ATTRIBUTES))
        assert attrs["secretBoundInstanceId"] == ""
        assert revoke.call_args.args[1]["apiKeyId"] == "key-2"
        # No start reuses the container of a launch that never received a credential; only the minted key
        # is taken back, so no listing of the session keys runs ahead of a start.
        assert revoke.call_args.kwargs == {"delete_secret": True, "actor": USER, "every_key": False}

    def test_a_rollback_revoke_that_fails_leaves_the_key_to_the_orphan_sweep_and_says_so(self):
        # The row never names the minted key, so only the reconciler's orphan sweep can take it back,
        # once it has expired, or once its desktop is no longer live and it has aged past the orphan
        # grace; the log states that.
        recorder = MagicMock()
        revoke = MagicMock(side_effect=bootstrap.SessionBootstrapError("throttled"))
        create = MagicMock(side_effect=bootstrap.SessionBootstrapError("denied"))
        with _Patched(_enabled(mint_session_key=MagicMock(return_value=dict(MINTED)),
                               create_session_secret=create, revoke_session=revoke, logger=recorder)):
            outcome, attrs = bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True)
        # The container may still exist, so the row keeps naming it.
        assert (outcome, attrs) == ("unavailable", {})
        orphan_lines = [str(call) for call in recorder.exception.call_args_list if "key-2" in str(call)]
        assert orphan_lines, "the orphaned key was not logged"
        assert all("orphan sweep" in line for line in orphan_lines)

    def test_a_rollback_whose_container_delete_fails_leaves_the_row_naming_the_container(self):
        # The key delete went through; the container did not go, so a start's row keeps the secret it
        # already names rather than being told it is gone.
        client = MagicMock()
        client.invoke.side_effect = lambda **kwargs: _invoke_response(200, {"message": "deleted"})
        sm = MagicMock()
        sm.delete_secret.side_effect = _client_error("InternalServiceError")
        create = MagicMock(side_effect=bootstrap.SessionBootstrapError("denied"))
        with _Patched(_enabled(mint_session_key=MagicMock(return_value=dict(MINTED)),
                               create_session_secret=create, lambda_client=client,
                               secretsmanager_client=sm, log_auth_changes=MagicMock())):
            outcome, attrs = bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True)
        assert (outcome, attrs) == ("unavailable", {})
        assert _deleted_ids(client) == ["key-2"]
        sm.delete_secret.assert_called_once()

    def test_an_unresolvable_base_url_mints_nothing(self):
        mint = MagicMock()
        ssm = MagicMock()
        ssm.get_parameter.side_effect = _client_error("ParameterNotFound")
        with _Patched(_enabled(vams_base_url="ssm:/vams-test/web/deployedUrl", ssm_client=ssm,
                               mint_session_key=mint)):
            assert bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True) == ("unavailable", {})
        mint.assert_not_called()

    @staticmethod
    def _rollback_wiring(secret_failure=None):
        """The real revoke's collaborators: a fake apiKeyService answering 200 to the rollback's key
        delete, and a Secrets Manager client whose create fails with ``secret_failure`` when one is
        given."""
        client = MagicMock()
        client.invoke.side_effect = lambda **kwargs: _invoke_response(200, {"message": "deleted"})
        sm = MagicMock()
        if secret_failure is not None:
            sm.create_secret.side_effect = secret_failure
        return client, sm

    @staticmethod
    def _clock(first, then):
        """A monotonic clock reading ``first`` once and ``then`` on every later call."""
        ticks = iter([first])
        return MagicMock(side_effect=lambda: next(ticks, then))

    @staticmethod
    def _the_minted_key_was_deleted(client, sm):
        """The rollback sent apiKeyService the delete of the minted key as the launching user, listed no
        session key, then overwrote and force-deleted the session's container."""
        (sent,) = _sent_by_method(client, "DELETE")
        assert sent["requestContext"]["http"] == {"method": "DELETE", "path": "/auth/api-keys/key-2"}
        assert sent["pathParameters"] == {"apiKeyId": "key-2"}
        assert sent["lambdaCrossCall"]["userName"] == USER
        assert not _sent_by_method(client, "GET"), "the rollback listed the session keys"
        assert sm.put_secret_value.call_args.kwargs == {
            "SecretId": SECRET_NAME, "SecretString": bootstrap.REVOKED_SECRET_VALUE}
        assert sm.delete_secret.call_args.kwargs == {"SecretId": SECRET_NAME, "ForceDeleteWithoutRecovery": True}

    def test_a_budget_that_runs_out_after_the_mint_takes_the_key_back(self):
        client, sm = self._rollback_wiring()
        audit = MagicMock()
        # The sequence starts at t=0 (the module budget ends at 15); the secret step asks the clock at t=20.
        with _Patched(_enabled(mint_session_key=MagicMock(return_value=dict(MINTED)), lambda_client=client,
                               secretsmanager_client=sm, log_auth_changes=audit, _monotonic=self._clock(0.0, 20.0))):
            assert bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True) == (
                "unavailable", dict(bootstrap.CLEARED_SECRET_ATTRIBUTES))
        sm.create_secret.assert_not_called()
        self._the_minted_key_was_deleted(client, sm)
        assert audit.call_args.args[1:] == ("edhSessionApiKeyRevoke", {
            "bootstrapId": BOOTSTRAP_ID, "apiKeyId": "key-2", "userId": USER, "operation": "revoke"})

    def test_a_secret_failure_inside_the_invoke_gate_window_still_takes_the_key_back(self):
        """A mint invoke may not start while fewer than CROSS_CALL_TIMEOUT_SECONDS of the budget remain;
        the rollback's key delete runs outside the budget, so that window never refuses it."""
        remaining = 5.0
        assert remaining < bootstrap.CROSS_CALL_TIMEOUT_SECONDS
        at = bootstrap.BOOTSTRAP_BUDGET_SECONDS - remaining
        # Control: at that instant a gated invoke is refused.
        with patch.object(bootstrap, "_budget_deadline", bootstrap.BOOTSTRAP_BUDGET_SECONDS), \
                patch.object(bootstrap, "_monotonic", MagicMock(return_value=at)):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap._check_budget("control", bootstrap.CROSS_CALL_TIMEOUT_SECONDS)

        client, sm = self._rollback_wiring(secret_failure=_client_error("AccessDeniedException"))
        # The sequence starts at t=0 (the module budget ends at 15); the secret step asks the clock at
        # ``at`` and is denied, with ``remaining`` seconds of the budget left for the rollback.
        with _Patched(_enabled(mint_session_key=MagicMock(return_value=dict(MINTED)), lambda_client=client,
                               secretsmanager_client=sm, log_auth_changes=MagicMock(),
                               _monotonic=self._clock(0.0, at))):
            assert bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True) == (
                "unavailable", dict(bootstrap.CLEARED_SECRET_ATTRIBUTES))
        sm.create_secret.assert_called_once()
        self._the_minted_key_was_deleted(client, sm)

    def test_a_caller_supplied_deadline_is_the_sequences_budget(self):
        seen = []

        def mint(event, user_id, bootstrap_id, mfa_enabled):
            seen.append(bootstrap._budget_deadline)
            return dict(MINTED)

        with _Patched(_enabled(mint_session_key=mint, create_session_secret=MagicMock(return_value=SECRET_ARN),
                               _monotonic=MagicMock(return_value=1000.0))):
            outcome, _attrs = bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True, deadline=1012.5)
        assert outcome == "provisioned"
        assert seen == [1012.5]

    def test_a_caller_supplied_deadline_that_has_passed_takes_the_minted_key_back(self):
        client, sm = self._rollback_wiring()
        with _Patched(_enabled(mint_session_key=MagicMock(return_value=dict(MINTED)), lambda_client=client,
                               secretsmanager_client=sm, log_auth_changes=MagicMock(),
                               _monotonic=MagicMock(return_value=1060.0))):
            assert bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True, deadline=1050.0) == (
                "unavailable", dict(bootstrap.CLEARED_SECRET_ATTRIBUTES))
        sm.create_secret.assert_not_called()
        self._the_minted_key_was_deleted(client, sm)

    def test_without_a_deadline_the_module_budget_applies(self):
        seen = []

        def mint(event, user_id, bootstrap_id, mfa_enabled):
            seen.append(bootstrap._budget_deadline)
            return dict(MINTED)

        with _Patched(_enabled(mint_session_key=mint, create_session_secret=MagicMock(return_value=SECRET_ARN),
                               _monotonic=MagicMock(return_value=1000.0))):
            bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True)
        assert seen == [1000.0 + bootstrap.BOOTSTRAP_BUDGET_SECONDS]

    def test_the_budget_is_cleared_when_the_sequence_ends(self):
        with _Patched(_enabled(mint_session_key=MagicMock(return_value=dict(MINTED)),
                               create_session_secret=MagicMock(return_value=SECRET_ARN))):
            bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True)
            assert bootstrap._budget_deadline is None
        with _Patched(_enabled(mint_session_key=MagicMock(side_effect=bootstrap.SessionBootstrapError("down")))):
            bootstrap.bootstrap_session(EVENT, _row(apiKeyId=""), mfa_enabled=True)
            assert bootstrap._budget_deadline is None


@pytest.mark.unit
class TestTheKeyNeverReachesALogLine:
    def test_create_and_revoke_log_nothing_carrying_the_key(self):
        sm = MagicMock()
        sm.create_secret.return_value = {"ARN": SECRET_ARN}
        client = MagicMock()
        client.invoke.side_effect = lambda **kwargs: _invoke_response(200, {"message": "deleted"})
        recorder = MagicMock()
        with patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "logger", recorder), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            bootstrap.create_session_secret(BOOTSTRAP_ID, PAYLOAD)
            bootstrap.revoke_session(EVENT, _row(), delete_secret=True, actor=USER)
        offenders = [line for line in _log_lines(recorder) if "vams_" in line]
        assert offenders == [], offenders
        recorder.info("control vams_control")
        assert any("vams_" in line for line in _log_lines(recorder))


def _listing(items, next_token=None):
    """One apiKeyService listing response; NextToken is present only while records remain."""
    body = {"Items": items}
    if next_token is not None:
        body["NextToken"] = next_token
        body["truncated"] = True
    return _invoke_response(200, body)


def _key(api_key_id, name, expires_at="2026-09-08T10:00:00Z", user=USER):
    return {"apiKeyId": api_key_id, "apiKeyName": name, "userId": user,
            "expiresAt": expires_at, "isActive": "true", "createdAt": "2026-09-08T02:00:00Z"}


@pytest.mark.unit
class TestSessionKeyRecords:
    def test_lists_only_session_keys_following_next_token_to_the_end(self):
        client = MagicMock()
        client.invoke.side_effect = [
            _listing([_key("k1", f"temporary-session-edh-{BOOTSTRAP_ID}"), _key("k2", "ci-deploy")], next_token="page-2"),
            _listing([_key("k3", "temporary-session-edh-other")]),
        ]
        with patch.object(bootstrap, "lambda_client", client):
            keys = bootstrap.list_session_keys("SYSTEM_USER")
        assert [k["apiKeyId"] for k in keys] == ["k1", "k3"]
        sent = [json.loads(c.kwargs["Payload"].decode("utf-8")) for c in client.invoke.call_args_list]
        assert [s["requestContext"]["http"] for s in sent] == [{"method": "GET", "path": "/auth/api-keys"}] * 2
        assert sent[0]["queryStringParameters"] == {"maxItems": "1000", "pageSize": "1000"}
        assert sent[1]["queryStringParameters"] == {"maxItems": "1000", "pageSize": "1000", "startingToken": "page-2"}
        assert all(s["lambdaCrossCall"] == {"userName": "SYSTEM_USER", "mfaEnabled": False} for s in sent)
        assert all("body" not in s for s in sent)

    def test_a_listing_failure_raises(self):
        client = MagicMock()
        client.invoke.return_value = _invoke_response(500, {"message": "Internal Server Error"})
        with patch.object(bootstrap, "lambda_client", client):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.list_session_keys("SYSTEM_USER")

    def test_the_page_cap_bounds_a_listing_that_never_ends(self):
        client = MagicMock()
        # A fresh response per page: the Payload stream is read once, so one shared response would be
        # empty from the second page on.
        client.invoke.side_effect = lambda **_kwargs: _listing(
            [_key("k1", f"temporary-session-edh-{BOOTSTRAP_ID}")], next_token="again")
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "KEY_LISTING_MAX_PAGES", 3):
            keys = bootstrap.list_session_keys("SYSTEM_USER")
        assert client.invoke.call_count == 3
        assert len(keys) == 3

    def test_delete_orphan_key_records_the_operation_it_is_given(self):
        client = MagicMock()
        client.invoke.return_value = _invoke_response(200, {"message": "deleted"})
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), patch.object(bootstrap, "log_auth_changes", audit):
            bootstrap.delete_orphan_key(EVENT, _key("k1", f"temporary-session-edh-{BOOTSTRAP_ID}"), "SYSTEM_USER",
                                        operation=bootstrap.AUDIT_OPERATION_REVOKE)
        assert audit.call_args.args[2]["operation"] == "revoke"
        assert (bootstrap.AUDIT_OPERATION_REVOKE, bootstrap.AUDIT_OPERATION_EXPIRE) == ("revoke", "expire")

    def test_delete_orphan_key_deletes_and_audits_with_the_bootstrap_id_the_name_encodes(self):
        client = MagicMock()
        client.invoke.return_value = _invoke_response(200, {"message": "deleted"})
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", audit):
            bootstrap.delete_orphan_key(EVENT, _key("k1", f"temporary-session-edh-{BOOTSTRAP_ID}"), "SYSTEM_USER")
        sent = json.loads(client.invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert sent["requestContext"]["http"] == {"method": "DELETE", "path": "/auth/api-keys/k1"}
        assert sent["pathParameters"] == {"apiKeyId": "k1"}
        assert sent["lambdaCrossCall"] == {"userName": "SYSTEM_USER", "mfaEnabled": False}
        assert audit.call_args.args[0] is EVENT
        assert audit.call_args.args[1] == "edhSessionApiKeyRevoke"
        assert audit.call_args.args[2] == {"bootstrapId": BOOTSTRAP_ID, "apiKeyId": "k1",
                                           "userId": USER, "operation": "expire"}

    def test_a_key_already_gone_is_tolerated_and_not_audited(self):
        client = MagicMock()
        client.invoke.return_value = _invoke_response(400, KEY_NOT_FOUND)
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", audit):
            bootstrap.delete_orphan_key(EVENT, _key("k1", f"temporary-session-edh-{BOOTSTRAP_ID}"), "SYSTEM_USER")
        assert client.invoke.call_args_list, "the delete was never attempted"
        audit.assert_not_called()

    @pytest.mark.parametrize("body", REFUSALS_OTHER_THAN_NOT_FOUND,
                             ids=["route-mismatch", "invalid-id", "general-error", "no-message"])
    def test_a_400_that_is_not_the_not_found_answer_raises_and_writes_no_audit_row(self, body):
        client = MagicMock()
        client.invoke.return_value = _invoke_response(400, body)
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", audit):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.delete_orphan_key(EVENT, _key("k1", f"temporary-session-edh-{BOOTSTRAP_ID}"), "SYSTEM_USER")
        audit.assert_not_called()

    def test_a_failed_delete_raises_and_writes_no_audit_row(self):
        client = MagicMock()
        client.invoke.return_value = _invoke_response(500, {"message": "Internal Server Error"})
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", audit):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.delete_orphan_key(EVENT, _key("k1", f"temporary-session-edh-{BOOTSTRAP_ID}"), "SYSTEM_USER")
        audit.assert_not_called()

    def test_an_orphaned_re_issued_key_is_audited_with_the_bootstrap_id_its_name_encodes(self):
        client = MagicMock()
        client.invoke.return_value = _invoke_response(200, {"message": "deleted"})
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", audit):
            bootstrap.delete_orphan_key(EVENT, _key("k1", f"temporary-session-edh-{BOOTSTRAP_ID}-r20260928170509"),
                                        "SYSTEM_USER")
        assert audit.call_args.args[2]["bootstrapId"] == BOOTSTRAP_ID


@pytest.mark.unit
class TestDeleteSessionKey:
    """One session key record deleted and nothing else: no secret is read or written, so a key that never
    reached the secret goes without disturbing the one the secret holds."""

    def test_the_record_is_deleted_as_the_actor_and_audited_as_the_given_operation(self):
        client = MagicMock()
        client.invoke.return_value = _invoke_response(200, {"message": "deleted"})
        sm = MagicMock()
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", sm), \
                patch.object(bootstrap, "log_auth_changes", audit):
            bootstrap.delete_session_key(EVENT, "key-2", bootstrap_id=BOOTSTRAP_ID, user_id=USER, actor=USER)
        sent = json.loads(client.invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert sent["requestContext"]["http"] == {"method": "DELETE", "path": "/auth/api-keys/key-2"}
        assert sent["pathParameters"] == {"apiKeyId": "key-2"}
        assert sent["lambdaCrossCall"] == {"userName": USER, "mfaEnabled": False}
        assert audit.call_args.args[1] == "edhSessionApiKeyRevoke"
        assert audit.call_args.args[2] == {"bootstrapId": BOOTSTRAP_ID, "apiKeyId": "key-2", "userId": USER,
                                           "operation": "revoke"}
        assert not sm.mock_calls

    def test_a_record_already_gone_is_tolerated_and_not_audited(self):
        client = MagicMock()
        client.invoke.return_value = _invoke_response(400, KEY_NOT_FOUND)
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", audit):
            bootstrap.delete_session_key(EVENT, "key-2", bootstrap_id=BOOTSTRAP_ID, user_id=USER, actor=USER)
        assert client.invoke.call_args_list, "the delete was never attempted"
        audit.assert_not_called()

    @pytest.mark.parametrize("answer", [(500, {"message": "Internal Server Error"})] +
                             [(400, body) for body in REFUSALS_OTHER_THAN_NOT_FOUND],
                             ids=["5xx", "route-mismatch", "invalid-id", "general-error", "no-message"])
    def test_a_delete_that_did_not_happen_raises_and_writes_no_audit_row(self, answer):
        client = MagicMock()
        client.invoke.return_value = _invoke_response(*answer)
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", audit):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.delete_session_key(EVENT, "key-2", bootstrap_id=BOOTSTRAP_ID, user_id=USER, actor=USER)
        audit.assert_not_called()
