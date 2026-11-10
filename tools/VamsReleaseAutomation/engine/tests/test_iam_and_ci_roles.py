# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""IAM templates and CI lanes (offline): role escalation paths, state-store protection, approval writers.

The CloudFormation templates are rendered with a small intrinsic-function resolver and evaluated with a
minimal IAM policy evaluator (explicit deny > allow > implicit deny; StringEquals/Like/NotLike, ArnLike,
Null, ForAllValues), so the escalation paths through the CDK execution policy are checked as policy
decisions, not as text.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

PKG = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ CloudFormation rendering
class _CfnLoader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    name = "Ref" if suffix == "Ref" else ("Fn::GetAtt" if suffix == "GetAtt" else f"Fn::{suffix}")
    if isinstance(node, yaml.ScalarNode):
        val = loader.construct_scalar(node)
        if suffix == "GetAtt":
            val = val.split(".")
    elif isinstance(node, yaml.SequenceNode):
        val = loader.construct_sequence(node, deep=True)
    else:
        val = loader.construct_mapping(node, deep=True)
    return {name: val}


_CfnLoader.add_multi_constructor("!", _tag)
NOVALUE = object()


def load_cfn(rel: str) -> dict:
    return yaml.load((PKG / rel).read_text(), Loader=_CfnLoader)


def render(node, params: dict, conditions: dict, resources: dict):
    def sub(text: str, extra: dict) -> str:
        import re

        def one(m):
            key = m.group(1)
            if key in extra:
                return str(extra[key])
            if key in params:
                return str(params[key])
            if key.endswith(".Arn") and key[:-4] in resources:   # ${Resource.Arn}
                return str(resources[key[:-4]])
            return m.group(0)
        return re.sub(r"\$\{([^}]+)\}", one, text)

    def r(n):
        if isinstance(n, dict) and len(n) == 1:
            (k, v), = n.items()
            if k == "Ref":
                if v == "AWS::NoValue":
                    return NOVALUE
                return resources.get(v, params.get(v))
            if k == "Fn::Sub":
                if isinstance(v, list):
                    return sub(v[0], {kk: r(vv) for kk, vv in v[1].items()})
                return sub(v, {})
            if k == "Fn::If":
                return r(v[1] if conditions[v[0]] else v[2])
            if k == "Fn::Join":
                return v[0].join(str(x) for x in r(v[1]))
            if k == "Fn::Split":
                return r(v[1]).split(v[0])
            if k == "Fn::GetAtt":
                return resources.get(v[0] if isinstance(v, list) else v)
        if isinstance(n, dict):
            out = {k: r(v) for k, v in n.items()}
            return {k: v for k, v in out.items() if v is not NOVALUE}
        if isinstance(n, list):
            return [x for x in (r(i) for i in n) if x is not NOVALUE]
        return n
    return r(node)


# ------------------------------------------------------------------ minimal IAM evaluator
def _as_list(v):
    return v if isinstance(v, list) else [v]


def _cond_ok(cond: dict, ctx: dict) -> bool:
    for op, kv in (cond or {}).items():
        for key, want in kv.items():
            want = [str(w) for w in _as_list(want)]
            have = ctx.get(key)
            if op == "Null":
                if (have is None) != (want[0] == "true"):
                    return False
                continue
            if op.startswith("ForAllValues:"):
                base = op.split(":", 1)[1]
                if have is None:
                    continue
                if not all(_cond_ok({base: {key: want}}, {key: h}) for h in _as_list(have)):
                    return False
                continue
            neg = "Not" in op
            if have is None:
                if neg:
                    continue  # negated operators match a missing key
                return False
            like = "Like" in op
            match = any((fnmatch.fnmatchcase(str(h), w) if like else str(h) == w) for h in _as_list(have) for w in want)
            if match == neg:
                return False
    return True


def _matches(stmt: dict, action: str, resource: str, ctx: dict) -> bool:
    if "Action" in stmt and not any(fnmatch.fnmatchcase(action.lower(), a.lower()) for a in _as_list(stmt["Action"])):
        return False
    if "Resource" in stmt and not any(fnmatch.fnmatchcase(resource, r) for r in _as_list(stmt["Resource"])):
        return False
    if "NotResource" in stmt and any(fnmatch.fnmatchcase(resource, r) for r in _as_list(stmt["NotResource"])):
        return False
    return _cond_ok(stmt.get("Condition"), ctx)


