#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""The claim across runs: the gate writes it, and the end Lambda releases it on failure or, on
success, retires the asset's claims on other content. Both run against one in-memory S3."""
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


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setenv("ALLOWED_INPUT_FILEEXTENSIONS", ".usd,.usda,.usdc,.usdz")
    import mirisUploadGate
    import pipelineEnd
    return importlib.reload(mirisUploadGate), importlib.reload(pipelineEnd)


class Asset:
    """One VAMS asset's bucket and its claims, with a run that goes through gate and end Lambda."""

    def __init__(self, pipeline, database_id="db1", s3=None):
        self.gate, self.end = pipeline
        self.database_id = database_id
        self.bucket = f"assets-{database_id}"
        self.s3 = s3 or FakeS3()
        self.runs = 0

    def set_layer(self, rel, etag):
        self.s3.put_file(self.bucket, f"a1/{rel}", etag)

    def claim(self, manual=False):
        """Run the gate as a new execution; returns (token, gate result)."""
        self.runs += 1
        token = f"tok-{self.database_id}-{self.runs}"
        body = {"assetId": "a1", "databaseId": self.database_id, "assetBucket": self.bucket,
                "assetRootS3Key": "a1/", "manual": manual,
                "inputOutputS3AssetAuxiliaryFilesPath": AUX, "sfnExternalTaskToken": token}
        with patch.object(self.gate, "s3_client", self.s3), patch.object(self.gate, "sfn_client"):
            return token, self.gate.lambda_handler({"body": json.dumps(body)}, None)

    def end_run(self, token, result, status="success"):
        event = {"externalSfnTaskToken": token, "status": status, "assetId": "a1",
                 "databaseId": self.database_id, "claimFingerprint": result["claimFingerprint"],
                 "inputOutputS3AssetAuxiliaryFilesPath": AUX}
        with patch.object(self.end, "s3_client", self.s3), patch.object(self.end, "sfn"):
            self.end.lambda_handler(event, None)

    def upload(self, manual=False):
        """A complete run; returns the gate decision."""
        token, result = self.claim(manual)
        if result["gate"] == "proceed":
            self.end_run(token, result)
        return result["gate"]

    def claims(self):
        return self.s3.claim_keys(AUX_BUCKET)


def test_a_reverted_version_uploads_again(pipeline):
    asset = Asset(pipeline)
    asset.set_layer("scene.usda", "e-A")
    assert asset.upload() == "proceed"
    asset.set_layer("scene.usda", "e-B")
    assert asset.upload() == "proceed"

    # A VAMS revert copies the old version back, with its old ETag.
    asset.set_layer("scene.usda", "e-A")
    assert asset.upload() == "proceed"


def test_a_successful_upload_leaves_only_its_own_claim(pipeline):
    asset = Asset(pipeline)
    asset.set_layer("scene.usda", "e-A")
    asset.upload()
    asset.set_layer("scene.usda", "e-B")
    token, result = asset.claim()
    asset.end_run(token, result)

    assert asset.claims() == [f"locks/miris-upload/db1/a1/{result['claimFingerprint']}.claim"]


def test_a_successful_manual_run_leaves_no_claim(pipeline):
    asset = Asset(pipeline)
    asset.set_layer("scene.usda", "e-A")
    asset.upload()
    assert asset.upload(manual=True) == "proceed"

    assert asset.claims() == []
    # The next trigger on the same content uploads again: a duplicate, never a stale skip.
    assert asset.upload() == "proceed"


def test_another_assets_claims_are_left_alone(pipeline):
    asset = Asset(pipeline)
    asset.s3.put_object(Bucket=AUX_BUCKET, Key="locks/miris-upload/db1/a10/x.claim", Body=b"{}")
    asset.set_layer("scene.usda", "e-A")
    asset.upload()

    assert "locks/miris-upload/db1/a10/x.claim" in asset.claims()


def test_retiring_an_in_flight_runs_claim_costs_it_only_its_dedup(pipeline):
    """Run X (content A) is still uploading when run Y (content B) succeeds and retires X's claim."""
    asset = Asset(pipeline)
    asset.set_layer("scene.usda", "e-A")
    token_x, claim_x = asset.claim()
    asset.set_layer("scene.usda", "e-B")
    token_y, claim_y = asset.claim()
    asset.end_run(token_y, claim_y)
    assert asset.claims() == [f"locks/miris-upload/db1/a1/{claim_y['claimFingerprint']}.claim"]

    # X fails: its release finds no claim and touches nothing else.
    asset.end_run(token_x, claim_x, status="failed")
    assert asset.claims() == [f"locks/miris-upload/db1/a1/{claim_y['claimFingerprint']}.claim"]


def test_two_contents_succeeding_concurrently_leave_no_stale_claim(pipeline):
    """Each run retires the other's claim, so none is left and the next trigger uploads again."""
    asset = Asset(pipeline)
    asset.set_layer("scene.usda", "e-A")
    token_x, claim_x = asset.claim()
    asset.set_layer("scene.usda", "e-B")
    token_y, claim_y = asset.claim()

    asset.end_run(token_x, claim_x)
    asset.end_run(token_y, claim_y)

    assert asset.claims() == []
    assert asset.upload() == "proceed"


def test_a_listing_failure_does_not_fail_the_successful_run(pipeline):
    asset = Asset(pipeline)
    asset.set_layer("scene.usda", "e-A")
    token, result = asset.claim()

    def broken(name):
        raise RuntimeError("AccessDenied")

    asset.s3.get_paginator = broken
    with patch.object(asset.end, "s3_client", asset.s3), patch.object(asset.end, "sfn") as sfn:
        response = asset.end.lambda_handler({
            "externalSfnTaskToken": token, "status": "success", "assetId": "a1", "databaseId": "db1",
            "claimFingerprint": result["claimFingerprint"],
            "inputOutputS3AssetAuxiliaryFilesPath": AUX}, None)

    assert response["statusCode"] == 200
    sfn.send_task_success.assert_called_once()


def test_the_same_asset_id_in_two_databases_claims_and_retires_separately(pipeline):
    first = Asset(pipeline, "db1")
    second = Asset(pipeline, "db2", s3=first.s3)
    first.set_layer("scene.usda", "e-A")
    second.set_layer("scene.usda", "e-A")

    token, result = first.claim()
    assert result["gate"] == "proceed"
    assert second.upload() == "proceed"
    first.end_run(token, result)

    assert sorted(first.claims()) == [
        f"locks/miris-upload/db1/a1/{result['claimFingerprint']}.claim",
        f"locks/miris-upload/db2/a1/{result['claimFingerprint']}.claim",
    ]
    # db1's success did not retire db2's claim, so db2's next trigger is still skipped.
    assert second.upload() == "skip"
