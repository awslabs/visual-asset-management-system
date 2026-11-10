# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""the CloudFront quota check before `cdk deploy` (vra 0.5.1).

live test: prod01's fresh prod install failed about 8 minutes into `cdk deploy` because the account's custom
CloudFront cache-policy quota (L-7D134442, 20 at the time) was full: StaticWeb `ApiCachePolicy` CREATE_FAILED
(ServiceLimitExceeded), and the rollback then got stuck. Each VAMS CloudFront deployment adds 2 cache
policies, 1 origin request policy, 1 response headers policy and 1 distribution. The engine checked IAM quotas
only. Each test here fails on the 0.5.0 code."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError

from vra import deploy, errors, permissions, runner, util

from conftest import AllowAllIam, FakeAws, fake_shell_cls
from test_build_deploy import FakeCfn, _build
from test_iam_quota import QuotaIam

PKG = Path(__file__).resolve().parents[2]
CF_ACTIONS = ["cloudfront:ListCachePolicies", "cloudfront:ListOriginRequestPolicies",
              "cloudfront:ListResponseHeadersPolicies", "cloudfront:ListDistributions"]


def _denied(op: str, action: str) -> ClientError:
    return ClientError({"Error": {"Code": "AccessDenied", "Message": (
        f"User: arn:aws:sts::111111111111:assumed-role/vra-deploy-dev/vra is not authorized to perform: {action} on "
        f"resource: * because no identity-based policy allows the {action} action")}}, op)


class FakeCloudFront:
    """cloudfront List* with the given custom counts, paginated (Marker / NextMarker) like the real API."""

    def __init__(self, cache=0, orp=0, rhp=0, dists=0, page=100, deny: str | None = None):
        self.n = {"list_cache_policies": cache, "list_origin_request_policies": orp,
                  "list_response_headers_policies": rhp, "list_distributions": dists}
        self.page, self.deny, self.calls = page, deny, []

    def _list(self, method, key, kw):
        self.calls.append((method, kw))
        if self.deny == method:
            op = "".join(w.capitalize() for w in method.split("_"))
            raise _denied(op, "cloudfront:" + op)
        if method != "list_distributions":
            assert kw.get("Type") == "custom", kw
        start = int(kw.get("Marker") or 0)
        qty = max(0, min(self.page, self.n[method] - start))
        out = {"Quantity": qty, "MaxItems": self.page, "Items": [{}] * qty}
        if start + qty < self.n[method]:
            out["NextMarker"] = str(start + qty)
        return {key: out}

    def list_cache_policies(self, **kw):
        return self._list("list_cache_policies", "CachePolicyList", kw)

    def list_origin_request_policies(self, **kw):
        return self._list("list_origin_request_policies", "OriginRequestPolicyList", kw)

    def list_response_headers_policies(self, **kw):
        return self._list("list_response_headers_policies", "ResponseHeadersPolicyList", kw)

    def list_distributions(self, **kw):
        return self._list("list_distributions", "DistributionList", kw)


class FakeQuotas:
    """servicequotas:GetServiceQuota (service code cloudfront); codes absent from ``values`` do not exist."""

    def __init__(self, values: dict | None = None, deny=False):
        self.values = values if values is not None else \
            {"L-7D134442": 20.0, "L-C3659C43": 20.0, "L-CF0D4FC5": 20.0, "L-24B04930": 500.0}
        self.deny, self.calls = deny, []

    def get_service_quota(self, ServiceCode, QuotaCode):
        self.calls.append((ServiceCode, QuotaCode))
        if self.deny:
            raise _denied("GetServiceQuota", "servicequotas:GetServiceQuota")
        if QuotaCode not in self.values:
            raise ClientError({"Error": {"Code": "NoSuchResourceException", "Message": (
                "The request failed because the specified quota and service do not exist.")}}, "GetServiceQuota")
        return {"Quota": {"ServiceCode": ServiceCode, "QuotaCode": QuotaCode, "Value": self.values[QuotaCode]}}


BOTO3_SERVICES = set(boto3.session.Session().get_available_services())


