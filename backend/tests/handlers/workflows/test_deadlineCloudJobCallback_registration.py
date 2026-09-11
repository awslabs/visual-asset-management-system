# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A Deadline Cloud job is registered while it can still be cancelled, and only when it is ours.

Registration is the only thing that makes a farm job reachable from `execution abort`: the workflow
runs the job through `createJob.waitForTaskToken`, so Step Functions holds the token and NOT the job,
and stopping the state machine leaves the job rendering. The callback therefore registers on EVERY
status it is handed, not only a terminal one — a job registered at its terminal status is registered
exactly when there is nothing left to stop.

Two halves of that contract are pinned here, because each fails silently on its own:

  - the IN-FLIGHT statuses. `RUNNING` is covered in `test_deadlineCloud_execution_type.py`; the cases
    below add the queued statuses (the state the job actually sits in when an operator aborts) and the
    lifecycle event, which carries no `taskRunStatus` field at all.
  - the LOCATOR. Cancelling needs farmId + queueId + jobId together. A registration carrying only a
    jobId is accepted by the registration lambda and then reported as uncancellable by abort, so
    asserting "it was registered" alone does not prove it can be stopped.

And the negative that bounds both: the status rule is unfiltered, so this lambda sees every Deadline
job in the account. A job without the reserved VamsTaskToken parameter must never be registered — a
stranger's job attached to a VAMS pipeline row would be cancelled by an abort that never asked for it.
"""

import os
import json

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("ORCHESTRATION_BUS_ARN", "arn:aws:events:us-east-1:1:event-bus/vams-orch")
os.environ.setdefault("ORCHESTRATION_EVENT_SOURCE_PREFIX", "vams.test")

# executionService resolves its table names at import; the root conftest seeds these too.
for _table_env in (
    "ASSET_STORAGE_TABLE_NAME", "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME",
    "PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "WORKFLOW_STORAGE_TABLE_NAME",
    "PIPELINE_STORAGE_TABLE_NAME", "EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME",
):
    os.environ.setdefault(_table_env, "t-" + _table_env.lower())

from backend.backend.handlers.workflows.sfn import deadlineCloudJobCallback as cb  # noqa: E402

# Deadline ids are a prefix plus 32 hex characters, which is what the registration lambda's `ID`
# validator has to accept for the locator to survive being written to the pipeline row.
FARM = "farm-" + "a1" * 16
QUEUE = "queue-" + "b2" * 16
JOB = "job-" + "c3" * 16

PEXEC = "pexec1"
WEXEC = "wexec1"


def _run_status(status):
    """A "Job Run Status Change" detail (combined task-run status)."""
    return {"farmId": FARM, "queueId": QUEUE, "jobId": JOB, "taskRunStatus": status}


def _lifecycle(status):
    """A "Job Lifecycle Status Change" detail — no taskRunStatus field at all."""
    return {"farmId": FARM, "queueId": QUEUE, "jobId": JOB, "lifecycleStatus": status}


def _vams_job(with_token=True):
    parameters = {
        cb.PIPELINE_EXECUTION_ID_PARAMETER: {"string": PEXEC},
        cb.WORKFLOW_EXECUTION_ID_PARAMETER: {"string": WEXEC},
    }
    if with_token:
        parameters[cb.TASK_TOKEN_PARAMETER] = {"string": "tok-123"}
    return {"jobId": JOB, "parameters": parameters}


def _registered(mock_events):
    """The subExecution entry the callback put on the orchestration bus."""
    entry = mock_events.put_events.call_args.kwargs["Entries"][0]
    return json.loads(entry["Detail"])["subExecution"]


@pytest.mark.unit
class TestInFlightRegistration:
    # SCHEDULED/READY is the state an abort actually finds: the job is submitted, no worker has
    # picked it up, and it is burning nothing yet but will render to completion if it is not
    # registered before the state machine is stopped.
    @pytest.mark.parametrize("status", ["READY", "SCHEDULED", "PENDING", "ASSIGNED",
                                        "STARTING", "SUSPENDED"])
    def test_a_non_terminal_run_status_registers_and_leaves_the_token_open(self, status):
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client") as mock_sfn, \
             patch.object(cb, "events_client") as mock_events, \
             patch.object(cb, "orchestration_bus_arn", "arn:bus"):
            mock_dl.get_job.return_value = _vams_job()
            cb.lambda_handler({"detail": _run_status(status)}, MagicMock())

            # GetJob is what reads the reserved parameters; returning before it is how a non-terminal
            # status used to be discarded, so this call is the pin on that early return being gone.
            mock_dl.get_job.assert_called_once_with(farmId=FARM, queueId=QUEUE, jobId=JOB)
            mock_events.put_events.assert_called_once()
            mock_sfn.send_task_success.assert_not_called()
            mock_sfn.send_task_failure.assert_not_called()

    def test_a_lifecycle_event_with_no_task_run_status_registers(self):
        """CREATE_COMPLETE carries only a lifecycleStatus. Reading a missing `taskRunStatus` as a
        reason to skip the job would drop the earliest point at which it can be registered."""
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client") as mock_sfn, \
             patch.object(cb, "events_client") as mock_events, \
             patch.object(cb, "orchestration_bus_arn", "arn:bus"):
            mock_dl.get_job.return_value = _vams_job()
            cb.lambda_handler({"detail": _lifecycle("CREATE_COMPLETE")}, MagicMock())

            mock_events.put_events.assert_called_once()
            assert _registered(mock_events)["jobId"] == JOB
            mock_sfn.send_task_success.assert_not_called()
            mock_sfn.send_task_failure.assert_not_called()

    def test_the_registration_carries_the_whole_locator_a_cancel_needs(self):
        """farmId + queueId + jobId, all three: UpdateJob addresses a job by the triple, and abort
        reports a registration missing the farm or queue as uncancellable rather than cancelling it."""
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client"), \
             patch.object(cb, "events_client") as mock_events, \
             patch.object(cb, "orchestration_bus_arn", "arn:bus"):
            mock_dl.get_job.return_value = _vams_job()
            cb.lambda_handler({"detail": _run_status("READY")}, MagicMock())

            sub = _registered(mock_events)
            assert (sub["farmId"], sub["queueId"], sub["jobId"]) == (FARM, QUEUE, JOB)

    def test_the_registered_resource_type_is_the_one_abort_dispatches_on(self):
        """The producer and the consumer of this string live in different modules with no shared
        constant, so a rename on either side leaves abort silently on its "not abortable" arm."""
        from backend.backend.handlers.workflows import executionService

        assert cb.RESOURCE_TYPE_DEADLINE_JOB == executionService.RESOURCE_TYPE_DEADLINE_CLOUD_JOB


@pytest.mark.unit
class TestRegistrationBounds:
    def test_a_foreign_job_is_not_registered_at_a_non_terminal_status(self):
        """The status rule is unfiltered, so every Deadline job in the account arrives here. Widening
        registration to run before the task-token check would attach another team's render job to a
        VAMS pipeline row, and the next abort of that execution would cancel it."""
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client") as mock_sfn, \
             patch.object(cb, "events_client") as mock_events, \
             patch.object(cb, "orchestration_bus_arn", "arn:bus"):
            mock_dl.get_job.return_value = _vams_job(with_token=False)
            cb.lambda_handler({"detail": _run_status("RUNNING")}, MagicMock())

            mock_events.put_events.assert_not_called()
            mock_sfn.send_task_success.assert_not_called()
            mock_sfn.send_task_failure.assert_not_called()

    def test_a_terminal_lifecycle_failure_still_registers_and_fails_the_token(self):
        """The positive control for the terminal arm: a job that fails at CREATE never reaches a
        task-run status, so the lifecycle failure is the only thing that resolves its token."""
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client") as mock_sfn, \
             patch.object(cb, "events_client") as mock_events, \
             patch.object(cb, "orchestration_bus_arn", "arn:bus"):
            mock_dl.get_job.return_value = _vams_job()
            cb.lambda_handler({"detail": _lifecycle("CREATE_FAILED")}, MagicMock())

            mock_events.put_events.assert_called_once()
            mock_sfn.send_task_failure.assert_called_once()
            cause = json.loads(mock_sfn.send_task_failure.call_args.kwargs["cause"])
            assert cause["status"] == "CREATE_FAILED"
            assert (cause["farmId"], cause["queueId"], cause["jobId"]) == (FARM, QUEUE, JOB)

    def test_an_unknown_lifecycle_status_registers_without_resolving_the_token(self):
        """A lifecycle state this lambda does not classify as a failure (UPLOAD_IN_PROGRESS) is
        in-flight, not terminal: register it, and leave the token for the task-run event."""
        with patch.object(cb, "deadline_client") as mock_dl, \
             patch.object(cb, "sfn_client") as mock_sfn, \
             patch.object(cb, "events_client") as mock_events, \
             patch.object(cb, "orchestration_bus_arn", "arn:bus"):
            mock_dl.get_job.return_value = _vams_job()
            cb.lambda_handler({"detail": _lifecycle("UPLOAD_IN_PROGRESS")}, MagicMock())

            mock_events.put_events.assert_called_once()
            mock_sfn.send_task_success.assert_not_called()
            mock_sfn.send_task_failure.assert_not_called()
