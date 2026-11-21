# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Files of an asset whose stored location sits inside another asset's folder belong to the nested asset.
The containing asset's list, info, download, zip, archive, unarchive, permanent delete, single-file
operations, versions and uploads leave them alone, and a path that resolves into a nested asset's location
answers like a file this asset does not have. The nested asset's own operations are unchanged.

Layout (bucket `vams-nested`): `outer` (database db1) at `p/outer/` holds `a.txt` and `sub/d.txt`. Inside it:
`inner` (database db2) at `p/outer/inner/`, `mfile` (db1, single file) at `p/outer/model.glb` with its
companion folder and preview, and `old`, archived (db3), at `p/outer/old/`. Once the location index is ready
the nested locations come from it; before it, only a destructive operation finds them, by walking the
bucket's registrations.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

import os  # noqa: E402

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

# Module-level imports populate the real handler packages before the root conftest's autouse fixture runs
from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402
from backend.backend.handlers.assets import assetVersions as av  # noqa: E402
from backend.backend.handlers.assets import uploadFile as up  # noqa: E402
from tests.common.fileAttributionFakes import (  # noqa: E402
    AttributionAssetTable, AttributionBucketsTable, asset_row, registration,
)

al = sys.modules['common.assetLocations']
fa = sys.modules['common.fileAttribution']

BUCKET = "vams-nested"
OUTER_LOCATION = "p/outer/"
OUTER_OWN = ("p/outer/a.txt", "p/outer/sub/d.txt")
NESTED_KEYS = ("p/outer/inner/", "p/outer/inner/b.txt", "p/outer/model.glb", "p/outer/model.glb/notes.txt",
               "p/outer/model.glb.previewFile.png", "p/outer/old/c.txt")
OUTER = asset_row("db1", "outer", "reg-1", OUTER_LOCATION, BUCKET)
ROWS = [OUTER,
        asset_row("db2", "inner", "reg-1", "p/outer/inner/", BUCKET),
        asset_row("db1", "mfile", "reg-1", "p/outer/model.glb", BUCKET),
        asset_row("db3", "old", "reg-1", "p/outer/old/", BUCKET, archived=True)]
CLAIMS = {"tokens": ["tester"], "roles": []}


def _keys(client):
    return sorted(o["Key"] for o in client.list_objects_v2(Bucket=BUCKET).get("Contents", []))


@pytest.fixture
def af(monkeypatch):
    from backend.backend.handlers.assets import assetFiles as module

    al.clear_nested_locations_cache()
    fa.clear_registration_cache()
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in OUTER_OWN + NESTED_KEYS:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"content")
        asset = dict(OUTER)
        monkeypatch.setattr(al, "index_ready", lambda name: True)
        monkeypatch.setattr(module, "asset_table", AttributionAssetTable(ROWS))
        monkeypatch.setattr(module, "buckets_table", AttributionBucketsTable([registration("reg-1", BUCKET, "/")]))
        monkeypatch.setattr(module, "s3_client", client)
        monkeypatch.setattr(module, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(module, "get_asset_with_permissions", lambda *args, **kwargs: asset)
        monkeypatch.setattr(module, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET, "baseAssetsPrefix": "/"})
        monkeypatch.setattr(module, "asset_file_version_history_table", None)
        monkeypatch.setattr(module, "MAX_PARALLEL_S3_WORKERS", 1)
        for name in ("send_subscription_email", "delete_assetAuxiliary_files", "delete_file_metadata",
                     "delete_file_version_records", "move_auxiliary_files", "copy_auxiliary_files"):
            monkeypatch.setattr(module, name, lambda *args, **kwargs: None)
        module.test_client = client
        module.test_asset = asset
        yield module
    al.clear_nested_locations_cache()


