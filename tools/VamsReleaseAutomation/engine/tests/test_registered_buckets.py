# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Registered asset buckets: per-bucket preflight, recorded grants and phase checks, strict data-copy
bucket mapping, and the shared-bucket guard. VAMS lets operators register asset buckets at any time, including
customer-named buckets and buckets in other accounts with their own KMS keys."""

import fnmatch
import json

import boto3
import pytest

from vra import buckets, datacopy, errors, permissions, runner, setup as setup_mod, util
from vra.context import Context
from vra.errors import NeedsHuman, PermissionDenied
from vra.ledger import Ledger

from conftest import FakeAws, stub_inventory
from test_backup_datacopy import _stack, allow_degraded
from test_build_deploy import FakeCfn, LiveCfn, _build

moto = pytest.importorskip("moto")

DEV_PREFIX = "/vams-dev-us-east-1/resourceNames"
STACK_BUCKET = "vams-core-dev-us-east-1-assetbucket"  # named after the core stack: stock templates grant it
CUSTOMER = "customer-archive"
OTHER_KEY = "arn:aws:kms:us-east-1:444444444444:key/1111aaaa-2222-bbbb-3333-cccc4444dddd"


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with moto.mock_aws():
        yield


class PatternIam:
    """Fake IAM shaped like the stock role templates: S3 only on the allowed bucket patterns, KMS only on the
    allowed key patterns, everything else allowed."""

    def __init__(self, s3=("arn:aws:s3:::vams-core-*", "arn:aws:s3:::vra-*"),
                 kms=("arn:aws:kms:*:111111111111:key/*", "arn:aws:kms:*:222222222222:key/*")):
        self.s3, self.kms, self.calls = list(s3), list(kms), []

    def _ok(self, r):
        if "*" in r:  # static manifest patterns (staging keys, key/*) are not what these tests are about
            return True
        if r.startswith("arn:aws:s3:::"):
            return any(fnmatch.fnmatchcase(r, p) for p in self.s3)
        if ":kms:" in r:
            return any(fnmatch.fnmatchcase(r, p) for p in self.kms)
        return True

    def simulate_principal_policy(self, **kw):
        self.calls.append(kw)
        res = kw.get("ResourceArns") or ["*"]
        ok = all(r == "*" or self._ok(r) for r in res)
        return {"EvaluationResults": [{"EvalActionName": a, "EvalResourceName": res[0],
                                       "EvalDecision": "allowed" if ok else "implicitDeny"}
                                      for a in kw["ActionNames"]], "IsTruncated": False}


def set_registered(ledger, env, **rb):
    d = util.read_yaml(ledger.root / f"envs/{env}/env.yaml")
    d["registeredBuckets"] = rb
    util.write_yaml(ledger.root / f"envs/{env}/env.yaml", d)
    ledger._envs.clear()


def register_bucket(table, name, bucket_id, kms_key=None):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=name)
    s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": "Enabled"})
    if kms_key:
        s3.put_bucket_encryption(Bucket=name, ServerSideEncryptionConfiguration={"Rules": [
            {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms", "KMSMasterKeyID": kms_key}}]})
    boto3.client("dynamodb", region_name="us-east-1").put_item(TableName=table, Item={
        "id": {"S": bucket_id}, "bucketId": {"S": bucket_id}, "bucketName": {"S": name}, "baseAssetsPrefix": {"S": "/"}})


def dev_stack():
    """dev: stack-named SSM + default asset bucket, plus a customer-named bucket with a KMS key in another
    account registered in s3AssetBucketsStorage."""
    names = _stack(DEV_PREFIX, bucket=STACK_BUCKET)
    register_bucket(names["dynamoTables/s3AssetBucketsStorage"], CUSTOMER, "cust-1", OTHER_KEY)
    return names


# ------------------------------------------------------------------ 1. preflight resolves the real buckets
@pytest.mark.parametrize("workflow", ["backup", "restore", "migrate", "data-copy-source", "data-copy-target"])
def test_preflight_simulates_every_registered_bucket_and_its_key(ctx, ledger, aws, workflow):
    dev_stack()
    set_registered(ledger, "dev", granted=False)
    iam = PatternIam()
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn(), "iam": iam}, moto=True)
    with pytest.raises(PermissionDenied) as ei:
        permissions.preflight(ctx, "dev", workflow)
    dn = ei.value.denials
    hit = [d for d in dn if d.get("bucket") == CUSTOMER]
    assert hit and all(d["resource"].startswith(f"arn:aws:s3:::{CUSTOMER}") for d in hit)
    assert not any("vams-core-dev" in (d.get("resource") or "") for d in dn)  # stack-owned buckets are fine
    assert not any(d.get("resource") == "*" for d in hit)
    # the simulation ran against the concrete bucket ARN (not the ${CoreStack}* pattern)
    sims = [r for c in iam.calls for r in (c.get("ResourceArns") or [])]
    assert f"arn:aws:s3:::{CUSTOMER}" in sims and f"arn:aws:s3:::{STACK_BUCKET}" in sims
    if workflow != "backup":  # every workflow that reads/writes objects needs the bucket's own KMS key
        k = [d for d in dn if d.get("kmsKey") == OTHER_KEY]
        assert k and {d["action"] for d in k} >= {"kms:Decrypt"}
        ctxs = [c for c in iam.calls if OTHER_KEY in (c.get("ResourceArns") or [])]
        assert ctxs and ctxs[0]["ContextEntries"][0]["ContextKeyName"] == "kms:ViaService"

    # IT granted it and the ledger records it: the same preflight passes
    set_registered(ledger, "dev", granted=True, bucketArns=[f"arn:aws:s3:::{CUSTOMER}"], kmsKeyArns=[OTHER_KEY])
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn(), "iam": PatternIam(
        s3=["arn:aws:s3:::vams-core-*", "arn:aws:s3:::vra-*", f"arn:aws:s3:::{CUSTOMER}*"],
        kms=["arn:aws:kms:*:111111111111:key/*", OTHER_KEY])}, moto=True)
    out = permissions.preflight(ctx, "dev", workflow)
    assert out["registeredBuckets"]["resolved"] is True
    assert {b["name"] for b in out["registeredBuckets"]["buckets"]} >= {CUSTOMER, STACK_BUCKET}


def test_preflight_before_first_deploy_falls_back_to_the_stack_pattern(ctx, ledger, aws):
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn(), "iam": PatternIam()}, moto=True)
    out = permissions.preflight(ctx, "dev", "restore")
    assert out["registeredBuckets"]["resolved"] is False
    assert out["registeredBuckets"]["pattern"] == "vams-core-dev-us-east-1*"


def test_manifests_use_the_registered_bucket_template_for_every_s3_bucket_statement():
    for wf in ("backup", "restore", "migrate", "data-copy-source", "data-copy-target"):
        m = permissions.load_manifest(wf)
        for st in m["statements"]:
            res = " ".join(st.get("resources") or [])
            if st.get("forEach") == "registeredBucket":
                assert "${RegisteredBucket}" in res, (wf, st["sid"])
            elif st.get("forEach") == "registeredBucketKmsKey":
                assert res == "${RegisteredBucketKmsKey}", (wf, st["sid"])
            else:
                assert "s3:::${CoreStack}" not in res and "${Registered" not in res, (wf, st["sid"])
        # without a bucket list (dry run) the bucket statements fall back to the stack pattern, keys are skipped
        flat = permissions.expand_manifest(m, {"Partition": "aws", "CoreStack": "vams-core-x"})
        assert not any("${Registered" in r for s in flat for r in s["resources"])


# ------------------------------------------------------------------ 2. phase checks, IT request, resume
def test_backup_blocks_on_an_ungranted_registered_bucket_and_resume_rechecks(ctx, ledger, aws):
    dev_stack()
    set_registered(ledger, "dev", granted=False)
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn(), "iam": PatternIam()}, moto=True)
    # the IAM preflight is green (identity policy allows the stack buckets); the registered bucket check is not
    out = runner.start(ctx, "backup", {"env": "dev"}, env="dev")
    assert out.exit_code == errors.BLOCKED_ON_IT, out.message
    assert CUSTOMER in out.message
    req = util.read_json(out.run.it_dir / "IT-REQUEST-1.json")
    rows = {r["bucket"]: r for r in req["registeredBuckets"]}
    assert set(rows) == {CUSTOMER}
    assert rows[CUSTOMER]["actions"] == sorted(["s3:GetBucketLocation", "s3:GetBucketVersioning",
                                                "s3:GetEncryptionConfiguration", "s3:ListBucket",
                                                "s3:ListBucketVersions"])
    # least-privilege policy from the concrete bucket ARN, no wildcards, no stack buckets
    resources = [r for s in req["policy"]["Statement"] for r in ([s["Resource"]] if isinstance(s["Resource"], str)
                                                               else s["Resource"])]
    assert resources == [f"arn:aws:s3:::{CUSTOMER}"]
    md = (out.run.it_dir / "IT-REQUEST-1.md").read_text()
    assert "## Registered asset buckets" in md and "registeredBuckets" in md
    assert not out.run.find_step("backup.dev")  # nothing was checkpointed

    # resume without a change: re-checked, still blocked, IT request n+1
    again = runner.resume(ctx, out.run.run_id)
    assert again.exit_code == errors.BLOCKED_ON_IT
    assert [r["id"] for r in again.run.state["itRequests"]][-1] == "IT-REQUEST-2"

    # granted and recorded in env.yaml -> resume continues and the checkpoint runs
    set_registered(ledger, "dev", granted=True, bucketArns=[f"arn:aws:s3:::{CUSTOMER}"])
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn(), "iam": PatternIam(
        s3=["arn:aws:s3:::vams-core-*", f"arn:aws:s3:::{CUSTOMER}"])}, moto=True)
    done = runner.resume(ctx, out.run.run_id)
    assert done.exit_code == 0, done.message
    assert done.run.find_step("backup.dev")["status"] == "done"
    inv = [h for h in ledger.history("dev") if h["action"] == "bucket-inventory"][-1]
    assert CUSTOMER in inv["buckets"] and OTHER_KEY in inv["kmsKeys"]


def test_a_bucket_registered_after_the_last_run_gets_a_new_it_request_naming_it_new(ctx, ledger, aws):
    names = dev_stack()
    set_registered(ledger, "dev", granted=True, bucketArns=[f"arn:aws:s3:::{CUSTOMER}"], kmsKeyArns=[OTHER_KEY])
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    assert runner.start(ctx, "backup", {"env": "dev"}, env="dev").exit_code == 0
    # an operator registers another customer bucket in VAMS
    register_bucket(names["dynamoTables/s3AssetBucketsStorage"], "customer-new-intake", "cust-2")
    ctx.run_id = None
    out = runner.start(ctx, "backup", {"env": "dev"}, env="dev")
    assert out.exit_code == errors.BLOCKED_ON_IT
    assert "newly registered since the last check: customer-new-intake" in out.message
    req = util.read_json(out.run.it_dir / "IT-REQUEST-1.json")
    rows = {r["bucket"]: r for r in req["registeredBuckets"]}
    assert list(rows) == ["customer-new-intake"] and rows["customer-new-intake"]["newlyRegistered"] is True
    assert "registered in VAMS after the last check" in (out.run.it_dir / "IT-REQUEST-1.md").read_text()


def test_restore_blocks_on_an_ungranted_bucket_and_its_key(ctx, ledger, aws):
    dev_stack()
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    set_registered(ledger, "dev", granted=True, bucketArns=["arn:aws:s3:::*"], kmsKeyArns=[OTHER_KEY])
    cid = runner.start(ctx, "backup", {"env": "dev"}, env="dev").run.state["checkpoint"]
    set_registered(ledger, "dev", granted=False)
    ctx.run_id = None
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True}, env="dev")
    assert out.exit_code == errors.BLOCKED_ON_IT
    assert not out.run.find_step("restore.safety-checkpoint")  # stopped before anything changed
    req = util.read_json(out.run.it_dir / "IT-REQUEST-1.json")
    assert {r["kmsKey"] for r in req["registeredBuckets"] if r.get("kmsKey")} == {OTHER_KEY}
    assert any(r["bucket"] == CUSTOMER and "s3:PutObject" in r["actions"] for r in req["registeredBuckets"])
    key_stmt = [s for s in req["policy"]["Statement"] if s["Resource"] == OTHER_KEY]
    assert key_stmt and "kms:Decrypt" in key_stmt[0]["Action"]


def test_a_bucket_registered_while_a_restore_is_parked_is_caught_on_resume(ctx, ledger, aws):
    """The check is re-validated at the start of the phase on every resume, not just the first time."""
    names = dev_stack()
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    set_registered(ledger, "dev", granted=True, bucketArns=[f"arn:aws:s3:::{CUSTOMER}"], kmsKeyArns=[OTHER_KEY])
    cid = runner.start(ctx, "backup", {"env": "dev"}, env="dev").run.state["checkpoint"]
    ctx.run_id = None
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid}, env="dev")
    assert out.exit_code == errors.NEEDS_HUMAN  # parks for the manual write-freeze confirmation
    assert out.run.find_step("restore.registered-buckets")["status"] == "done"
    register_bucket(names["dynamoTables/s3AssetBucketsStorage"], "customer-late", "cust-3")
    again = runner.resume(ctx, out.run.run_id, confirm=True)
    assert again.exit_code == errors.BLOCKED_ON_IT, again.message
    assert again.run.find_step("restore.registered-buckets")["status"] == "blocked"
    assert "newly registered since the last check: customer-late" in again.message
    assert not again.run.find_step("restore.table.dynamoTables/assetStorage")  # no table was cleared


def test_deploy_without_bucket_phases_is_not_blocked_but_reports_the_pending_it_request(ctx, repos, calls,
                                                                                       monkeypatch):
    b = _build(ctx).run.state["result"]["buildId"]
    set_registered(ctx.ledger, "dev", granted=False)
    stub_inventory(monkeypatch, [{"name": "vams-core-dev-us-east-1-assetbucket"}, {"name": CUSTOMER}])
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn()})  # first install: no checkpoint, no migrations
    out = runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev", build=b)
    assert out.exit_code == 0, out.message
    its = out.run.state["itRequests"]
    assert len(its) == 1 and its[0]["blocking"] is False and its[0]["status"] == "open"
    assert out.to_dict()["itRequests"][0]["id"] == "IT-REQUEST-1"
    report = (out.run.dir / "report.md").read_text()
    assert "IT-REQUEST-1 (open): pending, did not block this run" in report
    assert any("IT-REQUEST-1 is pending" in n for n in out.run.state["notes"])
    assert ctx.ledger.history("dev")[-1]["action"] == "deploy"


def test_deploy_with_data_migrations_blocks_before_cdk_deploy(ctx, repos, calls, monkeypatch):
    b = _build(ctx).run.state["result"]["buildId"]
    set_registered(ctx.ledger, "dev", granted=False, bucketArns=[])
    d = util.read_yaml(ctx.ledger.root / "envs/dev/env.yaml")
    d["backupBeforeDeploy"] = False
    util.write_yaml(ctx.ledger.root / "envs/dev/env.yaml", d)
    ctx.ledger._envs.clear()
    stub_inventory(monkeypatch, [{"name": CUSTOMER}])
    ctx.aws = FakeAws(ctx.ledger, {"cloudformation": LiveCfn(
        "(SO9299) (VAMS-CORE) (version:2.5.3) Primary Components for the Visual Asset Management Systems")})
    out = runner.start(ctx, "deploy", {"env": "dev", "build": b}, env="dev", build=b)
    assert out.exit_code == errors.BLOCKED_ON_IT, out.message
    assert out.run.find_step("deploy.dev.registered-buckets")["status"] == "blocked"
    assert not out.run.find_step("deploy.dev.cdk-deploy")
    assert not any("deploy" in c["argv"] and "--app" in c["argv"] and "diff" not in c["argv"] for c in calls)
    assert not (ctx.work_dir / "locks" / "dev.lock").exists()  # nothing changed: the lock is released


# ------------------------------------------------------------------ 2b. setup asks
def test_setup_asks_and_records_registered_bucket_grants(tmp_path):
    answers = {"project": {"name": "example-fork"}, "fork": "https://git.example.com/acme/vams-fork.git",
               "githost": {"type": "gitlab"},
               "envs": {"dev": {"account": "111111111111", "region": "us-east-1",
                                "registeredBuckets": {"granted": False}},
                        "test": {"account": "222222222222", "region": "us-east-1",
                                 "approvers": "qa-lead@example.com"},
                        "prod": {"account": "333333333333", "region": "us-east-1",
                                 "approvers": "release-manager@example.com",
                                 "registeredBuckets": {"granted": False}}}}
    ans = tmp_path / "answers.yaml"
    util.write_yaml(ans, answers)
    asked = []

    def prompt(question, default):
        asked.append(question)
        if "[test]" in question and "been granted access" in question:
            return "yes"
        if "[test]" in question and "Bucket ARNs" in question:
            return "arn:aws:s3:::example-assets-*,arn:aws:s3:::example-intake"
        if "[test]" in question and "KMS key ARNs" in question:
            return OTHER_KEY
        return ""

    lg = Ledger(tmp_path / "ledger")
    ctx = Context(ledger=lg, work_dir=tmp_path / "work", non_interactive=False, initiator="op@example.com",
                  prompt=prompt)
    ctx.aws = FakeAws(lg)
    res = setup_mod.run_setup(ctx, str(ans), skip_preflight=True)
    assert res["validation"]["ok"], res["validation"]["errors"]
    assert any("[test]" in q and "registered in VAMS" in q for q in asked)
    assert not any("[dev]" in q and "been granted access" in q for q in asked)  # answered in the file
    lg = Ledger(tmp_path / "ledger")
    assert lg.raw_env("dev")["registeredBuckets"] == {"granted": False, "bucketArns": [], "kmsKeyArns": []}
    assert lg.raw_env("test")["registeredBuckets"] == {
        "granted": True, "bucketArns": ["arn:aws:s3:::example-assets-*", "arn:aws:s3:::example-intake"],
        "kmsKeyArns": [OTHER_KEY]}
    assert any(n.startswith("dev: registeredBuckets.granted=false") and "exit 10" in n for n in res["notes"])


def test_ledger_validate_checks_registered_bucket_arns(ledger):
    set_registered(ledger, "dev", granted=True, bucketArns=["arn:aws-us-gov:s3:::x"])
    v = ledger.validate()
    assert any("registeredBuckets ARN arn:aws-us-gov:s3:::x is not in partition aws" in e for e in v["errors"])
    set_registered(ledger, "dev", granted=True, bucketArns=[])
    assert any("registeredBuckets.granted is true but bucketArns is empty" in w for w in ledger.validate()["warnings"])


# ------------------------------------------------------------------ 3. data copy stops on unmapped buckets
def _side(env, assets):
    return {"env": env, "account": "1", "region": "us-east-1", "partition": "aws", "names": {"all": {}, "buckets": {}},
            "buckets": [{"name": a, "kind": "asset", "bucketId": a + "-id", "baseAssetsPrefix": "/"} for a in assets]}


def test_every_source_asset_bucket_needs_a_distinct_target():
    src, dst = _side("prod", ["prod-assets", "prod-archive"]), _side("test", ["test-assets"])
    with pytest.raises(NeedsHuman) as ei:  # 2 vs 1, no map: both named, never skipped
        datacopy.build_remap(src, dst)
    assert "prod-assets" in ei.value.message and "prod-archive" in ei.value.message
    assert "dataCopy:\n    bucketMap:" in ei.value.message and ei.value.detail["fix"] == "envs/test/env.yaml dataCopy.bucketMap"
    with pytest.raises(NeedsHuman, match="prod-archive \\(no dataCopy.bucketMap entry\\)"):  # partial map
        datacopy.build_remap(src, dst, {"prod-assets": "test-assets"})
    with pytest.raises(NeedsHuman, match="not a registered asset bucket of test"):
        datacopy.build_remap(src, dst, {"prod-assets": "test-assets", "prod-archive": "nope"})
    dst2 = _side("test", ["test-assets", "test-archive"])
    with pytest.raises(NeedsHuman, match="all map to test-assets"):  # two sources into one target
        datacopy.build_remap(src, dst2, {"prod-assets": "test-assets", "prod-archive": "test-assets"})
    _, info = datacopy.build_remap(src, dst2, {"prod-assets": "test-assets", "prod-archive": "test-archive"})
    assert sorted(info["bucketPairs"]) == [("prod-archive", "test-archive"), ("prod-assets", "test-assets")]
    with pytest.raises(NeedsHuman, match="1 source vs 0 target"):
        datacopy.build_remap(_side("prod", ["prod-assets"]), _side("test", []))


def _copy_setup(ledger):
    from test_gates import approve
    approve(ledger, "test", "data-copy:prod", "qa-lead@example.com")
    approve(ledger, "prod", "data-copy-to:test", "release-manager@example.com")
    allow_degraded(ledger, "test")


def test_data_copy_with_an_unmapped_bucket_stops_before_writing(ctx, ledger, aws):
    p = _stack("/vams-prod-us-east-1/resourceNames", bucket="prod-assets")
    _stack("/vams-test-us-east-1/resourceNames", bucket="test-assets")
    register_bucket(p["dynamoTables/s3AssetBucketsStorage"], "prod-archive", "arch-1")
    _copy_setup(ledger)
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    out = runner.start(ctx, "data-copy", {"from": "prod", "to": "test", "approval": "data-copy:prod",
                                          "method": "scan", "writesStopped": True}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN, out.message
    assert "prod-archive" in out.message and "prod-assets" in out.message and "bucketMap" in out.message
    assert out.run.find_step("data-copy.bucket-map")["status"] == "blocked"
    assert not out.run.find_step("data-copy.checkpoint") and not out.run.find_step("data-copy.write-freeze")


def test_data_copy_blocks_on_an_ungranted_target_bucket_before_the_checkpoint(ctx, ledger, aws):
    _stack("/vams-prod-us-east-1/resourceNames", bucket="vams-core-prod-us-east-1-assets")
    t = _stack("/vams-test-us-east-1/resourceNames", bucket="vams-core-test-us-east-1-assets")
    register_bucket(t["dynamoTables/s3AssetBucketsStorage"], "customer-test-intake", "ti-1")
    register_bucket(boto3.client("ssm", region_name="us-east-1").get_parameter(
        Name="/vams-prod-us-east-1/resourceNames/dynamoTables/s3AssetBucketsStorage")["Parameter"]["Value"],
        "customer-prod-intake", "pi-1")
    d = util.read_yaml(ledger.root / "envs/test/env.yaml")
    d["dataCopy"]["bucketMap"] = {"vams-core-prod-us-east-1-assets": "vams-core-test-us-east-1-assets",
                                  "customer-prod-intake": "customer-test-intake"}
    util.write_yaml(ledger.root / "envs/test/env.yaml", d)
    ledger._envs.clear()
    set_registered(ledger, "test", granted=False)
    _copy_setup(ledger)
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    out = runner.start(ctx, "data-copy", {"from": "prod", "to": "test", "approval": "data-copy:prod",
                                          "method": "scan", "writesStopped": True}, env="test")
    assert out.exit_code == errors.BLOCKED_ON_IT, out.message
    assert "customer-test-intake" in out.message
    assert not out.run.find_step("data-copy.checkpoint") and not out.run.find_step("data-copy.write-freeze")
    req = util.read_json(out.run.it_dir / "IT-REQUEST-1.json")
    row = next(r for r in req["registeredBuckets"] if r["bucket"] == "customer-test-intake")
    assert "s3:PutObject" in row["actions"] and req["role"] == "dataCopy"


# ------------------------------------------------------------------ 4. shared-bucket guard
def test_data_copy_refuses_a_target_bucket_that_is_a_source_bucket(ctx, ledger, aws):
    _stack("/vams-prod-us-east-1/resourceNames", bucket="prod-assets")
    t = _stack("/vams-test-us-east-1/resourceNames", bucket="test-assets")
    # someone registered prod's bucket in test
    boto3.client("dynamodb", region_name="us-east-1").put_item(
        TableName=t["dynamoTables/s3AssetBucketsStorage"],
        Item={"id": {"S": "shared"}, "bucketId": {"S": "shared"}, "bucketName": {"S": "prod-assets"},
              "baseAssetsPrefix": {"S": "/"}})
    d = util.read_yaml(ledger.root / "envs/test/env.yaml")
    d["dataCopy"]["bucketMap"] = {"prod-assets": "test-assets"}
    util.write_yaml(ledger.root / "envs/test/env.yaml", d)
    ledger._envs.clear()
    _copy_setup(ledger)
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    out = runner.start(ctx, "data-copy", {"from": "prod", "to": "test", "approval": "data-copy:prod",
                                          "method": "scan", "writesStopped": True}, env="test")
    assert out.exit_code == errors.NEEDS_HUMAN, out.message
    assert "share bucket(s) prod-assets" in out.message and "source prod" in out.message
    assert not out.run.find_step("data-copy.checkpoint")


def test_restore_refuses_a_bucket_another_env_uses_and_names_it(ctx, ledger, aws):
    _stack(DEV_PREFIX, bucket=STACK_BUCKET)
    ctx.aws = FakeAws(ledger, {"cloudformation": FakeCfn()}, moto=True)
    cid = runner.start(ctx, "backup", {"env": "dev"}, env="dev").run.state["checkpoint"]
    # prod's recorded inventory shows it registered dev's default asset bucket
    ledger.append_history("prod", {"action": "bucket-inventory", "result": "SUCCEEDED",
                                   "buckets": ["prod-assets", STACK_BUCKET]})
    ctx.run_id = None
    out = runner.start(ctx, "restore", {"env": "dev", "checkpoint": cid, "writesStopped": True}, env="dev")
    assert out.exit_code == errors.NEEDS_HUMAN, out.message
    assert f"{STACK_BUCKET} (also used by prod)" in out.message and "prod is prod-class" in out.message
    assert not out.run.find_step("restore.safety-checkpoint") and not out.run.find_step("restore.write-freeze")
    # rollback with restore goes through the same guard
    ctx.run_id = None
    rb = runner.start(ctx, "rollback", {"env": "dev", "restoreCheckpoint": cid, "writesStopped": True}, env="dev")
    assert rb.exit_code == errors.NEEDS_HUMAN and "also used by prod" in rb.message


def test_shared_guard_reads_other_envs_from_history_and_literal_grants(ctx, ledger):
    set_registered(ledger, "test", granted=True, bucketArns=["arn:aws:s3:::lab-shared", "arn:aws:s3:::lab-*"])
    ledger.append_history("prod", {"action": "backup", "result": "SUCCEEDED", "bucketNames": ["prod-only"]})
    from vra.state import Run
    run = Run.create(ctx.work_dir, "restore", {}, "me")
    with pytest.raises(NeedsHuman, match=r"lab-shared \(also used by test\)"):
        buckets.shared_guard(ctx, run, "dev", ["dev-assets", "lab-shared"], "restore of x")
    assert buckets.shared_guard(ctx, run, "dev", ["dev-assets", "lab-other"], "restore of x")["checked"]
    # live discovery of the other envs is best effort (no AWS here): noted, history still counts
    assert any("live bucket discovery skipped" in n for n in run.state["notes"])
    json.dumps(run.state)  # state stays serialisable


# ------------------------------------------------------------------ IAM templates: AdditionalBucketArns / AdditionalKmsKeyArns
def _conditions(t: dict, params: dict) -> dict:
    from test_iam_and_ci_roles import render
    out: dict = {}

    def ev(n):
        (k, v), = n.items()
        if k == "Fn::Equals":
            a, b = (render(x, params, out, {}) for x in v)
            return (a if not isinstance(a, list) else ",".join(a)) == (b if not isinstance(b, list) else ",".join(b))
        if k == "Fn::Not":
            return not ev(v[0])
        if k == "Fn::And":
            return all(ev(x) for x in v)
        if k == "Fn::Or":
            return any(ev(x) for x in v)
        if k in ("Condition", "Fn::Condition"):
            return out[v]
        raise AssertionError(k)
    pending = dict(t["Conditions"])
    while pending:  # conditions may reference conditions defined later
        for name, node in list(pending.items()):
            try:
                out[name] = ev(node)
                del pending[name]
            except KeyError:
                continue
    return out


def _policy(rel: str, resource: str, extra: dict) -> list:
    from test_iam_and_ci_roles import load_cfn, render
    t = load_cfn(rel)
    params = {"AWS::Partition": "aws", "AWS::AccountId": "111111111111", "AWS::Region": "us-east-1",
              **{k: v.get("Default", "") for k, v in t["Parameters"].items()}, **extra}
    for k, v in params.items():
        if t["Parameters"].get(k, {}).get("Type") == "CommaDelimitedList" and isinstance(v, str):
            params[k] = v.split(",") if v else [""]
    conds = _conditions(t, params)
    res = t["Resources"][resource]
    doc = res["Properties"].get("PolicyDocument") or res["Properties"]["Policies"][0]["PolicyDocument"]
    return render(doc, params, conds, {"StagingBucket": "arn:aws:s3:::vra-data-copy-111111111111-us-east-1",
                                       "StagingKey": "arn:aws:kms:us-east-1:111111111111:key/staging"})["Statement"]


BUCKETS = {"AdditionalBucketArns": [f"arn:aws:s3:::{CUSTOMER}"], "AdditionalKmsKeyArns": [OTHER_KEY]}
VIA_S3 = {"kms:ViaService": "s3.us-east-1.amazonaws.com"}


def test_deploy_role_template_grants_the_registered_buckets_and_keys():
    from test_iam_and_ci_roles import allowed
    base = {"EnvName": "dev", "BaseStackName": "dev", "TrustedPrincipalArns": ["arn:aws:iam::555555555555:role/ci"]}
    plain = _policy("iam/cloudformation/vra-deploy-role.yaml", "DataPolicy", base)
    assert not allowed(plain, "s3:ListBucketVersions", f"arn:aws:s3:::{CUSTOMER}")
    data = _policy("iam/cloudformation/vra-deploy-role.yaml", "DataPolicy", {**base, **BUCKETS})
    assert allowed(data, "s3:ListBucketVersions", f"arn:aws:s3:::{CUSTOMER}")
    assert allowed(data, "s3:GetEncryptionConfiguration", f"arn:aws:s3:::{CUSTOMER}")
    assert allowed(data, "s3:GetObject", f"arn:aws:s3:::{CUSTOMER}/a1/model.glb")
    assert allowed(data, "kms:Decrypt", OTHER_KEY, VIA_S3) and not allowed(data, "kms:Decrypt", OTHER_KEY)
    assert not allowed(data, "s3:PutObject", f"arn:aws:s3:::{CUSTOMER}/a1/model.glb")  # writes only with restore
    rest = _policy("iam/cloudformation/vra-deploy-role.yaml", "RestorePolicy",
                   {**base, **BUCKETS, "EnableRestore": "true"})
    assert allowed(rest, "s3:PutObject", f"arn:aws:s3:::{CUSTOMER}/a1/model.glb")
    assert not allowed(rest, "s3:PutObject", "arn:aws:s3:::someone-else/a1")


def test_data_copy_role_templates_grant_the_registered_buckets_per_side():
    from test_iam_and_ci_roles import allowed
    base = {"EnvName": "prod", "PeerEnvName": "test", "BaseStackName": "prod", "PeerAccountId": "222222222222",
            "TrustedPrincipalArns": ["arn:aws:iam::555555555555:role/ci"]}
    src = _policy("iam/cloudformation/vra-data-copy-roles.yaml", "SourcePolicy", {**base, "Side": "source", **BUCKETS})
    assert allowed(src, "s3:GetObject", f"arn:aws:s3:::{CUSTOMER}/k") and allowed(src, "s3:ListBucket",
                                                                                  f"arn:aws:s3:::{CUSTOMER}")
    assert not allowed(src, "s3:PutObject", f"arn:aws:s3:::{CUSTOMER}/k")
    assert allowed(src, "kms:Decrypt", OTHER_KEY, VIA_S3)
    tgt = _policy("iam/cloudformation/vra-data-copy-roles.yaml", "TargetPolicy", {**base, "Side": "target", **BUCKETS})
    assert allowed(tgt, "s3:PutObject", f"arn:aws:s3:::{CUSTOMER}/k") and allowed(tgt, "s3:DeleteObject",
                                                                                  f"arn:aws:s3:::{CUSTOMER}/k")
    assert allowed(tgt, "kms:GenerateDataKey", OTHER_KEY, VIA_S3)
    none = _policy("iam/cloudformation/vra-data-copy-roles.yaml", "TargetPolicy", {**base, "Side": "target"})
    assert not allowed(none, "s3:PutObject", f"arn:aws:s3:::{CUSTOMER}/k")
