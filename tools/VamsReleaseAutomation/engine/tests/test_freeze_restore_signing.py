# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Write freeze and VAMS workflow executions, restore through parallel temp restores, the change-set
gate, KMS-signed tags, and sts:SourceIdentity by default for gated envs."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError

from vra import backup, build as build_mod, cli, deploy, errors, freeze, permissions, restore, runner, sshsig, util
from vra.aws import AwsFactory
from vra.ledger import source_identity_on

from conftest import FakeAws, fake_shell_cls, git
from test_build_deploy import FakeCfn
from test_gates import approve, fake_build
from test_iam_and_ci_roles import P, load_cfn, render

moto = pytest.importorskip("moto")
PKG = Path(__file__).resolve().parents[2]


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with moto.mock_aws():
        yield


def set_env(ledger, env, **kv):
    d = util.read_yaml(ledger.root / f"envs/{env}/env.yaml")
    d.update(kv)
    util.write_yaml(ledger.root / f"envs/{env}/env.yaml", d)
    ledger._envs.clear()


def _stack_with_data(prefix="/vams-dev-us-east-1/resourceNames"):
    from test_backup_datacopy import _stack
    names = _stack(prefix)
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    ddb.put_item(TableName=names["dynamoTables/assetStorage"], Item={"id": {"S": "a1"}, "v": {"S": "orig"}})
    boto3.client("s3", region_name="us-east-1").put_object(Bucket="dev-assets", Key="a1/m.glb", Body=b"v1")
    return names, ddb


def _checkpoint(ctx, ledger, clients=None):
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn(), **(clients or {})}, moto=True)
    cid = runner.start(ctx, "backup", {"env": "dev"}, env="dev").run.state["checkpoint"]
    ctx.run_id = None
    return cid


# ============================================================ write freeze: manual backend
def test_manual_freeze_parks_until_a_human_confirms_and_records_who(ctx, ledger, aws):
    names, ddb = _stack_with_data()
    cid = _checkpoint(ctx, ledger)
    ddb.put_item(TableName=names["dynamoTables/assetStorage"], Item={"id": {"S": "a1"}, "v": {"S": "changed"}})
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid}, env="dev")
    assert out.exit_code == errors.NEEDS_HUMAN and "stop all writes to dev" in out.message
    assert "#1002" in out.message
    # nothing was cleared yet: the freeze comes before the live tables change
    assert ddb.get_item(TableName=names["dynamoTables/assetStorage"], Key={"id": {"S": "a1"}})["Item"]["v"]["S"] == \
        "changed"
    det = out.run.find_step("restore.write-freeze")
    assert det["status"] != "done"
    ctx.initiator = "ops-lead@example.com"
    again = runner.resume(ctx, out.run.run_id, confirm=True)
    assert again.exit_code == 0, again.message
    wf = again.run.state["writeFreeze"]["dev"]
    assert wf["engaged"]["backend"] == "manual" and wf["engaged"]["acknowledgedBy"] == "ops-lead@example.com"
    assert wf["engaged"]["source"] == "resume --confirm" and wf["released"]["released"] is True
    assert wf["engaged"]["detection"]["maintenanceMode"]["supported"] is False
    hist = ledger.history("dev")[-1]
    assert hist["action"] == "restore" and hist["writeFreeze"]["acknowledgedBy"] == "ops-lead@example.com"
    assert ddb.get_item(TableName=names["dynamoTables/assetStorage"], Key={"id": {"S": "a1"}})["Item"]["v"]["S"] == "orig"


def test_writes_stopped_flag_is_the_recorded_acknowledgement_and_is_human_only(ctx, ledger, aws, monkeypatch):
    _stack_with_data()
    cid = _checkpoint(ctx, ledger)
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True}, env="dev")
    assert out.exit_code == 0, out.message
    eng = out.run.state["writeFreeze"]["dev"]["engaged"]
    assert eng["source"] == "--writes-stopped" and eng["acknowledgedBy"] == ctx.initiator and eng["acknowledgedAt"]
    md = (out.run.dir / "report.md").read_text()
    assert "## Write freeze" in md and f"acknowledged by `{ctx.initiator}`" in md and "lifted" in md
    monkeypatch.setenv("VRA_AGENT", "1")
    ns = cli.build_parser().parse_args(["restore", "--env", "dev", "--checkpoint", cid, "--writes-stopped"])
    with pytest.raises(errors.NeedsHuman, match="HUMAN stopped writes"):
        cli.agent_guard(ns)


def test_manual_freeze_on_prod_still_needs_the_restore_approval(ctx, ledger, aws):
    from test_backup_datacopy import _stack
    _stack("/vams-prod-us-east-1/resourceNames", bucket="prod-assets")
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    set_env(ledger, "prod", backup={"allowDegraded": True})
    cid = runner.start(ctx, "backup", {"env": "prod"}, env="prod").run.state["checkpoint"]
    ctx.run_id = None
    out = runner.start(ctx, "restore", {"env": "prod", "checkpoint": cid, "writesStopped": True}, env="prod")
    assert out.exit_code == errors.NEEDS_HUMAN and f"restore:{cid}" in out.message
    ctx.run_id = None
    approve(ctx.ledger, "prod", f"restore:{cid}", "release-manager@example.com")
    ok = runner.start(ctx, "restore", {"env": "prod", "checkpoint": cid, "writesStopped": True,
                                       "approval": f"restore:{cid}"}, env="prod")
    assert ok.exit_code == 0, ok.message
    assert ok.run.state["writeFreeze"]["prod"]["engaged"]["backend"] == "manual"


# ============================================================ write freeze: maintenance-mode backend
class Watch:
    """Records the maintenance-mode switch value every time a live table is cleared."""

    def __init__(self, name):
        self.name, self.seen = name, []

    def value(self):
        return boto3.client("ssm", region_name="us-east-1").get_parameter(Name=self.name)["Parameter"]["Value"]


