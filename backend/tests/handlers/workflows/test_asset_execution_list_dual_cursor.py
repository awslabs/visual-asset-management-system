# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset-scoped executions list's two-direction continuation.

An asset's history is the union of two independently-keyed queries — the inputs GSI (runs that read
the asset) and the output-asset GSI (runs that wrote to it) — sharing one MAX_EXECUTIONS_INSPECTED
budget. The invariant: a page that spends the budget always says where BOTH directions stand, so a
cap reached through either one is resumable and an output-only run (inputFileArity 'none', whose only
asset association is the output GSI) stays reachable.

executionService resolves its table names at import (mirrors test_executionService_wb53.py)."""

import base64
import json
import os

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

from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"

DB, ASSET = "db", "A"


def _allow_all():
    e = MagicMock()
    e.enforce.return_value = True
    e.enforceAPI.return_value = True
    return e


def _input_rows(count, start=0):
    return [{
        "workflowExecutionId": f"i{i:032d}",
        "databaseId:assetId": f"{DB}:{ASSET}",
        "databaseId:assetId:inputAssetFileKey": f"{DB}:{ASSET}:/f{i}.glb",
        "databaseId": DB, "assetId": ASSET, "inputAssetFileKey": f"/f{i}.glb",
        "workflowId": "wf", "workflowDatabaseId": "wf-db",
        "executionStartDate": f"2026-01-01T00:00:{i % 60:02d}Z",
    } for i in range(start, start + count)]


def _cfg_rows(count, start=0):
    return [{
        "workflowExecutionId": f"o{i:032d}",
        "outputDatabaseId:outputAssetId": f"{DB}:{ASSET}",
        "recordType": "configuration",
        "executionStartDate": f"2026-02-01T00:00:{i % 60:02d}Z",
    } for i in range(start, start + count)]


def _run(input_pages, cfg_pages, query_params=None):
    """Run the asset listing over canned query pages; returns (message dict, inputs_table, cfg_table).

    Each page is a full DynamoDB query response, so a page carrying 'LastEvaluatedKey' drives the
    walk on and one without it reports that query exhausted."""
    le.claims_and_roles = {"tokens": ["u1"]}
    inputs_table, cfg_table, main_table = MagicMock(), MagicMock(), MagicMock()
    inputs_table.query.side_effect = list(input_pages)
    cfg_table.query.side_effect = list(cfg_pages)
    main_table.query.return_value = {"Items": []}

    def _table(name):
        return {le.workflow_execution_inputs_table: inputs_table,
                le.workflow_execution_database_v2: main_table,
                le.workflow_execution_configuration_table: cfg_table}.get(name, MagicMock())

    # The shared logger mock takes no %-style args, which the cap warning uses.
    with patch(f"{MOD}.dynamodb") as ddb, \
         patch.object(le.logger, "warning", MagicMock()), \
         patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
         patch(f"{MOD}.get_asset_details",
               side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
         patch(f"{MOD}._execution_access_check", return_value=(True, "")), \
         patch(f"{MOD}.build_execution_items", return_value=[]), \
         patch(f"{MOD}.sfn"):
        ddb.Table.side_effect = _table
        resp = le.get_executions({}, DB, ASSET, "", "", query_params or {})
    assert resp["statusCode"] == 200
    return json.loads(resp["body"])["message"], inputs_table, cfg_table


def _decode(token):
    return json.loads(base64.b64decode(token).decode("utf-8"))


@pytest.mark.unit
class TestOutputDirectionCapIsContinuable:
    """The budget can be spent entirely on the output direction — a results-only asset has no input
    rows at all — so the output query has to contribute its own resume point."""

    def test_a_cap_reached_through_the_output_query_emits_a_token(self):
        # The cursor names the last row COLLECTED, not the end of the query page. The cap stops
        # part-way through a 210-row page, so the server's LastEvaluatedKey points past 10 rows this
        # page never returned — resuming there would skip them silently.
        cap = le.MAX_EXECUTIONS_INSPECTED
        rows = _cfg_rows(cap + 10)
        message, _inputs, _cfg = _run(
            [{"Items": _input_rows(3)}],
            [{"Items": rows, "LastEvaluatedKey": {"k": "server"}}])
        assert "NextToken" in message
        token = _decode(message["NextToken"])
        assert token["inputsDone"] is True
        assert token["outputKey"] != {"k": "server"}, "the page-end key would skip unread rows"
        assert token["outputKey"]["workflowExecutionId"] == rows[cap - 4]["workflowExecutionId"]
        assert message["warnings"]

    def test_an_exhausted_output_page_falls_back_to_the_last_row_key(self):
        # DynamoDB reports no LastEvaluatedKey on a page it considers complete, so a cap reached
        # mid-page needs a cursor synthesized from the row the walk stopped on.
        cap = le.MAX_EXECUTIONS_INSPECTED
        rows = _cfg_rows(cap + 10)
        message, _inputs, _cfg = _run([{"Items": _input_rows(3)}], [{"Items": rows}])
        token = _decode(message["NextToken"])
        assert token["inputsDone"] is True
        assert token["outputKey"] == {
            "outputDatabaseId:outputAssetId": f"{DB}:{ASSET}",
            "executionStartDate": rows[cap - 4]["executionStartDate"],
            "workflowExecutionId": rows[cap - 4]["workflowExecutionId"],
            "recordType": "configuration",
        }

    def test_the_token_resumes_the_output_query_without_rereading_inputs(self):
        cap = le.MAX_EXECUTIONS_INSPECTED
        first, _inputs, _cfg = _run(
            [{"Items": _input_rows(3)}],
            [{"Items": _cfg_rows(cap + 10), "LastEvaluatedKey": {"k": "server"}}])
        expected = _decode(first["NextToken"])["outputKey"]
        message, inputs_table, cfg_table = _run(
            [{"Items": []}], [{"Items": _cfg_rows(5, start=cap)}],
            query_params={"startingToken": first["NextToken"]})
        inputs_table.query.assert_not_called()
        # Resumes at the point the first page stopped serving, not at that query page's end.
        assert cfg_table.query.call_args.kwargs["ExclusiveStartKey"] == expected
        assert "NextToken" not in message
        assert "warnings" not in message


@pytest.mark.unit
class TestInputDirectionCapKeepsItsOwnCursor:
    """The input side's cursor stays under its own key, so the second page resumes the inputs GSI
    rather than the output one."""

    def test_a_cap_reached_through_the_input_query_emits_an_inputs_cursor(self):
        # As with the output direction, the cursor is the last row COLLECTED. The server's
        # LastEvaluatedKey is the end of the whole query page and would step over the rows the cap
        # left unread.
        cap = le.MAX_EXECUTIONS_INSPECTED
        rows = _input_rows(cap + 10)
        message, _inputs, cfg_table = _run(
            [{"Items": rows, "LastEvaluatedKey": {"k": "in"}}], [])
        token = _decode(message["NextToken"])
        assert token["inputsKey"] != {"k": "in"}, "the page-end key would skip unread rows"
        assert token["inputsKey"]["workflowExecutionId"] == rows[cap - 1]["workflowExecutionId"]
        # The budget was spent before the output direction was reached, so it was not queried.
        cfg_table.query.assert_not_called()

    def test_an_exhausted_input_page_falls_back_to_the_last_row_key(self):
        cap = le.MAX_EXECUTIONS_INSPECTED
        rows = _input_rows(cap + 10)
        message, _inputs, _cfg = _run([{"Items": rows}], [])
        assert _decode(message["NextToken"])["inputsKey"] == {
            "databaseId:assetId": f"{DB}:{ASSET}",
            "executionStartDate": rows[cap - 1]["executionStartDate"],
            "workflowExecutionId": rows[cap - 1]["workflowExecutionId"],
            "databaseId:assetId:inputAssetFileKey": rows[cap - 1]["databaseId:assetId:inputAssetFileKey"],
        }

    def test_that_cursor_resumes_the_inputs_query(self):
        cap = le.MAX_EXECUTIONS_INSPECTED
        rows = _input_rows(cap + 10)
        first, _i, _c = _run([{"Items": rows, "LastEvaluatedKey": {"k": "in"}}], [])
        expected = _decode(first["NextToken"])["inputsKey"]
        _message, inputs_table, _cfg = _run(
            [{"Items": _input_rows(4, start=cap)}], [{"Items": []}],
            query_params={"startingToken": first["NextToken"]})
        # The second request resumes at exactly the point the first one stopped serving.
        assert inputs_table.query.call_args.kwargs["ExclusiveStartKey"] == expected

    def test_a_legacy_single_cursor_token_still_resumes_the_inputs_query(self):
        token = base64.b64encode(json.dumps({"k": "in"}).encode("utf-8")).decode("utf-8")
        _message, inputs_table, _cfg = _run(
            [{"Items": _input_rows(2)}], [{"Items": []}],
            query_params={"startingToken": token})
        assert inputs_table.query.call_args.kwargs["ExclusiveStartKey"] == {"k": "in"}


@pytest.mark.unit
class TestUncappedPageSaysNothingWasWithheld:
    """A page that did not spend the budget must not claim one."""

    def test_a_complete_listing_carries_no_token_and_no_warning(self):
        message, _inputs, _cfg = _run(
            [{"Items": _input_rows(3)}], [{"Items": _cfg_rows(2)}])
        assert "NextToken" not in message
        assert "warnings" not in message


@pytest.mark.unit
class TestPageSizeBoundsTheWalk:
    """`pageSize` bounds the PAGE, not just the work budget.

    The walk's own cap (MAX_EXECUTIONS_INSPECTED) is a scan budget. Before this was wired, a caller
    asking for 28 rows was served every row the walk found — live, 57 of 57 — contradicting the
    pageSize the CLI (`--page-size`) and the API reference both document.

    Capping inside the walk rather than slicing the finished list is what keeps NextToken correct: the
    resume key is recorded against the last row actually collected, so a smaller page DEFERS the
    remainder instead of skipping it.
    """

    def test_the_collected_candidate_count_is_bounded_by_page_size(self):
        # build_execution_items is mocked out, so the observable effect is the walk stopping early and
        # emitting a resume cursor rather than reporting the inputs exhausted.
        page = 5
        message, _inputs, _cfg = _run(
            [{"Items": _input_rows(60), "LastEvaluatedKey": {"k": "server"}}],
            [{"Items": []}],
            query_params={"pageSize": str(page)})
        assert "NextToken" in message, "a page bounded by pageSize must be continuable"
        token = _decode(message["NextToken"])
        assert "inputsKey" in token, f"the inputs cursor must carry the resume point: {token}"

    def test_a_page_size_bounded_page_carries_no_work_budget_warning(self):
        # The warning names the MAX_EXECUTIONS_INSPECTED work budget. A page the CALLER bounded is an
        # ordinary page, so warning about a limit would misreport a normal paged read.
        message, _inputs, _cfg = _run(
            [{"Items": _input_rows(60), "LastEvaluatedKey": {"k": "server"}}],
            [{"Items": []}],
            query_params={"pageSize": "5"})
        assert not message.get("warnings"), message.get("warnings")

    def test_the_work_budget_still_warns_when_it_is_the_binding_cap(self):
        # Positive control: the warning must still fire when the WALK's cap is what bounded the page,
        # otherwise the check above would pass by having disabled warnings entirely.
        cap = le.MAX_EXECUTIONS_INSPECTED
        message, _inputs, _cfg = _run(
            [{"Items": _input_rows(cap + 10), "LastEvaluatedKey": {"k": "server"}}],
            [{"Items": []}])
        assert message.get("warnings"), "the work-budget cap must still be reported"

    def test_a_page_size_above_the_work_budget_cannot_raise_it(self):
        # pageSize narrows the walk; it never widens it past the work budget.
        cap = le.MAX_EXECUTIONS_INSPECTED
        message, _inputs, _cfg = _run(
            [{"Items": _input_rows(cap + 50), "LastEvaluatedKey": {"k": "server"}}],
            [{"Items": []}],
            query_params={"pageSize": str(cap + 500)})
        assert message.get("warnings"), "the work budget must still bound the page"

    @pytest.mark.parametrize("bad", ["", "0", "-4", "abc", None])
    def test_an_unusable_page_size_falls_back_to_the_work_budget(self, bad):
        # A malformed value must not collapse the page to zero rows; validate_pagination_info handles
        # rejection at the handler boundary, so here the walk simply keeps its own budget.
        cap = le.MAX_EXECUTIONS_INSPECTED
        message, _inputs, _cfg = _run(
            [{"Items": _input_rows(cap + 10), "LastEvaluatedKey": {"k": "server"}}],
            [{"Items": []}],
            query_params={"pageSize": bad})
        assert message.get("warnings"), f"pageSize={bad!r} must fall back to the work budget"


@pytest.mark.unit
class TestACappedPageNeitherSkipsNorRepeats:
    """The resume cursor must be the last row SERVED, not the end of the query page.

    A DynamoDB query reads a whole page while the cap can stop part-way through it. Preferring the
    server's LastEvaluatedKey therefore pointed past rows the page never returned: resuming there
    skipped them. Live, the reverse symptom appeared once pageSize made the cap bind early — 29 ids
    repeated across two pages — because the cursor and the served rows had drifted apart.
    """

    def _walk(self, total, page_size):
        """Page an asset's inputs to exhaustion; returns the ids served, in order."""
        rows = _input_rows(total)
        served, token, guard = [], None, 0
        while True:
            guard += 1
            assert guard < 50, "paging did not terminate"
            # One server page holds everything, so only the cap/pageSize can bound a page — which is
            # exactly the case where the two cursor candidates disagree.
            start = len(served)
            params = {"pageSize": str(page_size)}
            if token:
                params["startingToken"] = token
            message, _i, _c = _run([{"Items": rows[start:], "LastEvaluatedKey": {"k": "srv"}}],
                                   [{"Items": []}], query_params=params)
            token = message.get("NextToken")
            if not token:
                break
            resumed = _decode(token).get("inputsKey") or {}
            served.append(resumed.get("workflowExecutionId"))
        return served

    def test_each_page_resumes_at_the_row_after_the_last_one_served(self):
        cap, page = le.MAX_EXECUTIONS_INSPECTED, 5
        rows = _input_rows(cap + 10)
        message, _i, _c = _run([{"Items": rows, "LastEvaluatedKey": {"k": "srv"}}],
                               [{"Items": []}], query_params={"pageSize": str(page)})
        token = _decode(message["NextToken"])
        # With pageSize 5 the walk stops after 5 rows, so the cursor must name row 5 — not the end of
        # the 210-row page, which would step over rows 6..210.
        assert token["inputsKey"]["workflowExecutionId"] == rows[page - 1]["workflowExecutionId"], (
            "the cursor must name the last row served")

    def test_the_server_page_end_key_is_not_used_when_a_row_was_served(self):
        # The negative control: the old precedence emitted exactly this, and it is what skipped rows.
        message, _i, _c = _run(
            [{"Items": _input_rows(40), "LastEvaluatedKey": {"k": "srv"}}],
            [{"Items": []}], query_params={"pageSize": "3"})
        assert _decode(message["NextToken"])["inputsKey"] != {"k": "srv"}


