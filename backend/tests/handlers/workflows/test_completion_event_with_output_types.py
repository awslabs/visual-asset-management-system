# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The completion event and the execution's output types, recorded together on each terminal path.

A pipeline rule's result reaches compliance through the `workflow.execution.completed` event: the
compliance workflow callback reads the pipeline's `compliance-output.json` from the execution's
output-result rows once the event arrives. The same terminal writers record the execution's
`outputTypes` on its configuration row. These tests drive each writer once and check both:

* success (the end-state lambda): the result row and the output types are written before the one
  SUCCEEDED event, whose detail the callback accepts;
* failure (the error handler): the output types are derived from the rows that exist and the one
  FAILED event is published;
* abort (the abort API): the one ABORTED event is published, and the configuration row keeps its
  launch-time output types, since only the end-state and error writers record them.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from aws_lambda_powertools.utilities.parser import parse

from backend.tests.handlers.workflows import test_workflow_completion_event_emit as base
from backend.backend.models.executions import WorkflowExecutionCompletedDetailModel

po, heh, le = base.po, base.heh, base.le
EXEC_ID = "e0000000000000000000000000000001"
PEXEC_ID = "pexec-compliance-1"
COMPLIANCE_OUTPUT = json.dumps({"measurements": {"execution_success": 1}})


class _Tables:
    """One MagicMock per table name, created on first use, so each write lands on its own table."""

    def __init__(self):
        self.by_name = {}
        self.resource = MagicMock()
        self.resource.Table.side_effect = self.table

    def table(self, name):
        if name not in self.by_name:
            self.by_name[name] = MagicMock(name=name)
        return self.by_name[name]


def _detail(events_client):
    entry = base._published(events_client)
    return json.loads(entry["Detail"])


@pytest.mark.unit
class TestTheSucceededPath:

    def test_the_result_and_output_types_are_written_before_the_one_announcement(self):
        order = []
        tables = _Tables()
        results = tables.table(po.pipeline_execution_output_results_table)
        batch = MagicMock()
        batch.put_item.side_effect = lambda **kw: order.append(
            ("result", kw["Item"]["relativeFilePath"]))
        results.batch_writer.return_value.__enter__.return_value = batch
        config = tables.table(po.workflow_execution_configuration_table)
        config.update_item.side_effect = lambda **kw: order.append(
            ("outputTypes", kw["ExpressionAttributeValues"][":t"]))
        main = tables.table(po.workflow_execution_database_v2)
        main.update_item.side_effect = lambda **kw: (
            order.append(("main", kw["ExpressionAttributeValues"][":st"])),
            {"Attributes": {"executionStartDate": base.STARTED, "executionGroupId": "grp-c"}})[1]
        events_client = MagicMock()
        events_client.put_events.side_effect = lambda **kw: order.append(("publish", None))

        with patch.object(po, "events_client", events_client), \
                patch.object(po, "orchestration_bus_arn", base.BUS), \
                patch.object(po, "orchestration_event_source_prefix", base.PREFIX), \
                patch.object(po.eo, "set_pipeline_status"):
            po.record_execution_outputs(
                tables.resource, EXEC_ID, PEXEC_ID, "wf-db", "wf-1", "bucket",
                output_files=[], output_metadata=[],
                output_results=[{"relativeFilePath": "/compliance-output.json",
                                 "resultsContent": COMPLIANCE_OUTPUT, "s3Key": "results/x"}],
                result_log="", execution_log="", log_group_arn="", log_stream_name="",
                execution_status="SUCCEEDED")

        assert order == [("result", "/compliance-output.json"), ("outputTypes", ["Result"]),
                         ("main", "SUCCEEDED"), ("publish", None)]
        detail = parse(_detail(events_client), model=WorkflowExecutionCompletedDetailModel)
        assert (detail.executionId, detail.status, detail.executionGroupId) == (
            EXEC_ID, "SUCCEEDED", "grp-c")

    def test_a_lost_terminal_race_records_the_output_types_and_announces_nothing(self):
        tables = _Tables()
        tables.table(po.workflow_execution_database_v2).update_item.side_effect = \
            base._conditional_failure()
        events_client = MagicMock()
        with patch.object(po, "events_client", events_client), \
                patch.object(po, "orchestration_bus_arn", base.BUS), \
                patch.object(po, "orchestration_event_source_prefix", base.PREFIX), \
                patch.object(po.eo, "set_pipeline_status"):
            po.record_execution_outputs(
                tables.resource, EXEC_ID, PEXEC_ID, "wf-db", "wf-1", "bucket",
                output_files=[], output_metadata=[], output_results=[], result_log="",
                execution_log="", log_group_arn="", log_stream_name="",
                execution_status="SUCCEEDED")
        config = tables.table(po.workflow_execution_configuration_table)
        assert config.update_item.call_args.kwargs["ExpressionAttributeValues"] == {":t": []}
        events_client.put_events.assert_not_called()


