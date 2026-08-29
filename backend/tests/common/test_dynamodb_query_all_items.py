# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Paging behavior of the shared query_all_items helper.

A single DynamoDB query returns at most 1 MB of items, so reading only
response['Items'] truncates larger result sets. These tests pin the
LastEvaluatedKey loop that callers (asset-version file snapshots) depend on.

Every page here is served by CURSOR (``tests/pagingStub.Pager``), never by call index. A stub keyed
on call order turns an extra, retried, or reordered read into the wrong page and fails an
implementation that is strictly safer than the one it was written against; keyed on
``ExclusiveStartKey``, the same test asserts only what matters -- that the cursor was threaded and the
final page reached -- and its read cap turns a loop that never advances into a failure with a message
instead of a hang.
"""

import os
from unittest.mock import MagicMock

import pytest

from backend.tests.pagingStub import BareMockReader, Pager

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


def _table(pager):
    """A table stub whose ``query`` serves the pager's pages, keyed on ExclusiveStartKey."""
    table = MagicMock()
    table.query.side_effect = pager
    return table


@pytest.mark.unit
class TestQueryAllItems:
    def test_single_page_returns_all_items(self):
        pager = Pager({"Items": [{"fileKey": "/a"}, {"fileKey": "/b"}]}, name="query_all_items")

        items = query_all_items(_table(pager), KeyConditionExpression="pk=1")

        assert items == [{"fileKey": "/a"}, {"fileKey": "/b"}]
        # No continuation was needed, so no start key was ever supplied. Asserted over
        # every read rather than as a read COUNT: an extra or retried read is not a defect.
        assert pager.resumed_from == [], pager.calls

    def test_follows_last_evaluated_key_across_pages(self):
        pager = Pager(
            {"Items": [{"fileKey": "/a"}], "LastEvaluatedKey": {"pk": "1", "sk": "/a"}},
            {"Items": [{"fileKey": "/b"}], "LastEvaluatedKey": {"pk": "1", "sk": "/b"}},
            {"Items": [{"fileKey": "/c"}]},
            name="query_all_items",
        )

        items = query_all_items(_table(pager), KeyConditionExpression="pk=1")

        # Every page's items are returned, not just the first.
        assert items == [{"fileKey": "/a"}, {"fileKey": "/b"}, {"fileKey": "/c"}]
        # Each cursor the pager handed out was resumed from, which is what proves the final page
        # was reached. Stated over the SET of cursors, so no read count or read order is pinned.
        pager.assert_paged_to_exhaustion()

    def test_empty_result_returns_empty_list(self):
        pager = Pager({"Items": []}, name="query_all_items")

        assert query_all_items(_table(pager), KeyConditionExpression="pk=1") == []

    def test_missing_items_key_is_tolerated(self):
        pager = Pager({}, name="query_all_items")

        assert query_all_items(_table(pager), KeyConditionExpression="pk=1") == []

    def test_query_kwargs_are_passed_through(self):
        pager = Pager({"Items": []}, name="query_all_items")

        query_all_items(_table(pager), KeyConditionExpression="pk=1", IndexName="someIndex")

        assert all(call["KeyConditionExpression"] == "pk=1" for call in pager.calls), pager.calls
        assert all(call["IndexName"] == "someIndex" for call in pager.calls), pager.calls


@pytest.mark.unit
class TestQueryAllItemsPagesOnKeyPresence:
    """The loop ends on the ABSENCE of LastEvaluatedKey, not on a falsy value.

    DynamoDB omits the key on the final page, so presence is the accurate contract. It is also the
    only form that stays finite against an under-stubbed reader: ``MagicMock.get(...)`` answers with
    a truthy child mock forever, and the resulting loop HANGS the run rather than failing a test
    (backend/tests/CLAUDE.md, "A MagicMock never ends a paging loop").
    """

    def test_every_page_cursor_is_resumed_from(self):
        pager = Pager(
            {"Items": [{"fileKey": "/a"}], "LastEvaluatedKey": {"pk": "1", "sk": "/a"}},
            {"Items": [{"fileKey": "/b"}], "LastEvaluatedKey": {"pk": "1", "sk": "/b"}},
            {"Items": [{"fileKey": "/c"}]},
            name="query_all_items",
        )
        table = MagicMock()
        table.query.side_effect = pager

        items = query_all_items(table, KeyConditionExpression="pk=1")

        assert [item["fileKey"] for item in items] == ["/a", "/b", "/c"]
        # Asserted over the set of cursors rather than over read counts, so an extra read passes.
        pager.assert_paged_to_exhaustion()

    def test_the_query_kwargs_survive_every_continuation(self):
        """A continuation that dropped the key condition would read the wrong rows, not fewer."""
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"pk": "1", "sk": "/a"}},
            {"Items": [{"fileKey": "/b"}]},
            name="query_all_items",
        )
        table = MagicMock()
        table.query.side_effect = pager

        query_all_items(table, KeyConditionExpression="pk=1", IndexName="someIndex")

        assert all(call["KeyConditionExpression"] == "pk=1" for call in pager.calls), pager.calls
        assert all(call["IndexName"] == "someIndex" for call in pager.calls), pager.calls

    def test_terminates_against_an_under_stubbed_reader(self):
        """The regression guard for the loop FORM, not for the helper output.

        The reader raises after a capped number of reads, so the value form fails with a message
        instead of hanging -- a timeout names no test.
        """
        table = MagicMock()
        table.query.side_effect = BareMockReader(name="query_all_items")

        assert query_all_items(table, KeyConditionExpression="pk=1") == []
