# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-022: the global executions list must fill a page, not return whatever one query survived.

`get_global_executions` issues exactly ONE `main_table.query()` per request, then drops rows twice:
server-side via the `FilterExpression` built from the equality filters, and again in Python via the
per-row visibility check (`_execution_visible_to_caller`). Both filters are applied AFTER the
`Limit=page_size` has already been spent, so a caller whose role sees a small slice of the executions
— or who filters by status — routinely gets an EMPTY `Items` list together with a `NextToken`. The web
ExecutionsBoard renders that as an empty table with an active Next button, and the MCP `list_executions`
tool stops at `config.max_pages` before it reaches anything.

The fix loops the underlying query inside the request until `page_size` post-filter rows are collected
or a work budget is spent (the asset-scoped list already does this with `inspect_cap`), and marks a
page that came back empty behind a continuation.

Four invariants the loop must not break, each pinned below:
  - It must be BOUNDED. There is no page-count budget today; `_arm_authz_entity_budget` bounds
    authorization BREADTH only. An unbounded inner loop turns an empty page into a 504 at the API
    Gateway integration timeout, which is strictly worse than an empty page.
  - The per-page caches and the entity budget are armed ONCE before the walk. Re-arming inside the
    loop makes the entity budget unbounded; re-clearing defeats the memoisation.
  - The NextToken must come from the LAST query issued, and the entity-bound synthesized fallback
    must still fire, or rows the bound withheld become unreachable instead of deferred.
  - `result['warnings']` already carries the entity-bound message. A second reason must APPEND.

