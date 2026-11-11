# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Release gates and safety rules of the engine: shared history, promotion evidence, approval binding
and scope, the fail-closed diff, executed-suite evidence, build-only synth credentials, signed manifests,
resume gates, locks, checkpoints, agent mode, drift, the pinned CDK CLI, data-copy identities, restore,
cron semantics, redaction and source identity.

IAM template and CI lane tests are in test_iam_and_ci_roles.py.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import boto3
import pytest

from vra import (approvals, backup, build as build_mod, cli, cron, deploy, errors, identity, permissions, restore,
                 runner, tests_runner, util)
from vra.aws import AwsFactory
from vra.context import Context
from vra.errors import NeedsHuman
from vra.ledger import Ledger
from vra.state import LocalLock, Run

from conftest import FakeAws, fake_shell_cls, git
from test_build_deploy import FakeCfn
from test_gates import approve, fake_build, passed_test

moto = pytest.importorskip("moto")


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


def dev_evidence(ledger, build):
    ledger.append_history("dev", {"action": "deploy", "build": build, "result": "SUCCEEDED", "vamsVersion": "2.6.6"})
    passed_test(ledger, "dev", build)


# ============================================================ history shared through the state store
def test_history_in_state_store_lets_separate_ci_jobs_promote_and_roll_back(ctx, ledger, tmp_path, aws):
    """Each CI job gets a fresh ledger checkout and work dir. The dev deploy, the dev test and the promotion
    run in three such jobs; the promotion must still see the evidence, and the rollback target must exist."""
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="vra-state-test")
    doc = util.read_yaml(ledger.root / "vra.yaml")
    doc["stateStore"] = {"type": "s3", "bucket": "vra-state-test", "lockTable": "vra-locks", "region": "us-east-1"}
    util.write_yaml(ledger.root / "vra.yaml", doc)
    jobs = []
    for name in ("deploy-dev", "test-dev", "promote-test"):
        root = tmp_path / f"checkout-{name}"
        shutil.copytree(ledger.root, root)            # a fresh checkout: no history.jsonl of other jobs
        c = Context(ledger=Ledger(root), work_dir=tmp_path / f"work-{name}", initiator="ci@example.com")
        c.aws = FakeAws(c.ledger, moto=True)
        jobs.append(c)
    a, b, c = jobs
    a.ledger.append_history("dev", {"action": "deploy", "build": "cust-v2.6.6-1", "result": "SUCCEEDED",
                                    "vamsVersion": "2.6.6", "runId": "r-a"})
    passed_test(b.ledger, "dev", "cust-v2.6.6-1")
    assert not (c.ledger.root / "envs/dev/history.jsonl").exists()
    fake_build(c, "cust-v2.6.6-1")
    c.dry_run = True                                   # skips the fork trunk fetch
    assert deploy.check_promotion(c, "dev", "test", "cust-v2.6.6-1")["to"] == "test"
    assert c.ledger.last_good_deploy("dev")["build"] == "cust-v2.6.6-1"
    keys = boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket="vra-state-test",
                                                                       Prefix="vra/history/dev/")["Contents"]
    assert len(keys) == 2
    # records are numbered per env in write order, whichever job wrote them
    a.ledger.append_history("dev", {"action": "backup", "runId": "r-x", "result": "SUCCEEDED"})
    assert [h["seq"] for h in a.ledger.history("dev")] == [1, 2, 3]
    assert not (a.ledger.root / "envs/dev/history.jsonl").exists()


