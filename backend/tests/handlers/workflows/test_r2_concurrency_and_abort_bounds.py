# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Two bounds that were reported as violations of the thing they bound.

The concurrency guard (executeWorkflow) walks an asset's executions of one workflow to decide whether
a conflicting run is still going. That walk has an inspection budget, and a spent budget was answered
with a 400: an asset past the bound could never launch the workflow again, and no retry cleared it
because the count only grows. The same path runs as SYSTEM_USER from the fileUpload trigger
dispatcher, so it took automation down with it. A spent budget is a statement about the request, not
about concurrency — it is now spent adaptively across the selected assets and reported as an
unconfirmed limit rather than a conflict.

The abort's second pass (executionService) re-reads the pipeline rows to catch a sub-process
registered inside the abort window. Re-reading rows that were ALREADY terminal when the abort started
issues stop calls against steps that finished normally, which can attach a "may still be running"
warning to an abort that left nothing running.

The stubs here hand back REAL dicts and TERMINATE: a MagicMock answers `.get('LastEvaluatedKey')`
truthily forever, so a paging loop built on one never exits. Every fake counts its queries and fails
the test rather than hanging the suite.
"""

import json
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

# Both handlers resolve table names at import (mirrors test_executeWorkflow.py / test_executionService_wb53.py).
for _name, _value in [
    ("ASSET_STORAGE_TABLE_NAME", "t-assets"),
    ("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2"),
    ("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2"),
    ("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates"),
    ("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema"),
    ("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets"),
    ("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux"),
    ("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc"),
    ("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2"),
    ("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec"),
    ("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md"),
    ("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg"),
    ("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files"),
    ("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of"),
    ("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om"),
    ("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or"),
    ("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs"),
    ("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs"),
    ("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg"),
    ("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows"),
    ("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines"),
    ("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2"),
]:
    os.environ.setdefault(_name, _value)

# handlers.workflows package __init__ imports get_task_builder at import time; the shared mock package
# does not provide it, so register a lightweight stub before importing the handler.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import executeWorkflow as ewv2  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402

EW = "backend.backend.handlers.workflows.executeWorkflow"
ES = "backend.backend.handlers.workflows.executionService"

# Taken from the handler's own module attributes. The test tree puts both `backend/` and
# `backend/backend/` on sys.path, so a re-imported exception class is a DIFFERENT object and a
# pytest.raises on it would never match the one the handler raises.
VAMSGeneralErrorResponse = ewv2.VAMSGeneralErrorResponse

WF_DB, WF_ID = "wfdb", "wf1"
COMPOSITE = f"{WF_DB}:{WF_ID}"


def _condition_values(condition):
    """Every literal string inside a boto3 condition tree (the objects have an opaque repr)."""
    values, stack = [], [condition]
    while stack:
        node = stack.pop()
        operands = getattr(node, "_values", None)
        if operands is None:
            values.append(node)
            continue
        stack.extend(operands)
    return [v for v in values if isinstance(v, str)]


def _input_row(index, partition, composite=COMPOSITE, file_key="/f.glb"):
    """One input row as build_workflow_execution_input_record writes it."""
    database_id, asset_id = partition.split(":")
    workflow_database_id, workflow_id = (composite.split(":") if composite else ("", ""))
    return {
        "workflowExecutionId": f"e{index:031d}",
        "databaseId:assetId": partition,
        "databaseId:assetId:inputAssetFileKey": f"{partition}:{file_key}",
        "databaseId": database_id, "assetId": asset_id,
        "inputAssetFileKey": file_key,
        "workflowDatabaseId": workflow_database_id, "workflowId": workflow_id,
        "executionStartDate": f"2026-01-01T00:00:{index % 60:02d}Z",
    }


PAGE = 100
QUERY_CAP = 2000


def _inputs_table(rows_by_partition):
    """MagicMock table whose by-asset GSI query pages real dicts and terminates.

    A MagicMock is used rather than a bare class so the same object also absorbs the write calls the
    launch path makes on this table; only `query` is given behaviour.
    """
    table = MagicMock()
    state = {"queries": 0, "partitions": []}

    def _query(**kwargs):
        state["queries"] += 1
        assert state["queries"] < QUERY_CAP, "the candidate walk did not terminate"
        partition = next(v for v in _condition_values(kwargs["KeyConditionExpression"]) if ":" in v)
        state["partitions"].append(partition)
        rows = rows_by_partition.get(partition, [])
        start = (kwargs.get("ExclusiveStartKey") or {}).get("offset", 0)
        resp = {"Items": rows[start:start + PAGE]}
        if start + PAGE < len(rows):
            resp["LastEvaluatedKey"] = {"offset": start + PAGE}
        return resp

    table.query.side_effect = _query
    table.walk_state = state
    return table


def _main_table(rows_by_id):
    """MagicMock main-execution table read by primary key one execution at a time."""
    table = MagicMock()
    reads = []

    def _query(**kwargs):
        execution_id = _condition_values(kwargs["KeyConditionExpression"])[0]
        reads.append(execution_id)
        row = rows_by_id.get(execution_id)
        return {"Items": [row] if row else []}

    table.query.side_effect = _query
    table.reads = reads
    return table


def _running_main_row(execution_id):
    return {"workflowExecutionId": execution_id,
            "workflowDatabaseId:workflowId": COMPOSITE,
            "workflow_execution_arn": "arn:ex", "executionStopDate": ""}


def _guard(rows_by_partition, main_rows, restriction="perAsset", notices=None):
    """Run _running_execution_exists over the fakes; returns (result, inputs_table, main_table)."""
    inputs_table = _inputs_table(rows_by_partition)
    main_table = _main_table(main_rows)
    selected_inputs = [
        {"databaseId": p.split(":")[0], "assetId": p.split(":")[1], "relativeFileKey": "/f.glb"}
        for p in sorted(rows_by_partition)]
    asset_records = {(i["databaseId"], i["assetId"]): {"assetLocation": {"Key": ""}}
                     for i in selected_inputs}

    def _table(name):
        return {ewv2.workflow_execution_inputs_table: inputs_table,
                ewv2.workflow_execution_database_v2: main_table}.get(name, MagicMock())

    with patch.object(ewv2.dynamodb, "Table", side_effect=_table), \
            patch(f"{EW}.sfn_client") as sfn:
        # No stopDate in the describe response == still running.
        sfn.describe_execution.return_value = {"status": "RUNNING"}
        result = ewv2._running_execution_exists(
            WF_DB, WF_ID, selected_inputs, asset_records, restriction, notices=notices)
    return result, inputs_table, main_table


@pytest.mark.unit
class TestASpentInspectionBudgetDoesNotDenyTheLaunch:
    """A budget that ran out is not evidence of a conflict. Denying on it is unrecoverable: the
    asset's execution count only grows, so every later launch fails the same way."""

    def test_an_asset_deeper_than_the_budget_still_launches_when_nothing_is_running(self):
        # The core regression. One asset carrying more executions of this workflow than a single
        # request can confirm, and NOT ONE of them still running: the only correct answer is "no
        # conflict", and the previous answer was a 400 that no retry could clear.
        deep = ewv2.MAX_CONCURRENCY_CANDIDATES_INSPECTED + 10
        notices = []
        result, _inputs, main = _guard(
            {"db1:a1": [_input_row(i, "db1:a1") for i in range(deep)]}, {}, notices=notices)
        assert result is False, "an authorized launch was denied by an exhausted inspection budget"
        # And the incompleteness is stated rather than swallowed, so the caller is not told the limit
        # held when it could not be confirmed.
        assert notices, "a partially-inspected guard must say so"
        assert "could not be fully confirmed" in " ".join(notices)
        # Positive control on the fixture: the budget really was spent, so this is the spent-budget
        # case and not a walk that happened to finish.
        assert len(main.reads) >= ewv2.MAX_CONCURRENCY_CANDIDATES_INSPECTED

    def test_a_running_execution_of_this_workflow_is_still_a_conflict(self):
        # The restriction stays ENFORCED: nothing above weakens the case the guard exists for.
        rows = [_input_row(i, "db1:a1") for i in range(3)]
        notices = []
        result, _inputs, _main = _guard(
            {"db1:a1": rows},
            {rows[1]["workflowExecutionId"]: _running_main_row(rows[1]["workflowExecutionId"])},
            notices=notices)
        assert result is True
        assert notices == [], "a decided conflict is not an unconfirmed limit"

    def test_a_conflict_is_reported_even_when_it_sits_past_the_budget(self):
        # The budget bounds how many candidates are CONFIRMED, and the walk is newest-first, so a
        # running execution among the newest is still found on an asset whose history is far deeper
        # than the budget. Without this, "does not deny" could have been implemented by giving up.
        deep = ewv2.MAX_CONCURRENCY_CANDIDATES_INSPECTED + 50
        rows = [_input_row(i, "db1:a1") for i in range(deep)]
        newest = rows[0]
        result, _inputs, _main = _guard(
            {"db1:a1": rows},
            {newest["workflowExecutionId"]: _running_main_row(newest["workflowExecutionId"])})
        assert result is True

    def test_an_exhausted_walk_reports_no_unconfirmed_limit(self):
        # Control for the notice: an ordinary asset's whole history fits, so the guard KNOWS there is
        # no conflict and must not attach a caveat to every launch.
        notices = []
        result, _inputs, _main = _guard(
            {"db1:a1": [_input_row(i, "db1:a1") for i in range(3)]}, {}, notices=notices)
        assert result is False
        assert notices == []

    def test_a_restriction_of_none_reads_nothing_and_reports_nothing(self):
        notices = []
        result, inputs_table, _main = _guard(
            {"db1:a1": [_input_row(0, "db1:a1")]}, {}, restriction="none", notices=notices)
        assert result is False
        assert inputs_table.walk_state["queries"] == 0
        assert notices == []

    def test_the_notices_argument_is_optional(self):
        # The trigger dispatcher and the older call shape pass five positional arguments only; a
        # required sixth would turn a deep-history launch into a TypeError instead of a launch.
        deep = ewv2.MAX_CONCURRENCY_CANDIDATES_INSPECTED + 10
        result, _inputs, _main = _guard(
            {"db1:a1": [_input_row(i, "db1:a1") for i in range(deep)]}, {})
        assert result is False


