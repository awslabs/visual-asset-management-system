# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber

from vra import errors, permissions, runner, util
from vra.errors import PermissionDenied
from vra.runner import workflow

from conftest import FakeAws

GOV_CFN = ('Resource handler returned message: "User: arn:aws-us-gov:sts::111111111111:assumed-role/'
           'cdk-hnb659fds-cfn-exec-role-111111111111-us-gov-west-1/AWSCloudFormation is not authorized to perform: '
           'iam:CreateRole on resource: arn:aws-us-gov:iam::111111111111:role/vams-core-dev-us-gov-west-1-AuthRole '
           'because no identity-based policy allows the iam:CreateRole action (Service: Iam, Status Code: 403, '
           'Request ID: 0f0e1c2d-1111-2222-3333-444455556666)" (RequestToken: abc, HandlerErrorCode: AccessDenied)')
SCP_CFN = ('User: arn:aws:sts::333333333333:assumed-role/cdk-hnb659fds-cfn-exec-role-333333333333-us-east-1/'
           'AWSCloudFormation is not authorized to perform: wafv2:CreateWebACL on resource: '
           'arn:aws:wafv2:us-east-1:333333333333:global/webacl/vams-waf/* with an explicit deny in a service control '
           'policy')
BOUNDARY = ('User: arn:aws:sts::111111111111:assumed-role/vra-deploy/vra-run is not authorized to perform: '
            'cloudformation:CreateChangeSet on resource: arn:aws:cloudformation:us-east-1:111111111111:stack/'
            'vams-core-dev-us-east-1/* because no permissions boundary allows the cloudformation:CreateChangeSet action')
OLD_EC2 = "API: ec2:CreateVpc You are not authorized to perform this operation."
CDK_TRUST = ("current credentials could not be used to assume 'arn:aws:iam::111111111111:role/"
             "cdk-hnb659fds-deploy-role-111111111111-us-east-1', but are for the right account. Proceeding anyway.")
KMS = ("Resource handler returned message: \"The ciphertext refers to a customer managed key that does not exist, "
       "does not exist in this region, or you are not allowed to access. (Service: Kms, Status Code: 400)\"")


def test_classify_cloudformation_govcloud():
    d = permissions.classify_text(GOV_CFN, "cloudformation")
    assert len(d) == 1
    assert d[0]["action"] == "iam:CreateRole"
    assert d[0]["resource"] == "arn:aws-us-gov:iam::111111111111:role/vams-core-dev-us-gov-west-1-AuthRole"
    assert d[0]["principal"].startswith("arn:aws-us-gov:sts::111111111111:assumed-role/cdk-hnb659fds-cfn-exec-role")
    assert d[0]["kind"] == "identity"


@pytest.mark.parametrize("text,kind,action", [
    (SCP_CFN, "scp", "wafv2:CreateWebACL"),
    (BOUNDARY, "boundary", "cloudformation:CreateChangeSet"),
    (OLD_EC2, "unknown", "ec2:CreateVpc"),
    (CDK_TRUST, "cdk-bootstrap-trust", "sts:AssumeRole"),
])
def test_classify_kinds(text, kind, action):
    d = permissions.classify_text(text)
    assert d and d[0]["kind"] == kind and d[0]["action"] == action


def test_classify_kms_hint():
    d = permissions.classify_text(KMS)
    assert d and d[0]["kind"] == "kms"


def _ce(code, msg, op):
    return ClientError({"Error": {"Code": code, "Message": msg}}, op)


