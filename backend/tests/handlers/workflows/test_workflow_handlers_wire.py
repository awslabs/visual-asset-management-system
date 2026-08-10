# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end handler tests for executionService (list / abort / details / logs). These lock the API
wire contract (response body shapes, status codes, and validation messages).
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
    "AWS_REGION": "us-east-1",
    "LAMBDA_ROLE_ARN": "arn:aws:iam::123456789012:role/t-role",
    "LOG_GROUP_ARN": "arn:aws:logs:us-east-1:123456789012:log-group:t",
}.items():
    os.environ.setdefault(k, v)

# The handlers.workflows package import chain pulls common.workflows.stepfunctions_builder; provide a
# lightweight stub (these tests do not exercise ASL generation).
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf = types.ModuleType("common.workflows.stepfunctions_builder")
    for _name in (
        "create_lambda_task_state", "create_fail_state", "create_retry_config",
        "create_catch_config", "create_workflow_definition", "create_state_machine",
        "update_state_machine", "get_task_builder",
    ):
        setattr(_sf, _name, MagicMock())
    sys.modules["common.workflows.stepfunctions_builder"] = _sf

from backend.backend.handlers.workflows import executionService as le


@pytest.fixture(autouse=True)
def _stub_configuration_row():
    """Stand in for the workflow-execution configuration table.

    A failed read of that row RAISES rather than degrading to {} — the row carries the metadata sources
    and output target the read gate checks, so answering a failed read with {} would remove every
    data-level check and let a throttle turn a denial into an approval. These wire tests do not stub
    DynamoDB, so without this the real GetItem is attempted and every handler returns 500. A test that
    cares about the row's CONTENT patches over this with its own return value."""
    with patch.object(le, "get_workflow_execution_configuration_row", return_value={}):
        yield


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
            "workflowDatabaseId": "wdb", "workflowId": "wfx", "workflowExecutionId": "e1000000000000000000000000000001",
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
            "workflowExecutionId": "abc00000000000000000000000000001", "workflowId": "wfx", "workflowDatabaseId": "dbx",
            "workflow_execution_arn": "arn:ex:main", "executionStatus": status,
            "executionStopDate": "",
        }

    def test_method_not_allowed(self):
        with patch.object(le, "request_to_claims", return_value=self._claims()):
            resp = le.lambda_handler(_event("PUT", {"executionId": "abc00000000000000000000000000001"}), MagicMock())
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
            resp = le.lambda_handler(_event("DELETE", {"executionId": "abc00000000000000000000000000001"}), MagicMock())
        assert resp["statusCode"] == 403

    def test_execution_not_found_404(self):
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=None):
            MockEnf.return_value.enforceAPI.return_value = True
            resp = le.lambda_handler(_event("DELETE", {"executionId": "abc00000000000000000000000000001"}), MagicMock())
        assert resp["statusCode"] == 404
        assert _body(resp)["message"] == "Execution not found"

    def test_workflow_get_denied_403(self):
        # API allowed, but the workflow-level Tier-2 GET enforce denies -> 403.
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = False  # workflow GET denied
            resp = le.lambda_handler(_event("DELETE", {"executionId": "abc00000000000000000000000000001"}), MagicMock())
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
            resp = le.lambda_handler(_event("DELETE", {"executionId": "abc00000000000000000000000000001"}), MagicMock())
        assert resp["statusCode"] == 403

    def test_abort_happy_path_stops_inner_then_outer_and_marks_aborted(self):
        # Two pipeline rows: one running with a registered Step Functions sub-execution, one
        # already SUCCEEDED (must be left untouched). Assert inner+outer StopExecution and that
        # the running pipeline row + main row are written ABORTED.
        running_pipe = {"pipelineExecutionId": "P1", "workflowExecutionId": "abc00000000000000000000000000001",
                        "executionStatus": "RUNNING",
                        "registeredSubExecutions": [
                            {"resourceType": "stepFunctionsExecution",
                             "stateMachineArn": "arn:sm:inner1", "executionArn": "arn:ex:inner1"}],
                        "executionStopDate": ""}
        done_pipe = {"pipelineExecutionId": "P2", "workflowExecutionId": "abc00000000000000000000000000001",
                     "executionStatus": "SUCCEEDED",
                     "registeredSubExecutions": [], "executionStopDate": "d"}
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
            resp = le.lambda_handler(_event("DELETE", {"executionId": "abc00000000000000000000000000001"}), MagicMock())

        assert resp["statusCode"] == 200
        assert _body(resp)["message"] == "Execution aborted"
        # Inner running sub-execution stopped AND the outer main execution stopped.
        stopped_arns = {c.kwargs.get("executionArn") for c in mock_stop.call_args_list}
        assert "arn:ex:inner1" in stopped_arns
        assert "arn:ex:main" in stopped_arns
        # The running pipeline row was marked ABORTED via a targeted update (not a whole-item put,
        # which would drop what the still-running pipeline registered); the SUCCEEDED row was not.
        pexec_table.put_item.assert_not_called()
        pipe_updates = [c.kwargs for c in pexec_table.update_item.call_args_list]
        assert any(u["Key"]["pipelineExecutionId"] == "P1"
                   and u["ExpressionAttributeValues"][":st"] == "ABORTED"
                   for u in pipe_updates)
        assert all(u["Key"]["pipelineExecutionId"] != "P2" for u in pipe_updates)
        # Main row written ABORTED with a stop date, via a targeted update so a concurrent
        # end-state write is not replaced by this read's pre-abort snapshot.
        main_table.put_item.assert_not_called()
        assert main_table.update_item.call_args_list
        updated = main_table.update_item.call_args_list[-1].kwargs
        values = {name: updated["ExpressionAttributeValues"][f":v{i}"]
                  for i, name in enumerate(updated["ExpressionAttributeNames"].values())}
        assert values["executionStatus"] == "ABORTED"
        assert values["executionStopDate"]