@pytest.mark.unit
class TestTheBudgetIsSpentAdaptivelyAcrossTheSelectedAssets:
    """An equal share per asset stops while most of the budget is unspent: one deep asset exceeds its
    1/n share and the guard gave up even though the other assets barely used theirs."""

    def test_one_deep_asset_among_shallow_ones_is_still_fully_inspected(self):
        # Four assets, total candidates far below the whole budget, but the deep one holds more than
        # 1/4 of it. Every candidate is reachable inside the budget, so the guard can answer
        # definitively — and must, since this is an ordinary multi-asset selection.
        share = ewv2.MAX_CONCURRENCY_CANDIDATES_INSPECTED // 4
        partitions = {"db1:deep": [_input_row(i, "db1:deep") for i in range(share + 10)]}
        for n in range(1, 4):
            partitions[f"db1:s{n}"] = [_input_row(1000 * n + i, f"db1:s{n}") for i in range(2)]
        total = sum(len(v) for v in partitions.values())
        assert total < ewv2.MAX_CONCURRENCY_CANDIDATES_INSPECTED, (
            "fixture: the whole selection must fit inside one budget for this to be the unspent case")
        notices = []
        result, _inputs, main = _guard(partitions, {}, notices=notices)
        assert result is False
        assert notices == [], (
            "the selection fits inside the budget, so nothing was left unexamined and no caveat "
            "belongs on the response")
        # Every candidate of every asset was confirmed. Asserted as containment, so a stricter
        # implementation that reads more is still correct.
        expected = {row["workflowExecutionId"] for rows in partitions.values() for row in rows}
        assert expected <= set(main.reads)

    def test_no_selected_asset_goes_entirely_unexamined(self):
        # The anti-starvation floor: a selection spanning more assets than the budget has units still
        # gets each asset's newest candidate looked at, so a conflict on the last asset in the
        # selection is not invisible.
        count = ewv2.MAX_CONCURRENCY_CANDIDATES_INSPECTED + 5
        partitions = {f"db1:a{n:04d}": [_input_row(n, f"db1:a{n:04d}")] for n in range(count)}
        result, inputs_table, main = _guard(partitions, {})
        assert result is False
        assert set(inputs_table.walk_state["partitions"]) == set(partitions)
        expected = {rows[0]["workflowExecutionId"] for rows in partitions.values()}
        assert expected <= set(main.reads)

    def test_a_deep_asset_cannot_starve_the_others(self):
        # The property the per-asset share existed to provide, kept by interleaving: a first asset
        # with a history deeper than the whole budget must not consume it before the second asset
        # contributes a candidate — that is what made a conflict on the second one invisible.
        deep = 2 * ewv2.MAX_CONCURRENCY_CANDIDATES_INSPECTED + 10
        partitions = {"db1:a1": [_input_row(i, "db1:a1") for i in range(deep)],
                      "db1:a2": [_input_row(1000 + i, "db1:a2") for i in range(deep)]}
        _result, inputs_table, main = _guard(partitions, {})
        assert set(inputs_table.walk_state["partitions"]) == {"db1:a1", "db1:a2"}
        # The ids encode which asset a candidate came from (a2's start at 1000).
        indexes = [int(execution_id.lstrip("e0") or "0") for execution_id in main.reads]
        assert any(i < 1000 for i in indexes), "no candidate from the first asset was confirmed"
        assert any(i >= 1000 for i in indexes), (
            "no candidate from the second asset was confirmed: its share of the budget was spent "
            "elsewhere, which is what made a conflict there invisible")