class RegionAws(FakeAws):
    """Records the Region each client was asked for; every client name must be a real boto3 service (the live
    check found `servicequotas`, which boto3 does not know: its name is `service-quotas`)."""

    def client(self, env, service, role="deploy", region=None):
        assert service in BOTO3_SERVICES, service
        self.regions = getattr(self, "regions", {})
        self.regions[service] = region
        return super().client(env, service, role, region)


def vams_cloudfront_diff() -> str:
    """`cdk diff` of a change that adds the StaticWeb CloudFront resources (e.g. a switch from ALB)."""
    return ("Stack vams-core-dev\nResources\n"
            "[+] AWS::CloudFront::ResponseHeadersPolicy StaticWeb/ResponseHeadersPolicy RHP1\n"
            "[+] AWS::CloudFront::CachePolicy StaticWeb/CachePolicy CachePolicy1\n"
            "[+] AWS::CloudFront::CachePolicy StaticWeb/ApiCachePolicy ApiCachePolicy1\n"
            "[+] AWS::CloudFront::OriginRequestPolicy StaticWeb/ApiOriginRequestPolicy ORP1\n"
            "[+] AWS::CloudFront::Distribution StaticWeb/WebAppDistribution Dist1\n"
            "[+] AWS::CloudFront::OriginAccessControl StaticWeb/OAC OAC1\n"
            "\u2728  Number of stacks with differences: 1\n")


def vams_static_web_templates(asm: Path) -> Path:
    """A synthesized assembly shaped like the real cust-v2.6.6-8 bundle: StaticWeb nested stack with the
    CloudFront resources, IAM-free (precreated roles, as prod01)."""
    asm.mkdir(parents=True, exist_ok=True)
    util.write_json(asm / "vams-core-dev.template.json", {"Resources": {
        "StaticWebNestedStack": {"Type": "AWS::CloudFormation::Stack"}, "Fn": {"Type": "AWS::Lambda::Function"}}})
    util.write_json(asm / "vamscoredevStaticWeb.nested.template.json", {"Resources": {
        "CachePolicy": {"Type": "AWS::CloudFront::CachePolicy"},
        "ApiCachePolicy": {"Type": "AWS::CloudFront::CachePolicy"},
        "ApiOriginRequestPolicy": {"Type": "AWS::CloudFront::OriginRequestPolicy"},
        "ResponseHeadersPolicy": {"Type": "AWS::CloudFront::ResponseHeadersPolicy"},
        "WebAppDistribution": {"Type": "AWS::CloudFront::Distribution"},
        "Oac1": {"Type": "AWS::CloudFront::OriginAccessControl"}}})
    return asm


def _deploy(ctx, calls, monkeypatch, diff: str, cf: FakeCloudFront, sq: FakeQuotas, first_install: Path | None = None,
            env_over: dict | None = None, iam=None):
    b = _build(ctx).run.state["result"]["buildId"]
    if env_over:
        p = ctx.ledger.root / "envs" / "dev" / "env.yaml"
        util.write_yaml(p, {**util.read_yaml(p), **env_over})
        ctx.ledger = type(ctx.ledger)(ctx.ledger.root)

    def handler(argv, cwd, env):
        if "diff" in argv:
            return subprocess.CompletedProcess(argv, 0, diff, "")
        return None
    ctx.shell_cls = fake_shell_cls(calls, handler)
    ctx.aws = RegionAws(ctx.ledger, {"cloudformation": FakeCfn(), "iam": iam or QuotaIam(),
                                     "cloudfront": cf, "service-quotas": sq})
    if first_install is None:
        # the core stack is deployed (an update): the diff is the complete count
        monkeypatch.setattr(deploy, "_first_install_counts", lambda *a, **k: None, raising=False)
    else:
        # no core stack yet: the synthesized templates (nested included) are the count
        monkeypatch.setattr(deploy, "_first_install_counts",
                            lambda *a, **k: deploy.template_quota_counts(first_install), raising=False)
    return runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev", build=b)


def _cdk_deployed(calls) -> bool:
    return any("deploy" in c["argv"] and "--app" in c["argv"] and "diff" not in c["argv"] for c in calls)


