# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load


def _conditional_check_error():
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
    )


@pytest.mark.unit
class TestUpdateAssetTypeAttributeGuard:
    """The assetType update must never re-create a record for a moved asset."""

    def test_update_is_conditional_on_record_existing(self):
        m = _load()
        mock_table = MagicMock()
        m.dynamodb = MagicMock()
        m.dynamodb.Table.return_value = mock_table

        assert m._update_asset_type_attribute("bucket-1", "x-asset-1", "db1", ".txt") is True

        kwargs = mock_table.update_item.call_args.kwargs
        assert kwargs["ConditionExpression"] == "attribute_exists(assetId)"
        assert kwargs["Key"] == {"databaseId": "db1", "assetId": "x-asset-1"}

    def test_conditional_failure_returns_false_and_invalidates_cache(self):
        m = _load()
        mock_table = MagicMock()
        mock_table.update_item.side_effect = _conditional_check_error()
        m.dynamodb = MagicMock()
        m.dynamodb.Table.return_value = mock_table

        # Seed stale cache entries pointing at the moved asset
        m.asset_cache.set("bucket-1:x-asset-1", {"databaseId": "db1", "assetId": "x-asset-1"})
        m.asset_cache.set("bucket-1:x-asset-1:archived", {"databaseId": "db1#deleted"})

        assert m._update_asset_type_attribute("bucket-1", "x-asset-1", "db1", ".txt") is False
        assert m.asset_cache.get("bucket-1:x-asset-1") is None
        assert m.asset_cache.get("bucket-1:x-asset-1:archived") is None

    def test_other_client_errors_propagate(self):
        m = _load()
        mock_table = MagicMock()
        mock_table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem"
        )
        m.dynamodb = MagicMock()
        m.dynamodb.Table.return_value = mock_table

        with pytest.raises(ClientError):
            m._update_asset_type_attribute("bucket-1", "x-asset-1", "db1", ".txt")

    def test_update_asset_type_skips_on_moved_asset(self):
        # End-to-end through update_asset_type: cached record says the asset is in
        # db1, but the record was moved (conditional check fails). No phantom
        # record write is retried and the function reports failure.
        m = _load()
        m.lookup_asset = MagicMock(return_value={
            "databaseId": "db1", "assetId": "x-asset-1", "assetType": None
        })
        m.determine_asset_type = MagicMock(return_value=".txt")
        mock_table = MagicMock()
        mock_table.update_item.side_effect = _conditional_check_error()
        m.dynamodb = MagicMock()
        m.dynamodb.Table.return_value = mock_table

        assert m.update_asset_type("bucket-1", "x-asset-1", "asset-bucket", "x-asset-1/") is False
        # Exactly one conditional attempt; no unconditional fallback
        assert mock_table.update_item.call_count == 1


@pytest.mark.unit
class TestSimpleCacheDelete:
    def test_delete_removes_entry(self):
        m = _load()
        cache = m.SimpleCache()
        cache.set("k", "v")
        cache.delete("k")
        assert cache.get("k") is None

    def test_delete_missing_key_is_noop(self):
        m = _load()
        cache = m.SimpleCache()
        cache.delete("missing")  # must not raise