@pytest.mark.unit
class TestTheContainingAssetsFileOperations:
    def _listed(self, af, include_archived=False):
        request = af.ListAssetFilesRequestModel(pageSize=100, includeArchived=include_archived, basic=True)
        response = af.list_asset_files_current("db1", "outer", af.test_asset, BUCKET, OUTER_LOCATION, request)
        return sorted(item.key for item in response.items)

    def test_the_list_leaves_out_every_nested_asset_live_or_archived(self, af):
        assert self._listed(af) == list(OUTER_OWN)

    def test_before_the_index_is_ready_the_list_is_as_before(self, af, monkeypatch):
        monkeypatch.setattr(al, "index_ready", lambda name: False)
        # The list shows a preview under its base file, not as an item
        assert self._listed(af) == sorted(k for k in OUTER_OWN + NESTED_KEYS if ".previewFile." not in k)

    @pytest.mark.parametrize("path", ["/inner/b.txt", "/model.glb", "/model.glb/notes.txt", "/old/c.txt"])
    def test_a_path_in_a_nested_location_is_a_file_this_asset_does_not_have(self, af, path):
        for operation in (lambda: af.delete_file("db1", "outer", path, False, True, CLAIMS),
                          lambda: af.archive_file("db1", "outer", path, False, CLAIMS),
                          lambda: af.unarchive_file("db1", "outer", path, CLAIMS),
                          lambda: af.revert_file_version("db1", "outer", path, "v1", CLAIMS)):
            with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
                operation()
            assert str(raised.value).endswith(": File not found.")
        assert _keys(af.test_client) == sorted(OUTER_OWN + NESTED_KEYS)

    def test_file_info_of_a_nested_file_is_not_found_and_names_no_asset(self, af):
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.get_file_info("db1", "outer", "/inner/b.txt", False, CLAIMS)
        assert str(raised.value).endswith(": File not found.")
        assert "inner" not in str(raised.value) and "db2" not in str(raised.value)

    def test_a_move_or_folder_into_a_nested_location_is_refused(self, af):
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.move_file("db1", "outer", "/a.txt", "/inner/a.txt", CLAIMS)
        assert str(raised.value).endswith(": " + al.NESTED_PATH_REFUSAL)
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.move_file("db1", "outer", "/inner/b.txt", "/b.txt", CLAIMS)
        assert str(raised.value).endswith(": Source file not found.")
        assert _keys(af.test_client) == sorted(OUTER_OWN + NESTED_KEYS)

    def test_a_folder_delete_and_archive_of_the_whole_location_keep_the_nested_files(self, af):
        nested = af.nested_locations_for(af.test_asset, BUCKET, OUTER_LOCATION, destructive=True)
        archived = af.archive_s3_prefix(BUCKET, OUTER_LOCATION, "db1", "outer", location=OUTER_LOCATION, nested=nested)
        assert sorted(archived) == list(OUTER_OWN)
        assert _keys(af.test_client) == sorted(NESTED_KEYS)
        deleted = af.delete_s3_prefix_all_versions(BUCKET, OUTER_LOCATION, location=OUTER_LOCATION, nested=nested)
        assert sorted(deleted) == list(OUTER_OWN)
        assert _keys(af.test_client) == sorted(NESTED_KEYS)

    def test_before_the_index_a_destructive_operation_walks_the_registrations(self, af, monkeypatch):
        monkeypatch.setattr(al, "index_ready", lambda name: False)
        assert not af.nested_locations_for(af.test_asset, BUCKET, OUTER_LOCATION)
        nested = af.nested_locations_for(af.test_asset, BUCKET, OUTER_LOCATION, destructive=True)
        assert nested.source == "registrationWalk"
        deleted = af.delete_s3_prefix_all_versions(BUCKET, OUTER_LOCATION, location=OUTER_LOCATION, nested=nested)
        assert sorted(deleted) == list(OUTER_OWN)
        assert _keys(af.test_client) == sorted(NESTED_KEYS)

    def test_the_nested_assets_own_operations_are_unchanged(self, af):
        af.test_asset.update(asset_row("db2", "inner", "reg-1", "p/outer/inner/", BUCKET))
        response = af.delete_file("db2", "inner", "/b.txt", False, True, CLAIMS)
        assert response.affectedFiles == ["/b.txt"]
        assert "p/outer/inner/b.txt" not in _keys(af.test_client)


