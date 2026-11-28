# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The copy engine's asset type for a single-file asset comes from the keys it holds.

After a large copy lands, the engine sets the asset type from the asset's location listing. A
single-file location key (no trailing ``/``) listed as an S3 prefix also returns keys that only share
its characters (``model.glb.bak``, ``model.glbx/...``), which turned the asset into a ``folder``; a file
in its companion folder ``{file key}/`` does make it one. A folder asset beside a
sibling prefix is the control.
"""

import os

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE_NAME", "t-copy-jobs")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "assetStorageTable")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "t-uploads")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-executions")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "sendEmailFunction")
os.environ.setdefault("PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME", "processOutput")

from backend.backend.handlers.assets import largeObjectCopy as engine  # noqa: E402

BUCKET = "asset-bucket"


@pytest.fixture
def s3(monkeypatch):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in ("p1/model.glb", "p1/model.glb.bak", "p1/model.glbx/inner.txt",
                    "proj/a/one.obj", "proj/a-other/two.txt"):
            client.put_object(Bucket=BUCKET, Key=key, Body=b"x")
        monkeypatch.setattr(engine, "s3", client)
        yield client


@pytest.mark.unit
class TestAssetTypeAfterLanding:
    def test_a_single_file_asset_is_typed_by_its_object(self, s3):
        assert engine._determine_asset_type(BUCKET, "p1/model.glb") == ".glb"

    def test_a_single_file_asset_with_a_companion_file_is_a_folder(self, s3):
        s3.put_object(Bucket=BUCKET, Key="p1/model.glb/sub/part.obj", Body=b"x")
        assert engine._determine_asset_type(BUCKET, "p1/model.glb") == "folder"

    def test_control_a_folder_asset_ignores_a_sibling_prefix(self, s3):
        assert engine._determine_asset_type(BUCKET, "proj/a/") == ".obj"

    def test_control_a_folder_asset_with_two_files_is_a_folder(self, s3):
        s3.put_object(Bucket=BUCKET, Key="proj/a/three.obj", Body=b"x")
        assert engine._determine_asset_type(BUCKET, "proj/a/") == "folder"


@pytest.mark.unit
class TestAssetLocationForTheAssetType:
    """The asset type is read under the asset's stored location; a legacy row with none uses the
    asset's folder under the registration prefix, which the copy's destination key carries."""

    def test_the_stored_location_is_used(self):
        job = {"assetId": "a1", "dest": {"bucket": BUCKET, "key": "vams/projects/x/part.obj"}}
        asset = {"assetLocation": {"Key": "/vams/projects/x/"}}
        assert engine._asset_location(asset, job) == "vams/projects/x/"

    def test_a_legacy_row_uses_its_folder_under_the_registration_prefix(self):
        job = {"assetId": "a1", "dest": {"bucket": BUCKET, "key": "vams/a1/sub/part.obj"}}
        assert engine._asset_location({}, job) == "vams/a1/"

    def test_a_legacy_row_at_the_bucket_root(self):
        job = {"assetId": "a1", "dest": {"bucket": BUCKET, "key": "a1/part.obj"}}
        assert engine._asset_location({}, job) == "a1/"

    def test_a_segment_that_only_ends_with_the_asset_id_is_not_its_folder(self):
        job = {"assetId": "a1", "dest": {"bucket": BUCKET, "key": "vams/xa1/part.obj"}}
        assert engine._asset_location({}, job) == ""