def test_maintenance_mode_is_detected_engaged_during_reload_and_lifted(ctx, ledger, aws, monkeypatch):
    names, _ = _stack_with_data()
    param = "/vams-dev-us-east-1/resourceNames/maintenanceMode/enabled"
    boto3.client("ssm", region_name="us-east-1").put_parameter(Name=param, Value="false", Type="String")
    cid = _checkpoint(ctx, ledger)
    w = Watch(param)
    orig = restore.clear_table

    def spy(ddb, table, page=500):
        w.seen.append(w.value())
        return orig(ddb, table, page)
    monkeypatch.setattr(restore, "clear_table", spy)
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid}, env="dev")
    assert out.exit_code == 0, out.message
    assert w.seen and set(w.seen) == {"true"}            # every live table was reloaded with writes frozen
    assert w.value() == "false"                           # lifted at the end
    eng = out.run.state["writeFreeze"]["dev"]["engaged"]
    assert eng["backend"] == "maintenance-mode" and eng["parameter"] == param
    assert ledger.history("dev")[-1]["writeFreeze"]["backend"] == "maintenance-mode"


def test_maintenance_mode_is_lifted_when_the_restore_fails(ctx, ledger, aws, monkeypatch):
    _stack_with_data()
    param = "/vams-dev-us-east-1/resourceNames/maintenanceMode"
    boto3.client("ssm", region_name="us-east-1").put_parameter(Name=param, Value="false", Type="String")
    cid = _checkpoint(ctx, ledger)

    def boom(*a, **k):
        raise RuntimeError("S3 went away")
    monkeypatch.setattr(restore, "restore_bucket", boom)
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid}, env="dev")
    assert out.exit_code != 0
    rel = out.run.state["writeFreeze"]["dev"]["released"]
    assert rel["released"] is True and rel["onFailure"] is True
    assert Watch(param).value() == "false"
    assert "dev" in (out.run.state.get("dirtyEnvs") or {})   # env stays locked: tables were reloaded


def test_maintenance_mode_requested_but_not_supported_is_refused(ctx, ledger, aws):
    _stack_with_data()
    cid = _checkpoint(ctx, ledger)
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writeFreeze": "maintenance-mode"}, env="dev")
    assert out.exit_code == errors.NEEDS_HUMAN and "publishes no maintenance-mode switch" in out.message


def test_freeze_backends_are_pluggable_and_no_waf_backend_exists():
    assert set(freeze.BACKENDS) == {"maintenance-mode", "manual"}
    assert all(issubclass(c, freeze.WriteFreeze) for c in freeze.BACKENDS.values())
    src = (PKG / "engine/vra/freeze.py").read_text().lower()
    assert "wafv2" not in src and "web acl" not in src
    for f in (PKG / "iam/actions").glob("*.yaml"):
        assert "wafv2:" not in f.read_text(), f


def test_unattended_rollback_restore_freezes_or_refuses(ctx, ledger, aws):
    _stack_with_data()
    cid = _checkpoint(ctx, ledger)
    from vra.state import Run
    run = Run.create(ctx.work_dir, "deploy", {}, ctx.initiator)
    res = restore.do_restore(ctx, run, "dev", cid, skip_gate=True, unattended=True)
    assert res["writeFreeze"]["backend"] == "none" and res["writeFreeze"]["unattended"] is True
    assert any("writes were not frozen" in n for n in run.state["notes"])
    from test_backup_datacopy import _stack
    _stack("/vams-test-us-east-1/resourceNames", bucket="test-assets")
    set_env(ledger, "test", backup={"allowDegraded": True})
    tcid = runner.start(ctx, "backup", {"env": "test"}, env="test").run.state["checkpoint"]
    run2 = Run.create(ctx.work_dir, "deploy", {}, ctx.initiator)
    with pytest.raises(errors.NeedsHuman, match="a human must run it"):
        restore.do_restore(ctx, run2, "test", tcid, skip_gate=True, unattended=True)


def test_data_copy_freezes_the_target(ctx, ledger, aws):
    from test_backup_datacopy import _stack, allow_degraded
    _stack("/vams-prod-us-east-1/resourceNames", bucket="prod-assets")
    _stack("/vams-test-us-east-1/resourceNames", bucket="test-assets")
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="vra-staging-example")
    approve(ledger, "test", "data-copy:prod", "qa-lead@example.com")
    approve(ledger, "prod", "data-copy-to:test", "release-manager@example.com")
    allow_degraded(ledger, "test")
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    args = {"from": "prod", "to": "test", "approval": "data-copy:prod", "method": "scan"}
    out = runner.start(ctx, "data-copy", args, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN and "stop all writes to test" in out.message
    assert not out.run.find_step("data-copy.table.dynamoTables/assetStorage")
    ctx.run_id = None
    ok = runner.start(ctx, "data-copy", {**args, "writesStopped": True}, env="test")
    assert ok.exit_code == 0, ok.message
    assert "test" in ok.run.state["writeFreeze"] and "prod" not in ok.run.state["writeFreeze"]
    assert ledger.history("test")[-1]["writeFreeze"]["backend"] == "manual"


# ============================================================ workflow executions: list always, abort on request
class FakeSfn:
    def __init__(self, status):
        self.status, self.stopped = status, []

    def describe_execution(self, executionArn):
        return {"status": self.status.get(executionArn, "SUCCEEDED")}

    def stop_execution(self, executionArn, error, cause):
        self.stopped.append((executionArn, error, cause))
        return {}


def _executions_table(prefix="/vams-dev-us-east-1/resourceNames"):
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    t = f"{prefix}-WorkflowExecutionsV2".replace("/", "")
    ddb.create_table(TableName=t, KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"}],
                     AttributeDefinitions=[{"AttributeName": "workflowExecutionId", "AttributeType": "S"}],
                     BillingMode="PAY_PER_REQUEST")
    boto3.client("ssm", region_name="us-east-1").put_parameter(
        Name=f"{prefix}/dynamoTables/workflowExecutionsStorageV2", Value=t, Type="String")
    arn = "arn:aws:states:us-east-1:111111111111:execution:wf:run1"
    for eid, st, a in (("e1", "RUNNING", arn), ("e2", "SUCCEEDED", arn + "x"), ("e3", "RUNNING",
                                                                               arn + "-done")):
        ddb.put_item(TableName=t, Item={"workflowExecutionId": {"S": eid}, "executionStatus": {"S": st},
                                        "workflow_execution_arn": {"S": a}, "workflowId": {"S": "wf"},
                                        "workflowDatabaseId": {"S": "db1"}})
    return arn


def test_active_workflow_executions_are_listed_and_only_aborted_on_request(ctx, ledger, aws):
    _stack_with_data()
    arn = _executions_table()
    sfn = FakeSfn({arn: "RUNNING", arn + "-done": "SUCCEEDED"})   # e3: table lags, Step Functions says done
    cid = _checkpoint(ctx, ledger, {"stepfunctions": sfn})
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True}, env="dev")
    assert out.exit_code == 0, out.message
    res = out.run.find_step("restore.workflow-executions")["result"]
    assert [a["executionId"] for a in res["active"]] == ["e1"] and not sfn.stopped
    assert any("still running on dev" in n and "--abort-workflow-executions" in n for n in out.run.state["notes"])
    ids = [s["id"] for s in out.run.state["steps"]]
    assert ids.index("restore.workflow-executions") < ids.index("restore.write-freeze")
    ctx.run_id = None
    out2 = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True,
                                         "abortWorkflowExecutions": True}, env="dev")
    assert out2.exit_code == 0, out2.message
    assert [s[0] for s in sfn.stopped] == [arn] and sfn.stopped[0][1] == "VraMaintenance"


