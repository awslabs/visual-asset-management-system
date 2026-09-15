#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""openPipeline gates the extension against the PARSED allow list, starts the sub-state-machine with
the complete pipeline state, registers the sub-execution on the orchestration bus, and reports a
pre-start rejection against the parent task token WITHOUT wrapping the callback (the nested-invoke
rule: a swallowed callback failure returns a payload-level 400 the caller never inspects)."""

import datetime
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

import sysgenai_harness as h

_ALLOWED = ".glb,.png,.pdf"

# The Fargate render branch's env, as the CDK sets it when the `useFargateRenderer` sub-flag is on:
# the vended container group the job definition writes to + the physical job definition name.
_BATCH_ENV = {
    "BATCH_JOB_LOG_GROUP_NAME": "/aws/vendedlogs/Pipelines/SystemGenAiMetadataRenderabcdef1234",
    "BATCH_JOB_LOG_GROUP_ARN":
        "arn:aws:logs:us-east-1:123456789012:log-group:/aws/vendedlogs/Pipelines/SystemGenAiMetadataRenderabcdef1234:*",
    "BATCH_JOB_DEFINITION_NAME": "SystemGenAiMetadataRenderJobvams-test_vamsabcdef1234",
}


def _backend_validators():
    """The backend's real validators module, so registered values are checked against the regexes
    registerPipelineExecution applies rather than a copy of them. Appended to the END of sys.path so
    nothing under backend/backend shadows this pipeline's own modules."""
    backend_pkg = os.path.join(h.REPO_ROOT, "backend", "backend")
    if backend_pkg not in sys.path:
        sys.path.append(backend_pkg)
    return importlib.import_module("common.validators")


def _event(file_uri, **over):
    event = {
        "inputS3AssetFilePath": file_uri,
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/JOB/output/E1/files/",
        "outputS3AssetPreviewPath": "s3://abkt/pipelines/p1/JOB/output/E1/previews/",
        "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/JOB/output/E1/metadata/",
        "outputS3AssetResultsPath": "s3://abkt/pipelines/p1/JOB/output/E1/results/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/system-genai-metadata/E1/",
        "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
        "inputConfigurationS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
        "sfnExternalTaskToken": h.TASK_TOKEN,
        "executingUserName": "user@x",
        "executingRequestContext": "user@x",
        "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        "assetId": "xidM",
        "databaseId": "dbM",
        "bucketId": "bkt-01",
        "relativePath": "/models/pump.glb",
        "versionId": "v1",
        "workflowExecutionId": "E1",
    }
    event.update(over)
    return event


def _load():
    return h.load_handler("openPipeline", {"ALLOWED_INPUT_FILEEXTENSIONS": _ALLOWED})


def _start_response(**kw):
    return {"executionArn": f"arn:aws:states:us-east-1:1:execution:SystemGenAiMetadata:{kw.get('name', 'x')}",
            "startDate": datetime.datetime(2026, 1, 1, 0, 0, 0)}


def _run(mod, event, start=None, put_events=None, send_failure=None):
    mod.sfn = MagicMock()
    mod.sfn.start_execution = start or MagicMock(side_effect=lambda **kw: _start_response(**kw))
    mod.sfn.send_task_failure = send_failure or MagicMock()
    mod.events_client = MagicMock()
    mod.events_client.put_events = put_events or MagicMock()
    return mod.lambda_handler(event, MagicMock())


@pytest.mark.unit
class TestExtensionGate:
    @pytest.mark.parametrize("extension", [".gl", ".pn", ".glb,", ".p", ".zzz"])
    def test_non_member_is_rejected_and_reported(self, extension):
        mod = _load()
        resp = _run(mod, _event(f"s3://abkt/xidM/models/pump{extension}"))
        assert resp["statusCode"] == 400
        assert resp["body"]["message"] == "Pipeline cannot process file type provided"
        mod.sfn.start_execution.assert_not_called()
        mod.sfn.send_task_failure.assert_called_once()
        assert mod.sfn.send_task_failure.call_args.kwargs["taskToken"] == h.TASK_TOKEN

    @pytest.mark.parametrize("extension", [".glb", ".GLB", ".png", ".pdf"])
    def test_member_starts_the_machine(self, extension):
        mod = _load()
        resp = _run(mod, _event(f"s3://abkt/xidM/models/pump{extension}"))
        assert resp["statusCode"] == 200
        mod.sfn.start_execution.assert_called_once()
        mod.sfn.send_task_failure.assert_not_called()

    def test_no_extension_is_rejected(self):
        mod = _load()
        resp = _run(mod, _event("s3://abkt/xidM/models/pump"))
        assert resp["statusCode"] == 400
        mod.sfn.start_execution.assert_not_called()

    def test_folder_input_is_rejected_and_reported(self):
        mod = _load()
        resp = _run(mod, _event("s3://abkt/xidM/models/"))
        assert resp["statusCode"] == 400
        assert resp["body"]["message"] == "Input S3 URI cannot be a folder"
        mod.sfn.start_execution.assert_not_called()
        mod.sfn.send_task_failure.assert_called_once()

    def test_whitespace_in_the_list_is_tolerated(self):
        mod = h.load_handler("openPipeline", {"ALLOWED_INPUT_FILEEXTENSIONS": ".glb , .png ,.pdf"})
        resp = _run(mod, _event("s3://abkt/xidM/models/pump.png"))
        assert resp["statusCode"] == 200

    def test_the_abort_is_not_wrapped(self):
        """A failing callback must propagate so the nested caller sees FunctionError and reports the
        token under its own role; swallowing it returns a 400 payload the caller never inspects."""
        mod = _load()
        with pytest.raises(RuntimeError, match="AccessDenied"):
            _run(mod, _event("s3://abkt/xidM/models/pump.zzz"),
                 send_failure=MagicMock(side_effect=RuntimeError("AccessDenied: states:SendTaskFailure")))


