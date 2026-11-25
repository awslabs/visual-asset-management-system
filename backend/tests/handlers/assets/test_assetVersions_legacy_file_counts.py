# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The version list's `fileCount` counts the files the version's detail lists (#1795, L-4).

A version a file list created before #1795 can name one file both as `/a.txt` and `a.txt`, and can hold a `/`
row that names no file. Its detail lists each file once and no empty path, but the version list counted rows, so
such a version showed 4 files with 2 listed. Both counts (the one-walk tally and the per-version fallback past
its bound) now count one file per path without its leading slash and leave out a row that names no file. A
version without a slashed row is still counted with `Select='COUNT'`, without reading its keys. The versions
table is a moto table with the deployment's key schema and its `databaseIdAssetIdIndex`.
"""

from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.backend.handlers.assets import assetVersions as av  # noqa: E402

PK = "databaseId:assetId:assetVersionId"
DB = "db1"
ASSET = "a1"

ROWS = {
    "1": ["/a.txt", "a.txt", "/", "/c.txt"],   # legacy: one file both ways, a "/" row, a slashed file
    "2": ["a.txt", "b.txt"],                    # created since: no slashed row
    "3": ["/", "//"],                           # legacy: rows that name no file only
}
EXPECTED = {"1": 2, "2": 2, "3": 0}


class _RecordingTable:
    """The moto table, with every query's arguments recorded."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.table.query(**kwargs)


@pytest.fixture
def table(monkeypatch):
    with mock_aws():
        created = boto3.resource("dynamodb", region_name="us-east-1").create_table(
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
        for version_id, keys in ROWS.items():
            for file_key in keys:
                created.put_item(Item={PK: f"{DB}:{ASSET}:{version_id}", "fileKey": file_key,
                                       "databaseId:assetId": f"{DB}:{ASSET}", "versionId": "v"})
        recording = _RecordingTable(created)
        monkeypatch.setattr(av, "asset_file_versions_table", recording)
        yield recording


def _typed_version(version_id):
    return {"databaseId": {"S": DB}, "assetId": {"S": ASSET}, "assetVersionId": {"S": version_id},
            "dateCreated": {"S": "2026-11-01T00:00:00"}, "comment": {"S": "c"}, "description": {"S": ""},
            "createdBy": {"S": "alice"}, "isCurrentVersion": {"BOOL": version_id == "2"},
            "isArchived": {"BOOL": False}, "versionAlias": {"S": ""}}


def _list(version_ids=("3", "2", "1")):
    client = MagicMock()
    client.query.return_value = {"Items": [_typed_version(v) for v in version_ids]}
    with patch.object(av, "get_asset_with_permissions", return_value={"databaseId": DB, "assetId": ASSET}), \
            patch.object(av, "dynamodb_client", client):
        response = av.get_asset_versions(DB, ASSET, {}, {"tokens": ["alice"]})
    return {item.Version: item.fileCount for item in response.versions}


@pytest.mark.unit
class TestTheVersionList:
    def test_the_tally_counts_each_file_once(self, table):
        assert _list() == EXPECTED

    def test_the_per_version_count_past_the_tally_bound_counts_each_file_once(self, table):
        with patch.object(av, "tally_asset_version_file_counts", return_value=None):
            assert _list() == EXPECTED


@pytest.mark.unit
class TestThePerVersionCount:
    @pytest.mark.parametrize("version_id", list(ROWS))
    def test_it_counts_the_files_the_detail_lists(self, table, version_id):
        assert av.get_asset_version_file_count(DB, ASSET, version_id) == EXPECTED[version_id]

    def test_a_version_without_a_slashed_row_is_counted_without_reading_its_keys(self, table):
        assert av.get_asset_version_file_count(DB, ASSET, "2") == 2
        probe, *reads = table.calls
        assert probe.get("Limit") == 1
        assert reads and all(read.get("Select") == "COUNT" for read in reads)
