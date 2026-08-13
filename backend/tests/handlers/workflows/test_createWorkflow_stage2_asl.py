# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 2 ASL-flow tests for the shared workflow ASL generator
(common.workflows.workflowAslBuilder.generate_workflow_asl): interim-tracking states inserted between
pipelines, every state's Catch routed through the error-handler state, and the Stage 2 resolved-input
envelope on each pipeline payload.

Unlike test_createWorkflow_asl_passthrough.py (which stubs the ASL builder), these tests use the REAL
common.workflows.stepfunctions_builder (registered by the root conftest) so the generated state
structure -- Catch targets, the HandleExecutionError state, interim states -- is exercised end to end.
"""

import os
import json
import pytest
from unittest import mock

# The real stepfunctions_builder is registered by the root conftest, so this module deliberately does
# NOT stub it. The shared generator takes its Lambda names + partition explicitly; a small module-level
# harness supplies them so the tests read like the old 3-arg wrapper.
os.environ.setdefault("AWS_REGION", "us-east-1")

from backend.backend.common.workflows import workflowAslBuilder as _asl
from backend.backend.common.workflows import stepfunctions_builder as sfb


class _Cw:
    """Test harness mirroring the former createWorkflow module surface: a 3-arg generate_workflow_asl
    that supplies the fixed Lambda names + the (mockable) partition the shared generator needs."""
    process_workflow_output_function = "t-po"
    interim_tracking_function = "t-interim"
    error_handler_function = "t-err"
    aws_partition = "aws"

    def generate_workflow_asl(self, pipelines, database_id, workflow_id):
        return _asl.generate_workflow_asl(
            pipelines, database_id, workflow_id,
            process_workflow_output_function=self.process_workflow_output_function,
            interim_tracking_function=self.interim_tracking_function,
            error_handler_function=self.error_handler_function,
            aws_partition=self.aws_partition,
        )


cw = _Cw()


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


def _all_state_resources(definition):
    """Every Task state's service-integration Resource ARN in the definition."""
    return [s["Resource"] for s in definition["States"].values()
            if s.get("Type") == "Task" and "Resource" in s]