# ============================================================ deploy enforces promotion evidence
def test_deploy_to_gated_env_needs_the_same_evidence_as_promote(ctx, ledger):
    fake_build(ctx, "cust-v2.6.6-1")
    approve(ledger, "test", "cust-v2.6.6-1", "qa-lead@example.com")
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()})
    out = runner.start(ctx, "deploy", {"env": "test", "build": "cust-v2.6.6-1"}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN and "no successful deploy on dev" in out.message
    dev_evidence(ledger, "cust-v2.6.6-1")
    ctx.run_id, ctx.dry_run = None, True
    approve(ledger, "test", "cust-v2.6.6-1", "qa-lead@example.com")
    out = runner.start(ctx, "deploy", {"env": "test", "build": "cust-v2.6.6-1"}, env="test")
    assert out.exit_code == 0, out.message
    assert out.run.find_step("deploy.test.promotion-check")["status"] == "done"


def test_deploy_to_gated_env_refuses_self_approval(ctx, ledger):
    fake_build(ctx, "cust-v2.6.6-1")
    set_env(ledger, "test", approvers=["qa-lead@example.com", "dev-operator@example.com"])
    approve(ledger, "test", "cust-v2.6.6-1", "dev-operator@example.com")
    out = runner.start(ctx, "deploy", {"env": "test", "build": "cust-v2.6.6-1"}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN and "separation of duties" in out.message


# ============================================================ identity and approval binding
def _env(monkeypatch, **kv):
    for k, v in kv.items():
        monkeypatch.setenv(k, v)


def test_identity_from_github_ignores_self_asserted_initiator(monkeypatch):
    _env(monkeypatch, GITHUB_ACTIONS="true", GITHUB_ACTOR="bot", GITHUB_TRIGGERING_ACTOR="alice",
         GITHUB_REPOSITORY="example-org/vams-ledger", GITHUB_RUN_ID="42", VRA_INITIATOR="mallory")
    i = identity.resolve()
    assert (i.initiator, i.source, i.verified) == ("alice", "github", True)
    assert i.run_ref == "github:example-org/vams-ledger/actions/runs/42"
    assert "bot" in i.aliases and "mallory" not in i.all_names() and "ignored" in i.notes[0]


def test_identity_from_gitlab_and_codebuild_trigger(monkeypatch, ledger, tmp_path):
    _env(monkeypatch, GITLAB_CI="true", GITLAB_USER_LOGIN="bob", CI_PROJECT_PATH="grp/ledger", CI_PIPELINE_ID="7")
    i = identity.resolve()
    assert (i.initiator, i.run_ref) == ("bob", "gitlab:grp/ledger/pipelines/7")
    monkeypatch.delenv("GITLAB_CI")
    _env(monkeypatch, CODEBUILD_BUILD_ID="vra-deploy-test:abc", CODEBUILD_INITIATOR="codepipeline/vra-release",
         VRA_PIPELINE_NAME="vra-release", VRA_PIPELINE_EXECUTION_ID="exec-1")

    class Cp:
        def get_pipeline_execution(self, pipelineName, pipelineExecutionId):
            assert (pipelineName, pipelineExecutionId) == ("vra-release", "exec-1")
            return {"pipelineExecution": {"trigger": {
                "triggerType": "StartPipelineExecution",
                "triggerDetail": "arn:aws:sts::111111111111:assumed-role/Operator/carol@example.com"}}}
    c = Context(ledger=ledger, work_dir=tmp_path / "w")
    c.aws = FakeAws(ledger, {("tools", "codepipeline"): Cp()})
    assert c.identity.run_ref == "codepipeline:vra-release/exec-1"
    ident = identity.verify(c)
    assert ident.initiator.endswith("Operator/carol@example.com") and "codepipeline/vra-release" in ident.aliases
    assert c.initiator == ident.initiator


def test_local_asserted_initiator_is_checked_against_sts(monkeypatch, ledger, tmp_path):
    monkeypatch.setenv("VRA_INITIATOR", "qa-lead@example.com")  # claims to be someone else

    class Sts:
        def get_caller_identity(self):
            return {"Arn": "arn:aws:sts::111111111111:assumed-role/Ops/dev-operator@example.com"}
    c = Context(ledger=ledger, work_dir=tmp_path / "w")
    c.aws = FakeAws(ledger, {("tools", "sts"): Sts()})
    assert not c.identity.verified
    approve(ledger, "test", "b1", "qa-lead@example.com")
    with pytest.raises(NeedsHuman, match="separation of duties"):   # SoD checks the asserted name too
        approvals.check(c, "test", "b1", "deploy")
    assert c.initiator.endswith("Ops/dev-operator@example.com")


def test_arn_approver_needs_arn_entry_not_a_bare_session_name():
    arn = "arn:aws:sts::111111111111:assumed-role/AnyRole/qa-lead@example.com"
    assert not approvals.approver_listed(arn, ["qa-lead@example.com"])
    assert approvals.approver_listed(arn, ["arn:aws:sts::111111111111:assumed-role/AnyRole/qa-lead@example.com"])
    assert approvals.approver_listed(arn, ["arn:*:sts::111111111111:assumed-role/Any*/qa-lead@example.com"])
    assert not approvals.approver_listed(arn, ["arn:*:sts::222222222222:assumed-role/Any*/qa-lead@example.com"])
    assert approvals.approver_listed("qa-lead", ["QA-Lead"]) and not approvals.approver_listed("qa", ["qa-lead"])


def test_ci_approval_must_be_bound_to_this_run(ctx, ledger):
    """In a QUEUED pipeline, an approval of execution A must not authorize execution B."""
    ctx.identity = identity.Identity("codepipeline/vra-release", "codebuild", True,
                                     run_ref="codepipeline:vra-release/exec-B")
    p = approve(ledger, "test", "b1", "qa-lead@example.com", source="codepipeline:vra-release/exec-A")
    with pytest.raises(NeedsHuman, match="bound to"):
        approvals.check(ctx, "test", "b1", "promote", str(p))
    p = approve(ledger, "test", "b1", "qa-lead@example.com", source="codepipeline:vra-release/exec-B")
    assert approvals.check(ctx, "test", "b1", "promote", str(p))["source"].endswith("exec-B")
    # in CI only the record passed with --approval counts, never a file found in the ledger
    with pytest.raises(NeedsHuman, match="pass the record the CI approval step wrote"):
        approvals.check(ctx, "test", "b1", "promote")


# ============================================================ approval scope, expiry, single use
def test_approval_scope_required_default_expiry_and_single_use(ctx, ledger):
    p = approve(ledger, "test", "b1", "qa-lead@example.com", scope=None)
    rec = util.read_yaml(p)
    rec.pop("scope")
    util.write_yaml(p, rec)
    with pytest.raises(NeedsHuman, match="is invalid|no scope"):
        approvals.check(ctx, "test", "b1", "deploy")
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=73)).isoformat()
    approve(ledger, "test", "b1", "qa-lead@example.com", approvedAt=old)
    with pytest.raises(NeedsHuman, match="expired"):
        approvals.check(ctx, "test", "b1", "deploy")
    p = approve(ledger, "test", "b1", "qa-lead@example.com")
    sha = util.sha256_file(p)
    ctx.run_id = "run-now"
    assert approvals.check(ctx, "test", "b1", "deploy")
    ledger.append_history("test", {"action": "deploy", "build": "b1", "result": "SUCCEEDED", "runId": "run-earlier",
                                   "approvalSha256": sha})
    with pytest.raises(NeedsHuman, match="already used by run run-earlier"):
        approvals.check(ctx, "test", "b1", "rollback")


