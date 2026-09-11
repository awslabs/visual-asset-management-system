# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.tags.tagService import get_tags

CLAIMS = {"tokens": ["u"]}


def _ddb(item):
    """Wrap a plain dict as a DynamoDB-typed item for TypeDeserializer (scan path)."""
    return {k: {"S": v} for k, v in item.items()}


# Composite-key partitions: PK=databaseId, SK=tagName.
PARTITIONS = {
    "GLOBAL": [
        {"databaseId": "GLOBAL", "tagName": "Status", "description": "d", "tagTypeName": "System"},
        {"databaseId": "GLOBAL", "tagName": "GlobalX", "description": "d", "tagTypeName": "System"},
    ],
    "factory-db": [
        {"databaseId": "factory-db", "tagName": "EquipID", "description": "d", "tagTypeName": "Custom"},
    ],
    "hospital-db": [
        {"databaseId": "hospital-db", "tagName": "PatientT", "description": "d", "tagTypeName": "Custom"},
    ],
}


@pytest.mark.unit
@patch('backend.backend.handlers.tags.tagService.CasbinEnforcer')
@patch('backend.backend.handlers.tags.tagService.paginator')
@patch('backend.backend.handlers.tags.tagService.tag_table')
@patch('backend.backend.handlers.tags.tagService.get_tag_types')
@patch('backend.backend.handlers.tags.tagService.claims_and_roles', CLAIMS)
class TestGetTagsScope:
    def _setup(self, get_tag_types, tag_table, paginator, casbin, allow=True):
        get_tag_types.return_value = []

        def query(IndexName=None, KeyConditionExpression=None):
            scope = KeyConditionExpression._values[1]
            return {"Items": [dict(r) for r in PARTITIONS.get(scope, [])]}

        tag_table.query.side_effect = query

        # scan path (no scope / scope=all) returns typed items of every partition
        all_typed = [_ddb(r) for rows in PARTITIONS.values() for r in rows]
        paginator.paginate.return_value.build_full_result.return_value = {"Items": all_typed}

        inst = MagicMock(); inst.enforce.return_value = allow
        casbin.return_value = inst

    def test_databaseid_scope_returns_only_that_db(self, get_tag_types, tag_table, paginator, casbin):
        self._setup(get_tag_types, tag_table, paginator, casbin)
        result = get_tags({"maxItems": 100, "pageSize": 100, "startingToken": None,
                           "databaseId": "factory-db"})
        names = {t["tagName"] for t in result["Items"]}
        assert names == {"EquipID"}  # single-partition query: only factory-db
        tag_table.query.assert_called_once()

    def test_scope_global_returns_only_global(self, get_tag_types, tag_table, paginator, casbin):
        self._setup(get_tag_types, tag_table, paginator, casbin)
        result = get_tags({"maxItems": 100, "pageSize": 100, "startingToken": None,
                           "scope": "global"})
        names = {t["tagName"] for t in result["Items"]}
        assert names == {"Status", "GlobalX"}

    def test_no_scope_returns_all(self, get_tag_types, tag_table, paginator, casbin):
        self._setup(get_tag_types, tag_table, paginator, casbin)
        result = get_tags({"maxItems": 100, "pageSize": 100, "startingToken": None})
        names = {t["tagName"] for t in result["Items"]}
        assert names == {"Status", "GlobalX", "EquipID", "PatientT"}
        # 'all'/none uses a scan, not a single-partition query
        tag_table.query.assert_not_called()