EMPTY_DIFF = "Stack vams-core-dev\nResources\n[~] AWS::Lambda::Function Fn Fn1\n" \
             "\u2728  Number of stacks with differences: 1\n"


def test_parse_diff_counts_cloudfront_adds_and_replacements():
    p = deploy.parse_diff(vams_cloudfront_diff())
    assert p["cloudfront"] == {"cachePolicies": {"added": 2, "replaced": 0},
                               "originRequestPolicies": {"added": 1, "replaced": 0},
                               "responseHeadersPolicies": {"added": 1, "replaced": 0},
                               "distributions": {"added": 1, "replaced": 0}}
    # replacements count (the new one is created first); property-level "(requires replacement)"; removals free
    text = ("[~] AWS::CloudFront::CachePolicy C C1 replace\n[~] AWS::CloudFront::Distribution D D1\n"
            " └─ [~] DistributionConfig (may cause replacement)\n[-] AWS::CloudFront::CachePolicy Old Old1\n"
            "[~] AWS::CloudFront::ResponseHeadersPolicy R R1\n └─ [~] ResponseHeadersPolicyConfig\n")
    cf = deploy.parse_diff(text)["cloudfront"]
    assert cf["cachePolicies"] == {"added": 0, "replaced": 1} and cf["distributions"] == {"added": 0, "replaced": 1}
    assert cf["responseHeadersPolicies"] == {"added": 0, "replaced": 0}
    assert deploy.parse_diff(text)["iam"] == {"roles": {"added": 0, "replaced": 0},
                                             "policies": {"added": 0, "replaced": 0}}


def test_full_cache_policy_quota_stops_before_cdk_deploy(ctx, repos, calls, monkeypatch, tmp_path):
    """The live case: a fresh CloudFront install, 20 of 20 custom cache policies in use, 2 needed: exit 20 naming the
    quota, the numbers and the fix; cdk deploy never runs (before: it ran and failed ~8 minutes in)."""
    cf, sq = FakeCloudFront(cache=20, orp=11, rhp=11, dists=11), FakeQuotas()
    out = _deploy(ctx, calls, monkeypatch, EMPTY_DIFF, cf, sq, first_install=vams_static_web_templates(tmp_path / "a"))
    assert out.exit_code == errors.NEEDS_HUMAN, out.message
    for s in ("CloudFront quota", "Cache policies per AWS account (L-7D134442)", "20 in use", "2 needed",
              "headroom 2", "quota 20", "Request an", "increase", "delete unused"):
        assert s in out.message, s
    assert "Origin request" not in out.message          # 11 + 1 + 2 <= 20: only the full quota is named
    assert not _cdk_deployed(calls)
    assert out.run.find_step("deploy.dev.diff")["status"] != "succeeded"
    assert ("cloudfront", "L-7D134442") in sq.calls
    assert ctx.aws.regions["service-quotas"] == "us-east-1"   # CloudFront quotas are global (us-east-1)


def test_raised_quota_18_of_40_deploys(ctx, repos, calls, monkeypatch, tmp_path):
    """After the owner's increase: 18 of 40 + 2 + headroom 2 fits, the deploy goes on and records the numbers."""
    sq = FakeQuotas({"L-7D134442": 40.0, "L-C3659C43": 20.0, "L-CF0D4FC5": 20.0, "L-24B04930": 500.0})
    out = _deploy(ctx, calls, monkeypatch, EMPTY_DIFF, FakeCloudFront(18, 11, 11, 11), sq,
                  first_install=vams_static_web_templates(tmp_path / "a"))
    assert out.exit_code == 0, out.message
    q = out.run.find_step("deploy.dev.diff")["result"]["cloudfrontQuota"]
    assert q["ok"] and q["headroom"] == 2
    assert q["quotas"]["cachePolicies"] == {"code": "L-7D134442", "name": "Cache policies per AWS account",
                                            "inUse": 18, "needed": 2, "quota": 40, "quotaSource": "service-quotas"}
    assert q["quotas"]["distributions"]["inUse"] == 11 and q["quotas"]["distributions"]["quota"] == 500
    assert _cdk_deployed(calls)