# ============================================================ cdk diff fails closed
def _deploy_with_diff(ctx, calls, text, code=0):
    def handler(argv, cwd, env):
        if "diff" in argv:
            return subprocess.CompletedProcess(argv, code, text, "")
        return None
    ctx.shell_cls = fake_shell_cls(calls, handler)
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn()})
    fake_build(ctx, "cust-v2.6.6-1")
    return runner.start(ctx, "deploy", {"env": "dev", "build": "cust-v2.6.6-1"}, env="dev")


def test_diff_error_fails_closed(ctx, calls):
    out = _deploy_with_diff(ctx, calls, "Error: assembly is corrupt\n", code=2)
    assert out.exit_code == errors.NEEDS_HUMAN and "does not deploy without a diff" in out.message


def test_unparseable_diff_fails_closed(ctx, calls):
    out = _deploy_with_diff(ctx, calls, "Stack vams-core-dev\nsomething new happened\n"
                                        "Number of stacks with differences: 3\n")
    assert out.exit_code == errors.NEEDS_HUMAN and "could not parse" in out.message


def test_cognito_schema_change_and_deletion_policy_are_forward_only():
    text = ("Resources\n[~] AWS::Cognito::UserPool UserPool UserPool1\n \u2514\u2500 [~] Schema\n"
            "[~] AWS::DynamoDB::Table T T1\n \u251c\u2500 [~] DeletionPolicy\n"
            "[~] AWS::Lambda::Function F F1\n \u2514\u2500 [~] Code\n")
    p = deploy.parse_diff(text)
    assert {(d["type"], d["property"]) for d in p["forwardOnly"]} == {("AWS::Cognito::UserPool", "Schema"),
                                                                      ("AWS::DynamoDB::Table", "DeletionPolicy")}
    assert p["understood"] and p["changes"] == 3


# ============================================================ nothing executed is not a pass
def test_all_skipped_junit_is_not_ok(tmp_path):
    tests_runner.write_junit(tmp_path / "j" / "a.xml", "a", [{"name": "x", "status": "skipped"}])
    s = tests_runner.aggregate_junit(tmp_path / "j")
    assert s["ok"] is False and s["executed"] == 0 and "skipped" in s["reason"]
    missing = tests_runner.aggregate_junit(tmp_path / "nope")             # no KeyError, a clear reason
    assert missing["ok"] is False and missing["reason"] == "no test results"


def test_vra_test_with_nothing_configured_records_no_tests(ctx, ledger):
    set_env(ledger, "dev", liveTest={})
    ledger.append_history("dev", {"action": "deploy", "build": "b1", "result": "SUCCEEDED"})
    out = runner.start(ctx, "test", {"env": "dev", "suites": ["live-cli"]}, env="dev")
    assert out.exit_code == errors.TESTS_FAILED and "NO_TESTS" in out.message
    assert ledger.history("dev")[-1]["result"] == "NO_TESTS"


def test_smoke_of_env_feeding_a_gated_env_must_execute(ctx, ledger):
    set_env(ledger, "dev", liveTest={})                                    # live-cli is required on dev
    jdir = Path(ctx.work_dir) / "j"
    run = Run.create(ctx.work_dir, "x", {}, "me")
    with pytest.raises(errors.TestsFailed, match="executed nothing"):
        run.step("s", lambda sc: deploy._smoke(ctx, run, "dev", sc, None))
    assert not jdir.exists()
    set_env(ledger, "dev-preview", liveTest={})                            # nothing promotes from dev-preview
    run.step("p", lambda sc: deploy._smoke(ctx, run, "dev-preview", sc, None))


def test_unit_suites_that_execute_nothing_fail(ctx, repos, calls, monkeypatch):
    monkeypatch.setattr(tests_runner.shutil, "which", lambda name: None)
    out = runner.start(ctx, "build", {"lane": "release", "ref": "main", "suites": ["ash"]})
    assert out.exit_code == errors.TESTS_FAILED and "executed nothing" in out.message


def _enable(ledger, **build):
    doc = util.read_yaml(ledger.root / "vra.yaml")
    doc["build"].update(build)
    util.write_yaml(ledger.root / "vra.yaml", doc)
    ledger._project = None


def test_missing_scanner_fails_closed_and_waiver_is_refused_for_prod(ctx, ledger, repos, calls, monkeypatch):
    _enable(ledger, ash=True)
    real = shutil.which
    monkeypatch.setattr(build_mod.shutil, "which", lambda n: None if n == "ash" else real(n))
    out = runner.start(ctx, "build", {"lane": "release", "ref": "main", "suites": []})
    assert out.exit_code == errors.NEEDS_HUMAN and "--allow-missing-scanners" in out.message
    ctx.run_id = None
    out = runner.start(ctx, "build", {"lane": "release", "ref": "main", "suites": [], "allowMissingScanners": True})
    assert out.exit_code == 0, out.message
    m = out.run.state["result"]
    assert m["scanners"]["waived"] == ["ash"]
    ctx.run_id = None
    approve(ledger, "prod", m["buildId"], "release-manager@example.com")
    gate = runner.start(ctx, "deploy", {"env": "prod", "build": m["buildId"]}, env="prod")
    assert gate.exit_code == errors.NEEDS_HUMAN and "--allow-missing-scanners" in gate.message


def test_missing_sbom_tools_fail_closed(ctx, ledger, repos, calls, monkeypatch):
    _enable(ledger, sbom=True)
    real = shutil.which
    monkeypatch.setattr(build_mod.shutil, "which", lambda n: None if n.startswith("cyclonedx") else real(n))
    out = runner.start(ctx, "build", {"lane": "release", "ref": "main", "suites": []})
    assert out.exit_code == errors.NEEDS_HUMAN and "cyclonedx-npm" in out.message


