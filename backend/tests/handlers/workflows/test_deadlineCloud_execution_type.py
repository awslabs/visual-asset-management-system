# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deadline Cloud execution-type tests: the DeadlineCloudTaskBuilder ASL shape (createJob
SDK-integration task states with OpenJD Vams* job parameters + mandatory task-token callback)
and the deadlineCloudJobCallback lambda (token resolution from default-bus job status events,
non-VAMS job no-ops, token-gone swallowing, sub-process registration)."""

import os
import json
import pytest
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("ORCHESTRATION_BUS_ARN", "arn:aws:events:us-east-1:1:event-bus/vams-orch")
os.environ.setdefault("ORCHESTRATION_EVENT_SOURCE_PREFIX", "vams.test")

from backend.backend.common.workflows.stepfunctions_builder import (
    get_task_builder,
    DeadlineCloudTaskBuilder,
    TASK_BUILDER_CLASSES,
)


def _deadline_pipeline(**overrides):
    pipeline = {
        "name": "renderPipeline",
        "pipelineExecutionType": "DeadlineCloud",
        "waitForCallback": "Enabled",
        "taskTimeout": "7200",
        "userProvidedResource": json.dumps({
            "resourceType": "DeadlineCloud",
            "deadlineFarmId": "farm-" + "0" * 32,
            "deadlineQueueId": "queue-" + "0" * 32,
            "deadlineTemplate": "name: VamsJob\nspecificationVersion: jobtemplate-2023-09",
            "deadlineTemplateType": "YAML",
        }),
    }
    pipeline.update(overrides)
    return pipeline


def _build_state(pipeline=None, path_context=None):
    builder = get_task_builder("DeadlineCloud")
    pipeline = pipeline or _deadline_pipeline()
    path_context = path_context if path_context is not None else {
        "inputManifestS3Location": "States.Format('s3://{}/m.json', $.b, $$.Execution.Name)",
        "inputConfigurationS3Location": "States.Format('s3://{}/c.json', $.b, $$.Execution.Name)",
        "pipelineExecutionIdRef": "$.pipelineExecutionIds[0]",
    }
    payload = builder.build_payload(pipeline, path_context)
    payload = builder.apply_callback(payload, pipeline)
    return builder.build_task_state(pipeline, "abc12-renderPipeline", payload)


@pytest.mark.unit
class TestDeadlineCloudTaskBuilder:
    def test_registered_in_builder_registry(self):
        assert TASK_BUILDER_CLASSES["DeadlineCloud"] is DeadlineCloudTaskBuilder
        assert isinstance(get_task_builder("DeadlineCloud"), DeadlineCloudTaskBuilder)

    def test_resource_is_sdk_integration_with_task_token(self):
        state = _build_state()
        assert state["Resource"] == \
            "arn:aws:states:::aws-sdk:deadline:createJob.waitForTaskToken"

    def test_partition_threaded_into_integration_arn(self):
        builder = get_task_builder("DeadlineCloud", partition="aws-us-gov")
        pipeline = _deadline_pipeline()
        payload = builder.apply_callback(builder.build_payload(pipeline, {}), pipeline)
        state = builder.build_task_state(pipeline, "s1", payload)
        assert state["Resource"].startswith("arn:aws-us-gov:states:::")

    def test_farm_queue_template_and_priority(self):
        state = _build_state()
        params = state["Parameters"]
        assert params["FarmId"] == "farm-" + "0" * 32
        assert params["QueueId"] == "queue-" + "0" * 32
        assert params["TemplateType"] == "YAML"
        assert "specificationVersion" in params["Template"]
        assert params["Priority"] == DeadlineCloudTaskBuilder.DEFAULT_PRIORITY
        assert params["NameOverride"] == "abc12-renderPipeline"
        # Retry of the state must not double-submit: execution-scoped client token.
        assert "$$.Execution.Name" in params["ClientToken.$"]

    def test_body_envelope_flattened_to_vams_job_parameters(self):
        state = _build_state()
        job_params = state["Parameters"]["Parameters"]
        # Shared envelope fields, Vams-prefixed, string-typed with JSONPath refs.
        assert job_params["VamsWorkflowDatabaseId"] == {"String.$": "$.workflowDatabaseId"}
        assert job_params["VamsWorkflowId"] == {"String.$": "$.workflowId"}
        assert job_params["VamsWorkflowExecutionId"] == {"String.$": "$.workflowExecutionId"}
        assert job_params["VamsWorkflowExecutionS3InputOutputBucket"] == \
            {"String.$": "$.workflowExecutionS3InputOutputBucket"}
        assert job_params["VamsExecutingUserName"] == {"String.$": "$.executingUserName"}
        # executingRequestContext is a multi-KB object: excluded (Deadline caps a string
        # job parameter at 1024 chars); it stays in the SFN state, not the job.
        assert "VamsExecutingRequestContext" not in job_params
        # Manifest/config locations + the callback fields.
        assert "VamsInputManifestS3Location" in job_params
        assert "VamsInputConfigurationS3Location" in job_params
        assert job_params["VamsTaskToken"] == {"String.$": "$$.Task.Token"}
        assert job_params["VamsPipelineExecutionId"] == \
            {"String.$": "$.pipelineExecutionIds[0]"}

    def test_callback_timeout_applied(self):
        state = _build_state()
        assert state["TimeoutSeconds"] == 7200
        assert "HeartbeatSeconds" not in state

    def test_optional_job_settings(self):
        pipeline = _deadline_pipeline(userProvidedResource=json.dumps({
            "resourceType": "DeadlineCloud",
            "deadlineFarmId": "farm-" + "1" * 32,
            "deadlineQueueId": "queue-" + "1" * 32,
            "deadlineTemplate": "{}",
            "deadlineTemplateType": "JSON",
            "deadlinePriority": "80",
            "deadlineMaxRetriesPerTask": "2",
            "deadlineMaxFailedTasksCount": "0",
            "deadlineStorageProfileId": "sp-" + "2" * 32,
        }))
        state = _build_state(pipeline=pipeline)
        params = state["Parameters"]
        assert params["Priority"] == 80
        assert params["MaxRetriesPerTask"] == 2
        assert params["MaxFailedTasksCount"] == 0
        assert params["StorageProfileId"] == "sp-" + "2" * 32
        assert params["TemplateType"] == "JSON"

    def test_callback_disabled_rejected(self):
        # createJob only queues the job — fire-and-forget would let the workflow proceed
        # before any work ran, so the builder requires the task-token callback.
        with pytest.raises(ValueError, match="waitForCallback"):
            _build_state(pipeline=_deadline_pipeline(waitForCallback="Disabled"))

    def test_missing_farm_queue_or_template_rejected(self):
        for missing in ("deadlineFarmId", "deadlineQueueId", "deadlineTemplate"):
            resource = {
                "resourceType": "DeadlineCloud",
                "deadlineFarmId": "farm-" + "0" * 32,
                "deadlineQueueId": "queue-" + "0" * 32,
                "deadlineTemplate": "{}",
            }
            del resource[missing]
            with pytest.raises(ValueError, match="deadline"):
                _build_state(pipeline=_deadline_pipeline(
                    userProvidedResource=json.dumps(resource)))

    def test_catch_and_retry_present(self):
        state = _build_state()
        # Retry only transient createJob API errors — a broad States.ALL retry would
        # replay the fixed ClientToken against an already-created job whose stored
        # VamsTaskToken no longer matches the retry attempt's fresh token.
        assert state["Retry"][0]["ErrorEquals"] == [
            "Deadline.ThrottlingException", "Deadline.InternalServerErrorException"]
        assert state["Catch"][0]["Next"] == "WorkflowProcessingJobFailed"

    def test_priority_zero_preserved(self):
        pipeline = _deadline_pipeline(userProvidedResource=json.dumps({
            "resourceType": "DeadlineCloud",
            "deadlineFarmId": "farm-" + "0" * 32,
            "deadlineQueueId": "queue-" + "0" * 32,
            "deadlineTemplate": "{}",
            "deadlinePriority": 0,
        }))
        state = _build_state(pipeline=pipeline)
        assert state["Parameters"]["Priority"] == 0

    def test_non_numeric_setting_rejected(self):
        pipeline = _deadline_pipeline(userProvidedResource=json.dumps({
            "resourceType": "DeadlineCloud",
            "deadlineFarmId": "farm-" + "0" * 32,
            "deadlineQueueId": "queue-" + "0" * 32,
            "deadlineTemplate": "{}",
            "deadlinePriority": "high",
        }))
        with pytest.raises(ValueError, match="deadlinePriority"):
            _build_state(pipeline=pipeline)

    def test_payload_without_pipeline_execution_id_ref(self):
        # Legacy path contexts without the ref still build (no VamsPipelineExecutionId param).
        state = _build_state(path_context={})
        assert "VamsPipelineExecutionId" not in state["Parameters"]["Parameters"]


# ============================ deadlineCloudJobCallback lambda ============================

from backend.backend.handlers.workflows.sfn import deadlineCloudJobCallback as cb


def _job_event_detail(status="SUCCEEDED"):
    return {
        "farmId": "farm-" + "0" * 32,
        "queueId": "queue-" + "0" * 32,
        "jobId": "job-" + "0" * 32,
        "taskRunStatus": status,
    }


def _get_job_response(parameters=None):
    return {
        "jobId": "job-" + "0" * 32,
        "parameters": parameters if parameters is not None else {
            cb.TASK_TOKEN_PARAMETER: {"string": "tok-123"},
            cb.PIPELINE_EXECUTION_ID_PARAMETER: {"string": "pexec1"},
            cb.WORKFLOW_EXECUTION_ID_PARAMETER: {"string": "wexec1"},
        },
    }


@pytest.mark.unit
class TestDeadlineCloudJobCallback:
    def test_succeeded_sends_task_success_and_registers(self):
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client") as mock_sfn, \
             patch.object(cb, "events_client") as mock_events, \
             patch.object(cb, "orchestration_bus_arn", "arn:bus"):
            mock_dl.get_job.return_value = _get_job_response()
            cb.lambda_handler({"detail": _job_event_detail("SUCCEEDED")}, MagicMock())

            mock_sfn.send_task_success.assert_called_once()
            kwargs = mock_sfn.send_task_success.call_args.kwargs
            assert kwargs["taskToken"] == "tok-123"
            assert json.loads(kwargs["output"])["status"] == "SUCCEEDED"

            mock_events.put_events.assert_called_once()
            entry = mock_events.put_events.call_args.kwargs["Entries"][0]
            assert entry["DetailType"] == "pipeline.execution.register"
            assert entry["Source"] == "vams.test.execution.wexec1.pipeline.pexec1"
            detail = json.loads(entry["Detail"])
            assert detail["pipelineExecutionId"] == "pexec1"
            assert detail["subExecution"]["resourceType"] == cb.RESOURCE_TYPE_DEADLINE_JOB
            assert detail["subExecution"]["jobId"] == "job-" + "0" * 32

    @pytest.mark.parametrize("status", ["FAILED", "CANCELED", "NOT_COMPATIBLE"])
    def test_terminal_failure_sends_task_failure(self, status):
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client") as mock_sfn, \
             patch.object(cb, "events_client"):
            mock_dl.get_job.return_value = _get_job_response()
            cb.lambda_handler({"detail": _job_event_detail(status)}, MagicMock())
            mock_sfn.send_task_failure.assert_called_once()
            kwargs = mock_sfn.send_task_failure.call_args.kwargs
            assert kwargs["taskToken"] == "tok-123"
            assert kwargs["error"] == "DeadlineCloudJobFailed"
            assert json.loads(kwargs["cause"])["status"] == status

    def test_non_vams_job_is_ignored(self):
        # The default-bus rule sees every Deadline job in the account; a job without the
        # reserved VamsTaskToken parameter is not a VAMS workflow job.
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client") as mock_sfn:
            mock_dl.get_job.return_value = _get_job_response(parameters={})
            cb.lambda_handler({"detail": _job_event_detail("SUCCEEDED")}, MagicMock())
            mock_sfn.send_task_success.assert_not_called()
            mock_sfn.send_task_failure.assert_not_called()

    def test_non_terminal_status_skips_get_job(self):
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client") as mock_sfn:
            cb.lambda_handler({"detail": _job_event_detail("RUNNING")}, MagicMock())
            mock_dl.get_job.assert_not_called()
            mock_sfn.send_task_success.assert_not_called()

    def test_missing_identifiers_ignored(self):
        with patch.object(cb, "deadline_client") as mock_dl:
            cb.lambda_handler({"detail": {"taskRunStatus": "SUCCEEDED"}}, MagicMock())
            mock_dl.get_job.assert_not_called()

    @pytest.mark.parametrize("code", ["TaskDoesNotExist", "TaskTimedOut", "InvalidToken"])
    def test_token_gone_errors_swallowed(self, code):
        # Duplicate/late events are expected: an already-resolved or timed-out token is a no-op.
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client") as mock_sfn, \
             patch.object(cb, "events_client") as mock_events, \
             patch.object(cb, "orchestration_bus_arn", "arn:bus"):
            mock_dl.get_job.return_value = _get_job_response()
            mock_sfn.send_task_success.side_effect = ClientError(
                {"Error": {"Code": code, "Message": "gone"}}, "SendTaskSuccess")
            cb.lambda_handler({"detail": _job_event_detail("SUCCEEDED")}, MagicMock())
            # Registration still runs after a swallowed token-gone error.
            mock_events.put_events.assert_called_once()

    def test_other_sfn_errors_raise_for_eventbridge_retry(self):
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client") as mock_sfn:
            mock_dl.get_job.return_value = _get_job_response()
            mock_sfn.send_task_success.side_effect = ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "slow down"}},
                "SendTaskSuccess")
            with pytest.raises(ClientError):
                cb.lambda_handler({"detail": _job_event_detail("SUCCEEDED")}, MagicMock())

    def test_registration_failure_never_raises(self):
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client"), \
             patch.object(cb, "events_client") as mock_events, \
             patch.object(cb, "orchestration_bus_arn", "arn:bus"):
            mock_dl.get_job.return_value = _get_job_response()
            mock_events.put_events.side_effect = Exception("bus unavailable")
            cb.lambda_handler({"detail": _job_event_detail("SUCCEEDED")}, MagicMock())

    def test_registration_skipped_without_bus_or_pipeline_execution_id(self):
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client"), \
             patch.object(cb, "events_client") as mock_events, \
             patch.object(cb, "orchestration_bus_arn", ""):
            mock_dl.get_job.return_value = _get_job_response()
            cb.lambda_handler({"detail": _job_event_detail("SUCCEEDED")}, MagicMock())
            mock_events.put_events.assert_not_called()

    def test_string_detail_parsed(self):
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client") as mock_sfn, \
             patch.object(cb, "events_client"), \
             patch.object(cb, "orchestration_bus_arn", ""):
            mock_dl.get_job.return_value = _get_job_response()
            cb.lambda_handler(
                {"detail": json.dumps(_job_event_detail("SUCCEEDED"))}, MagicMock())
            mock_sfn.send_task_success.assert_called_once()
