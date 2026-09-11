# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The shared paging stubs accept every page sequence DynamoDB produces, and reject the rest.

``tests/pagingStub.py`` is the fixture that ~10 paging test files assert through, so what it refuses
to model is effectively a claim about DynamoDB. It refused one shape DynamoDB genuinely produces: a
LAST read that carries a ``LastEvaluatedKey``.

Both halves of that are documented behaviour. A ``FilterExpression`` is applied after the page has
been read, so a page can come back with zero Items and still carry a key to continue from; and per the
``Query``/``Scan`` API reference, "If LastEvaluatedKey is not empty, it does not necessarily mean that
there is more data in the result set. The only way to know when you have reached the end of the result
set is when LastEvaluatedKey is empty." A present key is therefore not a promise of more matching
items -- which is also why a loop must page on the key's ABSENCE -- and a loop carrying a bound of its
own (a page cap, an item ``limit``, a response-byte budget) legitimately stops with a cursor
outstanding and emits it as a continuation token.

A stub that rejects legitimate behaviour makes correct code look broken, which is how a real paging
fix gets reverted. Every acceptance below therefore comes with the positive control that shows the
stub can still fail: the checks that remain are checks on sequences DynamoDB cannot produce, and on
loops that do not terminate.
"""

import pytest

from backend.tests.pagingStub import (BareMockReader, Pager, PagingLoopDidNotTerminate, RoutedPager)


def _presence_walk(read, **kwargs):
    """The required form: continue on the key's PRESENCE."""
    rows = []
    while True:
        page = read(**kwargs)
        rows.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return rows


def _value_walk(read, **kwargs):
    """The banned form, here only as the thing the stubs exist to catch."""
    rows = []
    while True:
        page = read(**kwargs)
        rows.extend(page.get("Items", []))
        key = page.get("LastEvaluatedKey")
        if not key:
            break
        kwargs["ExclusiveStartKey"] = key
    return rows


def _capped_walk(read, max_pages, **kwargs):
    """A loop with a bound of its own: it can stop with a cursor still outstanding."""
    rows = []
    for _ in range(max_pages):
        page = read(**kwargs)
        rows.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return rows


@pytest.mark.unit
class TestTheStubAcceptsWhatDynamoDbProduces:
    def test_a_page_with_zero_items_still_pages_on(self):
        """A FilterExpression can empty a page while the read still has somewhere to continue from."""
        pager = Pager(
            {"Items": [], "LastEvaluatedKey": {"id": "filtered-out"}},
            {"Items": [{"id": "match"}]},
            name="filtered walk",
        )

        rows = _presence_walk(pager)

        assert rows == [{"id": "match"}]
        pager.assert_paged_to_exhaustion()

    def test_the_last_read_may_carry_a_lastevaluatedkey(self):
        """The shape the stub used to reject outright, at construction time."""
        pager = Pager({"Items": [{"id": "a"}], "LastEvaluatedKey": {"id": "a"}}, name="one page")

        page = pager()

        assert page["Items"] == [{"id": "a"}]
        assert pager.trailing_key == {"id": "a"}

    def test_a_bounded_loop_stops_with_a_cursor_outstanding(self):
        """The loop's own cap fires on a page that carries a key; that is not a stub failure."""
        pager = Pager(
            {"Items": [{"id": "a"}], "LastEvaluatedKey": {"id": "a"}},
            {"Items": [{"id": "b"}], "LastEvaluatedKey": {"id": "b"}},
            name="capped walk",
        )

        rows = _capped_walk(pager, 2)

        assert rows == [{"id": "a"}, {"id": "b"}]
        # It reached the last scripted page, and the key that page carried is the continuation token
        # the caller would emit.
        pager.assert_paged_to_exhaustion()
        assert pager.trailing_key == {"id": "b"}

    def test_the_trailing_key_needs_no_page_that_is_never_read(self):
        """Padding a script with an unread page to satisfy the stub is what this removes."""
        pager = Pager({"Items": [{"id": "a"}], "LastEvaluatedKey": {"id": "a"}}, name="no padding")

        assert _capped_walk(pager, 1) == [{"id": "a"}]
        assert len(pager.pages) == 1