@pytest.mark.unit
class TestTheFailedPath:

    def test_the_failure_is_announced_once_and_its_output_types_are_derived(self):
        tables = _Tables()
        tables.table(heh.workflow_execution_database_v2).query.return_value = {"Items": [
            {"executionStatus": "RUNNING", "executionStartDate": base.STARTED,
             "executionGroupId": "grp-f"}]}
        for name in (heh.pipeline_execution_output_files_table,
                     heh.pipeline_execution_output_metadata_table):
            tables.table(name).query.return_value = {"Items": []}
        tables.table(heh.pipeline_execution_output_results_table).query.return_value = {
            "Items": [{"pipelineExecutionId": PEXEC_ID}]}
        events_client = MagicMock()
        with patch.object(heh, "events_client", events_client), \
                patch.object(heh, "orchestration_bus_arn", base.BUS), \
                patch.object(heh, "orchestration_event_source_prefix", base.PREFIX), \
                patch.object(heh, "dynamodb", tables.resource), \
                patch.object(heh, "_fetch_execution_log", return_value=""), \
                patch.object(heh, "_get_pipeline_rows", return_value=[
                    {"pipelineExecutionId": PEXEC_ID, "executionStatus": "RUNNING"}]), \
                patch.object(heh.eo, "mark_inflight_pipelines_terminal", return_value=[]), \
                patch.object(heh.eo, "finalize_main_row", return_value=True):
            heh.reconcile_failed_execution(
                {"workflowExecutionId": EXEC_ID, "workflowDatabaseId": "wf-db",
                 "workflowId": "wf-1"},
                {"Error": "States.TaskFailed", "Cause": "boom"})

        detail = parse(_detail(events_client), model=WorkflowExecutionCompletedDetailModel)
        assert (detail.executionId, detail.status, detail.executionGroupId) == (
            EXEC_ID, "FAILED", "grp-f")
        config = tables.table(heh.workflow_execution_configuration_table)
        config.update_item.assert_called_once()
        assert config.update_item.call_args.kwargs["ExpressionAttributeValues"] == {
            ":t": ["Result"]}


@pytest.mark.unit
class TestTheAbortedPath:

    def test_the_abort_is_announced_once_and_leaves_the_output_types_alone(self):
        main_item = {"workflowExecutionId": EXEC_ID, "workflowDatabaseId:workflowId": "wf-db:wf-1",
                     "workflowDatabaseId": "wf-db", "workflowId": "wf-1",
                     "workflow_execution_arn": "arn:ex", "executionStatus": "RUNNING",
                     "executionStartDate": base.STARTED, "executionStopDate": "",
                     "executionGroupId": "grp-a"}
        events_client = MagicMock()
        with patch.object(le, "get_execution_main_row", return_value=main_item), \
                patch.object(le, "authorize_abort", return_value=(True, "")), \
                patch.object(le, "get_pipeline_execution_rows", return_value=[]), \
                patch.object(le, "_stop_sfn_execution"), \
                patch.object(le, "_persist_reconciled_main_row", return_value=True), \
                patch.object(le, "log_actions"), \
                patch.object(le.dynamodb, "Table"), \
                patch.object(le.eo, "set_pipeline_status"), \
                patch.object(le.er, "update_execution_output_types") as record_output_types, \
                patch.object(le, "events_client", events_client), \
                patch.object(le, "orchestration_bus_arn", base.BUS), \
                patch.object(le, "orchestration_event_source_prefix", base.PREFIX):
            response = le.abort_execution({}, EXEC_ID)

        assert response["statusCode"] == 200
        detail = parse(_detail(events_client), model=WorkflowExecutionCompletedDetailModel)
        assert (detail.executionId, detail.status, detail.executionGroupId) == (
            EXEC_ID, "ABORTED", "grp-a")
        record_output_types.assert_not_called()