def allowed(statements: list, action: str, resource: str, ctx: dict | None = None) -> bool:
    ctx = ctx or {}
    if any(s["Effect"] == "Deny" and _matches(s, action, resource, ctx) for s in statements):
        return False
    return any(s["Effect"] == "Allow" and _matches(s, action, resource, ctx) for s in statements)


ACCT, P = "111111111111", "aws"
BOUNDARY = f"arn:{P}:iam::{ACCT}:policy/vra-cdk-execution-role-boundary"


@pytest.fixture(scope="module")
def exec_policy():
    t = load_cfn("iam/cloudformation/vra-cdk-execution-policy.yaml")
    params = {"AWS::Partition": P, "AWS::AccountId": ACCT, "AWS::Region": "us-east-1", "PolicyName": "vra-cdk-execution",
              "ConfigName": "vams", "AllowedRegions": ["us-east-1"], "RequiredPermissionsBoundaryArn": "",
              "AllowRolesWithoutBoundary": "false", "ProtectForeignTrailsAndKeys": "true"}
    conds = {"CreateBoundary": True, "BoundaryOptional": False, "BoundaryEnforced": True, "ProtectForeign": True}
    res = {"RoleBoundary": BOUNDARY}
    doc = render(t["Resources"]["ExecutionPolicy"]["Properties"]["PolicyDocument"], params, conds, res)
    return doc["Statement"]


def test_execution_policy_closes_escalation_paths(exec_policy):
    """The CDK execution policy escalation paths (a)-(e), as policy decisions."""
    S = exec_policy
    rq = {"aws:RequestedRegion": "us-east-1"}
    # (a) the policy (and the boundary, and cdk/vra policies) cannot be rewritten
    for pol in ("vra-cdk-execution", "vra-cdk-execution-role-boundary", "vra-anything", "cdk-hnb659fds-x"):
        assert not allowed(S, "iam:CreatePolicyVersion", f"arn:{P}:iam::{ACCT}:policy/{pol}")
        assert not allowed(S, "iam:SetDefaultPolicyVersion", f"arn:{P}:iam::{ACCT}:policy/{pol}")
    assert allowed(S, "iam:CreatePolicyVersion", f"arn:{P}:iam::{ACCT}:policy/vams-core-dev-us-east-1-Fn")
    # (b) PassRole only to VAMS services, never deployment roles
    role = f"arn:{P}:iam::{ACCT}:role/vams-core-dev-us-east-1-FnRole"
    assert allowed(S, "iam:PassRole", role, {"iam:PassedToService": "lambda.amazonaws.com"})
    assert not allowed(S, "iam:PassRole", role, {"iam:PassedToService": "sagemaker.amazonaws.com"})
    assert not allowed(S, "iam:PassRole", role)
    assert not allowed(S, "iam:PassRole", f"arn:{P}:iam::{ACCT}:role/vra-deploy-prod",
                       {"iam:PassedToService": "lambda.amazonaws.com"})
    # (c) roles only with the boundary; admin policies never attachable; boundary never removable
    assert not allowed(S, "iam:CreateRole", role)
    assert not allowed(S, "iam:CreateRole", role, {"iam:PermissionsBoundary": f"arn:{P}:iam::{ACCT}:policy/other"})
    assert allowed(S, "iam:CreateRole", role, {"iam:PermissionsBoundary": BOUNDARY})
    for admin in ("AdministratorAccess", "PowerUserAccess", "IAMFullAccess", "AmazonS3FullAccess"):
        assert not allowed(S, "iam:AttachRolePolicy", role, {"iam:PolicyARN": f"arn:{P}:iam::aws:policy/{admin}"})
    assert allowed(S, "iam:AttachRolePolicy", role,
                   {"iam:PolicyARN": f"arn:{P}:iam::aws:policy/AWSXrayWriteOnlyAccess"})
    assert not allowed(S, "iam:DeleteRolePermissionsBoundary", role)
    # (d) no cloudtrail:*; foreign trails cannot be stopped or deleted, VAMS's own trail can be managed
    assert not any(a in ("cloudtrail:*", "kms:*") for s in S if s["Effect"] == "Allow" for a in _as_list(s["Action"]))
    org = f"arn:{P}:cloudtrail:us-east-1:{ACCT}:trail/org-trail"
    vams = f"arn:{P}:cloudtrail:us-east-1:{ACCT}:trail/vams-core-dev-us-east-1-CloudTrailVAMS1A2B"
    for a in ("cloudtrail:StopLogging", "cloudtrail:DeleteTrail", "cloudtrail:UpdateTrail"):
        assert not allowed(S, a, org, rq)
        assert allowed(S, a, vams, rq)
    # (e) keys VAMS does not own: no PutKeyPolicy / ScheduleKeyDeletion; VAMS's own keys: yes
    key = f"arn:{P}:kms:us-east-1:{ACCT}:key/1234"
    for a in ("kms:PutKeyPolicy", "kms:ScheduleKeyDeletion", "kms:DisableKey"):
        assert not allowed(S, a, key, rq)
        assert not allowed(S, a, key, {**rq, "aws:ResourceTag/aws:cloudformation:stack-name": "security-baseline"})
        assert allowed(S, a, key, {**rq, "aws:ResourceTag/aws:cloudformation:stack-name": "vams-core-dev-x"})
    # the protected deployment roles stay protected
    assert not allowed(S, "iam:AttachRolePolicy", f"arn:{P}:iam::{ACCT}:role/cdk-hnb659fds-deploy-role-{ACCT}-us-east-1",
                       {"iam:PolicyARN": f"arn:{P}:iam::aws:policy/AWSXrayWriteOnlyAccess"})