def test_classify_botocore():
    exc = _ce("AccessDeniedException",
              "User: arn:aws:sts::111111111111:assumed-role/vra-deploy/s is not authorized to perform: "
              "dynamodb:CreateBackup on resource: arn:aws:dynamodb:us-east-1:111111111111:table/vams-AssetStorage "
              "because no identity-based policy allows the dynamodb:CreateBackup action", "CreateBackup")
    d = permissions.classify_exception(exc, "dynamodb")
    assert d[0]["action"] == "dynamodb:CreateBackup" and d[0]["resource"].endswith("table/vams-AssetStorage")
    # S3 "Access Denied" carries nothing; operation -> IAM action mapping fills it in
    d = permissions.classify_exception(_ce("AccessDenied", "Access Denied", "ListObjectsV2"), "s3", "arn:aws:s3:::b")
    assert d[0]["action"] == "s3:ListBucket" and d[0]["resource"] == "arn:aws:s3:::b"
    assert permissions.classify_exception(_ce("ThrottlingException", "Rate exceeded", "Scan"), "dynamodb") == []
    assert permissions.is_denied(_ce("UnauthorizedOperation", "x", "RunInstances"))
    assert not permissions.is_denied(_ce("ValidationError", "x", "DescribeStacks"))


def test_encoded_message_decoded():
    text = "You are not authorized to perform this operation. Encoded authorization failure message: " + "A" * 40
    d = permissions.classify_text(text)
    assert d[0]["kind"] == "encoded"
    sts = boto3.client("sts", region_name="us-east-1")
    st = Stubber(sts)
    st.add_response("decode_authorization_message", {"DecodedMessage": json.dumps({
        "allowed": False, "explicitDeny": False, "context": {
            "principal": {"arn": "arn:aws:sts::111111111111:assumed-role/vra-deploy/s"},
            "action": "ec2:RunInstances", "resource": "arn:aws:ec2:us-east-1:111111111111:instance/*"}})})
    with st:
        out = permissions.decode_encoded(sts, d)
    assert out[0]["action"] == "ec2:RunInstances" and out[0]["kind"] == "identity"


def test_consolidated_policy_least_privilege():
    denials = permissions.classify_text(GOV_CFN + "\n" + SCP_CFN + "\n" + GOV_CFN.replace("iam:CreateRole", "iam:TagRole"))
    pol = permissions.consolidated_policy(denials)
    assert pol["Version"] == "2012-10-17"
    assert len(pol["Statement"]) == 1  # SCP denials are not fixable by policy
    st = pol["Statement"][0]
    assert st["Action"] == ["iam:CreateRole", "iam:TagRole"]
    assert st["Resource"].startswith("arn:aws-us-gov:iam::")
    assert "*" not in json.dumps(pol)


ENGINE_WORKFLOWS = ("deploy", "backup", "restore", "migrate", "data-copy-source", "data-copy-target", "test",
                    "build", "rollback")


def test_packaged_manifests_valid_and_partition_generic(monkeypatch):
    from vra import data as vra_data
    monkeypatch.setattr(permissions, "manifest_dirs", lambda: [vra_data.path("iam-actions")])
    for w in ENGINE_WORKFLOWS:
        m = permissions.load_manifest(w)
        assert "arn:aws:" not in json.dumps(m)
    vals = {"Partition": "aws-us-gov", "Region": "us-gov-west-1", "Account": "111111111111", "ConfigName": "vams",
            "CoreStack": "vams-core-dev-us-gov-west-1", "SsmPrefix": "/vams-dev-us-gov-west-1/resourceNames",
            "CdkQualifier": "hnb659fds"}
    st = permissions.expand_manifest(permissions.load_manifest("backup"), vals)
    resources = [r for s in st for r in s["resources"]]
    assert "arn:aws-us-gov:ssm:us-gov-west-1:111111111111:parameter/vams-dev-us-gov-west-1/resourceNames/*" in resources


def test_packaged_manifests_equal_reviewed_iam_actions():
    # iam/actions/ is the single source; the engine ships byte-identical copies for non-editable installs
    from pathlib import Path
    from vra import data as vra_data
    reviewed = Path(__file__).resolve().parents[2] / "iam" / "actions"
    packaged = Path(str(vra_data.path("iam-actions")))
    names = sorted(p.name for p in reviewed.glob("*.yaml"))
    assert names == sorted(p.name for p in packaged.glob("*.yaml"))
    for n in names:
        assert (reviewed / n).read_bytes() == (packaged / n).read_bytes(), n


