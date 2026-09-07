# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""workflowService has FOUR paged reads: two ``LastEvaluatedKey`` loops and two paginator-backed ones.

The two loops end on the PRESENCE of LastEvaluatedKey.

DynamoDB omits ``LastEvaluatedKey`` on the final page and never sets it empty, so presence is the
accurate contract. It is also the only form that stays finite against an under-stubbed reader:
``MagicMock.get('LastEvaluatedKey')`` answers with a truthy child mock forever, so the value form
HANGS instead of failing -- and a timeout raises no assertion, so it names no test.

The two loops, and what bounds each:

* ``find_workflow_id_owner`` -- BOUNDED by ``MAX_ID_LOOKUP_PAGES`` pages and returns a single id. It
  has NO ``try/except``, so a hang here is a hang of the request. It previously used the HYBRID form
  (``lek = resp.get(...)`` then ``if not isinstance(lek, dict) or not lek``), which DOES terminate
  against a bare mock -- no runtime assertion can distinguish it, which is why the AST guard in
  ``tests/common/test_paging_key_presence_form.py`` exists alongside these tests.
* ``_execution_count`` -- UNBOUNDED in pages. It accumulates an integer, not a set, so it cannot grow
  the response past the 6 MB Lambda limit (Rule 15); what is unbounded is the number of reads, and it
  runs once per authorized row of a user-facing listing page. That is a runtime cost tracked as an
  open item, not something to cap here: capping it would turn a reported total into a silently partial
  one, which is a contract decision. It is also best-effort (``except Exception`` -> ``None``), which
  is why the shared reader raises a ``BaseException`` -- an ``Exception`` would be swallowed and the
  hang would read as an ordinary degraded count.

**The other two paged reads -- ``get_all_workflows`` and ``get_database_workflows`` -- need their own
treatment, because they have no ``LastEvaluatedKey`` loop at all.** They page through the boto3
paginator (``paginator.paginate(...).build_full_result()``), which threads the cursor inside botocore,
so the tree-wide form guard (``tests/common/test_paging_key_presence_form.py``) has nothing to inspect
in them and says nothing about them in either direction: neither the value-form check nor its
structural converse ("this file pages, so it must test for the key") can see a paginator read.

Both ARE bounded, by two independent mechanisms. ``_pagination_config`` clamps ``MaxItems`` -- the
total the paginator accumulates into one response -- and ``PageSize`` to ``MAX_LIST_PAGE_ITEMS``; and
``_filtered_page`` then stops accumulating at ``MAX_LIST_PAGE_BYTES``, because the row cap alone does
not bound BYTES (a workflow row carries specifiedPipelines, systemConfig and the computed aggregates).
The caller reaches the rest of the set through ``NextToken`` either way.
``TestPaginatorBackedListsAreBounded`` below asserts both at BOTH call sites, and that a third
paginator read cannot be added to this module without a ``PaginationConfig`` -- which is the exact
failure Rule 15 names, ``build_full_result`` accumulating every record for a user-facing GET.

**Scope of that structural check, stated plainly: it is per-module and catches nothing outside this
file.** It parses ``workflowService.py`` only, exactly as the pipelineService twin parses its own
module. A paginator-backed read added to a THIRD backend module that has no such test of its own is
caught by nothing -- not by the tree-wide form guard (a paginator read has no continuation decision
for it to inspect) and not by these two per-module checks. Closing that would take a tree-walking
detector alongside ``tests/common/test_paging_key_presence_form.py``, which is an open item rather
than something this file can do.
"""

import ast
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from botocore.paginate import TokenDecoder

from backend.backend.handlers.workflows import workflowService as ws
from backend.tests.pagingStub import BareMockReader, Pager

MOD = "backend.backend.handlers.workflows.workflowService"

_WORKFLOW_SERVICE_SOURCE = pathlib.Path(ws.__file__).read_text(encoding="utf-8")

_CLAIMS = {"tokens": ["user1"], "roles": []}

# A bounded and an unbounded paginator read, as the positive/negative control for the detector below.
_PAGINATOR_FORMS = '''
def bounded(client, config):
    return client.get_paginator("query").paginate(
        TableName="t", PaginationConfig=config).build_full_result()


def unbounded(client):
    return client.get_paginator("query").paginate(TableName="t").build_full_result()
'''


def paginator_reads(source):
    """Every ``.paginate(...)`` call line. The anti-vacuity control for the check below."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "paginate"
    )


