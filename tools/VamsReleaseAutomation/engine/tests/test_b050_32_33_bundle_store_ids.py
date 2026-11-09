# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""E2E-050 Phase B10 regressions on the bundle store (VRA_BUNDLE_STORE, the pipeline's CI setup).

B-050-32: execution b5788c18 rebuilt commit 82ce189 after `vra iam apply` of prod-3 (R50-2: a bundle synthesized
under other IAM settings is never reused). The CI job holds no earlier bundle, so the build took the commit's id
cust-v2.6.6-10 again and synthesized it; the create-only upload then refused it ("already exists in the bundle store
with different content"): no rebuild of an already-built commit could ever pass in CI. Now a still-valid stored
bundle of the commit is reused (pulled, not synthesized) and an id the store holds with other content is superseded
by the next free id.

B-050-33: that refused upload still added the build's new files (cdk.out/prod/performance-counters.json) under the
immutable cust-v2.6.6-10, so its prod target no longer passes `verify_bundle`. A different stored manifest is now
refused before anything is uploaded.
"""

from __future__ import annotations

import json

import boto3
import moto
import pytest

from vra import build as build_mod

from conftest import FakeAws
from test_build_deploy import FakeCfn, _build

BUCKET = "vra-pipeline-artifacts"
PREFIX = "vra-bundles"


@pytest.fixture
def store_ctx(ctx, monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        monkeypatch.setenv(build_mod.BUNDLE_STORE_ENV, f"s3://{BUCKET}/{PREFIX}")
        ctx.aws = FakeAws(ctx.ledger, {"cloudformation": FakeCfn()}, moto=True)
        yield ctx


def _keys(build_id):
    s3 = boto3.client("s3", region_name="us-east-1")
    out = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=f"{PREFIX}/artifacts/{build_id}/"):
        out += [(o["Key"], o["ETag"]) for o in page.get("Contents", [])]
    return sorted(out)


def _ci_job_forgets(ctx, build_id):
    """A new CodeBuild job: only the shared home (runs) travels, the bundle directory does not."""
    import shutil
    shutil.rmtree(build_mod.build_dir(ctx, build_id))


def _store_manifest(build_id):
    s3 = boto3.client("s3", region_name="us-east-1")
    return json.loads(s3.get_object(Bucket=BUCKET, Key=f"{PREFIX}/artifacts/{build_id}/manifest.json")["Body"].read())


def _put_store_manifest(build_id, doc):
    boto3.client("s3", region_name="us-east-1").put_object(
        Bucket=BUCKET, Key=f"{PREFIX}/artifacts/{build_id}/manifest.json", Body=json.dumps(doc).encode())


def _synths(calls):
    return [c for c in calls if "synth" in c["argv"]]


def test_ci_rebuild_of_a_built_commit_reuses_the_stored_bundle(store_ctx, calls):
    first = _build(store_ctx)
    assert first.exit_code == 0, first.message
    b1 = first.run.state["result"]["buildId"]
    _ci_job_forgets(store_ctx, b1)
    n = len(_synths(calls))
    again = _build(store_ctx)
    assert again.exit_code == 0, again.message
    assert again.run.state["result"]["buildId"] == b1
    assert len(_synths(calls)) == n                                  # pulled from the store, not synthesized
    assert again.run.find_step("build.reuse")["status"] == "skipped"
    m = build_mod.load_manifest(store_ctx, b1)
    for t in m["targets"]:
        build_mod.verify_bundle(store_ctx, m, t)                     # the whole bundle came back


def test_ci_rebuild_under_other_settings_gets_the_next_free_id(store_ctx, calls):
    first = _build(store_ctx)
    assert first.exit_code == 0, first.message
    b1 = first.run.state["result"]["buildId"]
    _ci_job_forgets(store_ctx, b1)
    # the stored bundle was synthesized under other settings (here: another core patch set; in the E2E: prod-2)
    doc = _store_manifest(b1)
    doc["corePatches"] = [{"file": "core-patches/other.patch", "sha256": "0" * 64, "status": "applied"}]
    _put_store_manifest(b1, doc)
    before = _keys(b1)
    again = _build(store_ctx)
    assert again.exit_code == 0, again.message
    b2 = again.run.state["result"]["buildId"]
    assert b2 != b1 and b2 == b1.rsplit("-", 1)[0] + "-2"
    assert again.run.state["result"]["commit"] == first.run.state["result"]["commit"]
    assert any(b1 in n_ and b2 in n_ for n_ in again.run.state.get("notes", []))
    assert _keys(b1) == before                                       # the stored b1 is untouched
    assert _store_manifest(b2)["buildId"] == b2
    # a third CI job reuses b2 (newest valid bundle of the commit), it does not supersede it
    _ci_job_forgets(store_ctx, b2)
    n = len(_synths(calls))
    third = _build(store_ctx)
    assert third.exit_code == 0 and third.run.state["result"]["buildId"] == b2 and len(_synths(calls)) == n


def test_a_refused_upload_writes_nothing_under_the_stored_id(store_ctx, calls, monkeypatch):
    first = _build(store_ctx)
    assert first.exit_code == 0, first.message
    b1 = first.run.state["result"]["buildId"]
    _ci_job_forgets(store_ctx, b1)
    doc = _store_manifest(b1)
    doc["corePatches"] = [{"file": "core-patches/other.patch", "sha256": "0" * 64, "status": "applied"}]
    _put_store_manifest(b1, doc)
    # one file of the stored bundle is "new" to it from this build's point of view (performance-counters.json)
    s3 = boto3.client("s3", region_name="us-east-1")
    victim = next(k for k, _e in _keys(b1) if "/cdk.out/" in k and not k.endswith("manifest.json"))
    s3.delete_object(Bucket=BUCKET, Key=victim)
    before = _keys(b1)
    # a race: another job's resolve gave this build the same id (forced here; the id pick is B-050-32's part)
    monkeypatch.setattr(build_mod, "pick_store_build_id",
                        lambda ctx, store, mine, *a, **k: {"buildId": mine[0], "superseded": []}, raising=False)
    out = _build(store_ctx)
    assert out.exit_code == 20, out.message
    assert "already exists in the bundle store" in out.message
    assert _keys(b1) == before                                       # nothing added, nothing replaced


def test_store_manifest_tells_free_partial_and_held_ids_apart(store_ctx):
    store = build_mod.bundle_store(store_ctx)
    assert build_mod.store_manifest(store, "cust-v9-1") is None
    boto3.client("s3", region_name="us-east-1").put_object(Bucket=BUCKET, Key=f"{PREFIX}/artifacts/cust-v9-1/x",
                                                           Body=b"x")
    assert build_mod.store_manifest(store, "cust-v9-1") == {}
    _put_store_manifest("cust-v9-1", {"buildId": "cust-v9-1"})
    assert build_mod.store_manifest(store, "cust-v9-1") == {"buildId": "cust-v9-1"}