@pytest.mark.unit
class TestTheStubStillRejectsWhatItShould:
    """Positive controls. Every acceptance above is worthless if the stub cannot fail."""

    def test_a_continued_from_page_without_a_key_is_rejected(self):
        # DynamoDB omits the key only when the result set is exhausted, so a page a later page
        # continues from always has one -- and without it the stub has no cursor to key that page on.
        with pytest.raises(ValueError):
            Pager({"Items": [{"id": "a"}]}, {"Items": [{"id": "b"}]}, name="unreachable page")

    def test_an_empty_script_is_rejected(self):
        with pytest.raises(ValueError):
            Pager(name="no pages")

    def test_continuing_past_the_last_scripted_page_is_reported_as_that(self):
        """A loop that ignores its own bound must fail with the right diagnosis, not a wrong one."""
        pager = Pager({"Items": [{"id": "a"}], "LastEvaluatedKey": {"id": "a"}}, name="runs on")

        with pytest.raises(PagingLoopDidNotTerminate) as raised:
            _presence_walk(pager)

        message = str(raised.value)
        assert "scripted" in message, message
        # The cursor WAS handed out -- reporting it as invented sends the reader hunting the wrong bug.
        assert "never handed out" not in message, message

    def test_resuming_from_an_invented_cursor_is_still_reported_as_invented(self):
        pager = Pager({"Items": [{"id": "a"}]}, name="invented cursor")

        with pytest.raises(PagingLoopDidNotTerminate) as raised:
            pager(ExclusiveStartKey={"id": "never-handed-out"})

        assert "never handed out" in str(raised.value)

    def test_a_value_form_loop_hits_the_read_cap_and_raises(self):
        """The reason the cap exists: this loop would otherwise spin forever, naming no test."""
        reader = BareMockReader(name="value form", max_reads=4)

        with pytest.raises(PagingLoopDidNotTerminate):
            _value_walk(reader)

        assert len(reader.calls) > 1

    def test_a_presence_form_loop_reads_a_bare_mock_once(self):
        """The negative control for the cap: the required form is not what trips it."""
        reader = BareMockReader(name="presence form", max_reads=4)

        _presence_walk(reader)

        assert len(reader.calls) == 1


@pytest.mark.unit
class TestTheExhaustionAssertionCanFail:
    """A completeness assertion that cannot fail is the vacuity this suite keeps finding."""

    def test_it_fails_when_the_loop_stopped_short(self):
        pager = Pager(
            {"Items": [{"id": "a"}], "LastEvaluatedKey": {"id": "a"}},
            {"Items": [{"id": "b"}]},
            name="stops short",
        )

        pager()  # only the first page

        with pytest.raises(AssertionError):
            pager.assert_paged_to_exhaustion()

    def test_it_fails_when_nothing_read_the_pager_at_all(self):
        # A single-page script hands out no cursor, so without the read floor this would pass over a
        # loop that never ran -- the "iterating an empty collection" shape of a vacuous assertion.
        pager = Pager({"Items": [{"id": "a"}]}, name="never read")

        with pytest.raises(AssertionError):
            pager.assert_paged_to_exhaustion()

    def test_it_passes_once_the_last_scripted_page_is_reached(self):
        pager = Pager(
            {"Items": [{"id": "a"}], "LastEvaluatedKey": {"id": "a"}},
            {"Items": [{"id": "b"}]},
            name="reaches the end",
        )

        _presence_walk(pager)

        pager.assert_paged_to_exhaustion()

    def test_an_extra_repeated_read_is_not_a_failure(self):
        """Stated over the SET of cursors, so a strictly safer implementation still passes."""
        pager = Pager(
            {"Items": [{"id": "a"}], "LastEvaluatedKey": {"id": "a"}},
            {"Items": [{"id": "b"}]},
            name="reads twice",
        )

        _presence_walk(pager)
        pager(ExclusiveStartKey={"id": "a"})

        pager.assert_paged_to_exhaustion()


@pytest.mark.unit
class TestRoutedPagerKeepsEachSequenceIndependent:
    def test_each_route_is_served_and_asserted_on_its_own(self):
        routed = RoutedPager(
            on="IndexName",
            gsiA=Pager({"Items": [{"id": "a1"}], "LastEvaluatedKey": {"id": "a1"}},
                       {"Items": [{"id": "a2"}]}, name="gsiA"),
            gsiB=Pager({"Items": [{"id": "b1"}]}, name="gsiB"),
        )

        assert _presence_walk(routed, IndexName="gsiA") == [{"id": "a1"}, {"id": "a2"}]
        assert _presence_walk(routed, IndexName="gsiB") == [{"id": "b1"}]

        routed.assert_paged_to_exhaustion()

    def test_an_unrouted_read_is_reported(self):
        routed = RoutedPager(on="IndexName", gsiA=Pager({"Items": []}, name="gsiA"))

        with pytest.raises(PagingLoopDidNotTerminate):
            routed(IndexName="gsiUnknown")