def paginator_reads_without_a_bound(source):
    """``.paginate(...)`` call lines carrying no ``PaginationConfig``, i.e. an unbounded accumulation."""
    return sorted(
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "paginate"
        and not any(keyword.arg == "PaginationConfig" for keyword in node.keywords)
    )


def _row(workflow_id):
    """A workflow row carrying every key a by-date-GSI continuation needs."""
    return {
        "databaseId": "db1",
        "workflowId": workflow_id,
        "workflowName": workflow_id,
        "allListPartition": "workflow",
        "dateModified": f"2026-01-01T00:00:0{workflow_id[-1]}Z",
    }


def _list_read(which, query_params, page=None):
    """Run one of the two paginator-backed list reads against a stubbed paginator.

    Returns ``(paginator_stub, response_model)`` so a test can assert on what was ASKED of the
    paginator and on what came back out of the filter/budget stage.
    """
    paginator = MagicMock()
    paginator.paginate.return_value.build_full_result.return_value = (
        {"Items": []} if page is None else page)

    with patch(f"{MOD}.dynamodb") as mock_dynamodb:
        mock_dynamodb.meta.client.get_paginator.return_value = paginator
        # Enrichment reads are per-row best-effort helpers with their own tests; stub them out so
        # this file measures the BOUND rather than re-testing the counts.
        with patch(f"{MOD}._execution_count", return_value=0), \
                patch(f"{MOD}._trigger_summary", return_value=None), \
                patch(f"{MOD}._batch_pipeline_system_configs", return_value={}):
            if which == "get_all_workflows":
                result = ws.get_all_workflows(query_params, False, _CLAIMS)
            else:
                result = ws.get_database_workflows("db1", query_params, False, _CLAIMS)

    return paginator, result


