# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The listings keep a name that exists in two scopes as two distinct entries.

Creating a GLOBAL tag/tag type over a name a database already uses is allowed (with an advisory), so
both rows legitimately coexist. Anything that resolved a tag type BY NAME then crossed the two: the
tag-type listing attached one scope's tags to the other scope's type, and the tag listing's required
marker leaked across scopes. Both are keyed by scope AND name.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.tagTypes.tagTypeService import get_tag_types as list_tag_types
from backend.backend.handlers.tags.tagService import get_tags

CLAIMS = {"tokens": ["u"]}

# 'Line' exists in BOTH scopes: required in the database, optional globally, with its own tags.
TAG_TYPE_ROWS = [
    {"databaseId": {"S": "GLOBAL"}, "tagTypeName": {"S": "Line"},
     "description": {"S": "d"}, "required": {"S": "False"}},
    {"databaseId": {"S": "factory-db"}, "tagTypeName": {"S": "Line"},
     "description": {"S": "d"}, "required": {"S": "True"}},
]
TAG_ROWS = [
    {"databaseId": {"S": "GLOBAL"}, "tagName": {"S": "sharedLine"},
     "tagTypeName": {"S": "Line"}, "description": {"S": "d"}},
    {"databaseId": {"S": "factory-db"}, "tagName": {"S": "localLine"},
     "tagTypeName": {"S": "Line"}, "description": {"S": "d"}},
]


def _paginator(pages):
    """A scan paginator whose build_full_result returns `pages` in order of call."""
    calls = {"n": 0}

    def paginate(**kwargs):
        result = MagicMock()
        index = min(calls["n"], len(pages) - 1)
        result.build_full_result.return_value = {"Items": pages[index]}
        calls["n"] += 1
        return result

    client = MagicMock()
    client.get_paginator.return_value.paginate.side_effect = paginate
    return client


@pytest.mark.unit
@patch('backend.backend.handlers.tagTypes.tagTypeService.dynamodb_client')
@patch('backend.backend.handlers.tagTypes.tagTypeService.tag_type_table')
@patch('backend.backend.handlers.tagTypes.tagTypeService.CasbinEnforcer')
class TestTagTypeListingKeepsScopesDistinct:
    def _setup(self, casbin, dynamodb_client):
        # First paginate call: tag types (scope=all path). Second: the tags association lookup.
        client = _paginator([TAG_TYPE_ROWS, TAG_ROWS])
        dynamodb_client.get_paginator.side_effect = client.get_paginator.side_effect
        dynamodb_client.get_paginator.return_value = client.get_paginator.return_value
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        casbin.return_value = enforcer

    def test_each_scope_gets_only_its_own_tags(self, casbin, tag_type_table, dynamodb_client):
        self._setup(casbin, dynamodb_client)

        result = list_tag_types(
            {"maxItems": 100, "pageSize": 100, "startingToken": None}, CLAIMS
        )

        by_scope = {t["databaseId"]: t for t in result["Items"] if t["tagTypeName"] == "Line"}
        assert set(by_scope) == {"GLOBAL", "factory-db"}, "both scopes must be returned"
        assert by_scope["GLOBAL"]["tags"] == ["sharedLine"]
        assert by_scope["factory-db"]["tags"] == ["localLine"]
        # The required flag is per row and must not be conflated either.
        assert by_scope["GLOBAL"]["required"] == "False"
        assert by_scope["factory-db"]["required"] == "True"


@pytest.mark.unit
@patch('backend.backend.handlers.tags.tagService.dynamodb_client')
@patch('backend.backend.handlers.tags.tagService.paginator')
@patch('backend.backend.handlers.tags.tagService.tag_table')
@patch('backend.backend.handlers.tags.tagService.CasbinEnforcer')
@patch('backend.backend.handlers.tags.tagService.claims_and_roles', CLAIMS)
class TestTagListingRequiredMarkerIsScoped:
    def test_a_required_database_type_does_not_mark_a_global_tag(
        self, casbin, tag_table, paginator, dynamodb_client
    ):
        # Two paginate calls: tag types first (get_tag_types), then the tags themselves.
        pages = [TAG_TYPE_ROWS, TAG_ROWS]
        calls = {"n": 0}

        def paginate(**kwargs):
            result = MagicMock()
            result.build_full_result.return_value = {"Items": pages[min(calls["n"], 1)]}
            calls["n"] += 1
            return result

        paginator.paginate.side_effect = paginate
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        casbin.return_value = enforcer

        result = get_tags({"maxItems": 100, "pageSize": 100, "startingToken": None})

        marks = {t["tagName"]: t["tagTypeName"] for t in result["Items"]}
        # Only the database-scoped type is required, so only its tag carries the marker. Matching by
        # name alone marked both.
        assert marks["localLine"] == "Line [R]"
        assert marks["sharedLine"] == "Line"
