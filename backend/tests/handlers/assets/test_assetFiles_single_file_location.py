# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""File operations on a single-file asset act on its one object only.

An asset created with a ``bucketExistingKey`` that names an object has a location key with no
trailing ``/``. That key used as an S3 prefix also matches every longer key that starts with the same
characters -- here ``model.glb.bak``, the preview-shaped ``model.glb.previewFile.png`` and the subtree
``model.glb/inner.txt``, none of which belong to the asset -- and appending ``/`` to it names nothing.
These tests run the file listing, file info, archive, unarchive, delete and preview handling against a
versioned moto bucket holding the asset's object beside those keys, and check that only the asset's
own object is listed, resolved and changed. A folder asset next to a sibling prefix is the control.
"""

import os

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402

BUCKET = "vams-test-assets"
FILE_LOCATION = "p1/model.glb"
OWN_KEY = FILE_LOCATION
OUTSIDE_KEYS = ("p1/model.glb.bak", "p1/model.glb.previewFile.png", "p1/model.glb/inner.txt")
FOLDER_LOCATION = "proj/a/"
FOLDER_KEYS = ("proj/a/", "proj/a/x.txt", "proj/a/x.txt.previewFile.png")
SIBLING_FOLDER_KEYS = ("proj/a-other/y.txt", "proj/a.bak")
CLAIMS = {"tokens": ["tester"], "roles": []}


def _live(client, key):
    try:
        client.head_object(Bucket=BUCKET, Key=key)
        return True
    except client.exceptions.ClientError:
        return False


def _versions(client, key):
    listing = client.list_object_versions(Bucket=BUCKET, Prefix=key)
    return ([v for v in listing.get("Versions", []) if v["Key"] == key]
            + [m for m in listing.get("DeleteMarkers", []) if m["Key"] == key])


@pytest.fixture
def af(monkeypatch):
    """assetFiles against a versioned moto bucket; the asset record names the location under test."""
    from backend.backend.handlers.assets import assetFiles as module

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in (OWN_KEY,) + OUTSIDE_KEYS + FOLDER_KEYS + SIBLING_FOLDER_KEYS:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"content")

        asset = {"databaseId": "db1", "assetId": "a1", "bucketId": "b1",
                 "assetLocation": {"Key": FILE_LOCATION}}
        monkeypatch.setattr(module, "s3_client", client)
        monkeypatch.setattr(module, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(module, "get_asset_with_permissions", lambda *args, **kwargs: asset)
        monkeypatch.setattr(module, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET,
                                               "baseAssetsPrefix": ""})
        monkeypatch.setattr(module, "asset_file_version_history_table", None)
        for name in ("send_subscription_email", "delete_assetAuxiliary_files", "delete_file_metadata",
                     "delete_file_version_records", "move_auxiliary_files", "copy_auxiliary_files"):
            monkeypatch.setattr(module, name, lambda *args, **kwargs: None)
        module.test_client = client
        module.test_asset = asset
        yield module


@pytest.mark.unit
class TestResolveAssetFilePath:
    @pytest.mark.parametrize("path", ["/model.glb", "model.glb", OWN_KEY])
    def test_the_file_name_resolves_to_the_asset_object(self, af, path):
        assert af.resolve_asset_file_path(FILE_LOCATION, path) == OWN_KEY

    @pytest.mark.parametrize("path", ["/", "/model.glb.bak", "/.bak", "/inner.txt", "/model.glb/inner.txt",
                                      "p1/model.glb.bak", "/other.glb"])
    def test_no_other_path_resolves(self, af, path):
        with pytest.raises(af.VAMSGeneralErrorResponse) as e:
            af.resolve_asset_file_path(FILE_LOCATION, path)
        assert "not part of this asset" in str(e.value)

    @pytest.mark.parametrize("path,expected", [
        ("/x.txt", "proj/a/x.txt"), ("x.txt", "proj/a/x.txt"), ("proj/a/x.txt", "proj/a/x.txt"),
        ("/", "proj/a/"), ("/sub/", "proj/a/sub/"),
    ])
    def test_a_folder_location_resolves_as_before(self, af, path, expected):
        assert af.resolve_asset_file_path(FOLDER_LOCATION, path) == expected


@pytest.mark.unit
@pytest.mark.parametrize("basic_mode", [False, True], ids=["full", "basic"])
class TestListFiles:
    def test_only_the_asset_object_is_listed_at_its_file_name(self, af, basic_mode):
        result = af.list_s3_objects_with_archive_status(
            BUCKET, FILE_LOCATION, {}, include_archived=True, basic_mode=basic_mode)
        assert [(i["key"], i["relativePath"]) for i in result["items"]] == [(OWN_KEY, "/model.glb")]
        assert "NextToken" not in result

    def test_a_one_item_page_has_no_next_page(self, af, basic_mode):
        result = af.list_s3_objects_with_archive_status(
            BUCKET, FILE_LOCATION, {"pageSize": 1}, include_archived=False, basic_mode=basic_mode)
        assert [i["key"] for i in result["items"]] == [OWN_KEY]
        assert "NextToken" not in result

    def test_an_archived_neighbour_is_not_listed_and_the_archived_object_is(self, af, basic_mode):
        af.test_client.delete_object(Bucket=BUCKET, Key="p1/model.glb.bak")
        af.test_client.delete_object(Bucket=BUCKET, Key=OWN_KEY)
        result = af.list_s3_objects_with_archive_status(
            BUCKET, FILE_LOCATION, {}, include_archived=True, basic_mode=basic_mode)
        assert [(i["key"], i["relativePath"], i["isArchived"]) for i in result["items"]] == [
            (OWN_KEY, "/model.glb", True)]

    @pytest.mark.parametrize("prefix,listed", [("mod", [OWN_KEY]), ("/model.glb", [OWN_KEY]),
                                               ("sub/", []), ("model.glb/", [])])
    def test_a_prefix_filter_applies_to_the_file_name(self, af, basic_mode, prefix, listed):
        result = af.list_s3_objects_with_archive_status(
            BUCKET, FILE_LOCATION, {"prefix": prefix}, include_archived=True, basic_mode=basic_mode)
        assert [i["key"] for i in result["items"]] == listed

    def test_control_a_folder_lists_its_keys_and_no_sibling_prefix(self, af, basic_mode):
        result = af.list_s3_objects_with_archive_status(
            BUCKET, FOLDER_LOCATION, {}, include_archived=True, basic_mode=basic_mode)
        assert sorted(i["relativePath"] for i in result["items"]) == [
            "/", "/x.txt", "/x.txt.previewFile.png"]


@pytest.mark.unit
class TestPrefixHelpersStayInTheLocation:
    def test_permanent_delete_of_a_single_file_location_deletes_its_object_only(self, af):
        deleted = af.delete_s3_prefix_all_versions(BUCKET, FILE_LOCATION, location=FILE_LOCATION)
        assert deleted == [OWN_KEY]
        assert _versions(af.test_client, OWN_KEY) == []
        for key in OUTSIDE_KEYS:
            assert _live(af.test_client, key), key

    def test_permanent_delete_of_a_folder_location_keeps_sibling_prefixes(self, af):
        deleted = af.delete_s3_prefix_all_versions(BUCKET, "proj/a", location=FOLDER_LOCATION)
        assert sorted(deleted) == sorted(FOLDER_KEYS)
        for key in SIBLING_FOLDER_KEYS:
            assert _live(af.test_client, key), key

    def test_archive_of_a_single_file_location_archives_its_object_only(self, af):
        archived = af.archive_s3_prefix(BUCKET, FILE_LOCATION, "db1", "a1", location=FILE_LOCATION)
        assert list(archived) == [OWN_KEY]
        assert not _live(af.test_client, OWN_KEY)
        for key in OUTSIDE_KEYS:
            assert _live(af.test_client, key), key


@pytest.mark.unit
class TestFileOperations:
    @pytest.mark.parametrize("is_prefix", [False, True], ids=["file", "prefix"])
    def test_archive_file_archives_the_asset_object_only(self, af, is_prefix):
        response = af.archive_file("db1", "a1", "/model.glb", is_prefix, CLAIMS)
        assert response.success is True
        assert not _live(af.test_client, OWN_KEY)
        for key in OUTSIDE_KEYS:
            assert _live(af.test_client, key), key

    @pytest.mark.parametrize("is_prefix", [False, True], ids=["file", "prefix"])
    def test_delete_file_deletes_the_asset_object_only(self, af, is_prefix):
        response = af.delete_file("db1", "a1", "/model.glb", is_prefix, True, CLAIMS)
        assert response.success is True
        assert _versions(af.test_client, OWN_KEY) == []
        for key in OUTSIDE_KEYS:
            assert _live(af.test_client, key), key

    def test_unarchive_file_restores_the_asset_object_only(self, af):
        af.test_client.delete_object(Bucket=BUCKET, Key=OWN_KEY)
        af.test_client.delete_object(Bucket=BUCKET, Key="p1/model.glb.previewFile.png")
        response = af.unarchive_file("db1", "a1", "/model.glb", CLAIMS)
        assert response.affectedFiles == ["/model.glb"]
        assert _live(af.test_client, OWN_KEY)
        assert not _live(af.test_client, "p1/model.glb.previewFile.png")

    def test_file_info_reads_the_asset_object_at_its_file_name(self, af):
        info = af.get_file_info("db1", "a1", "/model.glb", False, CLAIMS)
        assert info.relativePath == "/model.glb"
        assert info.previewFile == ""

    @pytest.mark.parametrize("path", ["/model.glb.bak", "/inner.txt"])
    def test_file_operations_refuse_a_key_outside_the_asset(self, af, path):
        with pytest.raises(af.VAMSGeneralErrorResponse):
            af.archive_file("db1", "a1", path, False, CLAIMS)
        with pytest.raises(af.VAMSGeneralErrorResponse):
            af.delete_file("db1", "a1", path, False, True, CLAIMS)
        for key in OUTSIDE_KEYS:
            assert _live(af.test_client, key), key

    def test_a_folder_cannot_be_created_inside_a_single_file_asset(self, af):
        from models.assetsV3 import CreateFolderRequestModel
        with pytest.raises(af.VAMSGeneralErrorResponse):
            af.create_folder("db1", "a1", CreateFolderRequestModel(relativeKey="/new/"), CLAIMS)
        assert _versions(af.test_client, "p1/model.glb/new/") == []

    def test_copying_the_object_out_leaves_preview_shaped_neighbours_alone(self, af):
        copied = af.process_preview_files(
            operation="copy", bucket=BUCKET, source_key=OWN_KEY, dest_key="proj/a/model.glb",
            base_key=FOLDER_LOCATION, source_location=FILE_LOCATION)
        assert copied == []
        assert not _live(af.test_client, "proj/a/model.glb.previewFile.png")

    def test_control_a_folder_asset_still_moves_its_preview(self, af):
        moved = af.process_preview_files(
            operation="move", bucket=BUCKET, source_key="proj/a/x.txt", dest_key="proj/a/y.txt",
            base_key=FOLDER_LOCATION)
        assert moved == ["/x.txt.previewFile.png", "/y.txt.previewFile.png"]

    def test_a_folder_source_location_keeps_its_own_preview(self, af):
        moved = af.process_preview_files(
            operation="move", bucket=BUCKET, source_key="proj/a/x.txt", dest_key="proj/a/y.txt",
            base_key=FOLDER_LOCATION, source_location=FOLDER_LOCATION)
        assert moved == ["/x.txt.previewFile.png", "/y.txt.previewFile.png"]