@pytest.mark.unit
class TestTheOutputWalkAdvancesAcrossPages:
    """The output direction must not restart from its newest row on every page.

    The output query is deduped against `deduped_inputs`, which is per-request. A row served on an
    EARLIER page is invisible to it, so restarting the query re-serves every execution that is both an
    input and the output target for the asset.

    Live symptom on a 71-input-row asset at pageSize 44: page 1 capped on the inputs at index 43, page
    2 served inputs 44..70, drained them, then spent its remaining budget re-reading the output GSI
    from the newest row — repeating exactly the 17 executions that carry both roles. Reproduced against
    the GSI directly: page 1's cursor was correct (27 clean rows follow it), so the repeats came from
    the output side alone.
    """

    def test_a_page_that_read_output_rows_carries_an_output_cursor(self):
        # Inputs drain within the page, so the walk reaches the output query; the CAP then fires part
        # way through the output rows, leaving that direction unfinished. Before the fix the page was
        # continuable only through inputsDone, and the next page re-read the output GSI from newest.
        cap = le.MAX_EXECUTIONS_INSPECTED
        message, _i, cfg_table = _run(
            [{"Items": _input_rows(3)}],
            [{"Items": _cfg_rows(cap + 10), "LastEvaluatedKey": {"k": "out-more"}}],
            query_params={"pageSize": str(cap)})
        cfg_table.query.assert_called()
        token = _decode(message["NextToken"])
        assert "outputKey" in token, (
            f"an unfinished output walk must carry its cursor, else its rows repeat: {token}")

    def test_a_fully_walked_listing_still_carries_no_token(self):
        # The negative control: both directions exhausted is genuinely the end, and must not now
        # emit a token just because the output query was read.
        message, _i, _c = _run(
            [{"Items": _input_rows(3)}], [{"Items": _cfg_rows(2)}],
            query_params={"pageSize": "50"})
        assert "NextToken" not in message, message
        assert "warnings" not in message

    def test_an_output_continuation_page_carries_no_work_budget_warning(self):
        # The warning names the MAX_EXECUTIONS_INSPECTED budget; an ordinary output continuation has
        # not hit it, so claiming a limit would misreport a normal paged read.
        message, _i, _c = _run(
            [{"Items": _input_rows(3)}],
            [{"Items": _cfg_rows(4), "LastEvaluatedKey": {"k": "out-more"}},
             {"Items": _cfg_rows(2, start=4)}],
            query_params={"pageSize": "50"})
        # Both output pages are supplied here, so the walk finishes and the page is genuinely the end.
        # The claim under test is only that no work-budget warning is attached to it.
        assert not message.get("warnings"), message.get("warnings")



