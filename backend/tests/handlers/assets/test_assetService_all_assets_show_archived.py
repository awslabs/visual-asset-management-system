# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""GET /assets returns active assets, and with showArchived active and archived assets together.

Archived assets live under a ``<databaseId>#deleted`` partition key. The all-databases listing
scans the table, so the archived rows are told apart by that suffix. The scan double here
applies the legacy ScanFilter the way DynamoDB does.
"""

import pytest
from unittest.mock import patch

from backend.tests.handlers.assets.test_assetService_history import _load

ROWS = [
    {"databaseId": {"S": "db1"}, "assetId": {"S": "active-1"}},
    {"databaseId": {"S": "db1#deleted"}, "assetId": {"S": "archived-1"}},
    {"databaseId": {"S": "db2"}, "assetId": {"S": "active-2"}},
]


class _AssetTableScan:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        rows = self.rows
        condition = kwargs.get("ScanFilter", {}).get("databaseId")
        if condition:
            needle = condition["AttributeValueList"][0]["S"]
            if condition["ComparisonOperator"] == "CONTAINS":
                rows = [r for r in rows if needle in r["databaseId"]["S"]]
            elif condition["ComparisonOperator"] == "NOT_CONTAINS":
                rows = [r for r in rows if needle not in r["databaseId"]["S"]]
        return {"Items": rows[:kwargs["Limit"]]}


def _list(show_archived):
    m = _load()
    m.claims_and_roles = {"tokens": ["u1"]}
    scan = _AssetTableScan(ROWS)
    with patch.object(m, "dynamodb_client") as client:
        client.scan.side_effect = scan
        result = m.get_all_assets({"pageSize": 100, "maxItems": 1000}, show_archived)
    return result, scan


@pytest.mark.unit
class TestAllAssetsShowArchived:
    def test_show_archived_returns_active_and_archived_assets(self):
        result, _ = _list(True)

        assert sorted(item["assetId"] for item in result["Items"]) == [
            "active-1", "active-2", "archived-1"]

    def test_an_archived_asset_carries_the_archived_status(self):
        result, _ = _list(True)

        statuses = {item["assetId"]: item.get("status") for item in result["Items"]}
        assert statuses["archived-1"] == "archived"
        assert statuses["active-1"] is None

    def test_without_show_archived_only_active_assets_are_returned(self):
        result, scan = _list(False)

        assert sorted(item["assetId"] for item in result["Items"]) == ["active-1", "active-2"]
        assert scan.calls[0]["ScanFilter"]["databaseId"]["ComparisonOperator"] == "NOT_CONTAINS"