# ============================================================ build-only synth credentials
def test_gated_env_never_lends_its_deploy_role_to_synth(ctx, ledger, repos, calls):
    env, role = build_mod.synth_credentials(ctx, "test")
    assert role == "none" and env["AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"] == "" and \
        env["AWS_EC2_METADATA_DISABLED"] == "true"
    assert build_mod.synth_credentials(ctx, "dev")[1] == "deploy"          # ungated: the deploy role
    set_env(ledger, "prod", synthRoleArn="arn:aws:iam::333333333333:role/vra-synth-prod")
    assert build_mod.synth_credentials(ctx, "prod")[1] == "synth"
    assert AwsFactory.role_arn(FakeAws(ledger), "test", "synth") is None   # never falls back to deployRoleArn
    out = runner.start(ctx, "build", {"lane": "release", "ref": "main", "suites": []})
    assert out.exit_code == 0, out.message
    creds = {c["argv"][c["argv"].index("-o") + 1].split("/")[-1]: c["env"] for c in calls if "synth" in c["argv"]}
    assert creds["test"]["AWS_EC2_METADATA_DISABLED"] == "true" and "AWS_EC2_METADATA_DISABLED" not in creds["dev"]


# ============================================================ signed manifests
class FakeKms:
    def __init__(self):
        self.signed = {}

    def sign(self, KeyId, Message, MessageType, SigningAlgorithm):
        sig = b"sig:" + Message
        self.signed[sig] = Message
        return {"Signature": sig}

    def verify(self, KeyId, Message, MessageType, Signature, SigningAlgorithm):
        return {"SignatureValid": self.signed.get(Signature) == Message}


def test_signed_manifest_verifies_and_recomputed_hashes_do_not(ctx, ledger, repos, calls):
    _enable(ledger, signing={"kmsKeyArn": "arn:aws:kms:us-east-1:111111111111:key/abc"})
    kms = FakeKms()
    ctx.aws = FakeAws(ledger, {("tools", "kms"): kms})
    m = runner.start(ctx, "build", {"lane": "release", "ref": "main", "suites": []}).run.state["result"]
    assert build_mod.verify_bundle(ctx, m, "dev")["signature"]["valid"] is True
    # a writer with bundle access recomputes manifest.sha256 after editing the manifest: the signature catches it
    bdir = build_mod.build_dir(ctx, m["buildId"])
    doc = util.read_json(bdir / "manifest.json")
    doc["commit"] = "f" * 40
    util.write_json(bdir / "manifest.json", doc)
    (bdir / "manifest.sha256").write_text(util.sha256_file(bdir / "manifest.json") + "  manifest.json\n")
    with pytest.raises(errors.VraError, match="another manifest"):
        build_mod.verify_bundle(ctx, doc, "dev")


# ============================================================ gates re-run on resume; terminal runs
def test_resumed_deploy_rechecks_an_approval_that_expired_meanwhile(ctx, ledger, calls):
    dev_evidence(ledger, "cust-v2.6.6-1")
    fake_build(ctx, "cust-v2.6.6-1")
    approve(ledger, "test", "cust-v2.6.6-1", "qa-lead@example.com")
    diff = "Stack vams-core-test\n[~] AWS::DynamoDB::Table T T1 replace\n"

    def handler(argv, cwd, env):
        if "diff" in argv:
            return subprocess.CompletedProcess(argv, 0, diff, "")
        return None
    ctx.shell_cls = fake_shell_cls(calls, handler)
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()})
    ctx.dry_run = True
    set_env(ledger, "test", driftCheck=False)
    out = runner.start(ctx, "deploy", {"env": "test", "build": "cust-v2.6.6-1"}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN and "data-bearing" in out.message
    approve(ledger, "test", "cust-v2.6.6-1", "qa-lead@example.com", expiresAt="2020-01-01T00:00:00Z")
    again = runner.resume(ctx, out.run.run_id)
    assert again.exit_code == errors.NEEDS_HUMAN and "expired" in again.message


def test_rolled_back_run_cannot_be_resumed(ctx):
    run = Run.create(ctx.work_dir, "deploy", {}, "me")
    run.set_status("ROLLED_BACK", "rolled back")
    with pytest.raises(errors.UsageError, match="cannot be resumed"):
        runner.resume(ctx, run.run_id)


# ============================================================ lock held while the env is mid-change
def test_env_stays_locked_when_a_run_fails_after_cdk_deploy_started(ctx, ledger, calls, repos):
    dev_evidence(ledger, "cust-v2.6.6-1")
    fake_build(ctx, "cust-v2.6.6-1", commit=git(repos["fork"], "rev-parse", "main"))
    approve(ledger, "test", "cust-v2.6.6-1", "qa-lead@example.com")
    set_env(ledger, "test", driftCheck=False)

    def handler(argv, cwd, env):
        if "features" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "500")
        return None
    ctx.shell_cls = fake_shell_cls(calls, handler)
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()})
    out = runner.start(ctx, "deploy", {"env": "test", "build": "cust-v2.6.6-1"}, env="test")
    assert out.exit_code == errors.TESTS_FAILED                               # rollbackPolicy=ask on test
    holder = LocalLock(ctx.work_dir).holder("test")
    assert holder["held"] is True and holder["runId"] == out.run.run_id
    ctx.run_id = None
    other = runner.start(ctx, "backup", {"env": "test"}, env="test")
    assert other.exit_code == errors.NEEDS_HUMAN and "held by run" in other.message \
        and "never released by expiry" in other.message
    with pytest.raises(errors.UsageError, match="--reason"):
        runner.unlock(ctx, "test", out.run.run_id, "")
    res = runner.unlock(ctx, "test", out.run.run_id, "rolled back by hand, see CHG-2")
    assert res.exit_code == 0 and LocalLock(ctx.work_dir).holder("test") is None
    assert ledger.history("test")[-1]["action"] == "unlock"


