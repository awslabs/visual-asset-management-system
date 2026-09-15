# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The three places a workflow execution's terminal status is announced on the orchestration bus --
the end-state lambda (`processWorkflowExecutionOutput`), the error handler (`handleExecutionError`)
and the abort API (`executionService`) -- and the one rule they share: the event is published only by
the writer that WON the terminal-status guard, so a finished run is announced exactly once. Each
emitter is best-effort: skipped when no bus is configured, and a publish failure is logged rather than
raised out of the terminal write that precedes it."""

import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

for _key, _value in {
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "t-buckets",
    "METADATA_SERVICE_LAMBDA_FUNCTION_NAME": "t-md-svc",
    "FILE_UPLOAD_LAMBDA_FUNCTION_NAME": "t-upload",
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "ASSET_UPLOAD_TABLE_NAME": "t-asset-upload",
    "DATABASE_STORAGE_TABLE_NAME": "t-db",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME": "t-pin-files",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "WORKFLOW_STORAGE_TABLE_NAME": "t-workflows",
    "PIPELINE_STORAGE_TABLE_NAME": "t-pipelines",
    "EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME": "t-execv2",
}.items():
    os.environ.setdefault(_key, _value)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.common.workflows import executionRecords as er  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402
from backend.backend.handlers.workflows.sfn import handleExecutionError as heh  # noqa: E402
from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po  # noqa: E402

BUS = "arn:aws:events:us-east-1:123456789012:event-bus/vams-orchestration"
PREFIX = "vams.orchestration"
EXEC_ID = "e3000000000000000000000000000003"
STARTED = "2026-01-01T00:00:00Z"
STOPPED = "2026-01-01T00:05:00Z"

EMITTERS = [
    pytest.param(po, id="processWorkflowExecutionOutput"),
    pytest.param(heh, id="handleExecutionError"),
    pytest.param(le, id="executionService"),
]


def _conditional_failure():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}},
                       "UpdateItem")


def _published(events_client):
    """The single entry the emitter put on the bus."""
    events_client.put_events.assert_called_once()
    entries = events_client.put_events.call_args.kwargs["Entries"]
    assert len(entries) == 1
    return entries[0]


@pytest.mark.unit
class TestEachEmitterSharesOneContract:
    """`emit_workflow_execution_completed` in each of the three modules."""

    @pytest.mark.parametrize("module", EMITTERS)
    def test_the_entry_follows_the_completion_event_contract(self, module):
        events_client = MagicMock()
        with patch.object(module, "events_client", events_client), \
                patch.object(module, "orchestration_bus_arn", BUS), \
                patch.object(module, "orchestration_event_source_prefix", PREFIX):
            module.emit_workflow_execution_completed(
                EXEC_ID, "wf-db", "wf-1", "SUCCEEDED", started_at=STARTED, completed_at=STOPPED,
                execution_group_id="grp-1")
        entry = _published(events_client)
        assert entry == er.workflow_execution_completed_event(
            BUS, PREFIX, EXEC_ID, "wf-db", "wf-1", "SUCCEEDED", STARTED, STOPPED, "grp-1")
        assert entry["EventBusName"] == BUS
        assert entry["Source"] == f"{PREFIX}.execution.{EXEC_ID}"
        assert entry["DetailType"] == "workflow.execution.completed"
        detail = json.loads(entry["Detail"])
        assert detail == {"executionId": EXEC_ID, "workflowDatabaseId": "wf-db", "workflowId": "wf-1",
                          "status": "SUCCEEDED", "startedAt": STARTED, "completedAt": STOPPED,
                          "executionGroupId": "grp-1"}

    @pytest.mark.parametrize("module", EMITTERS)
    def test_no_bus_configured_means_no_publish(self, module):
        events_client = MagicMock()
        with patch.object(module, "events_client", events_client), \
                patch.object(module, "orchestration_bus_arn", ""):
            module.emit_workflow_execution_completed(
                EXEC_ID, "wf-db", "wf-1", "FAILED", started_at=STARTED, completed_at=STOPPED)
        events_client.put_events.assert_not_called()

    @pytest.mark.parametrize("module", EMITTERS)
    def test_a_publish_failure_is_logged_not_raised(self, module):
        events_client = MagicMock()
        events_client.put_events.side_effect = ClientError(
            {"Error": {"Code": "InternalException", "Message": "events down"}}, "PutEvents")
        with patch.object(module, "events_client", events_client), \
                patch.object(module, "orchestration_bus_arn", BUS), \
                patch.object(module, "orchestration_event_source_prefix", PREFIX), \
                patch.object(module, "logger") as logger:
            module.emit_workflow_execution_completed(
                EXEC_ID, "wf-db", "wf-1", "FAILED", started_at=STARTED, completed_at=STOPPED)
        logger.exception.assert_called_once()
        assert EXEC_ID in logger.exception.call_args.args[0]

    @pytest.mark.parametrize("module", EMITTERS)
    def test_the_event_carries_no_template_bodies_or_tag_values(self, module):
        events_client = MagicMock()
        with patch.object(module, "events_client", events_client), \
                patch.object(module, "orchestration_bus_arn", BUS), \
                patch.object(module, "orchestration_event_source_prefix", PREFIX):
            module.emit_workflow_execution_completed(
                EXEC_ID, "wf-db", "wf-1", "SUCCEEDED", started_at=STARTED, completed_at=STOPPED)
        detail = json.loads(_published(events_client)["Detail"])
        assert set(detail) <= {"executionId", "workflowDatabaseId", "workflowId", "status",
                               "startedAt", "completedAt", "executionGroupId"}


def _record_outputs(main_table, events_client):
    """Drive `record_execution_outputs` with no output rows, so the only writes are the pipeline row's
    status (patched) and the main row's terminal status on `main_table`."""
    dynamo = MagicMock()
    dynamo.Table.return_value = main_table
    with patch.object(po, "events_client", events_client), \
            patch.object(po, "orchestration_bus_arn", BUS), \
            patch.object(po, "orchestration_event_source_prefix", PREFIX), \
            patch.object(po.eo, "set_pipeline_status") as set_pipeline_status:
        po.record_execution_outputs(
            dynamo, EXEC_ID, "pexec-1", "wf-db", "wf-1", "bucket",
            output_files=[], output_metadata=[], output_results=[], result_log="", execution_log="",
            log_group_arn="", log_stream_name="", execution_status="SUCCEEDED")
    return set_pipeline_status


