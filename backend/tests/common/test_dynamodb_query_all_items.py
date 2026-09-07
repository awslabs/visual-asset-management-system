# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Paging behavior of the shared query_all_items and query_has_match helpers.

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


def _load_helper(name):
    """Extract one helper from the real module source without importing the package.

    Raises rather than returning None when the function is absent: a helper that silently failed to
    load leaves the tests below raising NameError, which reads as a broken test rather than a missing
    source function.
    """
    with open(_MODULE_PATH, encoding="utf-8") as f:
        source = f.read()

    marker = f"def {name}("
    if marker not in source:
        raise AssertionError(
            f"{name} is not defined in {_MODULE_PATH}. These tests load the helper from source "
            "because tests/conftest.py replaces the whole common.dynamodb module with a MagicMock, "
            "so a renamed or removed helper cannot surface as an ImportError here."
        )
    start = source.index(marker)
    end = source.index("\ndef ", start)
    namespace = {"List": list, "Dict": dict}
    exec(compile(source[start:end], _MODULE_PATH, "exec"), namespace)
    return namespace[name]


query_all_items = _load_helper("query_all_items")
query_has_match = _load_helper("query_has_match")


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

        # Set equality over the (condition, index) pairs actually sent, rather than `all(...)` over a
        # list that may be empty: a walk that read nothing satisfies `all(...)` and would report the
        # kwargs as preserved having sent none.
        assert {(call["KeyConditionExpression"], call["IndexName"]) for call in pager.calls} == {
            ("pk=1", "someIndex")
        }, pager.calls


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

        assert {(call["KeyConditionExpression"], call["IndexName"]) for call in pager.calls} == {
            ("pk=1", "someIndex")
        }, pager.calls

    def test_terminates_against_an_under_stubbed_reader(self):
        """The regression guard for the loop FORM, not for the helper output.

        The reader raises after a capped number of reads, so the value form fails with a message
        instead of hanging -- a timeout names no test.
        """
        table = MagicMock()
        table.query.side_effect = BareMockReader(name="query_all_items")

        assert query_all_items(table, KeyConditionExpression="pk=1") == []


@pytest.mark.unit
class TestQueryHasMatch:
    """An existence check decided from one page is a false negative.

    DynamoDB applies a FilterExpression AFTER the 1 MB page read, so empty `Items` alongside a
    present LastEvaluatedKey is the normal shape for "the match is on a later page". The relationship
    flags on an indexed asset are exactly that read: an asset with thousands of links returns an empty
    first page while its one link of the filtered type sits beyond it.
    """

    def test_a_match_on_a_later_page_is_found(self):
        """The defect arm: one page short of the match answers False without this loop."""
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"pk": "1", "sk": "/a"}},
            {"Items": [], "LastEvaluatedKey": {"pk": "1", "sk": "/b"}},
            {"Items": [{"relationshipType": "parentChild"}]},
            name="query_has_match",
        )

        assert query_has_match(_table(pager), KeyConditionExpression="pk=1") is True
        pager.assert_paged_to_exhaustion()

    def test_a_genuinely_absent_match_is_false(self):
        """Paired control: without it a helper returning True unconditionally passes the arm above."""
        pager = Pager({"Items": []}, name="query_has_match")

        assert query_has_match(_table(pager), KeyConditionExpression="pk=1") is False
        assert pager.resumed_from == [], pager.calls

    def test_it_stops_at_the_first_matching_page(self):
        """A match near the start must not cost the whole walk. The pager's LAST page carries a
        cursor, which DynamoDB genuinely produces and a short-circuiting caller legitimately leaves
        outstanding -- so resuming from it would be reported as running off the script."""
        pager = Pager(
            {"Items": [{"relationshipType": "related"}],
             "LastEvaluatedKey": {"pk": "1", "sk": "/a"}},
            name="query_has_match",
        )

        assert query_has_match(_table(pager), KeyConditionExpression="pk=1") is True
        assert pager.resumed_from == [], pager.calls

    def test_the_query_kwargs_survive_every_continuation(self):
        """A continuation that dropped the FilterExpression would answer about the wrong rows."""
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"pk": "1", "sk": "/a"}},
            {"Items": [{"relationshipType": "parentChild"}]},
            name="query_has_match",
        )

        query_has_match(_table(pager), KeyConditionExpression="pk=1",
                        IndexName="fromAssetGSI", FilterExpression="type=parentChild")

        # It stopped at the first non-empty page rather than draining the partition: an upper bound,
        # paired with a non-emptiness guard so a walk that read nothing cannot satisfy it.
        assert pager.calls, "the pager was never read, so nothing here is about short-circuiting"
        assert len(pager.calls) <= 2, pager.calls
        assert {(call["IndexName"], call["FilterExpression"]) for call in pager.calls} == {
            ("fromAssetGSI", "type=parentChild")
        }, pager.calls

    def test_terminates_against_an_under_stubbed_reader(self):
        """The loop FORM guard, and the answer here is True rather than False.

        A bare `MagicMock` answers `.get('Items')` with a truthy child mock, so the first page
        already looks like a match and the walk stops there -- which is why the assertion is on the
        READ COUNT, not on the boolean: an under-stubbed reader cannot say anything about the
        result, only about termination. The reader raises past its cap, so a value-form loop fails
        with a message instead of hanging the run (a timeout names no test).
        """
        reader = BareMockReader(name="query_has_match")
        table = MagicMock()
        table.query.side_effect = reader

        query_has_match(table, KeyConditionExpression="pk=1")

        # Both directions, because each catches a different failure: the non-emptiness guard catches a
        # reader that was never consulted (which would otherwise "terminate" trivially), and the upper
        # bound catches a walk that kept paging past the first apparent match.
        assert reader.calls, "the reader was never consulted, so termination proves nothing"
        assert len(reader.calls) <= 1, reader.calls

    def test_pages_on_key_presence_rather_than_on_a_truthy_value(self):
        """The termination decision must be the key's ABSENCE.

        `query_has_match` short-circuits on a matching page, so the bare-mock arm above cannot reach
        the continuation branch at all. This one does: every page is empty, so the only thing that
        can end the walk is the missing key on the last page.
        """
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"pk": "1", "sk": "/a"}},
            {"Items": [], "LastEvaluatedKey": {"pk": "1", "sk": "/b"}},
            {"Items": []},
            name="query_has_match",
        )

        assert query_has_match(_table(pager), KeyConditionExpression="pk=1") is False
        pager.assert_paged_to_exhaustion()
