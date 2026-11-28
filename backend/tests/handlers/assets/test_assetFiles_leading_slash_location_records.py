# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A delete or archive in an asset whose stored location starts with ``/`` clears the file's own records (#2065).

The file routes find an asset's files under its location without the leading ``/``: for a stored ``/scans/``,
``/x.txt`` is the key ``scans/x.txt``, and its metadata, attributes and version rows are kept under ``/x.txt``.
The record cleanup of a delete, and the history row of an archive, built the path from the stored location as
written: ``scans/x.txt`` does not start with ``/scans/``, so the path became ``/scans/x.txt``. The deleted
file's rows stayed, and the rows at ``/scans/x.txt`` (the file ``scans/scans/x.txt`` of a subfolder named like
the location) were deleted instead. The path now comes from the location without its leading ``/``, as the
file routes read it.

Each case runs the real ``delete_file`` or ``archive_file`` against a versioned moto bucket and moto metadata,
attribute and version tables, and compares what is left in all three tables. A location without a leading
``/`` and one under a bucket base prefix are controls that behave the same before and after.
"""

import boto3
import pytest
from moto import mock_aws

from backend.backend.handlers.assets import assetFiles as af  # noqa: E402
from backend.backend.common.downloadEligibility import asset_relative_key  # noqa: E402
from tests.common.fileAttributionFakes import AttributionAssetTable, AttributionBucketsTable, registration
from tests.handlers.assets.test_asset_version_entry_keys import _versions_table  # noqa: E402

BUCKET = "vams-test-assets"
DB = "db1"
CLAIMS = {"tokens": ["alice"], "roles": []}
TABLE_KEYS = {"metadata": "metadataKey", "attributes": "attributeKey"}

# (stored location, the registration's base prefix)
NORMAL = ("A1/", "")
LEADING_SLASH = ("/scans/", "")
BASE_PREFIX = ("assets/B1/", "assets/")
LEADING_SLASH_BASE_PREFIX = ("/assets/C1/", "assets/")
LOCATIONS = [
    pytest.param(NORMAL, id="location"),
    pytest.param(LEADING_SLASH, id="leading-slash-location"),
    pytest.param(BASE_PREFIX, id="base-prefix-location"),
    pytest.param(LEADING_SLASH_BASE_PREFIX, id="leading-slash-base-prefix-location"),
]


def _files(location):
    """Each file's key and the path it is listed at. The last one sits in a subfolder named like the location."""
    folder = location.lstrip("/")
    return {
        "/x.txt": folder + "x.txt",
        "/sub/y.txt": folder + "sub/y.txt",
        "/keep.txt": folder + "keep.txt",
        "/" + folder + "x.txt": folder + folder + "x.txt",
    }


def _record_table(dynamodb, name, row_key):
    dynamodb.create_table(
        TableName=f"test-{name}", BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": row_key, "KeyType": "HASH"},
                   {"AttributeName": "databaseId:assetId:filePath", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": a, "AttributeType": "S"} for a in
                              (row_key, "databaseId:assetId:filePath", "databaseId:assetId")],
        GlobalSecondaryIndexes=[
            {"IndexName": "DatabaseIdAssetIdIndex", "Projection": {"ProjectionType": "ALL"},
             "KeySchema": [{"AttributeName": "databaseId:assetId", "KeyType": "HASH"},
                           {"AttributeName": row_key, "KeyType": "RANGE"}]},
            {"IndexName": "DatabaseIdAssetIdFilePathIndex", "Projection": {"ProjectionType": "ALL"},
             "KeySchema": [{"AttributeName": "databaseId:assetId:filePath", "KeyType": "HASH"},
                           {"AttributeName": row_key, "KeyType": "RANGE"}]}])
    return dynamodb.Table(f"test-{name}")


@pytest.fixture
def asset(request, monkeypatch):
    """One folder asset at the parametrized location, its files in S3 and the records of each file."""
    location, base_prefix = request.param
    asset_id = location.strip("/").rsplit("/", 1)[-1]
    files = _files(location)
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in files.values():
            client.put_object(Bucket=BUCKET, Key=key, Body=b"content")

        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        tables = {name: _record_table(dynamodb, name, row_key) for name, row_key in TABLE_KEYS.items()}
        for name, row_key in TABLE_KEYS.items():
            for path in list(files) + ["/"]:
                tables[name].put_item(Item={row_key: "k", "databaseId:assetId:filePath": ":".join((DB, asset_id, path)),
                                            "databaseId:assetId": ":".join((DB, asset_id))})
        tables["versions"] = _versions_table(
            [(DB, asset_id, version, path.lstrip("/"), f"v{version}") for version in ("1", "2") for path in files])

        row = {"databaseId": DB, "assetId": asset_id, "bucketId": "b1", "assetLocation": {"Key": location}}
        monkeypatch.setattr(af, "asset_table", AttributionAssetTable([row]))
        monkeypatch.setattr(af, "buckets_table", AttributionBucketsTable([registration("b1", BUCKET, base_prefix)]))
        monkeypatch.setattr(af, "s3_client", client)
        monkeypatch.setattr(af, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(af, "get_asset_with_permissions", lambda *args, **kwargs: row)
        monkeypatch.setattr(af, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET,
                                               "baseAssetsPrefix": base_prefix})
        monkeypatch.setattr(af, "asset_file_metadata_table", tables["metadata"])
        monkeypatch.setattr(af, "file_attribute_table", tables["attributes"])
        monkeypatch.setattr(af, "asset_version_files_table", tables["versions"])
        monkeypatch.setattr(af, "asset_file_version_history_table", None)
        monkeypatch.setattr(af, "MAX_PARALLEL_S3_WORKERS", 1)
        for name in ("send_subscription_email", "delete_assetAuxiliary_files", "delete_auxiliary_files_of_keys"):
            monkeypatch.setattr(af, name, lambda *args, **kwargs: None)
        history = []
        monkeypatch.setattr(af, "write_archive_history",
                            lambda db, a, path, version_id, user: history.append(path))
        yield {"id": asset_id, "location": location, "files": files, "tables": tables, "history": history,
               "s3": client}


