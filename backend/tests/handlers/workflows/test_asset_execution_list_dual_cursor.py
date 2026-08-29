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


def _run(input_pages, cfg_pages, query_params=None, probe_hits=None, echo_items=False):
    """Run the asset listing over canned query pages; returns (message dict, inputs_table, cfg_table).

    Each page is a full DynamoDB query response, so a page carrying 'LastEvaluatedKey' drives the
    walk on and one without it reports that query exhausted.

    `probe_hits`, when given, is the set of execution ids that have an input row for this asset. It
    switches the inputs table onto a callable that answers BOTH kinds of read the listing makes of it:
    a by-asset GSI page (served from `input_pages` in order) and the per-candidate
    "did the input direction already serve this execution?" probe, which the base table answers.

    `echo_items` makes build_execution_items report the candidates it was handed, so a test can assert
    WHICH executions a page served rather than only how the walk ended.
    """
    le.claims_and_roles = {"tokens": ["u1"]}
    inputs_table, cfg_table, main_table = MagicMock(), MagicMock(), MagicMock()
    if probe_hits is None:
        inputs_table.query.side_effect = list(input_pages)
    else:
        pages = list(input_pages)

        def _inputs_query(**kwargs):
            if kwargs.get("IndexName"):
                assert pages, "the by-asset walk asked for more pages than the test supplied"
                return pages.pop(0)
            # A probe: the execution id is the only condition value carrying no ':' (the other is the
            # 'db:asset:/' sort-key prefix).
            values = _condition_values(kwargs["KeyConditionExpression"])
            execution_id = next((v for v in values if ":" not in v), "")
            return {"Items": [{"workflowExecutionId": execution_id}]
                    if execution_id in probe_hits else []}

        inputs_table.query.side_effect = _inputs_query
    cfg_table.query.side_effect = list(cfg_pages)
    main_table.query.return_value = {"Items": []}

    def _table(name):
        return {le.workflow_execution_inputs_table: inputs_table,
                le.workflow_execution_database_v2: main_table,
                le.workflow_execution_configuration_table: cfg_table}.get(name, MagicMock())

    def _echo(**kwargs):
        return [{"workflowExecutionId": i.get("workflowExecutionId", "")}
                for i in kwargs["input_items"]]

    build_patch = (patch(f"{MOD}.build_execution_items", side_effect=_echo) if echo_items
                   else patch(f"{MOD}.build_execution_items", return_value=[]))
    # The shared logger mock takes no %-style args, which the cap warning uses.
    with patch(f"{MOD}.dynamodb") as ddb, \
         patch.object(le.logger, "warning", MagicMock()), \
         patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
         patch(f"{MOD}.get_asset_details",
               side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
         patch(f"{MOD}._execution_access_check", return_value=(True, "")), \
         build_patch, \
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
        # The inputs GSI is not WALKED again — that walk is what `inputsDone` reports finished. The
        # per-candidate "did the input direction already serve this execution?" read is a different
        # thing: it is keyed on one execution and one asset prefix against the BASE table, never the
        # by-asset index, so it cannot re-serve or re-page the input direction.
        for call in inputs_table.query.call_args_list:
            assert call.kwargs.get("IndexName") != "WorkflowExecInputsByAssetGSI", (
                f"the input direction must not be re-walked: {call.kwargs}")
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


@pytest.mark.unit
class TestAnExhaustedInputWalkStillRecordsWhatItServed:
    """S2-BACKEND-043: the high-water mark was recorded only when the input walk CAPPED.

    A page whose inputs drain and whose output walk then caps served the input direction in full and
    said nothing about it: the token carried `inputsDone` + `outputKey` and no `servedThrough`, so the
    next page's guard was inert and every dual-role execution below the output cursor was served a
    second time.

    The mark alone cannot fix it. Once the inputs are drained the mark is the OLDEST date served, and
    the rows below it are a MIX of already-served dual-role executions and never-served output-only
    ones — so bounding the output query there would trade the duplicate for a row no page ever
    returns. Such a page therefore asks the inputs table per candidate instead, and both halves are
    asserted below.
    """

    def _first_page(self, input_count=8):
        """A page whose input walk drains and whose output walk then caps."""
        cap = le.MAX_EXECUTIONS_INSPECTED
        inputs = _input_rows(input_count)
        message, _i, _c = _run(
            [{"Items": inputs}],
            [{"Items": _cfg_rows(cap + 10), "LastEvaluatedKey": {"k": "srv"}}])
        return message, inputs

    def test_the_token_records_the_mark_even_though_the_inputs_exhausted(self):
        message, inputs = self._first_page()
        token = _decode(message["NextToken"])
        assert token["inputsDone"] is True
        assert "outputKey" in token
        assert token.get("servedThrough") == inputs[-1]["executionStartDate"], (
            f"the oldest input row served must be recorded: {token}")
        assert token.get("servedThroughId") == inputs[-1]["workflowExecutionId"]

    def test_a_dual_role_execution_is_not_served_again_on_the_next_page(self):
        first, inputs = self._first_page()
        dual_role = inputs[0]["workflowExecutionId"]
        # Page 2 continues the output walk. The dual-role execution sits below the output cursor, so
        # only the "already served" test can keep it out.
        rows = _cfg_rows(2, start=le.MAX_EXECUTIONS_INSPECTED)
        rows.append({**rows[0], "workflowExecutionId": dual_role})
        message, _i, _c = _run([{"Items": []}], [{"Items": rows}],
                               query_params={"startingToken": first["NextToken"]},
                               probe_hits={dual_role}, echo_items=True)
        served = [i["workflowExecutionId"] for i in message["Items"]]
        assert dual_role not in served, f"page 1 already served it as an input row: {served}"

    def test_an_output_only_execution_at_or_above_the_mark_is_still_served(self):
        # The CENTRAL behaviour of the per-candidate probe, and the only case where the fixed and the
        # unfixed code disagree. Once the input side is drained the mark is the OLDEST date served, so
        # the rows AT and ABOVE it are a MIX: already-served dual-role executions and never-served
        # output-only ones. The probe is what tells them apart. A date comparison alone - which is what
        # runs when the probe branch is skipped - drops every output-only row in that range for good.
        #
        # Both halves are asserted from ONE page, with `probe_hits` naming only the dual-role id, so the
        # claim is that the PROBE decides and not that the page served nothing (which the withholding
        # half alone is equally satisfied by).
        first, inputs = self._first_page()
        mark = _decode(first["NextToken"])["servedThrough"]
        dual_role = inputs[0]["workflowExecutionId"]
        above = _cfg_rows(2, start=le.MAX_EXECUTIONS_INSPECTED)
        assert all(r["executionStartDate"] > mark for r in above), (
            f"fixture: these rows must sit above the mark {mark} or the test proves nothing")
        at_mark = {**above[0], "workflowExecutionId": "o" + "7" * 32, "executionStartDate": mark}
        rows = above + [at_mark, {**above[0], "workflowExecutionId": dual_role}]
        message, _i, _c = _run([{"Items": []}], [{"Items": rows}],
                               query_params={"startingToken": first["NextToken"]},
                               probe_hits={dual_role}, echo_items=True)
        served = [i["workflowExecutionId"] for i in message["Items"]]
        for row in above + [at_mark]:
            assert row["workflowExecutionId"] in served, (
                f"an output-only execution at or above the mark {mark} was withheld: {served}")
        assert dual_role not in served, (
            f"the dual-role execution page 1 served as an input row must stay out: {served}")

    def test_an_output_only_execution_below_the_mark_is_still_served(self):
        # The control that rules out the naive fix: an execution with no input row for this asset was
        # never served by the input direction, so it must appear even though its date is inside the
        # range the mark covers. Bounding the output query by the mark would drop it silently.
        first, inputs = self._first_page()
        rows = _cfg_rows(2, start=le.MAX_EXECUTIONS_INSPECTED)
        # Same date as an input row this page's mark covers, but a different execution.
        rows.append({**rows[0], "workflowExecutionId": "o" + "9" * 32,
                     "executionStartDate": inputs[0]["executionStartDate"]})
        message, _i, _c = _run([{"Items": []}], [{"Items": rows}],
                               query_params={"startingToken": first["NextToken"]},
                               probe_hits=set(), echo_items=True)
        served = [i["workflowExecutionId"] for i in message["Items"]]
        assert "o" + "9" * 32 in served, (
            f"an output-only execution must not be withheld by the mark: {served}")

    def test_the_output_query_is_not_bounded_once_the_inputs_are_drained(self):
        # The mechanism behind the test above, asserted where it lives: with the input side exhausted
        # the mark no longer means "everything newer was served", so it must not narrow the range.
        first, _inputs = self._first_page()
        _m, _i, cfg_table = _run([{"Items": []}],
                                 [{"Items": _cfg_rows(2, start=le.MAX_EXECUTIONS_INSPECTED)}],
                                 query_params={"startingToken": first["NextToken"]},
                                 probe_hits=set())
        cond = cfg_table.query.call_args.kwargs["KeyConditionExpression"]
        dates = [v for v in _condition_values(cond) if v.endswith("Z")]
        assert len(dates) <= 1, f"the output range must not be narrowed by the mark here: {dates}"

    def test_a_long_run_of_already_served_rows_stops_with_a_continuation(self):
        # The probes are reads, so a stretch of already-served rows must not walk the whole index one
        # read at a time. The page stops at its probe budget and hands back a cursor BELOW the rows it
        # read, so the next request makes progress rather than repeating the stretch.
        first, _inputs = self._first_page()
        probes = le.MAX_ASSET_LIST_INPUT_ROW_PROBES
        rows = _cfg_rows(probes + 20, start=le.MAX_EXECUTIONS_INSPECTED)
        message, _i, _c = _run([{"Items": []}], [{"Items": rows}],
                               query_params={"startingToken": first["NextToken"]},
                               probe_hits={r["workflowExecutionId"] for r in rows},
                               echo_items=True)
        assert message["Items"] == [], "every row here was already served"
        token = _decode(message["NextToken"])
        assert token["outputKey"]["workflowExecutionId"] == rows[probes - 1]["workflowExecutionId"], (
            f"the cursor must advance over the rows the page read: {token}")

    def test_the_probe_budget_names_its_own_limit(self):
        # The probe budget and the executions-inspected budget are different limits with different
        # numbers. A page cut short by the probes but reporting the inspected budget quotes a bound
        # that did not bind it, so a caller narrowing the date range or the page against that number is
        # working from the wrong figure.
        first, _inputs = self._first_page()
        probes = le.MAX_ASSET_LIST_INPUT_ROW_PROBES
        rows = _cfg_rows(probes + 20, start=le.MAX_EXECUTIONS_INSPECTED)
        message, _i, _c = _run([{"Items": []}], [{"Items": rows}],
                               query_params={"startingToken": first["NextToken"]},
                               probe_hits={r["workflowExecutionId"] for r in rows},
                               echo_items=True)
        warning = " ".join(message.get("warnings") or [])
        assert str(probes) in warning, f"the probe budget must be the bound named: {warning!r}"
        assert str(le.MAX_EXECUTIONS_INSPECTED) not in warning, (
            f"this page never reached the executions-inspected budget: {warning!r}")

    def test_a_probe_bounded_page_warns_even_when_page_size_is_small(self):
        # The probe budget is a SERVER-side bound: it cuts the page short whatever pageSize the caller
        # asked for. Reusing the inspected-budget condition suppressed the warning entirely here, so a
        # page that withheld rows for a reason the caller did not choose looked like an ordinary one.
        first, _inputs = self._first_page()
        probes = le.MAX_ASSET_LIST_INPUT_ROW_PROBES
        rows = _cfg_rows(probes + 20, start=le.MAX_EXECUTIONS_INSPECTED)
        message, _i, _c = _run([{"Items": []}], [{"Items": rows}],
                               query_params={"startingToken": first["NextToken"], "pageSize": "5"},
                               probe_hits={r["workflowExecutionId"] for r in rows},
                               echo_items=True)
        warning = " ".join(message.get("warnings") or [])
        assert str(probes) in warning, f"a probe-bounded page must report it: {warning!r}"

    def test_the_executions_inspected_budget_still_names_its_own_limit(self):
        # Control: the probe arm must not have taken over the message for a page that really did reach
        # the executions-inspected budget, which would swap one wrong number for another.
        message, _inputs = self._first_page()
        warning = " ".join(message.get("warnings") or [])
        assert str(le.MAX_EXECUTIONS_INSPECTED) in warning, warning
        assert str(le.MAX_ASSET_LIST_INPUT_ROW_PROBES) not in warning, warning

    def test_a_page_with_no_continuation_token_probes_nothing(self):
        # Control on the cost: probes are spent only where an earlier page served the input direction.
        # A first page dedupes within itself, so it must not pay for a single read.
        _m, inputs_table, _c = _run([{"Items": _input_rows(3)}], [{"Items": _cfg_rows(2)}],
                                    probe_hits={"anything"})
        base_table_reads = [c for c in inputs_table.query.call_args_list
                            if not c.kwargs.get("IndexName")]
        assert base_table_reads == [], f"a first page must not probe: {base_table_reads}"
