# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`assetFiles.delete_file_version_records` removes the deleted files' version snapshot rows, and a
write it cannot make is logged as an error.

The file routes call it after the files are gone from Amazon S3. The rows live in the asset file
versions table, one per (asset version, file); an asset version lists a file from its row. The table is
a moto table with the deployment's key schema and its `databaseIdAssetIdIndex`.
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

PK = "databaseId:assetId:assetVersionId"


@pytest.fixture
def versions_table(monkeypatch):
    with mock_aws():
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
        for asset, version, key in [
            ("a1", "1", "/x.txt"), ("a1", "2", "/x.txt"), ("a1", "2", "/sub/y.txt"),
            ("a1", "2", "/keep.txt"), ("a2", "1", "/x.txt"),
        ]:
            table.put_item(Item={PK: f"db1:{asset}:{version}", "fileKey": key, "databaseId:assetId": f"db1:{asset}"})
        monkeypatch.setattr(af, "asset_version_files_table", table)
        yield table


def _rows(table):
    return sorted((item[PK], item["fileKey"]) for item in table.scan()["Items"])


def test_the_deleted_files_rows_are_removed_in_every_asset_version(versions_table):
    deleted = af.delete_file_version_records("db1", "a1", ["x.txt", "/sub/y.txt", "/folder/"])

    assert deleted == 3
    assert _rows(versions_table) == [("db1:a1:2", "/keep.txt"), ("db1:a2:1", "/x.txt")]


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

    assert len(batch.deletes) == 2
    assert deleted == 0
    logger.exception.assert_called_once()
    assert "AccessDeniedException" in logger.exception.call_args.args[0]
    logger.warning.assert_not_called()
    # The rows are still there, and the file route goes on (the files are already gone from S3).
    assert ("db1:a1:1", "/x.txt") in _rows(versions_table)
