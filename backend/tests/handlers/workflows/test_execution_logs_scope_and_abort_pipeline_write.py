# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for two executionService write/read scoping rules: a registered pipeline log group is
read scoped to the requesting execution, and abort marks a still-running pipeline row with a
targeted, condition-guarded update rather than a whole-item write.
"""

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

from backend.backend.handlers.workflows import executionService as le

MOD = "backend.backend.handlers.workflows.executionService"
EXEC_ID = "e1000000000000000000000000000001"


@pytest.mark.unit
class TestRegisteredLogScoping:
    """A registered log group can be shared by every execution of the same pipeline, so the read is
    filtered to the execution (and step) that asked for it."""

    def test_registered_log_group_is_read_scoped_to_this_execution(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db",
                "executionLogGroupArn": "arn:aws:logs:us-west-2:1:log-group:/g:*"}
        prow = {"pipelineExecutionId": "pe-1", "registeredSubExecutions": [],
                "registeredLogs": [{"logGroupArn": "arn:aws:logs:us-west-2:1:log-group:/shared:*"}]}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
             patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._sfn_execution_history_events",
                   return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._fetch_registered_log_events", return_value=(True, [], None)) as fetch:
            resp = le.get_execution_logs(
                {}, EXEC_ID, {"mode": "full", "pipelineExecutionId": "pe-1"})
        assert resp["statusCode"] == 200
        call = next(c for c in fetch.call_args_list if "/shared" in c.args[0])
        assert call.kwargs.get("scope_terms") == [EXEC_ID, "pe-1"]

    def test_the_step_invocation_log_is_read_scoped_to_the_execution_alone(self):
        # The invoked function logs the invoke body: the workflow execution id is in it, the pipeline
        # execution id is not, so an AND of both read every plain Lambda step's own log as empty.
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db",
                "executionLogGroupArn": "arn:aws:logs:us-west-2:1:log-group:/g:*"}
        prow = {"pipelineExecutionId": "pe-1", "registeredSubExecutions": [], "registeredLogs": [],
                "pipelineExecutionType": "Lambda",
                "pipelineResourceArn": "arn:aws:lambda:us-west-2:123456789012:function:vams-vamsExecuteY"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
             patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._sfn_execution_history_events",
                   return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._fetch_registered_log_events", return_value=(True, [], None)) as fetch:
            resp = le.get_execution_logs(
                {}, EXEC_ID, {"mode": "full", "pipelineExecutionId": "pe-1"})
        assert resp["statusCode"] == 200
        call = next(c for c in fetch.call_args_list if "/aws/lambda/vams-vamsExecuteY" in c.args[0])
        assert call.kwargs.get("scope_terms") == [EXEC_ID]

    def test_registered_log_stream_prefix_is_kept_alongside_the_scope(self):
        # A reported stream prefix still narrows streams; the scope terms narrow the events within.
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db",
                "executionLogGroupArn": "arn:aws:logs:us-west-2:1:log-group:/g:*"}
        prow = {"pipelineExecutionId": "pe-1", "registeredSubExecutions": [],
                "registeredLogs": [{"logGroupArn": "arn:aws:logs:us-west-2:1:log-group:/batch:*",
                                    "logStreamPrefix": "job/family"}]}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
             patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._sfn_execution_history_events",
                   return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._fetch_registered_log_events", return_value=(True, [], None)) as fetch:
            le.get_execution_logs({}, EXEC_ID, {"mode": "full", "pipelineExecutionId": "pe-1"})
        call = next(c for c in fetch.call_args_list if "/batch" in c.args[0])
        assert call.kwargs.get("log_stream_prefix") == "job/family"
        assert call.kwargs.get("scope_terms") == [EXEC_ID, "pe-1"]

    def _run(self, prow, summaries=None):
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowId": "wf", "workflowDatabaseId": "db",
                "executionLogGroupArn": "arn:aws:logs:us-west-2:1:log-group:/g:*"}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
             patch(f"{MOD}.authorize_execution_access", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[prow]), \
             patch(f"{MOD}._full_log_search", return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._sfn_execution_history_events",
                   return_value={"events": [], "nextToken": None}), \
             patch(f"{MOD}._batch_stream_summaries", return_value=summaries or []), \
             patch(f"{MOD}._log_search_window_start", return_value=1_700_000_000_000), \
             patch(f"{MOD}._fetch_registered_log_events", return_value=(True, [], None)) as fetch:
            resp = le.get_execution_logs({}, EXEC_ID, {"mode": "full", "pipelineExecutionId": "pe-1"})
        return resp, fetch

    def _batch_log(self, stream="", prefix="thumb-jd/default/", stage="Preview3dThumbnailBatchJob"):
        return {"logGroupArn": "arn:aws:logs:us-west-2:1:log-group:/aws/batch/job",
                "logGroupName": "/aws/batch/job", "logStreamName": stream, "logStreamPrefix": prefix,
                "stageName": stage, "label": "", "sourceType": "batch"}

    def test_an_exact_stream_the_pipeline_registered_is_read_without_scope_terms(self):
        prow = {"pipelineExecutionId": "pe-1", "registeredSubExecutions": [],
                "registeredLogs": [self._batch_log(stream="thumb-jd/default/task-1")]}
        resp, fetch = self._run(prow)
        call = next(c for c in fetch.call_args_list if "/aws/batch/job" in c.args[0])
        assert call.args[1] == "thumb-jd/default/task-1"
        assert not call.kwargs.get("scope_terms")
        # Dropping the scope terms must not drop the time window: the read is still bounded below.
        assert call.kwargs["default_start_time"] == 1_700_000_000_000
        body = json.loads(resp["body"])["message"]
        assert body["logSources"][0]["status"] == "empty"

    def test_a_batch_prefix_with_a_resolved_stream_under_it_is_read_exact_and_unscoped(self):
        prow = {"pipelineExecutionId": "pe-1", "registeredSubExecutions": [],
                "registeredLogs": [self._batch_log()]}
        summaries = [{"resourceType": "stepFunctionsExecution", "stageName": "", "stages": [
            {"stageName": "Preview3dThumbnailBatchJob",
             "batch": {"jobId": "j", "logStreamName": "thumb-jd/default/task-9"}}]}]
        _resp, fetch = self._run(prow, summaries)
        call = next(c for c in fetch.call_args_list if "/aws/batch/job" in c.args[0])
        assert call.args[1] == "thumb-jd/default/task-9"
        assert call.kwargs.get("log_stream_prefix") == ""
        assert not call.kwargs.get("scope_terms")

    def test_a_resolved_stream_outside_every_registered_prefix_keeps_the_scope_terms(self):
        prow = {"pipelineExecutionId": "pe-1", "registeredSubExecutions": [],
                "registeredLogs": [self._batch_log()]}
        summaries = [{"resourceType": "stepFunctionsExecution", "stageName": "", "stages": [
            {"stageName": "Preview3dThumbnailBatchJob",
             "batch": {"jobId": "j", "logStreamName": "elsewhere/default/task-9"}}]}]
        _resp, fetch = self._run(prow, summaries)
        call = next(c for c in fetch.call_args_list if "/aws/batch/job" in c.args[0])
        assert call.args[1] == "elsewhere/default/task-9"
        assert call.kwargs.get("scope_terms") == [EXEC_ID, "pe-1"]

    def test_a_batch_prefix_with_no_resolvable_stream_keeps_the_terms_and_is_reported_unscoped(self):
        prow = {"pipelineExecutionId": "pe-1", "registeredSubExecutions": [],
                "registeredLogs": [self._batch_log()]}
        resp, fetch = self._run(prow)
        call = next(c for c in fetch.call_args_list if "/aws/batch/job" in c.args[0])
        assert call.args[1] == "" and call.kwargs.get("log_stream_prefix") == "thumb-jd/default/"
        assert call.kwargs.get("scope_terms") == [EXEC_ID, "pe-1"]
        body = json.loads(resp["body"])["message"]
        assert body["logSources"][0]["status"] == "unscoped"


@pytest.mark.unit
class TestAbortPipelineRowWrite:
    """Abort touches only the status/stop-date attributes of a still-running pipeline row, so a
    registration or output the pipeline writes during the read-to-write window survives."""

    def _main_row(self):
        return {"workflowExecutionId": EXEC_ID, "workflowId": "wfx", "workflowDatabaseId": "dbx",
                "workflow_execution_arn": "arn:ex:main", "executionStatus": "RUNNING",
                "executionStopDate": ""}

    def _run_abort(self, pipeline_rows):
        pexec_table = MagicMock()
        main_table = MagicMock()

        def _table(name):
            return pexec_table if name == le.pipeline_executions_table else main_table

        with patch(f"{MOD}.get_execution_main_row", return_value=self._main_row()), \
             patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=pipeline_rows), \
             patch(f"{MOD}._abort_registered_sub_process", return_value=""), \
             patch(f"{MOD}._stop_sfn_execution"), \
             patch(f"{MOD}._persist_reconciled_main_row"), \
             patch(f"{MOD}.log_actions"), \
             patch.object(le.dynamodb, "Table", side_effect=_table):
            resp = le.abort_execution({}, EXEC_ID)
        return resp, pexec_table

    def test_running_row_updated_not_put(self):
        running = {"pipelineExecutionId": "P1", "workflowExecutionId": EXEC_ID,
                   "executionStatus": "RUNNING", "registeredSubExecutions": [],
                   "registeredLogs": [{"logGroupArn": "arn:lg:1"}], "executionStopDate": ""}
        resp, pexec_table = self._run_abort([running])
        assert resp["statusCode"] == 200
        pexec_table.put_item.assert_not_called()
        update = pexec_table.update_item.call_args_list[-1].kwargs
        assert update["Key"] == {"pipelineExecutionId": "P1", "workflowExecutionId": EXEC_ID}
        # Only status and stop date are written; nothing else on the row is replaced.
        assert "executionStatus" in update["UpdateExpression"]
        assert "executionStopDate" in update["UpdateExpression"]
        assert "registeredLogs" not in update["UpdateExpression"]
        assert update["ExpressionAttributeValues"][":st"] == "ABORTED"
        assert update["ExpressionAttributeValues"][":s"]
        # Guarded so a concurrent writer that already finished the row is not regressed.
        assert "executionStatus" in update["ConditionExpression"]

    def test_terminal_row_is_left_untouched(self):
        done = {"pipelineExecutionId": "P2", "workflowExecutionId": EXEC_ID,
                "executionStatus": "SUCCEEDED", "registeredSubExecutions": [],
                "executionStopDate": "d"}
        resp, pexec_table = self._run_abort([done])
        assert resp["statusCode"] == 200
        pexec_table.put_item.assert_not_called()
        pexec_table.update_item.assert_not_called()