def test_role_boundary_denies_iam_escalation_by_vams_roles():
    t = load_cfn("iam/cloudformation/vra-cdk-execution-policy.yaml")
    doc = render(t["Resources"]["RoleBoundary"]["Properties"]["PolicyDocument"],
                 {"AWS::Partition": P}, {}, {})
    S = doc["Statement"]
    assert allowed(S, "s3:GetObject", "arn:aws:s3:::b/k")
    for a in ("iam:CreateRole", "iam:AttachRolePolicy", "iam:CreatePolicyVersion", "cloudtrail:StopLogging",
              "kms:ScheduleKeyDeletion", "organizations:LeaveOrganization"):
        assert not allowed(S, a, "*"), a
    assert not allowed(S, "sts:AssumeRole", f"arn:{P}:iam::{ACCT}:role/vra-deploy-prod")


# ------------------------------------------------------------------ deploy role, OIDC, data copy, state store
def test_deploy_role_can_require_source_identity_and_limits_session_tags():
    t = load_cfn("iam/cloudformation/vra-deploy-role.yaml")
    params = {"AWS::Partition": P, "TrustedPrincipalArns": ["arn:aws:iam::444444444444:role/ci"], "ExternalId": ""}
    trust = render(t["Resources"]["DeployRole"]["Properties"]["AssumeRolePolicyDocument"], params,
                   {"HasExternalId": False, "SourceIdentityRequired": True}, {})["Statement"]
    assume = next(s for s in trust if s["Action"] == ["sts:AssumeRole"])
    assert assume["Condition"] == {"Null": {"sts:SourceIdentity": "false"}}
    tag = next(s for s in trust if s["Action"] == ["sts:TagSession"])
    assert tag["Condition"] == {"ForAllValues:StringLike": {"aws:TagKeys": "vra:*"}}
    assert any(s["Action"] == ["sts:SetSourceIdentity"] for s in trust)
    restore_stmt = next(s for s in t["Resources"]["RestorePolicy"]["Properties"]["PolicyDocument"]["Statement"]
                        if s.get("Sid") == "TempTableLifecycle")
    assert "dynamodb:RestoreTableFromBackup" in restore_stmt["Action"]


def _statements(node):
    """Every IAM statement (dict with Action) anywhere in a loaded template."""
    if isinstance(node, dict):
        if "Action" in node and "Effect" in node:
            yield node
        for v in node.values():
            yield from _statements(v)
    elif isinstance(node, list):
        for v in node:
            yield from _statements(v)


