# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset versions, asset export and compliance read the keys a single-file asset holds.

A single-file asset (location key with no trailing ``/``) holds its object, its companion folder
``{file key}/`` and its own previews. Each of these readers appended ``/`` to the location, so the
asset's own file and its previews were never listed (an asset version of it recorded no file, and an
export of it held none). Each test lists a versioned moto bucket holding the asset's keys beside keys
that only share its characters (``model.glb.bak``, ``model.glbx/...``) and a companion entry at the
object's own path, which the object shadows.
"""

import boto3
import pytest
from moto import mock_aws
from unittest.mock import MagicMock

from backend.tests.handlers.assets.test_assetVersions_change_provenance import _assetVersions as av
from backend.tests.handlers.assets.test_assetExportService_authz_fail_closed import _load_asset_export_service as _load_export
from backend.tests.handlers.compliance.test_complianceEvaluationStore import store

BUCKET = "vams-test-assets"
FILE_LOCATION = "p1/model.glb"
OWN_PREVIEW = "p1/model.glb.previewFile.png"
COMPANION = "p1/model.glb/sub/x.obj"
SHADOWED = "p1/model.glb/model.glb"
NEIGHBOURS = ("p1/model.glb.bak", "p1/model.glbx/inner.txt")
HELD = (FILE_LOCATION, OWN_PREVIEW, COMPANION)


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in HELD + (SHADOWED,) + NEIGHBOURS + ("proj/a/x.txt", "proj/a-other/y.txt"):
            client.put_object(Bucket=BUCKET, Key=key, Body=b"content",
                              Metadata={"vams-changesource": "upload"})
        yield client


@pytest.mark.unit
class TestAssetVersionSnapshot:
    def test_the_snapshot_lists_the_keys_the_asset_holds(self, s3, monkeypatch):
        monkeypatch.setattr(av, "s3_client", s3)
        files = av.list_s3_files_with_versions(BUCKET, FILE_LOCATION)
        assert sorted((f["key"], f["relativeKey"]) for f in files) == sorted([
            (FILE_LOCATION, "model.glb"), (OWN_PREVIEW, "model.glb.previewFile.png"), (COMPANION, "sub/x.obj")])

    def test_an_archived_neighbour_is_not_in_the_snapshot(self, s3, monkeypatch):
        monkeypatch.setattr(av, "s3_client", s3)
        s3.delete_object(Bucket=BUCKET, Key="p1/model.glb.bak")
        s3.delete_object(Bucket=BUCKET, Key=FILE_LOCATION)
        files = av.list_s3_files_with_versions(BUCKET, FILE_LOCATION, include_archived=True)
        assert {f["key"] for f in files} == set(HELD)
        assert [f["relativeKey"] for f in files if f["isArchived"]] == ["model.glb"]

    def test_control_a_folder_snapshot_keeps_no_sibling_prefix(self, s3, monkeypatch):
        monkeypatch.setattr(av, "s3_client", s3)
        assert [f["relativeKey"] for f in av.list_s3_files_with_versions(BUCKET, "proj/a/")] == ["x.txt"]


@pytest.mark.unit
class TestAssetExportListing:
    def test_the_export_lists_the_keys_the_asset_holds(self, s3, monkeypatch):
        export = _load_export()
        monkeypatch.setattr(export, "s3_client", s3)
        files = export.list_s3_files(BUCKET, FILE_LOCATION)
        assert sorted((f["key"], f["relativePath"]) for f in files) == sorted([
            (FILE_LOCATION, "/model.glb"), (OWN_PREVIEW, "/model.glb.previewFile.png"), (COMPANION, "/sub/x.obj")])

    def test_control_a_folder_export_keeps_no_sibling_prefix(self, s3, monkeypatch):
        export = _load_export()
        monkeypatch.setattr(export, "s3_client", s3)
        assert [f["relativePath"] for f in export.list_s3_files(BUCKET, "proj/a/")] == ["/x.txt"]


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