def _condition_values(condition):
    """Every literal value inside a boto3 DynamoDB condition tree.

    The objects have an opaque repr, so the operands are walked instead of string-matched.
    """
    values = []
    stack = [condition]
    while stack:
        node = stack.pop()
        operands = getattr(node, "_values", None)
        if operands is None:
            values.append(node)
            continue
        stack.extend(operands)
    return [v for v in values if isinstance(v, str)]


@pytest.mark.unit
class TestADualRoleExecutionIsNotServedTwiceAcrossPages:
    """An execution that is BOTH an input for the asset and its output target must appear once.

    The two GSI walks are deduped only within one request. Page 1 can cap on the inputs and serve a
    dual-role execution as an input row; page 2 then drains the inputs, reaches the output side for the
    first time, and — knowing nothing of page 1 — serves that same execution again. Live, an asset with
    71 input rows repeated exactly the 17 executions carrying both roles.

    The token therefore carries a high-water mark: the oldest executionStartDate already returned, plus
    the id served at that exact date. The output query is bounded above by it, and rows newer than it
    are dropped.
    """

    def test_the_token_carries_the_high_water_mark(self):
        message, _i, _c = _run(
            [{"Items": _input_rows(8), "LastEvaluatedKey": {"k": "in"}}],
            [{"Items": []}], query_params={"pageSize": "4"})
        token = _decode(message["NextToken"])
        assert token.get("servedThrough"), f"the mark must be recorded: {token}"
        assert token.get("servedThroughId"), f"the id at that date must be recorded: {token}"

    def test_the_output_query_is_bounded_by_the_mark(self):
        # The mark is the upper bound of the output range, so the output GSI is never re-read from the
        # newest row on a later page.
        rows = _input_rows(8)
        first, _i, _c = _run([{"Items": rows, "LastEvaluatedKey": {"k": "in"}}],
                             [{"Items": []}], query_params={"pageSize": "4"})
        mark = _decode(first["NextToken"])["servedThrough"]
        # Page 2 needs headroom under its cap after the remaining inputs, or the output query is never
        # reached and this would assert on a call that never happened.
        _m2, _i2, cfg_table = _run([{"Items": rows[4:]}], [{"Items": []}],
                                   query_params={"pageSize": "50",
                                                 "startingToken": first["NextToken"]})
        cfg_table.query.assert_called()
        values = _condition_values(cfg_table.query.call_args.kwargs["KeyConditionExpression"])
        assert mark in values, (
            f"the output query must be bounded by the high-water mark {mark}: {values}")

    def test_the_first_page_has_no_mark_so_the_output_range_is_unbounded_above(self):
        # The negative control: without a token there is nothing served yet, so the output walk must
        # not be narrowed at all.
        _m, _i, cfg_table = _run([{"Items": _input_rows(2)}], [{"Items": []}],
                                 query_params={"pageSize": "50"})
        cfg_table.query.assert_called()
        cond = cfg_table.query.call_args.kwargs["KeyConditionExpression"]
        # A first page uses gte(start) only; a narrowed one would carry a second date operand.
        dates = [v for v in _condition_values(cond) if v.endswith("Z")]
        assert len(dates) <= 1, f"an unbounded first page must not narrow the output range: {dates}"