_WORKFLOW = {
    "databaseId": WF_DB, "workflowId": WF_ID, "workflowName": "WF", "enabled": True,
    "archived": False,
    "workflow_arn": "arn:aws:states:us-east-1:1:stateMachine:vams-wf1", "jobNames": ["job-p1"],
    "specifiedPipelines": [{"pipelineDatabaseId": "db1", "pipelineId": "p1", "jobName": "p1"}],
    "systemConfig": {
        "inputFileArity": "one",
        "assetScope": {"crossAssetAllowed": False, "singleAssetOnly": True,
                       "wholeAssetAllowed": False, "folderAllowed": False},
        "metadataInputs": {"assetMetadata": False, "fileMetadata": False, "fileAttributes": False},
        "inputFileFilters": {"allow": [], "exclude": []},
        # The restriction under test: this workflow declares it, so the guard runs for real.
        "concurrencyRestriction": "perAsset",
        "outputTarget": {"locationType": "asset", "allowOverride": False},
    },
}
_PIPELINE = {
    "databaseId": "db1", "pipelineId": "p1", "pipelineName": "P1", "enabled": True,
    "archived": False,
    "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn"},
                        "waitForCallback": "Disabled"},
    "systemConfig": {"inputFileArity": "one", "requireTemplate": False,
                     "allowCustomTemplateOverride": False,
                     "assetScope": {"crossAssetAllowed": False, "singleAssetOnly": True,
                                    "wholeAssetAllowed": False, "folderAllowed": False},
                     "inputFileFilters": {"allow": [], "exclude": []}},
}
_ASSET = {"databaseId": "db1", "assetId": "a1", "assetName": "A1", "bucketId": "bkt-1",
          "assetLocation": {"Key": "a1/"}}


