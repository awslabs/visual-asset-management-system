# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A pipeline task state retries transient delivery faults only, never the pipeline's own failure.

A fire-and-forget task state carried one `States.ALL` retrier, so a deterministic failure -- a
synchronous Lambda pipeline raising, a request the service rejected -- ran the step three times before
the Catch. The retriers below are matched the way Step Functions matches them: the FIRST retrier whose
ErrorEquals names the error, or a `States.ALL` / `States.TaskFailed` wildcard that covers it, decides.
"""

import json

import pytest

from backend.backend.common.workflows import stepfunctions_builder as sfb
from backend.backend.common.workflows import workflowAslBuilder as asl

# States.ALL matches every error except these; States.TaskFailed every error except States.Timeout.
_NOT_MATCHED_BY_ALL = {"States.DataLimitExceeded", "States.Runtime"}


def _matching_retrier(retriers, error):
    for retrier in retriers:
        names = retrier["ErrorEquals"]
        if error in names:
            return retrier
        if "States.ALL" in names and error not in _NOT_MATCHED_BY_ALL:
            return retrier
        if "States.TaskFailed" in names and error != "States.Timeout" and error not in _NOT_MATCHED_BY_ALL:
            return retrier
    return None


def _is_retried(retriers, error):
    retrier = _matching_retrier(retriers, error)
    return retrier is not None and retrier.get("MaxAttempts", 3) > 0


def _pipeline_task_state(exec_type, resource, wait_for_callback):
    pipeline = {
        "name": "p1", "outputType": "assetFile", "pipelineExecutionType": exec_type,
        "pipelineType": "standardFile", "databaseId": "db",
        "waitForCallback": "Enabled" if wait_for_callback else "Disabled",
        "userProvidedResource": json.dumps(resource),
    }
    if wait_for_callback:
        pipeline["taskTimeout"] = "3600"
    definition, _jobs = asl.generate_workflow_asl(
        [pipeline], "db", "wf", process_workflow_output_function="t-po",
        interim_tracking_function="t-interim", error_handler_function="t-err", aws_partition="aws")
    states = [s for k, s in definition["States"].items()
              if not k.startswith(("interim-", "process-outputs-"))
              and k not in ("HandleExecutionError", "WorkflowProcessingJobFailed")]
    assert len(states) == 1
    return states[0]


_TYPES = [
    ("Lambda", {"resourceId": "arn:fn", "resourceType": "Lambda"}),
    ("SQS", {"resourceId": "https://sqs.us-east-1.amazonaws.com/1/q", "resourceType": "SQS"}),
    ("EventBridge", {"resourceId": "default", "resourceType": "EventBridge"}),
]

# What a synchronous Lambda pipeline's own failure surfaces as: the generic task failure, the
# integration's name for a function error with no type, and the error type a handler raised.
_DETERMINISTIC_ERRORS = ["States.TaskFailed", "Lambda.Unknown", "ValueError", "Exception"]


@pytest.mark.unit
class TestFireAndForgetRetryPolicy:
    def test_the_retrier_list_has_no_catch_all(self):
        retriers = sfb.create_task_retry_configs(wait_for_callback=False)
        assert all("States.ALL" not in r["ErrorEquals"] for r in retriers)
        assert all("States.TaskFailed" not in r["ErrorEquals"] for r in retriers)

    @pytest.mark.parametrize("exec_type,resource", _TYPES, ids=[t for t, _ in _TYPES])
    @pytest.mark.parametrize("error", _DETERMINISTIC_ERRORS)
    def test_a_pipeline_failure_is_not_retried(self, exec_type, resource, error):
        state = _pipeline_task_state(exec_type, resource, wait_for_callback=False)
        assert not _is_retried(state["Retry"], error), state["Retry"]

    @pytest.mark.parametrize("error", [
        "Lambda.TooManyRequestsException", "Lambda.ServiceException",
        "Lambda.AWSLambdaException", "Lambda.SdkClientException",
        "SQS.SdkClientException", "EventBridge.SdkClientException",
    ])
    def test_a_transient_delivery_fault_is_still_retried(self, error):
        state = _pipeline_task_state("Lambda", _TYPES[0][1], wait_for_callback=False)
        assert _is_retried(state["Retry"], error), state["Retry"]


@pytest.mark.unit
class TestCallbackRetryPolicyIsUnchanged:
    @pytest.mark.parametrize("exec_type,resource", _TYPES, ids=[t for t, _ in _TYPES])
    def test_timeouts_and_pipeline_failures_are_not_retried_and_throttling_is(self, exec_type, resource):
        state = _pipeline_task_state(exec_type, resource, wait_for_callback=True)
        for error in ["States.Timeout", "States.HeartbeatTimeout", *_DETERMINISTIC_ERRORS]:
            assert not _is_retried(state["Retry"], error), (error, state["Retry"])
        assert _is_retried(state["Retry"], "Lambda.TooManyRequestsException")