@pytest.mark.unit
class TestEndStateLambdaEmitsAfterTheTerminalWrite:

    def test_the_winning_terminal_write_publishes_with_the_rows_start_date_and_group(self):
        main_table = MagicMock()
        main_table.update_item.return_value = {"Attributes": {
            "executionStatus": "SUCCEEDED", "executionStartDate": STARTED, "executionGroupId": "grp-9"}}
        events_client = MagicMock()
        _record_outputs(main_table, events_client)

        write = main_table.update_item.call_args.kwargs
        assert write["ReturnValues"] == "ALL_NEW"
        assert "ConditionExpression" in write
        detail = json.loads(_published(events_client)["Detail"])
        assert detail["executionId"] == EXEC_ID
        assert detail["status"] == "SUCCEEDED"
        assert detail["startedAt"] == STARTED
        assert detail["completedAt"] == write["ExpressionAttributeValues"][":s"]
        assert detail["executionGroupId"] == "grp-9"
        assert (detail["workflowDatabaseId"], detail["workflowId"]) == ("wf-db", "wf-1")

    def test_a_terminal_write_that_lost_the_race_publishes_nothing(self):
        main_table = MagicMock()
        main_table.update_item.side_effect = _conditional_failure()
        events_client = MagicMock()
        _record_outputs(main_table, events_client)
        events_client.put_events.assert_not_called()

    def test_any_other_write_failure_still_surfaces(self):
        main_table = MagicMock()
        main_table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}}, "UpdateItem")
        events_client = MagicMock()
        with pytest.raises(ClientError):
            _record_outputs(main_table, events_client)
        events_client.put_events.assert_not_called()

    def test_the_publish_happens_after_the_write(self):
        order = []
        main_table = MagicMock()
        main_table.update_item.side_effect = lambda **kw: (order.append("write"), {"Attributes": {}})[1]
        events_client = MagicMock()
        events_client.put_events.side_effect = lambda **kw: order.append("publish")
        _record_outputs(main_table, events_client)
        assert order == ["write", "publish"]


def _reconcile(main_row, events_client, finalize_raises=None):
    """Drive `reconcile_failed_execution` with no pipeline rows, so the only main-row work is the
    read of `main_row` and the FAILED finalization (patched)."""
    main_table = MagicMock()
    main_table.query.return_value = {"Items": [main_row] if main_row else []}
    dynamodb = MagicMock()
    dynamodb.Table.return_value = main_table
    finalize = MagicMock(side_effect=finalize_raises)
    with patch.object(heh, "events_client", events_client), \
            patch.object(heh, "orchestration_bus_arn", BUS), \
            patch.object(heh, "orchestration_event_source_prefix", PREFIX), \
            patch.object(heh, "dynamodb", dynamodb), \
            patch.object(heh, "_fetch_execution_log", return_value=""), \
            patch.object(heh, "_get_pipeline_rows", return_value=[]), \
            patch.object(heh.eo, "mark_inflight_pipelines_terminal", return_value=[]), \
            patch.object(heh.eo, "finalize_main_row", finalize):
        heh.reconcile_failed_execution(
            {"workflowExecutionId": EXEC_ID, "workflowDatabaseId": "wf-db", "workflowId": "wf-1"},
            {"Error": "States.TaskFailed", "Cause": "boom"})
    return finalize


