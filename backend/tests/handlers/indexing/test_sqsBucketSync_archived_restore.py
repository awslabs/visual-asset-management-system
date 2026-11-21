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
    "write_file_version_history", "write_asset_history_record",
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
            "bucket-1", "x-asset-1", _archived_asset(), bucket_name="asset-bucket")
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

    def test_a_cached_image_of_a_permanently_deleted_asset_is_not_restored(self):
        """#1799: the archived lookup returns a cached image of an asset that was permanently deleted
        since. The restore writes nothing into the asset table, and the key is handled like any other
        key with no asset: the bucket's database and a new asset."""
        m = _load()
        _wire(m)
        table = TestRestoreArchivedAsset._routed_table(None)
        m.dynamodb = MagicMock()
        m.dynamodb.Table.return_value = table
        m.update_asset_count = MagicMock()
        m.asset_cache = MagicMock()
        m.write_file_version_history = MagicMock()
        m.write_asset_history_record = MagicMock()
        m.get_or_create_database_for_bucket = MagicMock(return_value="bucket-db")
        m.create_new_asset = MagicMock(return_value="x-asset-1")

        success, should_index, message = m.process_s3_record(_record())

        assert (success, should_index) == (True, True), message
        table.put_item.assert_not_called()
        table.delete_item.assert_not_called()
        m.write_asset_history_record.assert_not_called()
        assert m.create_new_asset.call_args.args == ("bucket-1", "bucket-db", "x-asset-1")
        assert m.update_s3_metadata.call_args.args[2] == "bucket-db"

    def test_restore_failure_reports_error_but_forwards(self):
        m = _load()
        _wire(m)
        m.restore_archived_asset = MagicMock(return_value=None)

        success, should_index, message = m.process_s3_record(_record())

        assert success is False and should_index is True


@pytest.mark.unit
class TestRestoreArchivedAsset:
    @staticmethod
    def _routed_table(archived_row, live_row=None):
        """One handle for both tables: the database read, the archived row's consistent re-read and
        the live row's read are told apart by their key. `archived_row` None means the archived row
        is already gone; `live_row` None means the asset has no live row."""
        mock_table = MagicMock()

        def get_item(Key, **kwargs):
            if Key.get("databaseId") == "db1#deleted":
                return {"Item": archived_row} if archived_row is not None else {}
            if "assetId" in Key:
                return {"Item": live_row} if live_row is not None else {}
            return {"Item": {"databaseId": "db1"}}

        mock_table.get_item.side_effect = get_item
        return mock_table

    def test_moves_record_to_live_partition(self):
        m = _load()
        mock_table = self._routed_table(_archived_asset())
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

    def test_restore_copies_the_archived_row_as_stored_not_the_callers_image(self):
        # The caller's image comes from an eventually consistent index read and a container cache.
        # Attributes written to the row after that image (the location index attributes) are kept.
        m = _load()
        stored = {**_archived_asset(), "locationBucket": "bucket-name", "locationKey": "x-asset-1/"}
        mock_table = self._routed_table(stored)
        m.dynamodb = MagicMock()
        m.dynamodb.Table.return_value = mock_table
        m.update_asset_count = MagicMock()

        result = m.restore_archived_asset("bucket-1", "x-asset-1", _archived_asset())

        assert result == "db1"
        archived_reads = [c for c in mock_table.get_item.call_args_list
                          if c.kwargs["Key"] == {"databaseId": "db1#deleted", "assetId": "x-asset-1"}]
        assert len(archived_reads) == 1
        assert archived_reads[0].kwargs.get("ConsistentRead") is True
        put_item = mock_table.put_item.call_args.kwargs["Item"]
        assert put_item["locationBucket"] == "bucket-name"
        assert put_item["locationKey"] == "x-asset-1/"
        assert put_item["databaseId"] == "db1"

    def test_a_permanently_deleted_asset_is_not_restored_from_the_callers_image(self):
        # #1799: the caller's image can outlive the archived row by up to the 60-second cache
        # lifetime. With no archived row and no live row, nothing is written or deleted.
        m = _load()
        mock_table = self._routed_table(None)
        m.dynamodb = MagicMock()
        m.dynamodb.Table.return_value = mock_table
        m.update_asset_count = MagicMock()
        m.asset_cache = MagicMock()

        result = m.restore_archived_asset("bucket-1", "x-asset-1", _archived_asset())

        assert result is m.ARCHIVED_RECORD_GONE
        mock_table.put_item.assert_not_called()
        mock_table.delete_item.assert_not_called()
        m.update_asset_count.assert_not_called()
        dropped = {c.args[0] for c in m.asset_cache.delete.call_args_list}
        assert dropped == {"bucket-1:x-asset-1", "bucket-1:x-asset-1:archived"}
        live_reads = [c for c in mock_table.get_item.call_args_list
                      if c.kwargs["Key"] == {"databaseId": "db1", "assetId": "x-asset-1"}]
        assert len(live_reads) == 1 and live_reads[0].kwargs.get("ConsistentRead") is True

    def test_a_concurrently_restored_asset_reports_its_live_database(self):
        # The archived row is gone because another event in the batch restored it: same outcome as
        # the conditional write losing the race, and nothing is written again.
        m = _load()
        live = {**_archived_asset(), "databaseId": "db1"}
        mock_table = self._routed_table(None, live_row=live)
        m.dynamodb = MagicMock()
        m.dynamodb.Table.return_value = mock_table
        m.update_asset_count = MagicMock()
        m.asset_cache = MagicMock()

        result = m.restore_archived_asset("bucket-1", "x-asset-1", _archived_asset())

        assert result == "db1"
        mock_table.put_item.assert_not_called()
        mock_table.delete_item.assert_not_called()

    def test_concurrent_restore_is_idempotent(self):
        m = _load()
        mock_table = self._routed_table(_archived_asset())
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