def test_deploy_with_migrations_lists_workflow_executions():
    src = (PKG / "engine/vra/deploy.py").read_text()
    i = src.index('if mplan.get("steps"):')
    assert "workflow_executions_step" in src[i:i + 400] and src.index("workflow-executions") < src.index(
        'p + "cdk-deploy"')


# ============================================================ restore: parallel temp restores + verification
def test_restore_starts_all_temp_tables_first_and_verifies_counts(ctx, ledger, aws):
    names, ddb = _stack_with_data()
    cid = _checkpoint(ctx, ledger)
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True}, env="dev")
    assert out.exit_code == 0, out.message
    start = out.run.find_step("restore.start")["result"]
    assert len(start["started"]) == len(names)
    ids = [s["id"] for s in out.run.state["steps"]]
    assert ids.index("restore.start") < min(i for i, s in enumerate(ids) if s.startswith("restore.table."))
    ver = out.run.find_step("restore.verify")["result"]
    assert ver["mismatches"] == [] and ver["items"] >= 1
    vb = out.run.find_step("restore.verify-buckets")["result"]
    assert vb["buckets"]["dev-assets"] == {"recorded": 1, "current": 1}
    assert out.run.find_step("restore.settle")["result"]["derivedWrites"] == {}
    order = [s["id"] for s in out.run.state["steps"]]
    assert order.index("restore.verify") < order.index("restore.bucket.dev-assets") < \
        order.index("restore.verify-buckets") < order.index("restore.settle")


def test_restore_verification_catches_a_write_during_the_reload(ctx, ledger, aws, tmp_path):
    names, ddb = _stack_with_data()
    live = names["dynamoTables/assetStorage"]
    ddb.put_item(TableName=live, Item={"id": {"S": "late"}})
    ctx.aws = FakeAws(ledger, moto=True)
    with pytest.raises(errors.NeedsHuman, match="restore verification failed"):
        restore.verify_tables(ctx, "dev", {"tables": [{"table": live, "copied": 1}]})


def test_writes_vams_derives_from_the_object_rollback_are_reported_not_failed(ctx, ledger, aws, monkeypatch):
    """Drill finding B12: rolling objects back creates new S3 versions; VAMS's S3 event processing then adds
    version-history rows. The table check runs before the rollback (strict); rows added after it are reported."""
    names, ddb = _stack_with_data()
    cid = _checkpoint(ctx, ledger)
    boto3.client("s3", region_name="us-east-1").put_object(Bucket="dev-assets", Key="a1/m.glb", Body=b"v2")
    live = names["dynamoTables/assetStorage"]
    orig = restore.restore_bucket

    def with_event_processing(ctx_, env, cdir, b):
        res = orig(ctx_, env, cdir, b)
        if res["restored"]:
            ddb.put_item(TableName=live, Item={"id": {"S": "version-row-from-s3-event"}})
        return res
    monkeypatch.setattr(restore, "restore_bucket", with_event_processing)
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True}, env="dev")
    assert out.exit_code == 0, out.message
    der = out.run.find_step("restore.settle")["result"]["derivedWrites"]
    assert der == {live: {"copied": 1, "live": 2, "delta": 1}}
    assert any("VAMS's event processing changed 1 table" in n for n in out.run.state["notes"])
    assert ledger.history("dev")[-1]["derivedWrites"] == 1


# ============================================================ change-set gate
class CsCfn(FakeCfn):
    """Stacks in ``existing`` exist; change sets come from ``sets`` {(stack or arn): describe response}."""

    def __init__(self, existing, sets):
        super().__init__()
        self.existing, self.sets, self.deleted = set(existing), sets, []

    def describe_stacks(self, StackName):
        if StackName in self.existing:
            return {"Stacks": [{"StackName": StackName, "StackStatus": "UPDATE_COMPLETE"}]}
        return super().describe_stacks(StackName)

    def describe_change_set(self, ChangeSetName, StackName=None, NextToken=None):
        return self.sets[StackName or ChangeSetName]

    def delete_change_set(self, ChangeSetName, StackName):
        self.deleted.append(StackName)


def _cs_build(ctx, stacks):
    m = fake_build(ctx, "cust-v2.6.6-1")
    m["targets"]["dev"]["stacks"] = stacks
    bdir = build_mod.build_dir(ctx, "cust-v2.6.6-1")
    util.write_json(bdir / "manifest.json", m)
    (bdir / "manifest.sha256").write_text(util.sha256_file(bdir / "manifest.json") + "  manifest.json\n")


def _rc(rtype, action="Modify", lid="R", **kw):
    return {"Type": "Resource", "ResourceChange": {"ResourceType": rtype, "Action": action, "LogicalResourceId": lid,
                                                   **kw}}


