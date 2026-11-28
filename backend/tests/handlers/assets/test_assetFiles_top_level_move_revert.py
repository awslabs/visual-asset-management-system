# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A file move or a version revert whose source resolves to the key the file list shows at `/` names the asset's
top-level folder, so it is refused with `400` and nothing changes, as a delete or archive of that key is
(test_assetFiles_location_folder_guard.py): a folder location itself, named by its full key or by `/`, or a
single-file location's companion folder `{key}/`. The permission check still runs first. Files and subfolder files
still move and revert, with their viewer data and their metadata.

Layout (buckets `vams-guard` and `vams-guard-aux`, versioned; every asset key has two versions): asset `A`
(database db1) at `A/` holds its folder marker `A/`, `x.txt`, `sub/x` and a root subfolder named like the asset,
`A/z.txt` (key `A/A/z.txt`). Asset `A2` sits below a parent folder, at `base/A2/`, with its marker, `x.txt` and
`sub/x`. Asset `L` has a location stored with a leading `/`, `/lead/`, with the marker `lead/` and `x.txt`. Asset
`M` was created from one object, `m/model.glb`; its companion folder `m/model.glb/` holds a marker and `sub/n.txt`.
Every file has viewer data in the auxiliary bucket, and each asset has an asset-level metadata row and attribute
row (path `/`) and rows for `/x.txt`.
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
A_KEYS = ("A/", "A/x.txt", "A/sub/x", "A/A/z.txt")
A2_KEYS = ("base/A2/", "base/A2/x.txt", "base/A2/sub/x")
L_KEYS = ("lead/", "lead/x.txt")
M_KEYS = ("m/model.glb", "m/model.glb/", "m/model.glb/sub/n.txt")
ALL_KEYS = A_KEYS + A2_KEYS + L_KEYS + M_KEYS
ROWS = {
    "A": asset_row("db1", "A", "reg-1", "A/", BUCKET),
    "A2": asset_row("db1", "A2", "reg-1", "base/A2/", BUCKET),
    "L": asset_row("db1", "L", "reg-1", "/lead/", BUCKET),
    "M": asset_row("db1", "M", "reg-1", "m/model.glb", BUCKET),
}
# Viewer data of every file, under {databaseId}/{file key}/preview/
AUX_KEYS = tuple(f"db1/{key}/preview/viewer.json" for key in ALL_KEYS if not key.endswith("/"))
METADATA_PATHS = ("/", "/x.txt")
CLAIMS = {"tokens": ["tester"], "roles": []}
MOVE_REFUSAL = ": Cannot move or rename the top-level asset folder"
REVERT_REFUSAL = ": Cannot revert the top-level asset folder"
# The top-level entry by its full key and by '/', for a folder location, one below a parent folder, one stored
# with a leading '/', and a single-file location's companion folder
TOP_LEVEL_SOURCES = [
    ("A", "A/"), ("A", "/"), ("A2", "base/A2/"), ("A2", "/"), ("L", "lead/"), ("L", "/"), ("M", "m/model.glb/"),
]
LOCATION_KEYS = {"A": "A/", "A2": "base/A2/", "L": "lead/", "M": "m/model.glb/"}


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
        for asset_id in ROWS:
            for path in METADATA_PATHS:
                metadata_table.put_item(Item={"metadataKey": "owner", "databaseId:assetId:filePath":
                                              f"db1:{asset_id}:{path}", "metadataValue": "team"})
                attribute_table.put_item(Item={"attributeKey": "label", "databaseId:assetId:filePath":
                                               f"db1:{asset_id}:{path}", "attributeValue": "x"})
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
        monkeypatch.setattr(module, "asset_file_version_history_table", None)
        monkeypatch.setattr(module, "MAX_PARALLEL_S3_WORKERS", 1)
        monkeypatch.setattr(module, "send_subscription_email", lambda *args, **kwargs: None)
        module.test_client = client
        yield module
    al.clear_nested_locations_cache()


@pytest.mark.unit
class TestAMoveOfTheTopLevelEntryIsRefused:
    @pytest.mark.parametrize("asset_id,path", TOP_LEVEL_SOURCES)
    def test_the_move_is_refused_and_nothing_changes(self, af, asset_id, path):
        before = _everything(af)
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.move_file("db1", asset_id, path, "/moved.txt", CLAIMS)
        assert str(raised.value).endswith(MOVE_REFUSAL)
        assert raised.value.status_code == 400
        assert _everything(af) == before

    def test_authorization_is_checked_before_the_path(self, af, monkeypatch):
        def deny(*args, **kwargs):
            raise af.VAMSAuthorizationDeniedResponse()

        monkeypatch.setattr(af, "get_asset_with_permissions", deny)
        before = _everything(af)
        for asset_id, path in TOP_LEVEL_SOURCES:
            with pytest.raises(af.VAMSAuthorizationDeniedResponse):
                af.move_file("db1", asset_id, path, "/moved.txt", CLAIMS)
        assert _everything(af) == before


