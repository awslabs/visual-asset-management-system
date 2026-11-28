# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Two rules for file operations on folder entries.

A folder marker (a key ending in `/`) has no viewer data of its own. Its viewer-data folder is that of the file named
like the folder (`{db}/A/sub/preview/` for the marker `A/sub/` is the data of the file `A/sub`), so deleting, reverting,
moving or copying the marker leaves every viewer-data object as it is (``common.assetLocations.viewer_data_scope``).

A file move or copy whose destination resolves to the entry the file list shows at `/` (the destination asset's
location folder, or a single-object asset's companion folder `{key}/`) is refused with `400` and nothing changes, as
a move of that entry is (test_assetFiles_top_level_move_revert.py). An asset has no object at that key when its
folder was written without a marker, or when nothing created the companion folder.

Layout (buckets `vams-guard` and `vams-guard-aux`, versioned; every asset key has two versions):
- `A` at `A/` holds its marker, `x.txt`, a file `sub` and a subfolder `sub/` with its marker and `sub/x`.
- `M` was created from one object, `m/model.glb`; its companion folder holds a marker, a file `sub`, and a subfolder
  `sub/` with its marker and `sub/n.txt`.
- `N` at `N/` and `B2` at `base/B2/` hold `x.txt` and `sub/x` but no folder marker; `L` has a location stored with a
  leading `/`, `/lead/`, and holds `x.txt` but no marker.
- `P` was created from one object, `p/model.glb`, and holds a companion file `n.txt` but no companion marker.

Every file has viewer data in the auxiliary bucket, and each asset has an asset-level metadata row and attribute row
(path `/`) and rows for `/x.txt` (`/n.txt` for `P`).
"""

import os
import sys

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

# Module-level imports populate the real handler packages before the root conftest's autouse fixture runs
from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402
from tests.common.fileAttributionFakes import (  # noqa: E402
    AttributionAssetTable, AttributionBucketsTable, asset_row, registration,
)

al = sys.modules['common.assetLocations']
fa = sys.modules['common.fileAttribution']

BUCKET = "vams-guard"
AUX_BUCKET = "vams-guard-aux"
KEYS = {
    "A": ("A/", "A/x.txt", "A/sub", "A/sub/", "A/sub/x"),
    "M": ("m/model.glb", "m/model.glb/", "m/model.glb/sub", "m/model.glb/sub/", "m/model.glb/sub/n.txt"),
    "N": ("N/x.txt", "N/sub/x"),
    "B2": ("base/B2/x.txt", "base/B2/sub/x"),
    "L": ("lead/x.txt",),
    "P": ("p/model.glb", "p/model.glb/n.txt"),
}
LOCATIONS = {"A": "A/", "M": "m/model.glb", "N": "N/", "B2": "base/B2/", "L": "/lead/", "P": "p/model.glb"}
ALL_KEYS = tuple(key for keys in KEYS.values() for key in keys)
ROWS = {asset_id: asset_row("db1", asset_id, "reg-1", location, BUCKET) for asset_id, location in LOCATIONS.items()}
# Viewer data of every file, under {databaseId}/{file key}/preview/
AUX_KEYS = tuple(f"db1/{key}/preview/viewer.json" for key in ALL_KEYS if not key.endswith("/"))
METADATA_PATHS = {asset_id: ("/", "/n.txt" if asset_id == "P" else "/x.txt") for asset_id in LOCATIONS}
CLAIMS = {"tokens": ["tester"], "roles": []}
MOVE_REFUSAL = ": Cannot move or rename a file onto the top-level asset folder"
COPY_REFUSAL = ": Cannot copy a file onto the top-level asset folder"
# A destination that resolves to the top-level entry of an asset that has no object there: (asset, source, destination)
TOP_LEVEL_DESTINATIONS = [
    ("N", "/x.txt", "/"), ("N", "/x.txt", "N/"), ("B2", "/x.txt", "/"), ("B2", "/x.txt", "base/B2/"),
    ("L", "/x.txt", "/"), ("L", "/x.txt", "lead/"), ("P", "/n.txt", "p/model.glb/"),
]
# Subfolder markers with a file named like the folder beside them: (asset, path, marker key, sibling file key)
SUBFOLDER_MARKERS = [
    ("A", "/sub/", "A/sub/", "A/sub"), ("A", "A/sub/", "A/sub/", "A/sub"),
    ("M", "/sub/", "m/model.glb/sub/", "m/model.glb/sub"),
]


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


def _versions(client, bucket):
    response = client.list_object_versions(Bucket=bucket)
    versions = [(v["Key"], v["VersionId"], False) for v in response.get("Versions", [])]
    markers = [(m["Key"], m["VersionId"], True) for m in response.get("DeleteMarkers", [])]
    return sorted(versions + markers)


def _rows(table):
    return sorted(tuple(sorted(item.items())) for item in table.scan()["Items"])


def _everything(af):
    """Both buckets (every version and delete marker) and both metadata tables."""
    return (_versions(af.test_client, BUCKET), _versions(af.test_client, AUX_BUCKET),
            _rows(af.asset_file_metadata_table), _rows(af.file_attribute_table))


def _tables(af):
    return _rows(af.asset_file_metadata_table), _rows(af.file_attribute_table)


def _live(client, bucket):
    return sorted(o["Key"] for o in client.list_objects_v2(Bucket=bucket).get("Contents", []))


def _paths(table):
    return sorted(dict(row)["databaseId:assetId:filePath"] for row in _rows(table))


def _older_version(client, key):
    response = client.list_object_versions(Bucket=BUCKET, Prefix=key)
    return next(v["VersionId"] for v in response["Versions"] if v["Key"] == key and not v["IsLatest"])


@pytest.fixture
def af(monkeypatch):
    from backend.backend.handlers.assets import assetFiles as module

    al.clear_nested_locations_cache()
    fa.clear_registration_cache()
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
        for asset_id, paths in METADATA_PATHS.items():
            for path in paths:
                metadata_table.put_item(Item={"metadataKey": "owner", "databaseId:assetId:filePath":
                                              f"db1:{asset_id}:{path}", "metadataValue": f"team{path}"})
                attribute_table.put_item(Item={"attributeKey": "label", "databaseId:assetId:filePath":
                                               f"db1:{asset_id}:{path}", "attributeValue": f"x{path}"})
        monkeypatch.setattr(al, "index_ready", lambda name: True)
        monkeypatch.setattr(module, "asset_table", AttributionAssetTable(list(ROWS.values())))
        monkeypatch.setattr(module, "buckets_table", AttributionBucketsTable([registration("reg-1", BUCKET, "/")]))
        monkeypatch.setattr(module, "s3_client", client)
        monkeypatch.setattr(module, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(module, "asset_aux_bucket_name", AUX_BUCKET)
        monkeypatch.setattr(module, "asset_file_metadata_table", metadata_table)
        monkeypatch.setattr(module, "file_attribute_table", attribute_table)
        monkeypatch.setattr(module, "get_asset_with_permissions",
                            lambda database_id, asset_id, *args, **kwargs: dict(ROWS[asset_id]))
        monkeypatch.setattr(module, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET, "baseAssetsPrefix": "/"})
        monkeypatch.setattr(module, "validate_cross_asset_permissions", lambda *args, **kwargs: True)
        monkeypatch.setattr(module, "check_quarantine_block", lambda *args, **kwargs: None)
        monkeypatch.setattr(module, "asset_file_version_history_table", None)
        monkeypatch.setattr(module, "MAX_PARALLEL_S3_WORKERS", 1)
        monkeypatch.setattr(module, "send_subscription_email", lambda *args, **kwargs: None)
        monkeypatch.setattr(module, "delete_file_version_records", lambda *args, **kwargs: 0)
        module.test_client = client
        yield module
    al.clear_nested_locations_cache()


@pytest.mark.unit
class TestAFolderMarkerHasNoViewerDataOfItsOwn:
    def test_the_scope_of_a_marker_holds_nothing_and_a_files_scope_is_unchanged(self):
        prefix, keep = al.viewer_data_scope("db1", "A/sub/")
        assert prefix == "db1/A/sub/preview/" and not keep("db1/A/sub/preview/viewer.json")
        prefix, keep = al.viewer_data_scope("db1", "A/sub")
        assert prefix == "db1/A/sub/preview/" and keep("db1/A/sub/preview/viewer.json")
        assert not al.viewer_data_scope("db1", "m/model.glb/")[1]("db1/m/model.glb/preview/viewer.json")

    @pytest.mark.parametrize("asset_id,path,marker,sibling", SUBFOLDER_MARKERS)
    def test_a_single_key_delete_of_a_subfolder_marker_keeps_the_same_named_files_viewer_data(
            self, af, asset_id, path, marker, sibling):
        aux_before, tables_before = _versions(af.test_client, AUX_BUCKET), _tables(af)
        af.delete_file("db1", asset_id, path, False, True, CLAIMS)
        assert marker not in {key for key, _, _ in _versions(af.test_client, BUCKET)}
        assert sibling in _live(af.test_client, BUCKET)
        assert _versions(af.test_client, AUX_BUCKET) == aux_before
        assert _tables(af) == tables_before

    @pytest.mark.parametrize("asset_id,path,marker,sibling", SUBFOLDER_MARKERS)
    def test_a_revert_of_a_subfolder_marker_keeps_the_same_named_files_viewer_data(
            self, af, asset_id, path, marker, sibling):
        aux_before, tables_before = _versions(af.test_client, AUX_BUCKET), _tables(af)
        af.revert_file_version("db1", asset_id, path, _older_version(af.test_client, marker), CLAIMS)
        assert af.test_client.get_object(Bucket=BUCKET, Key=marker)["Body"].read() == b"v1"
        assert _versions(af.test_client, AUX_BUCKET) == aux_before
        assert _tables(af) == tables_before

    def test_a_move_of_a_subfolder_marker_moves_no_viewer_data(self, af):
        aux_before = _versions(af.test_client, AUX_BUCKET)
        af.move_file("db1", "A", "/sub/", "/other/", CLAIMS)
        assert "A/other/" in _live(af.test_client, BUCKET) and "A/sub/" not in _live(af.test_client, BUCKET)
        assert _versions(af.test_client, AUX_BUCKET) == aux_before

    def test_a_copy_of_a_subfolder_marker_copies_no_viewer_data(self, af):
        aux_before = _versions(af.test_client, AUX_BUCKET)
        af.copy_file("db1", "A", "/sub/", "/other/", None, None, CLAIMS)
        assert {"A/other/", "A/sub/"} <= set(_live(af.test_client, BUCKET))
        assert _versions(af.test_client, AUX_BUCKET) == aux_before


@pytest.mark.unit
class TestFilesStillTakeTheirViewerData:
    def test_a_single_key_delete_of_the_file_named_like_the_folder_removes_its_own_viewer_data(self, af):
        af.delete_file("db1", "A", "/sub", False, True, CLAIMS)
        aux = _live(af.test_client, AUX_BUCKET)
        assert "db1/A/sub/preview/viewer.json" not in aux and "db1/A/sub/x/preview/viewer.json" in aux

    @pytest.mark.parametrize("asset_id,path,folder,sibling", [
        ("A", "/sub/", "A/sub/", "A/sub"), ("A", "A/sub/", "A/sub/", "A/sub"),
        ("M", "/sub/", "m/model.glb/sub/", "m/model.glb/sub"),
    ])
    def test_a_prefix_delete_of_a_subfolder_keeps_the_same_named_files_viewer_data(
            self, af, asset_id, path, folder, sibling):
        """The folder's base `{db}/{folder}` also holds the viewer data of the file named like the folder."""
        assets_before, aux_before = _versions(af.test_client, BUCKET), _versions(af.test_client, AUX_BUCKET)
        af.delete_file("db1", asset_id, path, True, True, CLAIMS)
        assert _versions(af.test_client, BUCKET) == [v for v in assets_before if not v[0].startswith(folder)]
        assert _versions(af.test_client, AUX_BUCKET) == [
            v for v in aux_before if not v[0].startswith(f"db1/{folder}") or v[0].startswith(f"db1/{sibling}/preview/")]
        assert f"db1/{sibling}/preview/viewer.json" in _live(af.test_client, AUX_BUCKET)

    def test_the_folder_keep_takes_the_folders_files_and_leaves_the_same_named_file(self):
        keep = al.folder_aux_keep("db1", "A/sub/")
        assert keep("db1/A/sub/x/preview/viewer.json") and not keep("db1/A/sub/preview/viewer.json")

    @pytest.mark.parametrize("asset_id,path,key", [
        ("A", "/sub", "A/sub"), ("A", "/sub/x", "A/sub/x"), ("M", "/sub/n.txt", "m/model.glb/sub/n.txt"),
    ])
    def test_a_file_revert_removes_only_its_own_viewer_data(self, af, asset_id, path, key):
        aux_before = set(_live(af.test_client, AUX_BUCKET))
        af.revert_file_version("db1", asset_id, path, _older_version(af.test_client, key), CLAIMS)
        assert aux_before - set(_live(af.test_client, AUX_BUCKET)) == {f"db1/{key}/preview/viewer.json"}

    def test_a_file_move_takes_its_viewer_data(self, af):
        af.move_file("db1", "A", "/sub", "/sub2", CLAIMS)
        aux = _live(af.test_client, AUX_BUCKET)
        assert "db1/A/sub2/preview/viewer.json" in aux and "db1/A/sub/preview/viewer.json" not in aux


@pytest.mark.unit
class TestAMoveOrCopyOntoTheTopLevelEntryIsRefused:
    @pytest.mark.parametrize("asset_id,source,dest", TOP_LEVEL_DESTINATIONS)
    def test_the_move_is_refused_and_nothing_changes(self, af, asset_id, source, dest):
        before = _everything(af)
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.move_file("db1", asset_id, source, dest, CLAIMS)
        assert str(raised.value).endswith(MOVE_REFUSAL)
        assert raised.value.status_code == 400
        assert _everything(af) == before

    @pytest.mark.parametrize("asset_id,source,dest", TOP_LEVEL_DESTINATIONS)
    def test_the_copy_is_refused_and_nothing_changes(self, af, asset_id, source, dest):
        before = _everything(af)
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.copy_file("db1", asset_id, source, dest, None, None, CLAIMS)
        assert str(raised.value).endswith(COPY_REFUSAL)
        assert raised.value.status_code == 400
        assert _everything(af) == before

    @pytest.mark.parametrize("dest_asset,dest", [("N", "/"), ("N", "N/"), ("P", "p/model.glb/")])
    def test_a_copy_to_another_assets_top_level_entry_is_refused(self, af, dest_asset, dest):
        before = _everything(af)
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.copy_file("db1", "A", "/x.txt", dest, dest_asset, None, CLAIMS)
        assert str(raised.value).endswith(COPY_REFUSAL)
        assert _everything(af) == before

    def test_authorization_is_checked_before_the_path(self, af, monkeypatch):
        def deny(*args, **kwargs):
            raise af.VAMSAuthorizationDeniedResponse()

        monkeypatch.setattr(af, "get_asset_with_permissions", deny)
        before = _everything(af)
        for asset_id, source, dest in TOP_LEVEL_DESTINATIONS:
            with pytest.raises(af.VAMSAuthorizationDeniedResponse):
                af.move_file("db1", asset_id, source, dest, CLAIMS)
            with pytest.raises(af.VAMSAuthorizationDeniedResponse):
                af.copy_file("db1", asset_id, source, dest, None, None, CLAIMS)
        assert _everything(af) == before


@pytest.mark.unit
class TestFilesStillMoveAndCopy:
    @pytest.mark.parametrize("asset_id,source,dest,new_key", [
        ("N", "/x.txt", "/sub/x.txt", "N/sub/x.txt"), ("B2", "base/B2/x.txt", "/y.txt", "base/B2/y.txt"),
        ("L", "/x.txt", "/y.txt", "lead/y.txt"), ("P", "/n.txt", "/sub/n.txt", "p/model.glb/sub/n.txt"),
    ])
    def test_a_move_writes_the_file_with_its_viewer_data_and_metadata(self, af, asset_id, source, dest, new_key):
        af.move_file("db1", asset_id, source, dest, CLAIMS)
        assert new_key in _live(af.test_client, BUCKET)
        assert f"db1/{new_key}/preview/viewer.json" in _live(af.test_client, AUX_BUCKET)
        assert f"db1:{asset_id}:/" in _paths(af.asset_file_metadata_table)
        if asset_id != "L":  # a location stored with a leading '/' keys its rows by another path today
            assert f"db1:{asset_id}:{dest if dest.startswith('/') else '/' + dest}" in _paths(
                af.asset_file_metadata_table)

    @pytest.mark.parametrize("asset_id,source,dest,new_key", [
        ("N", "/x.txt", "/y.txt", "N/y.txt"), ("P", "/n.txt", "/m.txt", "p/model.glb/m.txt"),
    ])
    def test_a_copy_within_the_asset_writes_the_copy(self, af, asset_id, source, dest, new_key):
        af.copy_file("db1", asset_id, source, dest, None, None, CLAIMS)
        assert new_key in _live(af.test_client, BUCKET)
        assert f"db1/{new_key}/preview/viewer.json" in _live(af.test_client, AUX_BUCKET)

    def test_a_copy_to_another_asset_writes_the_copy(self, af):
        af.copy_file("db1", "A", "/x.txt", "/from-a.txt", "N", None, CLAIMS)
        assert "N/from-a.txt" in _live(af.test_client, BUCKET)
        assert "db1:N:/from-a.txt" in _paths(af.asset_file_metadata_table)