def _cs_deploy(ctx, calls, ledger, sets, existing=("vams-a-dev",), stacks=("vams-a-dev", "vams-new-dev")):
    set_env(ledger, "dev", diffGate="changeset")
    _cs_build(ctx, list(stacks))
    cfn = CsCfn(existing, sets)
    ctx.aws = FakeAws(ledger, {"cloudformation": cfn})
    ctx.shell_cls = fake_shell_cls(calls)
    return runner.start(ctx, "deploy", {"env": "dev", "build": "cust-v2.6.6-1"}, env="dev"), cfn


def test_changeset_gate_blocks_a_nested_table_replacement_and_deletes_its_change_sets(ctx, calls, ledger):
    sets = {"vams-a-dev": {"Status": "CREATE_COMPLETE", "StackName": "vams-a-dev", "Changes": [
        _rc("AWS::CloudFormation::Stack", lid="Storage", ChangeSetId="arn:cs/nested")]},
            "arn:cs/nested": {"Status": "CREATE_COMPLETE", "StackName": "vams-a-dev-Storage", "Changes": [
                _rc("AWS::DynamoDB::Table", lid="AssetTable", Replacement="True"),
                _rc("AWS::Lambda::Function", lid="Fn", Replacement="True")]}}
    out, cfn = _cs_deploy(ctx, calls, ledger, sets)
    assert out.exit_code == errors.NEEDS_HUMAN and "change sets for dev replace" in out.message
    prep = [c["argv"] for c in calls if "--method=prepare-change-set" in c["argv"]]
    assert len(prep) == 1 and "vams-a-dev" in prep[0] and "vams-new-dev" not in prep[0]   # no CREATE change sets
    assert cfn.deleted == ["vams-a-dev"]
    assert not [c for c in calls if "deploy" in c["argv"] and "--method=prepare-change-set" not in c["argv"]]
    report = json.loads((out.run.dir / "change-sets-dev.json").read_text())
    assert report["replacements"][0]["resource"] == "AssetTable" and report["newStacks"] == ["vams-new-dev"]


def test_changeset_gate_passes_no_changes_and_flags_forward_only(ctx, calls, ledger):
    sets = {"vams-a-dev": {"Status": "FAILED", "StatusReason": "The submitted information didn't contain changes."}}
    out, _ = _cs_deploy(ctx, calls, ledger, sets)
    assert out.exit_code == 0, out.message
    assert out.run.find_step("deploy.dev.diff")["result"]["gate"] == "changeset"
    ctx.run_id = None
    calls.clear()
    sets = {"vams-a-dev": {"Status": "CREATE_COMPLETE", "Changes": [
        _rc("AWS::Cognito::UserPool", lid="Pool", Replacement="False",
            Details=[{"Target": {"Attribute": "Properties", "Name": "Schema"}}]),
        _rc("AWS::S3::Bucket", lid="B", Replacement="False", Scope=["DeletionPolicy"])]}}
    out, _ = _cs_deploy(ctx, calls, ledger, sets)
    assert out.exit_code == errors.NEEDS_HUMAN
    rep = json.loads((out.run.dir / "change-sets-dev.json").read_text())
    assert {(f["type"], f["property"]) for f in rep["forwardOnly"]} == {("AWS::Cognito::UserPool", "Schema"),
                                                                        ("AWS::S3::Bucket", "DeletionPolicy")}


def test_changeset_gate_fails_closed_on_unreadable_nested_change_sets(ctx, calls, ledger):
    sets = {"vams-a-dev": {"Status": "CREATE_COMPLETE", "Changes": [_rc("AWS::CloudFormation::Stack", lid="Nested")]}}
    out, _ = _cs_deploy(ctx, calls, ledger, sets)
    assert out.exit_code == errors.NEEDS_HUMAN and "could not be checked" in out.message


def test_text_gate_stays_the_default_and_changeset_needs_its_iam_statement(ctx, ledger):
    assert deploy.diff_gate(ctx, "dev") == "text"
    m = permissions.load_manifest("deploy")
    names = lambda feats: {s["sid"] for s in permissions.expand_manifest(m, {}, feats)}  # noqa: E731
    assert "ChangeSetGate" not in names(permissions.env_features(ctx, "dev"))
    set_env(ledger, "dev", diffGate="changeset")
    assert "ChangeSetGate" in names(permissions.env_features(ctx, "dev"))
    st = next(s for s in m["statements"] if s["sid"] == "ChangeSetGate")
    assert {"cloudformation:CreateChangeSet", "cloudformation:DescribeChangeSet",
            "cloudformation:DeleteChangeSet"} <= set(st["actions"])
    t = load_cfn("iam/cloudformation/vra-deploy-role.yaml")
    stmts = t["Resources"]["DeployPolicy"]["Properties"]["PolicyDocument"]["Statement"]
    gate = next(s for s in stmts if isinstance(s, dict) and "Fn::If" in s and s["Fn::If"][0] == "ChangeSetGateEnabled")
    assert "cloudformation:DeleteChangeSet" in gate["Fn::If"][1]["Action"]


# ============================================================ KMS-signed tags
class EcKms:
    """kms Sign/Verify/GetPublicKey backed by a real P-256 key (ECDSA_SHA_256, RAW and DIGEST)."""

    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        self.ec = ec
        self.key = ec.generate_private_key(ec.SECP256R1())

    def _alg(self, mt):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import utils
        return self.ec.ECDSA(utils.Prehashed(hashes.SHA256()) if mt == "DIGEST" else hashes.SHA256())

    def get_public_key(self, KeyId):
        from cryptography.hazmat.primitives import serialization
        return {"PublicKey": self.key.public_key().public_bytes(serialization.Encoding.DER,
                                                                serialization.PublicFormat.SubjectPublicKeyInfo)}

    def sign(self, KeyId, Message, MessageType, SigningAlgorithm):
        return {"Signature": self.key.sign(Message, self._alg(MessageType))}

    def verify(self, KeyId, Message, MessageType, Signature, SigningAlgorithm):
        try:
            self.key.public_key().verify(Signature, Message, self._alg(MessageType))
            return {"SignatureValid": True}
        except Exception:  # noqa: BLE001
            return {"SignatureValid": False}


