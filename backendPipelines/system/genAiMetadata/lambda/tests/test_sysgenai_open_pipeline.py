#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""openPipeline gates the extension against the PARSED allow list, starts the sub-state-machine with
the complete pipeline state, registers the sub-execution on the orchestration bus, and reports a
pre-start rejection against the parent task token WITHOUT wrapping the callback (the nested-invoke
rule: a swallowed callback failure returns a payload-level 400 the caller never inspects)."""

import datetime
import json
from unittest.mock import MagicMock

import pytest

import sysgenai_harness as h

_ALLOWED = ".glb,.png,.pdf"


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