executionService resolves its table names at import (mirrors test_executions_authz_bound.py)."""

import base64
import contextlib
import json
import os
from weakref import WeakKeyDictionary

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.common.workflows import executionRecords as er  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"

PAGE_SIZE = 5

# Queries a bounded walk must stay under. Not the exact budget the fix picks -- a generous ceiling, so
# any sane work budget passes and a one-query-per-request implementation (or an unbounded loop) does
# not.
QUERY_CALL_CEILING = 40

# Where the stub gives up rather than let an unbounded loop hang the suite.
RUNAWAY_QUERY_CALLS = 400


@pytest.fixture(autouse=True)
def _clear_caches():
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    le._disarm_authz_entity_budget()
    yield
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    le._disarm_authz_entity_budget()


def _rows(prefix, count, status="SUCCEEDED"):
    """Main rows carrying the by-date GSI's keys AND the base table's, which is what a continuation
    key names."""
    return [{"workflowExecutionId": f"{prefix}{i}", "workflowId": "wf", "workflowDatabaseId": "db",
             "workflowDatabaseId:workflowId": "db:wf",
             "allListPartition": er.ALL_EXECUTIONS_LIST_PARTITION,
             "executionStatus": status,
             "executionStartDate": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z"}
            for i in range(count)]


def _page(rows, last_key=None):
    page = {"Items": rows}
    if last_key is not None:
        page["LastEvaluatedKey"] = last_key
    return page


def _hidden_asset_enforcer():
    """Allows everything except assets whose id starts with 'hidden' — the shape of a role scoped to a
    few databases, which is what makes a filtered page empty."""
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.enforce.side_effect = lambda obj, action, *a, **k: not (
        obj.get("object__type") == "asset" and str(obj.get("assetId", "")).startswith("hidden"))
    return enforcer


def _allow_all():
    enforcer = MagicMock()
    enforcer.enforce.return_value = True
    enforcer.enforceAPI.return_value = True
    return enforcer


def _run_global_list(pages, input_assets_for, enforcer, query=None, repeat_last=False,
                     config_for=None, clock=None, extra_patches=()):
    """Run the global list over `pages` (DynamoDB-shaped query responses, served in order).

    Returns (parsed message, list of query kwargs). Once `pages` is exhausted the stub returns an
    empty page with no LastEvaluatedKey, so a finite scenario terminates. With `repeat_last` the final
    page (and its LastEvaluatedKey) is served forever, modelling an index the walk can never exhaust —
    until RUNAWAY_QUERY_CALLS, where it raises so an unbounded loop FAILS rather than hangs.

    `config_for(execution_id)` supplies each row's configuration row (default `{}`), which is what makes
    the output-asset gate (FIX-009) reachable through this endpoint. `clock` replaces the module's `time`
    so the wall-clock budget can be driven deterministically. `extra_patches` are additional context
    managers entered around the call."""
    table = MagicMock()
    calls = []

    def _query(**kwargs):
        calls.append(kwargs)
        if len(calls) > RUNAWAY_QUERY_CALLS:
            raise AssertionError(
                f"the global list was still querying after {RUNAWAY_QUERY_CALLS} calls")
        index = len(calls) - 1
        if index < len(pages):
            return pages[index]
        return pages[-1] if repeat_last else {"Items": []}

    table.query.side_effect = _query
    le.claims_and_roles = {"tokens": ["u1"]}
    with contextlib.ExitStack() as stack:
        ddb = stack.enter_context(patch(f"{MOD}.dynamodb"))
        stack.enter_context(patch(f"{MOD}.CasbinEnforcer", return_value=enforcer))
        stack.enter_context(patch(f"{MOD}.get_execution_input_assets", side_effect=input_assets_for))
        stack.enter_context(patch(f"{MOD}.get_workflow_execution_configuration_row",
                                  side_effect=config_for or (lambda execution_id: {})))
        stack.enter_context(patch(
            f"{MOD}.get_asset_details",
            side_effect=lambda d, a: {"databaseId": d, "assetId": a, "assetName": a}))
        if clock is not None:
            fake_time = MagicMock()
            fake_time.monotonic.side_effect = clock
            stack.enter_context(patch(f"{MOD}.time", fake_time))
        for extra in extra_patches:
            stack.enter_context(extra)
        ddb.Table.return_value = table
        ddb.batch_get_item.side_effect = lambda RequestItems: {
            "Responses": {name: [{"databaseId": k["databaseId"], "assetId": k["assetId"],
                                  "assetName": k["assetId"]}
                                 for k in spec["Keys"]]
                          for name, spec in RequestItems.items()}}
        response = le.get_global_executions({}, query or {"pageSize": str(PAGE_SIZE)})
    return json.loads(response["body"])["message"], calls


def _clock(trip_after):
    """A `monotonic()` that reads 0 for its first `trip_after` calls and far past the deadline after.

    Call 1 is the walk's `deadline = monotonic() + budget`; each subsequent call is either a per-row
    check or the query-boundary check, so `trip_after` selects exactly where the budget expires."""
    state = {"n": 0}

    def _monotonic():
        state["n"] += 1
        return 0.0 if state["n"] <= trip_after else 10_000.0

    return _monotonic


def _visibility_by_prefix(execution_id):
    """Rows named 'h*' read a hidden asset; rows named 'v*' read a visible one."""
    return [("db", "hidden-asset" if execution_id.startswith("h") else "visible-asset")]


def _empty_page_marker(message):
    """Whether the response tells the caller this page was cut short by filtering rather than by there
    being nothing left. Accepts either shape the recommendation names — a `warnings` entry or a
    `filteredEmptyPage` flag — so the assertion does not prescribe the wording."""
    return bool(message.get("warnings")) or bool(message.get("filteredEmptyPage"))


@pytest.mark.unit
class TestTheWalkFillsAPage:
    """A page whose rows are all filtered out must not end the request."""

    def test_a_fully_filtered_page_continues_to_the_next_query(self):
        """FIX-022: the walk collects page_size visible rows across as many queries as it takes, and
        the NextToken comes from the LAST query issued."""
        pages = [_page(_rows("h", PAGE_SIZE), last_key={"p": 1}),
                 _page(_rows("v", PAGE_SIZE), last_key={"p": 2})]
        message, calls = _run_global_list(pages, _visibility_by_prefix, _hidden_asset_enforcer())
        assert len(message["Items"]) == PAGE_SIZE, (
            f"the page came back with {len(message['Items'])} of {PAGE_SIZE} rows")
        assert len(calls) == 2, f"the walk issued {len(calls)} queries"
        assert "NextToken" in message
        assert json.loads(base64.b64decode(message["NextToken"])) == {"p": 2}, (
            "the continuation must resume after the LAST query, not the first")

    def test_a_walk_that_can_never_fill_a_page_is_bounded_and_says_so(self):
        """FIX-022: the over-fetch guard. Every page is fully filtered out and the index never
        exhausts, so the request must stop on its own work budget and TELL the caller the page was cut
        short — an empty `Items` with a `NextToken` and no explanation is the defect, and an unbounded
        loop is a 504, which is worse."""
        pages = [_page(_rows("h", PAGE_SIZE), last_key={"p": 1})]
        message, calls = _run_global_list(
            pages, _visibility_by_prefix, _hidden_asset_enforcer(), repeat_last=True)
        assert len(calls) > 1, "the walk never advanced past the first query"
        assert len(calls) <= QUERY_CALL_CEILING, (
            f"the walk issued {len(calls)} queries; it must stop on a work budget")
        assert _empty_page_marker(message), (
            "a page cut short by filtering must be distinguishable from the end of the list")

    def test_a_full_page_of_visible_rows_costs_exactly_one_query(self):
        """FIX-022 control: the permitted case stays cheap.

        Without this, an over-eager loop that re-reads even when the first query already filled the
        page passes the walk test while doubling the cost of every list call. Passes today and must
        keep passing after the fix."""
        pages = [_page(_rows("v", PAGE_SIZE), last_key={"p": 1})]
        message, calls = _run_global_list(pages, _visibility_by_prefix, _hidden_asset_enforcer())
        assert len(message["Items"]) == PAGE_SIZE
        assert len(calls) == 1, f"a full first page still cost {len(calls)} queries"
        assert json.loads(base64.b64decode(message["NextToken"])) == {"p": 1}

    def test_a_full_page_carries_no_empty_page_marker(self):
        """FIX-022 control: the marker must be conditional.

        Without this, the bounded-walk test above is satisfied by a marker emitted on every response,
        which tells a caller nothing. Passes today and must keep passing after the fix."""
        pages = [_page(_rows("v", PAGE_SIZE), last_key={"p": 1})]
        message, _calls = _run_global_list(pages, _visibility_by_prefix, _hidden_asset_enforcer())
        assert not _empty_page_marker(message)

    def test_a_genuinely_exhausted_index_ends_the_list(self):
        """FIX-022 control: the walk must still terminate when DynamoDB reports no continuation.

        A row filtered out on the LAST page must not produce a NextToken — otherwise the caller pages
        forever. Passes today and must keep passing after the fix."""
        pages = [_page(_rows("h", PAGE_SIZE))]  # no LastEvaluatedKey
        message, _calls = _run_global_list(pages, _visibility_by_prefix, _hidden_asset_enforcer())
        assert message["Items"] == []
        assert "NextToken" not in message


@pytest.mark.unit
class TestTheWalkKeepsTheServerSideFilter:
    """The equality filters are attached as a `FilterExpression` so unmatched rows drop before the
    per-row authorization fan-out. Every query in the walk must carry it, not just the first."""

    def test_every_query_in_the_walk_carries_the_filter_expression(self):
        """FIX-022: a status filter narrows every query the walk issues."""
        pages = [_page(_rows("h", PAGE_SIZE), last_key={"p": 1}),
                 _page(_rows("v", PAGE_SIZE), last_key={"p": 2})]
        message, calls = _run_global_list(
            pages, _visibility_by_prefix, _hidden_asset_enforcer(),
            query={"pageSize": str(PAGE_SIZE), "status": "SUCCEEDED"})
        assert len(calls) >= 2, f"the walk issued {len(calls)} queries"
        assert all("FilterExpression" in kwargs for kwargs in calls), (
            "a continuation query dropped the server-side filter, so it re-reads rows the filter "
            "was meant to remove")
        assert len(message["Items"]) == PAGE_SIZE

    def test_the_python_filter_still_runs_as_the_safety_net(self):
        """FIX-022 control: `_global_list_matches_filters` is not replaced by the FilterExpression.

        The stub returns rows the server-side filter would have removed; the Python check must still
        drop them, or a filtered list silently returns rows that do not match. Passes today and must
        keep passing after the fix."""
        pages = [_page(_rows("v", PAGE_SIZE, status="FAILED"), last_key={"p": 1})]
        message, _calls = _run_global_list(
            pages, _visibility_by_prefix, _allow_all(),
            query={"pageSize": str(PAGE_SIZE), "status": "SUCCEEDED"})
        assert message["Items"] == [], "a row whose status does not match the filter was listed"


@pytest.mark.unit
class TestTheEntityBoundSurvivesTheWalk:
    """`result['warnings']` already carries the distinct-entity bound message. A second reason must be
    APPENDED — overwriting it hides the bound, and the MCP tool's 'do not report a count when warnings
    are present' guidance silently stops firing."""

    def _bounded_page(self):
        per_row = 10
        row_count = (le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE // per_row) + 5
        rows = _rows("v", row_count)
        return rows, per_row

    def test_the_entity_bound_warning_and_its_continuation_are_preserved(self):
        """FIX-022 control: the existing bound message and synthesized NextToken still appear.

        Passes today; it is here so a fix that rewrites `result['warnings']` for the new empty-page
        reason fails instead of silently dropping the bound. Without a NextToken the rows the bound
        withheld are unreachable rather than deferred."""
        rows, per_row = self._bounded_page()
        message, _calls = _run_global_list(
            [_page(rows)],  # exhausted index: the token must be synthesized
            lambda eid: [("db", f"{eid}-asset-{i}") for i in range(per_row)],
            _allow_all(), query={"pageSize": "100"})
        warnings = message.get("warnings") or []
        assert any(str(le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE) in w for w in warnings), (
            f"the distinct-entity bound message was lost: {warnings}")
        assert "NextToken" in message
        key = json.loads(base64.b64decode(message["NextToken"]))
        assert set(key) == {"allListPartition", "executionStartDate",
                            "workflowExecutionId", "workflowDatabaseId:workflowId"}

    def test_the_entity_budget_is_disarmed_after_the_walk(self):
        """FIX-022 control: the budget is armed once, before the walk, and released after it.

        Re-arming it per inner query would make it effectively unbounded; leaving it armed would bound
        a single-execution authorization later in the same invocation. Passes today and must keep
        passing after the fix."""
        pages = [_page(_rows("h", PAGE_SIZE), last_key={"p": 1}),
                 _page(_rows("v", PAGE_SIZE))]
        _run_global_list(pages, _visibility_by_prefix, _hidden_asset_enforcer())
        assert le._authz_entity_budget_exceeded() is False
        assert le._authz_entities_within_budget(
            [("db", f"a{i}") for i in range(le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE * 2)]) is True


@pytest.mark.unit
class TestTheWalkIsBoundedByBothMeters:
    """Two meters, both necessary. The wall clock is authoritative (it is the only bound that addresses
    the 29 s API Gateway cliff and the only one robust to per-row cost variance and throttling), and the
    query count is required by the contract: because `seen` skips duplicates before any work is done, a
    re-served page makes zero progress per iteration and a clock-only design would spin thousands of
    times before the clock could stop it."""

    def test_the_query_cap_is_the_module_constant_not_a_ceiling(self):
        """Asserted against the constant IN-BAND, so raising the constant cannot silently un-bound the
        walk while a `<= QUERY_CALL_CEILING` assertion still passes."""
        pages = [_page(_rows("h", PAGE_SIZE), last_key={"p": 1})]
        message, calls = _run_global_list(
            pages, _visibility_by_prefix, _hidden_asset_enforcer(), repeat_last=True)
        assert len(calls) == le.MAX_GLOBAL_LIST_QUERIES_PER_REQUEST, (
            f"the walk issued {len(calls)} queries against a cap of "
            f"{le.MAX_GLOBAL_LIST_QUERIES_PER_REQUEST}")
        # The page it returns is short, says why, and stays continuable.
        assert message["Items"] == []
        assert "NextToken" in message
        assert len(message["warnings"]) == 1
        assert "work budget" in message["warnings"][0]

    def test_the_work_budget_warning_names_no_entity_count(self):
        """Without this, the pre-existing distinct-asset assertion (`str(500) in warnings[0]` in
        test_executions_authz_bound.py) can be satisfied by the WRONG message."""
        pages = [_page(_rows("h", PAGE_SIZE), last_key={"p": 1})]
        message, _calls = _run_global_list(
            pages, _visibility_by_prefix, _hidden_asset_enforcer(), repeat_last=True)
        warning = message["warnings"][0]
        assert str(le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE) not in warning, warning
        assert "distinct assets" not in warning, warning

    def test_the_two_stop_reasons_produce_distinct_messages(self):
        """The entity bound terminates the walk, so the two reasons cannot co-occur — but `warnings` is
        still built by APPEND, and each branch must produce its own text. Assigning a single-element list
        for the new reason is what would silently delete the bound message."""
        rows = _rows("v", (le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE // 10) + 5)
        bound_message, _c1 = _run_global_list(
            [_page(rows)], lambda eid: [("db", f"{eid}-a{i}") for i in range(10)],
            _allow_all(), query={"pageSize": "100"})
        budget_message, _c2 = _run_global_list(
            [_page(_rows("h", PAGE_SIZE), last_key={"p": 1})],
            _visibility_by_prefix, _hidden_asset_enforcer(), repeat_last=True)
        assert len(bound_message["warnings"]) == 1
        assert len(budget_message["warnings"]) == 1
        assert str(le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE) in bound_message["warnings"][0]
        assert bound_message["warnings"][0] != budget_message["warnings"][0]

    def test_the_wall_clock_stops_the_walk_mid_page_and_resumes_at_the_first_unread_row(self):
        """The clock is checked before EACH ROW, so the overshoot past the budget is one row rather than
        one whole query — and the resume key is the last row FULLY evaluated, never the query's
        LastEvaluatedKey, which points past the rows this page never returned."""
        pages = [_page(_rows("v", PAGE_SIZE), last_key={"p": 1})]
        # call 1 = the deadline; calls 2-3 = rows v0/v1; call 4 = row v2 -> expired.
        message, calls = _run_global_list(
            pages, _visibility_by_prefix, _hidden_asset_enforcer(), clock=_clock(3))
        assert len(calls) == 1
        assert [i["workflowExecutionId"] for i in message["Items"]] == ["v0", "v1"]
        assert "work budget" in message["warnings"][0]
        key = json.loads(base64.b64decode(message["NextToken"]))
        assert key["workflowExecutionId"] == "v1", (
            f"a mid-page stop must resume AT the first unevaluated row, not past the page: {key}")

    def test_the_wall_clock_at_a_query_boundary_uses_the_server_continuation(self):
        """A query consumed to its end leaves no unread rows, so the ordinary server key is the correct
        (and lossless) resume point."""
        pages = [_page(_rows("h", PAGE_SIZE), last_key={"p": 1})]
        # call 1 = deadline; calls 2-6 = the five rows; call 7 = the query-boundary check -> expired.
        message, calls = _run_global_list(
            pages, _visibility_by_prefix, _hidden_asset_enforcer(), clock=_clock(6),
            repeat_last=True)
        assert len(calls) == 1, "the clock must stop the walk before a second query is issued"
        assert message["Items"] == []
        assert "work budget" in message["warnings"][0]
        assert json.loads(base64.b64decode(message["NextToken"])) == {"p": 1}

    def test_a_full_page_is_never_charged_a_deadline_stop(self):
        """Control: the clock must not fire on a page the first query already filled, or every ordinary
        list response would carry a work-budget warning."""
        pages = [_page(_rows("v", PAGE_SIZE), last_key={"p": 1})]
        message, calls = _run_global_list(pages, _visibility_by_prefix, _hidden_asset_enforcer())
        assert len(calls) == 1
        assert len(message["Items"]) == PAGE_SIZE
        assert "warnings" not in message

    def test_an_unauthenticated_walk_stops_after_one_query(self):
        """`page_enforcer` is None only when there is no authenticated identity, in which case every row
        fails at "no tokens" and no amount of paging can ever fill the page. Unreachable through the API
        (the Tier-1 check denies first) but the walk must not burn its whole query cap on it."""
        table = MagicMock()
        calls = []

        def _query(**kwargs):
            calls.append(kwargs)
            if len(calls) > RUNAWAY_QUERY_CALLS:
                raise AssertionError("the unauthenticated walk never stopped")
            return _page(_rows("v", PAGE_SIZE), last_key={"p": 1})

        table.query.side_effect = _query
        le.claims_and_roles = {"tokens": []}
        try:
            with patch(f"{MOD}.dynamodb") as ddb, \
                 patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
                 patch(f"{MOD}.get_execution_input_assets", side_effect=_visibility_by_prefix), \
                 patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}):
                ddb.Table.return_value = table
                response = le.get_global_executions({}, {"pageSize": str(PAGE_SIZE)})
        finally:
            le.claims_and_roles = {"tokens": ["u1"]}
        message = json.loads(response["body"])["message"]
        assert message["Items"] == []
        assert len(calls) == 1, f"an unauthenticated walk issued {len(calls)} queries"


@pytest.mark.unit
class TestTheEntityBoundTerminatesTheWalk:
    """Once `_authz_entity_budget['exceeded']` is set it is never reset, so every later row needing a new
    asset is withheld too: continuing would pay full per-row cost for rows that cannot pass. Stopping also
    absorbs a pre-existing defect — the withheld rows become the resume point instead of being paged
    over."""

    PER_ROW = 10

    def _bounded_rows(self):
        return _rows("v", (le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE // self.PER_ROW) + 5)

    def _assets_for(self, execution_id):
        return [("db", f"{execution_id}-asset-{i}") for i in range(self.PER_ROW)]

    def test_the_bound_stops_the_walk_even_with_a_continuation_available(self):
        rows = self._bounded_rows()
        message, calls = _run_global_list(
            [_page(rows, last_key={"p": 1})], self._assets_for, _allow_all(),
            query={"pageSize": "100"})
        assert len(calls) == 1, (
            f"the walk kept querying after the breadth bound was spent ({len(calls)} queries); every "
            f"further row needing a new asset can only be withheld")
        assert message.get("warnings")
        assert str(le.MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE) in message["warnings"][0]

    def test_the_withheld_row_is_the_resume_point_not_a_skipped_row(self):
        """The pre-existing defect this absorbs: `next_key` preferred the server LastEvaluatedKey, which
        points PAST the withheld rows, so they were skipped rather than deferred."""
        rows = self._bounded_rows()
        message, _calls = _run_global_list(
            [_page(rows, last_key={"p": 1})], self._assets_for, _allow_all(),
            query={"pageSize": "100"})
        key = json.loads(base64.b64decode(message["NextToken"]))
        assert key != {"p": 1}, (
            "the continuation used the server key, so the rows the bound withheld are skipped forever")
        assert set(key) == {"allListPartition", "executionStartDate",
                            "workflowExecutionId", "workflowDatabaseId:workflowId"}
        listed = {i["workflowExecutionId"] for i in message["Items"]}
        # The resume row is the LAST row listed, so the first row this page did not evaluate is the very
        # next one the continuation reads — nothing between the page and the token is lost.
        ids = [r["workflowExecutionId"] for r in rows]
        resume_index = ids.index(key["workflowExecutionId"])
        assert ids[resume_index] in listed
        assert ids[resume_index + 1] not in listed


@pytest.mark.unit
class TestThePerPageCachesAreArmedExactlyOnce:
    """Re-arming the entity budget inside the walk makes authorization BREADTH effectively unbounded, and
    re-clearing the memos multiplies both the DynamoDB reads and the Casbin evaluations. Both look
    harmless in a diff, so they are pinned by call count."""

    class _CountingDict(dict):
        clears = 0

        def clear(self):
            type(self).clears += 1
            super().clear()

    class _CountingWeakDict(WeakKeyDictionary):
        clears = 0

        def clear(self):
            type(self).clears += 1
            super().clear()

    def test_each_is_cleared_or_armed_once_across_a_multi_query_walk(self):
        assets = type("CountingAssets", (self._CountingDict,), {"clears": 0})()
        decisions = type("CountingDecisions", (self._CountingWeakDict,), {"clears": 0})()
        arm = MagicMock(wraps=le._arm_authz_entity_budget)
        # Two fully hidden pages ('h*' rows read a hidden asset), then a visible one, so the walk really
        # spans three queries.
        pages = [_page(_rows("h", PAGE_SIZE), last_key={"p": 1}),
                 _page(_rows("h2", PAGE_SIZE), last_key={"p": 2}),
                 _page(_rows("v", PAGE_SIZE), last_key={"p": 3})]
        message, calls = _run_global_list(
            pages, _visibility_by_prefix, _hidden_asset_enforcer(),
            extra_patches=(patch(f"{MOD}._asset_details_cache", assets),
                           patch(f"{MOD}._authz_decision_cache", decisions),
                           patch(f"{MOD}._arm_authz_entity_budget", arm)))
        assert len(calls) == 3, "the scenario must actually span several queries"
        assert len(message["Items"]) == PAGE_SIZE
        assert type(assets).clears == 1, (
            f"_asset_details_cache was cleared {type(assets).clears} times; clearing inside the walk "
            f"re-reads every asset AND resets the entity-budget denominator")
        assert type(decisions).clears == 1, (
            f"_authz_decision_cache was cleared {type(decisions).clears} times")
        assert arm.call_count == 1, (
            f"_arm_authz_entity_budget ran {arm.call_count} times; re-arming per query makes the "
            f"breadth bound unreachable")

    def test_the_asset_memo_is_shared_across_the_walks_queries(self):
        """The positive control for the clear-once assertion: an asset resolved for a row in query 1 must
        answer from the memo for a row in query 2, so the enforcer sees ONE decision for it."""
        pages = [_page(_rows("h", PAGE_SIZE), last_key={"p": 1}),
                 _page(_rows("v", PAGE_SIZE), last_key={"p": 2})]
        enforcer = _hidden_asset_enforcer()
        message, calls = _run_global_list(pages, _visibility_by_prefix, enforcer)
        assert len(calls) == 2
        hidden_calls = [c for c in enforcer.enforce.call_args_list
                        if c.args[0].get("assetId") == "hidden-asset"]
        visible_calls = [c for c in enforcer.enforce.call_args_list
                         if c.args[0].get("assetId") == "visible-asset"]
        assert len(hidden_calls) == 1, f"the hidden asset was re-evaluated: {len(hidden_calls)}"
        assert len(visible_calls) == 1
        assert len(message["Items"]) == PAGE_SIZE


@pytest.mark.unit
class TestTheQueryIsBuiltOnceAndOnlyTheCursorMoves:
    """`query_kwargs` is assembled before the walk, so every query carries the same index, key condition,
    ScanIndexForward, Limit and FilterExpression. Rebuilding it per iteration is how a continuation query
    silently loses the server-side filter."""

    def _two_query_walk(self):
        pages = [_page(_rows("h", PAGE_SIZE), last_key={"p": 1}),
                 _page(_rows("v", PAGE_SIZE), last_key={"p": 2})]
        return _run_global_list(
            pages, _visibility_by_prefix, _hidden_asset_enforcer(),
            query={"pageSize": str(PAGE_SIZE), "status": "SUCCEEDED"})

    def test_the_filter_expression_object_is_identical_across_queries(self):
        _message, calls = self._two_query_walk()
        assert len(calls) == 2
        assert calls[0]["FilterExpression"] is calls[1]["FilterExpression"], (
            "the FilterExpression was rebuilt per query, so query_kwargs is not assembled once")

    def test_only_the_exclusive_start_key_differs_between_queries(self):
        _message, calls = self._two_query_walk()
        assert "ExclusiveStartKey" not in calls[0]
        assert calls[1]["ExclusiveStartKey"] == {"p": 1}
        first = {k: v for k, v in calls[0].items() if k != "ExclusiveStartKey"}
        second = {k: v for k, v in calls[1].items() if k != "ExclusiveStartKey"}
        assert set(first) == set(second)
        for key in first:
            assert first[key] is second[key], f"{key} was rebuilt between queries"
        assert calls[1]["Limit"] == PAGE_SIZE

    def test_a_continuation_query_keeps_the_full_page_size_limit(self):
        """The distinguishing case for the Limit: query 1 collects SOME rows, so a `page_size - len(items)`
        Limit would shrink. Shrinking makes the walk crawl — a `Limit=1` query with a narrow
        FilterExpression reads one row and usually returns none, which is precisely the regime this fix
        exists for."""
        pages = [_page(_rows("v", 2) + _rows("h", 3), last_key={"p": 1}),
                 _page(_rows("w", PAGE_SIZE), last_key={"p": 2})]
        message, calls = _run_global_list(
            pages, _visibility_by_prefix, _hidden_asset_enforcer())
        assert len(calls) == 2, "query 1 must be partially filled so a shrinking Limit would show"
        assert calls[1]["Limit"] == PAGE_SIZE, (
            f"the continuation query's Limit shrank to {calls[1]['Limit']}; it must stay page_size")
        assert len(message["Items"]) >= PAGE_SIZE


@pytest.mark.unit
class TestTheWalkCarriesFix009sTightenedPredicate:
    """FIX-009 and FIX-022 are one change: tightening the per-row predicate raises the proportion of rows
    dropped AFTER the DynamoDB page is read, which is exactly the symptom the walk exists to absorb. This
    pins the interaction end-to-end through the endpoint — a row hidden only because the caller cannot
    read the asset the run WROTE to must leave the list, and the walk must then keep going to fill the
    page."""

    # Rows named 'h*' read a readable asset and WROTE into one the caller cannot read.
    REDIRECTED = {"outputLocationType": "asset", "outputDatabaseId": "db",
                  "outputAssetId": "hidden-output"}

    def _config_for(self, execution_id):
        return dict(self.REDIRECTED) if execution_id.startswith("h") else {}

    def test_a_row_hidden_only_by_its_output_asset_leaves_the_list_and_the_walk_refills(self):
        pages = [_page(_rows("h", PAGE_SIZE), last_key={"p": 1}),
                 _page(_rows("v", PAGE_SIZE), last_key={"p": 2})]
        message, calls = _run_global_list(
            pages, lambda eid: [("db", "visible-asset")], _hidden_asset_enforcer(),
            config_for=self._config_for)
        listed = [i["workflowExecutionId"] for i in message["Items"]]
        assert all(not i.startswith("h") for i in listed), (
            f"a run that wrote into an unreadable asset was listed: {listed}")
        assert len(listed) == PAGE_SIZE, "the walk did not refill the page the output gate emptied"
        assert len(calls) == 2

    def test_the_same_rows_are_listed_when_the_output_asset_is_readable(self):
        """The control that makes the exclusion above attributable to the output asset: the identical
        page, with a readable output target, comes back on ONE query."""
        pages = [_page(_rows("h", PAGE_SIZE), last_key={"p": 1}),
                 _page(_rows("v", PAGE_SIZE), last_key={"p": 2})]
        message, calls = _run_global_list(
            pages, lambda eid: [("db", "visible-asset")], _hidden_asset_enforcer(),
            config_for=lambda eid: {"outputLocationType": "asset", "outputDatabaseId": "db",
                                    "outputAssetId": "visible-output"})
        assert [i["workflowExecutionId"] for i in message["Items"]] == [
            f"h{i}" for i in range(PAGE_SIZE)]
        assert len(calls) == 1

    def test_a_hidden_row_still_costs_at_most_one_configuration_read(self):
        """The output gate needs the configuration row, so a row it hides has paid for one read — but
        never two."""
        pages = [_page(_rows("h", PAGE_SIZE), last_key={"p": 1}),
                 _page(_rows("v", PAGE_SIZE), last_key={"p": 2})]
        reads = []

        def _config_for(execution_id):
            reads.append(execution_id)
            return self._config_for(execution_id)

        message, _calls = _run_global_list(
            pages, lambda eid: [("db", "visible-asset")], _hidden_asset_enforcer(),
            config_for=_config_for)
        assert len(reads) == len(set(reads)), f"a row paid for two configuration reads: {reads}"
        assert set(reads) == ({f"h{i}" for i in range(PAGE_SIZE)}
                              | {f"v{i}" for i in range(PAGE_SIZE)})
        assert len(message["Items"]) == PAGE_SIZE