# ===================== executionService (details) =====================

@pytest.mark.unit
class TestExecutionDetailsHandler:
    def _claims(self):
        return {"tokens": ["user@x"], "roles": [], "mfaEnabled": False}

    def _main_row(self):
        return {
            "workflowExecutionId": "abc00000000000000000000000000001", "workflowId": "wfx", "workflowDatabaseId": "dbx",
            "workflow_execution_arn": "arn:ex:main", "executionStatus": "SUCCEEDED",
            "executionStartDate": "2026-06-16T00:00:00Z", "executionStopDate": "2026-06-16T00:05:00Z",
            "triggerType": "Manual", "triggeredByUserId": "user@x", "executionError": "",
        }

    def _event_details(self):
        # {executionId} is the API route path parameter (unchanged client contract).
        ev = _event("GET", {"executionId": "abc00000000000000000000000000001"})
        ev["requestContext"]["http"]["path"] = "/workflows/executions/EabcId/details"
        return ev

    def test_details_routed_and_scrubbed(self):
        # Pipeline row carries internal fields (ARNs, S3 prefixes) that MUST NOT surface.
        prow = {
            "pipelineExecutionId": "P1", "pipelineId": "convert", "pipelineDatabaseId": "GLOBAL",
            "executionStatus": "SUCCEEDED", "executionStartDate": "s", "executionStopDate": "e",
            "endStatePipeline": "true", "pipelineExecutionType": "Lambda",
            "pipelineResourceArn": "arn:should:not:leak",
            "registeredSubExecutions": [
                {"resourceType": "stepFunctionsExecution",
                 "stateMachineArn": "arn:sm:leak", "executionArn": "arn:inner:leak"}],
            "registeredLogs": [{"logGroupArn": "arn:lg:leak"}],
            "S3AssetPipelineBucket": "secret-bucket",
            "S3AssetAuxPipelineBucketPrefixTemp": "tmp/secret/",
        }
        out_file = {"pipelineExecutionId": "P1", "relativeFilePath": "/out/model.gltf",
                    "fileType": "file", "fileSize": 2048, "contentType": "model/gltf-binary",
                    "s3Bucket": "secret-bucket", "s3Key": "secret/key"}

        def _query_all(table_name, key_cond):
            # Only the small per-pipeline input-configuration table is read via _query_all now.
            return []

        # The bounded output/input sub-collection reads go through _query_capped -> (rows, truncated).
        def _query_capped(table_name, key_cond, max_items):
            if table_name == le.pipeline_execution_output_files_table:
                return [out_file], False
            return [], False

        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()), \
             patch.object(le, "get_execution_input_assets", return_value=[("dbx", "a1")]), \
             patch.object(le, "get_asset_details", return_value={"assetId": "a1", "databaseId": "dbx"}), \
             patch.object(le, "get_pipeline_execution_rows", return_value=[prow]), \
             patch.object(le, "get_workflow_definition", return_value={"description": "wf desc"}), \
             patch.object(le, "get_pipeline_definition", return_value={"pipelineId": "convert", "description": "Converts", "pipelineType": "standardFile"}), \
             patch.object(le, "_query_all", side_effect=_query_all), \
             patch.object(le, "_query_capped", side_effect=_query_capped):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = le.lambda_handler(self._event_details(), MagicMock())

        assert resp["statusCode"] == 200
        msg = _body(resp)["message"]
        assert msg["workflowExecutionId"] == "abc00000000000000000000000000001"
        assert msg["workflowDescription"] == "wf desc"
        assert msg["pipelines"][0]["name"] == "convert"
        assert msg["pipelines"][0]["description"] == "Converts"
        assert msg["pipelines"][0]["endStatePipeline"] is True
        # Output file traceability surfaces path/type/size, NOT bucket/key.
        of = msg["outputs"]["files"][0]
        assert of["relativeFilePath"] == "/out/model.gltf" and of["fileSize"] == 2048
        # No internal fields anywhere in the serialized response.
        blob = json.dumps(msg)
        for leaked in ("arn:should:not:leak", "arn:inner:leak", "arn:sm:leak", "arn:lg:leak",
                       "secret-bucket", "secret/key", "tmp/secret/", "arn:ex:main"):
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
            "executionId": "abc00000000000000000000000000001", "workflowId": "wfx", "workflowDatabaseId": "dbx",
            "executionStatus": "RUNNING", "executionLog": "stored log text",
            "executionError": "", "executionLogGroupArn": "arn:aws:logs:us-east-1:1:log-group:vams-wf:*",
        }

    def _event_logs(self, query=None):
        ev = _event("GET", {"executionId": "abc00000000000000000000000000001"}, query=query or {})
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
        prow = {"pipelineExecutionId": "P1", "workflowExecutionId": "abc00000000000000000000000000001"}
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
        assert '"abc00000000000000000000000000001"' in captured["filterPattern"]
        assert '"P1"' in captured["filterPattern"]

    def test_logs_full_caller_filter_pattern_is_quoted_literal(self):
        # A caller filterPattern is appended as a single QUOTED literal term with embedded quotes
        # stripped, so it cannot break out of the quote and inject OR/negation that would broaden
        # the search past the AND-ed execution/pipeline scope (shared log group cross-read guard).
        prow = {"pipelineExecutionId": "P1", "workflowExecutionId": "abc00000000000000000000000000001"}
        captured = {}

        def _filter_log_events(**kwargs):
            captured.update(kwargs)
            return {"events": [], "nextToken": None}

        malicious = '" ?"OtherExecId'  # tries to close the term and OR in another id
        with patch.object(le, "request_to_claims", return_value=self._claims()), \
             patch.object(le, "CasbinEnforcer") as MockEnf, \
             patch.object(le, "get_execution_main_row", return_value=self._main_row()), \
             patch.object(le, "get_execution_input_assets", return_value=[]), \
             patch.object(le, "get_pipeline_execution_rows", return_value=[prow]), \
             patch.object(le.logs_client, "filter_log_events", side_effect=_filter_log_events):
            MockEnf.return_value.enforceAPI.return_value = True
            MockEnf.return_value.enforce.return_value = True
            resp = le.lambda_handler(
                self._event_logs(query={"mode": "full", "pipelineExecutionId": "P1",
                                        "filterPattern": malicious}), MagicMock())
        assert resp["statusCode"] == 200
        fp = captured["filterPattern"]
        # Scope terms still present, and the caller term is a single quoted literal with the
        # embedded double-quotes removed (so no term-boundary escape / OR injection).
        assert '"abc00000000000000000000000000001"' in fp and '"P1"' in fp
        assert '"?"' not in fp  # the "?" OR-prefix cannot appear as its own bare token
        assert '" ?"OtherExecId"' not in fp  # raw malicious form is not passed through verbatim

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