def test_text_diff_switch_to_cloudfront_is_counted_on_an_update(ctx, repos, calls, monkeypatch):
    out = _deploy(ctx, calls, monkeypatch, vams_cloudfront_diff(), FakeCloudFront(cache=19), FakeQuotas())
    assert out.exit_code == errors.NEEDS_HUMAN and "19 in use + 2 needed (2 added, 0 replaced)" in out.message
    assert not _cdk_deployed(calls)


def test_in_use_counts_follow_every_page():
    cf = FakeCloudFront(cache=250, dists=230, page=100)
    assert deploy._cf_in_use(cf, "list_cache_policies", "CachePolicyList") == 250
    assert deploy._cf_in_use(cf, "list_distributions", "DistributionList") == 230
    assert [m for m, _ in cf.calls].count("list_cache_policies") == 3


def test_no_cloudfront_resources_reads_nothing(ctx, repos, calls, monkeypatch, tmp_path):
    """ALB / GovCloud deployments and ordinary updates: no CloudFront resource added, so no read and no grant
    needed (both reads deny here, and neither matters)."""
    cf, sq = FakeCloudFront(cache=99, deny="list_cache_policies"), FakeQuotas(deny=True)
    out = _deploy(ctx, calls, monkeypatch, EMPTY_DIFF, cf, sq)
    assert out.exit_code == 0, out.message
    q = out.run.find_step("deploy.dev.diff")["result"]["cloudfrontQuota"]
    assert "skipped" in q and not cf.calls and not sq.calls
    # an ALB first install: the templates have no CloudFront resource either
    alb = tmp_path / "alb"
    alb.mkdir()
    util.write_json(alb / "vams-core-dev.template.json", {"Resources": {
        "Alb": {"Type": "AWS::ElasticLoadBalancingV2::LoadBalancer"}}})
    ctx.run_id = None
    calls.clear()
    out = _deploy(ctx, calls, monkeypatch, EMPTY_DIFF, cf, sq, first_install=alb)
    assert out.exit_code == 0, out.message
    assert not cf.calls and not sq.calls


def test_denied_list_read_is_an_it_request(ctx, repos, calls, monkeypatch):
    out = _deploy(ctx, calls, monkeypatch, vams_cloudfront_diff(), FakeCloudFront(deny="list_cache_policies"),
                  FakeQuotas())
    assert out.exit_code == errors.BLOCKED_ON_IT, out.message
    req = util.read_json(out.run.it_dir / "IT-REQUEST-1.json")
    assert req["actions"] == ["cloudfront:ListCachePolicies"] and req["step"] == "deploy.dev.diff"
    assert not _cdk_deployed(calls)


def test_denied_quota_read_is_an_it_request(ctx, repos, calls, monkeypatch):
    out = _deploy(ctx, calls, monkeypatch, vams_cloudfront_diff(), FakeCloudFront(), FakeQuotas(deny=True))
    assert out.exit_code == errors.BLOCKED_ON_IT, out.message
    req = util.read_json(out.run.it_dir / "IT-REQUEST-1.json")
    assert req["actions"] == ["servicequotas:GetServiceQuota"]
    assert not _cdk_deployed(calls)


def test_missing_quota_falls_back_to_the_aws_default(ctx, repos, calls, monkeypatch):
    """No applied value in Service Quotas: the documented AWS default (20 cache policies) is used and named."""
    out = _deploy(ctx, calls, monkeypatch, vams_cloudfront_diff(), FakeCloudFront(cache=5), FakeQuotas({}))
    assert out.exit_code == 0, out.message
    q = out.run.find_step("deploy.dev.diff")["result"]["cloudfrontQuota"]
    cp = q["quotas"]["cachePolicies"]
    assert cp["quota"] == 20 and cp["quotaSource"] == "aws-default" and "AWS default 20" in cp["note"]
    assert q["quotas"]["distributions"]["quota"] == 500 and q["notes"]
    ctx.run_id = None
    calls.clear()
    out = _deploy(ctx, calls, monkeypatch, vams_cloudfront_diff(), FakeCloudFront(cache=17), FakeQuotas({}))
    assert out.exit_code == errors.NEEDS_HUMAN and "quota 20 (AWS default, quota not readable)" in out.message