@pytest.mark.unit
class TestErrorHandlerEmitsWhenItFinalizes:

    def test_a_running_row_is_finalized_failed_and_announced(self):
        events_client = MagicMock()
        finalize = _reconcile({"executionStatus": "RUNNING", "executionStartDate": STARTED,
                               "executionGroupId": "grp-2"}, events_client)
        finalize.assert_called_once()
        assert finalize.call_args.args[5] == "FAILED"
        detail = json.loads(_published(events_client)["Detail"])
        assert detail["status"] == "FAILED"
        assert detail["executionId"] == EXEC_ID
        assert detail["startedAt"] == STARTED
        assert detail["completedAt"] == finalize.call_args.args[6]
        assert detail["executionGroupId"] == "grp-2"

    def test_a_row_with_no_status_yet_is_finalized_and_announced(self):
        events_client = MagicMock()
        finalize = _reconcile({"executionStartDate": STARTED}, events_client)
        finalize.assert_called_once()
        assert json.loads(_published(events_client)["Detail"])["status"] == "FAILED"

    @pytest.mark.parametrize("terminal", ["SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT"])
    def test_an_already_terminal_row_is_neither_rewritten_nor_announced(self, terminal):
        events_client = MagicMock()
        finalize = _reconcile({"executionStatus": terminal, "executionStartDate": STARTED},
                              events_client)
        finalize.assert_not_called()
        events_client.put_events.assert_not_called()

    def test_a_finalization_that_raises_publishes_nothing(self):
        events_client = MagicMock()
        _reconcile({"executionStatus": "RUNNING"}, events_client,
                   finalize_raises=RuntimeError("write failed"))
        events_client.put_events.assert_not_called()


def _abort(main_item, events_client, persisted):
    """Drive `abort_execution` past its authorization and sub-process stops to the main-row write,
    whose outcome `persisted` decides."""
    with patch.object(le, "get_execution_main_row", return_value=main_item), \
            patch.object(le, "authorize_abort", return_value=(True, "")), \
            patch.object(le, "get_pipeline_execution_rows", return_value=[]), \
            patch.object(le, "_stop_sfn_execution"), \
            patch.object(le, "_persist_reconciled_main_row", return_value=persisted) as persist, \
            patch.object(le, "log_actions"), \
            patch.object(le.dynamodb, "Table"), \
            patch.object(le.eo, "set_pipeline_status"), \
            patch.object(le, "events_client", events_client), \
            patch.object(le, "orchestration_bus_arn", BUS), \
            patch.object(le, "orchestration_event_source_prefix", PREFIX):
        response = le.abort_execution({}, EXEC_ID)
    return response, persist


def _running_main_item(**overrides):
    item = {"workflowExecutionId": EXEC_ID, "workflowDatabaseId:workflowId": "wf-db:wf-1",
            "workflowDatabaseId": "wf-db", "workflowId": "wf-1", "workflow_execution_arn": "arn:ex",
            "executionStatus": "RUNNING", "executionStartDate": STARTED, "executionStopDate": "",
            "executionGroupId": "grp-3"}
    item.update(overrides)
    return item


@pytest.mark.unit
class TestAbortEmitsWhenItsWriteLands:

    def test_an_abort_that_wrote_aborted_announces_it(self):
        events_client = MagicMock()
        response, persist = _abort(_running_main_item(), events_client, persisted=True)
        assert response["statusCode"] == 200
        persist.assert_called_once()
        assert persist.call_args.kwargs["only_if_not_terminal"] is True
        detail = json.loads(_published(events_client)["Detail"])
        assert detail["status"] == "ABORTED"
        assert detail["executionId"] == EXEC_ID
        assert (detail["workflowDatabaseId"], detail["workflowId"]) == ("wf-db", "wf-1")
        assert detail["startedAt"] == STARTED
        assert detail["completedAt"] == persist.call_args.args[1]["executionStopDate"]
        assert detail["executionGroupId"] == "grp-3"

    def test_an_abort_whose_write_lost_the_terminal_race_announces_nothing(self):
        """The end-state lambda finished the run between the abort's read and its write; that writer
        announced the run, so the abort must not announce it a second time as ABORTED."""
        events_client = MagicMock()
        response, persist = _abort(_running_main_item(), events_client, persisted=False)
        assert response["statusCode"] == 200
        persist.assert_called_once()
        events_client.put_events.assert_not_called()

    @pytest.mark.parametrize("terminal", ["SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT"])
    def test_an_already_terminal_execution_is_neither_rewritten_nor_announced(self, terminal):
        events_client = MagicMock()
        response, persist = _abort(_running_main_item(executionStatus=terminal, executionStopDate=STOPPED),
                                   events_client, persisted=True)
        assert response["statusCode"] == 200
        persist.assert_not_called()
        events_client.put_events.assert_not_called()

    def test_the_abort_keeps_an_existing_stop_date_in_the_event(self):
        events_client = MagicMock()
        _abort(_running_main_item(executionStopDate=STOPPED), events_client, persisted=True)
        assert json.loads(_published(events_client)["Detail"])["completedAt"] == STOPPED
