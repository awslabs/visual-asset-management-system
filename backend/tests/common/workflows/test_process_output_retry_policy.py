# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The process-output state retries a timed-out attempt, and deployed workflows pick that up (#1290).

On current Lambda runtimes Step Functions reports a function timeout as `Sandbox.Timedout`;
`States.Timeout` is raised only for a state `TimeoutSeconds`. The process-output retrier did not list
`Sandbox.Timedout`, so a timed-out output step went to the Catch and the run FAILED with its outputs
in the asset and none recorded. Retrying is safe because each attempt claims its outputs in the run's
ingest ledger (#1271). `EarlierIngestionInProgress` (an attempt that left outputs to a live earlier
attempt) is retried as well, and an application error still is not.

A change to every definition is folded into the deployment fingerprints through ASL_SCHEMA_VERSION,
so a workflow deployed before it is redeployed at its next launch.
"""

import json

import pytest

from backend.backend.common.workflows import workflowAsl as wa
from backend.backend.common.workflows import workflowAslBuilder as asl
from backend.tests.common.workflows.test_pipeline_task_retry_policy import _is_retried, _matching_retrier


def _process_output_state(n_pipelines=1):
    pipelines = [{
        "name": f"p{i}", "outputType": "assetFile", "pipelineExecutionType": "Lambda",
        "pipelineType": "standardFile", "databaseId": "db", "waitForCallback": "Disabled",
        "userProvidedResource": json.dumps({"resourceId": "arn:fn", "resourceType": "Lambda"}),
    } for i in range(n_pipelines)]
    definition, _jobs = asl.generate_workflow_asl(
        pipelines, "db", "wf", process_workflow_output_function="t-po",
        interim_tracking_function="t-interim", error_handler_function="t-err", aws_partition="aws")
    [state] = [s for k, s in definition["States"].items() if k.startswith("process-outputs-")]
    return state, definition


@pytest.mark.unit
class TestProcessOutputRetrier:
    @pytest.mark.parametrize("n", [1, 3])
    def test_a_lambda_timeout_is_retried(self, n):
        state, _definition = _process_output_state(n)
        assert _is_retried(state["Retry"], "Sandbox.Timedout")
        retrier = _matching_retrier(state["Retry"], "Sandbox.Timedout")
        # The same policy as the other retried faults.
        assert (retrier["IntervalSeconds"], retrier["BackoffRate"], retrier["MaxAttempts"]) == (5, 2.0, 3)

    def test_a_state_timeout_and_a_stood_aside_attempt_are_retried(self):
        state, _definition = _process_output_state()
        for error in ("States.Timeout", "EarlierIngestionInProgress", "Lambda.ServiceException"):
            assert _is_retried(state["Retry"], error), error

    @pytest.mark.parametrize("error", ["States.TaskFailed", "Lambda.Unknown", "VAMSGeneralErrorResponse",
                                       "Exception"])
    def test_an_application_error_is_not_retried(self, error):
        state, _definition = _process_output_state()
        assert not _is_retried(state["Retry"], error)


@pytest.mark.unit
class TestDeployedWorkflowsPickItUp:
    def test_the_schema_versions_agree_and_are_stamped(self):
        assert asl.ASL_SCHEMA_VERSION == wa.ASL_SCHEMA_VERSION >= 2
        _state, definition = _process_output_state()
        assert f"aslSchemaVersion={wa.ASL_SCHEMA_VERSION}" in definition["Comment"]

    def test_a_workflow_deployed_before_the_schema_key_is_redeployed_at_launch(self):
        record = {"databaseId": "db", "pipelineId": "p1", "executionConfig": {"executionType": "Lambda"}}
        current = wa.workflow_pipeline_fingerprints([record])
        old = {k: v for k, v in current.items() if k != wa.ASL_SCHEMA_FINGERPRINT_KEY}
        deployed_before = {"workflowVersionArn": "arn:sm:1", "pipelineFingerprints": old}
        assert wa.deployment_matches(deployed_before, [record]) is False
        deployed_under_v1 = {"workflowVersionArn": "arn:sm:1",
                             "pipelineFingerprints": {**old, wa.ASL_SCHEMA_FINGERPRINT_KEY: "1"}}
        assert wa.deployment_matches(deployed_under_v1, [record]) is False
        redeployed = {"workflowVersionArn": "arn:sm:2", "pipelineFingerprints": current}
        assert wa.deployment_matches(redeployed, [record]) is True
