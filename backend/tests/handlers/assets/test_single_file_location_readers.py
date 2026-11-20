# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset versions and asset export read the keys a single-file asset holds.

A single-file asset (location key with no trailing ``/``) holds its object, its companion folder
``{file key}/`` and its own previews. Each of these readers appended ``/`` to the location, so the
asset's own file and its previews were never listed (an asset version of it recorded no file, and an
export of it held none). Each test lists a versioned moto bucket holding the asset's keys beside keys
that only share its characters (``model.glb.bak``, ``model.glbx/...``); the companion entry named exactly
like the object is listed one level down.
"""

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.assets.test_assetVersions_change_provenance import _assetVersions as av
from backend.tests.handlers.assets.test_assetExportService_authz_fail_closed import _load_asset_export_service as _load_export

BUCKET = "vams-test-assets"
FILE_LOCATION = "p1/model.glb"
OWN_PREVIEW = "p1/model.glb.previewFile.png"
COMPANION = "p1/model.glb/sub/x.obj"
SHADOWED = "p1/model.glb/model.glb"
NEIGHBOURS = ("p1/model.glb.bak", "p1/model.glbx/inner.txt")
HELD = (FILE_LOCATION, OWN_PREVIEW, COMPANION, SHADOWED)


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in HELD + NEIGHBOURS + ("proj/a/x.txt", "proj/a-other/y.txt"):
            client.put_object(Bucket=BUCKET, Key=key, Body=b"content",
                              Metadata={"vams-changesource": "upload"})
        yield client


@pytest.mark.unit
class TestAssetVersionSnapshot:
    def test_the_snapshot_lists_the_keys_the_asset_holds(self, s3, monkeypatch):
        monkeypatch.setattr(av, "s3_client", s3)
        files = av.list_s3_files_with_versions(BUCKET, FILE_LOCATION)
        assert sorted((f["key"], f["relativeKey"]) for f in files) == sorted([
            (FILE_LOCATION, "model.glb"), (OWN_PREVIEW, "model.glb.previewFile.png"), (COMPANION, "sub/x.obj"),
            (SHADOWED, "model.glb/model.glb")])

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
            (FILE_LOCATION, "/model.glb"), (OWN_PREVIEW, "/model.glb.previewFile.png"), (COMPANION, "/sub/x.obj"),
            (SHADOWED, "/model.glb/model.glb")])

    def test_control_a_folder_export_keeps_no_sibling_prefix(self, s3, monkeypatch):
        export = _load_export()
        monkeypatch.setattr(export, "s3_client", s3)
        assert [f["relativePath"] for f in export.list_s3_files(BUCKET, "proj/a/")] == ["/x.txt"]
