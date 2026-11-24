#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from miris_upload_fakes import FakeS3

pytestmark = pytest.mark.unit

# This module's boto3 clients are constructed at import time from AWS_REGION. Set it
# defensively so this test file does not depend on the ambient shell environment.
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

AUX_BUCKET = "vams-aux-bucket"


@pytest.fixture
def gate(monkeypatch):
    import importlib
    import mirisUploadGate
    monkeypatch.setenv("ALLOWED_INPUT_FILEEXTENSIONS", ".usd,.usda,.usdc,.usdz")
    return importlib.reload(mirisUploadGate)


# The real workflow passes `auxBucket + auxTempPrefix`, where the temp prefix is
# `pipelines/{pipelineName}/{executionId}/`, unique per execution. The fixture mirrors that
# shape so a key derivation that folded the prefix in would be visible here.
def _aux_path(execution_id="exec-aaaa1111"):
    return f"s3://{AUX_BUCKET}/pipelines/miris-upload/{execution_id}/"


def _event(**overrides):
    body = {
        "databaseId": "db1",
        "assetId": "a1",
        "assetBucket": "assets",
        "assetRootS3Key": "a1/",
        "manual": False,
        "inputOutputS3AssetAuxiliaryFilesPath": _aux_path(),
        "sfnExternalTaskToken": "tok-1",
    }
    body.update(overrides)
    return {"body": json.dumps(body)}


def _asset_bucket(root="a1/", **files):
    """A fake S3 holding the asset's files ({relative path: etag}) under `root`."""
    s3 = FakeS3()
    for rel, etag in (files or {"scene.usda": "e-scene", "geo.usd": "e-geo"}).items():
        s3.put_file("assets", f"{root}{rel}", etag)
    return s3


def _run(gate, s3, token="tok-1", **overrides):
    with patch.object(gate, "s3_client", s3), patch.object(gate, "sfn_client") as sfn:
        result = gate.lambda_handler(_event(sfnExternalTaskToken=token, **overrides), None)
    return result, sfn


# --- content claim ----------------------------------------------------------------------------


def test_first_claim_proceeds_with_a_content_keyed_claim(gate):
    s3 = _asset_bucket()
    result, _ = _run(gate, s3)

    assert result["gate"] == "proceed"
    assert len(result["claimFingerprint"]) == 64
    assert s3.claim_keys(AUX_BUCKET) == [f"locks/miris-upload/db1/a1/{result['claimFingerprint']}.claim"]


def test_the_same_content_triggered_twice_uploads_once(gate):
    s3 = _asset_bucket()
    first, _ = _run(gate, s3, token="tok-1")
    # The aux path is execution-scoped; if it reached the claim key nothing would be deduplicated.
    second, sfn = _run(gate, s3, token="tok-2",
                       inputOutputS3AssetAuxiliaryFilesPath=_aux_path("exec-2222"))

    assert first["gate"] == "proceed"
    assert second["gate"] == "skip"
    assert len(s3.claim_keys(AUX_BUCKET)) == 1
    output = json.loads(sfn.send_task_success.call_args.kwargs["output"])
    assert output["status"] == "skipped"


@pytest.mark.parametrize("path, etag, uploads", [
    ("a1/geo.usd", "e-geo-2", True),
    ("a1/mat.usdc", "e-mat", True),
    ("a1/textures/wood.png", "e-wood-2", False),
], ids=["changed-layer", "added-layer", "changed-non-trigger-file"])
def test_only_a_trigger_file_change_uploads_again(gate, path, etag, uploads):
    s3 = _asset_bucket()
    s3.put_file("assets", "a1/textures/wood.png", "e-wood")
    first, _ = _run(gate, s3, token="tok-1")
    s3.put_file("assets", path, etag)
    second, _ = _run(gate, s3, token="tok-2")

    assert second["gate"] == ("proceed" if uploads else "skip")
    if uploads:
        assert second["claimFingerprint"] != first["claimFingerprint"]


def test_the_listing_honors_a_prefixed_asset_root(gate):
    """A bucket registered with baseAssetsPrefix "vams/data/" roots the asset there."""
    s3 = _asset_bucket(root="vams/data/a1/")
    result, _ = _run(gate, s3, assetRootS3Key="vams/data/a1/")
    bare, _ = _run(gate, _asset_bucket())

    assert result["gate"] == "proceed"
    assert s3.list_calls == [("assets", "vams/data/a1/")]
    # The fingerprint is keyed on asset-relative paths.
    assert result["claimFingerprint"] == bare["claimFingerprint"]