# ============================================================ strict checkpoint for gated envs
def test_gated_env_checkpoint_with_warnings_is_refused(ctx, ledger, aws):
    from test_backup_datacopy import _stack
    _stack("/vams-test-us-east-1/resourceNames", bucket="test-assets")       # moto: PITR off
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    run = Run.create(ctx.work_dir, "x", {}, "me")
    with pytest.raises(NeedsHuman, match="incomplete"):
        run.step("c", lambda sc: backup.create_checkpoint(ctx, "test", run, sc, "pre-deploy",
                                                          strict=backup.strict_for(ctx, "test")))
    set_env(ledger, "test", backup={"allowDegraded": True})
    assert not backup.strict_for(ctx, "test")


# ============================================================ streaming clear
def test_clear_table_streams_in_pages(monkeypatch):
    class Ddb:
        def describe_table(self, TableName):
            return {"Table": {"KeySchema": [{"AttributeName": "id"}]}}

        def scan(self, **kw):
            start = int(kw.get("ExclusiveStartKey", {}).get("id", {}).get("S", 0))
            items = [{"id": {"S": str(i)}} for i in range(start, min(start + 400, 1200))]
            resp = {"Items": items}
            if start + 400 < 1200:
                resp["LastEvaluatedKey"] = {"id": {"S": str(start + 400)}}
            return resp
    sizes = []
    monkeypatch.setattr(restore, "batch_write", lambda ddb, t, reqs: sizes.append(len(reqs)) or len(reqs))
    assert restore.clear_table(Ddb(), "t") == 1200
    assert sizes == [500, 500, 200]


# ============================================================ agent mode
def test_agent_cannot_confirm_or_waive_scanners(monkeypatch, ledger, capsys):
    monkeypatch.setenv("VRA_AGENT", "1")
    assert cli.main(["--ledger", str(ledger.root), "resume", "r1", "--confirm"]) == errors.NEEDS_HUMAN
    assert cli.main(["--ledger", str(ledger.root), "build", "--lane", "release", "--ref", "main",
                     "--allow-missing-scanners"]) == errors.NEEDS_HUMAN


def test_agent_allow_replacements_needs_a_scoped_approval(ctx, calls, monkeypatch):
    monkeypatch.setenv("VRA_AGENT", "1")
    diff = "Stack vams-core-dev\n[~] AWS::DynamoDB::Table T T1 replace\n"

    def handler(argv, cwd, env):
        if "diff" in argv:
            return subprocess.CompletedProcess(argv, 0, diff, "")
        return None
    ctx.shell_cls = fake_shell_cls(calls, handler)
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn()})
    fake_build(ctx, "cust-v2.6.6-1")
    out = runner.start(ctx, "deploy", {"env": "dev", "build": "cust-v2.6.6-1", "allowReplacements": True}, env="dev")
    assert out.exit_code == errors.NEEDS_HUMAN and "scope [allow-replacements]" in out.message


# ============================================================ drift
class DriftedCfn(FakeCfn):
    def describe_stacks(self, StackName):
        return {"Stacks": [{"StackName": StackName, "StackStatus": "UPDATE_COMPLETE",
                            "Description": "(VAMS-CORE) (version:2.6.6)", "Outputs": []}]}

    def detect_stack_drift(self, StackName):
        return {"StackDriftDetectionId": "d1"}

    def describe_stack_drift_detection_status(self, StackDriftDetectionId):
        return {"DetectionStatus": "DETECTION_COMPLETE", "StackDriftStatus": "DRIFTED"}

    def describe_stack_resource_drifts(self, StackName, StackResourceDriftStatusFilters):
        return {"StackResourceDrifts": [{"LogicalResourceId": "Waf", "ResourceType": "AWS::WAFv2::WebACL",
                                         "StackResourceDriftStatus": "MODIFIED"}]}