@pytest.mark.unit
class TestARevertOfTheTopLevelEntryIsRefused:
    @pytest.mark.parametrize("asset_id,path", TOP_LEVEL_SOURCES)
    def test_the_revert_is_refused_and_nothing_changes(self, af, asset_id, path):
        version_id = _older_version(af.test_client, LOCATION_KEYS[asset_id])
        before = _everything(af)
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.revert_file_version("db1", asset_id, path, version_id, CLAIMS)
        assert str(raised.value).endswith(REVERT_REFUSAL)
        assert raised.value.status_code == 400
        assert _everything(af) == before

    def test_authorization_is_checked_before_the_path(self, af, monkeypatch):
        def deny(*args, **kwargs):
            raise af.VAMSAuthorizationDeniedResponse()

        version_id = _older_version(af.test_client, "A/")
        monkeypatch.setattr(af, "get_asset_with_permissions", deny)
        before = _everything(af)
        for path in ("A/", "/"):
            with pytest.raises(af.VAMSAuthorizationDeniedResponse):
                af.revert_file_version("db1", "A", path, version_id, CLAIMS)
        assert _everything(af) == before


@pytest.mark.unit
class TestFilesStillMoveAndRevert:
    def test_a_root_file_moves_into_a_subfolder_with_its_viewer_data_and_metadata(self, af):
        af.move_file("db1", "A", "/x.txt", "/sub/x.txt", CLAIMS)
        assert "A/sub/x.txt" in _live(af.test_client, BUCKET) and "A/x.txt" not in _live(af.test_client, BUCKET)
        aux = _live(af.test_client, AUX_BUCKET)
        assert "db1/A/sub/x.txt/preview/viewer.json" in aux and "db1/A/x.txt/preview/viewer.json" not in aux
        assert "db1:A:/sub/x.txt" in _paths(af.asset_file_metadata_table)
        assert "db1:A:/x.txt" not in _paths(af.asset_file_metadata_table)
        assert "db1:A:/" in _paths(af.asset_file_metadata_table)

    @pytest.mark.parametrize("asset_id,path", [("A", "/sub/x"), ("A", "A/sub/x"), ("A2", "/sub/x")])
    def test_a_subfolder_file_moves_to_another_folder(self, af, asset_id, path):
        location = LOCATION_KEYS[asset_id]
        af.move_file("db1", asset_id, path, "/other/x", CLAIMS)
        live = _live(af.test_client, BUCKET)
        assert f"{location}other/x" in live and f"{location}sub/x" not in live
        assert f"db1/{location}other/x/preview/viewer.json" in _live(af.test_client, AUX_BUCKET)

    def test_a_file_of_an_asset_with_a_leading_slash_location_moves(self, af):
        af.move_file("db1", "L", "/x.txt", "/y.txt", CLAIMS)
        assert "lead/y.txt" in _live(af.test_client, BUCKET) and "lead/x.txt" not in _live(af.test_client, BUCKET)

    def test_a_companion_file_moves(self, af):
        af.move_file("db1", "M", "/sub/n.txt", "/sub/o.txt", CLAIMS)
        live = _live(af.test_client, BUCKET)
        assert "m/model.glb/sub/o.txt" in live and "m/model.glb/sub/n.txt" not in live
        assert "db1/m/model.glb/preview/viewer.json" in _live(af.test_client, AUX_BUCKET)

    @pytest.mark.parametrize("asset_id,path,key", [
        ("A", "/x.txt", "A/x.txt"), ("A", "/sub/x", "A/sub/x"), ("A2", "base/A2/x.txt", "base/A2/x.txt"),
        ("M", "/sub/n.txt", "m/model.glb/sub/n.txt"),
    ])
    def test_a_file_reverts_and_only_its_viewer_data_is_removed(self, af, asset_id, path, key):
        aux_before = set(_live(af.test_client, AUX_BUCKET))
        af.revert_file_version("db1", asset_id, path, _older_version(af.test_client, key), CLAIMS)
        response = af.test_client.get_object(Bucket=BUCKET, Key=key)
        assert response["Body"].read() == b"v1"
        assert aux_before - set(_live(af.test_client, AUX_BUCKET)) == {f"db1/{key}/preview/viewer.json"}