@pytest.mark.unit
class TestStateMachineInput:
    def test_the_input_is_the_complete_pipeline_state(self):
        mod = _load()
        _run(mod, _event("s3://abkt/xidM/models/pump.glb"))
        sfn_input = json.loads(mod.sfn.start_execution.call_args.kwargs["input"])
        assert sfn_input["jobName"].startswith("PipelineJob_")
        assert sfn_input["externalSfnTaskToken"] == h.TASK_TOKEN
        for field, value in {
            "inputS3AssetFilePath": "s3://abkt/xidM/models/pump.glb",
            "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/JOB/output/E1/files/",
            "outputS3AssetPreviewPath": "s3://abkt/pipelines/p1/JOB/output/E1/previews/",
            "outputS3AssetMetadataPath": "s3://abkt/pipelines/p1/JOB/output/E1/metadata/",
            "outputS3AssetResultsPath": "s3://abkt/pipelines/p1/JOB/output/E1/results/",
            "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/system-genai-metadata/E1/",
            "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
            "inputConfigurationS3Location":
                "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "assetId": "xidM", "databaseId": "dbM", "bucketId": "bkt-01", "relativePath": "/models/pump.glb",
            "versionId": "v1", "workflowExecutionId": "E1",
            "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
        }.items():
            assert sfn_input[field] == value, field
        # Content never travels; only locations do.
        assert "inputMetadata" not in sfn_input and "inputParameters" not in sfn_input
        # The executing identity stays with the vamsExecute hop.
        assert "executingUserName" not in sfn_input

    def test_missing_optional_fields_become_empty_strings(self):
        mod = _load()
        event = _event("s3://abkt/xidM/models/pump.glb")
        for key in ("versionId", "workflowExecutionId", "sfnExternalTaskToken"):
            event.pop(key)
        resp = _run(mod, event)
        assert resp["statusCode"] == 200
        sfn_input = json.loads(mod.sfn.start_execution.call_args.kwargs["input"])
        assert sfn_input["versionId"] == "" and sfn_input["workflowExecutionId"] == ""
        assert sfn_input["externalSfnTaskToken"] == ""

    def test_the_execution_name_is_the_job_name_and_unique(self):
        mod = _load()
        names = []
        for _ in range(20):
            _run(mod, _event("s3://abkt/xidM/models/pump.glb"))
            names.append(mod.sfn.start_execution.call_args.kwargs["name"])
        assert len(set(names)) == 20
        for name in names:
            assert len(name) <= 80 and ":" not in name and "/" not in name and " " not in name

    def test_start_failure_reports_the_token_and_returns_500(self):
        mod = _load()
        resp = _run(mod, _event("s3://abkt/xidM/models/pump.glb"),
                    start=MagicMock(side_effect=Exception("ExecutionLimitExceeded")))
        assert resp["statusCode"] == 500
        assert resp["body"]["message"] == "Internal Server Error"
        mod.sfn.send_task_failure.assert_called_once()


