# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Workflow, trigger and execution reads record the request; the import, the output write-back and
the end of an execution record what they did, for the user the execution ran for."""

import json
import os
import sys
import types
from unittest.mock import MagicMock

import pytest

# Names the system handlers read at import time.
for _k, _v in {
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "t-buckets", "METADATA_SERVICE_LAMBDA_FUNCTION_NAME": "t-md-svc",
    "FILE_UPLOAD_LAMBDA_FUNCTION_NAME": "t-upload", "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "ASSET_UPLOAD_TABLE_NAME": "t-asset-upload", "DATABASE_STORAGE_TABLE_NAME": "t-db",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2", "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "WORKFLOW_EXECUTION_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:vams-wf:*",
    "PIPELINE_SERVICE_V2_FUNCTION_NAME": "t-pipe-v2", "PIPELINE_TEMPLATE_SERVICE_FUNCTION_NAME": "t-tpl",
    "WORKFLOW_SERVICE_V2_FUNCTION_NAME": "t-wf-v2", "WORKFLOW_TRIGGER_SERVICE_FUNCTION_NAME": "t-trig",
    "LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET": "t-artefacts",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
    "PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME": "t-pin-files",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "WORKFLOW_STORAGE_TABLE_NAME": "t-workflows", "PIPELINE_STORAGE_TABLE_NAME": "t-pipelines",
    "EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME": "t-execv2",
}.items():
    os.environ.setdefault(_k, _v)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

import backend.backend.handlers.workflows.executionService as execution_svc  # noqa: E402
import backend.backend.handlers.workflows.importGlobalPipelineWorkflow as import_svc  # noqa: E402
import backend.backend.handlers.workflows.sfn.handleExecutionError as error_svc  # noqa: E402
import backend.backend.handlers.workflows.sfn.processWorkflowExecutionOutput as output_svc  # noqa: E402
import backend.backend.handlers.workflows.workflowService as workflow_svc  # noqa: E402
import backend.backend.handlers.workflows.workflowTriggerService as trigger_svc  # noqa: E402

CLAIMS = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": False}


def _event(method, path, path_params=None, query=None):
    return {"requestContext": {"http": {"method": method, "path": path}}, "pathParameters": path_params,
            "queryStringParameters": query, "body": None}


def _wire(monkeypatch, module, api=True):
    recorder = MagicMock()
    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = api
    enforcer.return_value.enforce.return_value = True
    monkeypatch.setattr(module, "log_action", recorder)
    monkeypatch.setattr(module, "request_to_claims", lambda event: CLAIMS)
    monkeypatch.setattr(module, "CasbinEnforcer", enforcer)
    return recorder


@pytest.mark.unit
class TestWorkflowReadRecords:
    def test_a_database_list_records_the_request_and_the_count(self, monkeypatch):
        recorder = _wire(monkeypatch, workflow_svc)
        monkeypatch.setattr(workflow_svc, "get_database_workflows",
                            lambda *args: workflow_svc.GetWorkflowsResponseModel(Items=[]))
        event = _event("GET", "/database/db1/workflows", {"databaseId": "db1"}, {})
        assert workflow_svc.lambda_handler(event, MagicMock())["statusCode"] == 200
        assert recorder.call_args.args == (event, "workflowList")
        assert recorder.call_args.kwargs["item_count"] == 0

    def test_a_denied_list_records_nothing(self, monkeypatch):
        recorder = _wire(monkeypatch, workflow_svc, api=False)
        assert workflow_svc.lambda_handler(_event("GET", "/workflows", {}, {}), MagicMock())["statusCode"] == 403
        recorder.assert_not_called()

    def test_a_trigger_list_records_the_request(self, monkeypatch):
        recorder = _wire(monkeypatch, trigger_svc)
        monkeypatch.setattr(trigger_svc, "_validate_ids", lambda params: None)
        monkeypatch.setattr(trigger_svc, "_enforce_parent_workflow",
                            lambda *args: (True, {"databaseId": "db1", "workflowId": "wf-01"}))
        monkeypatch.setattr(trigger_svc, "list_triggers",
                            lambda *args: trigger_svc.GetTriggersResponseModel(Items=[]))
        event = _event("GET", "/database/db1/workflows/wf-01/triggers", {"databaseId": "db1", "workflowId": "wf-01"},
                       {})
        response = trigger_svc.lambda_handler(event, MagicMock())
        assert response["statusCode"] == 200, response
        assert recorder.call_args.args == (event, "workflowTriggerList")
        assert recorder.call_args.kwargs["workflowId"] == "wf-01"


@pytest.mark.unit
class TestExecutionReadRecord:
    def test_a_successful_read_records_the_path_and_query(self, monkeypatch):
        recorder = MagicMock()
        monkeypatch.setattr(execution_svc, "log_action", recorder)
        event = {"pathParameters": {"executionId": "exec-01"}, "queryStringParameters": {"pageSize": "5"}}
        response = {"statusCode": 200, "body": json.dumps({"message": {"secret": "returned-content"}})}
        assert execution_svc._log_execution_read(event, "workflowExecutionResultsGet", response) is response
        recorder.assert_called_once()
        assert recorder.call_args.args == (event, "workflowExecutionResultsGet")
        assert recorder.call_args.kwargs["executionId"] == "exec-01"
        assert recorder.call_args.kwargs["request_parameters"] == {"pageSize": "5"}
        assert "returned-content" not in json.dumps(recorder.call_args.kwargs, default=str)

    @pytest.mark.parametrize("status", [403, 404, 500])
    def test_a_failed_read_records_nothing(self, monkeypatch, status):
        recorder = MagicMock()
        monkeypatch.setattr(execution_svc, "log_action", recorder)
        execution_svc._log_execution_read({}, "workflowExecutionGet", {"statusCode": status})
        recorder.assert_not_called()


def _allow_execution(monkeypatch, input_assets, config_row=None):
    """Run the real access rule over an execution whose reads are stubbed: every check allows."""
    enforcer = MagicMock()
    enforcer.return_value.enforce.return_value = True
    monkeypatch.setattr(execution_svc, "claims_and_roles", CLAIMS, raising=False)
    monkeypatch.setattr(execution_svc, "CasbinEnforcer", enforcer)
    monkeypatch.setattr(execution_svc, "_authz_decision_cache", {})
    monkeypatch.setattr(execution_svc, "_held_execution_entities", {})
    monkeypatch.setattr(execution_svc, "get_execution_input_assets", lambda execution_id: list(input_assets))
    monkeypatch.setattr(execution_svc, "prewarm_asset_details", lambda pairs: None)
    monkeypatch.setattr(execution_svc, "_get_asset_details_cached",
                        lambda database_id, asset_id: {"databaseId": database_id, "assetId": asset_id})
    main_item = {"workflowDatabaseId": "db1", "workflowId": "wf-01"}
    return execution_svc.authorize_execution_access("exec-01", main_item, "GET", config_row=config_row or {})


@pytest.mark.unit
class TestExecutionReadIds:
    """An execution read names the database (and asset) its access check already resolved."""

    def _read(self, monkeypatch):
        recorder = MagicMock()
        monkeypatch.setattr(execution_svc, "log_action", recorder)
        event = {"pathParameters": {"executionId": "exec-01"}, "queryStringParameters": {}}
        execution_svc._log_execution_read(event, "workflowExecutionGet", {"statusCode": 200})
        recorder.assert_called_once()
        return recorder.call_args.kwargs

    def test_a_run_over_one_asset_names_its_database_and_asset(self, monkeypatch):
        assert _allow_execution(monkeypatch, [("db1", "asset-01")]) == (True, "")
        kwargs = self._read(monkeypatch)
        assert kwargs["database_id"] == "db1"
        assert kwargs["asset_id"] == "asset-01"

    def test_a_run_over_assets_of_one_database_names_the_database(self, monkeypatch):
        _allow_execution(monkeypatch, [("db1", "asset-01")],
                         config_row={"outputDatabaseId": "db1", "outputAssetId": "asset-02"})
        kwargs = self._read(monkeypatch)
        assert kwargs["database_id"] == "db1"
        assert kwargs["asset_id"] is None

    def test_a_run_across_databases_names_none(self, monkeypatch):
        _allow_execution(monkeypatch, [("db1", "asset-01"), ("db2", "asset-02")])
        kwargs = self._read(monkeypatch)
        assert kwargs["database_id"] is None
        assert kwargs["asset_id"] is None

    def test_a_refused_check_holds_nothing(self, monkeypatch):
        _allow_execution(monkeypatch, [("db1", "asset-01")])
        execution_svc._held_execution_entities.clear()
        monkeypatch.setattr(execution_svc, "_get_asset_details_cached", lambda *args: None)
        enforcer = MagicMock()
        enforcer.return_value.enforce.return_value = False
        monkeypatch.setattr(execution_svc, "CasbinEnforcer", enforcer)
        allowed, _ = execution_svc.authorize_execution_access(
            "exec-01", {"workflowDatabaseId": "db1", "workflowId": "wf-01"}, "GET", config_row={})
        assert allowed is False
        assert execution_svc._held_execution_entities == {}


@pytest.mark.unit
class TestSystemRecords:
    def test_an_import_records_the_bundle_ids_and_what_it_applied(self, monkeypatch):
        recorder = MagicMock()
        monkeypatch.setattr(import_svc, "log_action", recorder)
        monkeypatch.setattr(import_svc, "register_bundle", lambda props: {
            "ids": {"pipelineDatabaseId": "GLOBAL", "pipelineId": "pipe-01", "workflowDatabaseId": "GLOBAL",
                    "workflowId": "wf-01"},
            "applied": ["pipeline 'pipe-01' created"]})
        event = {"ResourceProperties": {}}
        assert import_svc.lambda_handler(event, MagicMock())["statusCode"] == 200
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "pipelineImport")
        assert kwargs["pipelineId"] == "pipe-01" and kwargs["workflowId"] == "wf-01"
        assert kwargs["item_count"] == 1 and kwargs["details"]["requestType"] == "Register"

    @staticmethod
    def _run_error_handler(monkeypatch, main_row, input_rows, more_inputs=False):
        """Run the real error handler on the input its Step Functions state passes (the error handler
        payload of the generated state machine names only the execution and the workflow), with the
        execution tables faked."""
        recorder = MagicMock()
        monkeypatch.setattr(error_svc, "log_action", recorder)
        inputs = {"Items": input_rows}
        if more_inputs:
            inputs["LastEvaluatedKey"] = {"workflowExecutionId": "exec-01"}
        tables = {error_svc.workflow_execution_database_v2: MagicMock(query=MagicMock(
                      return_value={"Items": [main_row]})),
                  error_svc.workflow_execution_inputs_table: MagicMock(query=MagicMock(return_value=inputs))}
        monkeypatch.setattr(error_svc.dynamodb, "Table", lambda name: tables.get(name, MagicMock()))
        for name in ("_get_pipeline_rows", "_fetch_execution_log", "_refresh_execution_log",
                     "_execution_start_date"):
            monkeypatch.setattr(error_svc, name, MagicMock(return_value=[] if name == "_get_pipeline_rows" else ""))
        monkeypatch.setattr(error_svc.eo, "finalize_main_row", MagicMock())
        monkeypatch.setattr(error_svc.el, "release_locks_for_execution", MagicMock())
        event = {"body": {"workflowExecutionId": "exec-01", "workflowDatabaseId": "db1", "workflowId": "wf-01"},
                 "errorInfo": {"Error": "States.TaskFailed", "Cause": "{}"}}
        assert error_svc.lambda_handler(event, MagicMock()) == {"handled": True}
        return recorder, event

    def test_the_error_handler_payload_names_only_the_execution_and_workflow(self):
        from backend.backend.common.workflows import workflowAslBuilder
        source = open(workflowAslBuilder.__file__).read()
        payload = source[source.index("error_handler_payload = {"):source.index("error_handler_state =")]
        assert '"workflowExecutionId.$"' in payload and '"workflowId.$"' in payload
        assert "executingUserName" not in payload and "assetId" not in payload, (
            "the failed-end record must not rely on fields the error handler does not receive")

    def test_a_failed_run_records_its_user_duration_and_asset(self, monkeypatch):
        recorder, event = self._run_error_handler(
            monkeypatch, {"executionStatus": "RUNNING", "triggeredByUserId": "alice",
                          "executionStartDate": "2020-01-01T00:00:00Z"},
            [{"databaseId": "db1", "assetId": "asset-01"}, {"databaseId": "db1", "assetId": "asset-01"}])
        recorder.assert_called_once()
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "workflowExecutionEnd")
        assert kwargs["outcome"] == "failed" and kwargs["on_behalf_of"] == "alice"
        assert kwargs["duration_ms"] > 0
        assert (kwargs["database_id"], kwargs["asset_id"]) == ("db1", "asset-01")
        assert kwargs["executionId"] == "exec-01" and kwargs["workflowId"] == "wf-01"

    def test_a_trigger_run_names_no_user(self, monkeypatch):
        recorder, _ = self._run_error_handler(
            monkeypatch, {"executionStatus": "RUNNING", "triggeredByUserId": "SYSTEM_USER",
                          "executionStartDate": "2020-01-01T00:00:00Z"}, [])
        assert recorder.call_args.kwargs["on_behalf_of"] is None

    @pytest.mark.parametrize("rows, more", [
        ([{"databaseId": "db1", "assetId": "a1"}, {"databaseId": "db1", "assetId": "a2"}], False),
        ([{"databaseId": "db1", "assetId": "a1"}], True)])
    def test_a_run_over_several_assets_names_none(self, monkeypatch, rows, more):
        recorder, _ = self._run_error_handler(
            monkeypatch, {"executionStatus": "RUNNING", "triggeredByUserId": "alice",
                          "executionStartDate": "2020-01-01T00:00:00Z"}, rows, more_inputs=more)
        kwargs = recorder.call_args.kwargs
        assert kwargs["database_id"] is None and kwargs["asset_id"] is None

    def test_the_execution_end_record_carries_outcome_and_duration(self, monkeypatch):
        recorder = MagicMock()
        monkeypatch.setattr(output_svc, "log_action", recorder)
        body = {"databaseId": "db1", "assetId": "asset-01", "workflowId": "wf-01", "workflowExecutionId": "exec-01",
                "executingUserName": "alice", "workflowDatabaseId": "db1", "endStatePipelineExecutionId": "p-1"}
        output_svc._log_execution_end({"body": body}, body, "FAILED", "2020-01-01T00:00:00Z")
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args[1] == "workflowExecutionEnd"
        assert kwargs["outcome"] == "failed" and kwargs["reason"] == "internal"
        assert kwargs["duration_ms"] > 0 and kwargs["on_behalf_of"] == "alice"
        assert kwargs["details"]["pipelineExecutionId"] == "p-1"

    @pytest.mark.parametrize("value", ["", None, "not-a-date"])
    def test_an_unreadable_start_date_gives_no_duration(self, value):
        assert output_svc._duration_ms(value) is None
