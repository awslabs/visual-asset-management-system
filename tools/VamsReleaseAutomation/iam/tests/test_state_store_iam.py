# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""State store IAM (stateStore.type s3) against what the engine actually sends (offline).

- The bucket policy of vra-state-store.yaml enforces S3 conditional writes: history records and bundles
  create-only (If-None-Match), run state compare-before-write (If-Match / If-None-Match), and denies deleting
  history and approval claims. The engine's own writes (StateStore, S3History, S3Consumed, bundles) pass it;
  an unconditional overwrite does not.
- The lock table's resource policy admits exactly the engine's lock items (env#<env>, env#watch, run#<runId>)
  and attributes (captured from DynamoLock acquire / renew / dirty / hold / takeover / release), and denies
  UpdateItem.
- The iam/actions manifests carry one identical state-store block (no deletes, no UpdateItem, create-only
  history and bundles, lock keys env#* / run#*) and the approval-signature verify statement where approvals
  are checked.
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest
import yaml

IAM = Path(__file__).resolve().parents[1]
PKG = IAM.parent
_spec = importlib.util.spec_from_file_location("cfn_eval", IAM / "tests" / "cfn_eval.py")
cfn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfn)

BUCKET = "vra-state-111111111111-us-east-1"
BUCKET_ARN = f"arn:aws:s3:::{BUCKET}"
TABLE = "arn:aws:dynamodb:us-east-1:111111111111:table/vra-locks"
STORE = cfn.load_cfn(IAM / "cloudformation" / "vra-state-store.yaml")
PARAMS = {"AWS::Partition": "aws", "AWS::Region": "us-east-1", "AWS::AccountId": "111111111111",
          "HistoryAdminPrincipalArns": [""], "LockAdminPrincipalArns": [""], "LockTableName": "vra-locks"}


def bucket_policy(protect: bool = True) -> list[dict]:
    doc = STORE["Resources"]["StateBucketPolicy"]["Properties"]["PolicyDocument"]
    return cfn.render(doc, PARAMS, {"ProtectHistoryOn": protect}, {"StateBucket": BUCKET_ARN})["Statement"]


def lock_policy() -> list[dict]:
    node = STORE["Resources"]["LockTable"]["Properties"]["ResourcePolicy"]
    return cfn.render(node, PARAMS, {"ProtectLocksOn": True})["PolicyDocument"]["Statement"]


def _put_ctx(kw: dict) -> dict:
    ctx = {"s3:ObjectCreationOperation": "true"}
    if "IfNoneMatch" in kw:
        ctx["s3:if-none-match"] = kw["IfNoneMatch"]
    if "IfMatch" in kw:
        ctx["s3:if-match"] = kw["IfMatch"]
    return ctx


# ------------------------------------------------------------------ bucket policy decisions
def test_bucket_policy_requires_conditional_writes():
    S = bucket_policy()
    hist = f"{BUCKET_ARN}/vra/history/prod/000000000001-r1.json"
    bundle = f"{BUCKET_ARN}/vra/artifacts/b1/manifest.json"
    state = f"{BUCKET_ARN}/vra/runs/r1/state.json"
    log = f"{BUCKET_ARN}/vra/runs/r1/logs/deploy.log"
    plain = {"s3:ObjectCreationOperation": "true"}
    for key in (hist, bundle):
        assert cfn.denied(S, "s3:PutObject", key, plain), key
        assert not cfn.denied(S, "s3:PutObject", key, {**plain, "s3:if-none-match": "*"}), key
        assert cfn.denied(S, "s3:PutObject", key, {**plain, "s3:if-match": '"etag"'}), "overwrite of " + key
    assert cfn.denied(S, "s3:PutObject", state, plain)
    assert not cfn.denied(S, "s3:PutObject", state, {**plain, "s3:if-match": '"etag"'})
    assert not cfn.denied(S, "s3:PutObject", state, {**plain, "s3:if-none-match": "*"})
    assert not cfn.denied(S, "s3:PutObject", log, plain), "run logs are uploaded as they are"
    # parts of a multipart upload carry no conditional header
    assert not cfn.denied(S, "s3:PutObject", bundle, {"s3:ObjectCreationOperation": "false"})


def test_bucket_policy_lets_header_less_writes_use_the_default_key():
    """vra never names a KMS key on PutObject (bucket default encryption = the state key). The KMS statement denies
    only a request that names another key; StringNotEqualsIfExists alone would deny every vra write."""
    key = "arn:aws:kms:us-east-1:111111111111:key/state"
    doc = STORE["Resources"]["StateBucketPolicy"]["Properties"]["PolicyDocument"]
    S = cfn.render(doc, PARAMS, {"ProtectHistoryOn": True}, {"StateBucket": BUCKET_ARN, "StateKey": key})["Statement"]
    log = f"{BUCKET_ARN}/vra/runs/r1/logs/deploy.log"
    hdr = "s3:x-amz-server-side-encryption-aws-kms-key-id"
    assert not cfn.denied(S, "s3:PutObject", log, {})
    assert not cfn.denied(S, "s3:PutObject", log, {hdr: key})
    assert cfn.denied(S, "s3:PutObject", log, {hdr: "arn:aws:kms:us-east-1:111111111111:key/other"})


def test_bucket_policy_denies_deleting_history_and_approval_claims():
    S = bucket_policy()
    for key in ("vra/history/prod/000000000001-r1.json", "vra/approvals-consumed/prod/abc.json"):
        for a in ("s3:DeleteObject", "s3:DeleteObjectVersion"):
            assert cfn.denied(S, a, f"{BUCKET_ARN}/{key}", {"aws:PrincipalArn": "arn:aws:iam::1:role/x"}), (a, key)
    claims = next(s for s in S if s.get("Sid") == "ProtectApprovalClaims")
    assert claims["Resource"] == [f"{BUCKET_ARN}/*/approvals-consumed/*"]
    assert not any(s.get("Sid") == "ProtectApprovalClaims" for s in bucket_policy(protect=False))


class _FakeS3:
    """Records put_object; answers 412 for a create-only write to an existing key, like S3."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.puts: list[dict] = []

    def put_object(self, **kw):
        self.puts.append(kw)
        if kw.get("IfNoneMatch") == "*" and kw["Key"] in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[kw["Key"]] = kw["Body"] if isinstance(kw["Body"], bytes) else kw["Body"].read()
        return {"ETag": '"e%d"' % len(self.puts)}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key]), "ETag": '"e"'}

    def get_paginator(self, name):
        objs = self.objects

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in sorted(objs) if k.startswith(Prefix)]}
        return P()


def test_the_engines_state_writes_pass_the_bucket_policy(tmp_path):
    from vra import state
    s3 = _FakeS3()
    store = state.StateStore(s3, BUCKET, "vra", tmp_path / "state", tmp_path)
    store.put_run_state("r1", {"rev": 1}, None, create=True)              # Run.create
    etag = s3.puts[-1] and '"e1"'
    store.put_run_state("r1", {"rev": 2}, etag)                           # a save with the stored ETag
    state.S3History(store).append("prod", {"runId": "r1", "action": "deploy"})
    state.S3Consumed(store).claim("prod", "a" * 64, {"runId": "r1"})
    bundle = tmp_path / "artifacts" / "b1"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text("{}")
    assert store.push_dir(bundle, create_only=True) == []                # build bundles are create-only
    # IT packages and responses create-only, the apply record replaced, pipeline plans create-only
    pkg = tmp_path / "state" / "iam" / "prod" / "1"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text("{}")
    (pkg.parent / "responses").mkdir()
    (pkg.parent / "responses" / "1-abc.yaml").write_text("package: prod-1\n")
    (pkg.parent / "applied.json").write_text("{}")
    assert store.push_dir(pkg, create_only=True) == []
    assert store.push_file(pkg.parent / "responses" / "1-abc.yaml", create_only=True)
    s3.put_object(Bucket=BUCKET, Key="vra/iam/prod/applied.json", Body=b"{}")   # StateStore.push_file upload_file
    plans = tmp_path / "state" / "pipeline" / "plans"
    plans.mkdir(parents=True)
    (plans / "pl-1.json").write_text("{}")
    assert store.push_file(plans / "pl-1.json", create_only=True)
    S = bucket_policy()
    for k in ("vra/iam/prod/1/package.json", "vra/iam/prod/responses/1-abc.yaml", "vra/pipeline/plans/pl-1.json"):
        assert cfn.denied(S, "s3:PutObject", f"{BUCKET_ARN}/{k}", {"s3:ObjectCreationOperation": "true"}), k
    for kw in s3.puts:
        assert not cfn.denied(S, "s3:PutObject", f"{BUCKET_ARN}/{kw['Key']}", _put_ctx(kw)), kw["Key"]
    keys = [kw["Key"] for kw in s3.puts]
    assert "vra/history/prod/000000000001-r1.json" in keys and "vra/artifacts/b1/manifest.json" in keys
    # what the policy is for: an unconditional rewrite of a history record or a bundle is refused
    for k in ("vra/history/prod/000000000001-r1.json", "vra/artifacts/b1/manifest.json", "vra/runs/r1/state.json"):
        assert cfn.denied(S, "s3:PutObject", f"{BUCKET_ARN}/{k}", {"s3:ObjectCreationOperation": "true"}), k


# ------------------------------------------------------------------ lock table
class _FakeDynamo:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []

    def get_item(self, **kw):
        self.calls.append(("GetItem", kw))
        it = self.items.get(kw["Key"]["lockId"]["S"])
        return {"Item": it} if it else {}

    def put_item(self, **kw):
        self.calls.append(("PutItem", kw))
        self.items[kw["Item"]["lockId"]["S"]] = kw["Item"]
        return {}

    def delete_item(self, **kw):
        self.calls.append(("DeleteItem", kw))
        self.items.pop(kw["Key"]["lockId"]["S"], None)
        return {}


def _exercise_locks(clock):
    from vra import state
    db = _FakeDynamo()
    lock = state.DynamoLock(db, "vra-locks", ttl_seconds=60, clock=lambda: clock[0])
    a = lock.acquire("prod", "r1", env="prod")
    lock.renew("prod", a["token"])
    lock.set_dirty("prod", a["token"], "cdk deploy started")
    lock.hold("prod", "r1", "tests failed", token=a["token"])
    w = lock.acquire("watch", "r2")
    lock.release("watch", "r2", token=w["token"])
    lease = lock.acquire("run-r3", "r3")
    clock[0] += 3600                                     # the lease expired: r3 resumed elsewhere takes it over
    lock.acquire("run-r3", "r3", takeover=True)
    lock.release("run-r3", "r3", token=lease["token"])
    lock.release("prod", "r1", force=True)                # vra unlock
    return db


def test_lock_table_policy_admits_exactly_the_engines_lock_items():
    db = _exercise_locks([1_000_000.0])
    S = lock_policy()
    seen_attrs: set[str] = set()
    for action, kw in db.calls:
        key = (kw.get("Item") or kw.get("Key"))["lockId"]["S"]
        ctx = {"dynamodb:LeadingKeys": [key], "aws:PrincipalArn": "arn:aws:iam::1:role/vra-codebuild-x"}
        if action == "PutItem":
            ctx["dynamodb:Attributes"] = sorted(kw["Item"])
            seen_attrs |= set(kw["Item"])
        assert not cfn.denied(S, f"dynamodb:{action}", TABLE, ctx), (action, key, ctx.get("dynamodb:Attributes"))
    assert {k for _, kw in db.calls for k in [(kw.get("Item") or kw.get("Key"))["lockId"]["S"]]} >= {
        "env#prod", "env#watch", "run#r3"}
    allow = next(s for s in S if s["Sid"] == "OnlyLockAttributes")
    listed = set(allow["Condition"]["ForAnyValue:StringNotEquals"]["dynamodb:Attributes"])
    assert seen_attrs <= listed, f"engine writes attributes the policy denies: {sorted(seen_attrs - listed)}"
    assert {"ownerHash", "leaseUntil", "heartbeatAt", "dirty", "held", "ver", "takeovers", "expiresAt"} <= seen_attrs


def test_lock_table_policy_denies_other_items_attributes_and_update_item():
    S = lock_policy()
    who = {"aws:PrincipalArn": "arn:aws:iam::1:role/someone"}
    assert cfn.denied(S, "dynamodb:PutItem", TABLE, {**who, "dynamodb:LeadingKeys": ["config#x"],
                                                      "dynamodb:Attributes": ["lockId"]})
    assert cfn.denied(S, "dynamodb:PutItem", TABLE, {**who, "dynamodb:LeadingKeys": ["env#prod"],
                                                      "dynamodb:Attributes": ["lockId", "payload"]})
    for a in ("dynamodb:UpdateItem", "dynamodb:BatchWriteItem", "dynamodb:PartiQLUpdate"):
        assert cfn.denied(S, a, TABLE, {**who, "dynamodb:LeadingKeys": ["env#prod"]}), a
    ttl = STORE["Resources"]["LockTable"]["Properties"]["TimeToLiveSpecification"]
    assert ttl == {"AttributeName": "expiresAt", "Enabled": True}


def test_lock_ids_follow_the_engine():
    from vra import state
    assert state.DynamoLock._id("prod") == "env#prod"
    assert state.DynamoLock._id("watch") == "env#watch"
    assert state.DynamoLock._id("run-r1") == "run#r1"


# ------------------------------------------------------------------ manifests
MANIFESTS = {p.stem: yaml.safe_load(p.read_text()) for p in sorted((IAM / "actions").glob("*.yaml"))}
STATE_SIDS = ["ToolsStateStoreRuns", "ToolsStateStoreHistoryRead", "ToolsStateStoreCreateOnly",
              "ToolsStateStoreClaims", "ToolsStateStoreRecordsRead", "ToolsStateStoreRecords", "ToolsStateStoreList",
              "ToolsStateStoreLock", "ToolsStateStoreKey"]
# workflows whose runs check approval records (gated deploys, human:<action> records of the env)
VERIFYING = {"build", "deploy", "rollback", "restore", "migrate", "data-copy-source", "data-copy-target",
             "pipeline"}


def _sid(m: dict, sid: str) -> dict:
    return next(s for s in m["statements"] if s.get("sid") == sid)


@pytest.mark.parametrize("wf", sorted(MANIFESTS))
def test_every_manifest_carries_the_same_state_store_block(wf):
    m = MANIFESTS[wf]
    ref = MANIFESTS["deploy"]
    for sid in STATE_SIDS:
        assert _sid(m, sid) == _sid(ref, sid), (wf, sid)
    tools = [s for s in m["statements"] if str(s.get("sid", "")).startswith("ToolsStateStore")]
    assert [s["sid"] for s in tools] == STATE_SIDS
    for s in tools:
        assert not any(a.startswith("s3:Delete") or a == "dynamodb:UpdateItem" for a in s["actions"]), s["sid"]
    assert ("ToolsVerifyApprovalSignature" in {s.get("sid") for s in m["statements"]}) == (wf in VERIFYING)


def test_state_store_block_is_create_only_and_scoped_to_lock_items():
    m = MANIFESTS["deploy"]
    create = _sid(m, "ToolsStateStoreCreateOnly")
    assert create["actions"] == ["s3:PutObject"]
    assert create["condition"] == {"Null": {"s3:if-none-match": "false"}}
    assert sorted(r.split("/*/")[1] for r in create["resources"]) == ["artifacts/*", "history/*", "iam/*",
                                                                      "pipeline/*"]
    # the IT apply record is the only unconditional write under iam/
    rec = _sid(m, "ToolsStateStoreRecords")
    assert [r for r in rec["resources"] if "/iam/" in r] == ["arn:${Partition}:s3:::vra-state-*/*/iam/*/applied.json"]
    # no unconditional PutObject reaches history or bundles
    for s in m["statements"]:
        if "s3:PutObject" in s["actions"] and s["sid"] != "ToolsStateStoreCreateOnly":
            assert not any("/history/" in r or "/artifacts/" in r for r in s.get("resources", [])), s["sid"]
    lock = _sid(m, "ToolsStateStoreLock")
    assert lock["condition"] == {"ForAllValues:StringLike": {"dynamodb:LeadingKeys": ["env#*", "run#*"]}}
    verify = _sid(m, "ToolsVerifyApprovalSignature")
    assert verify["actions"] == ["kms:Verify", "kms:DescribeKey"] and verify["optional"] is True


def test_engine_copies_and_schema_accept_the_manifests():
    import jsonschema
    schema = json.loads((PKG / "engine/vra/data/iam-actions.schema.json").read_text())
    for p in sorted((IAM / "actions").glob("*.yaml")):
        jsonschema.validate(yaml.safe_load(p.read_text()), schema)
        assert (PKG / "engine/vra/data/iam-actions" / p.name).read_bytes() == p.read_bytes(), p.name
