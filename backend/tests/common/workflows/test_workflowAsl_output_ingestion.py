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


@pytest.mark.unit
class TestExistingWorkflowsMoveAtNextLaunch:
    PIPELINE = {"databaseId": "db1", "pipelineId": "p1", "executionConfig": {"executionType": "Lambda"}}

    def _deployed(self, schema):
        record = {"workflowVersionArn": "arn:sm:1",
                  "pipelineFingerprints": wa.workflow_pipeline_fingerprints([self.PIPELINE])}
        if schema is not None:
            record["aslSchemaVersion"] = schema
        return record

    @pytest.mark.parametrize("schema", [None, "", "1", 1])
    def test_an_older_schema_does_not_match(self, schema):
        record = self._deployed(schema)
        assert wa.fingerprints_match(record, [self.PIPELINE]) is True
        assert wa.deployment_matches(record, [self.PIPELINE]) is False

    @pytest.mark.parametrize("schema", ["2", 2])
    def test_the_current_schema_matches(self, schema):
        assert wa.deployment_matches(self._deployed(schema), [self.PIPELINE]) is True

    def test_the_max_wait_comes_from_the_environment_within_bounds(self, monkeypatch):
        monkeypatch.setenv("LARGE_OBJECT_COPY_MAX_WAIT_HOURS", "12")
        assert wa._max_wait_hours() == 12
        monkeypatch.setenv("LARGE_OBJECT_COPY_MAX_WAIT_HOURS", "999")
        assert wa._max_wait_hours() == 48
        monkeypatch.delenv("LARGE_OBJECT_COPY_MAX_WAIT_HOURS")
        assert wa._max_wait_hours() == 48
