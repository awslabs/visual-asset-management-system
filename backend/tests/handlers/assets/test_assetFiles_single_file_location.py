# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""File operations on a single-file asset act on the keys the asset holds, and prefix operations name folders.

An asset created with a ``bucketExistingKey`` that names an object has a location key with no trailing
``/``. The asset holds that object (``/model.glb``), its companion folder ``model.glb/`` (``/sub/x.obj``)
and its own previews (``/model.glb.previewFile.png``). Used as an S3 prefix, the key also matches keys
that only share its characters -- here ``model.glb.bak`` and ``model.glbx/inner.txt`` -- which belong to
something else. These tests run the listing, file info, archive, unarchive, delete, folder creation, move
and preview handling against a versioned moto bucket and check that only the asset's keys are listed,
resolved and changed. A companion entry named exactly like the object (``model.glb/model.glb``) is listed
one level down, at ``/model.glb/model.glb``; nothing is hidden. A prefix operation names a folder
(``sub/``), so ``sub.txt`` and ``subway.glb`` beside it are never selected.
"""

import os
from unittest.mock import patch

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
OWN_PREVIEW = "p1/model.glb.previewFile.png"
COMPANION = "p1/model.glb/sub/x.obj"
SHADOWED = "p1/model.glb/model.glb"
NEIGHBOURS = ("p1/model.glb.bak", "p1/model.glbx/inner.txt")
FOLDER_LOCATION = "proj/a/"
FOLDER_KEYS = ("proj/a/", "proj/a/x.txt", "proj/a/x.txt.previewFile.png",
               "proj/a/sub/y.txt", "proj/a/sub.txt", "proj/a/subway.glb")
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


def _neighbours_untouched(client):
    for key in NEIGHBOURS:
        assert _live(client, key), key
        assert len(_versions(client, key)) == 1, key


@pytest.fixture
def af(monkeypatch):
    """assetFiles against a versioned moto bucket; the asset record names the location under test."""
    from backend.backend.handlers.assets import assetFiles as module

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in (OWN_KEY, OWN_PREVIEW, COMPANION, SHADOWED) + NEIGHBOURS + FOLDER_KEYS + SIBLING_FOLDER_KEYS:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"content")

        asset = {"databaseId": "db1", "assetId": "a1", "bucketId": "b1",
                 "assetLocation": {"Key": FILE_LOCATION}}
        # The asset and registration tables, in memory: no other asset lies inside the location
        from tests.common.fileAttributionFakes import AttributionAssetTable, AttributionBucketsTable, registration
        monkeypatch.setattr(module, "asset_table", AttributionAssetTable([asset]))
        monkeypatch.setattr(module, "buckets_table", AttributionBucketsTable([registration("b1", BUCKET, "")]))
        monkeypatch.setattr(module, "s3_client", client)
        monkeypatch.setattr(module, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(module, "get_asset_with_permissions", lambda *args, **kwargs: asset)
        monkeypatch.setattr(module, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET,
                                               "baseAssetsPrefix": ""})
        monkeypatch.setattr(module, "asset_file_version_history_table", None)
        # moto serves one request at a time reliably; the handler's worker pool is not under test
        monkeypatch.setattr(module, "MAX_PARALLEL_S3_WORKERS", 1)
        for name in ("send_subscription_email", "delete_assetAuxiliary_files", "delete_file_metadata",
                     "delete_file_version_records", "move_auxiliary_files", "copy_auxiliary_files"):
            monkeypatch.setattr(module, name, lambda *args, **kwargs: None)
        module.test_client = client
        module.test_asset = asset
        yield module


def _folder_asset(af):
    af.test_asset["assetLocation"] = {"Key": FOLDER_LOCATION}


@pytest.mark.unit
class TestResolveAssetFilePath:
    @pytest.mark.parametrize("path,expected", [
        ("/model.glb", OWN_KEY), ("model.glb", OWN_KEY), (OWN_KEY, OWN_KEY),
        ("/model.glb.previewFile.png", OWN_PREVIEW), (OWN_PREVIEW, OWN_PREVIEW),
        ("/sub/x.obj", COMPANION), (COMPANION, COMPANION),
        ("/model.glb.bak", "p1/model.glb/model.glb.bak"),
        ("p1/model.glb.bak", "p1/model.glb/p1/model.glb.bak"),
        ("/new/", "p1/model.glb/new/"),
        ("/model.glb/model.glb", SHADOWED), (SHADOWED, SHADOWED),
        ("/model.glb/model.glb/model.glb", "p1/model.glb/model.glb/model.glb"),
    ])
    def test_every_path_resolves_inside_the_asset(self, af, path, expected):
        assert af.resolve_asset_file_path(FILE_LOCATION, path) == expected

    def test_the_asset_as_a_whole_is_no_single_file(self, af):
        with pytest.raises(af.VAMSGeneralErrorResponse) as e:
            af.resolve_asset_file_path(FILE_LOCATION, "/")
        assert "not part of this asset" in str(e.value)

    @pytest.mark.parametrize("path,expected", [
        ("/x.txt", "proj/a/x.txt"), ("x.txt", "proj/a/x.txt"), ("proj/a/x.txt", "proj/a/x.txt"),
        ("/", "proj/a/"), ("/sub/", "proj/a/sub/"),
    ])
    def test_a_folder_location_resolves_as_before(self, af, path, expected):
        assert af.resolve_asset_file_path(FOLDER_LOCATION, path) == expected


def _walk(af, params, basic_mode, include_archived=True):
    items, token = [], None
    while True:
        query = dict(params)
        if token:
            query["startingToken"] = token
        result = af.list_s3_objects_with_archive_status(
            BUCKET, FILE_LOCATION, query, include_archived=include_archived, basic_mode=basic_mode)
        items.extend(result["items"])
        token = result.get("NextToken")
        if not token:
            return items


@pytest.mark.unit
@pytest.mark.parametrize("basic_mode", [False, True], ids=["full", "basic"])
class TestListFiles:
    def test_the_object_its_previews_and_companion_folder_are_listed(self, af, basic_mode):
        items = _walk(af, {}, basic_mode)
        assert sorted((i["key"], i["relativePath"]) for i in items) == sorted([
            (OWN_KEY, "/model.glb"), (OWN_PREVIEW, "/model.glb.previewFile.png"), (COMPANION, "/sub/x.obj"),
            (SHADOWED, "/model.glb/model.glb")])

    def test_every_page_of_a_paged_listing_stays_inside_the_asset(self, af, basic_mode):
        items = _walk(af, {"pageSize": 1}, basic_mode, include_archived=False)
        assert sorted(i["key"] for i in items) == sorted([OWN_KEY, OWN_PREVIEW, COMPANION, SHADOWED])

    def test_a_real_key_further_down_the_chain_is_listed_one_level_deeper(self, af, basic_mode):
        af.test_client.put_object(Bucket=BUCKET, Key="p1/model.glb/model.glb/model.glb", Body=b"x")
        items = {i["key"]: i["relativePath"] for i in _walk(af, {}, basic_mode)}
        assert items[SHADOWED] == "/model.glb/model.glb"
        assert items["p1/model.glb/model.glb/model.glb"] == "/model.glb/model.glb/model.glb"

    def test_an_archived_neighbour_is_not_listed_and_an_archived_own_file_is(self, af, basic_mode):
        af.test_client.delete_object(Bucket=BUCKET, Key="p1/model.glb.bak")
        af.test_client.delete_object(Bucket=BUCKET, Key=OWN_KEY)
        items = _walk(af, {}, basic_mode)
        archived = [(i["key"], i["relativePath"]) for i in items if i["isArchived"]]
        assert archived == [(OWN_KEY, "/model.glb")]
        assert "p1/model.glb.bak" not in {i["key"] for i in items}

    @pytest.mark.parametrize("prefix,listed", [
        ("mod", [OWN_KEY, OWN_PREVIEW, SHADOWED]), ("/model.glb.p", [OWN_PREVIEW]),
        ("model.glb/", [SHADOWED]),
        ("sub/", [COMPANION]), ("/nothing", []),
    ])
    def test_a_prefix_filter_applies_to_the_relative_path(self, af, basic_mode, prefix, listed):
        items = _walk(af, {"prefix": prefix}, basic_mode)
        assert sorted(i["key"] for i in items) == sorted(listed)

    def test_control_a_folder_lists_its_keys_and_no_sibling_prefix(self, af, basic_mode):
        result = af.list_s3_objects_with_archive_status(
            BUCKET, FOLDER_LOCATION, {}, include_archived=True, basic_mode=basic_mode)
        assert sorted(i["relativePath"] for i in result["items"]) == [
            "/", "/sub.txt", "/sub/y.txt", "/subway.glb", "/x.txt", "/x.txt.previewFile.png"]


@pytest.mark.unit
class TestListAssetFiles:
    def test_the_own_file_carries_its_own_preview(self, af):
        response = af.list_asset_files("db1", "a1", {"pageSize": "100", "maxItems": "1000"}, CLAIMS)
        by_path = {item.relativePath: item for item in response.items}
        assert sorted(by_path) == ["/model.glb", "/model.glb/model.glb", "/sub/x.obj"]
        assert by_path["/model.glb"].previewFile == "/model.glb.previewFile.png"

    @pytest.mark.parametrize("basic", ["true", "false"])
    def test_an_asset_version_lists_the_keys_its_snapshot_names(self, af, basic):
        own_version = af.test_client.head_object(Bucket=BUCKET, Key=OWN_KEY)["VersionId"]
        companion_version = af.test_client.head_object(Bucket=BUCKET, Key=COMPANION)["VersionId"]
        preview_version = af.test_client.head_object(Bucket=BUCKET, Key=OWN_PREVIEW)["VersionId"]
        snapshot = {"files": [
            {"relativeKey": "model.glb", "versionId": own_version, "size": 7},
            {"relativeKey": "sub/x.obj", "versionId": companion_version, "size": 7},
            {"relativeKey": "model.glb.previewFile.png", "versionId": preview_version, "size": 7},
        ]}
        with patch.object(af, "get_asset_file_versions", return_value=snapshot):
            response = af.list_asset_files("db1", "a1", {"assetVersionId": "1", "basic": basic,
                                                         "pageSize": "100", "maxItems": "1000"}, CLAIMS)
        by_key = {item.key: item for item in response.items}
        assert sorted(by_key) == sorted([OWN_KEY, COMPANION])
        assert by_key[OWN_KEY].relativePath == "/model.glb"
        assert by_key[OWN_KEY].versionId == own_version
        assert by_key[OWN_KEY].previewFile == "/model.glb.previewFile.png"


@pytest.mark.unit
class TestPrefixHelpersStayInTheLocation:
    def test_permanent_delete_of_a_single_file_location_deletes_the_asset_keys_only(self, af):
        deleted = af.delete_s3_prefix_all_versions(BUCKET, FILE_LOCATION, location=FILE_LOCATION)
        assert sorted(deleted) == sorted([OWN_KEY, OWN_PREVIEW, COMPANION, SHADOWED])
        for key in (OWN_KEY, OWN_PREVIEW, COMPANION, SHADOWED):
            assert _versions(af.test_client, key) == [], key
        _neighbours_untouched(af.test_client)

    def test_permanent_delete_of_a_folder_location_keeps_sibling_prefixes(self, af):
        deleted = af.delete_s3_prefix_all_versions(BUCKET, "proj/a", location=FOLDER_LOCATION)
        assert sorted(deleted) == sorted(FOLDER_KEYS)
        for key in SIBLING_FOLDER_KEYS:
            assert _live(af.test_client, key), key

    def test_archive_of_a_single_file_location_archives_the_asset_keys_only(self, af):
        archived = af.archive_s3_prefix(BUCKET, FILE_LOCATION, "db1", "a1", location=FILE_LOCATION)
        assert sorted(archived) == sorted([OWN_KEY, OWN_PREVIEW, COMPANION, SHADOWED])
        _neighbours_untouched(af.test_client)


@pytest.mark.unit
class TestFileOperations:
    def test_archive_file_archives_the_object_and_its_preview_only(self, af):
        response = af.archive_file("db1", "a1", "/model.glb", False, CLAIMS)
        assert sorted(response.affectedFiles) == ["/model.glb", "/model.glb.previewFile.png"]
        assert not _live(af.test_client, OWN_KEY) and not _live(af.test_client, OWN_PREVIEW)
        assert _live(af.test_client, COMPANION)
        _neighbours_untouched(af.test_client)

    def test_delete_file_deletes_the_object_and_its_preview_only(self, af):
        response = af.delete_file("db1", "a1", "/model.glb", False, True, CLAIMS)
        assert sorted(response.affectedFiles) == ["/model.glb", "/model.glb.previewFile.png"]
        assert _versions(af.test_client, OWN_KEY) == [] and _versions(af.test_client, OWN_PREVIEW) == []
        assert _live(af.test_client, COMPANION)
        _neighbours_untouched(af.test_client)

    @pytest.mark.parametrize("path", ["/sub", "/sub/"])
    def test_a_prefix_delete_in_the_companion_folder_deletes_that_folder_only(self, af, path):
        response = af.delete_file("db1", "a1", path, True, True, CLAIMS)
        assert response.affectedFiles == ["sub/x.obj"]
        assert _versions(af.test_client, COMPANION) == []
        assert _live(af.test_client, OWN_KEY) and _live(af.test_client, OWN_PREVIEW)
        _neighbours_untouched(af.test_client)

    def test_the_companion_copy_named_like_the_object_is_reached_one_level_down(self, af):
        info = af.get_file_info("db1", "a1", "/model.glb/model.glb", False, CLAIMS)
        assert info.relativePath == "/model.glb/model.glb"
        response = af.archive_file("db1", "a1", "/model.glb/model.glb", False, CLAIMS)
        assert response.affectedFiles == ["/model.glb/model.glb"]
        assert not _live(af.test_client, SHADOWED) and _live(af.test_client, OWN_KEY)

    @pytest.mark.parametrize("path", ["/model.glb", "/model.glb/"])
    def test_a_prefix_delete_of_the_folder_named_like_the_file_takes_the_copy_listed_in_it(self, af, path):
        af.test_client.put_object(Bucket=BUCKET, Key="p1/model.glb/model.glb.previewFile.png", Body=b"x")
        af.test_client.put_object(Bucket=BUCKET, Key="p1/model.glb/model.glb/part.obj", Body=b"x")
        response = af.delete_file("db1", "a1", path, True, True, CLAIMS)
        assert sorted(response.affectedFiles) == [
            "model.glb/model.glb", "model.glb/model.glb.previewFile.png", "model.glb/part.obj"]
        assert _live(af.test_client, OWN_KEY) and _live(af.test_client, OWN_PREVIEW)
        assert _live(af.test_client, COMPANION)
        _neighbours_untouched(af.test_client)

    @pytest.mark.parametrize("operation", ["delete", "archive"])
    def test_the_folder_named_like_the_file_is_found_behind_many_keys_that_only_start_with_its_name(
            self, af, operation):
        """The folder /model.glb/ is listed under p1/model.glb/model.glb, where more than one page of
        p1/model.glb/model.glb.bak* keys sorts before p1/model.glb/model.glb/part.obj."""
        client = af.test_client
        for version in client.list_object_versions(Bucket=BUCKET, Prefix=SHADOWED).get("Versions", []):
            if version["Key"] == SHADOWED:
                client.delete_object(Bucket=BUCKET, Key=SHADOWED, VersionId=version["VersionId"])
        for i in range(1001):
            client.put_object(Bucket=BUCKET, Key=f"p1/model.glb/model.glb.bak{i:04}", Body=b"x")
        client.put_object(Bucket=BUCKET, Key="p1/model.glb/model.glb/part.obj", Body=b"x")
        if operation == "delete":
            response = af.delete_file("db1", "a1", "/model.glb/", True, True, CLAIMS)
        else:
            response = af.archive_file("db1", "a1", "/model.glb/", True, CLAIMS)
        assert [path.lstrip("/") for path in response.affectedFiles] == ["model.glb/part.obj"]
        assert not _live(client, "p1/model.glb/model.glb/part.obj")
        assert _live(client, "p1/model.glb/model.glb.bak0000") and _live(client, OWN_KEY)

    def test_unarchive_file_restores_the_object_and_its_preview(self, af):
        af.test_client.delete_object(Bucket=BUCKET, Key=OWN_KEY)
        af.test_client.delete_object(Bucket=BUCKET, Key=OWN_PREVIEW)
        response = af.unarchive_file("db1", "a1", "/model.glb", CLAIMS)
        assert response.affectedFiles == ["/model.glb", "/model.glb.previewFile.png"]
        assert _live(af.test_client, OWN_KEY) and _live(af.test_client, OWN_PREVIEW)

    def test_file_info_reads_the_object_and_names_its_preview(self, af):
        info = af.get_file_info("db1", "a1", "/model.glb", False, CLAIMS)
        assert info.relativePath == "/model.glb"
        assert info.previewFile == "/model.glb.previewFile.png"

    def test_file_info_reads_a_companion_entry(self, af):
        assert af.get_file_info("db1", "a1", "/sub/x.obj", False, CLAIMS).relativePath == "/sub/x.obj"

    def test_a_folder_is_created_in_the_companion_folder(self, af):
        from models.assetsV3 import CreateFolderRequestModel
        af.create_folder("db1", "a1", CreateFolderRequestModel(relativeKey="/new/"), CLAIMS)
        assert _live(af.test_client, "p1/model.glb/new/")

    def test_the_own_object_cannot_be_moved(self, af):
        with pytest.raises(af.VAMSGeneralErrorResponse) as e:
            af.move_file("db1", "a1", "/model.glb", "/renamed.glb", CLAIMS)
        assert "cannot be moved or renamed" in str(e.value)
        assert _live(af.test_client, OWN_KEY)

    def test_a_companion_entry_moves_within_the_companion_folder(self, af):
        af.move_file("db1", "a1", "/sub/x.obj", "/sub/y.obj", CLAIMS)
        assert _live(af.test_client, "p1/model.glb/sub/y.obj") and not _live(af.test_client, COMPANION)

    def test_copying_the_object_out_copies_its_own_preview(self, af):
        copied = af.process_preview_files(
            operation="copy", bucket=BUCKET, source_key=OWN_KEY, dest_key="proj/a/model.glb",
            base_key=FOLDER_LOCATION, source_location=FILE_LOCATION)
        assert copied == ["/model.glb.previewFile.png"]
        assert _live(af.test_client, "proj/a/model.glb.previewFile.png")

    def test_control_a_folder_asset_still_moves_its_preview(self, af):
        moved = af.process_preview_files(
            operation="move", bucket=BUCKET, source_key="proj/a/x.txt", dest_key="proj/a/y.txt",
            base_key=FOLDER_LOCATION)
        assert moved == ["/x.txt.previewFile.png", "/y.txt.previewFile.png"]


@pytest.mark.unit
class TestFolderPrefixOperationsNameAFolder:
    """A prefix delete or archive of '/sub' names the folder 'sub/', never 'sub.txt' or 'subway.glb'."""

    @pytest.mark.parametrize("path", ["/sub", "/sub/"])
    def test_a_prefix_delete_selects_the_folder_only(self, af, path):
        _folder_asset(af)
        response = af.delete_file("db1", "a1", path, True, True, CLAIMS)
        assert response.affectedFiles == ["sub/y.txt"]
        assert _live(af.test_client, "proj/a/sub.txt") and _live(af.test_client, "proj/a/subway.glb")

    @pytest.mark.parametrize("path", ["/sub", "/sub/"])
    def test_a_prefix_archive_selects_the_folder_only(self, af, path):
        _folder_asset(af)
        response = af.archive_file("db1", "a1", path, True, CLAIMS)
        assert response.affectedFiles == ["sub/y.txt"]
        assert _live(af.test_client, "proj/a/sub.txt") and _live(af.test_client, "proj/a/subway.glb")