pytest.importorskip("cryptography")


def _signed_build(ctx, ledger, kms):
    doc = util.read_yaml(ledger.root / "vra.yaml")
    doc["build"]["signing"] = {"kmsKeyArn": "arn:aws:kms:us-east-1:111111111111:key/abc", "tags": True}
    util.write_yaml(ledger.root / "vra.yaml", doc)
    ledger._project = None
    ctx.aws = FakeAws(ledger, {("tools", "kms"): kms})
    out = runner.start(ctx, "build", {"lane": "release", "ref": "main", "suites": []})
    assert out.exit_code == 0, out.message
    ctx.run_id = None
    return out.run.state["result"]


def test_release_tag_is_kms_signed_in_ssh_format_and_verified(ctx, ledger, repos, calls):
    kms = EcKms()
    m = _signed_build(ctx, ledger, kms)
    assert build_mod.verify_release_tag(ctx, m)["valid"] is True
    raw = git(ctx.repo_dir, "cat-file", "tag", m["tag"])
    assert sshsig.BEGIN in raw and f"manifest sha256" in raw
    if shutil.which("ssh-keygen"):   # interoperability: plain `git verify-tag` accepts the KMS signature
        pub = build_mod.tag_signer_path(ctx, m["buildId"]).read_text().split()
        allowed = ctx.work_dir / "allowed_signers"
        allowed.write_text(f'vra-release namespaces="git" {pub[0]} {pub[1]}\n')
        p = subprocess.run(["git", "-C", str(ctx.repo_dir), "-c", f"gpg.ssh.allowedSignersFile={allowed}",
                            "verify-tag", m["tag"]], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
    # the tag pushed to the fork is the signed one
    assert git(repos["fork"], "cat-file", "-t", m["tag"]) == "tag"


def test_unsigned_or_foreign_signed_tag_is_refused_by_deploy(ctx, ledger, repos, calls):
    kms = EcKms()
    m = _signed_build(ctx, ledger, kms)
    # replace the tag with an unsigned annotated one on the same commit
    git(ctx.repo_dir, "tag", "-f", "-a", m["tag"], m["commit"], "-m", f"vra build\nmanifest sha256 x")
    with pytest.raises(errors.VraError, match="not signed"):
        build_mod.verify_release_tag(ctx, m)
    out = runner.start(ctx, "deploy", {"env": "dev", "build": m["buildId"]}, env="dev")
    assert out.exit_code != 0 and "not signed" in out.message
    # a valid signature by another key is refused too
    other = EcKms()
    ctx.aws = FakeAws(ledger, {("tools", "kms"): other})
    git(ctx.repo_dir, "tag", "-d", m["tag"])
    g = build_mod.gitops.Git(ctx, ctx.repo_dir)
    msha = (build_mod.build_dir(ctx, m["buildId"]) / "manifest.sha256").read_text().split()[0]
    build_mod.create_signed_tag(ctx, g, m["tag"], m["commit"], f"vra build\nmanifest sha256 {msha}", m["buildId"])
    ctx.aws = FakeAws(ledger, {("tools", "kms"): kms})
    with pytest.raises(errors.VraError, match="another key"):
        build_mod.verify_release_tag(ctx, m)


def test_signing_stays_optional_and_validate_recommends_it_for_prod(ledger):
    assert not build_mod.tag_signing_enabled(type("C", (), {"ledger": ledger})())
    res = ledger.validate()
    assert any("prod-class env without build.signing" in w for w in res["warnings"])


def test_sshsig_round_trip_without_git():
    kms = EcKms()
    point = sshsig.kms_public_point(kms, "k")
    payload = b"object abc\ntype commit\ntag t\ntagger x <x@example.com> 0 +0000\n\nmsg\n"
    arm = sshsig.armor(point, sshsig.kms_sign(kms, "k", payload))
    assert sshsig.kms_verify(kms, "k", payload, arm)["valid"]
    assert not sshsig.kms_verify(kms, "k", payload + b"tampered", arm)["valid"]


# ============================================================ sts:SourceIdentity by default for gated envs
def test_gated_envs_set_source_identity_by_default(ledger):
    assert source_identity_on(ledger.env("test")) and source_identity_on(ledger.env("prod"))
    assert not source_identity_on(ledger.env("dev"))
    set_env(ledger, "dev", setSourceIdentity=True)
    assert source_identity_on(ledger.env("dev"))
    set_env(ledger, "prod", setSourceIdentity=False)
    assert not source_identity_on(ledger.env("prod"))
    assert any("setSourceIdentity=false on an approval-gated env" in w for w in ledger.validate()["warnings"])


def test_engine_passes_the_initiator_as_source_identity_for_test_but_not_dev(ledger):
    calls = []

    class Sts:
        def assume_role(self, **kw):
            calls.append(kw)
            return {"Credentials": {"AccessKeyId": "a", "SecretAccessKey": "b", "SessionToken": "c",
                                    "Expiration": dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)}}

    class Base:
        def client(self, service, region_name=None):
            return Sts()
    for env in ("test", "dev"):
        f = AwsFactory(ledger, "r1")
        f.base_session = lambda region=None: Base()
        f.source_identity_value = "alice@example.com"
        f.assume_fetcher(env)()
    assert calls[0]["SourceIdentity"] == "alice@example.com" and "SourceIdentity" not in calls[1]
    f = AwsFactory(ledger, "r1")
    f.source_identity_value = "alice@example.com"
    env_vars = f.credential_process_env("test")
    cfg = Path(env_vars["AWS_CONFIG_FILE"]).read_text()
    assert "--source-identity alice@example.com" in cfg


