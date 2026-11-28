# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A delete or archive whose path resolves to the key the file list shows at `/` names the asset's top-level
folder, so it is refused like `/` and nothing changes: no version in the asset bucket, no viewer data in the
auxiliary bucket, no metadata or attribute row (#1943). That key is a folder location itself (its folder marker,
which every created asset has) or a single-file location's companion folder `{key}/`, named as a folder
(`isPrefix` true) or as a single key (`isPrefix` false). A real subfolder, including one named like the asset,
and a real file still delete and archive.

A path with a leading `/` is a path in the asset (`AssetFilePaths.request_key_for`, #1943), so for an asset at
`A/` the prefix `/A/` is the subfolder named like the asset (`A/A/`), not the location folder; the full key
`A/` names the location folder and is refused, as are `/` and an empty path. `/A` and `A` resolve to the
prefix `A/A`, which selects the subfolder named like the asset and any sibling key starting with `A/A`; that
is unchanged.

Layout (bucket `vams-guard`, versioned): asset `A` (database db1) at `A/` holds its folder marker `A/`,
`x.txt`, `sub/y.txt` and a root subfolder named like the asset, `A/z.txt` (key `A/A/z.txt`). Asset `A2` (db1)
sits below a parent folder, at `base/A2/`, and holds its marker, `x.txt` and `sub/y.txt`. Asset `L` (db1) has a
location stored with a leading `/`, `/lead/`, and holds the marker `lead/` and `x.txt`. Asset `M` (db1) was created
from one object: its location `m/model.glb` is not a folder, so its object's key is the location and still deletes
and archives; its companion folder `m/model.glb/` holds a marker and `sub/n.txt`. Every file has viewer data in the
auxiliary bucket `vams-guard-aux`, and each asset has an asset-level metadata row and attribute row (path `/`) and
file rows for `x.txt`.
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
from models.common import VAMSAuthorizationDeniedResponse, VAMSGeneralErrorResponse  # noqa: E402

BUCKET = "vams-guard"
AUX_BUCKET = "vams-guard-aux"
A_KEYS = ("A/", "A/x.txt", "A/sub/y.txt", "A/A/z.txt")
A2_KEYS = ("base/A2/", "base/A2/x.txt", "base/A2/sub/y.txt")
L_KEYS = ("lead/", "lead/x.txt")
M_KEYS = ("m/model.glb", "m/model.glb/", "m/model.glb/sub/n.txt")
ALL_KEYS = A_KEYS + A2_KEYS + L_KEYS + M_KEYS
LOCATIONS = {"A": "A/", "A2": "base/A2/", "L": "/lead/", "M": "m/model.glb"}
# Viewer data of every file, under {databaseId}/{file key}/
AUX_KEYS = tuple(f"db1/{key}/preview/viewer.json" for key in ALL_KEYS if not key.endswith("/"))
# Asset-level (path '/') and file metadata and attribute rows
METADATA_PATHS = ("/", "/x.txt")
CLAIMS = {"tokens": ["tester"], "roles": []}
DELETE_REFUSAL = ": Cannot delete the top-level asset folder"
ARCHIVE_REFUSAL = ": Cannot archive the top-level asset folder"
# The full-key forms of the location folder (a path with a leading `/` is a path in the asset, #1943)
REFUSED = [("A", "A/"), ("A2", "base/A2/")]
# The key listed at '/' by its full key: as a folder and as a single key, for every location kind
TOP_LEVEL_KEYS = [("A", "A/"), ("A2", "base/A2/"), ("L", "lead/"), ("M", "m/model.glb/")]


def _state(client):
    """Every (key, versionId, is delete marker) in the bucket, so a refusal can be shown to change nothing."""
    response = client.list_object_versions(Bucket=BUCKET)
    versions = [(v["Key"], v["VersionId"], False) for v in response.get("Versions", [])]
    markers = [(m["Key"], m["VersionId"], True) for m in response.get("DeleteMarkers", [])]
    return sorted(versions + markers)


def _live_keys(client):
    return sorted(o["Key"] for o in client.list_objects_v2(Bucket=BUCKET).get("Contents", []))


def _version_keys(client):
    return sorted({v["Key"] for v in client.list_object_versions(Bucket=BUCKET).get("Versions", [])})


def _aux_state(client):
    response = client.list_object_versions(Bucket=AUX_BUCKET)
    versions = [(v["Key"], v["VersionId"]) for v in response.get("Versions", [])]
    markers = [(m["Key"], m["VersionId"]) for m in response.get("DeleteMarkers", [])]
    return sorted(versions), sorted(markers)


def _rows(table):
    return sorted(tuple(sorted(item.items())) for item in table.scan()["Items"])


def _metadata_paths(af):
    return {dict(row)["databaseId:assetId:filePath"] for row in _rows(af.asset_file_metadata_table)}


def _everything(af):
    """The asset bucket, the auxiliary bucket and the metadata and attribute tables, to show a refusal changes
    nothing in any of them."""
    return (_state(af.test_client), _aux_state(af.test_client), _rows(af.asset_file_metadata_table),
            _rows(af.file_attribute_table))


def _create_metadata_table(resource, name, key):
    return resource.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": key, "KeyType": "HASH"},
                   {"AttributeName": "databaseId:assetId:filePath", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"},
                              {"AttributeName": "databaseId:assetId:filePath", "AttributeType": "S"}],
        GlobalSecondaryIndexes=[{
            "IndexName": "DatabaseIdAssetIdFilePathIndex",
            "KeySchema": [{"AttributeName": "databaseId:assetId:filePath", "KeyType": "HASH"},
                          {"AttributeName": key, "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def af(monkeypatch):
    """assetFiles against versioned moto buckets; each asset record names its location in LOCATIONS."""
    from backend.backend.handlers.assets import assetFiles as module

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        for bucket in (BUCKET, AUX_BUCKET):
            client.create_bucket(Bucket=bucket)
            client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        for key in ALL_KEYS:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"v1")
            client.put_object(Bucket=BUCKET, Key=key, Body=b"v2")
        for key in AUX_KEYS:
            client.put_object(Bucket=AUX_BUCKET, Key=key, Body=b"{}")
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        metadata_table = _create_metadata_table(dynamodb, "guard-file-metadata", "metadataKey")
        attribute_table = _create_metadata_table(dynamodb, "guard-file-attributes", "attributeKey")
        for asset_id in LOCATIONS:
            for path in METADATA_PATHS:
                metadata_table.put_item(Item={"metadataKey": "owner", "databaseId:assetId:filePath":
                                              f"db1:{asset_id}:{path}", "metadataValue": "team"})
                attribute_table.put_item(Item={"attributeKey": "label", "databaseId:assetId:filePath":
                                               f"db1:{asset_id}:{path}", "attributeValue": "x"})

        def asset(database_id, asset_id, *args, **kwargs):
            return {"databaseId": database_id, "assetId": asset_id, "bucketId": "b1",
                    "assetLocation": {"Key": LOCATIONS[asset_id]}}

        monkeypatch.setattr(module, "s3_client", client)
        monkeypatch.setattr(module, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(module, "asset_aux_bucket_name", AUX_BUCKET)
        monkeypatch.setattr(module, "get_asset_with_permissions", asset)
        monkeypatch.setattr(module, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET, "baseAssetsPrefix": ""})
        monkeypatch.setattr(module, "MAX_PARALLEL_S3_WORKERS", 1)
        monkeypatch.setattr(module, "send_subscription_email", MagicMock())
        monkeypatch.setattr(module, "asset_file_version_history_table", None)
        monkeypatch.setattr(module, "asset_file_metadata_table", metadata_table)
        monkeypatch.setattr(module, "file_attribute_table", attribute_table)
        monkeypatch.setattr(module, "delete_file_version_records", lambda *args, **kwargs: 0)
        module.test_client = client
        yield module


@pytest.mark.unit
class TestAPrefixThatNamesTheLocationFolderIsRefused:
    @pytest.mark.parametrize("asset_id,path", REFUSED)
    def test_a_prefix_delete_is_refused_and_no_version_is_removed(self, af, asset_id, path):
        before = _state(af.test_client)
        with pytest.raises(VAMSGeneralErrorResponse) as raised:
            af.delete_file("db1", asset_id, path, True, True, CLAIMS)
        assert str(raised.value).endswith(DELETE_REFUSAL)
        assert raised.value.status_code == 400
        assert _state(af.test_client) == before

    @pytest.mark.parametrize("asset_id,path", REFUSED)
    def test_a_prefix_archive_is_refused_and_nothing_is_archived(self, af, asset_id, path):
        before = _state(af.test_client)
        with pytest.raises(VAMSGeneralErrorResponse) as raised:
            af.archive_file("db1", asset_id, path, True, CLAIMS)
        assert str(raised.value).endswith(ARCHIVE_REFUSAL)
        assert raised.value.status_code == 400
        assert _state(af.test_client) == before

    def test_the_top_level_folder_is_still_refused(self, af):
        for path in ("/", ""):
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                af.delete_file("db1", "A", path, True, True, CLAIMS)
            assert str(raised.value).endswith(DELETE_REFUSAL)
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                af.archive_file("db1", "A", path, True, CLAIMS)
            assert str(raised.value).endswith(ARCHIVE_REFUSAL)

    @pytest.mark.parametrize("is_prefix", [True, False], ids=["prefix", "single-key"])
    def test_authorization_is_checked_before_the_path(self, af, monkeypatch, is_prefix):
        def deny(*args, **kwargs):
            raise VAMSAuthorizationDeniedResponse()

        monkeypatch.setattr(af, "get_asset_with_permissions", deny)
        before = _everything(af)
        for asset_id, path in TOP_LEVEL_KEYS:
            with pytest.raises(VAMSAuthorizationDeniedResponse):
                af.delete_file("db1", asset_id, path, is_prefix, True, CLAIMS)
            with pytest.raises(VAMSAuthorizationDeniedResponse):
                af.archive_file("db1", asset_id, path, is_prefix, CLAIMS)
        assert _everything(af) == before


@pytest.mark.unit
class TestTheKeyListedAtTheRootIsRefusedAsAFolderOrASingleKey:
    """The location folder's marker, or a single-file asset's companion folder `{key}/`, by its full key, with
    `isPrefix` true or false: 400, and the asset bucket, the auxiliary bucket and the metadata and attribute rows
    are unchanged."""

    @pytest.mark.parametrize("is_prefix", [True, False], ids=["prefix", "single-key"])
    @pytest.mark.parametrize("asset_id,path", TOP_LEVEL_KEYS)
    def test_a_delete_is_refused_and_nothing_changes(self, af, asset_id, path, is_prefix):
        before = _everything(af)
        with pytest.raises(VAMSGeneralErrorResponse) as raised:
            af.delete_file("db1", asset_id, path, is_prefix, True, CLAIMS)
        assert str(raised.value).endswith(DELETE_REFUSAL)
        assert raised.value.status_code == 400
        assert _everything(af) == before

    @pytest.mark.parametrize("is_prefix", [True, False], ids=["prefix", "single-key"])
    @pytest.mark.parametrize("asset_id,path", TOP_LEVEL_KEYS)
    def test_an_archive_is_refused_and_nothing_changes(self, af, asset_id, path, is_prefix):
        before = _everything(af)
        with pytest.raises(VAMSGeneralErrorResponse) as raised:
            af.archive_file("db1", asset_id, path, is_prefix, CLAIMS)
        assert str(raised.value).endswith(ARCHIVE_REFUSAL)
        assert raised.value.status_code == 400
        assert _everything(af) == before

    def test_a_root_file_delete_still_removes_its_own_rows_and_viewer_data_only(self, af):
        """The fixture's metadata and viewer data are live: a real file's delete removes its own, not the
        asset's."""
        af.delete_file("db1", "A", "/x.txt", False, True, CLAIMS)
        assert "db1:A:/x.txt" not in _metadata_paths(af) and "db1:A:/" in _metadata_paths(af)
        aux_keys = {key for key, _ in _aux_state(af.test_client)[0]}
        assert "db1/A/x.txt/preview/viewer.json" not in aux_keys
        assert "db1/A/sub/y.txt/preview/viewer.json" in aux_keys

    def test_the_marker_of_the_subfolder_named_like_the_asset_still_deletes_by_its_path(self, af):
        """'/A/' is the path of 'A/A/' (#1943), so its folder marker is a single key of a subfolder."""
        af.test_client.put_object(Bucket=BUCKET, Key="A/A/", Body=b"")
        af.delete_file("db1", "A", "/A/", False, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(ALL_KEYS)
        assert "db1:A:/" in _metadata_paths(af)

    def test_a_file_of_an_asset_with_a_leading_slash_location_still_deletes(self, af):
        af.delete_file("db1", "L", "/x.txt", False, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"lead/x.txt"})


@pytest.mark.unit
class TestARealSubfolderStillDeletesAndArchives:
    def test_a_subfolder_delete_removes_only_its_files(self, af):
        af.delete_file("db1", "A", "/sub/", True, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/sub/y.txt"})

    def test_a_subfolder_archive_archives_only_its_files(self, af):
        af.archive_file("db1", "A", "/sub/", True, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/sub/y.txt"})
        assert _version_keys(af.test_client) == sorted(ALL_KEYS)

    @pytest.mark.parametrize("path", ["A/A/", "/A/"], ids=["full-key", "path"])
    def test_the_subfolder_named_like_the_asset_deletes_by_its_full_key_or_its_path(self, af, path):
        af.delete_file("db1", "A", path, True, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/A/z.txt"})

    @pytest.mark.parametrize("path", ["A/A/", "/A/"], ids=["full-key", "path"])
    def test_the_subfolder_named_like_the_asset_archives_by_its_full_key_or_its_path(self, af, path):
        af.archive_file("db1", "A", path, True, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/A/z.txt"})

    def test_a_path_that_repeats_the_location_names_a_folder_inside_the_asset(self, af):
        """'/A/A/' is the path of 'A/A/A/' and '/base/A2/' of 'base/A2/base/A2/', which hold nothing: answered
        as a missing folder, never as the top-level asset folder, and nothing changes."""
        for asset_id, path in (("A", "/A/A/"), ("A2", "/base/A2/")):
            before = _state(af.test_client)
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                af.delete_file("db1", asset_id, path, True, True, CLAIMS)
            assert str(raised.value).endswith(": No files found under prefix.")
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                af.archive_file("db1", asset_id, path, True, CLAIMS)
            assert str(raised.value).endswith(": No files found under prefix.")
            assert _state(af.test_client) == before

    @pytest.mark.parametrize("path", ["/A", "A"])
    def test_the_asset_name_without_a_trailing_slash_selects_the_prefix_under_the_location(self, af, path):
        af.delete_file("db1", "A", path, True, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/A/z.txt"})

    def test_a_subfolder_of_an_asset_below_a_parent_folder_deletes(self, af):
        af.delete_file("db1", "A2", "/sub/", True, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"base/A2/sub/y.txt"})

    def test_a_single_file_delete_is_unchanged(self, af):
        af.delete_file("db1", "A", "/x.txt", False, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/x.txt"})

    def test_a_single_file_archive_is_unchanged(self, af):
        af.archive_file("db1", "A", "/x.txt", False, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"A/x.txt"})
        assert _version_keys(af.test_client) == sorted(ALL_KEYS)


@pytest.mark.unit
class TestTheObjectOfASingleObjectAssetIsNotGuarded:
    """The object's own key is the location here, so a request naming the object resolves to the location key
    and must still act on the object."""

    @pytest.mark.parametrize("path", ["/model.glb", "m/model.glb"], ids=["path", "full-key"])
    def test_the_object_deletes_by_its_path_or_its_key(self, af, path):
        af.delete_file("db1", "M", path, False, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb"})

    @pytest.mark.parametrize("path", ["/model.glb", "m/model.glb"], ids=["path", "full-key"])
    def test_the_object_archives_by_its_path_or_its_key(self, af, path):
        af.archive_file("db1", "M", path, False, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb"})
        assert _version_keys(af.test_client) == sorted(ALL_KEYS)

    @pytest.mark.parametrize("path", ["/sub/n.txt", "m/model.glb/sub/n.txt"], ids=["path", "full-key"])
    def test_a_companion_file_deletes_by_its_path_or_its_key(self, af, path):
        af.delete_file("db1", "M", path, False, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb/sub/n.txt"})

    @pytest.mark.parametrize("path", ["/sub/n.txt", "m/model.glb/sub/n.txt"], ids=["path", "full-key"])
    def test_a_companion_file_archives_by_its_path_or_its_key(self, af, path):
        af.archive_file("db1", "M", path, False, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb/sub/n.txt"})
        assert _version_keys(af.test_client) == sorted(ALL_KEYS)

    @pytest.mark.parametrize("path", ["/m/model.glb/", "/model.glb/"])
    def test_a_path_that_repeats_the_file_names_a_folder_inside_the_companion_folder(self, af, path):
        """With a leading '/' these are paths in the asset (#1943), not the companion folder: they name
        folders that hold nothing here, so the answer is a missing folder and nothing changes."""
        before = _everything(af)
        with pytest.raises(VAMSGeneralErrorResponse) as raised:
            af.delete_file("db1", "M", path, True, True, CLAIMS)
        assert "Cannot delete the top-level asset folder" not in str(raised.value)
        assert _everything(af) == before

    def test_a_companion_subfolder_deletes_with_a_prefix(self, af):
        af.delete_file("db1", "M", "/sub/", True, True, CLAIMS)
        assert _version_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb/sub/n.txt"})

    def test_a_companion_subfolder_archives_with_a_prefix(self, af):
        af.archive_file("db1", "M", "/sub/", True, CLAIMS)
        assert _live_keys(af.test_client) == sorted(set(ALL_KEYS) - {"m/model.glb/sub/n.txt"})