def test_backup_manifest_covers_cmk_encrypted_tables():
    """E2E finding (W9): a checkpoint of a stack with useKmsCmkEncryption was denied kms:Decrypt on CreateBackup,
    but the backup manifest had no KMS statement, so `vra preflight --workflow backup` (and the checkpoint inside
    deploy/data-copy) could not predict the gap. The manifest must simulate kms:Decrypt via DynamoDB."""
    vals = {"Partition": "aws", "Region": "us-east-1", "Account": "111111111111", "ConfigName": "vams",
            "CoreStack": "vams-core-dev-us-east-1", "SsmPrefix": "/vams-dev-us-east-1/resourceNames",
            "CdkQualifier": "hnb659fds"}
    st = permissions.expand_manifest(permissions.load_manifest("backup"), vals)
    kms = [s for s in st if "kms:Decrypt" in s["actions"]]
    assert kms, "backup manifest has no kms:Decrypt statement"
    assert any(r.startswith("arn:aws:kms:us-east-1:111111111111:key/") for s in kms for r in s["resources"])
    raw = [s for s in permissions.load_manifest("backup")["statements"] if "kms:Decrypt" in s["actions"]]
    assert "dynamodb" in json.dumps([s.get("condition") for s in raw])  # scoped to use through DynamoDB


def test_every_engine_workflow_resolves_a_manifest():
    # iam/actions/ (Lane C) first, packaged defaults as fallback; every engine lookup must resolve + validate
    for w in ENGINE_WORKFLOWS:
        m = permissions.load_manifest(w)
        assert "arn:aws:" not in json.dumps({k: v for k, v in m.items() if k != "_path"})


def test_tools_statements_not_simulated_against_env_role():
    m = {"workflow": "x", "statements": [{"sid": "ToolsAssume", "actions": ["sts:AssumeRole"], "resources": ["*"]},
                                         {"sid": "Env", "actions": ["s3:ListBucket"], "resources": ["*"]}]}
    st = permissions.expand_manifest(m, {})
    assert [s["tools"] for s in st] == [True, False]


# ------------------------------------------------------------------ IT request + resume loop
def test_simulate_with_botocore_stubber():
    iam = boto3.client("iam", region_name="us-east-1")
    st = Stubber(iam)
    st.add_response("simulate_principal_policy", {"EvaluationResults": [
        {"EvalActionName": "dynamodb:CreateBackup", "EvalDecision": "allowed", "EvalResourceName": "*"},
        {"EvalActionName": "dynamodb:ListBackups", "EvalDecision": "explicitDeny", "EvalResourceName": "*",
         "OrganizationsDecisionDetail": {"AllowedByOrganizations": False}}], "IsTruncated": False},
        {"PolicySourceArn": "arn:aws-us-gov:iam::111111111111:role/vra-deploy", "ActionNames": ANY,
         "ResourceArns": ["arn:aws-us-gov:dynamodb:us-gov-west-1:111111111111:table/*"]})
    with st:
        res = permissions.simulate(iam, "arn:aws-us-gov:iam::111111111111:role/vra-deploy", [
            {"sid": "x", "actions": ["dynamodb:CreateBackup", "dynamodb:ListBackups"],
             "resources": ["arn:aws-us-gov:dynamodb:us-gov-west-1:111111111111:table/*"], "optional": False}])
    assert res["allowed"] == 1 and res["denied"][0]["kind"] == "scp"


def test_simulate_resimulates_resource_agnostic_actions_against_star():
    """E2E finding: cloudformation:DescribeStackDriftDetectionStatus has no resource-level permissions. IAM
    simulates it against a stack ARN as implicitDeny with its own ARN template as EvalResourceName, so an
    Admin role produced an IT request. The engine must re-simulate such actions against '*'."""
    calls = []

    class Iam:
        def simulate_principal_policy(self, **kw):
            calls.append(kw)
            out = []
            for a in kw["ActionNames"]:
                if a == "cloudformation:DescribeStackDriftDetectionStatus" and kw.get("ResourceArns"):
                    out.append({"EvalActionName": a, "EvalDecision": "implicitDeny",
                                "EvalResourceName": "arn:aws:cloudformation:${Region}:${Account}:${ResourceType}/${Id}"})
                else:
                    out.append({"EvalActionName": a, "EvalDecision": "allowed", "EvalResourceName": "*"})
            return {"EvaluationResults": out, "IsTruncated": False}

    res = permissions.simulate(Iam(), "arn:aws:iam::111111111111:role/vra-deploy", [
        {"sid": "x", "actions": ["cloudformation:DescribeStacks", "cloudformation:DescribeStackDriftDetectionStatus"],
         "resources": ["arn:aws:cloudformation:*:111111111111:stack/vams-*/*"], "optional": False}])
    assert res["denied"] == [] and res["allowed"] == 2
    assert calls[1] == {"PolicySourceArn": "arn:aws:iam::111111111111:role/vra-deploy",
                        "ActionNames": ["cloudformation:DescribeStackDriftDetectionStatus"]}