def test_drift_on_gated_env_blocks_until_accepted_with_approval(ctx, ledger, calls, repos):
    dev_evidence(ledger, "cust-v2.6.6-1")
    m = fake_build(ctx, "cust-v2.6.6-1", commit=git(repos["fork"], "rev-parse", "main"))
    m["targets"]["test"]["stacks"] = ["vams-core-test-us-east-1"]
    util.write_json(build_mod.build_dir(ctx, "cust-v2.6.6-1") / "manifest.json", m)
    bdir = build_mod.build_dir(ctx, "cust-v2.6.6-1")
    (bdir / "manifest.sha256").write_text(util.sha256_file(bdir / "manifest.json") + "  manifest.json\n")
    set_env(ledger, "test", backupBeforeDeploy=False)
    approve(ledger, "test", "cust-v2.6.6-1", "qa-lead@example.com")
    ctx.aws = FakeAws(ledger, {"cloudformation": DriftedCfn()})
    out = runner.start(ctx, "deploy", {"env": "test", "build": "cust-v2.6.6-1"}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN and "drifted" in out.message
    ctx.run_id, ctx.dry_run = None, False
    approve(ledger, "test", "cust-v2.6.6-1", "qa-lead@example.com", scope=["promote", "deploy", "accept-drift"])
    ctx.dry_run = True
    out = runner.start(ctx, "deploy", {"env": "test", "build": "cust-v2.6.6-1", "acceptDrift": True}, env="test")
    assert out.exit_code == 0, out.message


# ============================================================ pinned CDK CLI
def test_cdk_cli_pin_and_override(monkeypatch, tmp_path):
    monkeypatch.setenv("VRA_CDK_CLI", "aws-cdk@latest")
    assert build_mod.cdk_argv("2.1.0", "deploy")[:3] == ["npx", "--yes", "aws-cdk@latest"]
    assert build_mod.cdk_argv("2.1.0", "deploy", allow_override=False)[:3] == ["npx", "--yes", "aws-cdk@2.1.0"]
    assert build_mod.cdk_argv("2.1.0", "diff", allow_override=False, tarball="/b/tools/aws-cdk-2.1.0.tgz") == \
        ["npx", "--yes", "--package", "/b/tools/aws-cdk-2.1.0.tgz", "cdk", "diff"]


def test_cdk_cli_vendored_from_lockfile_with_integrity(ctx, tmp_path):
    import base64
    import hashlib
    tgz_bytes = b"fake-cdk-tarball"
    good = "sha512-" + base64.b64encode(hashlib.sha512(tgz_bytes).digest()).decode()

    class G:
        def __init__(self, integrity):
            self.integrity = integrity

        def show(self, commit, path):
            return json.dumps({"packages": {"node_modules/aws-cdk": {"version": "2.1135.0",
                                                                     "integrity": self.integrity}}})

    def handler(argv, cwd, env):
        if argv[:2] == ["npm", "pack"]:
            dest = Path(argv[argv.index("--pack-destination") + 1])
            (dest / "aws-cdk-2.1135.0.tgz").write_bytes(tgz_bytes)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return None
    ctx.shell_cls = fake_shell_cls([], handler)
    run = Run.create(ctx.work_dir, "x", {}, "me")
    res = run.step("cli", lambda sc: build_mod.vendor_cdk_cli(ctx, G(good), "c", tmp_path / "b1", sc))
    assert res["vendored"] and res["tarball"] == "tools/aws-cdk-2.1135.0.tgz"
    with pytest.raises(errors.VraError, match="lockfile integrity"):
        run.step("cli2", lambda sc: build_mod.vendor_cdk_cli(ctx, G("sha512-other"), "c", tmp_path / "b2", sc))


def test_gated_env_refuses_build_without_cli_version(ctx, ledger):
    dev_evidence(ledger, "cust-v2.6.6-1")
    m = fake_build(ctx, "cust-v2.6.6-1")
    m["cdkCliVersion"] = None
    bdir = build_mod.build_dir(ctx, "cust-v2.6.6-1")
    util.write_json(bdir / "manifest.json", m)
    (bdir / "manifest.sha256").write_text(util.sha256_file(bdir / "manifest.json") + "  manifest.json\n")
    approve(ledger, "test", "cust-v2.6.6-1", "qa-lead@example.com")
    out = runner.start(ctx, "deploy", {"env": "test", "build": "cust-v2.6.6-1"}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN and "CDK CLI version" in out.message


# ============================================================ source data owner approves
def test_data_copy_needs_the_source_data_owners_approval(ctx, ledger):
    approve(ledger, "test", "data-copy:prod", "qa-lead@example.com")
    set_env(ledger, "prod", dataOwners=["data-owner@example.com"])
    out = runner.start(ctx, "data-copy", {"from": "prod", "to": "test", "approval": "data-copy:prod",
                                          "method": "scan", "writesStopped": True}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN and "data-copy-to:test" in out.message
    ctx.run_id = None
    approve(ledger, "prod", "data-copy-to:test", "release-manager@example.com")   # not a data owner
    out = runner.start(ctx, "data-copy", {"from": "prod", "to": "test", "approval": "data-copy:prod",
                                          "method": "scan", "writesStopped": True}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN and "dataOwners" in out.message


# ============================================================ data copy identities
class Recording:
    def __init__(self, client, label, log):
        self.c, self.label, self.log = client, label, log

    def __getattr__(self, name):
        fn = getattr(self.c, name)

        def wrapped(*a, **kw):
            if name == "copy_object":
                self.log.append((self.label, kw["CopySource"]["Bucket"], kw["Bucket"]))
            return fn(*a, **kw)
        return wrapped if callable(fn) else fn


def test_data_copy_reads_source_only_with_the_source_role(ctx, ledger, aws):
    from test_backup_datacopy import _stack, allow_degraded
    p = _stack("/vams-prod-us-east-1/resourceNames", bucket="prod-assets")
    _stack("/vams-test-us-east-1/resourceNames", bucket="test-assets")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="vra-staging-example")
    s3.put_object(Bucket="prod-assets", Key="a1/model.glb", Body=b"m")
    boto3.client("dynamodb", region_name="us-east-1").put_item(
        TableName=p["dynamoTables/assetStorage"], Item={"id": {"S": "a1"}, "assetLocation": {"M": {"Key": {"S": "a1/"}}}})
    approve(ledger, "test", "data-copy:prod", "qa-lead@example.com")
    approve(ledger, "prod", "data-copy-to:test", "release-manager@example.com")
    allow_degraded(ledger, "test")
    set_env(ledger, "test", dataCopy={**ledger.env("test")["dataCopy"], "chainSourceRole": True})
    log: list = []
    fa = FakeAws(ledger, {"cloudformation": FakeCfn(),
                          ("prod", "s3", "dataCopy"): Recording(boto3.client("s3", region_name="us-east-1"), "prod", log),
                          ("test", "s3", "dataCopy"): Recording(boto3.client("s3", region_name="us-east-1"), "test", log)},
                 moto=True)
    ctx.aws = fa
    out = runner.start(ctx, "data-copy", {"from": "prod", "to": "test", "approval": "data-copy:prod",
                                          "method": "scan", "writesStopped": True}, env="test")
    assert out.exit_code == 0, out.message
    assert ("prod", "prod-assets", "vra-staging-example") in log          # hop 1: source role -> staging
    assert ("test", "vra-staging-example", "test-assets") in log          # hop 2: target role <- staging
    assert not [x for x in log if x[0] == "test" and x[1] == "prod-assets"]   # target never reads prod buckets
    assert fa.via[("prod", "dataCopy")] == ("test", "dataCopy")           # chained source role
    assert out.run.find_step("data-copy.preflight.backup")["status"] == "done"  # target checkpoint preflighted


def test_cross_account_copy_without_staging_is_refused(ctx):
    src = {"env": "prod", "role": "dataCopy", "account": "333333333333"}
    dst = {"env": "test", "role": "dataCopy", "account": "222222222222"}

    class S3:
        def get_paginator(self, name):
            class P:
                def paginate(self, **kw):
                    return [{"Contents": [{"Key": "a"}]}]
            return P()
    ctx.aws = FakeAws(ctx.ledger, {"s3": S3()})
    run = Run.create(ctx.work_dir, "x", {}, "me")
    with pytest.raises(NeedsHuman, match="needs dataCopy.stagingBucket"):
        run.step("b", lambda sc: __import__("vra.datacopy", fromlist=["x"]).copy_bucket(
            ctx, run, src, dst, "prod-assets", "test-assets", None, 100, None, None, True, sc))


def test_chained_source_role_is_assumed_from_the_target_loader(ledger):
    calls = []

    class Sts:
        def assume_role(self, **kw):
            calls.append(kw)
            return {"Credentials": {"AccessKeyId": "a", "SecretAccessKey": "b", "SessionToken": "c",
                                    "Expiration": dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)}}

    class Parent:
        def client(self, service, region_name=None):
            return Sts()
    set_env(ledger, "prod", dataCopyRoleArn="arn:aws:iam::333333333333:role/vra-data-copy-source-prod",
            sessionDurationSeconds=7200)
    f = AwsFactory(ledger, "r1")
    f.set_via("prod", "dataCopy", ("test", "dataCopy"))
    f.session = lambda env, role="deploy": Parent() if (env, role) == ("test", "dataCopy") else None
    f.base_session = lambda region=None: (_ for _ in ()).throw(AssertionError("base session used"))
    f.assume_fetcher("prod", "dataCopy")()
    assert calls[0]["RoleArn"].endswith("vra-data-copy-source-prod") and calls[0]["DurationSeconds"] == 3600


# ============================================================ restore temp table matches IAM
def test_restore_temp_table_name_matches_both_iam_grants(ctx, ledger):
    import fnmatch
    name = restore.temp_table_name(ctx, "dev", "dynamoTables/assetStorage", "a1b2c3")
    arn = f"arn:aws:dynamodb:us-east-1:111111111111:table/{name}"
    st = next(s for s in permissions.expand_manifest(permissions.load_manifest("restore"),
                                                    permissions.placeholders(ctx, "dev"))
              if s["sid"] == "TempTableLifecycle")
    assert "dynamodb:DeleteTable" in st["actions"] and "dynamodb:RestoreTableFromBackup" in st["actions"]
    assert any(fnmatch.fnmatchcase(arn, r) for r in st["resources"]), (arn, st["resources"])
    # vra-deploy-role.yaml RestorePolicy: table/vra-restore-${BaseStackName}-* with the RAW base (no region)
    cfn = f"arn:aws:dynamodb:us-east-1:111111111111:table/vra-restore-{ledger.env('dev')['baseStackName']}-*"
    assert fnmatch.fnmatchcase(arn, cfn)
    live = st["resources"][0].replace("vra-restore-", "")
    assert not fnmatch.fnmatchcase(f"arn:aws:dynamodb:us-east-1:111111111111:table/{ledger.core_stack_name('dev')}-X",
                                   st["resources"][0]) and live


def test_restore_keeps_temp_tables_until_the_end(ctx, ledger, aws):
    from test_backup_datacopy import _stack
    names = _stack("/vams-dev-us-east-1/resourceNames")
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    ddb.put_item(TableName=names["dynamoTables/assetStorage"], Item={"id": {"S": "a1"}, "v": {"S": "orig"}})
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    cid = runner.start(ctx, "backup", {"env": "dev"}, env="dev").run.state["checkpoint"]
    ddb.put_item(TableName=names["dynamoTables/assetStorage"], Item={"id": {"S": "a1"}, "v": {"S": "changed"}})
    ctx.run_id = None
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True}, env="dev")
    assert out.exit_code == 0, out.message
    item = ddb.get_item(TableName=names["dynamoTables/assetStorage"], Key={"id": {"S": "a1"}})["Item"]
    assert item["v"]["S"] == "orig"
    deleted = out.run.find_step("restore.cleanup")["result"]["deleted"]
    assert deleted and all(t.startswith("vra-restore-dev-us-east-1-") for t in deleted)
    assert not [t for t in ddb.list_tables()["TableNames"] if t.startswith("vra-restore-")]