def _conditions(tpl: dict, params: dict) -> dict:
    """Evaluate a template's Conditions (Equals / Not / And / Or / Condition) for the given parameters."""
    conds = tpl["Conditions"]
    cache: dict = {}

    def val(n):
        if isinstance(n, dict) and "Ref" in n:
            return params.get(n["Ref"], (tpl["Parameters"].get(n["Ref"]) or {}).get("Default"))
        if isinstance(n, dict) and "Fn::Join" in n:  # !Join ["", !Ref SomeList] (CommaDelimitedList "is empty")
            sep, items = n["Fn::Join"]
            items = val(items)
            return sep.join(items if isinstance(items, list) else [str(items or "")])
        return n

    def ev(n):
        (k, v), = n.items()
        if k == "Fn::Equals":
            return str(val(v[0])) == str(val(v[1]))
        if k == "Fn::Not":
            return not ev(v[0])
        if k == "Fn::And":
            return all(ev(x) for x in v)
        if k == "Fn::Or":
            return any(ev(x) for x in v)
        if k in ("Condition", "Fn::Condition"):
            return get(v)
        raise AssertionError(k)

    def get(name):
        if name not in cache:
            cache[name] = ev(conds[name])
        return cache[name]
    return {n: get(n) for n in conds}


def test_test_and_prod_deploy_roles_require_source_identity_by_default():
    t = load_cfn("iam/cloudformation/vra-deploy-role.yaml")
    assert t["Parameters"]["RequireSourceIdentity"]["Default"] == "auto"
    assert t["Parameters"]["EnvTier"]["Default"] == "prod"
    cases = {("dev", "auto"): False, ("test", "auto"): True, ("prod", "auto"): True, ("dev", "true"): True,
             ("prod", "false"): False}
    for (tier, req), want in cases.items():
        assert _conditions(t, {"EnvTier": tier, "RequireSourceIdentity": req})["SourceIdentityRequired"] is want
    assert _conditions(t, {})["SourceIdentityRequired"] is True        # no parameters: the strict default
    params = {"AWS::Partition": P, "TrustedPrincipalArns": ["arn:aws:iam::444444444444:role/ci"], "ExternalId": ""}
    conds = _conditions(t, {"EnvTier": "test"})
    trust = render(t["Resources"]["DeployRole"]["Properties"]["AssumeRolePolicyDocument"], params, conds,
                   {})["Statement"]
    assert next(s for s in trust if s["Action"] == ["sts:AssumeRole"])["Condition"] == \
        {"Null": {"sts:SourceIdentity": "false"}}
    # role chaining into the CDK bootstrap roles keeps the source identity: needs SetSourceIdentity on both sides
    pol = render(t["Resources"]["DeployPolicy"]["Properties"]["PolicyDocument"], {**params, "AWS::AccountId": "1",
                 "ConfigName": "vams", "BaseStackName": "b", "AWS::Region": "us-east-1", "VamsRegion": ""}, conds,
                 {})["Statement"]
    cdk = next(s for s in pol if isinstance(s, dict) and s.get("Sid") == "CdkBootstrapRolesAssume")
    assert "sts:SetSourceIdentity" in cdk["Action"]
    for wf in ("deploy", "rollback"):
        st = next(s for s in permissions.load_manifest(wf)["statements"] if s["sid"] == "CdkBootstrapRolesAssume")
        assert "sts:SetSourceIdentity" in st["actions"]


def test_data_copy_roles_accept_the_source_identity_vra_sets():
    t = load_cfn("iam/cloudformation/vra-data-copy-roles.yaml")
    trust = t["Resources"]["CopyRole"]["Properties"]["AssumeRolePolicyDocument"]["Statement"]
    assert any(isinstance(s, dict) and s.get("Action") == "sts:SetSourceIdentity" for s in trust)


def _load_patcher():
    spec = importlib.util.spec_from_file_location("patch_bt", PKG / "iam/scripts/patch-bootstrap-template.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


STOCK_TRUST = {"Statement": [
    {"Action": "sts:AssumeRole", "Effect": "Allow", "Principal": {"AWS": {"Ref": "AWS::AccountId"}}},
    {"Action": "sts:TagSession", "Effect": "Allow", "Principal": {"AWS": {"Ref": "AWS::AccountId"}}},
    {"Fn::If": ["HasTrustedAccounts", {"Action": ["sts:TagSession"], "Effect": "Allow",
                                       "Principal": {"AWS": {"Ref": "TrustedAccounts"}}}, {"Ref": "AWS::NoValue"}]}]}


def test_bootstrap_patch_adds_set_source_identity_to_every_chained_role(tmp_path):
    import copy
    mod = _load_patcher()
    roles = {r: {"Type": "AWS::IAM::Role", "Properties": {"AssumeRolePolicyDocument": copy.deepcopy(STOCK_TRUST)}}
             for r in mod.CHAINED_ROLES}
    roles["CloudFormationExecutionRole"] = {"Type": "AWS::IAM::Role", "Properties": {
        "AssumeRolePolicyDocument": {"Statement": [{"Action": "sts:AssumeRole", "Effect": "Allow",
                                                    "Principal": {"Service": "cloudformation.amazonaws.com"}}]}}}
    tpl = {"Description": "CDK", "Resources": roles}
    rep = mod.patch(tpl)
    assert rep == {r: 2 for r in mod.CHAINED_ROLES}
    for r in mod.CHAINED_ROLES:
        doc = tpl["Resources"][r]["Properties"]["AssumeRolePolicyDocument"]
        assert deploy._trust_allows_set_source_identity(doc)
        assert any("Fn::If" in s and s["Fn::If"][1]["Action"] == "sts:SetSourceIdentity" for s in doc["Statement"])
    assert not deploy._trust_allows_set_source_identity(
        tpl["Resources"]["CloudFormationExecutionRole"]["Properties"]["AssumeRolePolicyDocument"])
    assert mod.patch(tpl) == {r: 0 for r in mod.CHAINED_ROLES}   # idempotent


class TrustIam:
    def __init__(self, doc):
        self.doc = doc

    def get_role(self, RoleName):
        return {"Role": {"AssumeRolePolicyDocument": self.doc}}


def test_deploy_stops_before_changes_when_bootstrap_roles_reject_source_identity(ctx, ledger):
    ctx.aws = FakeAws(ledger, {"iam": TrustIam(STOCK_TRUST)})
    ctx.aws.source_identity_value = "alice@example.com"
    with pytest.raises(errors.NeedsHuman, match="do not trust sts:SetSourceIdentity"):
        deploy.bootstrap_trust_check(ctx, "test")
    assert deploy.bootstrap_trust_check(ctx, "dev")["skipped"]
    import copy
    patched = copy.deepcopy(STOCK_TRUST)
    patched["Statement"].append({"Action": "sts:SetSourceIdentity", "Effect": "Allow", "Principal": {"AWS": "x"}})
    ctx.aws = FakeAws(ledger, {"iam": TrustIam(patched)})
    ctx.aws.source_identity_value = "alice@example.com"
    assert len(deploy.bootstrap_trust_check(ctx, "test")["checked"]) == 4


def test_an_offered_approval_is_verified_even_on_an_ungated_env(ctx, ledger, aws):
    _stack_with_data()
    cid = _checkpoint(ctx, ledger)
    set_env(ledger, "dev", approvers=["env-owner@example.com"])
    approve(ledger, "dev", f"restore:{cid}", "env-owner@example.com")
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True,
                                        "approval": f"restore:{cid}"}, env="dev")
    assert out.exit_code == 0, out.message
    assert out.run.state["approvals"][0]["approver"] == "env-owner@example.com"
    assert ledger.history("dev")[-1]["approver"] == "env-owner@example.com"
    ctx.run_id = None
    approve(ledger, "dev", f"restore:{cid}", ctx.initiator)        # self-approval is refused, gated or not
    bad = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True,
                                        "approval": f"restore:{cid}"}, env="dev")
    assert bad.exit_code == errors.NEEDS_HUMAN


