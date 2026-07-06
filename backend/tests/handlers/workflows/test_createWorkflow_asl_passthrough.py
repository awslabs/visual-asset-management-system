# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import sys
import types
import pytest
from unittest.mock import MagicMock

os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-wf")
os.environ.setdefault("VAMS_STACK_NAME", "t-stack")
os.environ.setdefault("PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME", "t-po")
os.environ.setdefault("INTERIM_PIPELINE_TRACKING_LAMBDA_FUNCTION_NAME", "t-interim")
os.environ.setdefault("HANDLE_EXECUTION_ERROR_LAMBDA_FUNCTION_NAME", "t-err")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("LAMBDA_ROLE_ARN", "arn:aws:iam::1:role/t")
os.environ.setdefault("LOG_GROUP_ARN", "arn:aws:logs:us-east-1:1:log-group:t")

# Stub the ASL builder so generate_workflow_asl runs without the real builder.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    stub = types.ModuleType("common.workflows.stepfunctions_builder")
    stub.create_lambda_task_state = lambda **kw: {"Type": "Task", "_payload": kw.get("payload")}
    stub.create_fail_state = lambda **kw: {"Type": "Fail"}
    stub.create_retry_config = lambda **kw: {}
    stub.create_catch_config = lambda **kw: {}
    stub.create_workflow_definition = lambda states, comment: {"States": dict(states), "StartAt": states[0][0]}
    stub.create_state_machine = MagicMock()
    stub.update_state_machine = MagicMock()

    class _Builder:
        def build_payload(self, pipeline, path_context):
            return {"body": {}}
        def apply_callback(self, payload, pipeline):
            return payload
        def build_task_state(self, pipeline, state_name, payload):
            return {"Type": "Task", "_payload": payload}
    stub.get_task_builder = lambda exec_type: _Builder()
    sys.modules["common.workflows.stepfunctions_builder"] = stub

from backend.backend.handlers.workflows.createWorkflow import generate_workflow_asl


@pytest.mark.unit
def test_process_output_payload_threads_execution_ids():
    pipelines = [{
        "name": "p1",
        "outputType": "assetFile",
        "pipelineExecutionType": "Lambda",
        "pipelineType": "standardFile",
        "databaseId": "db",
        "waitForCallback": "Disabled",
        "userProvidedResource": json.dumps({"resourceId": "arn:fn", "resourceType": "Lambda"}),
    }]
    definition, _job_names = generate_workflow_asl(pipelines, "db", "wf")
    # find the process-outputs state
    po_states = [v for k, v in definition["States"].items() if k.startswith("process-outputs-")]
    assert len(po_states) == 1
    body = po_states[0]["Parameters"]["Payload"]["body"]
    assert body["workflowExecutionId.$"] == "$.workflowExecutionId"
    assert body["endStatePipelineExecutionId.$"] == "$.endStatePipelineExecutionId"
    # The workflow log group is a single shared group provided to processWorkflowExecutionOutput
    # via env var (not baked into each workflow's ASL definition), so it must NOT appear in the
    # process-output payload.
    assert "executionLogGroupArn" not in body


@pytest.mark.unit
def test_first_job_name_is_baked_into_output_uris():
    # The job name generate_workflow_asl returns (and the workflow record persists) is the SAME
    # value baked into the ASL output S3 URIs. executeWorkflow reads jobNames[0] to build the
    # manifest outputs, so this is the parity contract that keeps the manifest's output folder
    # equal to the folder the ASL hands the first pipeline's container.
    pipelines = [{
        "name": "p1", "outputType": "assetFile", "pipelineExecutionType": "Lambda",
        "pipelineType": "standardFile", "databaseId": "db", "waitForCallback": "Disabled",
        "userProvidedResource": json.dumps({"resourceId": "arn:fn", "resourceType": "Lambda"}),
    }]
    definition, job_names = generate_workflow_asl(pipelines, "db", "wf")
    assert job_names and job_names[0]
    # The ASL output S3 URIs embed job_names[0] (the value executeWorkflow reads to build the
    # manifest outputs), so the manifest and the ASL point at the same output folder.
    definition_text = json.dumps(definition)
    assert f"pipelines/p1/{job_names[0]}" in definition_text
