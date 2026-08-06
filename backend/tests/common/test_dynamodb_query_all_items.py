# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Paging behavior of the shared query_all_items helper.

A single DynamoDB query returns at most 1 MB of items, so reading only
response['Items'] truncates larger result sets. These tests pin the
LastEvaluatedKey loop that callers (asset-version file snapshots) depend on.
"""

import importlib.util
import os

import pytest

# tests/conftest.py replaces the whole `common.dynamodb` module with a MagicMock (the real
# one bootstraps AWS clients at import), so load this single function from source instead of
# importing the package — the helper is pure and needs no AWS.
_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "backend", "common", "dynamodb.py"
)


def _load_query_all_items():
    """Extract query_all_items from the real module source without importing the package."""
    with open(_MODULE_PATH, encoding="utf-8") as f:
        source = f.read()

    start = source.index("def query_all_items(")
    end = source.index("\ndef ", start)
    namespace = {"List": list, "Dict": dict}
    exec(compile(source[start:end], _MODULE_PATH, "exec"), namespace)
    return namespace["query_all_items"]


query_all_items = _load_query_all_items()


class FakeTable:
    """Table stub returning a scripted sequence of query pages."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages[len(self.calls) - 1]


@pytest.mark.unit
class TestQueryAllItems:
    def test_single_page_returns_all_items(self):
        table = FakeTable([{"Items": [{"fileKey": "/a"}, {"fileKey": "/b"}]}])

        items = query_all_items(table, KeyConditionExpression="pk=1")

        assert items == [{"fileKey": "/a"}, {"fileKey": "/b"}]
        assert len(table.calls) == 1
        # No continuation was needed, so no start key was ever supplied.
        assert "ExclusiveStartKey" not in table.calls[0]

    def test_follows_last_evaluated_key_across_pages(self):
        table = FakeTable([
            {"Items": [{"fileKey": "/a"}], "LastEvaluatedKey": {"pk": "1", "sk": "/a"}},
            {"Items": [{"fileKey": "/b"}], "LastEvaluatedKey": {"pk": "1", "sk": "/b"}},
            {"Items": [{"fileKey": "/c"}]},
        ])

        items = query_all_items(table, KeyConditionExpression="pk=1")

        # Every page's items are returned, not just the first.
        assert items == [{"fileKey": "/a"}, {"fileKey": "/b"}, {"fileKey": "/c"}]
        assert len(table.calls) == 3
        # Each continuation passes the prior page's LastEvaluatedKey.
        assert table.calls[1]["ExclusiveStartKey"] == {"pk": "1", "sk": "/a"}
        assert table.calls[2]["ExclusiveStartKey"] == {"pk": "1", "sk": "/b"}

    def test_empty_result_returns_empty_list(self):
        table = FakeTable([{"Items": []}])

        assert query_all_items(table, KeyConditionExpression="pk=1") == []

    def test_missing_items_key_is_tolerated(self):
        table = FakeTable([{}])

        assert query_all_items(table, KeyConditionExpression="pk=1") == []

    def test_query_kwargs_are_passed_through(self):
        table = FakeTable([{"Items": []}])

        query_all_items(table, KeyConditionExpression="pk=1", IndexName="someIndex")

        assert table.calls[0]["KeyConditionExpression"] == "pk=1"
        assert table.calls[0]["IndexName"] == "someIndex"
