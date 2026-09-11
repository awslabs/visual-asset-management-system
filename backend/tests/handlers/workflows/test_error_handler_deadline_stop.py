# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A failure that reconciles a run cancels its Deadline Cloud job, the same way abort does.

There are two paths that stop a pipeline's registered sub-processes, and they are separate code:
`executionService._abort_registered_sub_process` (the abort API) and
`executionOutputs.stop_registered_sub_process` (the error handler, reached through
`mark_inflight_pipelines_terminal`). Only the first knew what a `deadlineCloudJob` was, so a workflow
that FAILED — a later step erroring, a state timing out, another parallel branch throwing — stamped its
pipeline rows terminal while the farm job kept rendering. A terminal row is no longer a candidate for
the abort API, so at that point the job has no in-product remedy at all.

The registration only reaches that arm because the job-status callback now registers on every status,
not only a terminal one; these cases pin the consumer end of that contract. Each one is written so it
fails against the pre-fix module rather than against the generic "resource type not supported" message
the entry used to fall through to — that message also named the job id, so asserting the id alone would
have passed before the arm existed.
"""

import contextlib
import os
import json

import pytest
from unittest.mock import MagicMock, patch

# Sentinel: "this test did not opt into a pipeline-definition reader", which is different from
# "the reader returns None".
_UNSET = object()

# Env vars the error-handler lambda reads at import time (the root conftest seeds these too).
for _k, _v in {
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "WORKFLOW_EXECUTION_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:vams-wf:*",
}.items():
    os.environ.setdefault(_k, _v)

from backend.backend.common.workflows import executionOutputs as eo  # noqa: E402
from backend.backend.handlers.workflows.sfn import handleExecutionError as heh  # noqa: E402

BODY = {"workflowExecutionId": "EXEC1", "workflowDatabaseId": "wdb", "workflowId": "wf"}
ERROR_INFO = {"Error": "States.Timeout", "Cause": json.dumps({"errorMessage": "step timed out"})}

FARM = "farm-1111"
QUEUE = "queue-2222"
JOB = "job-3333"

# The locator the job-status callback registers, keyed by the resourceType the producer writes.
DEADLINE_SUB = {"resourceType": eo.RESOURCE_TYPE_DEADLINE_CLOUD_JOB,
                "farmId": FARM, "queueId": QUEUE, "jobId": JOB}


def _row(*subs, status="RUNNING", pipeline_execution_id="P1"):
    return {"pipelineExecutionId": pipeline_execution_id, "workflowExecutionId": "EXEC1",
            "executionStatus": status, "registeredSubExecutions": [dict(s) for s in subs]}


def _reconcile(rows, deadline_client, log_row_sink=None, definition=_UNSET):
    """Run the error handler over `rows` with `deadline_client` injected. Returns the pipeline-log
    Item written for the in-flight row (the only in-product record of what was left running).

    `definition` replaces the pipeline-definition reader the handler hands to the shared stop path,
    which is where a DeadlineCloud row's farm and queue come from. Left unset it is not patched at
    all, so a test that does not opt in cannot accidentally depend on one."""
    logs_table = log_row_sink or MagicMock()
    pexec_table = MagicMock()
    main_table = MagicMock(query=MagicMock(return_value={"Items": [{"executionStatus": "RUNNING"}]}))

    def _table(name):
        if name == heh.pipeline_execution_logs_table:
            return logs_table
        if name == heh.pipeline_executions_table:
            return pexec_table
        return main_table

    with contextlib.ExitStack() as patches:
        for context in (
            patch.object(heh, "_get_pipeline_rows", return_value=rows),
            patch.object(heh, "deadline_client", deadline_client),
            patch.object(heh.eo, "finalize_main_row", MagicMock()),
            patch.object(heh, "_fetch_execution_log", return_value=""),
            patch.object(heh.dynamodb, "Table", side_effect=_table),
        ):
            patches.enter_context(context)
        if definition is not _UNSET:
            patches.enter_context(
                patch.object(heh, "get_pipeline_definition", return_value=definition))
        response = heh.lambda_handler({"body": BODY, "errorInfo": ERROR_INFO}, MagicMock())

    assert response == {"handled": True}
    return logs_table, pexec_table


def _error_log(logs_table):
    return logs_table.put_item.call_args.kwargs["Item"]["errorLog"]


def _client_error(code):
    import botocore.exceptions
    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": code}}, "UpdateJob")


def mark_terminal_positional_names():
    """The parameter names of `mark_inflight_pipelines_terminal`, in declaration order."""
    import inspect
    return list(inspect.signature(eo.mark_inflight_pipelines_terminal).parameters)


@pytest.mark.unit
class TestTheErrorHandlerCancelsARegisteredFarmJob:
    def test_the_job_is_cancelled_and_the_row_still_goes_terminal(self):
        """UpdateJob addresses a job by farm + queue + job together, so the whole triple is asserted:
        a call carrying only the job id cancels nothing and raises no error."""
        deadline = MagicMock()
        logs_table, pexec_table = _reconcile([_row(DEADLINE_SUB)], deadline)

        assert deadline.update_job.call_args.kwargs == {
            "farmId": FARM, "queueId": QUEUE, "jobId": JOB,
            "targetTaskRunStatus": "CANCELED"}
        # Nothing was left running, so the log row carries no warning about this job.
        assert JOB not in _error_log(logs_table)
        # The stop does not cost the reconciliation: the row is still stamped FAILED afterwards.
        assert pexec_table.update_item.call_args.kwargs[
            "ExpressionAttributeValues"][":st"] == "FAILED"

    def test_a_row_that_already_finished_keeps_its_job(self):
        """The bound on the arm. An already-terminal pipeline row's job belongs to a run that ended;
        cancelling it here would kill a job the next step is legitimately still consuming."""
        deadline = MagicMock()
        _reconcile([_row(DEADLINE_SUB, status="SUCCEEDED", pipeline_execution_id="P0")], deadline)
        deadline.update_job.assert_not_called()

    def test_a_job_already_finished_on_the_farm_is_not_reported_as_left_running(self):
        """Deadline Cloud rejects the transition once every task is done, and a failure racing a job
        that just completed is normal — reporting it would name work that no longer exists."""
        deadline = MagicMock()
        deadline.update_job.side_effect = _client_error("ConflictException")
        logs_table, _ = _reconcile([_row(DEADLINE_SUB)], deadline)
        assert JOB not in _error_log(logs_table)

    def test_a_real_cancel_failure_names_the_job_and_its_cause(self):
        """A missing deadline:UpdateJob grant is the realistic case, and it is exactly the one that
        must not read as a clean reconciliation."""
        deadline = MagicMock()
        deadline.update_job.side_effect = _client_error("AccessDeniedException")
        logs_table, _ = _reconcile([_row(DEADLINE_SUB)], deadline)
        error_log = _error_log(logs_table)
        assert JOB in error_log
        assert "AccessDeniedException" in error_log
        assert "may still be running on the farm" in error_log


@pytest.mark.unit
class TestUnaddressableAndUnsupportedJobs:
    def test_a_registration_with_no_farm_or_queue_says_so(self):
        """`not supported` is the message this entry used to fall through to, and it is wrong here:
        the type IS supported, this particular registration just cannot address a job."""
        deadline = MagicMock()
        partial = {"resourceType": eo.RESOURCE_TYPE_DEADLINE_CLOUD_JOB, "jobId": JOB}
        logs_table, _ = _reconcile([_row(partial)], deadline)
        error_log = _error_log(logs_table)
        assert JOB in error_log
        assert "names no farm or queue" in error_log
        assert "not supported" not in error_log
        deadline.update_job.assert_not_called()

    def test_a_deployment_with_no_deadline_client_names_the_job(self):
        """The client is built in a try/except and is None where the service does not resolve. Silence
        would report a clean reconciliation while the farm job kept rendering."""
        logs_table, _ = _reconcile([_row(DEADLINE_SUB)], None)
        error_log = _error_log(logs_table)
        assert JOB in error_log
        assert "not available in this deployment" in error_log

    def test_a_type_with_no_stop_api_is_still_reported_as_left_running(self):
        """Positive control: adding the Deadline arm must not swallow the fallback that reports the
        resource types which genuinely have no stop API."""
        logs_table, _ = _reconcile(
            [_row({"resourceType": "ecsTask", "taskArn": "arn:task:9"})], MagicMock())
        error_log = _error_log(logs_table)
        assert "arn:task:9" in error_log
        assert "not supported" in error_log


DEFINITION = {"executionConfig": {"executionType": "DeadlineCloud",
                                  "deadlineCloud": {"farmId": FARM, "queueId": QUEUE}}}


def _deadline_row(*subs, status="RUNNING", pipeline_execution_id="P1"):
    """A row for a DeadlineCloud pipeline. `pipelineExecutionType` is what gates the discovery
    pass, and `pipelineDatabaseId`/`pipelineId` are what address the definition the farm and queue
    are read from."""
    row = _row(*subs, status=status, pipeline_execution_id=pipeline_execution_id)
    row.update({"pipelineExecutionType": "DeadlineCloud",
                "pipelineDatabaseId": "pdb", "pipelineId": "p1"})
    return row


def _summaries(*rows):
    """A Deadline client whose single ListJobs page carries `rows` as job summaries."""
    paginator = MagicMock()
    paginator.paginate.return_value = [{"jobs": list(rows)}]
    client = MagicMock()
    client.get_paginator.return_value = paginator
    return client


def _job(job_id, pipeline_execution_id):
    # Deadline stores every job parameter as {"string": "<value>"}.
    return {"jobId": job_id,
            "parameters": {eo.DEADLINE_PIPELINE_EXECUTION_PARAMETER:
                           {"string": pipeline_execution_id}}}


@pytest.mark.unit
class TestAFailureReachesAJobThatWasNeverRegistered:
    """The gap the registration-driven arm above cannot close.

    Registration comes from the job-status callback, which Deadline invokes on a status CHANGE. A job
    that is submitted and then sits queued with no worker assigned never produces one, so the row
    carries no `deadlineCloudJob` entry and there is no id to cancel — while the job is on the farm
    and about to be forgotten, because this pass stamps the row terminal. The abort API grew a
    discovery fallback for exactly this; these cases pin that the failure path uses the same one.
    """

    def test_an_unregistered_job_is_found_by_the_execution_id_it_carries(self):
        deadline = _summaries({"jobId": JOB, "taskRunStatus": "READY"})
        deadline.get_job.return_value = _job(JOB, "P1")
        logs_table, pexec_table = _reconcile([_deadline_row()], deadline, definition=DEFINITION)

        assert deadline.update_job.call_args.kwargs == {
            "farmId": FARM, "queueId": QUEUE, "jobId": JOB,
            "targetTaskRunStatus": "CANCELED"}
        # Cancelled, so nothing is reported as left running, and the row still goes terminal.
        assert JOB not in _error_log(logs_table)
        assert pexec_table.update_item.call_args.kwargs[
            "ExpressionAttributeValues"][":st"] == "FAILED"

    def test_another_executions_job_on_the_same_queue_is_left_alone(self):
        """The decisive negative control. Without it, "the job got cancelled" also passes for code
        that cancels whatever is newest on the queue — and the queue is shared by every execution
        the pipeline has ever run."""
        other = "job-belongs-to-someone-else"
        deadline = _summaries({"jobId": other, "taskRunStatus": "RUNNING"})
        deadline.get_job.return_value = _job(other, "P-OTHER")
        _reconcile([_deadline_row()], deadline, definition=DEFINITION)
        deadline.update_job.assert_not_called()

    def test_a_finished_job_is_not_read_or_cancelled(self):
        """A terminal task-run status means there is nothing to cancel, and skipping it before the
        GetJob keeps the read count proportional to what is actually running."""
        deadline = _summaries({"jobId": JOB, "taskRunStatus": "SUCCEEDED"})
        deadline.get_job.return_value = _job(JOB, "P1")
        _reconcile([_deadline_row()], deadline, definition=DEFINITION)
        deadline.get_job.assert_not_called()
        deadline.update_job.assert_not_called()

    def test_a_row_that_already_holds_a_registration_is_not_searched_again(self):
        """The registration-driven arm has already cancelled this job; a second pass would find the
        same job and issue a second cancel for it."""
        deadline = _summaries({"jobId": JOB, "taskRunStatus": "READY"})
        deadline.get_job.return_value = _job(JOB, "P1")
        _reconcile([_deadline_row(DEADLINE_SUB)], deadline, definition=DEFINITION)
        deadline.get_paginator.assert_not_called()
        assert [c.kwargs["jobId"] for c in deadline.update_job.call_args_list] == [JOB]

    def test_a_row_of_another_execution_type_is_never_searched(self):
        """The gate. Discovery costs a queue walk per row, and only a DeadlineCloud pipeline can
        have submitted a farm job at all."""
        deadline = _summaries({"jobId": JOB, "taskRunStatus": "READY"})
        _reconcile([_row()], deadline, definition=DEFINITION)
        deadline.get_paginator.assert_not_called()

    def test_an_unresolvable_farm_or_queue_is_reported_on_the_row(self):
        """A deleted pipeline definition leaves nothing to address a job with. Reported rather than
        skipped: this row is about to be stamped terminal, after which nothing looks at it again."""
        deadline = _summaries({"jobId": JOB, "taskRunStatus": "READY"})
        logs_table, _ = _reconcile([_deadline_row()], deadline, definition=None)
        error_log = _error_log(logs_table)
        assert "could not resolve the Deadline farm or queue" in error_log
        assert "P1" in error_log
        deadline.get_paginator.assert_not_called()

    def test_a_cancel_failure_on_a_discovered_job_names_it(self):
        """A missing grant is the realistic failure, and it must not read as a clean
        reconciliation."""
        deadline = _summaries({"jobId": JOB, "taskRunStatus": "READY"})
        deadline.get_job.return_value = _job(JOB, "P1")
        deadline.update_job.side_effect = _client_error("AccessDeniedException")
        logs_table, _ = _reconcile([_deadline_row()], deadline, definition=DEFINITION)
        error_log = _error_log(logs_table)
        assert JOB in error_log
        assert "AccessDeniedException" in error_log
        assert "may still be running on the farm" in error_log

    def test_the_queue_walk_is_bounded(self):
        """A failure on a deployment with a busy queue must not turn into an unbounded scan. The
        bound is logged when it is hit; it does not become row text, because the abort path it
        shares this implementation with does not report it either."""
        rows = [{"jobId": f"job-{i}", "taskRunStatus": "RUNNING"}
                for i in range(eo.DEADLINE_DISCOVERY_MAX_JOBS + 25)]
        deadline = _summaries(*rows)
        deadline.get_job.side_effect = lambda farmId, queueId, jobId: _job(jobId, "P-OTHER")
        _reconcile([_deadline_row()], deadline, definition=DEFINITION)
        assert deadline.get_job.call_count <= eo.DEADLINE_DISCOVERY_MAX_JOBS
        deadline.update_job.assert_not_called()

    def test_a_deployment_with_no_deadline_client_does_not_search(self):
        """The client is None where the service does not resolve, and the pass must not report a
        farm job it has no way of knowing exists."""
        logs_table, _ = _reconcile([_deadline_row()], None, definition=DEFINITION)
        assert "may still be running on the farm" not in _error_log(logs_table)


@pytest.mark.unit
class TestTheClientIsThreadedIntoTheStopPath:
    def test_the_handler_hands_its_deadline_client_to_the_stop_path(self):
        """The wiring, pinned separately: the arm exists in the shared helper, but without this kwarg
        every case above degrades to 'reported, not cancelled' with no error anywhere."""
        deadline = MagicMock()
        mark_terminal = MagicMock(return_value=[])
        main_table = MagicMock(query=MagicMock(return_value={"Items": []}))

        with patch.object(heh, "_get_pipeline_rows", return_value=[_row(DEADLINE_SUB)]), \
             patch.object(heh, "deadline_client", deadline), \
             patch.object(heh.eo, "mark_inflight_pipelines_terminal", mark_terminal), \
             patch.object(heh.eo, "finalize_main_row", MagicMock()), \
             patch.object(heh, "_fetch_execution_log", return_value=""), \
             patch.object(heh.dynamodb, "Table", return_value=main_table):
            heh.lambda_handler({"body": BODY, "errorInfo": ERROR_INFO}, MagicMock())

        mark_terminal.assert_called_once()
        assert mark_terminal.call_args.kwargs["deadline_client"] is deadline
        # The definition reader travels the same hop. Without it the shared helper resolves ("", "")
        # for every DeadlineCloud row and reports each one as possibly still running -- which reads
        # as a Deadline problem and is a missing kwarg.
        assert (mark_terminal.call_args.kwargs["get_pipeline_definition"]
                is heh.get_pipeline_definition)

    def test_every_parameter_the_shared_helper_gained_is_keyword_only_at_the_call_site(self):
        """`mark_inflight_pipelines_terminal`'s positional arguments are pinned elsewhere by INDEX
        (test_stage2_interim_outputs asserts `args[3] == "FAILED"`), so a parameter inserted rather
        than appended silently moves that assertion onto another value."""
        assert mark_terminal_positional_names()[:5] == [
            "dynamo", "pipeline_executions_table", "pipeline_rows", "status", "stop_date"]

    def test_the_shared_helper_walks_rows_with_the_client(self):
        """`stop_registered_sub_processes` is the hop between the handler and the per-entry arm; a
        kwarg dropped there is invisible to both of the tests either side of it."""
        deadline = MagicMock()
        rows = [_row(DEADLINE_SUB), _row(dict(DEADLINE_SUB, jobId="job-4444"), status="FAILED")]
        assert eo.stop_registered_sub_processes(rows, deadline_client=deadline) == []
        assert [c.kwargs["jobId"] for c in deadline.update_job.call_args_list] == [JOB]
