# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests that sqsBucketSync auto-restore writes an asset lifecycle history
record (unarchiveDirect, SYSTEM_USER)."""

from unittest.mock import MagicMock

import pytest

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load

_PATCHED_ATTRS = (
    "dynamodb", "update_asset_count", "asset_cache", "write_asset_history_record",
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
        "description": "d1",
        "isDistributable": True,
        "tags": [],
        "bucketId": "bucket-1",
        "status": "archived",
        "archivedAt": "2026-07-01T00:00:00",
        "archivedBy": "someone",
        "archivedReason": "old",
        "assetLocation": {"Key": "x-asset-1/"},
    }


@pytest.mark.unit
class TestRestoreArchivedAssetHistory:
    def test_restore_writes_unarchive_direct_record(self):
        m = _load()
        mock_table = MagicMock()
        m.dynamodb = MagicMock()
        m.dynamodb.Table.return_value = mock_table
        m.update_asset_count = MagicMock()
        m.write_asset_history_record = MagicMock()

        result = m.restore_archived_asset("bucket-1", "x-asset-1", _archived_asset())

        assert result == "db1"
        m.write_asset_history_record.assert_called_once()
        args = m.write_asset_history_record.call_args[0]
        assert args[:4] == ("db1", "x-asset-1", m.CHANGE_SOURCE_UNARCHIVE_DIRECT, "SYSTEM_USER")
        assert args[4]["unarchivedReason"].startswith("Auto-restored")
        assert args[4]["assetName"] == "x-asset-1"

    def test_no_history_record_when_restore_fails(self):
        m = _load()
        bad = _archived_asset()
        bad["databaseId"] = "db1"  # missing #deleted suffix -> restore aborts
        m.write_asset_history_record = MagicMock()

        result = m.restore_archived_asset("bucket-1", "x-asset-1", bad)

        assert result is None
        m.write_asset_history_record.assert_not_called()
