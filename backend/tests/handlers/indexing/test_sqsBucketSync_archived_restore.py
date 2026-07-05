# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for direct-S3 restore of archived assets in sqsBucketSync.

A new file uploaded directly to S3 under an archived asset's prefix restores
the asset record (DynamoDB-only unarchive). Previously archived files keep
their delete markers; only the record moves back to the live partition.
"""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load

_PATCHED_ATTRS = (
    "asset_bucket_name", "asset_bucket_prefix", "RESERVED_S3_PREFIX_FOLDERS",
    "get_bucket_id", "extract_asset_id_from_key", "validate_asset_id",
    "lookup_asset", "lookup_archived_asset", "restore_archived_asset",
    "get_or_create_database_for_bucket", "create_new_asset",
    "object_still_exists", "update_s3_metadata", "update_asset_type",
    "dynamodb", "update_asset_count", "asset_cache",
)


@pytest.fixture(autouse=True)
def _restore_module_attrs():
    m = _load()
    saved = {name: getattr(m, name) for name in _PATCHED_ATTRS}
    yield
    for name, value in saved.items():
        setattr(m, name, value)


def _archived_asset():
    return {
        "databaseId": "db1#deleted",
        "assetId": "x-asset-1",
        "assetName": "x-asset-1",
        "bucketId": "bucket-1",
        "status": "archived",
        "archivedAt": "2026-07-01T00:00:00",
        "archivedBy": "someone",
        "archivedReason": "old",
        "assetLocation": {"Key": "x-asset-1/"},
    }


def _record(key="db/x-asset-1/new-file.glb"):
    return {"s3": {"bucket": {"name": "asset-bucket"}, "object": {"key": key}},
            "eventName": "ObjectCreated:Put"}


def _wire(m):
    m.asset_bucket_name = "asset-bucket"
    m.asset_bucket_prefix = "db/"
    m.RESERVED_S3_PREFIX_FOLDERS = set()
    m.get_bucket_id = MagicMock(return_value="bucket-1")
    m.extract_asset_id_from_key = MagicMock(return_value="x-asset-1")
    m.validate_asset_id = MagicMock(return_value=True)
    m.lookup_asset = MagicMock(return_value=None)  # no live record
    m.lookup_archived_asset = MagicMock(return_value=_archived_asset())
    m.object_still_exists = MagicMock(return_value=True)
    m.update_s3_metadata = MagicMock(return_value=True)
    m.update_asset_type = MagicMock(return_value=True)
    m.create_new_asset = MagicMock()
    m.get_or_create_database_for_bucket = MagicMock()
    m.s3_client = MagicMock()  # history-write head_object


@pytest.mark.unit
class TestProcessRecordRestoresArchivedAsset:
    def test_new_upload_restores_archived_asset(self):
        m = _load()
        _wire(m)
        m.restore_archived_asset = MagicMock(return_value="db1")

        success, should_index, message = m.process_s3_record(_record())

        assert success is True and should_index is True
        m.restore_archived_asset.assert_called_once_with(
            "bucket-1", "x-asset-1", _archived_asset())
        # Restore path must not run the fresh-create flow
        m.create_new_asset.assert_not_called()
        m.get_or_create_database_for_bucket.assert_not_called()
        # Downstream processing continued with the live database id
        m.update_s3_metadata.assert_called_once()
        assert m.update_s3_metadata.call_args.args[2] == "db1"

    def test_stale_event_does_not_restore(self):
        m = _load()
        _wire(m)
        m.object_still_exists = MagicMock(return_value=False)
        m.restore_archived_asset = MagicMock()

        success, should_index, message = m.process_s3_record(_record())

        assert success is True and should_index is True
        m.restore_archived_asset.assert_not_called()

    def test_restore_failure_reports_error_but_forwards(self):
        m = _load()
        _wire(m)
        m.restore_archived_asset = MagicMock(return_value=None)

        success, should_index, message = m.process_s3_record(_record())

        assert success is False and should_index is True


@pytest.mark.unit
class TestRestoreArchivedAsset:
    def test_moves_record_to_live_partition(self):
        m = _load()
        mock_table = MagicMock()
        m.dynamodb = MagicMock()
        m.dynamodb.Table.return_value = mock_table
        m.update_asset_count = MagicMock()

        result = m.restore_archived_asset("bucket-1", "x-asset-1", _archived_asset())

        assert result == "db1"
        put_item = mock_table.put_item.call_args.kwargs["Item"]
        assert put_item["databaseId"] == "db1"
        assert "status" not in put_item
        assert "archivedAt" not in put_item
        assert put_item["unarchivedBy"] == "SYSTEM_USER"
        mock_table.delete_item.assert_called_once_with(
            Key={"databaseId": "db1#deleted", "assetId": "x-asset-1"})

    def test_concurrent_restore_is_idempotent(self):
        m = _load()
        mock_table = MagicMock()
        mock_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        m.dynamodb = MagicMock()
        m.dynamodb.Table.return_value = mock_table
        m.update_asset_count = MagicMock()

        result = m.restore_archived_asset("bucket-1", "x-asset-1", _archived_asset())

        assert result == "db1"
        mock_table.delete_item.assert_not_called()

    def test_unexpected_databaseid_returns_none(self):
        m = _load()
        bad = _archived_asset()
        bad["databaseId"] = "db1"  # missing #deleted suffix
        result = m.restore_archived_asset("bucket-1", "x-asset-1", bad)
        assert result is None
