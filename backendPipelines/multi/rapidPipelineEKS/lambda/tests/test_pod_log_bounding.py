#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests what the multi/rapidPipelineEKS CHECK_JOB operation does with the pod log it reads.

Two separate properties, and each has its own failure mode.

**Size, on the paths that still carry the log.** The pod log is placed in payloads with hard
ceilings: the SendTaskFailure cause (32768 characters) and the lambda's own return value, which Step
Functions holds as state (262144 bytes). Every one of those rejections is raised inside the
callback's own `except`, which logs and returns - so an oversized log does not fail loudly, it leaves
the parent workflow's task token unreleased and the execution reading RUNNING until its taskTimeout
expires. Measured pre-bound: a 3.03 MB pod log produced a cause of 3,072,993 characters against the
32768 limit, and the execution read RUNNING for its full 14400s.

**Absence, on the SUCCEEDED path.** A success carries no pod log at all - not in the callback output
and not in the return body. On a run that worked the log has no diagnostic value in the execution
record, and it is third-party rpdx output going into a durable, broadly readable place; a bounded
tail is not redaction. The log is still FETCHED and written to the function's own log stream, which
is what keeps the `pods/log` RBAC verb exercised and leaves an operator a tail to read. The tests
below assert both halves, because removing the fetch would satisfy the absence half on its own.