def test_temp_table_cleanup_is_idempotent():
    """Drill finding B13: a retried DeleteTable met 'Table is being deleted' and failed the whole restore."""
    calls = []

    class Ddb:
        def __init__(self, errors):
            self.errors = list(errors)

        def delete_table(self, TableName):
            calls.append(TableName)
            if self.errors:
                raise self.errors.pop(0)
    busy = ClientError({"Error": {"Code": "ResourceInUseException",
                                  "Message": "Attempt to change a resource which is still in use: Table is being "
                                             "deleted: vra-restore-x"}}, "DeleteTable")
    gone = ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "Requested resource not found"}},
                       "DeleteTable")
    limit = ClientError({"Error": {"Code": "LimitExceededException", "Message": "too many"}}, "DeleteTable")
    restore.delete_temp_table(Ddb([busy]), "dev", "t1")
    restore.delete_temp_table(Ddb([gone]), "dev", "t2")
    restore.delete_temp_table(Ddb([limit]), "dev", "t3")
    assert calls == ["t1", "t2", "t3", "t3"]
    other = ClientError({"Error": {"Code": "ValidationException", "Message": "bad"}}, "DeleteTable")
    with pytest.raises(ClientError):
        restore.delete_temp_table(Ddb([other]), "dev", "t4")


def test_resume_after_the_frozen_part_does_not_freeze_or_ask_again(ctx, ledger, aws, monkeypatch):
    _stack_with_data()
    cid = _checkpoint(ctx, ledger)
    real = restore.delete_temp_table

    def flaky(ddb, env, name, max_wait=900):
        raise RuntimeError("cleanup hiccup")
    monkeypatch.setattr(restore, "delete_temp_table", flaky)
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True}, env="dev")
    assert out.exit_code != 0 and out.run.find_step("restore.cleanup")["status"] == "failed"
    assert out.run.state["writeFreeze"]["dev"]["released"]["released"] is True
    monkeypatch.setattr(restore, "delete_temp_table", real)
    again = runner.resume(ctx, out.run.run_id)          # no --confirm needed: nothing is reloaded again
    assert again.exit_code == 0, again.message
    wf = again.run.state["writeFreeze"]["dev"]
    assert wf["engaged"]["source"] == "--writes-stopped" and wf["released"]["released"] is True
    assert ledger.history("dev")[-1]["writeFreeze"]["source"] == "--writes-stopped"


def test_freeze_acknowledgement_names_the_verified_initiator(ctx, ledger, aws):
    _stack_with_data()
    cid = _checkpoint(ctx, ledger)
    from vra.state import Run
    run = Run.create(ctx.work_dir, "restore", {}, "asserted-alias")
    run.state["writeFreezeAck"] = {"by": "asserted-alias", "at": util.now_iso(), "source": "--writes-stopped"}
    ctx.initiator = "arn:aws:sts::111111111111:assumed-role/Ops/alice"
    res = restore.do_restore(ctx, run, "dev", cid)
    eng = run.state["writeFreeze"]["dev"]["engaged"]
    assert eng["acknowledgedBy"] == ctx.initiator and eng["assertedBy"] == "asserted-alias"
    assert res["writeFreeze"]["acknowledgedBy"] == ctx.initiator


def test_temp_table_names_are_unique_per_table_key(ctx):
    """Drill finding B14: dynamoTables/legacy/tagStorage and dynamoTables/tagStorage shared a temporary table."""
    a = restore.temp_table_name(ctx, "dev", "dynamoTables/legacy/tagStorage", "t1")
    b = restore.temp_table_name(ctx, "dev", "dynamoTables/tagStorage", "t1")
    assert a != b and a == "vra-restore-dev-us-east-1-legacy-tagStorage-t1"
    long = restore.temp_table_name(ctx, "dev", "dynamoTables/" + "x" * 300, "t1")
    assert len(long) <= 255 and long.startswith("vra-restore-dev-us-east-1-") and long.endswith("-t1")
    with pytest.raises(errors.NeedsHuman, match="not from the checkpoint"):
        restore.check_temp_source({"TableName": a, "RestoreSummary": {"SourceBackupArn": "arn:other"}},
                                  {"name": "live", "backupArn": "arn:mine"})
    restore.check_temp_source({"TableName": a, "RestoreSummary": {"SourceBackupArn": "arn:mine"}},
                              {"name": "live", "backupArn": "arn:mine"})


