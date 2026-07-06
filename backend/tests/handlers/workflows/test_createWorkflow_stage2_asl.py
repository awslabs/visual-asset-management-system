# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 2 ASL-flow tests for createWorkflow.generate_workflow_asl: interim-tracking states
inserted between pipelines, every state's Catch routed through the error-handler state, and
the Stage 2 resolved-input envelope on each pipeline payload.

Unlike test_createWorkflow_asl_passthrough.py (which stubs the ASL builder), these tests use
the REAL common.workflows.stepfunctions_builder (registered by the root conftest) so the generated
state structure -- Catch targets, the HandleExecutionError state, interim states -- is
exercised end to end.
"""

import os
import json
import pytest

# createWorkflow reads these at import time. The real stepfunctions_builder is registered by
# the root conftest, so this module deliberately does NOT stub it.
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-wf")
os.environ.setdefault("VAMS_STACK_NAME", "t-stack")
os.environ.setdefault("PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME", "t-po")
os.environ.setdefault("INTERIM_PIPELINE_TRACKING_LAMBDA_FUNCTION_NAME", "t-interim")
os.environ.setdefault("HANDLE_EXECUTION_ERROR_LAMBDA_FUNCTION_NAME", "t-err")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("LAMBDA_ROLE_ARN", "arn:aws:iam::1:role/t")
os.environ.setdefault("LOG_GROUP_ARN", "arn:aws:logs:us-east-1:1:log-group:t")

from backend.backend.handlers.workflows import createWorkflow as cw


def _pipelines(n):
    return [{
        "name": f"p{i}", "outputType": "assetFile", "pipelineExecutionType": "Lambda",
        "pipelineType": "standardFile", "databaseId": "db", "waitForCallback": "Disabled",
        "userProvidedResource": json.dumps({"resourceId": "arn:fn", "resourceType": "Lambda"}),
    } for i in range(1, n + 1)]


def _pipeline_states(states):
    """Task states that are actual pipelines (not interim/process/error/fail)."""
    return [s for k, s in states.items()
            if not k.startswith("interim-") and not k.startswith("process-outputs-")
            and k not in ("HandleExecutionError", "WorkflowProcessingJobFailed")]


@pytest.mark.unit
class TestCreateWorkflowStage2ASL:
    def test_single_pipeline_has_error_handler_no_interim(self):
        definition, _jobs = cw.generate_workflow_asl(_pipelines(1), "db", "wf")
        states = definition["States"]
        assert "HandleExecutionError" in states
        assert "WorkflowProcessingJobFailed" in states
        assert not any(k.startswith("interim-") for k in states)
        # The error handler reconciles, then transitions to the Fail state.
        assert states["HandleExecutionError"]["Next"] == "WorkflowProcessingJobFailed"

    def test_every_task_catch_routes_to_error_handler(self):
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        states = definition["States"]
        # Every pipeline + interim + process-output state's Catch points at the error handler
        # (not the bare Fail state).
        for k, s in states.items():
            if k in ("HandleExecutionError", "WorkflowProcessingJobFailed"):
                continue
            for c in s.get("Catch", []):
                assert c["Next"] == "HandleExecutionError"
                assert c.get("ResultPath") == "$.errorInfo"

    def test_interim_state_between_each_pipeline_pair(self):
        definition, _jobs = cw.generate_workflow_asl(_pipelines(3), "db", "wf")
        states = definition["States"]
        interim = [k for k in states if k.startswith("interim-")]
        # 3 pipelines -> 2 interim states.
        assert len(interim) == 2
        for k in interim:
            body = states[k]["Parameters"]["Payload"]["body"]
            # Interim carries the version-diff scope + next-pipeline manifest/config targets.
            assert "fromPipelineExecutionId.$" in body
            assert "outputFilesPrefix.$" in body
            assert "nextPipelineManifestS3Key.$" in body
            assert "nextPipelineConfigS3Key.$" in body
            # The interim state invokes the configured interim-tracking function (the env
            # value bound at createWorkflow import; shared process-wide across tests).
            assert states[k]["Parameters"]["FunctionName"] == cw.interim_tracking_function

    def test_pipeline_payload_carries_manifest_location_and_top_level_fields(self):
        # The lean body carries the manifest + per-pipeline config S3 LOCATIONS, plus the fields
        # only available at the workflow-execution level (bucket names, asset keys, ids, context).
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        body = _pipeline_states(definition["States"])[0]["Parameters"]["Payload"]["body"]
        for field in ("inputManifestS3Location.$", "inputConfigurationS3Location.$",
                      "workflowExecutionS3InputOutputBucket.$", "bucketAssetAuxiliary.$",
                      "inputAssetFileKey.$",
                      "workflowExecutionId.$",
                      "workflowDatabaseId.$", "workflowId.$",
                      "executingUserName.$", "executingRequestContext.$"):
            assert field in body

    def test_pipeline_payload_omits_manifest_recoverable_and_inline_fields(self):
        # Everything the pipeline can read from the manifest (resolved input file, output/aux/
        # metadata locations, asset identity) and all inline content is NOT in the body — only
        # the manifest/config S3 locations + genuinely top-level fields travel.
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        body = _pipeline_states(definition["States"])[0]["Parameters"]["Payload"]["body"]
        for field in (
            "inputMetadata", "inputMetadata.$", "inputParameters", "inputParameters.$",
            "inputS3AssetFilePath.$", "outputS3AssetFilesPath.$", "outputS3AssetPreviewPath.$",
            "outputS3AssetMetadataPath.$", "inputOutputS3AssetAuxiliaryFilesPath.$",
            "assetId.$", "databaseId.$", "inputMetadataS3Location.$",
            "inputAssetFilesS3Root.$", "auxTempPrefix.$", "outputType",
            # inputAssetLocationKey is no longer threaded: each input file is self-locating in the
            # manifest (per-file assetFilesS3Root), so pipelines derive the asset root from there.
            "inputAssetLocationKey.$",
            # executionId is a redundant alias of workflowExecutionId — dropped.
            "executionId.$",
        ):
            assert field not in body

    def test_process_output_carries_prior_pipeline_ids(self):
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        states = definition["States"]
        po = [s for k, s in states.items() if k.startswith("process-outputs-")][0]
        body = po["Parameters"]["Payload"]["body"]
        # End-state diff baseline: all pipeline-execution ids threaded through.
        assert body["priorPipelineExecutionIds.$"] == "$.pipelineExecutionIds"
        # Process-output Catch also routes through the error handler.
        assert po["Catch"][0]["Next"] == "HandleExecutionError"

    def test_process_output_carries_output_target_and_path_extension(self):
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        states = definition["States"]
        po = [s for k, s in states.items() if k.startswith("process-outputs-")][0]
        body = po["Parameters"]["Payload"]["body"]
        # Output target identity + base-execution path extension threaded from the SFN input.
        assert body["outputLocationType.$"] == "$.outputLocationType"
        assert body["outputAssetId.$"] == "$.outputAssetId"
        assert body["outputDatabaseId.$"] == "$.outputDatabaseId"
        assert body["outputFileBaseExecutionPathExtension.$"] == "$.outputFileBaseExecutionPathExtension"

    def test_process_output_omits_input_asset_and_redundant_fields(self):
        # The end-state lambda writes to the OUTPUT target (outputAssetId/outputDatabaseId), so the
        # input asset id/db, the input asset location key, the per-pipeline outputType, and the
        # $$.Execution.Name alias (== workflowExecutionId) are NOT threaded.
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        states = definition["States"]
        po = [s for k, s in states.items() if k.startswith("process-outputs-")][0]
        body = po["Parameters"]["Payload"]["body"]
        for field in ("databaseId.$", "assetId.$", "assetLocationKey.$",
                      "outputType", "executionId.$"):
            assert field not in body
        # workflowExecutionId remains the canonical execution id.
        assert body["workflowExecutionId.$"] == "$.workflowExecutionId"

    def test_interim_carries_output_target_and_path_extension(self):
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        states = definition["States"]
        interim = [s for k, s in states.items() if k.startswith("interim-")][0]
        body = interim["Parameters"]["Payload"]["body"]
        # The interim lambda threads output-target identity into the next pipeline's manifest.
        assert body["outputLocationType.$"] == "$.outputLocationType"
        assert body["outputAssetId.$"] == "$.outputAssetId"
        assert body["outputDatabaseId.$"] == "$.outputDatabaseId"
        assert body["outputFileBaseExecutionPathExtension.$"] == "$.outputFileBaseExecutionPathExtension"

    def test_interim_carries_all_four_output_uris(self):
        # All four output-location URIs are threaded so the rebuilt next-pipeline manifest's
        # outputs block (files/previews/metadata/results) matches pipeline 1's manifest.
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        states = definition["States"]
        interim = [s for k, s in states.items() if k.startswith("interim-")][0]
        body = interim["Parameters"]["Payload"]["body"]
        for field in ("outputFilesUri.$", "outputPreviewsUri.$", "outputMetadataUri.$",
                      "outputResultsUri.$"):
            assert field in body

    def test_interim_omits_unused_workflow_identity(self):
        # The interim lambda never reads workflowDatabaseId/workflowId, so they are not threaded.
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        states = definition["States"]
        interim = [s for k, s in states.items() if k.startswith("interim-")][0]
        body = interim["Parameters"]["Payload"]["body"]
        assert "workflowDatabaseId.$" not in body
        assert "workflowId.$" not in body
        # inputAssetLocationKey is no longer threaded: each input file is self-locating in the
        # manifest (per-file assetFilesS3Root), so the interim derives relative paths per file.
        assert "inputAssetLocationKey.$" not in body
        # The execution id the interim lambda does read remains.
        assert body["workflowExecutionId.$"] == "$.workflowExecutionId"