The assertions are on the recorded Step Functions call arguments, the returned body and the recorded
logger calls, so they measure what would actually be sent rather than the helper in isolation. Each
absence assertion names a field the payload still carries (`status`, `k8sJobName`) in the same
object, so a lookup that found nothing cannot read as a pass.
"""

import os
import sys
import json
import types
import importlib
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

for k, v in {
    "AWS_DEFAULT_REGION": "us-east-1",
    "AWS_REGION": "us-east-1",
    "EKS_CLUSTER_NAME": "test-cluster",
    "CONTAINER_IMAGE_URI": "123456789012.dkr.ecr.us-east-1.amazonaws.com/rapid-pipeline:latest",
    "KUBERNETES_NAMESPACE": "default",
}.items():
    os.environ.setdefault(k, v)

# The Step Functions ceilings the payloads must fit.
SEND_TASK_SUCCESS_OUTPUT_BYTES = 262144
SEND_TASK_FAILURE_CAUSE_CHARS = 32768

FIRST_MARKER = "RPDX-FIRST-LINE-MARKER"
LAST_MARKER = "RPDX-LAST-LINE-MARKER"


def _huge_log(lines=3000, width=1000, tag="POD"):
    """A pod log far larger than any of the payload ceilings, with its first and last lines marked
    so a test can tell a tail from a head."""
    body = [f"{FIRST_MARKER} {tag} " + "x" * width]
    body += [f"{tag} progress line {i} " + "y" * width for i in range(lines)]
    body += [f"{LAST_MARKER} {tag} " + "z" * width]
    return "\n".join(body)


def _ctx():
    ctx = MagicMock()
    ctx.aws_request_id = "req-test"
    ctx.get_remaining_time_in_millis.return_value = 300000
    return ctx


def _event(**overrides):
    event = {
        "operation": "CHECK_JOB",
        "jobName": "PipelineJobEKS_x",
        "k8sJobName": "rapid-pipeline-job-abcd1234",
        "externalSfnTaskToken": "task-token-value",
        "counter": 3,
        "maxAttempts": 726,
        "startTime": "2026-01-01T00:00:00Z",
    }
    event.update(overrides)
    return event


def _load():
    if "consolidated_handler" in sys.modules:
        return importlib.reload(sys.modules["consolidated_handler"])
    return importlib.import_module("consolidated_handler")


def _run(status, error_logs, pod_logs, event=None):
    """Run CHECK_JOB with the kubernetes reads stubbed to return real text.

    Returns the module together with everything the invocation produced or touched: the handler
    result, the Step Functions client that recorded the callback, the logger that recorded what
    reached the log stream, and the pod-log reader, so a test can assert the fetch happened rather
    than only that its text is absent from the payload.
    """
    mod = _load()
    sfn = MagicMock()
    logger = MagicMock()
    pod_log_reader = MagicMock(return_value=pod_logs)
    with patch.object(mod, "sfn", sfn), \
            patch.object(mod, "logger", logger), \
            patch.object(mod, "check_job_status", return_value=(status, error_logs)), \
            patch.object(mod, "get_pod_logs_for_job", pod_log_reader):
        result = mod.lambda_handler(event or _event(), _ctx())
    return types.SimpleNamespace(
        mod=mod, result=result, sfn=sfn, logger=logger, pod_log_reader=pod_log_reader
    )


def _logged(run):
    """Everything the invocation wrote to its own log stream, as one string."""
    calls = list(run.logger.info.call_args_list) + list(run.logger.warning.call_args_list)
    return "\n".join(str(call.args[0]) for call in calls if call.args)


@pytest.mark.unit
class TestSucceededPathCarriesNoPodLog:
    def test_the_success_callback_output_carries_no_pod_log(self):
        """The disclosure arm, on the payload the parent workflow stores.

        `status` and `k8sJobName` are asserted in the SAME object, so an absent `logs` key cannot be
        an artefact of having looked in the wrong place: a lookup that found nothing would fail those
        first. The marker check covers every OTHER key too, so moving the text rather than dropping
        it is not a pass.
        """
        run = _run("SUCCEEDED", None, _huge_log())
        run.sfn.send_task_success.assert_called_once()
        raw = run.sfn.send_task_success.call_args.kwargs["output"]
        output = json.loads(raw)

        assert output["status"] == "COMPLETED"
        assert output["k8sJobName"] == "rapid-pipeline-job-abcd1234"
        assert "logs" not in output, (
            f"the success callback output still carries a logs key: {sorted(output)}"
        )
        assert LAST_MARKER not in raw and FIRST_MARKER not in raw, (
            "pod log text reached the callback output under some other key"
        )
        assert len(raw.encode("utf-8")) < SEND_TASK_SUCCESS_OUTPUT_BYTES

    def test_the_success_return_body_carries_no_pod_log(self):
        """The same removal in the lambda's return value.

        Both land in the same broadly readable record: this pipeline's state machine sets
        `includeExecutionData: true` and registers its log group as an execution log source, so
        `subProcessEvents` surfaces this body exactly as the parent surfaces the callback output.
        """
        run = _run("SUCCEEDED", None, _huge_log())
        body = run.result["body"]

        assert body["status"] == "COMPLETED"
        assert body["k8sJobName"] == "rapid-pipeline-job-abcd1234"
        assert "logs" not in body, (
            f"the success return body still carries a logs key: {sorted(body)}"
        )
        assert LAST_MARKER not in json.dumps(run.result)
        assert FIRST_MARKER not in json.dumps(run.result)

    def test_the_pod_log_is_still_read_and_written_to_the_log_stream(self):
        """What separates removing the KEY from removing the FETCH.

        Without this, dropping the `get_pod_logs_for_job` call would satisfy both tests above while
        taking away the operator's only copy of the tail and leaving the `pods/log` RBAC verb
        unexercised - which is the reason the live suite checks the log separately from SUCCEEDED.
        """
        run = _run("SUCCEEDED", None, _huge_log())

        run.pod_log_reader.assert_called_once()
        emitted = _logged(run)
        assert LAST_MARKER in emitted, (
            "the pod log tail no longer reaches the function's own log stream"
        )
        assert run.mod.POD_LOG_TRUNCATION_MARKER in emitted
        # A tail, not a head: the start of a multi-megabyte log is what gets dropped.
        assert FIRST_MARKER not in emitted
        assert "rapid-pipeline-job-abcd1234" in emitted, (
            "the tail must name the job it belongs to, or it cannot be matched to a run"
        )

    def test_an_unreadable_pod_log_does_not_fail_the_success_path(self):
        """A `pods/log` denial must not turn a completed conversion into a failure."""
        mod = _load()
        sfn, logger = MagicMock(), MagicMock()
        with patch.object(mod, "sfn", sfn), \
                patch.object(mod, "logger", logger), \
                patch.object(mod, "check_job_status", return_value=("SUCCEEDED", None)), \
                patch.object(mod, "get_pod_logs_for_job",
                             side_effect=RuntimeError("forbidden: pods/log")):
            result = mod.lambda_handler(_event(), _ctx())

        assert result["body"]["status"] == "COMPLETED"
        sfn.send_task_success.assert_called_once()
        assert "forbidden: pods/log" in "\n".join(
            str(call.args[0]) for call in logger.warning.call_args_list if call.args
        )


@pytest.mark.unit
class TestFailedPathBounding:
    def test_failure_cause_fits_the_sendtaskfailure_ceiling(self):
        run = _run("FAILED", _huge_log(tag="STATUS"), _huge_log(tag="POD"))
        run.sfn.send_task_failure.assert_called_once()
        cause = run.sfn.send_task_failure.call_args.kwargs["cause"]
        assert len(cause) <= SEND_TASK_FAILURE_CAUSE_CHARS
        assert LAST_MARKER in cause

    def test_failure_return_body_fits_the_state_ceiling(self):
        run = _run("FAILED", _huge_log(tag="STATUS"), _huge_log(tag="POD"))
        assert len(json.dumps(run.result).encode("utf-8")) < SEND_TASK_SUCCESS_OUTPUT_BYTES
        assert run.result["body"]["status"] == "FAILED"
        # The size assertion above only means something because this path still carries the log.
        assert LAST_MARKER in run.result["body"]["logs"]

    def test_the_status_error_is_bounded_when_no_pod_log_is_available(self):
        # check_job_status embeds the whole pod log in the error string it returns, so that source is
        # oversized on its own even when the second fetch finds nothing.
        run = _run("FAILED", _huge_log(tag="STATUS"), "No pods found for job")
        cause = run.sfn.send_task_failure.call_args.kwargs["cause"]
        assert len(cause) <= SEND_TASK_FAILURE_CAUSE_CHARS
        assert "STATUS" in run.result["body"]["logs"]

    def test_a_failure_still_carries_the_tail_an_operator_needs(self):
        """The paired arm for the success-path removal: failure diagnostics must not be stripped too.

        This is the whole reason the removal is scoped to SUCCEEDED. On a failure the bounded tail IS
        the execution's error text, and it has to reach both the callback cause and the record.
        """
        run = _run("FAILED", _huge_log(tag="STATUS"), _huge_log(tag="POD"))

        cause = run.sfn.send_task_failure.call_args.kwargs["cause"]
        assert LAST_MARKER in cause
        assert LAST_MARKER in run.result["body"]["logs"]
        assert run.result["body"]["error"]["Error"] == "JobExecutionFailed"


@pytest.mark.unit
class TestUnknownPathBounding:
    def test_unknown_status_logs_are_bounded(self):
        run = _run("UNKNOWN", _huge_log(tag="STATUS"), "")
        assert len(json.dumps(run.result).encode("utf-8")) < SEND_TASK_SUCCESS_OUTPUT_BYTES
        assert run.result["body"]["status"] == "UNKNOWN"
        assert LAST_MARKER in run.result["body"]["logs"]


@pytest.mark.unit
class TestBoundedLogTail:
    def test_a_log_within_the_bounds_passes_through_unchanged(self):
        # Control: the bound must not rewrite an ordinary log, or every assertion above would be
        # satisfied by a helper that simply discarded its input.
        mod = _load()
        small = "\n".join(f"line {i}" for i in range(20))
        assert mod.bounded_log_tail(small) == small
        assert mod.POD_LOG_TRUNCATION_MARKER not in mod.bounded_log_tail(small)

    def test_a_single_enormous_line_is_capped(self):
        # rpdx writes progress with carriage returns rather than newlines, so a whole run can arrive
        # as one line. A line-count bound alone would pass it through intact.
        mod = _load()
        one_line = "q" * 5_000_000
        bounded = mod.bounded_log_tail(one_line)
        assert len(bounded) <= len(mod.POD_LOG_TRUNCATION_MARKER) + mod.POD_LOG_TAIL_MAX_CHARS
        assert bounded.startswith(mod.POD_LOG_TRUNCATION_MARKER)

    def test_empty_and_absent_logs_are_returned_as_given(self):
        mod = _load()
        assert mod.bounded_log_tail("") == ""
        assert mod.bounded_log_tail(None) is None

    def test_the_bound_leaves_headroom_under_the_tighter_ceiling(self):
        # The same bounded text is placed in the SendTaskFailure cause, which is the tighter of the
        # two ceilings, alongside a fixed prefix naming the attempt count.
        mod = _load()
        worst_case = len(mod.POD_LOG_TRUNCATION_MARKER) + mod.POD_LOG_TAIL_MAX_CHARS
        assert worst_case < SEND_TASK_FAILURE_CAUSE_CHARS