def test_drift_detection_grants_the_per_resource_action_it_needs():
    """Live test: the first approval-gated promote (test1) stopped BLOCKED_ON_IT at deploy.test.drift:
    CloudFormation's DetectStackDrift runs DetectStackResourceDrift as the caller, and the deploy role, the deploy
    and rollback manifests granted only DetectStackDrift and DescribeStackResourceDrifts."""
    t = load_cfn("iam/cloudformation/vra-deploy-role.yaml")
    stmts = [s for s in _statements(t["Resources"]) if "cloudformation:DetectStackDrift" in _as_list(s["Action"])]
    assert stmts and all("cloudformation:DetectStackResourceDrift" in _as_list(s["Action"]) for s in stmts)
    for rel in ("iam/actions/deploy.yaml", "iam/actions/rollback.yaml"):
        doc = yaml.safe_load((PKG / rel).read_text())
        found = [s for s in _statements_manifest(doc) if "cloudformation:DetectStackDrift" in s.get("actions", [])]
        assert found and all("cloudformation:DetectStackResourceDrift" in s["actions"] for s in found), rel


def _statements_manifest(node):
    if isinstance(node, dict):
        if "actions" in node and "resources" in node:
            yield node
        for v in node.values():
            yield from _statements_manifest(v)
    elif isinstance(node, list):
        for v in node:
            yield from _statements_manifest(v)


def test_ci_oidc_role_is_pinned_to_its_own_envs():
    """Resources are tag-pinned to DeployableEnvs (default: the CI env itself), history is never deleted."""
    t = load_cfn("cicd/oidc/vra-ci-oidc-role.yaml")
    params = {"AWS::Partition": P, "AWS::Region": "us-east-1", "AWS::AccountId": ACCT, "CiEnvName": "dev",
              "DeployableEnvs": [""], "EnvAccountIds": ["222222222222"], "StateBucketName": "vra-state-x",
              "LockTableName": "vra-locks", "StateKeyArn": "", "Provider": "github"}
    conds = {"OwnEnvOnly": True, "NoEnvRoles": False, "SynthRoles": False, "HasStateStore": True, "HasStateKey": False,
             "HasSigningKey": False, "MaySign": False}
    S = render(t["Resources"]["CiPolicy"]["Properties"]["PolicyDocument"], params, conds, {})["Statement"]
    ctx_dev = {"aws:ResourceAccount": "222222222222", "aws:ResourceTag/vra:env": "dev"}
    ctx_prod = {"aws:ResourceAccount": "222222222222", "aws:ResourceTag/vra:env": "prod"}
    assert allowed(S, "sts:AssumeRole", f"arn:{P}:iam::222222222222:role/vra-deploy-dev", ctx_dev)
    assert not allowed(S, "sts:AssumeRole", f"arn:{P}:iam::222222222222:role/vra-deploy-prod", ctx_prod)
    assert not allowed(S, "s3:DeleteObject", "arn:aws:s3:::vra-state-x/vra/history/dev/x.json")
    # the build role: no env roles at all, only build-only synth roles
    conds_b = {**conds, "OwnEnvOnly": False, "NoEnvRoles": True, "SynthRoles": True}
    Sb = render(t["Resources"]["CiPolicy"]["Properties"]["PolicyDocument"], {**params, "DeployableEnvs": ["none"]},
                conds_b, {})["Statement"]
    assert not allowed(Sb, "sts:AssumeRole", f"arn:{P}:iam::222222222222:role/vra-deploy-dev", ctx_dev)
    assert allowed(Sb, "sts:AssumeRole", f"arn:{P}:iam::222222222222:role/vra-synth-dev",
                   {"aws:ResourceAccount": "222222222222"})
    # With a signing key every CI role verifies; only the build role (AllowSigning) signs
    key = f"arn:{P}:kms:us-east-1:{ACCT}:key/k"
    Sv = render(t["Resources"]["CiPolicy"]["Properties"]["PolicyDocument"], {**params, "SigningKeyArn": key},
                {**conds, "HasSigningKey": True}, {})["Statement"]
    assert allowed(Sv, "kms:Verify", key) and not allowed(Sv, "kms:Sign", key)
    Ss = render(t["Resources"]["CiPolicy"]["Properties"]["PolicyDocument"], {**params, "SigningKeyArn": key},
                {**conds_b, "HasSigningKey": True, "MaySign": True}, {})["Statement"]
    assert allowed(Ss, "kms:Sign", key)