# ============================================================ POSIX cron day semantics; re-check
def test_cron_dom_and_dow_are_ored_when_both_restricted():
    w = {"cron": "0 3 1 * 6", "durationMinutes": 60}
    sat = dt.datetime(2026, 11, 7, 3, 30, tzinfo=dt.timezone.utc)    # Saturday the 7th
    first = dt.datetime(2026, 12, 1, 3, 30, tzinfo=dt.timezone.utc)  # Tuesday the 1st
    tue = dt.datetime(2026, 11, 3, 3, 30, tzinfo=dt.timezone.utc)
    assert cron.in_window(w, sat) and cron.in_window(w, first) and not cron.in_window(w, tue)
    assert cron.in_window({"cron": "0 3 * * 6"}, sat) and not cron.in_window({"cron": "0 3 * * 6"}, first)


def test_change_window_is_rechecked_before_cdk_deploy(ctx, calls):
    fake_build(ctx, "cust-v2.6.6-1")
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn()})
    out = runner.start(ctx, "deploy", {"env": "dev", "build": "cust-v2.6.6-1"}, env="dev")
    assert out.exit_code == 0, out.message
    ids = [s["id"] for s in out.run.state["steps"]]
    assert ids.index("deploy.dev.window.cdk-deploy") < ids.index("deploy.dev.cdk-deploy")