def test_restore_of_tables_whose_keys_share_a_last_segment_keeps_their_data_apart(ctx, ledger, aws):
    names, ddb = _stack_with_data()
    ssm = boto3.client("ssm", region_name="us-east-1")
    for key, item in (("dynamoTables/legacy/tagStorage", "legacy-tag"), ("dynamoTables/tagStorage", "v2-tag")):
        t = "/vams-dev-us-east-1/resourceNames".replace("/", "") + key.replace("/", "-")
        ddb.create_table(TableName=t, KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
                         BillingMode="PAY_PER_REQUEST")
        ssm.put_parameter(Name=f"/vams-dev-us-east-1/resourceNames/{key}", Value=t, Type="String")
        ddb.put_item(TableName=t, Item={"id": {"S": item}})
        names[key] = t
    cid = _checkpoint(ctx, ledger)
    for key in ("dynamoTables/legacy/tagStorage", "dynamoTables/tagStorage"):
        ddb.put_item(TableName=names[key], Item={"id": {"S": "post-checkpoint"}})
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True}, env="dev")
    assert out.exit_code == 0, out.message
    assert [i["id"]["S"] for i in ddb.scan(TableName=names["dynamoTables/legacy/tagStorage"])["Items"]] == ["legacy-tag"]
    assert [i["id"]["S"] for i in ddb.scan(TableName=names["dynamoTables/tagStorage"])["Items"]] == ["v2-tag"]
    assert out.run.find_step("restore.start")["result"]["alreadyPresent"] == []


def test_a_second_restore_does_not_copy_objects_that_already_match(ctx, ledger, aws):
    _stack_with_data()
    s3 = boto3.client("s3", region_name="us-east-1")
    cid = _checkpoint(ctx, ledger)
    s3.put_object(Bucket="dev-assets", Key="a1/m.glb", Body=b"v2")
    first = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True}, env="dev")
    assert first.exit_code == 0 and first.run.find_step("restore.bucket.dev-assets")["result"]["restored"] == 1
    ctx.run_id = None
    second = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True}, env="dev")
    assert second.exit_code == 0, second.message
    res = second.run.find_step("restore.bucket.dev-assets")["result"]
    assert res["restored"] == 0 and res["unchanged"] == 1
    assert s3.get_object(Bucket="dev-assets", Key="a1/m.glb")["Body"].read() == b"v1"


def test_restored_copies_are_recognised_even_when_etags_differ(ctx, ledger, aws):
    """With SSE-KMS (the VAMS CMK) every copy has a new ETag, so the restore remembers its own copies' version ids
    (the live drill re-copied all 91 objects on a second run before this)."""
    _stack_with_data()
    s3 = boto3.client("s3", region_name="us-east-1")
    cid = _checkpoint(ctx, ledger)
    cp_dir = backup.checkpoint_dir(ctx, cid)
    cp = backup.load_checkpoint(ctx, cid)
    b = next(x for x in cp["buckets"] if x["name"] == "dev-assets")
    lines = [json.loads(x) for x in (cp_dir / b["manifest"]).read_text().splitlines() if x.strip()]
    (cp_dir / b["manifest"]).write_text("".join(json.dumps({**x, "etag": "kms-etag-" + x["key"]}) + "\n"
                                                for x in lines))   # ETags that never match a copy
    s3.put_object(Bucket="dev-assets", Key="a1/m.glb", Body=b"v2")
    assert restore.restore_bucket(ctx, "dev", cp_dir, b)["restored"] == 1
    assert restore.restored_map_path(cp_dir, b).exists()
    again = restore.restore_bucket(ctx, "dev", cp_dir, b)
    assert again["restored"] == 0 and again["unchanged"] == 1


def test_an_approval_dated_in_the_future_is_refused(ctx, ledger):
    from vra import approvals
    fake_build(ctx, "cust-v2.6.6-1")
    later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    approve(ledger, "test", "cust-v2.6.6-1", "qa-lead@example.com", approvedAt=later)
    with pytest.raises(errors.NeedsHuman, match="in the future"):
        approvals.check(ctx, "test", "cust-v2.6.6-1", "deploy", None)


# ============================================================ restore keeps revocations
def test_restore_leaves_access_tables_as_they_are_unless_asked(ctx, ledger, aws):
    """An API key revoked after the checkpoint must stay revoked: the access tables are not restored by default."""
    names, ddb = _stack_with_data()
    keys = "vams-dev-ApiKeys"
    ddb.create_table(TableName=keys, KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                     AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}], BillingMode="PAY_PER_REQUEST")
    boto3.client("ssm", region_name="us-east-1").put_parameter(
        Name="/vams-dev-us-east-1/resourceNames/dynamoTables/apiKeyStorage", Value=keys, Type="String")
    ddb.put_item(TableName=keys, Item={"id": {"S": "key-1"}})
    cid = _checkpoint(ctx, ledger)
    ddb.delete_item(TableName=keys, Key={"id": {"S": "key-1"}})          # revoked after the checkpoint
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True}, env="dev")
    assert out.exit_code == 0, out.message
    assert "Item" not in ddb.get_item(TableName=keys, Key={"id": {"S": "key-1"}})
    assert any("dynamoTables/apiKeyStorage" in n and "stay revoked" in n for n in out.run.state["notes"])
    assert not any(s["id"].endswith("apiKeyStorage") for s in out.run.state["steps"] if s["id"].startswith(
        "restore.table."))
    ctx.run_id = None
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True,
                                        "includeAuthTables": True}, env="dev")
    assert out.exit_code == 0, out.message
    assert "Item" in ddb.get_item(TableName=keys, Key={"id": {"S": "key-1"}})


def test_secret_scan_covers_the_whole_ledger(ledger):
    from vra.ledger import Ledger
    (ledger.root / "tests").mkdir(exist_ok=True)
    (ledger.root / "tests/customer.yaml").write_text("- name: smoke\n  cmd: run --token AKIAABCDEFGHIJKLMNOP\n")
    errs = Ledger(ledger.root).validate()["errors"]
    assert any("tests/customer.yaml" in e and "access key" in e for e in errs)