def test_only_configured_extensions_enter_the_fingerprint(gate, monkeypatch):
    monkeypatch.setattr(gate, "ALLOWED_INPUT_FILEEXTENSIONS", ".usdz")
    s3 = _asset_bucket(**{"model.usdz": "e1", "scene.usda": "e2"})
    _run(gate, s3, token="tok-1")
    s3.put_file("assets", "a1/scene.usda", "e2-changed")
    second, _ = _run(gate, s3, token="tok-2")

    assert second["gate"] == "skip"


def test_claim_records_a_digest_of_the_task_token_not_the_token(gate):
    s3 = _asset_bucket()
    _run(gate, s3)

    body = s3.objects[(AUX_BUCKET, s3.claim_keys(AUX_BUCKET)[0])][0].decode("utf-8")
    assert json.loads(body)["owner"] == gate.owner_id("tok-1")
    assert "tok-1" not in body


# --- manual runs ------------------------------------------------------------------------------


def test_a_manual_run_uploads_unchanged_content_without_a_claim(gate):
    s3 = _asset_bucket()
    _run(gate, s3, token="tok-1")
    claims_before = s3.claim_keys(AUX_BUCKET)

    result, sfn = _run(gate, s3, token="tok-2", manual=True)

    assert result == {"gate": "proceed", "claimFingerprint": ""}
    assert s3.claim_keys(AUX_BUCKET) == claims_before
    sfn.send_task_success.assert_not_called()


def test_only_a_boolean_true_marks_a_manual_run(gate):
    s3 = _asset_bucket()
    _run(gate, s3, token="tok-1")
    result, _ = _run(gate, s3, token="tok-2", manual="true")

    assert result["gate"] == "skip"


# --- no claim to record -----------------------------------------------------------------------


@pytest.mark.parametrize("files, overrides", [
    (None, {"inputOutputS3AssetAuxiliaryFilesPath": ""}),
    (None, {"assetRootS3Key": ""}),
    (None, {"assetBucket": ""}),
    ({"notes.txt": "e1"}, {}),
], ids=["no-aux-path", "no-asset-root", "no-asset-bucket", "no-trigger-files"])
def test_a_run_with_no_claim_to_record_proceeds_without_one(gate, files, overrides):
    s3 = _asset_bucket(**(files or {}))
    result, _ = _run(gate, s3, **overrides)

    assert result == {"gate": "proceed", "claimFingerprint": ""}
    assert s3.claim_keys(AUX_BUCKET) == []


# --- claim ownership --------------------------------------------------------------------------


def _claimed(s3, existing_claim):
    """Make the conditional put fail, with `existing_claim` (a dict, raw bytes, or the exception the
    read raises) already stored."""
    s3.exceptions.ClientError = ClientError
    s3.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "a1/scene.usda", "ETag": '"e1"'}]}]
    s3.put_object.side_effect = ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
    if isinstance(existing_claim, Exception):
        s3.get_object.side_effect = existing_claim
    else:
        body = MagicMock()
        body.read.return_value = (existing_claim if isinstance(existing_claim, bytes)
                                  else json.dumps(existing_claim).encode("utf-8"))
        s3.get_object.return_value = {"Body": body}


def test_a_retried_put_that_meets_its_own_claim_proceeds(gate):
    """The first put landed but its response was lost; the retry meets our own claim."""
    with patch.object(gate, "s3_client") as s3, patch.object(gate, "sfn_client") as sfn:
        _claimed(s3, {"assetId": "a1", "owner": gate.owner_id("tok-1")})
        result = gate.lambda_handler(_event(), None)

    assert result["gate"] == "proceed"
    assert result["claimFingerprint"]
    sfn.send_task_success.assert_not_called()


@pytest.mark.parametrize("existing_claim", [
    {"assetId": "a1"},
    b"not JSON",
    ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject"),
], ids=["no-owner-field", "unreadable", "released-before-read-back"])
def test_an_unverifiable_claim_is_treated_as_another_executions(gate, existing_claim):
    with patch.object(gate, "s3_client") as s3, patch.object(gate, "sfn_client"):
        _claimed(s3, existing_claim)
        result = gate.lambda_handler(_event(), None)

    assert result["gate"] == "skip"


def test_without_a_task_token_a_held_claim_is_never_claimed_as_ours(gate):
    with patch.object(gate, "s3_client") as s3, patch.object(gate, "sfn_client"):
        _claimed(s3, {"assetId": "a1", "owner": ""})
        result = gate.lambda_handler(_event(sfnExternalTaskToken=""), None)

    assert result["gate"] == "skip"
    s3.get_object.assert_not_called()