def test_data_copy_target_role_never_reads_source_vams_buckets():
    """The target loader reads only the staging bucket; the source role reads source tables (incl. Scan)."""
    t = load_cfn("iam/cloudformation/vra-data-copy-roles.yaml")
    params = {"AWS::Partition": P, "AWS::Region": "us-east-1", "AWS::AccountId": "222222222222",
              "PeerAccountId": "333333333333", "ConfigName": "vams", "BaseStackName": "test", "PeerEnvName": "prod",
              "EnvName": "test"}
    conds = {"TargetChainsToPeer": False, "TargetMayAbort": False, "TargetAdditionalBuckets": False,
             "SourceAdditionalBuckets": False, "HasAdditionalBuckets": False, "HasAdditionalKeys": False}
    target = render(t["Resources"]["TargetPolicy"]["Properties"]["PolicyDocument"], params, conds, {})["Statement"]
    common = render(t["Resources"]["CommonPolicy"]["Properties"]["PolicyDocument"], params, {}, {})["Statement"]
    S = target + common
    assert not allowed(S, "s3:GetObject", "arn:aws:s3:::vams-core-prod-us-east-1-assetbucket/k")
    assert allowed(S, "s3:GetObject", "arn:aws:s3:::vra-data-copy-333333333333-us-east-1/vra/r/assets/b/k")
    assert allowed(S, "s3:PutObject", "arn:aws:s3:::vams-core-test-us-east-1-assetbucket/k")
    src = render(t["Resources"]["SourcePolicy"]["Properties"]["PolicyDocument"],
                 {**params, "AWS::AccountId": "333333333333", "BaseStackName": "prod"}, conds,
                 {"StagingBucket": "arn:aws:s3:::vra-data-copy-333333333333-us-east-1",
                  "StagingKey": "arn:aws:kms:us-east-1:333333333333:key/k"})["Statement"]
    assert allowed(src, "dynamodb:Scan", "arn:aws:dynamodb:us-east-1:333333333333:table/vams-core-prod-us-east-1-T")
    chained = render(t["Resources"]["TargetPolicy"]["Properties"]["PolicyDocument"], params,
                     {**conds, "TargetChainsToPeer": True}, {})["Statement"]
    assert allowed(chained, "sts:AssumeRole", "arn:aws:iam::333333333333:role/vra-data-copy-source-prod")


def test_state_store_protects_history():
    t = load_cfn("iam/cloudformation/vra-state-store.yaml")
    params = {"AWS::Partition": P, "HistoryAdminPrincipalArns": [""]}
    doc = render(t["Resources"]["StateBucketPolicy"]["Properties"]["PolicyDocument"], params,
                 {"ProtectHistoryOn": True}, {"StateBucket": "arn:aws:s3:::vra-state-x"})
    deny = next(s for s in doc["Statement"] if s.get("Sid") == "ProtectHistory")
    assert "s3:DeleteObject" in deny["Action"] and deny["Resource"] == ["arn:aws:s3:::vra-state-x/*/history/*"]


def test_data_copy_target_manifest_covers_cmk_tables():
    """The target loader scans / deletes / writes CMK tables through DynamoDB."""
    m = yaml.safe_load((PKG / "iam/actions/data-copy-target.yaml").read_text())
    st = next(s for s in m["statements"] if s["sid"] == "TargetDataKms")
    assert {"kms:Decrypt", "kms:DescribeKey"} <= set(st["actions"])
    assert "dynamodb.*" in st["condition"]["StringLike"]["kms:ViaService"]
    chain = next(s for s in m["statements"] if s["sid"] == "ChainToSourceExportRole")
    assert chain["optional"] is True