@pytest.mark.unit
class TestATriggerDispatchedLaunchSurvivesADeepAssetHistory:
    """workflowTriggerDispatch invokes this handler as a lambdaCrossCall with userName SYSTEM_USER, and
    reads only the status code. A 400 from the guard therefore stopped fileUpload-triggered pipelines
    on an active asset with nothing but a log line to show it."""

    @staticmethod
    def _cross_call_event():
        # The shape workflowTriggerDispatch sends: no headers, identity in lambdaCrossCall.
        return {
            "requestContext": {"http": {"method": "POST",
                                        "path": f"/workflows/{WF_DB}/{WF_ID}/execute"}},
            "pathParameters": {"workflowDatabaseId": WF_DB, "workflowId": WF_ID},
            "queryStringParameters": {},
            "body": json.dumps({"inputFiles": [
                {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}),
            "lambdaCrossCall": {"userName": "SYSTEM_USER"},
        }

    def _launch(self, input_rows, main_rows):
        inputs_table = _inputs_table({"db1:a1": input_rows})
        main_table = _main_table(main_rows)
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        enforcer.enforceAPI.return_value = True

        def _table(name):
            return {ewv2.workflow_execution_inputs_table: inputs_table,
                    ewv2.workflow_execution_database_v2: main_table}.get(name, MagicMock())

        with patch(f"{EW}._get_workflow", return_value=dict(_WORKFLOW)), \
                patch(f"{EW}._get_pipeline", return_value=dict(_PIPELINE)), \
                patch(f"{EW}._get_asset", return_value=dict(_ASSET)), \
                patch(f"{EW}._default_run_bucket",
                      return_value={"bucketName": "run-bucket", "baseAssetsPrefix": ""}), \
                patch(f"{EW}._asset_bucket_details",
                      return_value={"bucketName": "asset-bucket", "baseAssetsPrefix": ""}), \
                patch(f"{EW}._input_exists_in_s3", return_value=(True, "v-resolved")), \
                patch(f"{EW}.CasbinEnforcer", return_value=enforcer), \
                patch(f"{EW}.request_to_claims", return_value={"tokens": ["SYSTEM_USER"]}), \
                patch(f"{EW}.s3c"), patch(f"{EW}.sfn_client") as m_sfn, \
                patch(f"{EW}.dynamodb") as m_dynamo:
            m_dynamo.Table.side_effect = _table
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            # A real dict with no stopDate == still running. Left as a MagicMock, `.get("stopDate")`
            # answers truthily and every candidate reads as already stopped, which would make the
            # conflict control below pass vacuously.
            m_sfn.describe_execution.return_value = {"status": "RUNNING"}
            resp = ewv2.lambda_handler(self._cross_call_event(), MagicMock())
        return resp, m_sfn

    def test_the_state_machine_still_starts_and_the_response_names_the_unconfirmed_limit(self):
        deep = ewv2.MAX_CONCURRENCY_CANDIDATES_INSPECTED + 10
        resp, m_sfn = self._launch([_input_row(i, "db1:a1") for i in range(deep)], {})
        assert resp["statusCode"] == 200, resp["body"]
        m_sfn.start_execution.assert_called_once()
        message = json.loads(resp["body"])["message"]
        assert any("could not be fully confirmed" in w for w in (message.get("warnings") or [])), (
            f"the launch must state that the limit was not confirmed: {message}")

    def test_a_real_conflict_still_stops_the_trigger_dispatched_launch(self):
        # Positive control on the same path: the restriction is still enforced for SYSTEM_USER, so
        # "does not deny" has not become "never denies".
        rows = [_input_row(i, "db1:a1") for i in range(3)]
        resp, m_sfn = self._launch(
            rows, {rows[0]["workflowExecutionId"]: _running_main_row(rows[0]["workflowExecutionId"])})
        assert resp["statusCode"] == 400, resp["body"]
        assert "already running" in json.loads(resp["body"])["message"]
        m_sfn.start_execution.assert_not_called()


def _pipeline_row(subs, status="RUNNING", pipeline_id="P1"):
    return {"pipelineExecutionId": pipeline_id, "workflowExecutionId": "x-exec",
            "executionStatus": status, "registeredSubExecutions": subs,
            "executionStopDate": ""}


@pytest.mark.unit
class TestTheAbortSecondPassLeavesFinishedStepsAlone:
    """The second pass exists to catch a sub-process registered inside the abort window. A row that was
    already terminal when the abort started is not that case: its step finished on its own and released
    its own sub-processes, so stopping them again can only misreport a clean abort."""

    def _abort(self, row_reads, sub_spy=None):
        main_table, pexec_table = MagicMock(), MagicMock()
        reads = list(row_reads)

        def _table(name):
            return pexec_table if name == le.pipeline_executions_table else main_table

        with patch(f"{ES}.get_execution_main_row",
                   return_value={"workflowExecutionId": "x-exec", "workflowId": WF_ID,
                                 "workflowDatabaseId": WF_DB,
                                 "workflow_execution_arn": "arn:ex:main",
                                 "executionStatus": "RUNNING", "executionStopDate": ""}), \
                patch(f"{ES}.authorize_abort", return_value=(True, "")), \
                patch(f"{ES}.get_pipeline_execution_rows",
                      side_effect=lambda execution_id: reads.pop(0) if reads else []), \
                patch(f"{ES}._stop_sfn_execution"), \
                patch(f"{ES}._abort_registered_sub_process",
                      side_effect=(sub_spy or (lambda sub: ""))), \
                patch(f"{ES}._persist_reconciled_main_row"), \
                patch(f"{ES}.log_actions"), \
                patch.object(le.dynamodb, "Table", side_effect=_table), \
                patch.object(le.eo, "set_pipeline_status"):
            resp = le.abort_execution({}, "x-exec")
        return resp

    def test_a_step_that_finished_before_the_abort_is_not_stopped(self):
        finished = {"resourceType": "ecsTask", "taskArn": "arn:task:finished"}
        attempted = []
        resp = self._abort(
            [[_pipeline_row([finished], status="SUCCEEDED")],
             [_pipeline_row([finished], status="SUCCEEDED")]],
            sub_spy=lambda sub: attempted.append(sub.get("taskArn")) or
            "could not be aborted: arn:task:finished; it may still be running.")
        assert resp["statusCode"] == 200
        assert attempted == [], (
            f"the sub-processes of a step that finished normally were stopped again: {attempted}")
        body = json.loads(resp["body"])
        assert "warnings" not in body, (
            f"a clean abort must not warn about work that finished on its own: {body}")

    def test_a_sub_process_registered_inside_the_window_is_still_stopped(self):
        # Positive control: the skip must not have disabled the second pass. This row was RUNNING at
        # the first read, so it IS the late-registration case the pass exists for.
        late = {"resourceType": "batchJob", "jobId": "job-late"}
        stopped = []
        resp = self._abort(
            [[_pipeline_row([])], [_pipeline_row([late], status="ABORTED")]],
            sub_spy=lambda sub: stopped.append(sub.get("jobId")) or "")
        assert resp["statusCode"] == 200
        assert "job-late" in stopped

    def test_a_row_that_appeared_inside_the_window_is_still_stopped(self):
        # A pipeline row absent from the first read is likewise not a pre-terminal row, so the skip
        # must not swallow it.
        late = {"resourceType": "batchJob", "jobId": "job-new-row"}
        stopped = []
        resp = self._abort(
            [[], [_pipeline_row([late], status="RUNNING", pipeline_id="P2")]],
            sub_spy=lambda sub: stopped.append(sub.get("jobId")) or "")
        assert resp["statusCode"] == 200
        assert "job-new-row" in stopped

    def test_a_late_registration_on_a_pre_terminal_row_is_reported_when_that_row_was_running(self):
        # The distinguishing case: the SAME pipeline id, RUNNING at the first read and terminal at the
        # second, carries a job registered in between. The skip keys on the FIRST read's status, so
        # this one is still caught.
        late = {"resourceType": "deadlineCloudJob", "farmId": "f", "queueId": "q", "jobId": "job-x"}
        stopped = []
        resp = self._abort(
            [[_pipeline_row([], status="RUNNING")],
             [_pipeline_row([late], status="SUCCEEDED")]],
            sub_spy=lambda sub: stopped.append(sub.get("jobId")) or "")
        assert resp["statusCode"] == 200
        assert "job-x" in stopped


@pytest.mark.unit
class TestAPartialDeadlineRegistrationIsNamed:
    """A registration naming a job but no farm or queue cannot be cancelled. Returning "" reports a
    clean abort while a farm job may still be running — the one outcome the return value exists to
    prevent."""

    def test_a_registration_without_a_farm_or_queue_is_reported(self):
        client = MagicMock()
        with patch.object(le, "deadline_client", client):
            warning = le._abort_registered_sub_process(
                {"resourceType": "deadlineCloudJob", "jobId": "job-1"})
        assert "job-1" in warning
        assert "may still be running" in warning
        client.update_job.assert_not_called()

    def test_a_registration_missing_only_the_queue_is_reported(self):
        client = MagicMock()
        with patch.object(le, "deadline_client", client):
            warning = le._abort_registered_sub_process(
                {"resourceType": "deadlineCloudJob", "farmId": "farm-1", "jobId": "job-2"})
        assert "job-2" in warning
        client.update_job.assert_not_called()

    def test_a_registration_with_no_job_id_stays_silent(self):
        # Control: with no job named there is nothing to report and nothing was left running — the
        # same conclusion the Step Functions and Batch arms reach for an empty locator.
        client = MagicMock()
        with patch.object(le, "deadline_client", client):
            assert le._abort_registered_sub_process(
                {"resourceType": "deadlineCloudJob", "farmId": "f", "queueId": "q"}) == ""
        client.update_job.assert_not_called()

    def test_a_complete_registration_is_cancelled_and_reports_nothing(self):
        # Control: the partial-row arm must not have swallowed the working path.
        client = MagicMock()
        with patch.object(le, "deadline_client", client):
            warning = le._abort_registered_sub_process(
                {"resourceType": "deadlineCloudJob", "farmId": "farm-1", "queueId": "queue-1",
                 "jobId": "job-3"})
        assert warning == ""
        assert client.update_job.call_args.kwargs == {
            "farmId": "farm-1", "queueId": "queue-1", "jobId": "job-3",
            "targetTaskRunStatus": "CANCELED"}
