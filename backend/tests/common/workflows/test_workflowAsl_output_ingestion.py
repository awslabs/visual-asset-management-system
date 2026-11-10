# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""ASL schema 2: the wait for large outputs after the process-output state, and how existing
workflows move to it (at their next launch).

- process-output no longer ends the run; a Choice on outputIngestionPending either ends it (no large
  outputs: the same outcome as schema 1) or waits on the batch's task token, bounded by the
  configured maximum wait, then finalizes;
- the wait's timeout is named in $.errorInfo before the error handler records it;
- every service-integration ARN is partition-aware;
- a workflow deployed with schema 1 no longer matches its deployment, so its next launch redeploys
  it; a pipeline-settings mismatch still does as before."""

import json
import os

import pytest

os.environ.setdefault("AWS_REGION", "us-east-1")

from backend.backend.common.workflows import workflowAslBuilder as asl  # noqa: E402
from backend.backend.common.workflows import workflowAsl as wa  # noqa: E402
from backend.backend.common.workflows import stepfunctions_builder as sfb  # noqa: E402


def _pipelines(n=1):
    return [{
        "name": f"p{i}", "pipelineExecutionType": "Lambda", "databaseId": "db",
        "waitForCallback": "Disabled",
        "userProvidedResource": json.dumps({"resourceId": "arn:fn", "resourceType": "Lambda"}),
    } for i in range(1, n + 1)]


def _generate(partition="aws", hours=48, n=1):
    definition, _jobs = asl.generate_workflow_asl(
        _pipelines(n), "db", "wf", process_workflow_output_function="t-po",
        interim_tracking_function="t-it", error_handler_function="t-err",
        aws_partition=partition, max_output_ingestion_wait_hours=hours)
    return definition


def _process_output_id(states):
    return next(k for k in states if k.startswith("process-outputs-"))


@pytest.mark.unit
class TestSchemaTwoDefinition:
    def test_schema_version_is_two_and_stamped(self):
        assert asl.ASL_SCHEMA_VERSION == 2 == wa.ASL_SCHEMA_VERSION
        assert "aslSchemaVersion=2" in _generate()["Comment"]

    def test_process_output_leads_to_the_choice_and_tells_the_step_a_wait_follows(self):
        states = _generate()["States"]
        po = states[_process_output_id(states)]
        assert "End" not in po
        assert po["Next"] == asl.INGESTION_CHOICE_STATE_ID
        assert po["Parameters"]["Payload"]["body"]["awaitOutputIngestion"] is True

    def test_choice_waits_only_when_pending_is_present_and_true(self):
        states = _generate()["States"]
        choice = states[asl.INGESTION_CHOICE_STATE_ID]
        rule = choice["Choices"][0]
        assert rule["Next"] == asl.AWAIT_INGESTION_STATE_ID
        assert {"IsPresent": True} .items() <= rule["And"][0].items()
        assert rule["And"][1]["BooleanEquals"] is True
        assert choice["Default"] == asl.WORKFLOW_SUCCEEDED_STATE_ID
        assert states[asl.WORKFLOW_SUCCEEDED_STATE_ID]["Type"] == "Succeed"

    @pytest.mark.parametrize("hours", [1, 48, 168])
    def test_wait_is_a_task_token_bounded_by_the_configured_hours(self, hours):
        await_state = _generate(hours=hours)["States"][asl.AWAIT_INGESTION_STATE_ID]
        assert await_state["Resource"].endswith(":states:::lambda:invoke.waitForTaskToken")
        assert await_state["TimeoutSeconds"] == hours * 3600
        body = await_state["Parameters"]["Payload"]["body"]
        assert body["mode"] == "await"
        assert body["taskToken.$"] == "$$.Task.Token"
        assert await_state["Next"] == asl.FINALIZE_OUTPUTS_STATE_ID

    def test_the_wait_is_not_retried_after_a_timeout(self):
        retry = _generate()["States"][asl.AWAIT_INGESTION_STATE_ID]["Retry"][0]
        assert "States.Timeout" not in retry["ErrorEquals"]
        assert "States.ALL" not in retry["ErrorEquals"]

    def test_a_timeout_is_named_before_the_error_handler(self):
        states = _generate(hours=6)["States"]
        catches = states[asl.AWAIT_INGESTION_STATE_ID]["Catch"]
        assert catches[0] == {"ErrorEquals": ["States.Timeout"], "Next": asl.INGESTION_TIMEOUT_STATE_ID,
                              "ResultPath": "$.errorInfo"}
        assert catches[-1]["Next"] == "HandleExecutionError"
        marker = states[asl.INGESTION_TIMEOUT_STATE_ID]
        assert marker["Next"] == "HandleExecutionError"
        assert marker["Result"]["Error"] == asl.INGESTION_TIMEOUT_ERROR
        assert "6 hours" in marker["Result"]["Cause"]

    def test_finalize_invokes_the_same_function_and_routes_errors_to_the_handler(self):
        states = _generate()["States"]
        finalize = states[asl.FINALIZE_OUTPUTS_STATE_ID]
        assert finalize["Parameters"]["FunctionName"] == "t-po"
        assert states[asl.AWAIT_INGESTION_STATE_ID]["Parameters"]["FunctionName"] == "t-po"
        assert finalize["Parameters"]["Payload"]["body"]["mode"] == "finalize"
        assert finalize["Catch"][0]["Next"] == "HandleExecutionError"
        assert finalize["Next"] == asl.WORKFLOW_SUCCEEDED_STATE_ID

    @pytest.mark.parametrize("partition", ["aws", "aws-us-gov", "aws-eusc"])
    def test_integration_arns_are_partition_aware(self, partition):
        states = _generate(partition=partition)["States"]
        for state_id in (asl.AWAIT_INGESTION_STATE_ID, asl.FINALIZE_OUTPUTS_STATE_ID):
            assert states[state_id]["Resource"].startswith(f"arn:{partition}:states:::")

    def test_a_ten_step_definition_stays_under_the_definition_quota(self):
        sfb.serialize_definition(_generate(n=10))

    def test_every_state_is_reachable_and_no_dangling_next(self):
        states = _generate(n=3)["States"]
        for state in states.values():
            for target in [state.get("Next"), state.get("Default")] + \
                    [c.get("Next") for c in state.get("Choices", [])] + \
                    [c.get("Next") for c in state.get("Catch", [])]:
                if target:
                    assert target in states


THROTTLE = "Lambda.TooManyRequestsException"


def _first_matching_retrier(state, error):
    """The retrier Step Functions applies to `error` (the first whose ErrorEquals matches)."""
    for retrier in state.get("Retry") or []:
        if error in retrier["ErrorEquals"] or "States.ALL" in retrier["ErrorEquals"]:
            return retrier
    return None


@pytest.mark.unit
class TestThrottlingWaitsForCapacity:
    """Lambda throttling on the output states waits for capacity (about 8 hours of jittered backoff)
    instead of failing the run after 35 seconds, which sent it to the error handler and cancelled its
    copies."""

    @pytest.mark.parametrize("state_id", [None, asl.AWAIT_INGESTION_STATE_ID, asl.FINALIZE_OUTPUTS_STATE_ID])
    def test_throttling_has_its_own_long_retrier(self, state_id):
        states = _generate()["States"]
        state = states[state_id or _process_output_id(states)]
        retrier = _first_matching_retrier(state, THROTTLE)
        assert retrier["ErrorEquals"] == [THROTTLE]
        assert retrier["MaxAttempts"] >= 90
        assert retrier["MaxDelaySeconds"] == 300
        assert retrier["JitterStrategy"] == "FULL"
        total = sum(min(retrier["IntervalSeconds"] * retrier["BackoffRate"] ** n, retrier["MaxDelaySeconds"])
                    for n in range(retrier["MaxAttempts"]))
        assert total > 6 * 3600

    @pytest.mark.parametrize("state_id", [None, asl.AWAIT_INGESTION_STATE_ID, asl.FINALIZE_OUTPUTS_STATE_ID])
    def test_faults_keep_their_short_retrier(self, state_id):
        states = _generate()["States"]
        state = states[state_id or _process_output_id(states)]
        fault = _first_matching_retrier(state, "Lambda.ServiceException")
        assert fault["MaxAttempts"] <= 3
        assert THROTTLE not in fault["ErrorEquals"]

    def test_the_output_step_is_told_its_attempt(self):
        states = _generate()["States"]
        body = states[_process_output_id(states)]["Parameters"]["Payload"]["body"]
        assert body["outputStepAttempt.$"] == "$$.State.RetryCount"


@pytest.mark.unit
class TestTheWaitIsPartOfTheDeployment:
    """Low L1: the wait is baked into each definition, so a changed maximum wait redeploys the
    workflow at its next launch."""
    PIPELINE = {"databaseId": "db1", "pipelineId": "p1", "executionConfig": {"executionType": "Lambda"}}

    def _record(self, wait):
        record = {"workflowVersionArn": "arn:sm:1", "aslSchemaVersion": "2",
                  "pipelineFingerprints": wa.workflow_pipeline_fingerprints([self.PIPELINE])}
        if wait is not None:
            record["outputIngestionWaitHours"] = wait
        return record

    def test_a_changed_wait_does_not_match(self, monkeypatch):
        monkeypatch.setattr(wa, "MAX_OUTPUT_INGESTION_WAIT_HOURS", 72)
        assert wa.deployment_matches(self._record("48"), [self.PIPELINE]) is False
        assert wa.deployment_matches(self._record(None), [self.PIPELINE]) is False
        assert wa.deployment_matches(self._record("72"), [self.PIPELINE]) is True

    def test_the_current_wait_matches_and_a_stale_variable_is_ignored(self, monkeypatch):
        monkeypatch.setenv("LARGE_OBJECT_COPY_MAX_WAIT_HOURS", "72")
        assert wa.deployment_matches(self._record("48"), [self.PIPELINE]) is True
        assert wa.deployment_matches(self._record("72"), [self.PIPELINE]) is False


@pytest.mark.unit
class TestExistingWorkflowsMoveAtNextLaunch:
    PIPELINE = {"databaseId": "db1", "pipelineId": "p1", "executionConfig": {"executionType": "Lambda"}}

    def _deployed(self, schema):
        record = {"workflowVersionArn": "arn:sm:1",
                  "pipelineFingerprints": wa.workflow_pipeline_fingerprints([self.PIPELINE])}
        if schema is not None:
            record["aslSchemaVersion"] = schema
        record["outputIngestionWaitHours"] = str(wa.MAX_OUTPUT_INGESTION_WAIT_HOURS)
        return record

    @pytest.mark.parametrize("schema", [None, "", "1", 1])
    def test_an_older_schema_does_not_match(self, schema):
        record = self._deployed(schema)
        assert wa.fingerprints_match(record, [self.PIPELINE]) is True
        assert wa.deployment_matches(record, [self.PIPELINE]) is False

    @pytest.mark.parametrize("schema", ["2", 2])
    def test_the_current_schema_matches(self, schema):
        assert wa.deployment_matches(self._deployed(schema), [self.PIPELINE]) is True

    def test_the_max_wait_is_the_fixed_constant(self, monkeypatch):
        monkeypatch.setenv("LARGE_OBJECT_COPY_MAX_WAIT_HOURS", "12")
        assert wa._max_wait_hours() == 48
        from common import largeObjectCopy as loc
        from common.workflows import workflowAslBuilder
        # The builder keeps its own default (import-free module); the two stay equal.
        assert workflowAslBuilder.DEFAULT_MAX_OUTPUT_INGESTION_WAIT_HOURS == loc.MAX_OUTPUT_INGESTION_WAIT_HOURS