def test_headroom_is_configurable_and_validated(ctx, repos, calls, monkeypatch):
    # 16 + 2 + 2 = 20: fits
    out = _deploy(ctx, calls, monkeypatch, vams_cloudfront_diff(), FakeCloudFront(cache=16), FakeQuotas())
    assert out.exit_code == 0, out.message
    ctx.run_id = None
    calls.clear()
    out = _deploy(ctx, calls, monkeypatch, vams_cloudfront_diff(), FakeCloudFront(cache=16), FakeQuotas(),
                  env_over={"cloudfront": {"quotaHeadroom": 3}})
    assert out.exit_code == errors.NEEDS_HUMAN and "headroom 3" in out.message
    assert deploy.cloudfront_quota_headroom({}) == 2
    assert deploy.cloudfront_quota_headroom({"cloudfront": {"quotaHeadroom": 0}}) == 0
    for bad in (-1, "5", True):
        try:
            deploy.cloudfront_quota_headroom({"cloudfront": {"quotaHeadroom": bad}})
        except errors.UsageError:
            continue
        raise AssertionError(bad)


def test_change_set_gate_counts_cloudfront_adds_and_replacements():
    class Cfn:
        def describe_change_set(self, **kw):
            if kw["ChangeSetName"] == "nested-cs":
                rc = [{"ResourceType": "AWS::CloudFront::CachePolicy", "Action": "Add", "LogicalResourceId": f"C{i}"}
                      for i in range(2)]
                rc += [{"ResourceType": "AWS::CloudFront::Distribution", "Action": "Modify", "Replacement": "True",
                        "LogicalResourceId": "D"},
                       {"ResourceType": "AWS::CloudFront::OriginRequestPolicy", "Action": "Modify",
                        "Replacement": "False", "LogicalResourceId": "O"}]
                return {"StackName": "vams-core-dev-StaticWeb", "Status": "CREATE_COMPLETE",
                        "Changes": [{"ResourceChange": c} for c in rc]}
            rc = [{"ResourceType": "AWS::CloudFormation::Stack", "Action": "Modify", "LogicalResourceId": "StaticWeb",
                   "ChangeSetId": "nested-cs"},
                  {"ResourceType": "AWS::CloudFront::ResponseHeadersPolicy", "Action": "Add", "LogicalResourceId": "R"}]
            return {"StackName": "vams-core-dev", "Status": "CREATE_COMPLETE",
                    "Changes": [{"ResourceChange": c} for c in rc]}

    res = deploy.analyze_change_set(Cfn(), "vams-core-dev", "cs")
    assert res["cloudfront"] == {"cachePolicies": {"added": 2, "replaced": 0},
                                 "originRequestPolicies": {"added": 0, "replaced": 0},
                                 "responseHeadersPolicies": {"added": 1, "replaced": 0},
                                 "distributions": {"added": 0, "replaced": 1}}


def test_template_counts_cover_nested_stacks(tmp_path):
    asm = vams_static_web_templates(tmp_path / "a")
    assert deploy.template_cloudfront_counts(asm) == {
        "cachePolicies": {"added": 2, "replaced": 0}, "originRequestPolicies": {"added": 1, "replaced": 0},
        "responseHeadersPolicies": {"added": 1, "replaced": 0}, "distributions": {"added": 1, "replaced": 0}}
    assert deploy.template_cloudfront_counts(asm, ["vams-core-dev"])["cachePolicies"]["added"] == 0


def test_rollback_check_is_advisory(ctx):
    """blocking=False (the rollback redeploy): a full quota is a warning, a denied read is "unverified"."""
    ctx.aws = RegionAws(ctx.ledger, {"cloudfront": FakeCloudFront(cache=20), "service-quotas": FakeQuotas()})
    counts = {**deploy._cf_counter(), "cachePolicies": {"added": 2, "replaced": 0}}
    res = deploy.cloudfront_quota_check(ctx, "dev", counts, blocking=False)
    assert res["ok"] is False and "quota 20" in res["warning"]
    ctx.aws = RegionAws(ctx.ledger, {"cloudfront": FakeCloudFront(deny="list_cache_policies"),
                                   "service-quotas": FakeQuotas()})
    res = deploy.cloudfront_quota_check(ctx, "dev", counts, blocking=False)
    assert res["unverified"] == "cloudfront read denied"