def test_simulate_resource_agnostic_real_denial_requests_star():
    class Iam:
        def simulate_principal_policy(self, **kw):
            res = "arn:aws:cloudformation:${Region}:${Account}:${ResourceType}/${Id}" if kw.get("ResourceArns") else "*"
            return {"EvaluationResults": [{"EvalActionName": a, "EvalDecision": "implicitDeny", "EvalResourceName": res}
                                          for a in kw["ActionNames"]], "IsTruncated": False}

    res = permissions.simulate(Iam(), "arn:aws:iam::111111111111:role/vra-deploy", [
        {"sid": "x", "actions": ["cloudformation:DescribeStackDriftDetectionStatus"],
         "resources": ["arn:aws:cloudformation:*:111111111111:stack/vams-*/*"], "optional": False}])
    assert [d["resource"] for d in res["denied"]] == ["*"]


def test_simulate_retries_throttling(monkeypatch):
    """E2E finding: `preflight --all` hit `Throttling: Rate exceeded` on SimulatePrincipalPolicy and the whole
    run failed with exit 1. Throttling is retried with backoff."""
    from botocore.exceptions import ClientError
    monkeypatch.setattr(permissions, "_sleep", lambda s: None)
    n = {"calls": 0}

    class Iam:
        def simulate_principal_policy(self, **kw):
            n["calls"] += 1
            if n["calls"] < 3:
                raise ClientError({"Error": {"Code": "Throttling", "Message": "Rate exceeded"}},
                                  "SimulatePrincipalPolicy")
            return {"EvaluationResults": [{"EvalActionName": "s3:ListBucket", "EvalDecision": "allowed",
                                           "EvalResourceName": "*"}], "IsTruncated": False}

    res = permissions.simulate(Iam(), "arn:aws:iam::111111111111:role/r", [
        {"sid": "x", "actions": ["s3:ListBucket"], "resources": ["*"], "optional": False}])
    assert res["allowed"] == 1 and n["calls"] == 3


class SimIam:
    """Minimal fake IAM: allows everything except a configurable denied set (mutable between calls)."""

    def __init__(self, denied):
        self.denied = set(denied)
        self.calls = []

    def simulate_principal_policy(self, **kw):
        self.calls.append(kw)
        return {"EvaluationResults": [{"EvalActionName": a, "EvalResourceName": (kw.get("ResourceArns") or ["*"])[0],
                                       "EvalDecision": "implicitDeny" if a in self.denied else "allowed"}
                                      for a in kw["ActionNames"]], "IsTruncated": False}


