# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #1846: a single-file asset's own object is listed at ``/{file name}`` and reached there by every file route.

The asset's location is the object ``p1/model.glb``; it also holds its preview ``p1/model.glb.previewFile.png`` and
the companion folder ``p1/model.glb/``. Before the fix the object was listed at ``/`` (which the web shows as the
asset root) and every file route resolved a path under ``p1/model.glb/``, so no path reached it.

Without a companion entry named like the object (``p1/model.glb/model.glb``), the object is listed at
``/model.glb`` and every file route reaches it there, and by its own key (the web's ``keyPrefix``). With such an
entry, that entry keeps ``/model.glb`` and the asset behaves as before. Keys that only share the location's
characters stay out of reach, ``..`` is refused, and a folder asset is unchanged. These tests run the real
assetFiles functions against a versioned moto bucket.
"""

import os
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402
from models.common import VAMSGeneralErrorResponse  # noqa: E402

BUCKET = "vams-test-assets"
AUX_BUCKET = "vams-test-aux"
LOCATION = "p1/model.glb"
OWN_PREVIEW = "p1/model.glb.previewFile.png"
COMPANION = "p1/model.glb/sub/x.obj"
COPY = "p1/model.glb/model.glb"
NEIGHBOURS = ("p1/model.glb.bak", "p1/model.glbx/inner.txt", "p1/model.glb_tiles/t.b3dm")
FOLDER_LOCATION = "proj/a/"
OTHER_ASSET_KEY = "proj/other/secret.txt"
CLAIMS = {"tokens": ["tester"], "roles": []}


def _versions(client, key, bucket=BUCKET):
    listing = client.list_object_versions(Bucket=bucket, Prefix=key)
    return ([v for v in listing.get("Versions", []) if v["Key"] == key]
            + [m for m in listing.get("DeleteMarkers", []) if m["Key"] == key])


def _live(client, key, bucket=BUCKET):
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except client.exceptions.ClientError:
        return False


@pytest.fixture(params=["no-companion", "companion"])
def state(request):
    return request.param


@pytest.fixture
def af(monkeypatch):
    """assetFiles against versioned moto buckets; the asset record names LOCATION (or FOLDER_LOCATION)."""
    from backend.backend.handlers.assets import assetFiles as module

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        for bucket in (BUCKET, AUX_BUCKET):
            client.create_bucket(Bucket=bucket)
            client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        for key in (LOCATION, OWN_PREVIEW, COMPANION) + NEIGHBOURS + (
                "proj/a/", "proj/a/x.txt", OTHER_ASSET_KEY):
            client.put_object(Bucket=BUCKET, Key=key, Body=b"content-" + key.encode())
        for key in ("db1/p1/model.glb/preview/PotreeViewer/m.json",
                    "db1/p1/model.glb/sub/x.obj/preview/PotreeViewer/m.json"):
            client.put_object(Bucket=AUX_BUCKET, Key=key, Body=b"aux")

        holder = {"asset": {"databaseId": "db1", "assetId": "a1", "bucketId": "b1",
                            "assetLocation": {"Key": LOCATION}}}
        monkeypatch.setattr(module, "s3_client", client)
        monkeypatch.setattr(module, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(module, "asset_aux_bucket_name", AUX_BUCKET)
        monkeypatch.setattr(module, "get_asset_with_permissions", lambda *args, **kwargs: holder["asset"])
        monkeypatch.setattr(module, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET,
                                               "baseAssetsPrefix": ""})
        monkeypatch.setattr(module, "MAX_PARALLEL_S3_WORKERS", 1)
        monkeypatch.setattr(module, "send_subscription_email", MagicMock())
        monkeypatch.setattr(module, "asset_file_version_history_table", None)
        module.metadata_deletes = []
        monkeypatch.setattr(module, "delete_file_metadata",
                            lambda db, asset, path: module.metadata_deletes.append(path) or True)
        monkeypatch.setattr(module, "delete_file_version_records", lambda *args, **kwargs: 0)
        module.metadata_copies = []
        monkeypatch.setattr(module, "_copy_file_metadata_to_destination",
                            lambda *args: module.metadata_copies.append(args) or 0)
        module.test_client = client
        module.test_holder = holder
        yield module


def _with_state(af, state):
    if state == "companion":
        af.test_client.put_object(Bucket=BUCKET, Key=COPY, Body=b"the companion entry")


@pytest.mark.unit
class TestListing:
    def test_without_a_companion_the_object_is_one_row_at_its_file_name_with_its_preview(self, af):
        response = af.list_asset_files("db1", "a1", {"pageSize": "100"}, CLAIMS)
        by_key = {item.key: item for item in response.items}
        assert sorted(by_key) == sorted([LOCATION, COMPANION])
        assert by_key[LOCATION].relativePath == "/model.glb"
        assert by_key[LOCATION].fileName == "model.glb"
        assert by_key[LOCATION].previewFile == "/model.glb.previewFile.png"
        assert [i.relativePath for i in response.items].count("/model.glb") == 1

    def test_with_a_companion_the_listing_is_unchanged(self, af):
        _with_state(af, "companion")
        response = af.list_asset_files("db1", "a1", {"pageSize": "100"}, CLAIMS)
        by_key = {item.key: item for item in response.items}
        assert by_key[LOCATION].relativePath == "/"
        assert by_key[LOCATION].previewFile == "/.previewFile.png"
        assert by_key[COPY].relativePath == "/model.glb"

    @pytest.mark.parametrize("basic", ["true", "false"])
    def test_every_page_lists_the_object_once_and_no_neighbour(self, af, basic):
        items, token = [], None
        while True:
            query = {"pageSize": "1", "basic": basic}
            if token:
                query["startingToken"] = token
            response = af.list_asset_files("db1", "a1", query, CLAIMS)
            items.extend(response.items)
            token = response.NextToken
            if not token:
                break
        paths = sorted(i.relativePath for i in items)
        assert paths.count("/model.glb") == 1
        assert not {i.key for i in items} & set(NEIGHBOURS)

    def test_an_archived_object_is_listed_archived_at_its_file_name(self, af):
        af.test_client.delete_object(Bucket=BUCKET, Key=LOCATION)
        query = {"pageSize": "100", "maxItems": "1000", "includeArchived": "true"}
        response = af.list_asset_files("db1", "a1", query, CLAIMS)
        archived = [(i.key, i.relativePath) for i in response.items if i.isArchived]
        assert archived == [(LOCATION, "/model.glb")]

    def test_a_folder_filter_still_reports_paths_after_the_filter(self, af):
        response = af.list_asset_files("db1", "a1", {"pageSize": "100", "prefix": "sub/"}, CLAIMS)
        assert [(i.key, i.relativePath) for i in response.items] == [(COMPANION, "/x.obj")]

    def test_control_a_folder_asset_is_unchanged(self, af):
        af.test_holder["asset"] = {"databaseId": "db1", "assetId": "a2", "bucketId": "b1",
                                   "assetLocation": {"Key": FOLDER_LOCATION}}
        response = af.list_asset_files("db1", "a2", {"pageSize": "100"}, CLAIMS)
        assert sorted(i.relativePath for i in response.items) == ["/", "/x.txt"]


@pytest.mark.unit
class TestFileInfo:
    @pytest.mark.parametrize("path", ["/model.glb", "p1/model.glb"])
    def test_the_object_is_reached_by_its_path_and_its_own_key(self, af, path):
        info = af.get_file_info("db1", "a1", path, False, CLAIMS)
        assert info.key == LOCATION
        assert info.relativePath == "/model.glb"
        assert info.previewFile == "/model.glb.previewFile.png"

    def test_with_a_companion_the_path_names_the_companion(self, af):
        _with_state(af, "companion")
        info = af.get_file_info("db1", "a1", "/model.glb", False, CLAIMS)
        assert info.key == COPY
        assert info.relativePath == "/model.glb"

    def test_the_objects_version_history_is_listed(self, af, monkeypatch):
        af.test_client.put_object(Bucket=BUCKET, Key=LOCATION, Body=b"v2")
        versions_table = MagicMock()
        versions_table.query.return_value = {"Items": []}
        monkeypatch.setattr(af, "asset_version_files_table", versions_table)
        monkeypatch.setattr(af, "get_all_asset_versions", MagicMock(return_value=[]))
        monkeypatch.setattr(af, "query_asset_version_history_map", MagicMock(return_value={}))
        info = af.get_file_info("db1", "a1", "/model.glb", True, CLAIMS)
        assert len(info.versions) == 2

    @pytest.mark.parametrize("path", ["/../other/secret.txt", "/sub/../../x", ".."])
    def test_a_traversal_is_refused(self, af, path, state):
        _with_state(af, state)
        with pytest.raises(VAMSGeneralErrorResponse) as error:
            af.get_file_info("db1", "a1", path, False, CLAIMS)
        assert af.FILE_PATH_NOT_IN_ASSET_MESSAGE in str(error.value)

    @pytest.mark.parametrize("path", NEIGHBOURS + (OTHER_ASSET_KEY, "/" + OTHER_ASSET_KEY))
    def test_a_neighbour_or_another_assets_key_is_not_reached(self, af, path):
        with pytest.raises(VAMSGeneralErrorResponse):
            af.get_file_info("db1", "a1", path, False, CLAIMS)

    def test_control_a_folder_asset_is_unchanged(self, af):
        af.test_holder["asset"] = {"databaseId": "db1", "assetId": "a2", "bucketId": "b1",
                                   "assetLocation": {"Key": FOLDER_LOCATION}}
        for path in ("/x.txt", "proj/a/x.txt"):
            info = af.get_file_info("db1", "a2", path, False, CLAIMS)
            assert (info.key, info.relativePath) == ("proj/a/x.txt", "/x.txt")


@pytest.mark.unit
class TestArchiveUnarchiveDelete:
    def test_archive_and_unarchive_the_object_at_its_path(self, af):
        result = af.archive_file("db1", "a1", "/model.glb", False, CLAIMS)
        assert result.success
        assert not _live(af.test_client, LOCATION)
        assert not _live(af.test_client, OWN_PREVIEW)
        assert _live(af.test_client, COMPANION)
        for key in NEIGHBOURS:
            assert _live(af.test_client, key), key
        assert "/model.glb.previewFile.png" in result.affectedFiles

        result = af.unarchive_file("db1", "a1", "/model.glb", CLAIMS)
        assert result.success
        assert _live(af.test_client, LOCATION)
        assert _live(af.test_client, OWN_PREVIEW)

    def test_archive_with_a_companion_archives_the_companion(self, af):
        _with_state(af, "companion")
        af.archive_file("db1", "a1", "/model.glb", False, CLAIMS)
        assert not _live(af.test_client, COPY)
        assert _live(af.test_client, LOCATION)

    @pytest.mark.parametrize("path", ["/model.glb", "p1/model.glb"])
    def test_delete_removes_the_object_its_preview_metadata_and_own_aux_only(self, af, path):
        result = af.delete_file("db1", "a1", path, False, True, CLAIMS)
        assert result.success
        assert _versions(af.test_client, LOCATION) == []
        assert _versions(af.test_client, OWN_PREVIEW) == []
        assert _live(af.test_client, COMPANION)
        for key in NEIGHBOURS:
            assert _live(af.test_client, key), key
        assert af.metadata_deletes == ["model.glb"]
        assert _versions(af.test_client, "db1/p1/model.glb/preview/PotreeViewer/m.json", AUX_BUCKET) == []
        assert _live(af.test_client, "db1/p1/model.glb/sub/x.obj/preview/PotreeViewer/m.json", AUX_BUCKET)

    def test_deleting_a_companion_entry_keeps_the_objects_aux_data(self, af):
        af.delete_file("db1", "a1", "/sub/x.obj", False, True, CLAIMS)
        assert _live(af.test_client, "db1/p1/model.glb/preview/PotreeViewer/m.json", AUX_BUCKET)
        assert _live(af.test_client, LOCATION)


@pytest.mark.unit
class TestMoveCopyRevertPrimary:
    @pytest.mark.parametrize("source", ["/model.glb", "p1/model.glb"])
    def test_the_object_cannot_be_moved_or_renamed(self, af, source):
        with pytest.raises(VAMSGeneralErrorResponse) as error:
            af.move_file("db1", "a1", source, "/renamed.glb", CLAIMS)
        assert af.OWN_FILE_MOVE_REFUSAL in str(error.value)
        assert _live(af.test_client, LOCATION)

    def test_with_a_companion_the_companion_moves_as_before(self, af):
        _with_state(af, "companion")
        af.move_file("db1", "a1", "/model.glb", "/renamed.glb", CLAIMS)
        assert _live(af.test_client, "p1/model.glb/renamed.glb")
        assert _live(af.test_client, LOCATION)

    def test_the_object_copies_into_the_companion_folder_with_its_preview_and_aux(self, af):
        result = af.copy_file("db1", "a1", "/model.glb", "/copied.glb", None, None, CLAIMS)
        assert result.success
        assert _live(af.test_client, "p1/model.glb/copied.glb")
        assert _live(af.test_client, "p1/model.glb/copied.glb.previewFile.png")
        assert _live(af.test_client, "db1/p1/model.glb/copied.glb/preview/PotreeViewer/m.json", AUX_BUCKET)
        assert af.metadata_copies[0][2] == "model.glb" and af.metadata_copies[0][5] == "copied.glb"

    def test_a_copy_onto_the_object_is_refused_as_an_existing_destination(self, af):
        with pytest.raises(VAMSGeneralErrorResponse):
            af.copy_file("db1", "a1", "/sub/x.obj", "/model.glb", None, None, CLAIMS)
        assert af.test_client.get_object(Bucket=BUCKET, Key=LOCATION)["Body"].read() == b"content-p1/model.glb"

    def test_revert_the_object_by_its_own_key(self, af):
        first = _versions(af.test_client, LOCATION)[0]["VersionId"]
        af.test_client.put_object(Bucket=BUCKET, Key=LOCATION, Body=b"v2")
        result = af.revert_file_version("db1", "a1", "p1/model.glb", first, CLAIMS)
        assert result.success
        assert af.test_client.get_object(Bucket=BUCKET, Key=LOCATION)["Body"].read() == b"content-p1/model.glb"
        assert _live(af.test_client, "db1/p1/model.glb/sub/x.obj/preview/PotreeViewer/m.json", AUX_BUCKET)

    def test_set_the_objects_primary_type(self, af):
        af.set_primary_file("db1", "a1", "/model.glb", "primary", None, CLAIMS)
        head = af.test_client.head_object(Bucket=BUCKET, Key=LOCATION)
        assert head["Metadata"].get(af.VAMS_PRIMARY_TYPE_METADATA_KEY) == "primary"

    def test_delete_the_objects_auxiliary_preview_data_only(self, af):
        result = af.delete_auxiliary_preview_asset_files("db1", "a1", "/model.glb", CLAIMS)
        assert result.deletedCount == 1
        assert _live(af.test_client, "db1/p1/model.glb/sub/x.obj/preview/PotreeViewer/m.json", AUX_BUCKET)


@pytest.mark.unit
class TestVersionListingUnchanged:
    def test_a_snapshot_still_lists_its_companion_entries_only(self, af, monkeypatch):
        companion_version = _versions(af.test_client, COMPANION)[0]["VersionId"]
        monkeypatch.setattr(af, "validate_asset_version_exists", MagicMock())
        monkeypatch.setattr(af, "get_asset_file_versions", MagicMock(return_value={"files": [
            {"relativeKey": "sub/x.obj", "versionId": companion_version, "size": 1, "lastModified": "x"},
        ]}))
        request = af.ListAssetFilesRequestModel(assetVersionId="1", basic=False)
        response = af.list_asset_files_from_version("db1", "a1", af.test_holder["asset"], BUCKET, LOCATION, request)
        assert [(i.key, i.versionId) for i in response.items] == [(COMPANION, companion_version)]


@pytest.mark.unit
class TestPrefixOperationsStayInTheCompanionFolder:
    """D1: a prefix archive or delete is a folder operation; on the own file or its previews it is refused."""

    @pytest.mark.parametrize("path", ["/model.glb", "p1/model.glb", "/model.glb.previewFile.png"])
    @pytest.mark.parametrize("operation", ["archive", "delete"])
    def test_a_prefix_operation_on_the_own_file_is_refused_and_changes_nothing(self, af, path, operation):
        with pytest.raises(VAMSGeneralErrorResponse) as error:
            if operation == "archive":
                af.archive_file("db1", "a1", path, True, CLAIMS)
            else:
                af.delete_file("db1", "a1", path, True, True, CLAIMS)
        assert af.OWN_FILE_PREFIX_REFUSAL in str(error.value)
        for key in (LOCATION, OWN_PREVIEW, COMPANION) + NEIGHBOURS:
            assert _live(af.test_client, key), key
        assert af.metadata_deletes == []

    def test_the_prefix_archive_keeps_to_the_location_it_is_given(self, af):
        archived = af.archive_s3_prefix(BUCKET, LOCATION, "db1", "a1", location=LOCATION)
        assert sorted(archived) == sorted([LOCATION, OWN_PREVIEW, COMPANION])
        for key in NEIGHBOURS:
            assert _live(af.test_client, key), key

    def test_a_prefix_archive_of_a_companion_folder_reports_its_paths(self, af, monkeypatch):
        history = []
        monkeypatch.setattr(af, "write_archive_history", lambda db, asset, path, *rest: history.append(path))
        result = af.archive_file("db1", "a1", "/sub/", True, CLAIMS)
        assert result.affectedFiles == ["sub/x.obj"] and history == ["sub/x.obj"]
        assert not _live(af.test_client, COMPANION) and _live(af.test_client, LOCATION)

    def test_a_prefix_delete_of_a_companion_folder_deletes_its_files_metadata_only(self, af):
        result = af.delete_file("db1", "a1", "/sub/", True, True, CLAIMS)
        assert result.affectedFiles == ["sub/x.obj"] and af.metadata_deletes == ["sub/x.obj"]
        assert _live(af.test_client, LOCATION) and _live(af.test_client, OWN_PREVIEW)

    def test_with_a_companion_entry_a_prefix_on_the_file_name_names_the_entry_as_before(self, af):
        _with_state(af, "companion")
        af.archive_file("db1", "a1", "/model.glb", True, CLAIMS)
        assert not _live(af.test_client, COPY) and _live(af.test_client, LOCATION)

    def test_control_a_folder_assets_prefix_delete_is_unchanged(self, af):
        af.test_client.put_object(Bucket=BUCKET, Key="proj/a/sub/y.txt", Body=b"y")
        af.test_holder["asset"] = {"databaseId": "db1", "assetId": "a2", "bucketId": "b1",
                                   "assetLocation": {"Key": FOLDER_LOCATION}}
        result = af.delete_file("db1", "a2", "/sub/", True, True, CLAIMS)
        assert result.affectedFiles == ["sub/y.txt"] and af.metadata_deletes == ["sub/y.txt"]
        assert _live(af.test_client, "proj/a/x.txt")


@pytest.mark.unit
class TestCompanionFolderDeleteAuxData:
    def test_deleting_the_companion_folder_by_its_key_keeps_the_objects_viewer_data(self, af):
        """D2: the companion folder's files lose their viewer data; the object keeps the file and its data."""
        af.delete_file("db1", "a1", "p1/model.glb/", True, True, CLAIMS)
        assert _live(af.test_client, LOCATION) and not _live(af.test_client, COMPANION)
        assert _live(af.test_client, "db1/p1/model.glb/preview/PotreeViewer/m.json", AUX_BUCKET)
        assert _versions(af.test_client, "db1/p1/model.glb/sub/x.obj/preview/PotreeViewer/m.json", AUX_BUCKET) == []
