# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Aborting an execution cancels its Deadline Cloud job even when the job was never REGISTERED.

Why discovery is needed at all. A DeadlineCloud step runs through
`aws-sdk:deadline:createJob.waitForTaskToken`, so Step Functions holds the task token and NOT the job:
`StopExecution` abandons the token and leaves the job on the farm. The abort path therefore cancels
the job explicitly — but it could only cancel a job it had an id for, and the id arrives from the
job-status callback, which Deadline invokes on a status CHANGE. A job that is submitted and then sits
queued with no worker assigned never produces one, so nothing was registered and nothing was
cancelled. Measured on a real farm: abort reported success while the job stayed READY with
`targetTaskRunStatus: None`.

Every submitted job carries the pipeline execution id as the reserved `VamsPipelineExecutionId` job
parameter, so the job can be found from the execution being aborted with no event having occurred.
These tests pin that fallback and, as importantly, pin the cases where it must NOT run.
"""

import os

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.handlers.workflows import executionService as le  # noqa: E402

FARM = "farm-1111"
QUEUE = "queue-2222"
PEXEC = "p1000000000000000000000000000001"
JOB = "job-3333"

# Deadline stores every job parameter as {"string": "<value>"}.
def _job(job_id, pexec_id):
    return {"parameters": {le.DEADLINE_PIPELINE_EXECUTION_PARAMETER: {"string": pexec_id}},
            "jobId": job_id}


def _summaries(*rows):
    """A paginator whose single page carries `rows` as job summaries."""
    paginator = MagicMock()
    paginator.paginate.return_value = [{"jobs": list(rows)}]
    return paginator


def _deadline(paginator, jobs_by_id):
    client = MagicMock()
    client.get_paginator.return_value = paginator
    client.get_job.side_effect = lambda farmId, queueId, jobId: jobs_by_id[jobId]
    return client


@pytest.mark.unit
class TestDiscoverDeadlineJobId:
    def test_finds_the_job_carrying_this_pipeline_execution_id(self):
        client = _deadline(
            _summaries({"jobId": JOB, "taskRunStatus": "READY"}),
            {JOB: _job(JOB, PEXEC)})
        with patch.object(le, "deadline_client", client):
            assert le._discover_deadline_job_id(FARM, QUEUE, PEXEC) == JOB

    def test_ignores_a_job_belonging_to_another_execution(self):
        """The decisive negative: a busy queue holds other executions' jobs, and cancelling one of
        those would abort work the caller never asked to stop."""
        other = "job-other"
        client = _deadline(
            _summaries({"jobId": other, "taskRunStatus": "RUNNING"}),
            {other: _job(other, "p9999999999999999999999999999999")})
        with patch.object(le, "deadline_client", client):
            assert le._discover_deadline_job_id(FARM, QUEUE, PEXEC) == ""

    def test_skips_terminal_jobs_without_reading_them(self):
        """A finished job is not worth a GetJob call, and cancelling it is a no-op."""
        client = _deadline(
            _summaries({"jobId": JOB, "taskRunStatus": "SUCCEEDED"}),
            {JOB: _job(JOB, PEXEC)})
        with patch.object(le, "deadline_client", client):
            assert le._discover_deadline_job_id(FARM, QUEUE, PEXEC) == ""
        client.get_job.assert_not_called()

    def test_a_queued_job_is_still_found(self):
        """READY is the state the whole fallback exists for — submitted, no worker, no status event."""
        client = _deadline(
            _summaries({"jobId": JOB, "taskRunStatus": "READY"}),
            {JOB: _job(JOB, PEXEC)})
        with patch.object(le, "deadline_client", client):
            assert le._discover_deadline_job_id(FARM, QUEUE, PEXEC) == JOB

    def test_one_unreadable_job_does_not_end_the_search(self):
        good = "job-good"
        client = _deadline(
            _summaries({"jobId": "job-bad", "taskRunStatus": "RUNNING"},
                       {"jobId": good, "taskRunStatus": "RUNNING"}),
            {good: _job(good, PEXEC)})

        def _get(farmId, queueId, jobId):
            if jobId == "job-bad":
                raise RuntimeError("AccessDenied on this one job")
            return _job(good, PEXEC)

        client.get_job.side_effect = _get
        with patch.object(le, "deadline_client", client):
            assert le._discover_deadline_job_id(FARM, QUEUE, PEXEC) == good

    def test_the_scan_is_bounded(self):
        """An abort against a busy queue must not turn into an unbounded scan."""
        rows = [{"jobId": f"job-{i}", "taskRunStatus": "RUNNING"}
                for i in range(le.DEADLINE_DISCOVERY_MAX_JOBS + 25)]
        client = _deadline(_summaries(*rows), {})
        client.get_job.side_effect = lambda farmId, queueId, jobId: _job(jobId, "someone-else")
        with patch.object(le, "deadline_client", client):
            assert le._discover_deadline_job_id(FARM, QUEUE, PEXEC) == ""
        assert client.get_job.call_count <= le.DEADLINE_DISCOVERY_MAX_JOBS

    def test_missing_inputs_and_no_client_are_no_ops(self):
        client = _deadline(_summaries({"jobId": JOB, "taskRunStatus": "READY"}),
                           {JOB: _job(JOB, PEXEC)})
        with patch.object(le, "deadline_client", client):
            assert le._discover_deadline_job_id("", QUEUE, PEXEC) == ""
            assert le._discover_deadline_job_id(FARM, "", PEXEC) == ""
            assert le._discover_deadline_job_id(FARM, QUEUE, "") == ""
        with patch.object(le, "deadline_client", None):
            assert le._discover_deadline_job_id(FARM, QUEUE, PEXEC) == ""

    def test_a_listing_failure_is_swallowed(self):
        """Discovery is best effort — a Deadline outage must not fail the whole abort."""
        client = MagicMock()
        client.get_paginator.side_effect = RuntimeError("deadline unavailable")
        with patch.object(le, "deadline_client", client):
            assert le._discover_deadline_job_id(FARM, QUEUE, PEXEC) == ""


@pytest.mark.unit
class TestFarmQueueResolution:
    def test_reads_farm_and_queue_from_the_pipeline_definition(self):
        definition = {"executionConfig": {"executionType": "DeadlineCloud",
                                          "deadlineCloud": {"farmId": FARM, "queueId": QUEUE}}}
        with patch.object(le, "get_pipeline_definition", return_value=definition):
            assert le._deadline_farm_queue_for_pipeline(
                {"pipelineDatabaseId": "db", "pipelineId": "p"}) == (FARM, QUEUE)

    def test_accepts_a_json_encoded_deadline_block(self):
        import json
        definition = {"executionConfig": {"deadlineCloud": json.dumps(
            {"farmId": FARM, "queueId": QUEUE})}}
        with patch.object(le, "get_pipeline_definition", return_value=definition):
            assert le._deadline_farm_queue_for_pipeline(
                {"pipelineDatabaseId": "db", "pipelineId": "p"}) == (FARM, QUEUE)

    def test_a_deleted_pipeline_yields_no_locators_rather_than_raising(self):
        """A pipeline definition can be gone by the time its execution is aborted; the abort must
        still complete and report what it could not reach."""
        with patch.object(le, "get_pipeline_definition", return_value=None):
            assert le._deadline_farm_queue_for_pipeline(
                {"pipelineDatabaseId": "db", "pipelineId": "p"}) == ("", "")
        with patch.object(le, "get_pipeline_definition", side_effect=RuntimeError("gone")):
            assert le._deadline_farm_queue_for_pipeline(
                {"pipelineDatabaseId": "db", "pipelineId": "p"}) == ("", "")