@pytest.mark.unit
class TestRegistration:
    def test_registers_the_sub_execution(self):
        mod = _load()
        _run(mod, _event("s3://abkt/xidM/models/pump.glb"))
        mod.events_client.put_events.assert_called_once()
        entry = mod.events_client.put_events.call_args.kwargs["Entries"][0]
        assert entry["EventBusName"] == "vams-orchestration"
        assert entry["Source"] == "vams.prod.execution.E1.pipeline.P1"
        assert entry["DetailType"] == "pipeline.execution.register"
        detail = json.loads(entry["Detail"])
        assert detail["pipelineExecutionId"] == "P1"
        assert detail["subExecution"]["stateMachineArn"] == mod.STATE_MACHINE_ARN
        assert detail["subExecution"]["executionArn"].startswith("arn:aws:states:")
        assert detail["logs"][0]["logGroupName"] == h.DEFAULT_ENV["STATE_MACHINE_LOG_GROUP_NAME"]
        assert detail["logs"][0]["logGroupArn"] == h.DEFAULT_ENV["STATE_MACHINE_LOG_GROUP_ARN"]
        assert detail["logs"][0]["logStreamName"] == ""

    def test_the_sub_execution_and_its_state_machine_log_are_labelled(self):
        """The #321 contract: `label` on the sub-execution, `sourceType` + `label` on each log entry."""
        mod = _load()
        _run(mod, _event("s3://abkt/xidM/models/pump.glb"))
        detail = json.loads(mod.events_client.put_events.call_args.kwargs["Entries"][0]["Detail"])
        assert detail["subExecution"]["label"] == "GenAI metadata processing"
        assert len(detail["logs"]) == 1
        assert detail["logs"][0]["sourceType"] == "stateMachine"
        assert detail["logs"][0]["label"] == "GenAI metadata state machine"
        validators = _backend_validators()
        assert validators.validate_display_label("label", detail["subExecution"]["label"])[0]
        assert validators.validate_display_label("label", detail["logs"][0]["label"])[0]

    def test_registers_the_render_job_container_log_when_the_fargate_branch_is_deployed(self):
        mod = h.load_handler("openPipeline", dict({"ALLOWED_INPUT_FILEEXTENSIONS": _ALLOWED}, **_BATCH_ENV))
        _run(mod, _event("s3://abkt/xidM/models/pump.glb"))
        detail = json.loads(mod.events_client.put_events.call_args.kwargs["Entries"][0]["Detail"])
        sfn_log, batch_log = detail["logs"]
        assert sfn_log["sourceType"] == "stateMachine"
        assert batch_log == {
            "logGroupArn": _BATCH_ENV["BATCH_JOB_LOG_GROUP_ARN"],
            "logGroupName": _BATCH_ENV["BATCH_JOB_LOG_GROUP_NAME"],
            "logStreamName": "",
            "logStreamPrefix": _BATCH_ENV["BATCH_JOB_DEFINITION_NAME"] + "/default/",
            "stageName": "FargateRenderJob",
            "sourceType": "batch",
            "label": "FargateRenderJob container",
        }
        # The values pass the validators the backend applies on receipt; a shape it rejects is
        # dropped silently there and surfaces only as a missing log source.
        validators = _backend_validators()
        assert validators.validate_cloudwatch_log_group_arn("logGroupArn", batch_log["logGroupArn"])[0]
        assert validators.validate_log_stream_name("logStreamPrefix", batch_log["logStreamPrefix"])[0]
        assert validators.validate_sfn_state_name("stageName", batch_log["stageName"])[0]
        assert validators.validate_display_label("label", batch_log["label"])[0]
        assert not validators.validate_cloudwatch_log_group_arn(
            "logGroupArn", "arn:aws:logs:us-east-1:123456789012:log-group//aws/batch/job")[0]

    @pytest.mark.parametrize("missing", ["BATCH_JOB_DEFINITION_NAME", "BATCH_JOB_LOG_GROUP_NAME"])
    def test_the_container_log_is_skipped_when_its_env_is_incomplete(self, missing):
        env = dict({"ALLOWED_INPUT_FILEEXTENSIONS": _ALLOWED}, **_BATCH_ENV)
        env[missing] = ""
        if missing == "BATCH_JOB_LOG_GROUP_NAME":
            env["BATCH_JOB_LOG_GROUP_ARN"] = ""
        mod = h.load_handler("openPipeline", env)
        _run(mod, _event("s3://abkt/xidM/models/pump.glb"))
        detail = json.loads(mod.events_client.put_events.call_args.kwargs["Entries"][0]["Detail"])
        assert [log["sourceType"] for log in detail["logs"]] == ["stateMachine"]

    def test_the_batch_state_name_is_the_construct_id_of_the_fargate_task(self):
        """The CDK test asserts every `*_STATE_NAME` literal here is a key of the synthesized ASL; this
        pins the producer side of that contract to the one Batch state the construct declares."""
        mod = _load()
        assert mod.BATCH_STATE_NAME == "FargateRenderJob"

    def test_registration_is_skipped_without_a_prefix(self):
        mod = _load()
        resp = _run(mod, _event("s3://abkt/xidM/models/pump.glb", orchestrationEventPrefix=""))
        assert resp["statusCode"] == 200
        mod.events_client.put_events.assert_not_called()

    def test_registration_failure_never_fails_the_start(self):
        mod = _load()
        resp = _run(mod, _event("s3://abkt/xidM/models/pump.glb"),
                    put_events=MagicMock(side_effect=Exception("AccessDenied: events:PutEvents")))
        assert resp["statusCode"] == 200
        mod.sfn.send_task_failure.assert_not_called()
