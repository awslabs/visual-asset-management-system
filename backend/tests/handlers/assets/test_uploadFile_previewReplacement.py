# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A file's preview companions are its own (#1716): the clean-up before a workflow preview write for
`model.glb` lists only keys under `model.glb.previewFile.`, so the preview of a same-named file in a
subfolder (`lod1/model.glb`) stays current."""

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
def test_a_same_named_file_in_a_subfolder_keeps_its_preview(versioned_bucket):
    """The companions of `asset-1/model.glb` are its own: `asset-1/lod1/model.glb` is another file,
    and its preview stays current."""
    uf, s3 = versioned_bucket
    nested = "asset-1/lod1/model.glb.previewFile.gif"
    s3.put_object(Bucket=BUCKET, Key="asset-1/lod1/model.glb", Body=b"x")
    s3.put_object(Bucket=BUCKET, Key=nested, Body=b"x")
    assert uf.find_existing_preview_files(BUCKET, BASE) == [
        f"{BASE}.previewFile.gif", f"{BASE}.previewFile.png"]
    uf.delete_existing_preview_files(BUCKET, BASE)
    s3.head_object(Bucket=BUCKET, Key=nested)  # still current
    assert uf.find_existing_preview_files(BUCKET, "asset-1/lod1/model.glb") == [nested]
