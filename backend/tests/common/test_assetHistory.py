# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the shared asset lifecycle history writer (common.assetHistory)."""

import os
from unittest.mock import MagicMock

import pytest

# Env override so get_table_name() resolves without SSM
os.environ.setdefault("ASSET_HISTORY_STORAGE_TABLE_NAME", "test-asset-history-table")


@pytest.fixture()
def asset_history_module():
    # Module is loaded by conftest via import_module_from_path (reload does
    # not work on it); swap in a fresh table mock per test instead.
    import common.assetHistory as mod
    saved_table = mod.asset_history_table
    mod.asset_history_table = MagicMock()
    yield mod
    mod.asset_history_table = saved_table


@pytest.mark.unit
class TestWriteAssetHistoryRecord:
    def test_record_shape(self, asset_history_module):
        m = asset_history_module
        m.write_asset_history_record("db1", "a1", m.CHANGE_SOURCE_CREATE, "user1", {"assetName": "A"})

        m.asset_history_table.put_item.assert_called_once()
        item = m.asset_history_table.put_item.call_args[1]["Item"]
        assert item["databaseId:assetId"] == "db1:a1"
        assert item["databaseId"] == "db1"
        assert item["assetId"] == "a1"
        assert item["changeSource"] == "create"
        assert item["changeUserId"] == "user1"
        assert item["assetSnapshot"] == {"assetName": "A"}
        # SK = {recordDate}#{8-char uuid suffix}, chronologically sortable
        record_date, _, suffix = item["historyRecordId"].partition("#")
        assert record_date == item["recordDate"]
        assert len(suffix) == 8
        assert item["recordDate"].endswith("Z")

    def test_user_fallback_to_system_user(self, asset_history_module):
        m = asset_history_module
        m.write_asset_history_record("db1", "a1", m.CHANGE_SOURCE_EDIT, None, {})
        item = m.asset_history_table.put_item.call_args[1]["Item"]
        assert item["changeUserId"] == "SYSTEM_USER"

    def test_write_failure_is_swallowed(self, asset_history_module):
        m = asset_history_module
        m.asset_history_table.put_item.side_effect = Exception("boom")
        # Must not raise into the calling operation
        m.write_asset_history_record("db1", "a1", m.CHANGE_SOURCE_ARCHIVE, "u", {})

    def test_missing_table_is_swallowed(self, asset_history_module):
        m = asset_history_module
        m.asset_history_table = None
        m.write_asset_history_record("db1", "a1", m.CHANGE_SOURCE_ARCHIVE, "u", {})


@pytest.mark.unit
class TestBuildAssetSnapshot:
    def test_full_asset(self, asset_history_module):
        m = asset_history_module
        asset = {
            "assetName": "My Asset",
            "description": "desc",
            "isDistributable": True,
            "tags": ["t1"],
            "bucketId": "b1",
            "assetLocation": {"Key": "xabc/"},
            "assetType": "none",  # not part of the snapshot
        }
        snapshot = m.build_asset_snapshot(asset)
        assert snapshot == {
            "assetName": "My Asset",
            "description": "desc",
            "isDistributable": True,
            "tags": ["t1"],
            "bucketId": "b1",
            "assetLocationKey": "xabc/",
        }

    def test_reasons_only_when_provided(self, asset_history_module):
        m = asset_history_module
        snapshot = m.build_asset_snapshot({}, archived_reason="cleanup")
        assert snapshot["archivedReason"] == "cleanup"
        assert "unarchivedReason" not in snapshot

        snapshot = m.build_asset_snapshot({}, unarchived_reason="restore")
        assert snapshot["unarchivedReason"] == "restore"
        assert "archivedReason" not in snapshot

    def test_missing_asset_location(self, asset_history_module):
        m = asset_history_module
        snapshot = m.build_asset_snapshot({"assetName": "A"})
        assert "assetLocationKey" not in snapshot
