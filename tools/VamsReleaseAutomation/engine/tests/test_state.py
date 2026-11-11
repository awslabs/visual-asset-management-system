# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json

import pytest
from moto import mock_aws
import boto3

from vra import runner
from vra.errors import NeedsHuman, VraError
from vra.runner import workflow
from vra.state import DynamoLock, LocalLock, Run

COUNTER = {"a": 0, "b": 0}


@workflow("_test_checkpoint")
def _wf(ctx, run, args):
    def a(sc):
        COUNTER["a"] += 1
        return "A"

    def b(sc):
        COUNTER["b"] += 1
        if COUNTER["b"] == 1:
            raise VraError("transient failure")
        return "B"
    run.step("a", a)
    run.step("b", b)
    return {"ok": True}


def test_steps_checkpoint_and_resume(ctx):
    COUNTER.update(a=0, b=0)
    out = runner.start(ctx, "_test_checkpoint", {})
    assert out.exit_code == 1 and out.run.status == "FAILED"
    st = {s["id"]: s["status"] for s in out.run.state["steps"]}
    assert st == {"a": "done", "b": "failed"}
    assert (out.run.dir / "report.md").exists() and (out.run.dir / "report.json").exists()
    out2 = runner.resume(ctx, out.run.run_id)
    assert out2.exit_code == 0 and out2.run.status == "SUCCEEDED"
    assert COUNTER == {"a": 1, "b": 2}  # a not re-executed


def test_resumed_step_does_not_keep_previous_error(ctx):
    """E2E finding (W7/W10): a smoke step that failed, was resumed and then passed still showed the failed
    attempt's AccessDenied text in the report's Detail column next to a green tick."""
    COUNTER.update(a=0, b=0)
    out = runner.start(ctx, "_test_checkpoint", {})
    assert out.run.find_step("b")["detail"] == "transient failure"
    out2 = runner.resume(ctx, out.run.run_id)
    b = out2.run.find_step("b")
    assert b["status"] == "done" and b["detail"] is None and b["previousDetail"] == "transient failure"
    assert "transient failure" not in (out2.run.dir / "report.md").read_text()


def test_local_lock_is_not_reentrant_by_run_id(tmp_path):
    lock = LocalLock(tmp_path)
    r1 = Run.create(tmp_path, "x", {}, "me", run_id="r1")
    lock.acquire("dev", "r1", token="tok-a")
    lock.acquire("dev", "r1", token="tok-a")  # the owner renews
    with pytest.raises(NeedsHuman, match="another owner token"):
        lock.acquire("dev", "r1", token="tok-b")  # same run id, other process: refused
    Run.create(tmp_path, "x", {}, "me", run_id="r2")
    with pytest.raises(NeedsHuman):
        lock.acquire("dev", "r2", token="tok-c")
    r1.set_status("FAILED")  # holder run ended without releasing (crash before release) and left the env clean
    lock.acquire("dev", "r2", token="tok-c")
    assert lock.holder("dev")["runId"] == "r2"
    assert "tok-c" not in json.dumps(lock.holder("dev"))  # only the hash is shared
    lock.release("dev", "r2", token="tok-c")
    assert lock.holder("dev") is None


def test_dynamo_lock_owner_token_dirty_and_takeover(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with mock_aws():
        c = boto3.client("dynamodb", region_name="us-east-1")
        c.create_table(TableName="vra-locks", KeySchema=[{"AttributeName": "lockId", "KeyType": "HASH"}],
                       AttributeDefinitions=[{"AttributeName": "lockId", "AttributeType": "S"}],
                       BillingMode="PAY_PER_REQUEST")
        now = [1000.0]
        lock = DynamoLock(c, "vra-locks", ttl_seconds=900, clock=lambda: now[0])
        info = lock.acquire("prod", "run-a", token="ta")
        assert info["expiresAt"] == 1900.0
        with pytest.raises(NeedsHuman, match="locked by run run-a"):
            lock.acquire("prod", "run-b", token="tb")
        with pytest.raises(NeedsHuman, match="another owner token"):
            lock.acquire("prod", "run-a", token="other")      # same run id from another host
        assert lock.renew("prod", "ta")["leaseUntil"] is not None
        lock.set_dirty("prod", "ta", "cdk deploy")
        now[0] = 10_000.0                                      # lease long expired, holder gone
        with pytest.raises(NeedsHuman, match="never released by expiry"):
            lock.acquire("prod", "run-b", token="tb")
        took = lock.acquire("prod", "run-a", token="new", takeover=True)
        assert took["takeover"] and took["dirty"] == "cdk deploy"
        assert lock.release("prod", "run-a", token="new")
        assert lock.holder("prod") is None


class _SlowS3:
    """Fake S3 client: every transfer takes a moment; records peak concurrency and the order keys arrive."""

    def __init__(self, delay=0.05):
        import threading
        self.delay, self.lock, self.active, self.peak = delay, threading.Lock(), 0, 0
        self.objects, self.order = {}, []

    def _enter(self):
        import time
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1

    def put_object(self, Bucket, Key, Body, IfNoneMatch=None):
        self._enter()
        with self.lock:
            self.objects[Key] = Body
            self.order.append(Key)

    def get_paginator(self, _name):
        store = self

        class _P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in sorted(store.objects) if k.startswith(Prefix)]}
        return _P()

    def download_file(self, bucket, key, dest):
        self._enter()
        from pathlib import Path
        Path(dest).write_bytes(self.objects[key])


def test_bundle_store_transfers_run_in_parallel_and_the_manifest_lands_last(tmp_path):
    """Live test: the pipeline's bundle store copied a ~15k-file bundle one PutObject at a time (~13 minutes
    per build, the same again for a CI rollback's fetch). Transfers now run on a bounded pool, and the bundle's
    manifest.json is written only after every other file, so a reader never sees a half-copied bundle as complete."""
    from vra.state import StateStore

    top = tmp_path / "home"
    bdir = top / "artifacts" / "cust-v1-1"
    for i in range(40):
        f = bdir / "cdk.out" / "dev" / f"asset.{i:02d}" / "index.js"
        f.parent.mkdir(parents=True)
        f.write_text(f"asset {i}\n")
    (bdir / "manifest.json").write_text('{"buildId": "cust-v1-1"}\n')
    s3 = _SlowS3()
    store = StateStore(s3, "bkt", "vra-bundles", top / "state", top)
    assert store.push_dir(bdir, create_only=True) == []
    assert s3.peak > 1, "uploads ran one at a time"
    assert s3.order[-1] == "vra-bundles/artifacts/cust-v1-1/manifest.json"
    assert len(s3.objects) == 41

    s3.peak = 0
    other = tmp_path / "ci"
    reader = StateStore(s3, "bkt", "vra-bundles", other / "state", other)
    assert reader.pull_prefix("artifacts/cust-v1-1/") == 41
    assert s3.peak > 1, "downloads ran one at a time"
    assert (other / "artifacts" / "cust-v1-1" / "cdk.out" / "dev" / "asset.07" / "index.js").read_text() == "asset 7\n"