@pytest.mark.unit
class TestStepFunctionsIntegrationArnPartition:
    """The Step Functions service-integration ARNs embedded in the generated ASL must use the
    deployment partition, not a hardcoded 'aws', so workflows are valid in GovCloud/China/ISO."""

    def test_default_partition_is_commercial_aws(self):
        # With no AWS_PARTITION override, ARNs stay commercial (unchanged behavior).
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        resources = _all_state_resources(definition)
        assert resources, "expected at least one Task state with a Resource ARN"
        assert all(r.startswith("arn:aws:states:::") for r in resources)

    def test_govcloud_partition_threaded_into_all_integration_arns(self):
        # createWorkflow reads the partition from its module-level env-bound value; emulate a
        # GovCloud deployment and assert EVERY integration ARN (pipeline lambda/sqs/eventbridge,
        # interim, error-handler, process-output) uses the aws-us-gov partition.
        import json as _json
        pipelines = [
            {"name": "p1", "outputType": "assetFile", "pipelineExecutionType": "Lambda",
             "pipelineType": "standardFile", "databaseId": "db", "waitForCallback": "Disabled",
             "userProvidedResource": _json.dumps({"resourceId": "arn:fn", "resourceType": "Lambda"})},
            {"name": "p2", "outputType": "assetFile", "pipelineExecutionType": "SQS",
             "pipelineType": "standardFile", "databaseId": "db", "waitForCallback": "Enabled",
             "userProvidedResource": _json.dumps({"resourceId": "https://sqs/q", "resourceType": "SQS"})},
            {"name": "p3", "outputType": "assetFile", "pipelineExecutionType": "EventBridge",
             "pipelineType": "standardFile", "databaseId": "db", "waitForCallback": "Enabled",
             "userProvidedResource": _json.dumps({"resourceId": "bus", "resourceType": "EventBridge"})},
        ]
        with mock.patch.object(cw, "aws_partition", "aws-us-gov"):
            definition, _jobs = cw.generate_workflow_asl(pipelines, "db", "wf")
        resources = _all_state_resources(definition)
        assert all(r.startswith("arn:aws-us-gov:states:::") for r in resources)
        # No commercial-partition ARN leaked through any builder path.
        assert not any(r.startswith("arn:aws:states:::") for r in resources)
        # The three task-type integrations are all present and partition-correct.
        joined = " ".join(resources)
        assert "arn:aws-us-gov:states:::lambda:invoke" in joined
        assert "arn:aws-us-gov:states:::sqs:sendMessage" in joined
        assert "arn:aws-us-gov:states:::events:putEvents" in joined


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
        # only available at the workflow-execution level (the I/O bucket, ids, context). The aux
        # bucket is NOT threaded — it lives in the manifest (manifest.auxBucket).
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        body = _pipeline_states(definition["States"])[0]["Parameters"]["Payload"]["body"]
        for field in ("inputManifestS3Location.$", "inputConfigurationS3Location.$",
                      "workflowExecutionS3InputOutputBucket.$",
                      "workflowExecutionId.$",
                      "workflowDatabaseId.$", "workflowId.$",
                      "executingUserName.$", "executingRequestContext.$"):
            assert field in body
        # The auxiliary bucket is not threaded in the pipeline body.
        assert "bucketAssetAuxiliary.$" not in body

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
            "inputAssetRootS3Key.$", "auxTempPrefix.$", "outputType",
            # inputAssetLocationKey is no longer threaded: each input file is self-locating in the
            # manifest (per-file assetRootS3Key + bucket), so pipelines derive the asset root there.
            "inputAssetLocationKey.$",
            # No single triggering file key is threaded: the SFN body is input-file-agnostic and
            # multi-file-ready (each input file is self-locating in the manifest).
            "inputAssetFileKey.$",
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

    def test_interim_carries_all_four_output_prefixes_relative(self):
        # All four asset-bucket-RELATIVE output prefixes are threaded (no s3://) so the rebuilt
        # next-pipeline manifest's outputs block (bucket + files/previews/metadata/results relative
        # prefixes) matches pipeline 1's manifest. The output bucket travels separately (the
        # workflow-execution I/O bucket).
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        states = definition["States"]
        interim = [s for k, s in states.items() if k.startswith("interim-")][0]
        body = interim["Parameters"]["Payload"]["body"]
        for field in ("outputFilesPrefixRelative.$", "outputPreviewsPrefixRelative.$",
                      "outputMetadataPrefixRelative.$", "outputResultsPrefixRelative.$"):
            assert field in body
        # The next pipeline's aux temp prefix is threaded bucket-relative + execution-scoped.
        assert body["nextPipelineAuxTempPrefix.$"].startswith("States.Format('pipelines/")
        # Orchestration bus config is NOT threaded through the SFN input (interim sources it from
        # its own environment).
        assert "orchestrationBusArn.$" not in body
        assert "orchestrationEventSourcePrefix.$" not in body

    def test_interim_threads_next_pipeline_identity_for_template_rendering(self):
        # The interim lambda renders the next pipeline's input-configuration template tags, so the
        # next-pipeline identity + workflow ids + executing user are threaded for the tag context.
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        states = definition["States"]
        interim = [s for k, s in states.items() if k.startswith("interim-")][0]
        body = interim["Parameters"]["Payload"]["body"]
        assert body["workflowDatabaseId.$"] == "$.workflowDatabaseId"
        assert body["workflowId.$"] == "$.workflowId"
        assert body["executingUserName.$"] == "$.executingUserName"
        # Next-pipeline identity (name/db/job) is known at ASL-build time and threaded literally.
        assert body["nextPipelineId"] and body["nextPipelineJobName"]
        assert "nextPipelineDatabaseId" in body
        # inputAssetLocationKey is no longer threaded: each input file is self-locating in the
        # manifest (per-file assetRootS3Key), so the interim derives relative paths per file.
        assert "inputAssetLocationKey.$" not in body
        # The execution id the interim lambda does read remains.
        assert body["workflowExecutionId.$"] == "$.workflowExecutionId"