@pytest.mark.unit
class TestTheSingleFileAssetWithAnAssetInItsCompanionFolder:
    def test_the_companion_folder_asset_keeps_its_files(self, monkeypatch):
        monkeypatch.setattr(al, "index_ready", lambda name: True)
        al.clear_nested_locations_cache()
        rows = [asset_row("db1", "one", "reg-1", "q/model.glb", BUCKET),
                asset_row("db2", "sub", "reg-1", "q/model.glb/sub/", BUCKET)]
        nested = fa.nested_locations_of_asset(AttributionAssetTable(rows), AttributionBucketsTable([]), rows[0],
                                              BUCKET)
        assert [nested.holds(k) for k in ("q/model.glb", "q/model.glb/x.obj", "q/model.glb/sub/y.obj")] == [
            True, True, False]


@pytest.mark.unit
class TestDownloadAndUpload:
    def test_a_download_of_a_nested_file_is_not_found(self):
        from backend.backend.common import downloadEligibility as de
        nested = al.NestedLocations(OUTER_LOCATION, [al.LocationEntry("db2", "inner", "reg-1", "p/outer/inner/")])
        s3 = MagicMock()
        with pytest.raises(de.VAMSGeneralErrorResponse) as raised:
            de.check_file_eligibility("db1", "outer", s3, BUCKET, OUTER_LOCATION, "/inner/b.txt", nested=nested)
        assert str(raised.value).endswith(": " + de.MSG_FILE_NOT_FOUND)
        s3.head_object.assert_not_called()

    def test_an_upload_into_a_nested_location_is_refused(self, monkeypatch):
        monkeypatch.setattr(al, "index_ready", lambda name: True)
        al.clear_nested_locations_cache()
        monkeypatch.setattr(up, "asset_table", AttributionAssetTable(ROWS))
        monkeypatch.setattr(up, "buckets_table", AttributionBucketsTable([]))
        assert up.nested_path_refused(OUTER, BUCKET, OUTER_LOCATION, "p/outer/inner/new.txt") is True
        assert up.nested_path_refused(OUTER, BUCKET, OUTER_LOCATION, "p/outer/new.txt") is False


@pytest.mark.unit
class TestVersionsAndZip:
    def test_a_version_snapshot_of_the_latest_files_leaves_out_nested_files(self):
        with mock_aws():
            client = boto3.client("s3", region_name="us-east-1")
            client.create_bucket(Bucket=BUCKET)
            client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
            for key in OUTER_OWN + NESTED_KEYS:
                client.put_object(Bucket=BUCKET, Key=key, Body=b"content")
            saved = av.s3_client
            av.s3_client = client
            try:
                nested = al.NestedLocations(OUTER_LOCATION, [
                    al.LocationEntry("db2", "inner", "reg-1", "p/outer/inner/"),
                    al.LocationEntry("db1", "mfile", "reg-1", "p/outer/model.glb"),
                    al.LocationEntry("db3#deleted", "old", "reg-1", "p/outer/old/")])
                files = av.list_s3_files_with_versions(BUCKET, OUTER_LOCATION, nested=nested)
            finally:
                av.s3_client = saved
        assert sorted(f["relativeKey"] for f in files) == ["a.txt", "sub/d.txt"]

    def test_a_zip_selection_in_a_nested_location_is_refused_and_a_folder_scan_leaves_it_out(self, monkeypatch):
        from backend.tests.handlers.zipDownloads import zipHarness as h
        svc = h.load_handlers().svc
        nested = al.NestedLocations(OUTER_LOCATION, [al.LocationEntry("db2", "inner", "reg-1", "p/outer/inner/")])
        for kind, path in (("file", "/inner/b.txt"), ("folder", "/inner/")):
            request = SimpleNamespace(selection=[SimpleNamespace(type=kind, relativePath=path, versionId=None)])
            with pytest.raises(svc.ZipRefusal):
                svc._resolve_current(BUCKET, OUTER_LOCATION, request, nested)
        listing = {"Versions": [{"Key": k, "IsLatest": True, "Size": 1}
                                for k in ("p/outer/a.txt", "p/outer/inner/b.txt")]}
        monkeypatch.setattr(svc, "s3", MagicMock(list_object_versions=MagicMock(return_value=listing)))
        count, _size, done = svc._prescan_prefix(BUCKET, OUTER_LOCATION, float("inf"), [100], nested)
        assert (count, done) == (1, True)