def test_preflight_it_request_and_resume_loop(ctx, monkeypatch):
    from vra import data as vra_data
    monkeypatch.setattr(permissions, "manifest_dirs", lambda: [vra_data.path("iam-actions")])
    from conftest import stub_inventory
    stub_inventory(monkeypatch)
    iam = SimIam({"dynamodb:CreateBackup", "s3:GetBucketVersioning"})
    ctx.aws = FakeAws(ctx.ledger, {"iam": iam})
    out = runner.start(ctx, "preflight", {"env": "dev", "workflow": "backup"}, env="dev")
    assert out.exit_code == errors.BLOCKED_ON_IT
    run = out.run
    assert run.status == "BLOCKED_ON_IT"
    req = util.read_json(run.it_dir / "IT-REQUEST-1.json")
    assert sorted(req["actions"]) == ["dynamodb:CreateBackup", "s3:GetBucketVersioning"]
    assert req["principals"] == ["arn:aws:iam::111111111111:role/vra-deploy"]
    md = (run.it_dir / "IT-REQUEST-1.md").read_text()
    assert "dynamodb:CreateBackup" in md and "vra resume" in md and '"Version": "2012-10-17"' in md
    assert run.find_step("preflight.dev.backup")["status"] == "blocked"

    # IT fixed only one action -> resume re-simulates exactly those actions and writes IT-REQUEST-2
    iam.denied = {"s3:GetBucketVersioning"}
    iam.calls.clear()
    out2 = runner.resume(ctx, run.run_id)
    assert out2.exit_code == errors.BLOCKED_ON_IT
    simulated = sorted(a for c in iam.calls for a in c["ActionNames"])
    assert simulated == ["dynamodb:CreateBackup", "s3:GetBucketVersioning"]
    req2 = util.read_json(out2.run.it_dir / "IT-REQUEST-2.json")
    assert req2["actions"] == ["s3:GetBucketVersioning"]
    assert [r["status"] for r in out2.run.state["itRequests"]] == ["superseded", "open"]

    # everything granted -> resume continues and succeeds
    iam.denied = set()
    out3 = runner.resume(ctx, run.run_id)
    assert out3.exit_code == 0, out3.message
    assert out3.run.status == "SUCCEEDED"
    assert out3.run.state["itRequests"][-1]["status"] == "resolved"


ATTEMPTS = {"n": 0}


@workflow("_test_cfn_denied")
def _wf(ctx, run, args):
    def deploy(sc):
        ATTEMPTS["n"] += 1
        if ATTEMPTS["n"] == 1:
            raise PermissionDenied("cdk deploy denied", permissions.classify_text(KMS + "\n" + GOV_CFN),
                                   env="dev")
        return "deployed"
    return {"r": run.step("deploy", deploy)}


def test_step_denial_from_cloudformation_then_resume_retries(ctx):
    ATTEMPTS["n"] = 0
    out = runner.start(ctx, "_test_cfn_denied", {}, env="dev")
    assert out.exit_code == errors.BLOCKED_ON_IT
    req = util.read_json(out.run.it_dir / "IT-REQUEST-1.json")
    assert "kms" in req["kinds"] and "iam:CreateRole" in req["actions"]
    # KMS denials cannot be proven by simulation -> resume retries the real step
    out2 = runner.resume(ctx, out.run.run_id)
    assert out2.exit_code == 0 and ATTEMPTS["n"] == 2


def test_resume_does_not_crash_on_an_it_request_whose_principal_is_in_another_account(tmp_path):
    """Live test: a synth child fell back to the build host's role (another account); its IT request named that
    principal, and `vra resume` crashed with InvalidInput from SimulatePrincipalPolicy. Such a request is not
    simulable in the env's account: the resume retries the real step instead."""
    import json as _json
    from types import SimpleNamespace

    from vra import permissions as perms

    req = {"env": "test", "role": "synth", "principals": ["arn:aws:sts::555555555555:assumed-role/HostRole/i-0abc"],
           "denials": [{"action": "sts:AssumeRole", "resource": "arn:aws:iam::222222222222:role/cdk-lookup",
                        "kind": "identity"}]}
    (tmp_path / "IT-REQUEST-1.json").write_text(_json.dumps(req))

    class NoIam:
        def client(self, *a, **k):
            raise AssertionError("must not simulate a principal from another account")

    ctx = SimpleNamespace(dry_run=False, aws=NoIam(),
                          ledger=SimpleNamespace(env=lambda n: {"account": "222222222222", "region": "us-east-1"}))
    run = SimpleNamespace(it_dir=tmp_path)
    assert perms.resimulate(ctx, run, {"id": "IT-REQUEST-1"}) is None

    class InvalidInputIam:
        def client(self, *a, **k):
            return self

        def simulate_principal_policy(self, **kw):
            raise ClientError({"Error": {"Code": "InvalidInput", "Message": "Invalid Policy Source Arn"}},
                              "SimulatePrincipalPolicy")

    req["principals"] = ["arn:aws:sts::222222222222:assumed-role/Gone/x"]
    (tmp_path / "IT-REQUEST-1.json").write_text(_json.dumps(req))
    ctx.aws = InvalidInputIam()
    assert perms.resimulate(ctx, run, {"id": "IT-REQUEST-1"}) is None


