#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""A claim records when it was taken and whether its run succeeded. A pending claim older than the
inner execution's time limit plus a margin is stale and the next triggered run replaces it; a
completed claim keeps collapsing repeat triggers on the same content whatever its age."""
import importlib
import json
import os
from unittest.mock import patch

import pytest

from miris_upload_fakes import FakeS3

pytestmark = pytest.mark.unit

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

AUX = "s3://vams-aux-bucket/pipelines/miris-upload/exec-aaaa1111/"
AUX_BUCKET = "vams-aux-bucket"
STALE_AFTER = 6000


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setenv("ALLOWED_INPUT_FILEEXTENSIONS", ".usd,.usda,.usdc,.usdz")
    monkeypatch.setenv("MIRIS_UPLOAD_CLAIM_STALE_SECONDS", str(STALE_AFTER))
    import mirisUploadGate
    import pipelineEnd
    return importlib.reload(mirisUploadGate), importlib.reload(pipelineEnd)


class Asset:
    """One asset's bucket and claims; runs go through the gate and the end Lambda at `s3.now`."""

    def __init__(self, pipeline):
        self.gate, self.end = pipeline
        self.s3 = FakeS3()
        self.s3.put_file("assets", "a1/scene.usda", "e-scene")
        self.runs = 0

    def claim(self):
        self.runs += 1
        token = f"tok-{self.runs}"
        body = {"assetId": "a1", "databaseId": "db1", "assetBucket": "assets",
                "assetRootS3Key": "a1/", "manual": False,
                "inputOutputS3AssetAuxiliaryFilesPath": AUX, "sfnExternalTaskToken": token}
        with patch.object(self.gate, "s3_client", self.s3), patch.object(self.gate, "sfn_client"), \
             patch.object(self.gate.time, "time", return_value=self.s3.now):
            return token, self.gate.lambda_handler({"body": json.dumps(body)}, None)

    def end_run(self, token, result, status="success"):
        event = {"externalSfnTaskToken": token, "status": status, "assetId": "a1",
                 "databaseId": "db1", "claimFingerprint": result["claimFingerprint"],
                 "inputOutputS3AssetAuxiliaryFilesPath": AUX}
        with patch.object(self.end, "s3_client", self.s3), patch.object(self.end, "sfn"):
            self.end.lambda_handler(event, None)

    def only_claim(self):
        [key] = self.s3.claim_keys(AUX_BUCKET)
        return self.s3.claim(AUX_BUCKET, key)


def test_a_new_claim_is_pending_and_records_when_it_was_taken(pipeline):
    asset = Asset(pipeline)
    token, result = asset.claim()

    assert result["gate"] == "proceed"
    claim = asset.only_claim()
    assert claim["state"] == "pending"
    assert claim["claimedAt"] == asset.s3.now
    assert claim["owner"] == asset.gate.owner_id(token)


def test_a_pending_claim_within_the_window_still_skips_the_next_trigger(pipeline):
    asset = Asset(pipeline)
    asset.claim()
    asset.s3.now += STALE_AFTER

    _, result = asset.claim()

    assert result["gate"] == "skip"


def test_a_stale_pending_claim_is_replaced_and_the_run_proceeds(pipeline):
    asset = Asset(pipeline)
    asset.claim()
    asset.s3.now += STALE_AFTER + 1

    token, result = asset.claim()

    assert result["gate"] == "proceed"
    claim = asset.only_claim()
    assert (claim["owner"], claim["state"], claim["claimedAt"]) == (
        asset.gate.owner_id(token), "pending", asset.s3.now)


def test_a_stale_claim_another_run_replaced_first_is_not_taken_over(pipeline):
    asset = Asset(pipeline)
    asset.claim()
    asset.s3.now += STALE_AFTER + 1
    real_put = asset.s3.put_object

    def replaced_between_read_and_write(**kwargs):
        if kwargs.get("IfMatch"):
            real_put(Bucket=kwargs["Bucket"], Key=kwargs["Key"], Body=b'{"owner": "other"}')
        return real_put(**kwargs)

    asset.s3.put_object = replaced_between_read_and_write
    _, result = asset.claim()

    assert result["gate"] == "skip"
    assert asset.only_claim() == {"owner": "other"}


def test_a_successful_run_marks_its_claim_completed(pipeline):
    asset = Asset(pipeline)
    token, result = asset.claim()
    asset.end_run(token, result)

    claim = asset.only_claim()
    assert (claim["state"], claim["owner"]) == ("completed", asset.gate.owner_id(token))


def test_a_completed_claim_skips_repeat_triggers_whatever_its_age(pipeline):
    asset = Asset(pipeline)
    token, result = asset.claim()
    asset.end_run(token, result)
    asset.s3.now += 10 * STALE_AFTER

    _, again = asset.claim()

    assert again["gate"] == "skip"
    assert asset.only_claim()["state"] == "completed"


def test_success_leaves_a_claim_another_run_holds_unchanged(pipeline):
    asset = Asset(pipeline)
    token, result = asset.claim()
    asset.s3.now += STALE_AFTER + 1
    newer_token, _ = asset.claim()

    asset.end_run(token, result)

    claim = asset.only_claim()
    assert (claim["state"], claim["owner"]) == ("pending", asset.gate.owner_id(newer_token))


def test_a_claim_without_a_recorded_time_ages_from_its_last_write(pipeline):
    asset = Asset(pipeline)
    asset.claim()
    key = asset.s3.claim_keys(AUX_BUCKET)[0]
    written = asset.s3.modified[(AUX_BUCKET, key)]
    asset.s3.objects[(AUX_BUCKET, key)] = (json.dumps({"owner": "earlier"}).encode(), '"legacy"')
    asset.s3.modified[(AUX_BUCKET, key)] = written

    asset.s3.now = written + STALE_AFTER
    assert asset.claim()[1]["gate"] == "skip"
    asset.s3.now = written + STALE_AFTER + 1
    assert asset.claim()[1]["gate"] == "proceed"
