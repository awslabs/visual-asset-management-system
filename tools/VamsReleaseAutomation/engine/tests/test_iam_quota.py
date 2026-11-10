# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""the IAM quota check before `cdk deploy`.

Live test Phase B7, live: a permission-boundary + role-prefix switch on test1 replaced all 91 VAMS roles; the
account held 942 of 1000 roles, CloudFormation creates each replacement before it deletes the old role, and
`cdk deploy` failed after 18 minutes with "Cannot exceed quota for RolesPerAccount: 1000" (UPDATE_ROLLBACK_COMPLETE).
The engine checked permissions, not quotas. Each test here failed on the code before the check."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml
from botocore.exceptions import ClientError

from vra import deploy, errors, iam as iam_mod, permissions, runner, util

from conftest import AllowAllIam, FakeAws, fake_shell_cls
from test_build_deploy import FakeCfn, _build

PKG = Path(__file__).resolve().parents[2]


def boundary_switch_diff(n: int = 91) -> str:
    """`cdk diff` of a boundary + prefix switch, in the rendering of the live test1 diff (cdk-diff-test.txt)."""
    lines = ["Stack vams-core-dev", "Resources"]
    for i in range(n):
        lines += [f"[~] AWS::IAM::Role ApiBuilder/fn{i}/ServiceRole fn{i}ServiceRole{i:08X} replace",
                  " ├─ [~] PermissionsBoundary",
                  " │   └─ @@ -1,3 +1,1 @@",
                  ' │      [-] {', ' │      [-]   "Ref": "AWS::NoValue"', " │      [-] }",
                  ' │      [+] "arn:aws:iam::111111111111:policy/it-boundary"',
                  " └─ [~] RoleName (requires replacement)",
                  f"     ├─ [-] vamscoredev-fn{i}-us-east-1",
                  f"     └─ [+] ITVAMS-vamscoredev-fn{i}-us-east-1",
                  f"[~] AWS::IAM::Policy ApiBuilder/fn{i}/ServiceRole/DefaultPolicy fn{i}DefaultPolicy{i:08X}",
                  " └─ [~] Roles"]
    lines.append("\u2728  Number of stacks with differences: 1")
    return "\n".join(lines) + "\n"


class QuotaIam(AllowAllIam):
    """Allow-all simulator plus iam:GetAccountSummary (counts as the live account had them)."""

    def __init__(self, roles=942, quota=1000, policies=28, policies_quota=1500, deny=False):
        self.summary = {"Roles": roles, "RolesQuota": quota, "Policies": policies, "PoliciesQuota": policies_quota}
        self.deny = deny
        self.calls = 0

    def get_account_summary(self):
        self.calls += 1
        if self.deny:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": (
                "User: arn:aws:sts::111111111111:assumed-role/vra-deploy-dev/vra is not authorized to perform: "
                "iam:GetAccountSummary on resource: * because no identity-based policy allows the "
                "iam:GetAccountSummary action")}}, "GetAccountSummary")
        return {"SummaryMap": dict(self.summary)}


def _deploy(ctx, calls, monkeypatch, diff: str, iam: QuotaIam, env_over: dict | None = None):
    b = _build(ctx).run.state["result"]["buildId"]
    if env_over:
        p = ctx.ledger.root / "envs" / "dev" / "env.yaml"
        util.write_yaml(p, {**util.read_yaml(p), **env_over})
        ctx.ledger = type(ctx.ledger)(ctx.ledger.root)
        ctx.aws.ledger = ctx.ledger

    def handler(argv, cwd, env):
        if "diff" in argv:
            return subprocess.CompletedProcess(argv, 0, diff, "")
        return None
    ctx.shell_cls = fake_shell_cls(calls, handler)
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn(), "iam": iam})
    # the core stack is deployed (an update, not a first install): the diff is the complete count
    monkeypatch.setattr(deploy, "_first_install_counts", lambda *a, **k: None, raising=False)
    return runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev", build=b)


def _cdk_deployed(calls) -> bool:
    return any("deploy" in c["argv"] and "--app" in c["argv"] and "diff" not in c["argv"] for c in calls)