# ------------------------------------------------------------------ (found live)
_B8_VALS = {"Partition": "aws", "Region": "us-east-1", "Account": "111111111111", "ConfigName": "vams",
            "CoreStack": "vams-core-devel-us-east-1", "SsmPrefix": "/vams-devel-us-east-1/resourceNames",
            "BaseStackName": "devel-us-east-1", "CdkQualifier": "hnb659fds"}


class _ConditionAwareIam:
    """Allows a key/* KMS action only when the simulation carries kms:ViaService dynamodb.<region>... and
    kms:CallerAccount, the way vra-deploy-role.yaml's DataKms statement is conditioned; everything else allowed."""

    def __init__(self):
        self.calls = []

    def simulate_principal_policy(self, **kw):
        self.calls.append(kw)
        ctx = {e["ContextKeyName"]: e["ContextKeyValues"] for e in kw.get("ContextEntries") or []}
        ok_kms = (ctx.get("kms:ViaService", [""])[0].startswith("dynamodb.us-east-1.")
                  and ctx.get("kms:CallerAccount") == ["111111111111"])
        out = [{"EvalActionName": a, "EvalResourceName": "*",
                "EvalDecision": "allowed" if not a.startswith("kms:") or ok_kms else "implicitDeny"}
               for a in kw["ActionNames"]]
        return {"EvaluationResults": out, "IsTruncated": False}


def test_conditioned_manifest_statements_simulate_with_their_context():
    """, live on devel through the pipeline: the backup and migrate preflights of vra-deploy-dev (deployed
    from vra-deploy-role.yaml, whose DataKms grants kms:* on key/* only via dynamodb.* / s3.*) reported
    kms:Decrypt/DescribeKey as implicitDeny, because the manifest's ``condition:`` never reached
    SimulatePrincipalPolicy. The real call (through DynamoDB) is allowed, so the IT request was false."""
    st = permissions.expand_manifest(permissions.load_manifest("backup"), _B8_VALS, {})
    kms = next(s for s in st if s["sid"] == "TableKmsForBackup")
    assert {"ContextKeyName": "kms:ViaService", "ContextKeyValues": ["dynamodb.us-east-1.amazonaws.com"],
            "ContextKeyType": "string"} in kms["context"]
    assert {"ContextKeyName": "kms:CallerAccount", "ContextKeyValues": ["111111111111"],
            "ContextKeyType": "string"} in kms["context"]
    iam = _ConditionAwareIam()
    res = permissions.simulate(iam, "arn:aws:iam::111111111111:role/vra-deploy-dev", [s for s in st if not s["tools"]])
    assert res["denied"] == [], res["denied"]


def test_condition_context_is_partition_aware_and_skips_unresolved_values():
    ctx = permissions.condition_context({"StringLike": {"kms:ViaService": "s3.*"},
                                         "StringEquals": {"aws:ResourceTag/x": "${Nope}"}},
                                        {"Partition": "aws-cn", "Region": "cn-north-1"})
    assert ctx == [{"ContextKeyName": "kms:ViaService", "ContextKeyValues": ["s3.cn-north-1.amazonaws.com.cn"],
                    "ContextKeyType": "string"}]
    assert permissions.condition_context(None, {}) == []


def test_cognito_export_statement_is_simulated_only_when_the_env_exports():
    """, live: the backup preflight of every env demanded cognito-idp:ListUsers/ListGroups/... although
    ``cognitoExport`` defaults to false and the role template grants it only with EnableCognitoExport=true."""
    man = permissions.load_manifest("backup")
    off = [s["sid"] for s in permissions.expand_manifest(man, _B8_VALS, {})]
    on = [s["sid"] for s in permissions.expand_manifest(man, _B8_VALS, {"cognitoExport": "on"})]
    assert "CognitoExportOptional" not in off
    assert "CognitoExportOptional" in on