# ------------------------------------------------------------------ approval writers (GitHub)
def _writer():
    spec = importlib.util.spec_from_file_location("approval_record", PKG / "cicd/codebuild/scripts/approval_record.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_codepipeline_record_uses_only_this_executions_approval():
    w = _writer()
    env = {"VRA_PIPELINE_NAME": "vra-release", "VRA_PIPELINE_EXECUTION_ID": "exec-B", "CODEBUILD_BUILD_ID": "b:1"}
    detail = lambda exe, who, status="Succeeded": {  # noqa: E731
        "pipelineExecutionId": exe, "stageName": "Approve-prod", "actionName": "FinalApproval-prod",
        "status": status, "updatedBy": who, "lastUpdateTime": "2026-11-03T05:00:00Z",
        "output": {"executionResult": {"externalExecutionSummary": "ticket: CHG-9; notes: ok"}}}
    data = {"actionExecutionDetails": [detail("exec-A", "arn:aws:sts::1:assumed-role/A/alice"),
                                       detail("exec-B", "arn:aws:sts::1:assumed-role/A/bob")]}
    rec = w.from_codepipeline(data, "Approve-prod", "FinalApproval-prod", "prod", "cust-v2.6.6-1", env)
    assert rec["approver"].endswith("/bob") and rec["source"] == "codepipeline:vra-release/exec-B"
    assert rec["scope"] == ["promote"] and rec["expiresAt"] and rec["ticket"] == "CHG-9"
    assert rec["binding"]["codebuildBuildId"] == "b:1"
    with pytest.raises(w.Refused, match="no execution"):
        w.from_codepipeline({"actionExecutionDetails": [detail("exec-A", "x")]}, "Approve-prod", "FinalApproval-prod",
                            "prod", "b", env)
    with pytest.raises(w.Refused, match="InProgress"):
        w.from_codepipeline({"actionExecutionDetails": [detail("exec-B", "x", "InProgress")]}, "Approve-prod",
                            "FinalApproval-prod", "prod", "b", env)


def test_gitlab_record_never_takes_the_job_player_as_approver():
    w = _writer()
    env = {"GITLAB_USER_LOGIN": "operator", "CI_PROJECT_PATH": "grp/ledger", "CI_PIPELINE_ID": "77", "CI_JOB_ID": "9"}
    dep = {"id": 5, "approvals": [{"user": {"username": "operator"}, "status": "approved"}]}
    with pytest.raises(w.Refused, match="separation of duties"):
        w.from_gitlab(dep, "prod", "b", env)
    with pytest.raises(w.Refused, match="no approvals"):
        w.from_gitlab({"id": 5, "approvals": []}, "prod", "b", env)
    dep["approvals"].append({"user": {"username": "release-mgr"}, "status": "approved", "created_at": "t"})
    rec = w.from_gitlab(dep, "prod", "b", env)
    assert rec["approver"] == "release-mgr" and rec["source"] == "gitlab:grp/ledger/pipelines/77"


def test_github_record_refuses_self_review_and_matches_engine_run_ref(monkeypatch):
    w = _writer()
    env = {"GITHUB_TRIGGERING_ACTOR": "alice", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "3"}
    ap = [{"state": "approved", "environments": [{"name": "prod"}], "user": {"login": "alice"}}]
    with pytest.raises(w.Refused, match="initiator"):
        w.from_github(ap, "prod", "b", env)
    ap.append({"state": "approved", "environments": [{"name": "prod"}], "user": {"login": "bob"}, "comment": ""})
    rec = w.from_github(ap, "prod", "b", env)
    for k, v in {**env, "GITHUB_ACTIONS": "true"}.items():
        monkeypatch.setenv(k, v)
    from vra import identity
    assert rec["source"] == identity.resolve().run_ref and rec["approver"] == "bob"


# ------------------------------------------------------------------ CI lane wiring
def test_ci_lanes_test_the_build_commit_and_keep_gated_roles_out_of_the_build_job():
    gha_deploy = (PKG / "cicd/github-actions/vra-deploy-env.yml").read_text()
    assert "ref: ${{ steps.commit.outputs.sha }}" in gha_deploy and "VRA_FORK_TRUNK" not in gha_deploy
    assert "approval_record.py github" in gha_deploy
    gha_release = yaml.safe_load((PKG / "cicd/github-actions/deploy.yml").read_text())
    build_steps = json.dumps(gha_release["jobs"]["build"]["steps"])
    assert "preflight" not in build_steps
    gl = (PKG / "cicd/gitlab-ci/.gitlab-ci.yml").read_text()
    assert "approval_record.py gitlab" in gl and '"approver": os.environ.get("GITLAB_USER_LOGIN"' not in gl
    assert "--depth 1" not in gl and 'checkout --quiet "${commit}"' in gl
    gl_doc = yaml.safe_load(gl)
    assert "preflight" not in json.dumps(gl_doc["validate"]["script"])
    spec = (PKG / "cicd/codebuild/scripts/record-approval.sh").read_text()
    assert "list-action-executions" in spec and "pipelineExecutionId=${VRA_PIPELINE_EXECUTION_ID}" in spec
    assert "get-pipeline-state" not in spec
