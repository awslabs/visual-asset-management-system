# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync's auto-restore of an archived asset whose database has been deleted.

A new object written straight to S3 under an archived asset's prefix moves the asset record back
into the live partition. When the asset's database has been deleted since the archive, that move
would leave a live asset under a database that exists only as its `#deleted` record, so the
restore is refused before any write and the asset stays archived. The database read is strongly
consistent, so a database deleted moments before the write is seen as deleted, and a read that
fails refuses the restore rather than assuming the database exists.
"""

from unittest.mock import MagicMock

import pytest

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load


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
        "assetLocation": {"Key": "db/x-asset-1/"},
    }


def _record(key="db/x-asset-1/new-file.glb"):
    return {"s3": {"bucket": {"name": "asset-bucket"}, "object": {"key": key}},
            "eventName": "ObjectCreated:Put"}


@pytest.fixture
def sync(monkeypatch):
    """The loaded sqsBucketSync with one mock table behind every `dynamodb.Table(...)`.

    The module is cached across test files, so each replacement goes through monkeypatch and is
    undone after the test.
    """
    m = _load()
    table = MagicMock()
    resource = MagicMock()
    resource.Table.return_value = table
    monkeypatch.setattr(m, "dynamodb", resource)
    monkeypatch.setattr(m, "update_asset_count", MagicMock())
    monkeypatch.setattr(m, "write_asset_history_record", MagicMock())
    monkeypatch.setattr(m, "asset_cache", MagicMock())
    return m, table


def _wire_process_record(m, monkeypatch):
    """Route a created event for an archived asset's prefix to the restore branch."""
    monkeypatch.setattr(m, "asset_bucket_name", "asset-bucket")
    monkeypatch.setattr(m, "asset_bucket_prefix", "db/")
    monkeypatch.setattr(m, "RESERVED_S3_PREFIX_FOLDERS", set())
    monkeypatch.setattr(m, "get_bucket_id", MagicMock(return_value="bucket-1"))
    monkeypatch.setattr(m, "extract_asset_id_from_key", MagicMock(return_value="x-asset-1"))
    monkeypatch.setattr(m, "validate_asset_id", MagicMock(return_value=True))
    monkeypatch.setattr(m, "lookup_asset", MagicMock(return_value=None))
    monkeypatch.setattr(m, "lookup_archived_asset", MagicMock(return_value=_archived_asset()))
    monkeypatch.setattr(m, "object_still_exists", MagicMock(return_value=True))
    monkeypatch.setattr(m, "update_s3_metadata", MagicMock(return_value=True))
    monkeypatch.setattr(m, "update_asset_type", MagicMock(return_value=True))
    monkeypatch.setattr(m, "create_new_asset", MagicMock())
    monkeypatch.setattr(m, "get_or_create_database_for_bucket", MagicMock())
    monkeypatch.setattr(m, "s3_client", MagicMock())


@pytest.mark.unit
class TestRestoreIntoADeletedDatabaseIsRefused:
    def test_a_deleted_database_leaves_the_asset_archived(self, sync, monkeypatch):
        m, table = sync
        monkeypatch.setattr(m, "verify_database_exists", MagicMock(return_value=False))

        result = m.restore_archived_asset("bucket-1", "x-asset-1", _archived_asset())

        assert result is None
        m.verify_database_exists.assert_called_once_with("db1")
        table.put_item.assert_not_called()
        table.delete_item.assert_not_called()
        m.update_asset_count.assert_not_called()
        m.write_asset_history_record.assert_not_called()

    def test_a_failed_database_read_refuses_the_restore(self, sync, monkeypatch):
        m, table = sync
        monkeypatch.setattr(m, "verify_database_exists",
                            MagicMock(side_effect=Exception("Error verifying database.")))

        result = m.restore_archived_asset("bucket-1", "x-asset-1", _archived_asset())

        assert result is None
        table.put_item.assert_not_called()
        table.delete_item.assert_not_called()

    def test_the_database_read_is_strongly_consistent(self, sync):
        m, table = sync
        table.get_item.return_value = {"Item": {"databaseId": "db1"}}

        assert m.verify_database_exists("db1") is True
        table.get_item.assert_called_once_with(Key={"databaseId": "db1"}, ConsistentRead=True)

    def test_process_record_reports_the_refused_restore_and_still_forwards(self, sync, monkeypatch):
        """The record fails VAMS-side processing, as any failed restore does, and still reaches
        the indexers; nothing is stamped on the object and no asset is created for it."""
        m, table = sync
        _wire_process_record(m, monkeypatch)
        table.get_item.return_value = {}

        success, should_index, message = m.process_s3_record(_record())

        assert success is False and should_index is True
        assert message.startswith("Failed to restore archived asset"), message
        table.put_item.assert_not_called()
        m.update_s3_metadata.assert_not_called()
        m.create_new_asset.assert_not_called()
        m.get_or_create_database_for_bucket.assert_not_called()


@pytest.mark.unit
class TestRestoreIntoALiveDatabaseStillWorks:
    def test_a_live_database_receives_the_asset(self, sync, monkeypatch):
        """Positive control: every refusal above is also satisfied by a restore that never runs."""
        m, table = sync
        monkeypatch.setattr(m, "verify_database_exists", MagicMock(return_value=True))

        result = m.restore_archived_asset("bucket-1", "x-asset-1", _archived_asset())

        assert result == "db1"
        put = table.put_item.call_args.kwargs
        assert put["Item"]["databaseId"] == "db1"
        assert "attribute_not_exists" in put["ConditionExpression"]
        table.delete_item.assert_called_once_with(
            Key={"databaseId": "db1#deleted", "assetId": "x-asset-1"})