def test_env_features_turn_on_cognito_export(tmp_path):
    class L:
        project: dict = {}

        def __init__(self, e):
            self.e = e

        def env(self, name):
            return self.e

        def vams_config(self, name):
            return {}

    class C:
        pass

    c = C()
    c.ledger = L({"cognitoExport": True})
    assert permissions.env_features(c, "dev").get("cognitoExport") == "on"
    c.ledger = L({})
    assert "cognitoExport" not in permissions.env_features(c, "dev")


def test_drift_detection_status_is_granted_and_simulated_on_star():
    """, live: cloudformation:DescribeStackDriftDetectionStatus has no resource-level permissions, but
    vra-deploy-role.yaml granted it only on the VAMS stack ARNs, so vra-deploy-dev could not poll the drift
    detection `vra deploy` starts (preflight deploy: implicitDeny on `*`). Template and manifests grant it on `*`."""
    import yaml as _yaml
    from pathlib import Path
    pkg = Path(__file__).resolve().parents[2]
    action = "cloudformation:DescribeStackDriftDetectionStatus"
    for wf in ("deploy", "rollback"):
        sts = [s for s in permissions.load_manifest(wf)["statements"] if action in s["actions"]]
        assert sts and all(s.get("resources") == ["*"] for s in sts), wf

    class _L(_yaml.SafeLoader):
        pass

    _L.add_multi_constructor("!", lambda loader, suffix, node: None)
    ldr = _L((pkg / "iam/cloudformation/vra-deploy-role.yaml").read_text())  # a SafeLoader subclass
    try:
        t = ldr.get_single_data()
    finally:
        ldr.dispose()
    stmts = [s for s in t["Resources"]["DeployPolicy"]["Properties"]["PolicyDocument"]["Statement"]
             if isinstance(s, dict) and action in (s.get("Action") or [])]
    assert stmts and all(s.get("Resource") == "*" for s in stmts)


def test_simulate_splits_a_call_whose_actions_need_different_authorization():
    """Live test: IAM refuses one SimulatePrincipalPolicy call that mixes kms key actions with
    kms:ListKeys (InvalidInput "require different authorization information"); `vra iam verify` then left 73 of
    87 precreated roles unchecked. Such a call is split per action; other InvalidInput errors still raise."""
    from botocore.exceptions import ClientError
    calls = []

    class Iam:
        def simulate_principal_policy(self, **kw):
            calls.append(list(kw["ActionNames"]))
            if len(kw["ActionNames"]) > 1:
                raise ClientError({"Error": {"Code": "InvalidInput", "Message": "Invalid Input Actions: [kms:Decrypt] "
                                             "and [kms:ListKeys] require different authorization information."}},
                                  "SimulatePrincipalPolicy")
            return {"EvaluationResults": [{"EvalActionName": kw["ActionNames"][0], "EvalDecision": "allowed",
                                           "EvalResourceName": (kw.get("ResourceArns") or ["*"])[0]}]}

    res = permissions.simulate(Iam(), "arn:aws:iam::111111111111:role/r", [
        {"sid": None, "actions": ["kms:Decrypt", "kms:ListKeys"], "resources": ["arn:aws:kms:us-east-1:111111111111:key/*"],
         "optional": False}])
    assert res == {"denied": [], "optional": [], "allowed": 2}
    assert calls == [["kms:Decrypt", "kms:ListKeys"], ["kms:Decrypt"], ["kms:ListKeys"]]

    class Bad:
        def simulate_principal_policy(self, **kw):
            raise ClientError({"Error": {"Code": "InvalidInput", "Message": "Invalid Policy Source Arn"}},
                              "SimulatePrincipalPolicy")

    with pytest.raises(ClientError):
        permissions.simulate(Bad(), "arn:aws:iam::111111111111:role/r", [
            {"sid": None, "actions": ["kms:Decrypt", "kms:ListKeys"], "resources": ["*"], "optional": False}])