def _record_paths(asset):
    """What is left: the metadata and attribute rows by path, and the version rows by version and file."""
    prefix = ":".join((DB, asset["id"], ""))
    left = {name: sorted(item["databaseId:assetId:filePath"][len(prefix):]
                         for item in asset["tables"][name].scan()["Items"]) for name in TABLE_KEYS}
    left["versions"] = sorted((item["databaseId:assetId:assetVersionId"].rsplit(":", 1)[-1], item["fileKey"])
                              for item in asset["tables"]["versions"].scan()["Items"])
    return left


def _expected_without(asset, *paths):
    kept = [path for path in asset["files"] if path not in paths]
    return {"metadata": sorted(kept + ["/"]), "attributes": sorted(kept + ["/"]),
            "versions": sorted((version, path.lstrip("/")) for version in ("1", "2") for path in kept)}


def _keys(asset):
    return sorted(version["Key"] for version in
                  asset["s3"].list_object_versions(Bucket=BUCKET).get("Versions", []))


@pytest.mark.unit
class TestDeleteClearsTheFilesOwnRecords:
    @pytest.mark.parametrize("asset", LOCATIONS, indirect=True)
    def test_a_file_deleted_by_its_path(self, asset):
        af.delete_file(DB, asset["id"], "/x.txt", False, True, CLAIMS)

        assert asset["files"]["/x.txt"] not in _keys(asset)
        assert _record_paths(asset) == _expected_without(asset, "/x.txt")

    @pytest.mark.parametrize("asset", LOCATIONS, indirect=True)
    def test_a_file_deleted_by_its_full_key(self, asset):
        af.delete_file(DB, asset["id"], asset["files"]["/x.txt"], False, True, CLAIMS)

        assert asset["files"]["/x.txt"] not in _keys(asset)
        assert _record_paths(asset) == _expected_without(asset, "/x.txt")

    @pytest.mark.parametrize("asset", LOCATIONS, indirect=True)
    def test_a_folder_deleted_by_its_path(self, asset):
        af.delete_file(DB, asset["id"], "/sub/", True, True, CLAIMS)

        assert asset["files"]["/sub/y.txt"] not in _keys(asset)
        assert _record_paths(asset) == _expected_without(asset, "/sub/y.txt")


@pytest.mark.unit
class TestArchiveRecordsTheFilesOwnPath:
    @pytest.mark.parametrize("asset", LOCATIONS, indirect=True)
    def test_a_file_archive(self, asset):
        af.archive_file(DB, asset["id"], "/x.txt", False, CLAIMS)

        assert asset["history"] == ["/x.txt"]

    @pytest.mark.parametrize("asset", LOCATIONS, indirect=True)
    def test_a_folder_archive(self, asset):
        af.archive_file(DB, asset["id"], "/sub/", True, CLAIMS)

        assert asset["history"] == ["/sub/y.txt"]


@pytest.mark.unit
class TestTheListedPathOfAKey:
    @pytest.mark.parametrize("location, key, path", [
        ("/scans/", "scans/x.txt", "/x.txt"),
        ("/assets/C1/", "assets/C1/sub/y.txt", "/sub/y.txt"),
        # controls: unchanged
        ("A1/", "A1/x.txt", "/x.txt"),
        ("A1/", "A1//x.txt", "//x.txt"),
        ("/scans/", "/scans/x.txt", "/x.txt"),
        ("A1/", "other/x.txt", "/other/x.txt"),
        ("p/model.glb", "p/model.glb/sub/x.obj", "/sub/x.obj"),
        ("p/model.glb", "p/model.glbx", "/p/model.glbx"),
    ])
    def test_the_records_path(self, location, key, path):
        assert af._relative_to_location(location, key) == path

    @pytest.mark.parametrize("location, key, path", [
        ("/scans/", "scans/x.txt", "/x.txt"),
        # controls: unchanged
        ("A1/", "A1/x.txt", "/x.txt"),
        ("assets/B1/", "assets/B1/sub/y.txt", "/sub/y.txt"),
        ("A1/", "other/x.txt", "other/x.txt"),
    ])
    def test_the_version_snapshot_path_of_a_download(self, location, key, path):
        assert asset_relative_key(key, location) == path
