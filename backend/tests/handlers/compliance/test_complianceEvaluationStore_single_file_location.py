# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compliance reads the keys a single-file asset holds.

A single-file asset (location key with no trailing ``/``) holds its object, its companion folder
``{file key}/`` and its own previews. The compliance asset location appended ``/`` to the key, so the
asset's own file and previews were never evaluated. The listings run against a versioned moto bucket
holding the asset's keys beside keys that only share its characters and a companion entry at the
object's own path, which the object shadows.
"""

from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance._harness import ASSET  # noqa: F401
from handlers.compliance import complianceEvaluationStore as store

BUCKET = "vams-test-assets"
FILE_LOCATION = "p1/model.glb"
OWN_PREVIEW = "p1/model.glb.previewFile.png"
COMPANION = "p1/model.glb/sub/x.obj"
SHADOWED = "p1/model.glb/model.glb"
NEIGHBOURS = ("p1/model.glb.bak", "p1/model.glbx/inner.txt")


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in (FILE_LOCATION, OWN_PREVIEW, COMPANION, SHADOWED) + NEIGHBOURS:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"content")
        yield client


@pytest.mark.unit
class TestComplianceAssetFiles:
    def test_the_asset_location_is_the_stored_key(self, monkeypatch):
        table = MagicMock()
        table.query.return_value = {"Items": [{"bucketId": "b1", "bucketName": BUCKET}]}
        monkeypatch.setattr(store, "s3_asset_buckets_table", table)
        asset = {"bucketId": "b1", "assetLocation": {"Key": FILE_LOCATION}}
        assert store.asset_s3_location(asset) == (BUCKET, FILE_LOCATION)
        assert store.asset_s3_location(dict(asset, assetLocation={"Key": "proj/a/"})) == (BUCKET, "proj/a/")

    def test_the_file_keys_are_the_keys_the_asset_holds(self, s3, monkeypatch):
        monkeypatch.setattr(store, "s3_client", s3)
        assert store.list_file_keys_under(BUCKET, FILE_LOCATION) == [
            "/model.glb", "/model.glb.previewFile.png", "/sub/x.obj"]

    def test_the_current_files_are_the_keys_the_asset_holds(self, s3, monkeypatch):
        monkeypatch.setattr(store, "s3_client", s3)
        monkeypatch.setattr(store, "asset_s3_location", lambda asset: (BUCKET, FILE_LOCATION))
        files = store.get_current_files("db1", "a1", asset={"assetId": "a1"})
        assert [f[0] for f in files] == ["/model.glb", "/model.glb.previewFile.png", "/sub/x.obj"]

    @pytest.mark.parametrize("relative,key", [("/model.glb", FILE_LOCATION), ("/sub/x.obj", COMPANION)])
    def test_a_file_change_source_reads_the_asset_key(self, monkeypatch, relative, key):
        heads = []
        monkeypatch.setattr(store, "object_change_source", lambda bucket, k: heads.append(k) or "upload")
        assert store.asset_file_change_source((BUCKET, FILE_LOCATION), relative) == "upload"
        assert heads == [key]
