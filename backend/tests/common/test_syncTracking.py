# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the outbound system sync tracking writer (common.syncTracking)."""

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("SYNC_TRACKING_OUTBOUND_STORAGE_TABLE_NAME", "test-sync-tracking-table")


@pytest.fixture()
def sync_tracking_module():
    import common.syncTracking as mod
    saved_table = mod.sync_tracking_outbound_table
    mod.sync_tracking_outbound_table = MagicMock()
    yield mod
    mod.sync_tracking_outbound_table = saved_table


@pytest.mark.unit
class TestWriteOutboundSyncRecord:
    def test_asset_file_record_shape(self, sync_tracking_module):
        m = sync_tracking_module
        m.write_outbound_sync_record(
            m.SYNC_OBJECT_TYPE_ASSET_FILE, "db1", "physna", "https://api#tenant1",
            m.SYNC_ACTION_CREATE, m.SYNC_STATUS_SUCCESS,
            asset_id="a1", file_path="/dir/part.CATPart",
            s3_version_id="v123", sync_system_entity_id="uuid-9",
        )
        item = m.sync_tracking_outbound_table.put_item.call_args[1]["Item"]
        assert item["objectId"] == "db1:a1:/dir/part.CATPart"
        assert item["objectType"] == "assetFile"
        assert item["databaseId"] == "db1"
        assert item["assetId"] == "a1"
        assert item["filePath"] == "/dir/part.CATPart"
        assert item["s3VersionId"] == "v123"
        assert item["systemType"] == "physna"
        assert item["systemUniqueId"] == "https://api#tenant1"
        assert item["systemType:systemUniqueId"] == "physna:https://api#tenant1"
        assert item["databaseId:systemType:systemUniqueId"] == "db1:physna:https://api#tenant1"
        assert item["action"] == "create"
        assert item["syncStatus"] == "success"
        assert item["syncSystemEntityId"] == "uuid-9"
        record_date, _, suffix = item["syncRecordId"].partition("#")
        assert record_date == item["recordDate"]
        assert len(suffix) == 8
        assert item["recordDate"].endswith("Z")
        assert "errorMessage" not in item

    def test_file_path_normalized_to_one_leading_slash(self, sync_tracking_module):
        m = sync_tracking_module
        m.write_outbound_sync_record(
            m.SYNC_OBJECT_TYPE_ASSET_FILE, "db1", "physna", "sys1",
            m.SYNC_ACTION_MODIFY, m.SYNC_STATUS_SUCCESS,
            asset_id="a1", file_path="dir/part.stp",
        )
        item = m.sync_tracking_outbound_table.put_item.call_args[1]["Item"]
        assert item["objectId"] == "db1:a1:/dir/part.stp"
        assert item["filePath"] == "/dir/part.stp"

    def test_asset_and_database_object_ids(self, sync_tracking_module):
        m = sync_tracking_module
        m.write_outbound_sync_record(
            m.SYNC_OBJECT_TYPE_ASSET, "db1", "physna", "sys1",
            m.SYNC_ACTION_MODIFY, m.SYNC_STATUS_SUCCESS, asset_id="a1",
        )
        assert m.sync_tracking_outbound_table.put_item.call_args[1]["Item"]["objectId"] == "db1:a1"

        m.write_outbound_sync_record(
            m.SYNC_OBJECT_TYPE_DATABASE, "db1", "garnetFramework", "queue-url",
            m.SYNC_ACTION_DELETE, m.SYNC_STATUS_SUCCESS,
        )
        item = m.sync_tracking_outbound_table.put_item.call_args[1]["Item"]
        assert item["objectId"] == "db1"
        assert "assetId" not in item
        assert "filePath" not in item

    def test_error_message_truncated(self, sync_tracking_module):
        m = sync_tracking_module
        m.write_outbound_sync_record(
            m.SYNC_OBJECT_TYPE_ASSET, "db1", "physna", "sys1",
            m.SYNC_ACTION_MODIFY, m.SYNC_STATUS_FAILED,
            asset_id="a1", error_message="x" * 5000,
        )
        item = m.sync_tracking_outbound_table.put_item.call_args[1]["Item"]
        assert len(item["errorMessage"]) == 1024
        assert item["syncStatus"] == "failed"

    def test_invalid_enums_write_nothing(self, sync_tracking_module):
        m = sync_tracking_module
        m.write_outbound_sync_record("bogusType", "db1", "physna", "sys1",
                                     m.SYNC_ACTION_CREATE, m.SYNC_STATUS_SUCCESS)
        m.write_outbound_sync_record(m.SYNC_OBJECT_TYPE_DATABASE, "db1", "physna", "sys1",
                                     "bogusAction", m.SYNC_STATUS_SUCCESS)
        m.write_outbound_sync_record(m.SYNC_OBJECT_TYPE_DATABASE, "db1", "physna", "sys1",
                                     m.SYNC_ACTION_CREATE, "bogusStatus")
        m.sync_tracking_outbound_table.put_item.assert_not_called()

    def test_missing_required_fields_write_nothing(self, sync_tracking_module):
        m = sync_tracking_module
        # asset without asset_id; assetFile without file_path; empty system fields
        m.write_outbound_sync_record(m.SYNC_OBJECT_TYPE_ASSET, "db1", "physna", "sys1",
                                     m.SYNC_ACTION_CREATE, m.SYNC_STATUS_SUCCESS)
        m.write_outbound_sync_record(m.SYNC_OBJECT_TYPE_ASSET_FILE, "db1", "physna", "sys1",
                                     m.SYNC_ACTION_CREATE, m.SYNC_STATUS_SUCCESS, asset_id="a1")
        m.write_outbound_sync_record(m.SYNC_OBJECT_TYPE_DATABASE, "db1", "", "sys1",
                                     m.SYNC_ACTION_CREATE, m.SYNC_STATUS_SUCCESS)
        m.sync_tracking_outbound_table.put_item.assert_not_called()

    def test_put_failure_is_swallowed(self, sync_tracking_module):
        m = sync_tracking_module
        m.sync_tracking_outbound_table.put_item.side_effect = Exception("boom")
        # Must not raise into the calling sync handler
        m.write_outbound_sync_record(m.SYNC_OBJECT_TYPE_DATABASE, "db1", "physna", "sys1",
                                     m.SYNC_ACTION_CREATE, m.SYNC_STATUS_SUCCESS)

    def test_missing_table_is_swallowed(self, sync_tracking_module):
        m = sync_tracking_module
        m.sync_tracking_outbound_table = None
        m.write_outbound_sync_record(m.SYNC_OBJECT_TYPE_DATABASE, "db1", "physna", "sys1",
                                     m.SYNC_ACTION_CREATE, m.SYNC_STATUS_SUCCESS)