def test_the_reads_are_in_the_manifests_and_the_deploy_role():
    """Preflight simulates the manifests, so a deploy role without the grants becomes the usual IT request before
    any deploy work; rollback only notes the quota (optional, never blocks a rollback)."""
    for wf, optional in (("deploy", False), ("rollback", True)):
        stmts = permissions.load_manifest(wf)["statements"]
        got = {a: s for s in stmts for a in s["actions"] if a in CF_ACTIONS + ["servicequotas:GetServiceQuota"]}
        assert sorted(got) == sorted(CF_ACTIONS + ["servicequotas:GetServiceQuota"]), wf
        for s in got.values():
            assert s["resources"] == ["*"] and bool(s.get("optional")) is optional and s["feature"] == "cloudFrontQuota"
            assert len(s["note"]) > 80, s["sid"]                     # the reason IT reads in the package
        assert (PKG / "iam/actions" / f"{wf}.yaml").read_bytes() == \
            (PKG / "engine/vra/data/iam-actions" / f"{wf}.yaml").read_bytes()

    class _L(yaml.SafeLoader):
        pass

    _L.add_multi_constructor("!", lambda loader, suffix, node: None)
    t = yaml.load((PKG / "iam/cloudformation/vra-deploy-role.yaml").read_text(), Loader=_L)
    stmts = [s for s in t["Resources"]["DeployPolicy"]["Properties"]["PolicyDocument"]["Statement"]
             if isinstance(s, dict)]
    for action in CF_ACTIONS + ["servicequotas:GetServiceQuota"]:
        hit = [s for s in stmts if action in json.dumps(s.get("Action"))]
        assert hit and all(s.get("Resource") == "*" for s in hit), action


def test_feature_is_on_for_cloudfront_envs_only(ctx, monkeypatch):
    def cfg(app):
        monkeypatch.setattr(type(ctx.ledger), "vams_config", lambda self, env: {"name": "vams", "app": app})
        return permissions.cloudfront_feature(ctx, "dev")

    assert cfg({}) == "on"                                              # VAMS default: useCloudFront enabled
    assert cfg({"useCloudFront": {"enabled": True}, "useAlb": {"enabled": False}}) == "on"
    assert cfg({"useCloudFront": {"enabled": False}, "useAlb": {"enabled": True}}) is None
    assert cfg({"useCloudFront": {"enabled": False}}) is None
    monkeypatch.setattr(type(ctx.ledger), "vams_config", lambda self, env: None)
    assert permissions.cloudfront_feature(ctx, "dev") == "optional"
    monkeypatch.setitem(ctx.ledger.env("dev"), "partition", "aws-us-gov")
    assert permissions.cloudfront_feature(ctx, "dev") is None          # GovCloud: ALB only
    assert "cloudFrontQuota" not in permissions.env_features(ctx, "dev")


def test_preflight_names_the_missing_cloudfront_grant(ctx):
    class DenyCf(AllowAllIam):
        def simulate_principal_policy(self, **kw):
            return {"EvaluationResults": [
                {"EvalActionName": a, "EvalResourceName": "*",
                 "EvalDecision": "implicitDeny" if a == "cloudfront:ListCachePolicies" else "allowed"}
                for a in kw["ActionNames"]], "IsTruncated": False}

    ctx.aws = FakeAws(ctx.ledger, {"iam": DenyCf(), "cloudformation": FakeCfn()})
    try:
        permissions.preflight(ctx, "dev", "deploy")
    except errors.PermissionDenied as exc:
        assert [d["action"] for d in exc.denials] == ["cloudfront:ListCachePolicies"]
    else:
        raise AssertionError("preflight passed without cloudfront:ListCachePolicies")
