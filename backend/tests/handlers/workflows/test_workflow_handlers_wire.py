# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end handler tests for executeWorkflow, executionService, createWorkflow,
and workflowService after the gold-standard refactor. These lock the API wire
contract (response body shapes, status codes, and validation messages) so the
refactor does not change I/O.
"""

import os
import sys
import types
import json
import pytest
from unittest.mock import MagicMock, patch

# Env vars both handlers read at import time.
for k, v in {
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "t-buckets",
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "PIPELINE_STORAGE_TABLE_NAME": "t-pipelines",
    "WORKFLOW_STORAGE_TABLE_NAME": "t-workflows",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME": "t-pin-files",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
    # Output/log table names are SHARED with processWorkflowExecutionOutput's tests; use the
    # same values so whichever handler imports first, both see consistent env (these are set
    # process-wide via setdefault and read into module globals at import time).
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "S3_ASSETAUXILIARY_STORAGE_BUCKET": "t-aux",
    "METADATA_SERVICE_LAMBDA_FUNCTION_NAME": "t-md-svc",
    # createWorkflow additionally reads these at import time.
    "VAMS_STACK_NAME": "t-stack",
    "PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME": "t-po",
    "AWS_REGION": "us-east-1",
    "LAMBDA_ROLE_ARN": "arn:aws:iam::123456789012:role/t-role",
    "LOG_GROUP_ARN": "arn:aws:logs:us-east-1:123456789012:log-group:t",
}.items():
    os.environ.setdefault(k, v)

# handlers.workflows package __init__ + createWorkflow import common.stepfunctions_builder
# at import time; provide a lightweight stub (these tests do not exercise ASL generation).
if "common.stepfunctions_builder" not in sys.modules:
    _sf = types.ModuleType("common.stepfunctions_builder")
    for _name in (
        "create_lambda_task_state", "create_fail_state", "create_retry_config",
        "create_catch_config", "create_workflow_definition", "create_state_machine",
        "update_state_machine", "get_task_builder",
    ):
        setattr(_sf, _name, MagicMock())
    sys.modules["common.stepfunctions_builder"] = _sf

from backend.backend.handlers.workflows import executeWorkflow as ew
from backend.backend.handlers.workflows import executionService as le
from backend.backend.handlers.workflows import createWorkflow as cw
from backend.backend.handlers.workflows import workflowService as ws


def _event(method, path_params=None, body=None, query=None):
    event = {
        "requestContext": {"http": {"method": method, "path": "/x"}, "authorizer": {}},
        "pathParameters": path_params or {},
        "queryStringParameters": query if query is not None else {},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def _body(resp):
    return json.loads(resp["body"])


# ============================ executeWorkflow ============================

@pytest.mark.unit
class TestExecuteWorkflowHandler:
    def _claims(self):
        return {"tokens": ["user@x"], "roles": [], "mfaEnabled": False}

    def test_method_not_allowed(self):
        with patch.object(ew, "request_to_claims", return_value=self._claims()), \
             patch.object(ew, "CasbinEnforcer") as MockEnf:
            MockEnf.return_value.enforceAPI.return_value = True
            resp = ew.lambda_handler(_event("GET", {"databaseId": "dbx", "assetId": "a1", "workflowId": "wfx"}), MagicMock())
        assert resp["statusCode"] == 400
        assert _body(resp)["message"] == "Method not allowed"

    def test_api_unauthorized(self):
        with patch.object(ew, "request_to_claims", return_value={"tokens": []}):
            resp = ew.lambda_handler(_event("POST", {"databaseId": "dbx", "assetId": "a1", "workflowId": "wfx"}), MagicMock())
        assert resp["statusCode"] == 403

    def test_missing_path_param(self):
        with patch.object(ew, "request_to_claims", return_value=self._claims()), \
             patch.object(ew, "CasbinEnforcer") as MockEnf:
            MockEnf.return_value.enforceAPI.return_value = True
            # Missing workflowId path param.
            resp = ew.lambda_handler(_event("POST", {"databaseId": "dbx", "assetId": "a1"}, body={"workflowDatabaseId": "dbx"}), MagicMock())
        assert resp["statusCode"] == 400
        assert "Missing path parameter" in _body(resp)["message"] and "workflowId" in _body(resp)["message"]

    def test_missing_workflow_database_id_message_preserved(self):
        # The request model's @root_validator emits the original required-field message
        # when workflowDatabaseId is absent. (Exercised at the model level so the real
        # validate() dispatcher message is used rather than the test's permissive mock,
        # and surfaced through the handler's _clean_validation_message extraction.)
        from aws_lambda_powertools.utilities.parser import parse, ValidationError
        from backend.backend.models.workflows import ExecuteWorkflowRequestModel
        with patch("models.workflows.validate",
                   return_value=(False, "workflowDatabaseId is a required field.")):
            with pytest.raises(ValidationError) as ei:
                parse({}, model=ExecuteWorkflowRequestModel)
        assert ew._clean_validation_message(ei.value) == "workflowDatabaseId is a required field."

    def test_workflow_database_mismatch_message_preserved(self):
        with patch.object(ew, "request_to_claims", return_value=self._claims()), \
             patch.object(ew, "CasbinEnforcer") as MockEnf:
            MockEnf.return_value.enforceAPI.return_value = True
            # workflowDatabaseId is a valid ID but neither GLOBAL nor the asset's db.
            resp = ew.lambda_handler(
                _event("POST", {"databaseId": "dbAxx", "assetId": "a1", "workflowId": "wfx"},
                       body={"workflowDatabaseId": "dbBxx"}), MagicMock())
        assert resp["statusCode"] == 400
        assert _body(resp)["message"] == (
            "Workflow can only be executed on assets from the same database or from global workflows")

    def test_happy_path_returns_execution_id(self):
        # Authorize everything, stub the data layer + launch, assert {"message": execId}.
        with patch.object(ew, "request_to_claims", return_value=self._claims()), \
             patch.object(ew, "CasbinEnforcer") as MockEnf, \
             patch.object(ew, "get_asset", return_value=[{"assetId": "a1", "bucketId": "b1",
                          "assetLocation": {"Key": "a1/"}, "assetName": "n", "tags": []}]), \
             patch.object(ew, "get_workflow", return_value=[{"workflowId": "wfx", "databaseId": "dbx",
                          "workflow_arn": "arn:sm", "specifiedPipelines": {"functions": [{"name": "p1"}]}}]), \
             patch.object(ew, "validate_pipelines", return_value=(True, "")), \
             patch.object(ew, "get_default_bucket_details", return_value={"bucketName": "bkt", "baseAssetsPrefix": "", "bucketId": "b1"}), \
             patch.object(ew, "get_workflow_executions", return_value={"Items": []}), \
             patch.object(ew, "build_pipeline_input_metadata", return_value={"VAMS": {}}), \
             patch.object(ew, "launchWorkflow", return_value="EXEC123") as mock_launch:
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = ew.lambda_handler(
                _event("POST", {"databaseId": "dbx", "assetId": "a1", "workflowId": "wfx"},
                       body={"workflowDatabaseId": "dbx", "fileKey": "/folder/x.glb"}), MagicMock())
        assert resp["statusCode"] == 200
        assert _body(resp)["message"] == "EXEC123"
        mock_launch.assert_called_once()

    def test_duplicate_running_execution_blocked(self):
        with patch.object(ew, "request_to_claims", return_value=self._claims()), \
             patch.object(ew, "CasbinEnforcer") as MockEnf, \
             patch.object(ew, "get_asset", return_value=[{"assetId": "a1", "bucketId": "b1",
                          "assetLocation": {"Key": "a1/"}, "assetName": "n", "tags": []}]), \
             patch.object(ew, "get_workflow", return_value=[{"workflowId": "wfx", "databaseId": "dbx",
                          "workflow_arn": "arn:sm", "specifiedPipelines": {"functions": [{"name": "p1"}]}}]), \
             patch.object(ew, "validate_pipelines", return_value=(True, "")), \
             patch.object(ew, "get_default_bucket_details", return_value={"bucketName": "bkt", "baseAssetsPrefix": "", "bucketId": "b1"}), \
             patch.object(ew, "get_workflow_executions", return_value={"Items": [{"executionId": "running"}]}):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = ew.lambda_handler(
                _event("POST", {"databaseId": "dbx", "assetId": "a1", "workflowId": "wfx"},
                       body={"workflowDatabaseId": "dbx"}), MagicMock())
        assert resp["statusCode"] == 400
        assert _body(resp)["message"] == "Workflow has a currently running execution on this file"


# ============================ executionService (list) ============================

@pytest.mark.unit
class TestListExecutionsHandler:
    def _claims(self):
        return {"tokens": ["user@x"], "roles": [], "mfaEnabled": False}

    def test_method_not_allowed(self):
        with patch.object(le, "request_to_claims", return_value=self._claims()):
            resp = le.lambda_handler(_event("POST", {"databaseId": "dbx", "assetId": "a1"}), MagicMock())
        assert resp["statusCode"] == 400
        assert _body(resp)["message"] == "Method not allowed"

    def test_happy_path_wire_shape(self):
        # Authorize, stub the listing to return one execution; assert {"message": {"Items":[...]}}.
        items = [{
            "workflowDatabaseId": "wdb", "workflowId": "wfx", "executionId": "E1",
            "executionStatus": "SUCCEEDED", "startDate": "2026-06-16T00:00:00Z",
            "stopDate": "2026-06-16T00:05:00Z", "inputAssetFileKey": "/x.glb",
            "databaseId": "dbx", "assetId": "a1", "executionError": "", "executionLog": "",
        }]
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_asset_details", return_value={"assetId": "a1", "databaseId": "dbx"}), \
             patch.object(le, "build_execution_items", return_value=items), \
             patch.object(le, "validate_pagination_info", return_value=None), \
             patch.object(le.dynamodb, "Table", return_value=MagicMock(query=MagicMock(return_value={"Items": []}))):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = le.lambda_handler(_event("GET", {"databaseId": "dbx", "assetId": "a1", "workflowId": "wfx"}), MagicMock())
        assert resp["statusCode"] == 200
        msg = _body(resp)["message"]
        assert msg["Items"] == items

    def test_asset_not_found_404(self):
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_asset_details", return_value=None), \
             patch.object(le, "validate_pagination_info", return_value=None):
            MockEnf.return_value.enforceAPI.return_value = True
            resp = le.lambda_handler(_event("GET", {"databaseId": "dbx", "assetId": "a1", "workflowId": "wfx"}), MagicMock())
        assert resp["statusCode"] == 404
        assert _body(resp)["message"] == "Asset not found"

    def test_object_level_not_authorized_403(self):
        # API allowed, but the asset-level Tier-2 enforce denies -> 403 "Not Authorized".
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_asset_details", return_value={"assetId": "a1", "databaseId": "dbx"}), \
             patch.object(le, "validate_pagination_info", return_value=None):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = False
            resp = le.lambda_handler(_event("GET", {"databaseId": "dbx", "assetId": "a1", "workflowId": "wfx"}), MagicMock())
        assert resp["statusCode"] == 403
        assert _body(resp)["message"] == "Not Authorized"


# ===================== executionService (abort) =====================

@pytest.mark.unit
class TestAbortExecutionHandler:
    def _claims(self):
        return {"tokens": ["user@x"], "roles": [], "mfaEnabled": False}

    def _main_row(self, status="RUNNING"):
        return {
            "executionId": "EabcId", "workflowId": "wfx", "workflowDatabaseId": "dbx",
            "workflow_execution_arn": "arn:ex:main", "executionStatus": status,
            "executionStopDate": "",
        }

    def test_method_not_allowed(self):
        with patch.object(le, "request_to_claims", return_value=self._claims()):
            resp = le.lambda_handler(_event("PUT", {"executionId": "EabcId"}), MagicMock())
        assert resp["statusCode"] == 400
        assert _body(resp)["message"] == "Method not allowed"

    def test_missing_execution_id(self):
        with patch.object(le, "request_to_claims", return_value=self._claims()):
            resp = le.lambda_handler(_event("DELETE", {}), MagicMock())
        assert resp["statusCode"] == 400
        assert "executionId" in _body(resp)["message"]

    def test_api_unauthorized(self):
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf:
            MockEnf.return_value.enforceAPI.return_value = False
            resp = le.lambda_handler(_event("DELETE", {"executionId": "EabcId"}), MagicMock())
        assert resp["statusCode"] == 403

    def test_execution_not_found_404(self):
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=None):
            MockEnf.return_value.enforceAPI.return_value = True
            resp = le.lambda_handler(_event("DELETE", {"executionId": "EabcId"}), MagicMock())
        assert resp["statusCode"] == 404
        assert _body(resp)["message"] == "Execution not found"

    def test_workflow_get_denied_403(self):
        # API allowed, but the workflow-level Tier-2 GET enforce denies -> 403.
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = False  # workflow GET denied
            resp = le.lambda_handler(_event("DELETE", {"executionId": "EabcId"}), MagicMock())
        assert resp["statusCode"] == 403

    def test_input_asset_post_denied_403(self):
        # Workflow GET allowed but an input asset POST is denied -> 403.
        def _enforce(obj, action):
            if obj.get("object__type") == "workflow":
                return True
            return False  # asset POST denied
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()), \
             patch.object(le, "get_execution_input_assets", return_value=[("dbx", "a1")]), \
             patch.object(le, "get_asset_details", return_value={"assetId": "a1", "databaseId": "dbx"}):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.side_effect = _enforce
            resp = le.lambda_handler(_event("DELETE", {"executionId": "EabcId"}), MagicMock())
        assert resp["statusCode"] == 403

    def test_abort_happy_path_stops_inner_then_outer_and_marks_aborted(self):
        # Two pipeline rows: one running with an inner sub-execution ARN, one already
        # SUCCEEDED (must be left untouched). Assert inner+outer StopExecution and that
        # the running pipeline row + main row are written ABORTED.
        running_pipe = {"pipelineExecutionId": "P1", "workflowExecutionId": "EabcId",
                        "executionStatus": "RUNNING",
                        "pipeline_execution_sub_execution_arn": "arn:ex:inner1",
                        "executionStopDate": ""}
        done_pipe = {"pipelineExecutionId": "P2", "workflowExecutionId": "EabcId",
                     "executionStatus": "SUCCEEDED",
                     "pipeline_execution_sub_execution_arn": "", "executionStopDate": "d"}
        pexec_table = MagicMock()
        main_table = MagicMock()

        def _table(name):
            # executionService.dynamodb.Table(...) is called with the env table names;
            # route pipeline-executions vs main by the configured names.
            return pexec_table if name == le.pipeline_executions_table else main_table

        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()), \
             patch.object(le, "get_execution_input_assets", return_value=[("dbx", "a1")]), \
             patch.object(le, "get_asset_details", return_value={"assetId": "a1", "databaseId": "dbx"}), \
             patch.object(le, "get_pipeline_execution_rows", return_value=[running_pipe, done_pipe]), \
             patch.object(le.dynamodb, "Table", side_effect=_table), \
             patch.object(le.sfn, "stop_execution") as mock_stop:
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = le.lambda_handler(_event("DELETE", {"executionId": "EabcId"}), MagicMock())

        assert resp["statusCode"] == 200
        assert _body(resp)["message"] == "Execution aborted"
        # Inner running sub-execution stopped AND the outer main execution stopped.
        stopped_arns = {c.kwargs.get("executionArn") for c in mock_stop.call_args_list}
        assert "arn:ex:inner1" in stopped_arns
        assert "arn:ex:main" in stopped_arns
        # The running pipeline row was written back ABORTED; the SUCCEEDED row was not.
        written = [c.kwargs["Item"] for c in pexec_table.put_item.call_args_list]
        assert any(i["pipelineExecutionId"] == "P1" and i["executionStatus"] == "ABORTED"
                   for i in written)
        assert all(i["pipelineExecutionId"] != "P2" for i in written)
        # Main row written ABORTED with a stop date.
        main_written = [c.kwargs["Item"] for c in main_table.put_item.call_args_list]
        assert main_written and main_written[-1]["executionStatus"] == "ABORTED"
        assert main_written[-1]["executionStopDate"]


# ===================== executionService (details) =====================

@pytest.mark.unit
class TestExecutionDetailsHandler:
    def _claims(self):
        return {"tokens": ["user@x"], "roles": [], "mfaEnabled": False}

    def _main_row(self):
        return {
            "executionId": "EabcId", "workflowId": "wfx", "workflowDatabaseId": "dbx",
            "workflow_execution_arn": "arn:ex:main", "executionStatus": "SUCCEEDED",
            "executionStartDate": "2026-06-16T00:00:00Z", "executionStopDate": "2026-06-16T00:05:00Z",
            "triggerType": "Manual", "triggeredByUserId": "user@x", "executionError": "",
        }

    def _event_details(self):
        ev = _event("GET", {"executionId": "EabcId"})
        ev["requestContext"]["http"]["path"] = "/workflows/executions/EabcId/details"
        return ev

    def test_details_routed_and_scrubbed(self):
        # Pipeline row carries internal fields (ARNs, S3 prefixes) that MUST NOT surface.
        prow = {
            "pipelineExecutionId": "P1", "pipelineId": "convert", "pipelineDatabaseId": "GLOBAL",
            "executionStatus": "SUCCEEDED", "executionStartDate": "s", "executionStopDate": "e",
            "endStatePipeline": "true", "pipelineExecutionType": "Lambda",
            "pipelineResourceArn": "arn:should:not:leak",
            "pipeline_execution_sub_execution_arn": "arn:inner:leak",
            "S3AssetPipelineBucket": "secret-bucket",
            "S3AssetAuxPipelineBucketPrefixTemp": "tmp/secret/",
        }
        out_file = {"pipelineExecutionId": "P1", "relativeFilePath": "/out/model.gltf",
                    "fileType": "file", "fileSize": 2048, "contentType": "model/gltf-binary",
                    "s3Bucket": "secret-bucket", "s3Key": "secret/key"}

        def _query_all(table_name, key_cond):
            if table_name == le.pipeline_execution_output_files_table:
                return [out_file]
            return []

        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()), \
             patch.object(le, "get_execution_input_assets", return_value=[("dbx", "a1")]), \
             patch.object(le, "get_asset_details", return_value={"assetId": "a1", "databaseId": "dbx"}), \
             patch.object(le, "get_pipeline_execution_rows", return_value=[prow]), \
             patch.object(le, "get_workflow_definition", return_value={"description": "wf desc"}), \
             patch.object(le, "get_pipeline_definition", return_value={"pipelineId": "convert", "description": "Converts", "pipelineType": "standardFile"}), \
             patch.object(le, "_query_all", side_effect=_query_all):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = le.lambda_handler(self._event_details(), MagicMock())

        assert resp["statusCode"] == 200
        msg = _body(resp)["message"]
        assert msg["executionId"] == "EabcId"
        assert msg["workflowDescription"] == "wf desc"
        assert msg["pipelines"][0]["name"] == "convert"
        assert msg["pipelines"][0]["description"] == "Converts"
        assert msg["pipelines"][0]["endStatePipeline"] is True
        # Output file traceability surfaces path/type/size, NOT bucket/key.
        of = msg["outputs"]["files"][0]
        assert of["relativeFilePath"] == "/out/model.gltf" and of["fileSize"] == 2048
        # No internal fields anywhere in the serialized response.
        blob = json.dumps(msg)
        for leaked in ("arn:should:not:leak", "arn:inner:leak", "secret-bucket",
                       "secret/key", "tmp/secret/", "arn:ex:main"):
            assert leaked not in blob

    def test_details_execution_not_found_404(self):
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=None):
            MockEnf.return_value.enforceAPI.return_value = True
            resp = le.lambda_handler(self._event_details(), MagicMock())
        assert resp["statusCode"] == 404
        assert _body(resp)["message"] == "Execution not found"

    def test_details_asset_get_denied_403(self):
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()), \
             patch.object(le, "get_execution_input_assets", return_value=[("dbx", "a1")]), \
             patch.object(le, "get_asset_details", return_value={"assetId": "a1", "databaseId": "dbx"}):
            MockEnf.return_value.enforceAPI.return_value = True
            # Workflow GET allowed, asset GET denied.
            MockEnf.return_value.enforce.side_effect = \
                lambda obj, act: obj.get("object__type") == "workflow"
            resp = le.lambda_handler(self._event_details(), MagicMock())
        assert resp["statusCode"] == 403


# ===================== executionService (logs) =====================

@pytest.mark.unit
class TestExecutionLogsHandler:
    def _claims(self):
        return {"tokens": ["user@x"], "roles": [], "mfaEnabled": False}

    def _main_row(self):
        return {
            "executionId": "EabcId", "workflowId": "wfx", "workflowDatabaseId": "dbx",
            "executionStatus": "RUNNING", "executionLog": "stored log text",
            "executionError": "", "executionLogGroupArn": "arn:aws:logs:us-east-1:1:log-group:vams-wf:*",
        }

    def _event_logs(self, query=None):
        ev = _event("GET", {"executionId": "EabcId"}, query=query or {})
        ev["requestContext"]["http"]["path"] = "/workflows/executions/EabcId/logs"
        return ev

    def test_logs_truncated_default_returns_stored_log(self):
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()), \
             patch.object(le, "get_execution_input_assets", return_value=[]):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = le.lambda_handler(self._event_logs(), MagicMock())
        assert resp["statusCode"] == 200
        msg = _body(resp)["message"]
        assert msg["mode"] == "truncated"
        assert msg["executionLog"] == "stored log text"

    def test_logs_invalid_mode_400(self):
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()), \
             patch.object(le, "get_execution_input_assets", return_value=[]):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = le.lambda_handler(self._event_logs(query={"mode": "bogus"}), MagicMock())
        assert resp["statusCode"] == 400

    def test_logs_full_scopes_filter_to_execution_and_pipeline(self):
        # Pipeline must belong to this execution; the live search filter pattern must
        # include BOTH the execution id and the pipeline execution id so only that
        # pipeline's events can ever be returned.
        prow = {"pipelineExecutionId": "P1", "workflowExecutionId": "EabcId"}
        captured = {}

        def _filter_log_events(**kwargs):
            captured.update(kwargs)
            return {"events": [{"timestamp": 1, "message": "m"}], "nextToken": None}

        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()), \
             patch.object(le, "get_execution_input_assets", return_value=[]), \
             patch.object(le, "get_pipeline_execution_rows", return_value=[prow]), \
             patch.object(le.logs_client, "filter_log_events", side_effect=_filter_log_events):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = le.lambda_handler(
                self._event_logs(query={"mode": "full", "pipelineExecutionId": "P1"}), MagicMock())
        assert resp["statusCode"] == 200
        msg = _body(resp)["message"]
        assert msg["mode"] == "full" and msg["events"]
        # The filter pattern is scoped to BOTH ids -> cannot leak other pipelines/executions.
        assert '"EabcId"' in captured["filterPattern"]
        assert '"P1"' in captured["filterPattern"]

    def test_logs_full_unknown_pipeline_404(self):
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()), \
             patch.object(le, "get_execution_input_assets", return_value=[]), \
             patch.object(le, "get_pipeline_execution_rows", return_value=[]):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = le.lambda_handler(
                self._event_logs(query={"mode": "full", "pipelineExecutionId": "nope"}), MagicMock())
        assert resp["statusCode"] == 404


# ============================ createWorkflow ============================

def _pipeline(database_id="dbx", name="pipeOne"):
    """A pipeline entry carrying every field the required-field check enforces."""
    return {
        "name": name,
        "databaseId": database_id,
        "pipelineType": "standardFile",
        "pipelineExecutionType": "Lambda",
        "outputType": "assetFile",
        "waitForCallback": "Disabled",
        "userProvidedResource": json.dumps({"isProvided": True, "resourceId": "arn:fn"}),
    }


def _workflow_body(database_id="dbx", workflow_id="wfxOne", pipelines=None):
    return {
        "databaseId": database_id,
        "workflowId": workflow_id,
        "description": "a valid workflow description",
        "specifiedPipelines": {"functions": pipelines or [_pipeline(database_id)]},
        "autoTriggerOnFileExtensionsUpload": "",
    }


@pytest.mark.unit
class TestCreateWorkflowHandler:
    def _claims(self):
        return {"tokens": ["user@x"], "roles": [], "mfaEnabled": False}

    def test_method_not_allowed(self):
        with patch.object(cw, "request_to_claims", return_value=self._claims()), \
             patch.object(cw, "CasbinEnforcer") as MockEnf:
            MockEnf.return_value.enforceAPI.return_value = True
            resp = cw.lambda_handler(_event("GET", body=_workflow_body()), MagicMock())
        assert resp["statusCode"] == 400
        assert _body(resp)["message"] == "Method not allowed"

    def test_api_unauthorized(self):
        with patch.object(cw, "request_to_claims", return_value={"tokens": []}):
            resp = cw.lambda_handler(_event("PUT", body=_workflow_body()), MagicMock())
        assert resp["statusCode"] == 403

    def test_missing_body_required(self):
        with patch.object(cw, "request_to_claims", return_value=self._claims()), \
             patch.object(cw, "CasbinEnforcer") as MockEnf:
            MockEnf.return_value.enforceAPI.return_value = True
            resp = cw.lambda_handler(_event("PUT"), MagicMock())
        assert resp["statusCode"] == 400
        assert _body(resp)["message"] == "Request body is required"

    def test_pipeline_missing_required_field(self):
        # userProvidedResource is Optional on the Pydantic model but required by the
        # handler's per-pipeline check. Dropping it passes model parse, then the
        # handler's required-field check rejects it (proving the check still runs).
        bad_pipeline = _pipeline()
        del bad_pipeline["userProvidedResource"]
        with patch.object(cw, "request_to_claims", return_value=self._claims()), \
             patch.object(cw, "CasbinEnforcer") as MockEnf:
            MockEnf.return_value.enforceAPI.return_value = True
            resp = cw.lambda_handler(
                _event("PUT", body=_workflow_body(pipelines=[bad_pipeline])), MagicMock())
        assert resp["statusCode"] == 400
        assert _body(resp)["message"] == "Pipeline entry 0 is missing required field(s): userProvidedResource"

    def test_global_workflow_rejects_non_global_pipeline(self):
        body = _workflow_body(database_id="GLOBAL", workflow_id="wfxGlobal",
                              pipelines=[_pipeline(database_id="dbx")])
        with patch.object(cw, "request_to_claims", return_value=self._claims()), \
             patch.object(cw, "CasbinEnforcer") as MockEnf:
            MockEnf.return_value.enforceAPI.return_value = True
            resp = cw.lambda_handler(_event("PUT", body=body), MagicMock())
        assert resp["statusCode"] == 400
        assert _body(resp)["message"] == "Only global pipelines are allowed in global workflows."

    def test_database_workflow_rejects_other_database_pipeline(self):
        body = _workflow_body(database_id="dbx", pipelines=[_pipeline(database_id="dbOther")])
        with patch.object(cw, "request_to_claims", return_value=self._claims()), \
             patch.object(cw, "CasbinEnforcer") as MockEnf:
            MockEnf.return_value.enforceAPI.return_value = True
            resp = cw.lambda_handler(_event("PUT", body=body), MagicMock())
        assert resp["statusCode"] == 400
        assert _body(resp)["message"] == (
            "Only global or same database pipelines are allowed in a database specifc workflows.")

    def test_pipeline_not_authorized(self):
        # API + pipeline scope ok, but the pipeline Tier-2 GET enforce denies.
        with patch.object(cw, "request_to_claims", return_value=self._claims()), \
             patch.object(cw, "CasbinEnforcer") as MockEnf:
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = False
            resp = cw.lambda_handler(_event("PUT", body=_workflow_body()), MagicMock())
        assert resp["statusCode"] == 403
        assert _body(resp)["message"] == "Not Authorized to read the pipeline"

    def test_workflow_id_conflict(self):
        with patch.object(cw, "request_to_claims", return_value=self._claims()), \
             patch.object(cw, "CasbinEnforcer") as MockEnf, \
             patch.object(cw, "find_conflicting_database", return_value="dbOther"):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = cw.lambda_handler(_event("PUT", body=_workflow_body()), MagicMock())
        assert resp["statusCode"] == 400
        assert _body(resp)["message"] == (
            "Workflow ID is already in use by another database. Workflow IDs must be "
            "unique across all databases (including GLOBAL). Choose a different ID.")

    def test_happy_path_put_returns_succeeded(self):
        with patch.object(cw, "request_to_claims", return_value=self._claims()), \
             patch.object(cw, "CasbinEnforcer") as MockEnf, \
             patch.object(cw, "find_conflicting_database", return_value=None), \
             patch.object(cw, "create_workflow", return_value=json.dumps({"message": "Succeeded"})) as mk:
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = cw.lambda_handler(_event("PUT", body=_workflow_body()), MagicMock())
        assert resp["statusCode"] == 200
        assert _body(resp)["message"] == "Succeeded"
        mk.assert_called_once()

    def test_post_invocation_also_creates(self):
        # importGlobalPipelineWorkflow invokes this lambda with method POST; POST and
        # PUT must behave identically (both create/update the workflow).
        with patch.object(cw, "request_to_claims", return_value=self._claims()), \
             patch.object(cw, "CasbinEnforcer") as MockEnf, \
             patch.object(cw, "find_conflicting_database", return_value=None), \
             patch.object(cw, "create_workflow", return_value=json.dumps({"message": "Succeeded"})) as mk:
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = cw.lambda_handler(_event("POST", body=_workflow_body()), MagicMock())
        assert resp["statusCode"] == 200
        assert _body(resp)["message"] == "Succeeded"
        mk.assert_called_once()


# ============================ workflowService ============================

@pytest.mark.unit
class TestWorkflowServiceHandler:
    def _claims(self):
        return {"tokens": ["user@x"], "roles": [], "mfaEnabled": False}

    def test_method_not_allowed_is_403(self):
        # workflowService returns "Method not allowed" as a 403 (authorization_error),
        # unlike the execute/list handlers which use 400. Preserve that.
        with patch.object(ws, "request_to_claims", return_value=self._claims()), \
             patch.object(ws, "CasbinEnforcer") as MockEnf:
            MockEnf.return_value.enforceAPI.return_value = True
            resp = ws.lambda_handler(_event("POST", {"databaseId": "dbx", "workflowId": "wfx"}), MagicMock())
        assert resp["statusCode"] == 403
        assert _body(resp)["message"] == "Method not allowed"

    def test_api_unauthorized(self):
        with patch.object(ws, "request_to_claims", return_value={"tokens": []}):
            resp = ws.lambda_handler(_event("GET", {"databaseId": "dbx", "workflowId": "wfx"}), MagicMock())
        assert resp["statusCode"] == 403

    def test_get_single_workflow_happy_path(self):
        item = {"databaseId": "dbx", "workflowId": "wfx", "workflow_arn": "arn:sm"}
        with patch.object(ws, "request_to_claims", return_value=self._claims()), \
             patch.object(ws, "CasbinEnforcer") as MockEnf, \
             patch.object(ws.dynamodb, "Table", return_value=MagicMock(
                 get_item=MagicMock(return_value={"Item": dict(item)}))):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = ws.lambda_handler(_event("GET", {"databaseId": "dbx", "workflowId": "wfx"}), MagicMock())
        assert resp["statusCode"] == 200
        msg = _body(resp)["message"]
        assert msg["workflowId"] == "wfx"
        # Missing autoTrigger field is backfilled to empty string.
        assert msg["autoTriggerOnFileExtensionsUpload"] == ""

    def test_get_single_workflow_not_found_404_empty(self):
        with patch.object(ws, "request_to_claims", return_value=self._claims()), \
             patch.object(ws, "CasbinEnforcer") as MockEnf, \
             patch.object(ws.dynamodb, "Table", return_value=MagicMock(
                 get_item=MagicMock(return_value={}))):
            MockEnf.return_value.enforceAPI.return_value = True
            resp = ws.lambda_handler(_event("GET", {"databaseId": "dbx", "workflowId": "wfx"}), MagicMock())
        assert resp["statusCode"] == 404
        # A missing (or unauthorized) workflow returns an empty body message.
        assert _body(resp)["message"] == {}

    def test_get_single_workflow_unauthorized_404_empty(self):
        # Object-level enforce denies -> 404 empty body (indistinguishable from missing).
        item = {"databaseId": "dbx", "workflowId": "wfx", "workflow_arn": "arn:sm"}
        with patch.object(ws, "request_to_claims", return_value=self._claims()), \
             patch.object(ws, "CasbinEnforcer") as MockEnf, \
             patch.object(ws.dynamodb, "Table", return_value=MagicMock(
                 get_item=MagicMock(return_value={"Item": dict(item)}))):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = False
            resp = ws.lambda_handler(_event("GET", {"databaseId": "dbx", "workflowId": "wfx"}), MagicMock())
        assert resp["statusCode"] == 404
        assert _body(resp)["message"] == {}

    def test_delete_workflow_happy_path(self):
        item = {"databaseId": "dbx", "workflowId": "wfx", "workflow_arn": "arn:sm"}
        table = MagicMock(
            get_item=MagicMock(return_value={"Item": dict(item)}),
            put_item=MagicMock(), delete_item=MagicMock(return_value={}))
        with patch.object(ws, "request_to_claims", return_value=self._claims()), \
             patch.object(ws, "CasbinEnforcer") as MockEnf, \
             patch.object(ws.dynamodb, "Table", return_value=table), \
             patch.object(ws, "delete_stepfunction", return_value={}):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = ws.lambda_handler(_event("DELETE", {"databaseId": "dbx", "workflowId": "wfx"}), MagicMock())
        assert resp["statusCode"] == 200
        assert _body(resp)["message"] == "Workflow deleted"

    def test_delete_workflow_not_found_404(self):
        table = MagicMock(get_item=MagicMock(return_value={}))
        with patch.object(ws, "request_to_claims", return_value=self._claims()), \
             patch.object(ws, "CasbinEnforcer") as MockEnf, \
             patch.object(ws.dynamodb, "Table", return_value=table):
            MockEnf.return_value.enforceAPI.return_value = True
            resp = ws.lambda_handler(_event("DELETE", {"databaseId": "dbx", "workflowId": "wfx"}), MagicMock())
        assert resp["statusCode"] == 404
        assert _body(resp)["message"] == "Record not found"

    def test_delete_workflow_not_allowed_403(self):
        item = {"databaseId": "dbx", "workflowId": "wfx", "workflow_arn": "arn:sm"}
        table = MagicMock(get_item=MagicMock(return_value={"Item": dict(item)}))
        with patch.object(ws, "request_to_claims", return_value=self._claims()), \
             patch.object(ws, "CasbinEnforcer") as MockEnf, \
             patch.object(ws.dynamodb, "Table", return_value=table):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = False
            resp = ws.lambda_handler(_event("DELETE", {"databaseId": "dbx", "workflowId": "wfx"}), MagicMock())
        assert resp["statusCode"] == 403
        assert _body(resp)["message"] == "Action not allowed"
