# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`assetFiles.delete_file_version_records` removes the deleted files' version snapshot rows, and a
write it cannot make is logged as an error.

The file routes call it after the files are gone from Amazon S3. The rows live in the asset file
versions table, one per (asset version, file); an asset version lists a file from its row. The table is
a moto table with the deployment's key schema and its `databaseIdAssetIdIndex`.

A row's `fileKey` is the `relativeKey` its asset version was created with. A version built from the
asset's current files (`useLatestFiles`, the default of the web's Create version, `vamscli asset-version
create` and `sync file push --version-comment`) stores it without a leading slash (`x.txt`); a version
built from a file list stores the paths as the caller wrote them, often as the file list shows them
(`/x.txt`). One asset can hold both.
"""

import os
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles as af  # noqa: E402
# The file route on a versioned moto bucket holding the folder asset db1/a1 at proj/a/ (x.txt and its
# preview, sub/y.txt, sub/z.txt); the fixtures are imported so this module can drive delete_file.
from tests.handlers.assets.test_permanent_delete_s3_failures import (  # noqa: E402,F401
    CLAIMS, aux, files, s3,
)

PK = "databaseId:assetId:assetVersionId"
DELETE_FILE_VERSION_RECORDS = af.delete_file_version_records

ROWS = [
    # a1: two default versions, keys without a leading slash
    ("a1", "1", "x.txt"), ("a1", "2", "x.txt"), ("a1", "2", "sub/y.txt"), ("a1", "2", "keep.txt"),
    # a3: two versions built from a file list, keys with a leading slash
    ("a3", "1", "/x.txt"), ("a3", "2", "/x.txt"), ("a3", "2", "/keep.txt"),
    # a4: a default version, then one built from a file list
    ("a4", "1", "x.txt"), ("a4", "1", "keep.txt"), ("a4", "2", "/x.txt"), ("a4", "2", "/keep.txt"),
    # a2: another asset of the database, in both shapes
    ("a2", "1", "x.txt"), ("a2", "2", "/x.txt"),
]


def _create_versions_table(rows):
    table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
        TableName="test-asset-file-versions-table",
        KeySchema=[{"AttributeName": PK, "KeyType": "HASH"}, {"AttributeName": "fileKey", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": PK, "AttributeType": "S"},
            {"AttributeName": "fileKey", "AttributeType": "S"},
            {"AttributeName": "databaseId:assetId", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": "databaseIdAssetIdIndex",
            "KeySchema": [{"AttributeName": "databaseId:assetId", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
    )
    for asset, version, key in rows:
        table.put_item(Item={PK: f"db1:{asset}:{version}", "fileKey": key, "databaseId:assetId": f"db1:{asset}"})
    return table


@pytest.fixture
def versions_table(monkeypatch):
    with mock_aws():
        table = _create_versions_table(ROWS)
        monkeypatch.setattr(af, "asset_version_files_table", table)
        yield table


def _rows(table, asset=None):
    return sorted((item[PK], item["fileKey"]) for item in table.scan()["Items"]
                  if asset is None or item["databaseId:assetId"] == f"db1:{asset}")


def test_default_version_rows_are_removed_in_every_asset_version(versions_table):
    deleted = af.delete_file_version_records("db1", "a1", ["/x.txt", "sub/y.txt", "/folder/"])

    assert deleted == 3
    assert _rows(versions_table, "a1") == [("db1:a1:2", "keep.txt")]
    assert _rows(versions_table, "a2") == [("db1:a2:1", "x.txt"), ("db1:a2:2", "/x.txt")]


def test_leading_slash_rows_are_removed(versions_table):
    deleted = af.delete_file_version_records("db1", "a3", ["x.txt"])

    assert deleted == 2
    assert _rows(versions_table, "a3") == [("db1:a3:2", "/keep.txt")]


def test_a_mixed_asset_loses_each_row_by_the_key_it_is_stored_under(versions_table):
    deleted = af.delete_file_version_records("db1", "a4", ["/x.txt"])

    assert deleted == 2
    assert _rows(versions_table, "a4") == [("db1:a4:1", "keep.txt"), ("db1:a4:2", "/keep.txt")]
    assert _rows(versions_table, "a2") == [("db1:a2:1", "x.txt"), ("db1:a2:2", "/x.txt")]


def test_the_asset_root_and_folders_name_no_row(versions_table):
    assert af.delete_file_version_records("db1", "a1", ["/", "sub/", ""]) == 0
    assert len(_rows(versions_table, "a1")) == 4


class _RefusedBatch:
    def __init__(self):
        self.deletes = []

    def __enter__(self):
        return self

    def delete_item(self, Key):
        self.deletes.append(Key)

    def __exit__(self, *exc):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}},
                          "BatchWriteItem")


def test_a_refused_write_is_logged_as_an_error_and_reports_no_row_deleted(versions_table, monkeypatch):
    batch = _RefusedBatch()
    refusing_table = MagicMock(wraps=versions_table)
    refusing_table.query.side_effect = versions_table.query
    refusing_table.batch_writer.return_value = batch
    monkeypatch.setattr(af, "asset_version_files_table", refusing_table)
    logger = MagicMock()
    monkeypatch.setattr(af, "logger", logger)

    deleted = af.delete_file_version_records("db1", "a1", ["/x.txt"])

    assert batch.deletes == [{PK: "db1:a1:1", "fileKey": "x.txt"}, {PK: "db1:a1:2", "fileKey": "x.txt"}]
    assert deleted == 0
    logger.exception.assert_called_once()
    assert "AccessDeniedException" in logger.exception.call_args.args[0]
    logger.warning.assert_not_called()
    # The rows are still there, and the file route goes on (the files are already gone from S3).
    assert ("db1:a1:1", "x.txt") in _rows(versions_table)


ROUTE_ROWS = [
    ("a1", "1", "x.txt"), ("a1", "1", "x.txt.previewFile.png"), ("a1", "1", "sub/y.txt"),
    ("a1", "2", "x.txt"), ("a1", "2", "/sub/y.txt"), ("a1", "2", "/sub/z.txt"),
]


@pytest.fixture
def route(files, monkeypatch):
    """The file route with the real version-row cleanup and a moto versions table (in the files' moto)."""
    table = _create_versions_table(ROUTE_ROWS)
    monkeypatch.setattr(af, "asset_version_files_table", table)
    monkeypatch.setattr(af, "delete_file_version_records", DELETE_FILE_VERSION_RECORDS)
    files.versions_table = table
    return files


def test_a_prefix_delete_removes_the_deleted_files_rows_in_both_shapes(route):
    response = af.delete_file("db1", "a1", "/sub/", True, True, CLAIMS)

    assert response.success is True
    assert sorted(response.affectedFiles) == ["sub/y.txt", "sub/z.txt"]
    assert _rows(route.versions_table) == [
        ("db1:a1:1", "x.txt"), ("db1:a1:1", "x.txt.previewFile.png"), ("db1:a1:2", "x.txt")]


def test_a_single_file_delete_removes_the_files_and_its_previews_rows(route):
    response = af.delete_file("db1", "a1", "/x.txt", False, True, CLAIMS)

    assert response.success is True
    assert _rows(route.versions_table) == [
        ("db1:a1:1", "sub/y.txt"), ("db1:a1:2", "/sub/y.txt"), ("db1:a1:2", "/sub/z.txt")]
