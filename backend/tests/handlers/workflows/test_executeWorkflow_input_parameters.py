# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the execute-time per-pipeline inputParameters override.

A non-empty override on the execute call replaces the named pipeline's stored inputParameters
for this run only; the stored workflow definition is left untouched. The override is applied in
execute_workflow before launchWorkflow, so the per-execution config.json (written from each
pipeline's inputParameters) carries the run-specific value. (The removal of inline inputMetadata
/ inputParameters from the generated ASL is covered in test_createWorkflow_stage2_asl.py.)

Imports of the handler/model are deferred into each test so they resolve AFTER the root
conftest's autouse mock-import setup runs (matching test_workflow_handlers_wire.py); a
collection-time import would bind a divergent module identity and defeat the runtime patches.

Run as part of the workflows suite (the CI default). The execute-driving tests rely on the
shared conftest mock-import setup that the broader suite establishes; running this file in
isolation is not supported.
"""

import json
import pytest
from unittest.mock import MagicMock, patch


def _claims():
    return {"tokens": ["user@x"], "roles": ["r"], "externalAttributes": []}


def _event(body):
    return {
        "requestContext": {"http": {"method": "POST", "path": "/x"}, "authorizer": {}},
        "pathParameters": {"databaseId": "dbx", "assetId": "a1", "workflowId": "wfx"},
        "body": json.dumps(body),
        "headers": {},
    }


def _stored_pipelines():
    return [
        {"name": "p1", "inputParameters": json.dumps({"stored": 1})},
        {"name": "p2", "inputParameters": json.dumps({"stored": 2})},
    ]


def _run_execute_call(body):
    """Drive ew.lambda_handler through the execute path and return the launchWorkflow mock so
    callers can inspect both positional and keyword arguments."""
    from backend.backend.handlers.workflows import executeWorkflow as ew
    with patch.object(ew, "request_to_claims", return_value=_claims()), \
         patch.object(ew, "CasbinEnforcer") as MockEnf, \
         patch.object(ew, "get_asset", return_value=[{"assetId": "a1", "bucketId": "b1",
                      "assetLocation": {"Key": "a1/"}, "assetName": "n", "tags": []}]), \
         patch.object(ew, "get_workflow", return_value=[{"workflowId": "wfx", "databaseId": "dbx",
                      "workflow_arn": "arn:sm",
                      "specifiedPipelines": {"functions": _stored_pipelines()}}]), \
         patch.object(ew, "validate_pipelines", return_value=(True, "")), \
         patch.object(ew, "get_default_bucket_details",
                      return_value={"bucketName": "bkt", "baseAssetsPrefix": "", "bucketId": "b1"}), \
         patch.object(ew, "get_workflow_executions", return_value={"Items": []}), \
         patch.object(ew, "build_pipeline_input_metadata", return_value={"VAMS": {}}), \
         patch.object(ew, "launchWorkflow", return_value="EXEC123") as mock_launch:
        MockEnf.return_value.enforceAPI.return_value = True
        MockEnf.return_value.enforce.return_value = True
        resp = ew.lambda_handler(_event(body), MagicMock())
    assert resp["statusCode"] == 200, resp
    return mock_launch


def _run_execute(body):
    """Drive the execute path and return the pipelines list that launchWorkflow received.
    launchWorkflow(asset_bucket, asset_file_key, file_key, arn, db, asset, wf_db, wf_id,
                   user, reqctx, pipelines, inputMetadata, trigger_type, storedJobNames=...)."""
    return _run_execute_call(body).call_args[0][10]


@pytest.mark.unit
class TestExecuteRequestModelValidation:
    def test_accepts_per_pipeline_json_overrides(self):
        from models.workflows import ExecuteWorkflowRequestModel
        m = ExecuteWorkflowRequestModel.parse_obj({
            "workflowDatabaseId": "dbx",
            "pipelineInputParameters": {"p1": json.dumps({"a": 1}), "p2": json.dumps({"b": 2})},
        })
        assert m.pipelineInputParameters["p1"] == json.dumps({"a": 1})

    def test_absent_overrides_is_none(self):
        from models.workflows import ExecuteWorkflowRequestModel
        m = ExecuteWorkflowRequestModel.parse_obj({"workflowDatabaseId": "dbx"})
        assert m.pipelineInputParameters is None

    # Each override value is validated as STRING_JSON in the model's root_validator (production
    # validators.py). The unit-test harness substitutes a simplified mock validate() that does
    # not implement STRING_JSON, so the bad-JSON rejection is not assertable here; it is covered
    # by the real validator's own behavior.


@pytest.mark.unit
class TestExecuteOverrideApplied:
    def test_override_replaces_named_pipeline_only(self):
        override = json.dumps({"runSpecific": True})
        pipelines = _run_execute({"workflowDatabaseId": "dbx", "fileKey": "/f/x.glb",
                                  "pipelineInputParameters": {"p1": override}})
        by_name = {p["name"]: p for p in pipelines}
        assert by_name["p1"]["inputParameters"] == override
        # p2 keeps its stored value
        assert by_name["p2"]["inputParameters"] == json.dumps({"stored": 2})

    def test_empty_override_value_keeps_stored(self):
        pipelines = _run_execute({"workflowDatabaseId": "dbx", "fileKey": "/f/x.glb",
                                  "pipelineInputParameters": {"p1": ""}})
        by_name = {p["name"]: p for p in pipelines}
        assert by_name["p1"]["inputParameters"] == json.dumps({"stored": 1})

    def test_no_overrides_passes_stored_values(self):
        pipelines = _run_execute({"workflowDatabaseId": "dbx", "fileKey": "/f/x.glb"})
        by_name = {p["name"]: p for p in pipelines}
        assert by_name["p1"]["inputParameters"] == json.dumps({"stored": 1})
        assert by_name["p2"]["inputParameters"] == json.dumps({"stored": 2})


@pytest.mark.unit
class TestFileBaseExecutionPathExtension:
    def test_model_defaults_to_none(self):
        from models.workflows import ExecuteWorkflowRequestModel
        m = ExecuteWorkflowRequestModel.parse_obj({"workflowDatabaseId": "dbx"})
        assert m.fileBaseExecutionPathExtension is None

    def test_model_accepts_relative_path(self):
        from models.workflows import ExecuteWorkflowRequestModel
        m = ExecuteWorkflowRequestModel.parse_obj({
            "workflowDatabaseId": "dbx", "fileBaseExecutionPathExtension": "/exec-2026/"})
        assert m.fileBaseExecutionPathExtension == "/exec-2026/"

    def test_override_threaded_to_launch_workflow(self):
        # The execute-call override reaches launchWorkflow as outputFileBaseExecutionPathExtension.
        mock_launch = _run_execute_call({"workflowDatabaseId": "dbx", "fileKey": "/f/x.glb",
                                         "fileBaseExecutionPathExtension": "/exec-2026/"})
        assert mock_launch.call_args.kwargs["outputFileBaseExecutionPathExtension"] == "/exec-2026/"

    def test_default_is_slash_when_omitted(self):
        mock_launch = _run_execute_call({"workflowDatabaseId": "dbx", "fileKey": "/f/x.glb"})
        assert mock_launch.call_args.kwargs["outputFileBaseExecutionPathExtension"] == "/"