@pytest.mark.unit
class TestNextPipelineIdentityThreading:
    """The interim lambda feeds nextPipelineId to the template renderer as {{pipelineId}} /
    {{pipelineName}}, and the execute handler supplies the real pipelineId for pipeline 1, so the
    ASL must thread the pipeline id — not the jobName-derived output-path name."""

    def test_next_pipeline_id_is_the_pipeline_id_not_the_job_name(self):
        pipelines = _pipelines(2)
        # A workflow ref jobName override makes `name` differ from `pipelineId`.
        pipelines[1]["name"] = "convert-b"
        pipelines[1]["pipelineId"] = "meshConvert"
        definition, _jobs = cw.generate_workflow_asl(pipelines, "db", "wf")
        interim = [s for k, s in definition["States"].items() if k.startswith("interim-")][0]
        body = interim["Parameters"]["Payload"]["body"]
        assert body["nextPipelineId"] == "meshConvert"
        # The uuid-prefixed output-path job name stays on its own field.
        assert body["nextPipelineJobName"].endswith("-convert-b")

    def test_next_pipeline_id_falls_back_to_the_name_without_a_pipeline_id(self):
        definition, _jobs = cw.generate_workflow_asl(_pipelines(2), "db", "wf")
        interim = [s for k, s in definition["States"].items() if k.startswith("interim-")][0]
        assert interim["Parameters"]["Payload"]["body"]["nextPipelineId"] == "p2"


@pytest.mark.unit
class TestAslPipelineNameSafety:
    """Pipeline names are spliced into single-quoted States.Format() intrinsic arguments in the
    generated output-path templates, so a name carrying intrinsic syntax must be rejected before
    the definition is built rather than failing Step Functions validation on create."""

    @pytest.mark.parametrize("job_name", [
        "Kurt's job", "a{}b", "a{", "b}", "back\\slash", "line\nbreak",
    ])
    def test_intrinsic_unsafe_job_name_rejected(self, job_name):
        pipelines = _pipelines(1)
        pipelines[0]["name"] = job_name
        with pytest.raises(ValueError):
            cw.generate_workflow_asl(pipelines, "db", "wf")

    def test_empty_pipeline_name_rejected(self):
        pipelines = _pipelines(1)
        pipelines[0]["name"] = ""
        with pytest.raises(ValueError):
            cw.generate_workflow_asl(pipelines, "db", "wf")

    def test_ordinary_job_name_accepted(self):
        pipelines = _pipelines(1)
        pipelines[0]["name"] = "convert-a_1"
        definition, job_names = cw.generate_workflow_asl(pipelines, "db", "wf")
        assert job_names[0].endswith("-convert-a_1")
        assert definition["States"]


@pytest.mark.unit
class TestCallbackTaskRetryPolicy:
    """A .waitForTaskToken task must not retry a callback timeout: the pipeline may still be
    running, so re-sending the invocation starts a second concurrent run of the same step."""

    def _pipeline(self, exec_type, resource):
        return {
            "name": "p1", "outputType": "assetFile", "pipelineExecutionType": exec_type,
            "databaseId": "db", "waitForCallback": "Enabled", "taskTimeout": "3600",
            "userProvidedResource": json.dumps(resource),
        }

    @pytest.mark.parametrize("exec_type,resource", [
        ("Lambda", {"resourceId": "arn:fn", "resourceType": "Lambda"}),
        ("SQS", {"resourceId": "https://sqs.us-east-1.amazonaws.com/1/q", "resourceType": "SQS"}),
        ("EventBridge", {"resourceId": "default", "resourceType": "EventBridge"}),
    ])
    def test_timeout_errors_are_not_retried_for_callback_states(self, exec_type, resource):
        definition, _jobs = cw.generate_workflow_asl(
            [self._pipeline(exec_type, resource)], "db", "wf")
        state = _pipeline_states(definition["States"])[0]
        assert state["Retry"][0]["ErrorEquals"] == ["States.Timeout", "States.HeartbeatTimeout"]
        assert state["Retry"][0]["MaxAttempts"] == 0
        # Transient delivery faults still retry. An application failure the pipeline reported through
        # SendTaskFailure does not, so a failed run is not re-invoked while its outputs may be draining.
        assert state["Retry"][-1]["ErrorEquals"] == list(
            sfb.CALLBACK_TRANSIENT_RETRYABLE_ERRORS)
        assert "States.ALL" not in state["Retry"][-1]["ErrorEquals"]
        assert state["Retry"][-1]["MaxAttempts"] > 0

    def test_fire_and_forget_states_keep_the_single_catch_all_retrier(self):
        definition, _jobs = cw.generate_workflow_asl(_pipelines(1), "db", "wf")
        state = _pipeline_states(definition["States"])[0]
        assert len(state["Retry"]) == 1
        assert state["Retry"][0]["ErrorEquals"] == ["States.ALL"]