@pytest.mark.unit
class TestExecutionCountPaging:
    """A COUNT query is capped at 1 MB of SCANNED index per page, so the count needs every page."""

    def test_the_count_sums_every_page(self):
        pager = Pager(
            {"Count": 4, "LastEvaluatedKey": {"executionStartDate": "2026-01-01T00:00:00Z"}},
            {"Count": 2, "LastEvaluatedKey": {"executionStartDate": "2026-01-02T00:00:00Z"}},
            {"Count": 1},
            name="_execution_count",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch.object(ws.dynamodb, "Table", return_value=table):
            total = ws._execution_count("db1", "wf-a")

        assert total == 7
        # Asserted over the set of cursors rather than over read counts, so an extra read passes.
        pager.assert_paged_to_exhaustion()

    def test_the_count_query_survives_every_continuation(self):
        """A continuation that dropped Select=COUNT or the GSI would count the wrong thing."""
        pager = Pager(
            {"Count": 1, "LastEvaluatedKey": {"executionStartDate": "2026-01-01T00:00:00Z"}},
            {"Count": 1},
            name="_execution_count",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch.object(ws.dynamodb, "Table", return_value=table):
            ws._execution_count("db1", "wf-a")

        # `all()` over an empty sequence is True, so the three property assertions below are
        # vacuous unless the reader was actually driven. Assert the read count first: two
        # scripted pages means a continuation happened, which is what "survives every
        # continuation" claims.
        assert len(pager.calls) >= 2, (
            "the pager was not driven across a continuation, so the properties below would "
            f"hold vacuously: {pager.calls}")
        assert all(call["Select"] == "COUNT" for call in pager.calls), pager.calls
        assert all(call["IndexName"] == ws.WORKFLOW_EXECUTIONS_BY_WORKFLOW_GSI
                   for call in pager.calls), pager.calls
        assert all("KeyConditionExpression" in call for call in pager.calls), pager.calls

    def test_terminates_against_an_under_stubbed_reader(self):
        """The count is best-effort (``except Exception`` -> ``None``), so the guard must outrank it.

        A non-terminating loop here would hang the run; the capped reader raises a BaseException so it
        is neither swallowed by the best-effort arm nor reported as a timeout.
        """
        table = MagicMock()
        table.query.side_effect = BareMockReader(name="_execution_count")

        with patch.object(ws.dynamodb, "Table", return_value=table):
            total = ws._execution_count("db1", "wf-a")

        # A Mock Count is not a real number, so the total itself is meaningless -- what matters is
        # that the loop RETURNED rather than spun, and did not degrade to the error arm.
        assert total is not None

    def test_a_read_error_still_degrades_to_no_count(self):
        """Positive control for the assertion above: the error arm is reachable and returns None."""
        table = MagicMock()
        table.query.side_effect = Exception("throttled")

        with patch.object(ws.dynamodb, "Table", return_value=table):
            assert ws._execution_count("db1", "wf-a") is None


@pytest.mark.unit
class TestWorkflowIdOwnerLookupPaging:
    """BOUNDED: capped at MAX_ID_LOOKUP_PAGES pages and returns a single id, but the owning row can
    sit on any page within the cap -- and this loop has NO try/except, so a hang here is a real one."""

    def test_an_owner_on_a_later_page_is_found(self):
        pager = Pager(
            {"Items": [],
             "LastEvaluatedKey": {"allListPartition": "workflow", "dateModified": "1"}},
            {"Items": [{"databaseId": "owner-db", "workflowId": "wf1"}]},
            name="find_workflow_id_owner",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch(f"{MOD}._workflow_table", return_value=table):
            owner = ws.find_workflow_id_owner("wf1")

        assert owner == "owner-db"
        pager.assert_paged_to_exhaustion()

    def test_a_free_id_is_reported_free_after_every_page(self):
        """Positive control: paging to exhaustion must not report every id as taken."""
        pager = Pager(
            {"Items": [],
             "LastEvaluatedKey": {"allListPartition": "workflow", "dateModified": "1"}},
            {"Items": [{"databaseId": "db1", "workflowId": "someone-else"}]},
            name="find_workflow_id_owner",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch(f"{MOD}._workflow_table", return_value=table):
            # The row on page two belongs to the excluded database, so no other owner exists.
            owner = ws.find_workflow_id_owner("wf1", excluding_database_id="db1")

        assert owner is None
        pager.assert_paged_to_exhaustion()

    def test_the_query_stays_on_the_by_date_gsi_across_continuations(self):
        pager = Pager(
            {"Items": [],
             "LastEvaluatedKey": {"allListPartition": "workflow", "dateModified": "1"}},
            {"Items": []},
            name="find_workflow_id_owner",
        )
        table = MagicMock()
        table.query.side_effect = pager

        with patch(f"{MOD}._workflow_table", return_value=table):
            ws.find_workflow_id_owner("wf1")

        # A continuation that lost the index would resume the cursor against the base table.
        # Same vacuity guard as above: `all()` is True over no calls.
        assert len(pager.calls) >= 2, (
            f"the pager was not driven across a continuation: {pager.calls}")
        assert all(call["IndexName"] == "WorkflowsByDateGSI" for call in pager.calls), pager.calls
        assert all("FilterExpression" in call for call in pager.calls), pager.calls

    def test_terminates_against_an_under_stubbed_reader(self):
        table = MagicMock()
        # The reader must serve MORE reads than the loop's own page cap allows, or the bound below
        # cannot fail: the default stub cap (12) is under MAX_ID_LOOKUP_PAGES, so a value-form loop
        # would trip the stub instead of the assertion.
        reader = BareMockReader(name="find_workflow_id_owner",
                                max_reads=ws.MAX_ID_LOOKUP_PAGES + 5)
        table.query.side_effect = reader

        with patch(f"{MOD}._workflow_table", return_value=table):
            assert ws.find_workflow_id_owner("wf1") is None

        # The bare-Mock page advertises no LastEvaluatedKey, so the loop must have ended on that
        # rather than by exhausting its page cap. Stated as a direction-correct bound, not a count.
        assert len(reader.calls) < ws.MAX_ID_LOOKUP_PAGES, (
            f"the lookup read {len(reader.calls)} pages from a reader advertising no "
            f"LastEvaluatedKey, so it ended by exhausting MAX_ID_LOOKUP_PAGES "
            f"({ws.MAX_ID_LOOKUP_PAGES}) rather than on the absent key")


@pytest.mark.unit
class TestPaginatorBackedListsAreBounded:
    """The two paged reads the tree-wide form guard cannot see.

    ``paginator.paginate(...)`` threads the cursor inside botocore, so there is no continuation
    decision in the source for that guard to inspect -- a paginator read is neither flagged nor
    cleared by it. What has to hold instead is a bound on what one response accumulates (Rule 15),
    asserted here at both call sites, in BOTH of the two dimensions the response can grow in (rows
    and bytes), and structurally for whatever paginator read lands in this module next.
    """

    LIST_READS = ["get_all_workflows", "get_database_workflows"]

    def test_this_module_really_does_page_through_the_paginator(self):
        # Anti-vacuity: if these reads stopped using the paginator, the structural check below would
        # pass by finding nothing at all.
        found = paginator_reads(_WORKFLOW_SERVICE_SOURCE)
        assert len(found) >= len(self.LIST_READS), (
            f"only {len(found)} paginator read(s) found in {ws.__file__}, but the list endpoints are "
            "paginator-backed -- the structural check below would be scanning nothing")

    def test_no_paginator_read_here_accumulates_without_a_bound(self):
        """A THIRD paginator read added without a PaginationConfig fails here, not in production."""
        unbounded = paginator_reads_without_a_bound(_WORKFLOW_SERVICE_SOURCE)

        assert unbounded == [], (
            f"{ws.__file__} line(s) {unbounded}: paginate(...) with no PaginationConfig accumulates "
            "every matching record into one response, which is what Rule 15 forbids for a user-facing "
            "GET. Pass PaginationConfig=_pagination_config(query_params) so MaxItems is clamped to "
            "MAX_LIST_PAGE_ITEMS and the caller pages the rest through NextToken.")

    def test_the_detector_separates_a_bounded_read_from_an_unbounded_one(self):
        """Positive control: an empty result must mean bounded, never "nothing was recognised"."""
        assert len(paginator_reads(_PAGINATOR_FORMS)) == 2, _PAGINATOR_FORMS

        flagged = paginator_reads_without_a_bound(_PAGINATOR_FORMS)

        assert len(flagged) == 1, flagged
        # It flagged the unbounded one specifically, not just "one of them".
        assert "PaginationConfig" not in _PAGINATOR_FORMS.splitlines()[flagged[0] - 1]

    @pytest.mark.parametrize("which", LIST_READS)
    def test_an_enormous_request_is_clamped_before_it_reaches_the_paginator(self, which):
        paginator, _ = _list_read(
            which, {"maxItems": "999999", "pageSize": "999999", "startingToken": None})

        assert paginator.paginate.call_args is not None, (
            f"{which} never reached the paginator, so this asserts nothing")
        config = paginator.paginate.call_args.kwargs["PaginationConfig"]
        # UPPER bounds, in the direction that matters: a smaller cap -- a strictly safer
        # implementation -- passes, while an uncapped accumulation fails.
        assert config["MaxItems"] <= ws.MAX_LIST_PAGE_ITEMS, config
        assert config["PageSize"] <= config["MaxItems"], config

    @pytest.mark.parametrize("which", LIST_READS)
    def test_a_modest_request_is_not_satisfied_by_reading_nothing(self, which):
        """Positive control for the bounds above: they must not hold by clamping everything to zero."""
        paginator, _ = _list_read(which, {"maxItems": "7", "pageSize": "3", "startingToken": None})

        config = paginator.paginate.call_args.kwargs["PaginationConfig"]
        assert 0 < config["MaxItems"] <= 7, config
        assert 0 < config["PageSize"] <= config["MaxItems"], config

    @pytest.mark.parametrize("which", LIST_READS)
    def test_the_caller_can_resume_where_the_last_response_stopped(self, which):
        """The bound is only legitimate because the remainder is reachable: the token is threaded."""
        paginator, _ = _list_read(
            which, {"maxItems": "10", "pageSize": "10", "startingToken": "opaque-token"})

        assert paginator.paginate.call_args.kwargs["PaginationConfig"]["StartingToken"] == (
            "opaque-token")

    @pytest.mark.parametrize("which", LIST_READS)
    def test_the_paginators_own_next_token_is_passed_through(self, which):
        """A clamped page that the paginator itself stopped short of must still be pageable."""
        _, result = _list_read(
            which, {"maxItems": "10", "pageSize": "10", "startingToken": None},
            page={"Items": [_row("wf-1")], "NextToken": "paginator-token"})

        assert [i.workflowId for i in result.Items] == ["wf-1"]
        assert result.NextToken == "paginator-token"

    def test_a_page_over_the_byte_budget_is_trimmed_and_stays_pageable(self):
        """The row cap does not bound BYTES: MAX_LIST_PAGE_ITEMS rows can exceed the 6 MB Lambda
        response limit, which fails the whole request with no body and no NextToken. The byte budget
        has to stop the page AND hand back a cursor, or the trimmed rows become unreachable rather
        than deferred."""
        page = {"Items": [_row("wf-1"), _row("wf-2"), _row("wf-3")]}

        # A budget of one byte overflows on the second row whatever a workflow row weighs, so the
        # test does not depend on the size of the fixture.
        with patch.object(ws, "MAX_LIST_PAGE_BYTES", 1):
            _, result = _list_read(
                "get_all_workflows",
                {"maxItems": "10", "pageSize": "10", "startingToken": None}, page=page)

        # Trimmed, but not to nothing: the first row is always kept, or the caller reads the page as
        # "no workflows" and cannot page past it either.
        assert [i.workflowId for i in result.Items] == ["wf-1"]
        assert result.NextToken, "a trimmed page returned no cursor, so wf-2/wf-3 are unreachable"
        # The cursor resumes after the last row KEPT, not at the query's own end -- decoded through
        # botocore, which is the contract the caller relies on when passing it back as startingToken.
        resumed = TokenDecoder().decode(result.NextToken)["ExclusiveStartKey"]
        assert resumed["workflowId"] == "wf-1", resumed
        # A by-date-GSI cursor names the index keys as well as the table's, or DynamoDB rejects it.
        assert {"databaseId", "workflowId", "allListPartition", "dateModified"} <= set(resumed)

    def test_a_page_inside_the_byte_budget_keeps_every_row(self):
        """Positive control for the trim above: the budget must not truncate an ordinary page."""
        page = {"Items": [_row("wf-1"), _row("wf-2"), _row("wf-3")]}

        _, result = _list_read(
            "get_all_workflows",
            {"maxItems": "10", "pageSize": "10", "startingToken": None}, page=page)

        assert [i.workflowId for i in result.Items] == ["wf-1", "wf-2", "wf-3"]
        # Nothing was withheld, so nothing needs a cursor -- an unset NextToken here is the signal
        # the caller stops on.
        assert result.NextToken in (None, "")