# ============================================================ redaction
def test_redaction_covers_json_jwt_cookie_and_known_secrets():
    util.register_secret("Pa55-live-test")
    text = ('{"password": "hunter2", "apiKey":"abc", \'IdToken\': \'x\'} Cookie: s=1; t=2\n'
            "Authorization: Basic Zm9vOmJhcg== jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4fwpMeJf36P "
            "login with Pa55-live-test")
    out = util.redact(text)
    for secret in ("hunter2", '"abc"', "'x'", "s=1", "Zm9vOmJhcg", "eyJhbGciOiJIUzI1", "Pa55-live-test"):
        assert secret not in out, (secret, out)


# ============================================================ source identity
def test_source_identity_passed_when_env_requires_it(ledger):
    calls = []

    class Sts:
        def assume_role(self, **kw):
            calls.append(kw)
            return {"Credentials": {"AccessKeyId": "a", "SecretAccessKey": "b", "SessionToken": "c",
                                    "Expiration": dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)}}

    class Base:
        def client(self, service, region_name=None):
            return Sts()
    set_env(ledger, "prod", setSourceIdentity=True)
    f = AwsFactory(ledger, "r1")
    f.source_identity_value = "arn:aws:sts::1:assumed-role/Ops/alice@example.com"
    f.base_session = lambda region=None: Base()
    f.assume_fetcher("prod")()
    assert calls[0]["SourceIdentity"] == "arn-aws-sts--1-assumed-role-Ops-alice@example.com"
    f2 = AwsFactory(ledger, "r1")
    f2.source_identity_value = "x"
    f2.base_session = lambda region=None: Base()
    f2.assume_fetcher("dev")()
    assert "SourceIdentity" not in calls[1]


def test_gated_synth_without_credentials_that_needs_lookups_says_what_to_set(tmp_path):
    """Live test: stage (existing VPC) as a gated env without synthRoleArn failed `cdk synth` with a bare exit 1
    ("Need to perform AWS calls ... but no credentials have been configured"). The build now parks with the fix."""
    log = tmp_path / "synth.log"
    log.write_text("ERROR Need to perform AWS calls for account 222222222222, but no credentials have been "
                   "configured (Construct Annotations)\n")
    with pytest.raises(NeedsHuman, match="synthRoleArn") as exc:
        build_mod._raise_if_lookups_need_credentials(log, "test", "none")
    assert "cdk.context.json" in exc.value.message
    build_mod._raise_if_lookups_need_credentials(log, "test", "synth")      # had credentials: other failure
    other = tmp_path / "other.log"
    other.write_text("TypeError: something else\n")
    build_mod._raise_if_lookups_need_credentials(other, "test", "none")     # unrelated failure: unchanged


def test_synth_role_that_cannot_be_assumed_blocks_before_synth(ctx, ledger, repos, calls):
    """Live test: the synth role's trust lacked sts:SetSourceIdentity, so `vra credential-process` failed inside cdk
    and synth ended minutes later in a generic error. The build proves the role first and writes an IT request."""
    from botocore.exceptions import ClientError

    class DeniedSts:
        def get_caller_identity(self):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": (
                "User: arn:aws:sts::333333333333:assumed-role/Admin/op is not authorized to perform: "
                "sts:SetSourceIdentity on resource: arn:aws:iam::333333333333:role/vra-synth-prod")}}, "AssumeRole")
    set_env(ledger, "prod", synthRoleArn="arn:aws:iam::333333333333:role/vra-synth-prod")
    ctx.aws.clients[("prod", "sts", "synth")] = DeniedSts()
    out = runner.start(ctx, "build", {"lane": "release", "ref": "main", "suites": []})
    assert out.exit_code == errors.BLOCKED_ON_IT, out.message
    assert "SetSourceIdentity" in out.message
    assert not [c for c in calls if "synth" in c["argv"] and "/prod" in " ".join(c["argv"])]   # never synthesized


def test_iam_plan_proves_the_synth_role_before_synth(ctx, ledger, repos, calls):
    """Live test: `vra iam plan --env test` (stage, existing VPC) used the gated env's synth role, whose trust lacked
    sts:SetSourceIdentity; the VPC lookup inside cdk found no credentials and the plan failed with a bare
    `command failed (1)`. Like `vra build`, iam plan now proves the role first and writes an IT request."""
    from botocore.exceptions import ClientError

    class DeniedSts:
        def get_caller_identity(self):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": (
                "User: arn:aws:sts::333333333333:assumed-role/Admin/op is not authorized to perform: "
                "sts:SetSourceIdentity on resource: arn:aws:iam::333333333333:role/vra-synth-prod")}}, "AssumeRole")
    set_env(ledger, "prod", synthRoleArn="arn:aws:iam::333333333333:role/vra-synth-prod")
    ctx.aws.clients[("prod", "sts", "synth")] = DeniedSts()
    out = runner.start(ctx, "iam-plan", {"env": "prod", "modes": ["precreated"], "ref": "main", "report": None},
                       env="prod")
    assert out.exit_code == errors.BLOCKED_ON_IT, out.message
    assert "SetSourceIdentity" in out.message
    assert not [c for c in calls if "synth" in c["argv"]]   # never synthesized