def test_parse_diff_counts_the_live_boundary_role_replacements():
    p = deploy.parse_diff(boundary_switch_diff(91))
    assert p["iam"] == {"roles": {"added": 0, "replaced": 91}, "policies": {"added": 0, "replaced": 0}}
    # property-level "(requires replacement)" under a resource line without "replace"; additions; removals free
    text = ("[~] AWS::IAM::Role R R1\n └─ [~] RoleName (requires replacement)\n[+] AWS::IAM::Role N N1\n"
            "[+] AWS::IAM::ManagedPolicy P P1\n[-] AWS::IAM::Role Old Old1\n[~] AWS::IAM::Role B B1\n"
            " └─ [~] PermissionsBoundary\n")
    assert deploy.parse_diff(text)["iam"] == {"roles": {"added": 1, "replaced": 1},
                                              "policies": {"added": 1, "replaced": 0}}


def test_boundary_switch_stops_before_cdk_deploy(ctx, repos, calls, monkeypatch):
    """942 roles + 91 replaced + headroom 10 > 1000: exit 20 naming the numbers and the fix; cdk deploy never runs
    (before: the deploy ran and CloudFormation rolled back on RolesPerAccount)."""
    iam = QuotaIam(942, 1000)
    out = _deploy(ctx, calls, monkeypatch, boundary_switch_diff(91), iam)
    assert out.exit_code == errors.NEEDS_HUMAN, out.message
    for s in ("Roles 942", "91 needed", "91 replaced", "headroom 10", "RolesQuota 1000", "Roles per account"):
        assert s in out.message, s
    assert iam.calls == 1 and not _cdk_deployed(calls)
    assert out.run.find_step("deploy.dev.diff")["status"] != "succeeded"


def test_quota_headroom_is_configurable(ctx, repos, calls, monkeypatch):
    # 899 + 91 + 10 = 1000, not above the quota: deploys
    out = _deploy(ctx, calls, monkeypatch, boundary_switch_diff(91), QuotaIam(899, 1000))
    assert out.exit_code == 0, out.message
    q = out.run.find_step("deploy.dev.diff")["result"]["iamQuota"]
    assert q["ok"] and q["needed"]["roles"] == 91 and q["headroom"] == 10 and q["Roles"] == 899
    ctx.run_id = None
    calls.clear()
    out = _deploy(ctx, calls, monkeypatch, boundary_switch_diff(91), QuotaIam(899, 1000),
                  env_over={"iam": {"quotaHeadroom": 11}})
    assert out.exit_code == errors.NEEDS_HUMAN and "headroom 11" in out.message
    assert not _cdk_deployed(calls)


def test_quota_headroom_is_validated():
    assert deploy.quota_headroom({}) == 10 and deploy.quota_headroom({"iam": {"quotaHeadroom": 0}}) == 0
    for bad in (-1, "5", True):
        try:
            deploy.quota_headroom({"iam": {"quotaHeadroom": bad}})
        except errors.UsageError:
            continue
        raise AssertionError(bad)


def test_precreated_roles_env_counts_zero_and_reads_no_quota(ctx, repos, calls, monkeypatch):
    """A switch to IT-precreated roles removes the stack roles ([-]): nothing needs quota, so the deploy neither
    reads iam:GetAccountSummary nor needs that grant (the prod01 case: IT's 87 roles, no AWS::IAM::Role)."""
    diff = "Stack vams-core-dev\nResources\n" + "".join(
        f"[-] AWS::IAM::Role ApiBuilder/fn{i}/ServiceRole fn{i}ServiceRole destroy\n" for i in range(91)) + \
        "[~] AWS::Lambda::Function Fn Fn1\n\u2728  Number of stacks with differences: 1\n"
    iam = QuotaIam(999, 1000, deny=True)        # full account, read denied: neither matters
    out = _deploy(ctx, calls, monkeypatch, diff, iam)
    assert out.exit_code == 0, out.message
    q = out.run.find_step("deploy.dev.diff")["result"]["iamQuota"]
    assert q["needed"] == {"roles": 0, "policies": 0} and "skipped" in q and iam.calls == 0


def test_get_account_summary_denied_is_an_it_request(ctx, repos, calls, monkeypatch):
    out = _deploy(ctx, calls, monkeypatch, boundary_switch_diff(3), QuotaIam(deny=True))
    assert out.exit_code == errors.BLOCKED_ON_IT, out.message
    req = util.read_json(out.run.it_dir / "IT-REQUEST-1.json")
    assert req["actions"] == ["iam:GetAccountSummary"] and req["step"] == "deploy.dev.diff"
    assert not _cdk_deployed(calls)


