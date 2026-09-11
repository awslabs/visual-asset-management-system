# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared stubs for asserting that a DynamoDB paging loop threads its cursor AND terminates.

Every paged read must test for the PRESENCE of ``LastEvaluatedKey``
(``if 'LastEvaluatedKey' not in response: break``) rather than for its value. Presence is the
accurate DynamoDB contract: the key is OMITTED once the result set is exhausted and is never set
empty, and its ABSENCE is the only end-of-set signal there is -- a key that IS present does not
promise that more matching items exist (DynamoDB API Reference, ``Query``/``Scan``: "If
LastEvaluatedKey is not empty, it does not necessarily mean that there is more data in the result
set"). Presence is also the only form that stays finite against an under-stubbed reader:
``MagicMock.get('LastEvaluatedKey')`` answers with a truthy child mock forever, while
``'LastEvaluatedKey' in mock`` answers ``False`` (backend/tests/CLAUDE.md, "A MagicMock never ends a
paging loop").

The value form does not fail, it HANGS. One such loop ran the backend suite past 600 s against a
167 s baseline, and because a timeout raises no assertion it names no test: whoever has to debug it
starts with nothing. Both readers here therefore CAP the number of reads they will serve and raise on
the cap, which is what turns a non-terminating loop into a diagnosable failure with a message.

The cap is per instance (``max_reads=``) because the default sits BELOW the page caps some loops carry
of their own (``MAX_ID_LOOKUP_PAGES``, ``MAX_REFERENCING_WORKFLOW_PAGES``). A test that asserts a bound
against such a cap -- "it stopped on the key, not by exhausting its pages" -- can only fail if the stub
is willing to serve more reads than the loop's own cap allows, so raise ``max_reads`` above that cap.
Left at the default, the loop hits the stub's cap first and the bound is unfailable: a vacuous
assertion wearing a bound's clothing.

``PagingLoopDidNotTerminate`` derives from ``BaseException`` deliberately. Several of the loops these
stubs feed sit inside best-effort helpers that catch ``Exception`` and degrade quietly
(``pipelineService._template_count`` returns ``None``, ``_referencing_workflow_labels`` returns
``[]``, ``tagService.get_tags`` re-raises as a generic error), so an ``Exception`` here would be
swallowed and the failure would read as an ordinary degraded result.

Pages are served by CURSOR, not by call order, so what gets asserted is "the cursor is threaded"
rather than "exactly N reads happened": an extra, retried, or repeated read still resolves to the
right page and does not fail a strictly-safer implementation.
"""

import json
from unittest.mock import MagicMock


class PagingLoopDidNotTerminate(BaseException):
    """A paged read asked for more pages than the stub was told to serve."""


def _cursor(key):
    """A hashable identity for an ``ExclusiveStartKey`` value (``None`` for the first read)."""
    return json.dumps(key, sort_keys=True, default=repr)


class Pager:
    """A ``query``/``scan`` stub that serves scripted pages keyed on ``ExclusiveStartKey``.

    Pass the pages in order, shaped as DynamoDB shapes them. Every page a following page continues
    from carries the ``LastEvaluatedKey`` that reaches it -- pages are keyed on that cursor, so a page
    without one leaves the next unreachable.

    The LAST scripted page MAY carry a ``LastEvaluatedKey``, because DynamoDB genuinely produces that
    response and a correct loop can legitimately stop on it:

    * a page filtered down to zero Items still carries a key and still pages on (``FilterExpression``
      is applied after the page has been read), so a present key is not a promise of more matching
      items -- only its ABSENCE ends the result set;
    * a loop with a bound of its own (``MAX_ID_LOOKUP_PAGES``, an item ``limit``, a response-byte
      budget) stops with that cursor outstanding and emits it as a continuation token.

    Rejecting that shape made a legitimate sequence unscriptable and forced tests to append a page
    that is never read, which is how a real paging fix ends up looking broken.
    """

    MAX_READS = 12

    def __init__(self, *pages, name="paged read", max_reads=None):
        if not pages:
            raise ValueError("a Pager needs at least one page")
        for page in pages[:-1]:
            if "LastEvaluatedKey" not in page:
                raise ValueError(
                    "every page a later page continues from needs a LastEvaluatedKey: pages are "
                    "keyed on that cursor, so a page without one leaves the next unreachable -- and "
                    "DynamoDB omits the key only once the result set is exhausted, i.e. when there is "
                    "no next page")
        self.name = name
        self.max_reads = self.MAX_READS if max_reads is None else max_reads
        self.pages = {_cursor(None): pages[0]}
        for previous, page in zip(pages, pages[1:]):
            self.pages[_cursor(previous["LastEvaluatedKey"])] = page
        # The cursors a following page answers, i.e. the ones a loop must resume from to reach the last
        # scripted page. A key on the LAST page is handed out but answers to no page: the loop is
        # entitled to stop there, and one that continues instead runs off the end of the script, which
        # is reported as such rather than as an invented cursor.
        self.handed_out = [page["LastEvaluatedKey"] for page in pages[:-1]]
        self.trailing_key = pages[-1].get("LastEvaluatedKey")
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) > self.max_reads:
            raise PagingLoopDidNotTerminate(
                f"{self.name}: did not terminate after {self.max_reads} reads over "
                f"{len(self.pages)} pages. Either it pages on the VALUE of LastEvaluatedKey instead "
                "of its PRESENCE, or it never passes the previous page's key back as "
                "ExclusiveStartKey.")
        cursor = _cursor(kwargs.get("ExclusiveStartKey"))
        if cursor not in self.pages:
            if self.trailing_key is not None and cursor == _cursor(self.trailing_key):
                raise PagingLoopDidNotTerminate(
                    f"{self.name}: continued past the last scripted page, resuming from the "
                    "LastEvaluatedKey that page carried. Either script the page that key leads to, "
                    "or -- if the loop was expected to stop on a bound of its own -- that bound did "
                    "not hold.")
            raise PagingLoopDidNotTerminate(
                f"{self.name}: resumed from a cursor this pager never handed out: "
                f"{kwargs.get('ExclusiveStartKey')!r}")
        return self.pages[cursor]

    @property
    def resumed_from(self):
        """Every ``ExclusiveStartKey`` the loop actually sent, in read order."""
        return [call["ExclusiveStartKey"] for call in self.calls if "ExclusiveStartKey" in call]

    def assert_paged_to_exhaustion(self):
        """Every cursor a later page answers was resumed from, so the last scripted page was reached.

        Stated over the SET of cursors rather than over read counts or read order, so an extra or
        repeated read is not a failure. The read floor is what keeps a single-page script from making
        this assertion vacuous: with nothing to resume from, "it reached the end" would otherwise hold
        for a loop that never read at all.
        """
        assert self.calls, (
            f"{self.name}: nothing read this pager, so it says nothing about reaching the final page")
        for cursor in self.handed_out:
            assert cursor in self.resumed_from, (
                f"{self.name}: page cursor {cursor!r} was never resumed from, so the loop stopped "
                f"short of the final page. Cursors resumed from: {self.resumed_from!r}")


class RoutedPager:
    """Independent paged reads on one table stub, routed by a read kwarg such as ``IndexName``.

    A handler that pages two GSIs off the same table needs each read served from its own page
    sequence; routing on a kwarg keeps that independent of call order.
    """

    def __init__(self, on, **pagers):
        self.on = on
        self.pagers = pagers

    def __call__(self, **kwargs):
        route = kwargs.get(self.on)
        pager = self.pagers.get(route)
        if pager is None:
            raise PagingLoopDidNotTerminate(
                f"unrouted read: {self.on}={route!r} is not one of {sorted(self.pagers)}")
        return pager(**kwargs)

    def assert_paged_to_exhaustion(self):
        for pager in self.pagers.values():
            pager.assert_paged_to_exhaustion()


class BareMockReader:
    """A reader whose every page is a bare ``MagicMock`` -- what an under-stubbed fixture hands a loop.

    This is the shape that hangs rather than fails: ``.get('LastEvaluatedKey')`` is truthy forever,
    while ``'LastEvaluatedKey' in page`` is ``False``. A loop written on key presence reads once and
    stops; the value form reads until the cap and raises with an explanation.
    """

    MAX_READS = 12

    def __init__(self, name="paged read", max_reads=None):
        self.name = name
        # Raise this above a loop's OWN page cap when the test bounds the read count against that cap
        # (see the module docstring): a stub cap below it makes such an assertion unfailable.
        self.max_reads = self.MAX_READS if max_reads is None else max_reads
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) > self.max_reads:
            raise PagingLoopDidNotTerminate(
                f"{self.name}: did not terminate against an under-stubbed reader after "
                f"{self.max_reads} reads. A MagicMock answers .get('LastEvaluatedKey') with a truthy "
                "child mock forever; test for the key's PRESENCE instead "
                "(if 'LastEvaluatedKey' not in response: break).")
        return MagicMock()
