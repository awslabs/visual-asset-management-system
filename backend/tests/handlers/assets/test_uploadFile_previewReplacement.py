# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Replacing a file's preview companion writes a new version of its key and deletes only the
companions of other extensions (#1713). A delete marker on the key being overwritten raises an S3
removal whose provenance cannot be read, which the compliance trigger evaluated as a user change:
with a pipeline rule whose output a chained preview workflow runs on, that evaluation launched the
rule again, every cycle."""

import os
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile  # noqa: F401,E402
from backend.tests.handlers.assets.test_uploadFile_preview_extension_external import (  # noqa: E402
    ASSET_BUCKET, UPLOAD_ID, _complete_external_upload_env, _external_request,
)

BUCKET = "asset-bucket"
BASE = "asset-1/model.glb"


@pytest.fixture
def versioned_bucket():
    from backend.backend.handlers.assets import uploadFile as uf
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for extension in ("gif", "png"):
            s3.put_object(Bucket=BUCKET, Key=f"{BASE}.previewFile.{extension}", Body=b"x")
        with patch.object(uf, "s3", s3):
            yield uf, s3


@pytest.mark.unit
def test_the_preview_being_overwritten_gets_no_delete_marker(versioned_bucket):
    uf, s3 = versioned_bucket
    deleted = uf.delete_existing_preview_files(BUCKET, BASE, keep=f"{BASE}.previewFile.gif")
    assert deleted == [f"{BASE}.previewFile.png"]
    markers = s3.list_object_versions(Bucket=BUCKET, Prefix=BASE).get("DeleteMarkers", [])
    assert [marker["Key"] for marker in markers] == [f"{BASE}.previewFile.png"]
    s3.head_object(Bucket=BUCKET, Key=f"{BASE}.previewFile.gif")  # still current


@pytest.mark.unit
def test_without_a_kept_key_every_companion_is_deleted(versioned_bucket):
    uf, _s3 = versioned_bucket
    assert sorted(uf.delete_existing_preview_files(BUCKET, BASE)) == [
        f"{BASE}.previewFile.gif", f"{BASE}.previewFile.png"]


@pytest.mark.unit
def test_the_external_completion_keeps_the_key_its_copy_overwrites():
    """The one caller passes the companion's final key, so a same-extension replacement deletes
    nothing of its own key and a companion of another extension is still removed."""
    companion = "/model.glb.previewFile.gif"
    with _complete_external_upload_env("assetFile") as (uf, mocks):
        with patch.object(uf, "delete_existing_preview_files", return_value=[]) as cleanup:
            response = uf.complete_external_upload(
                UPLOAD_ID, _external_request("assetFile", companion), {})
    assert response.overallSuccess is True
    cleanup.assert_called_once_with(ASSET_BUCKET, "asset-1/model.glb",
                                    keep="asset-1/model.glb.previewFile.gif")
    assert mocks["copy"].call_args.args[3] == "asset-1/model.glb.previewFile.gif"