def test_first_install_counts_the_templates(ctx, repos, calls, tmp_path):
    """No core stack yet: `cdk diff` may not render nested stacks, so the synthesized templates are the count."""
    asm = tmp_path / "asm"
    asm.mkdir()
    util.write_json(asm / "vams-core-dev.template.json", {"Resources": {
        "A": {"Type": "AWS::IAM::Role"}, "B": {"Type": "AWS::Lambda::Function"},
        "N": {"Type": "AWS::CloudFormation::Stack"}}})
    util.write_json(asm / "ApiNested.nested.template.json", {"Resources": {
        f"R{i}": {"Type": "AWS::IAM::Role"} for i in range(90)} | {"P": {"Type": "AWS::IAM::ManagedPolicy"}}})
    assert deploy.template_iam_counts(asm) == {"roles": {"added": 91, "replaced": 0},
                                               "policies": {"added": 1, "replaced": 0}}
    assert deploy.template_iam_counts(asm, ["vams-core-dev"])["roles"]["added"] == 1


def test_change_set_gate_counts_role_adds_and_replacements():
    class Cfn:
        def describe_change_set(self, **kw):
            rc = [{"ResourceType": "AWS::IAM::Role", "Action": "Modify", "Replacement": "True",
                   "LogicalResourceId": f"R{i}"} for i in range(5)]
            rc += [{"ResourceType": "AWS::IAM::Role", "Action": "Add", "LogicalResourceId": "N"},
                   {"ResourceType": "AWS::IAM::Role", "Action": "Modify", "Replacement": "False",
                    "LogicalResourceId": "B"},
                   {"ResourceType": "AWS::IAM::ManagedPolicy", "Action": "Add", "LogicalResourceId": "P"}]
            return {"StackName": "vams-core-dev", "Status": "CREATE_COMPLETE",
                    "Changes": [{"ResourceChange": c} for c in rc]}

    res = deploy.analyze_change_set(Cfn(), "vams-core-dev", "cs")
    assert res["iam"] == {"roles": {"added": 1, "replaced": 5}, "policies": {"added": 1, "replaced": 0}}


def test_iam_plan_summary_states_the_role_quota_need():
    roles = [{"constructPath": f"s/r{i}"} for i in range(91)]
    assert iam_mod.role_quota_summary({"modes": ["boundary"]}, roles)["stackRoles"] == 91
    assert iam_mod.role_quota_summary({"modes": ["precreated", "bootstrap"]}, roles)["stackRoles"] == 0


def test_get_account_summary_is_in_the_manifests_and_the_deploy_role():
    """Preflight simulates the manifests, so a deploy role without the grant becomes the usual IT request before
    any deploy work; rollback only notes the quota (optional, never blocks a rollback)."""
    action = "iam:GetAccountSummary"
    for wf, optional in (("deploy", False), ("rollback", True)):
        sts = [s for s in permissions.load_manifest(wf)["statements"] if action in s["actions"]]
        assert len(sts) == 1 and sts[0]["resources"] == ["*"] and bool(sts[0].get("optional")) is optional, wf
        assert (PKG / "iam/actions" / f"{wf}.yaml").read_bytes() == \
            (PKG / "engine/vra/data/iam-actions" / f"{wf}.yaml").read_bytes()

    class _L(yaml.SafeLoader):
        pass

    _L.add_multi_constructor("!", lambda loader, suffix, node: None)
    t = yaml.load((PKG / "iam/cloudformation/vra-deploy-role.yaml").read_text(), Loader=_L)
    stmts = [s for s in t["Resources"]["DeployPolicy"]["Properties"]["PolicyDocument"]["Statement"]
             if isinstance(s, dict) and action in json.dumps(s.get("Action"))]
    assert stmts and all(s.get("Resource") == "*" for s in stmts)


def test_preflight_names_the_missing_quota_grant(ctx):
    class DenyQuota(AllowAllIam):
        def simulate_principal_policy(self, **kw):
            return {"EvaluationResults": [
                {"EvalActionName": a, "EvalResourceName": "*",
                 "EvalDecision": "implicitDeny" if a == "iam:GetAccountSummary" else "allowed"}
                for a in kw["ActionNames"]], "IsTruncated": False}

    ctx.aws = FakeAws(ctx.ledger, {"iam": DenyQuota(), "cloudformation": FakeCfn()})
    try:
        permissions.preflight(ctx, "dev", "deploy")
    except errors.PermissionDenied as exc:
        assert [d["action"] for d in exc.denials] == ["iam:GetAccountSummary"]
    else:
        raise AssertionError("preflight passed without iam:GetAccountSummary")
