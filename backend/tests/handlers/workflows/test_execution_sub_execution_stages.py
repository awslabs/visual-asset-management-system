# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-stage status of a registered Step Functions sub-execution, derived at read time.

The pure module walks a state machine definition into an ordered stage frame and folds the execution
history onto it; the executionService wiring memoises DescribeStateMachine per invocation, pages
GetExecutionHistory under two caps, summarises sub-executions with DescribeExecution / DescribeJobs, and
never lets one of those calls fail the request."""

import json
import os
from datetime import datetime, timezone

import botocore
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

from backend.backend.common.workflows import subExecutionStages as ses  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"
T0 = datetime(2026, 9, 11, 10, 0, 0, tzinfo=timezone.utc)


def _ts(seconds):
    """A history timestamp `seconds` after T0 (boto3 returns aware datetimes)."""
    return datetime.fromtimestamp(T0.timestamp() + seconds, tz=timezone.utc)


def _iso(seconds):
    return _ts(seconds).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.unit
class TestIsoUtc:
    def test_datetime_and_epoch_millis_render_the_same_instant(self):
        assert ses.iso_utc(T0) == "2026-09-11T10:00:00Z"
        assert ses.iso_utc(int(T0.timestamp() * 1000)) == "2026-09-11T10:00:00Z"

    def test_empty_values_render_empty(self):
        assert ses.iso_utc(None) == "" and ses.iso_utc("") == ""

    def test_a_naive_datetime_is_read_as_utc(self):
        assert ses.iso_utc(datetime(2026, 9, 11, 10, 0, 0)) == "2026-09-11T10:00:00Z"


@pytest.mark.unit
class TestArnAndBatchHelpers:
    def test_state_machine_arn_is_derived_from_an_execution_arn(self):
        assert ses.state_machine_arn_from_execution_arn(
            "arn:aws:states:us-west-2:123456789012:execution:VAMSPreview3d-abc:run-1") == \
            "arn:aws:states:us-west-2:123456789012:stateMachine:VAMSPreview3d-abc"
        assert ses.state_machine_arn_from_execution_arn("") == ""
        assert ses.state_machine_arn_from_execution_arn("arn:aws:states:us-west-2:1:stateMachine:x") == ""

    def test_batch_submit_job_is_recognised_from_history_details_and_from_the_definition(self):
        assert ses.is_batch_submit_job("submitJob.sync", "batch") is True
        assert ses.is_batch_submit_job("arn:aws:states:::batch:submitJob.sync") is True
        assert ses.is_batch_submit_job("arn:aws-us-gov:states:::batch:submitJob.sync") is True
        assert ses.is_batch_submit_job("invoke", "lambda") is False
        assert ses.is_batch_submit_job("arn:aws:states:::lambda:invoke") is False

    def test_job_id_and_stream_are_read_from_pascal_case_sync_output(self):
        submitted = json.dumps({"JobArn": "arn:x", "JobId": "job-1", "JobName": "n"})
        assert ses.batch_job_id_from_output(submitted) == "job-1"
        succeeded = json.dumps({"JobId": "job-1", "Status": "SUCCEEDED",
                                "Container": {"LogStreamName": "jd/default/task-1"}})
        assert ses.batch_log_stream_from_job(succeeded) == "jd/default/task-1"

    def test_stream_falls_back_to_the_last_attempt(self):
        job = {"Attempts": [{"Container": {"LogStreamName": "jd/default/a1"}},
                            {"Container": {"LogStreamName": "jd/default/a2"}}]}
        assert ses.batch_log_stream_from_job(job) == "jd/default/a2"

    def test_stream_is_read_from_camel_case_describe_jobs(self):
        job = {"jobId": "job-1", "status": "FAILED", "container": {"logStreamName": "jd/default/t9"}}
        assert ses.batch_log_stream_from_job(job) == "jd/default/t9"
        assert ses.batch_log_stream_from_job({"attempts": [{"container": {"logStreamName": "jd/default/x"}}]}) \
            == "jd/default/x"

    def test_unparseable_or_foreign_payloads_yield_empty(self):
        assert ses.batch_log_stream_from_job("not json") == ""
        assert ses.batch_log_stream_from_job(None) == ""
        assert ses.batch_log_stream_from_job(json.dumps({"errorMessage": "boom"})) == ""
        assert ses.batch_job_id_from_output(json.dumps(["a"])) == ""

    @pytest.mark.parametrize("raw,mapped", [
        ("SUBMITTED", "RUNNING"), ("PENDING", "RUNNING"), ("RUNNABLE", "RUNNING"),
        ("STARTING", "RUNNING"), ("RUNNING", "RUNNING"), ("SUCCEEDED", "SUCCEEDED"),
        ("FAILED", "FAILED"), ("", "UNKNOWN"), ("WEIRD", "UNKNOWN")])
    def test_batch_statuses_map_onto_the_execution_vocabulary(self, raw, mapped):
        assert ses.map_batch_status(raw) == mapped


def _task(next_state=None, resource="arn:aws:states:::lambda:invoke", end=False):
    state = {"Type": "Task", "Resource": resource}
    if end:
        state["End"] = True
    else:
        state["Next"] = next_state
    return state


THUMBNAIL_LIKE = {
    "StartAt": "Prepare",
    "States": {
        "Prepare": _task("Preview3dThumbnailBatchJob"),
        "Preview3dThumbnailBatchJob": {
            "Type": "Task", "Resource": "arn:aws:states:::batch:submitJob.sync",
            "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "HandleBatchError", "ResultPath": "$.error"}],
            "Next": "PipelineEndTask"},
        "HandleBatchError": {"Type": "Pass", "Next": "PipelineEndTask"},
        "PipelineEndTask": _task("Job Complete?"),
        "Job Complete?": {"Type": "Choice",
                          "Choices": [{"Variable": "$.ok", "BooleanEquals": True, "Next": "Done"}],
                          "Default": "FailState"},
        "FailState": {"Type": "Fail"},
        "Done": {"Type": "Succeed"},
    },
}


@pytest.mark.unit
class TestStagesFromDefinition:
    def test_a_linear_chain_with_a_choice_is_walked_in_order(self):
        stages, truncated = ses.stages_from_definition(THUMBNAIL_LIKE)
        # Next is queued before a Catch target; a Choice queues its Default before its Choices.
        assert [s["stageName"] for s in stages] == [
            "Prepare", "Preview3dThumbnailBatchJob", "PipelineEndTask", "HandleBatchError",
            "Job Complete?", "FailState", "Done"]
        assert truncated is False
        by_name = {s["stageName"]: s for s in stages}
        assert by_name["Preview3dThumbnailBatchJob"]["stateType"] == "Task"
        assert by_name["Preview3dThumbnailBatchJob"]["resource"] == "arn:aws:states:::batch:submitJob.sync"
        assert by_name["Job Complete?"]["stateType"] == "Choice"
        assert "resource" not in by_name["FailState"]
        assert all(s["depth"] == 0 for s in stages)

    def test_catch_targets_are_reached_through_catch_next(self):
        # HandleBatchError is reachable only through the Catch; it must still be a stage.
        stages, _ = ses.stages_from_definition(THUMBNAIL_LIKE)
        assert "HandleBatchError" in [s["stageName"] for s in stages]

    def test_parallel_branches_and_map_processors_are_nested_with_depth_and_parent(self):
        definition = {
            "StartAt": "Fan",
            "States": {
                "Fan": {"Type": "Parallel", "Next": "Each",
                        "Branches": [
                            {"StartAt": "A1", "States": {"A1": _task(end=True)}},
                            {"StartAt": "B1", "States": {"B1": _task("B2"), "B2": _task(end=True)}}]},
                "Each": {"Type": "Map", "End": True,
                         "ItemProcessor": {"StartAt": "Item", "States": {"Item": _task(end=True)}}},
            },
        }
        stages, truncated = ses.stages_from_definition(definition)
        assert [(s["stageName"], s["depth"], s.get("parent", "")) for s in stages] == [
            ("Fan", 0, ""), ("A1", 1, "Fan"), ("B1", 1, "Fan"), ("B2", 1, "Fan"),
            ("Each", 0, ""), ("Item", 1, "Each")]
        assert truncated is False

    def test_a_legacy_map_iterator_is_walked_like_an_item_processor(self):
        definition = {"StartAt": "M", "States": {
            "M": {"Type": "Map", "End": True,
                  "Iterator": {"StartAt": "I", "States": {"I": _task(end=True)}}}}}
        stages, _ = ses.stages_from_definition(definition)
        assert [s["stageName"] for s in stages] == ["M", "I"]

    def test_a_choice_loop_terminates(self):
        definition = {"StartAt": "Wait", "States": {
            "Wait": {"Type": "Wait", "Seconds": 5, "Next": "Check"},
            "Check": {"Type": "Choice", "Choices": [{"Variable": "$.done", "BooleanEquals": True,
                                                     "Next": "Done"}], "Default": "Wait"},
            "Done": {"Type": "Succeed"}}}
        stages, truncated = ses.stages_from_definition(definition)
        assert [s["stageName"] for s in stages] == ["Wait", "Check", "Done"]
        assert truncated is False

    def test_the_stage_cap_truncates(self):
        states = {f"S{i}": _task(f"S{i + 1}") for i in range(60)}
        states["S60"] = _task(end=True)
        stages, truncated = ses.stages_from_definition({"StartAt": "S0", "States": states}, max_stages=50)
        assert len(stages) == 50 and truncated is True

    def test_the_depth_cap_truncates(self):
        inner = {"StartAt": "L3", "States": {"L3": _task(end=True)}}
        definition = {"StartAt": "L0", "States": {"L0": {"Type": "Map", "End": True, "ItemProcessor": {
            "StartAt": "L1", "States": {"L1": {"Type": "Map", "End": True, "ItemProcessor": {
                "StartAt": "L2", "States": {"L2": {"Type": "Map", "End": True, "ItemProcessor": {
                    "StartAt": "L2b", "States": {"L2b": {"Type": "Map", "End": True,
                                                        "ItemProcessor": inner}}}}}}}}}}}}
        stages, truncated = ses.stages_from_definition(definition, max_depth=3)
        assert [s["stageName"] for s in stages] == ["L0", "L1", "L2", "L2b"]
        assert truncated is True

    @pytest.mark.parametrize("definition", [None, {}, {"StartAt": "X"}, {"States": {}}, "not a dict"])
    def test_an_unusable_definition_yields_no_stages(self, definition):
        assert ses.stages_from_definition(definition) == ([], False)


class _History:
    """Builds a GetExecutionHistory event list with consistent ids and previousEventId links."""

    def __init__(self):
        self.events = []

    def add(self, etype, seconds, prev=None, **details):
        eid = len(self.events) + 1
        if prev is None:
            prev = eid - 1
        event = {"id": eid, "previousEventId": prev, "type": etype, "timestamp": _ts(seconds)}
        event.update(details)
        self.events.append(event)
        return eid

    def entered(self, kind, name, seconds, prev=None):
        return self.add(f"{kind}StateEntered", seconds, prev, stateEnteredEventDetails={"name": name})

    def exited(self, kind, name, seconds, prev=None):
        return self.add(f"{kind}StateExited", seconds, prev, stateExitedEventDetails={"name": name})


def _frame(*names, types=None):
    types = types or {}
    return [{"stageName": n, "stateType": types.get(n, "Task"), "depth": 0} for n in names]


def _by_name(folded):
    return {s["stageName"]: s for s in folded["stages"]}


# A Step Functions task token is a bearer capability to complete or fail the pending pipeline task.
# When a `batch:submitJob.sync` task fails, its Cause is the DescribeJobs object, whose container
# environment carries the token as a {"Name","Value"} pair — so the Cause must be redacted before it
# is capped, or the cap keeps whatever of the token fits (VAMS #319/#307).
TASK_TOKEN = "AAAAKgAAAAIAAAAAAAAAA" + "b" * 680
REDACTED = "<redacted>"
S3_KEY_CAUSE = '{"Name":"S3_INPUT_KEY","Value":"assets/TASK_TOKEN/model.glb"}'


def _token_cause(env_name="TASK_TOKEN", job_id="j1", stream="isaac-jd/default/abc"):
    """A DescribeJobs-shaped failure Cause carrying the task token in the container environment."""
    return json.dumps({
        "JobId": job_id, "Status": "FAILED",
        "Container": {"Environment": [{"Name": env_name, "Value": TASK_TOKEN}], "LogStreamName": stream},
    }, separators=(",", ":"))


@pytest.mark.unit
class TestFoldHistoryCore:
    def test_a_succeeded_task_reports_dates_and_one_attempt(self):
        h = _History()
        h.add("ExecutionStarted", 0, prev=0)
        h.entered("Task", "Prepare", 1)
        h.add("TaskScheduled", 2, taskScheduledEventDetails={"resource": "invoke", "resourceType": "lambda"})
        h.add("TaskStarted", 3, taskStartedEventDetails={"resource": "invoke", "resourceType": "lambda"})
        h.add("TaskSucceeded", 4, taskSucceededEventDetails={"resource": "invoke", "resourceType": "lambda",
                                                             "output": "{}"})
        h.exited("Task", "Prepare", 5)
        folded = ses.fold_history(h.events, _frame("Prepare", "Later"))
        prepare = _by_name(folded)["Prepare"]
        assert prepare["status"] == "SUCCEEDED"
        assert prepare["startDate"] == _iso(1) and prepare["stopDate"] == _iso(5)
        assert prepare["attempts"] == 1
        assert prepare["error"] == "" and prepare["cause"] == ""
        assert "caught" not in prepare
        assert folded["stagesTruncated"] is False

    def test_a_never_entered_frame_stage_is_not_started(self):
        folded = ses.fold_history([], _frame("Prepare", "Later"))
        assert [(s["stageName"], s["status"]) for s in folded["stages"]] == [
            ("Prepare", "NOT_STARTED"), ("Later", "NOT_STARTED")]
        assert folded["stages"][0]["startDate"] == "" and folded["stages"][0]["attempts"] == 0

    def test_an_open_stage_is_running(self):
        h = _History()
        h.entered("Task", "Prepare", 1, prev=0)
        h.add("TaskScheduled", 2, taskScheduledEventDetails={"resource": "invoke", "resourceType": "lambda"})
        folded = ses.fold_history(h.events, _frame("Prepare"))
        stage = _by_name(folded)["Prepare"]
        assert stage["status"] == "RUNNING" and stage["stopDate"] == ""

    def test_a_caught_failure_exits_normally_and_is_reported_failed_with_caught(self):
        # Every built-in Batch machine catches the Batch error and continues, so the Task state
        # exits normally after TaskFailed; the stage must read FAILED, not SUCCEEDED.
        h = _History()
        h.entered("Task", "Preview3dThumbnailBatchJob", 1, prev=0)
        h.add("TaskScheduled", 2, taskScheduledEventDetails={"resource": "submitJob.sync", "resourceType": "batch"})
        h.add("TaskStarted", 3, taskStartedEventDetails={"resource": "submitJob.sync", "resourceType": "batch"})
        h.add("TaskFailed", 60, taskFailedEventDetails={"resource": "submitJob.sync", "resourceType": "batch",
                                                        "error": "States.TaskFailed", "cause": "exit 1"})
        h.exited("Task", "Preview3dThumbnailBatchJob", 61)
        h.entered("Pass", "HandleBatchError", 62)
        h.exited("Pass", "HandleBatchError", 63)
        h.entered("Fail", "FailState", 64)
        h.add("ExecutionFailed", 65, executionFailedEventDetails={"error": "PipelineFailed", "cause": "x"})
        folded = ses.fold_history(h.events, _frame("Preview3dThumbnailBatchJob", "HandleBatchError", "FailState",
                                                   types={"HandleBatchError": "Pass", "FailState": "Fail"}))
        stages = _by_name(folded)
        batch = stages["Preview3dThumbnailBatchJob"]
        assert batch["status"] == "FAILED" and batch["caught"] is True
        assert batch["error"] == "States.TaskFailed" and batch["cause"] == "exit 1"
        assert batch["stopDate"] == _iso(61)
        assert stages["HandleBatchError"]["status"] == "SUCCEEDED"
        assert stages["FailState"]["status"] == "FAILED" and "caught" not in stages["FailState"]
        assert stages["FailState"]["stopDate"] == _iso(64)

    @pytest.mark.parametrize("failed_type,error,resource", [
        ("TaskTimedOut", "States.Timeout", {"resource": "submitJob.sync", "resourceType": "batch"}),
        ("TaskTimedOut", "States.HeartbeatTimeout", {"resource": "invoke.waitForTaskToken", "resourceType": "lambda"}),
        ("TaskSubmitFailed", "Batch.ClientException", {"resource": "submitJob.sync", "resourceType": "batch"}),
        ("TaskStartFailed", "Batch.AWSBatchException", {"resource": "submitJob.sync", "resourceType": "batch"}),
        ("LambdaFunctionTimedOut", "States.Timeout", {"resource": "arn:aws:lambda:r:a:function:f"}),
    ])
    def test_a_timed_out_or_unsubmitted_task_folds_like_a_failed_one(self, failed_type, error, resource):
        # A task-level TimeoutSeconds/HeartbeatSeconds ends the task with TaskTimedOut and a Batch job
        # that never runs ends it with TaskSubmitFailed/TaskStartFailed; the machines that set those
        # bounds catch the error and exit normally, so the stage must read FAILED with the task's own
        # error, not SUCCEEDED and not the Fail state's.
        is_batch = resource.get("resourceType") == "batch"
        describe_jobs = json.dumps({"JobId": "job-1", "Container": {"LogStreamName": "def/default/abc"}})
        cause = describe_jobs if is_batch else "no heartbeat"
        details_key = failed_type[0].lower() + failed_type[1:] + "EventDetails"
        scheduled = "LambdaFunctionScheduled" if failed_type.startswith("LambdaFunction") else "TaskScheduled"
        h = _History()
        h.entered("Task", "Job", 1, prev=0)
        h.add(scheduled, 2, **{scheduled[0].lower() + scheduled[1:] + "EventDetails": dict(resource)})
        h.add(failed_type, 60, **{details_key: dict(resource, error=error, cause=cause)})
        h.exited("Task", "Job", 61)
        h.entered("Pass", "HandleError", 62)
        h.exited("Pass", "HandleError", 63)
        h.entered("Fail", "FailState", 64)
        h.add("ExecutionFailed", 65, executionFailedEventDetails={"error": "PipelineFailed", "cause": "x"})
        folded = ses.fold_history(h.events, _frame("Job", "HandleError", "FailState",
                                                   types={"HandleError": "Pass", "FailState": "Fail"}))
        stages = _by_name(folded)
        job = stages["Job"]
        assert job["status"] == "FAILED" and job["caught"] is True
        assert job["error"] == error and job["cause"] == cause
        assert job["stopDate"] == _iso(61) and job["attempts"] == 1
        if is_batch:
            assert job["batch"] == {"jobId": "job-1", "logStreamName": "def/default/abc"}
        else:
            assert "batch" not in job
        assert stages["FailState"]["error"] == "PipelineFailed"

    def test_the_prod5_caught_failure_sequence_folds_as_recorded(self):
        # The exact event sequence of a failed thumbnail sub-execution observed live on prod5
        # (PipelineJob_20260905_233540_888_65137590): the container fails the .sync task through
        # SendTaskFailure with its own error text and a PLAIN-STRING cause (not the DescribeJobs
        # object), the machine's Catch routes through HandleBatchError -> PipelineEndTask ->
        # EndStatesChoice -> FailState, and ExecutionFailed closes the run. The Batch stage is FAILED
        # and caught with the container's texts verbatim; the stream is NOT in the history (the
        # SubmitJob result is discarded), so the stage carries only the submitted jobId and
        # _resolve_batch_stage_streams resolves the stream later.
        error_text = "Pipeline Failure: Failed to load 3D file (.glb): incorrect header on GLB file"
        cause_text = "See AWS cloudwatch logs for full error log and cause."
        job_id = "8c1f2e3d-0000-4000-8000-000000000001"
        h = _History()
        h.add("ExecutionStarted", 0, prev=0)
        h.entered("Task", "ConstructPipelineTask", 1)
        h.add("LambdaFunctionScheduled", 1, lambdaFunctionScheduledEventDetails={"resource": "arn:fn"})
        h.add("LambdaFunctionStarted", 2)
        h.add("LambdaFunctionSucceeded", 3, lambdaFunctionSucceededEventDetails={"output": "{}"})
        h.exited("Task", "ConstructPipelineTask", 3)
        h.entered("Task", "Preview3dThumbnailBatchJob", 4)
        h.add("TaskScheduled", 4, taskScheduledEventDetails={"resource": "submitJob.sync", "resourceType": "batch"})
        h.add("TaskStarted", 5, taskStartedEventDetails={"resource": "submitJob.sync", "resourceType": "batch"})
        h.add("TaskSubmitted", 5, taskSubmittedEventDetails={
            "resource": "submitJob.sync", "resourceType": "batch",
            "output": json.dumps({"JobArn": f"arn:aws:batch:us-west-2:123456789012:job/{job_id}",
                                  "JobId": job_id, "JobName": "Preview3dThumbnailJob",
                                  "SdkHttpMetadata": {"HttpStatusCode": 200},
                                  "SdkResponseMetadata": {"RequestId": "r-1"}})})
        h.add("TaskFailed", 95, taskFailedEventDetails={"resource": "submitJob.sync", "resourceType": "batch",
                                                        "error": error_text, "cause": cause_text})
        h.exited("Task", "Preview3dThumbnailBatchJob", 95)
        h.entered("Pass", "HandleBatchError", 96)
        h.exited("Pass", "HandleBatchError", 96)
        h.entered("Task", "PipelineEndTask", 96)
        h.add("LambdaFunctionScheduled", 96, lambdaFunctionScheduledEventDetails={"resource": "arn:fn"})
        h.add("LambdaFunctionStarted", 97)
        h.add("LambdaFunctionSucceeded", 98, lambdaFunctionSucceededEventDetails={"output": "{}"})
        h.exited("Task", "PipelineEndTask", 98)
        h.entered("Choice", "EndStatesChoice", 98)
        h.exited("Choice", "EndStatesChoice", 98)
        h.entered("Fail", "FailState", 99)
        h.add("ExecutionFailed", 99, executionFailedEventDetails={"error": "PipelineFailed",
                                                                  "cause": "Pipeline execution failed"})
        frame = _frame("ConstructPipelineTask", "Preview3dThumbnailBatchJob", "HandleBatchError",
                       "PipelineEndTask", "EndStatesChoice", "SuccessState", "FailState",
                       types={"HandleBatchError": "Pass", "EndStatesChoice": "Choice",
                              "SuccessState": "Succeed", "FailState": "Fail"})
        folded = ses.fold_history(h.events, frame)
        stages = _by_name(folded)
        batch = stages["Preview3dThumbnailBatchJob"]
        assert batch["status"] == "FAILED" and batch["caught"] is True
        assert batch["error"] == error_text and batch["cause"] == cause_text
        assert batch["startDate"] == _iso(4) and batch["stopDate"] == _iso(95)
        assert batch["attempts"] == 1
        # The jobId is the only Batch fact the history carries: no stream, and the non-JSON cause
        # produces neither an exception nor a stand-in value (fold_history has no warning channel and
        # must not need one here).
        assert batch["batch"] == {"jobId": job_id, "logStreamName": ""}
        assert stages["ConstructPipelineTask"]["status"] == "SUCCEEDED"
        assert stages["HandleBatchError"]["status"] == "SUCCEEDED"
        assert stages["PipelineEndTask"]["status"] == "SUCCEEDED"
        assert stages["EndStatesChoice"]["status"] == "SUCCEEDED"
        assert stages["SuccessState"]["status"] == "NOT_STARTED"
        fail = stages["FailState"]
        assert fail["status"] == "FAILED" and "caught" not in fail
        assert fail["stopDate"] == _iso(99)
        # ExecutionFailed is the closer of the sub-execution: after it nothing is left RUNNING, and it
        # never overwrites the error a stage recorded for itself.
        assert h.events[-1]["type"] == "ExecutionFailed"
        assert all(s["status"] != "RUNNING" for s in folded["stages"])
        assert batch["error"] != "PipelineFailed"
        assert folded["stagesTruncated"] is False

    def test_a_retried_task_counts_attempts_and_recovers(self):
        h = _History()
        h.entered("Task", "Convert", 1, prev=0)
        h.add("LambdaFunctionScheduled", 2, lambdaFunctionScheduledEventDetails={"resource": "arn:fn"})
        h.add("LambdaFunctionStarted", 3)
        h.add("LambdaFunctionFailed", 4, lambdaFunctionFailedEventDetails={"error": "Throttled", "cause": "slow"})
        h.add("LambdaFunctionScheduled", 5, lambdaFunctionScheduledEventDetails={"resource": "arn:fn"})
        h.add("LambdaFunctionStarted", 6)
        h.add("LambdaFunctionSucceeded", 7, lambdaFunctionSucceededEventDetails={"output": "{}"})
        h.exited("Task", "Convert", 8)
        stage = _by_name(ses.fold_history(h.events, _frame("Convert")))["Convert"]
        assert stage["status"] == "SUCCEEDED" and stage["attempts"] == 2
        assert stage["error"] == "" and stage["cause"] == "" and "caught" not in stage

    def test_task_events_are_attributed_through_previous_event_id_not_by_recency(self):
        # Two Task states open at once inside a Parallel: the failure belongs to the branch whose
        # TaskStateEntered the previousEventId chain reaches, not to the most recently entered one.
        h = _History()
        par = h.entered("Parallel", "Fan", 1, prev=0)
        a = h.entered("Task", "A", 2, prev=par)
        b = h.entered("Task", "B", 3, prev=par)
        a_sched = h.add("TaskScheduled", 4, prev=a, taskScheduledEventDetails={"resource": "invoke", "resourceType": "lambda"})
        b_sched = h.add("TaskScheduled", 5, prev=b, taskScheduledEventDetails={"resource": "invoke", "resourceType": "lambda"})
        a_start = h.add("TaskStarted", 6, prev=a_sched)
        b_start = h.add("TaskStarted", 7, prev=b_sched)
        h.add("TaskFailed", 8, prev=a_start, taskFailedEventDetails={"error": "E", "cause": "A broke"})
        h.add("TaskSucceeded", 9, prev=b_start, taskSucceededEventDetails={"output": "{}"})
        folded = ses.fold_history(h.events, _frame("Fan", "A", "B", types={"Fan": "Parallel"}))
        stages = _by_name(folded)
        assert stages["A"]["status"] == "RUNNING" and stages["A"]["error"] == "E"
        assert stages["B"]["status"] == "RUNNING" and stages["B"]["error"] == ""

    def test_history_only_states_are_appended_after_the_frame(self):
        h = _History()
        h.entered("Task", "Renamed", 1, prev=0)
        h.exited("Task", "Renamed", 2)
        folded = ses.fold_history(h.events, _frame("Prepare"))
        assert [s["stageName"] for s in folded["stages"]] == ["Prepare", "Renamed"]
        assert _by_name(folded)["Renamed"]["stateType"] == "Task"

    def test_without_a_frame_the_order_is_first_entered(self):
        h = _History()
        h.entered("Pass", "Second", 5, prev=0)
        h.exited("Pass", "Second", 6)
        h.entered("Task", "First", 1)
        h.exited("Task", "First", 2)
        folded = ses.fold_history(h.events, [])
        assert [s["stageName"] for s in folded["stages"]] == ["Second", "First"]

    def test_the_cause_is_capped_after_capture(self):
        h = _History()
        h.entered("Task", "T", 1, prev=0)
        h.add("TaskFailed", 2, taskFailedEventDetails={"error": "E", "cause": "x" * 1000})
        stage = _by_name(ses.fold_history(h.events, _frame("T"), max_error_chars=256))["T"]
        assert len(stage["cause"]) == 256

    @pytest.mark.parametrize("env_name", ["TASK_TOKEN", "VAMS_TASK_TOKEN", "EXTERNAL_SFN_TASK_TOKEN"])
    def test_a_batch_failure_cause_is_parsed_for_its_stream_then_redacted_before_the_cap(self, env_name):
        # The raw Cause is what yields the job id and log stream; the redacted Cause is what a caller
        # sees. Redacting before the cap is what makes the tail of the object survive at all: capped
        # first, the 700-character token would fill the budget and its opening quote would never close.
        h = _History()
        h.entered("Task", "B", 1, prev=0)
        h.add("TaskScheduled", 2, taskScheduledEventDetails={"resource": "submitJob.sync", "resourceType": "batch"})
        h.add("TaskFailed", 3, taskFailedEventDetails={"resource": "submitJob.sync", "resourceType": "batch",
                                                       "error": "States.TaskFailed",
                                                       "cause": _token_cause(env_name)})
        stage = _by_name(ses.fold_history(h.events, _frame("B"), max_error_chars=256))["B"]
        assert stage["batch"] == {"jobId": "j1", "logStreamName": "isaac-jd/default/abc"}
        assert TASK_TOKEN[:32] not in stage["cause"], f"task token leaked behind Name {env_name}"
        assert REDACTED in stage["cause"] and "isaac-jd/default/abc" in stage["cause"]
        assert len(stage["cause"]) <= 256

    def test_an_execution_failed_cause_carrying_a_task_token_is_redacted_for_every_stage_it_closes(self):
        # The closer's Cause is copied onto the raising Fail state and onto each still-open stage.
        h = _History()
        h.entered("Task", "T", 1, prev=0)
        h.entered("Fail", "F", 2)
        h.add("ExecutionFailed", 3, executionFailedEventDetails={"error": "PipelineFailed",
                                                                 "cause": _token_cause()})
        stages = _by_name(ses.fold_history(h.events, _frame("T", "F", types={"F": "Fail"}), max_error_chars=256))
        for name in ("T", "F"):
            assert TASK_TOKEN[:32] not in stages[name]["cause"], f"task token leaked on {name}"
            assert REDACTED in stages[name]["cause"]

    def test_a_map_run_failed_cause_carrying_a_task_token_is_redacted(self):
        h = _History()
        h.entered("Map", "M", 1, prev=0)
        h.add("MapRunStarted", 2, mapRunStartedEventDetails={"mapRunArn": "arn:aws:states:r:a:mapRun:M/x"})
        h.add("MapRunFailed", 3, mapRunFailedEventDetails={"error": "States.ExceedToleratedFailureThreshold",
                                                           "cause": _token_cause()})
        stage = _by_name(ses.fold_history(h.events, _frame("M", types={"M": "Map"}), max_error_chars=256))["M"]
        assert TASK_TOKEN[:32] not in stage["cause"] and REDACTED in stage["cause"]

    @pytest.mark.parametrize("cause", ["exit 1", "Essential container in task exited", S3_KEY_CAUSE])
    def test_an_ordinary_cause_is_kept_verbatim(self, cause):
        # Redaction is key-driven: a plain container message, and an environment pair whose Name is
        # not sensitive (even when its Value mentions a token), pass through byte-identical.
        h = _History()
        h.entered("Task", "B", 1, prev=0)
        h.add("TaskFailed", 2, taskFailedEventDetails={"resource": "submitJob.sync", "resourceType": "batch",
                                                       "error": "States.TaskFailed", "cause": cause})
        stage = _by_name(ses.fold_history(h.events, _frame("B"), max_error_chars=256))["B"]
        assert stage["cause"] == cause

    def test_the_stage_cap_truncates_history_only_names(self):
        h = _History()
        for i in range(60):
            h.entered("Pass", f"P{i}", i, prev=0 if i == 0 else None)
        folded = ses.fold_history(h.events, [], max_stages=50)
        assert len(folded["stages"]) == 50 and folded["stagesTruncated"] is True

    def test_epoch_millisecond_timestamps_are_accepted(self):
        events = [{"id": 1, "previousEventId": 0, "type": "TaskStateEntered",
                   "timestamp": int(T0.timestamp() * 1000), "stateEnteredEventDetails": {"name": "T"}}]
        assert _by_name(ses.fold_history(events, _frame("T")))["T"]["startDate"] == "2026-09-11T10:00:00Z"


@pytest.mark.unit
class TestFoldHistoryExtended:
    def test_map_iterations_aggregate_and_do_not_count_as_attempts(self):
        h = _History()
        m = h.entered("Map", "Each", 1, prev=0)
        for index in range(3):
            it = h.add("MapIterationStarted", 2 + index, prev=m,
                       mapIterationStartedEventDetails={"name": "Each", "index": index})
            t = h.entered("Task", "Item", 2 + index, prev=it)
            s = h.add("TaskScheduled", 2 + index, prev=t,
                      taskScheduledEventDetails={"resource": "invoke", "resourceType": "lambda"})
            ok = h.add("TaskSucceeded", 3 + index, prev=s, taskSucceededEventDetails={"output": "{}"})
            h.exited("Task", "Item", 3 + index, prev=ok)
            h.add("MapIterationSucceeded" if index < 2 else "MapIterationFailed", 4 + index, prev=m,
                  **{("mapIterationSucceededEventDetails" if index < 2 else "mapIterationFailedEventDetails"):
                     {"name": "Each", "index": index}})
        h.exited("Map", "Each", 10, prev=m)
        folded = ses.fold_history(h.events, _frame("Each", "Item", types={"Each": "Map"}))
        each, item = _by_name(folded)["Each"], _by_name(folded)["Item"]
        assert each["iterations"] == {"started": 3, "succeeded": 2, "failed": 1, "aborted": 0}
        assert "distributed" not in each
        assert item["attempts"] == 1
        assert item["status"] == "SUCCEEDED"

    def test_a_distributed_map_reports_distributed_and_omits_iterations(self):
        h = _History()
        m = h.entered("Map", "Big", 1, prev=0)
        h.add("MapRunStarted", 2, prev=m, mapRunStartedEventDetails={"mapRunArn": "arn:run"})
        h.add("MapRunFailed", 30, prev=2, mapRunFailedEventDetails={"error": "States.ExceedToleratedFailureThreshold",
                                                                    "cause": "too many"})
        h.exited("Map", "Big", 31, prev=3)
        stage = _by_name(ses.fold_history(h.events, _frame("Big", types={"Big": "Map"})))["Big"]
        assert stage["distributed"] is True and "iterations" not in stage
        assert stage["status"] == "FAILED" and stage["caught"] is True
        assert stage["error"] == "States.ExceedToleratedFailureThreshold"

    def test_map_run_aborted_closes_the_map_as_aborted(self):
        h = _History()
        m = h.entered("Map", "Big", 1, prev=0)
        h.add("MapRunStarted", 2, prev=m, mapRunStartedEventDetails={"mapRunArn": "arn:run"})
        h.add("MapRunAborted", 3, prev=2, mapRunAbortedEventDetails={"error": "", "cause": ""})
        stage = _by_name(ses.fold_history(h.events, _frame("Big", types={"Big": "Map"})))["Big"]
        assert stage["status"] == "ABORTED"

    def test_sibling_states_aborted_when_a_parallel_branch_fails(self):
        h = _History()
        par = h.entered("Parallel", "Fan", 1, prev=0)
        a = h.entered("Task", "A", 2, prev=par)
        b = h.entered("Wait", "B", 3, prev=par)
        a_s = h.add("TaskScheduled", 4, prev=a, taskScheduledEventDetails={"resource": "invoke", "resourceType": "lambda"})
        a_f = h.add("TaskFailed", 5, prev=a_s, taskFailedEventDetails={"error": "E", "cause": "boom"})
        h.add("WaitStateAborted", 6, prev=b, stateExitedEventDetails={"name": "B"})
        h.add("ParallelStateAborted", 7, prev=a_f)
        h.add("ExecutionFailed", 8, prev=7, executionFailedEventDetails={"error": "E", "cause": "boom"})
        stages = _by_name(ses.fold_history(h.events, _frame("Fan", "A", "B", types={"Fan": "Parallel", "B": "Wait"})))
        assert stages["B"]["status"] == "ABORTED"
        assert stages["Fan"]["status"] == "ABORTED"
        assert stages["A"]["status"] == "FAILED" and "caught" not in stages["A"]
        assert stages["A"]["stopDate"] == _iso(8)

    def test_a_caught_parallel_branch_failure_folds_the_branch_and_the_parallel_as_failed_and_caught(self):
        # A failed branch has no TaskStateExited of its own: the branch ends with ParallelStateFailed,
        # the Parallel's Catch routes on, and the run finishes cleanly. Both the branch task and the
        # Parallel read FAILED and caught (never SUCCEEDED), the Parallel takes the branch's error, and
        # the branch task closes with its container rather than with the execution.
        h = _History()
        h.add("ExecutionStarted", 0, prev=0)
        fan = h.entered("Parallel", "Fan", 1)
        started = h.add("ParallelStateStarted", 1, prev=fan)
        a = h.entered("Task", "A", 2, prev=started)
        a_s = h.add("TaskScheduled", 3, prev=a,
                    taskScheduledEventDetails={"resource": "invoke", "resourceType": "lambda"})
        a_st = h.add("TaskStarted", 4, prev=a_s)
        a_f = h.add("TaskFailed", 5, prev=a_st, taskFailedEventDetails={"error": "E", "cause": "A broke"})
        failed = h.add("ParallelStateFailed", 5, prev=a_f)
        h.exited("Parallel", "Fan", 6, prev=failed)
        h.entered("Pass", "Recover", 7)
        h.exited("Pass", "Recover", 8)
        h.add("ExecutionSucceeded", 9, executionSucceededEventDetails={"output": "{}"})
        stages = _by_name(ses.fold_history(
            h.events, _frame("Fan", "A", "Recover", types={"Fan": "Parallel", "Recover": "Pass"})))
        fan_stage = stages["Fan"]
        assert fan_stage["status"] == "FAILED" and fan_stage["caught"] is True
        assert fan_stage["error"] == "E" and fan_stage["cause"] == "A broke"
        assert fan_stage["stopDate"] == _iso(6)
        a_stage = stages["A"]
        assert a_stage["status"] == "FAILED" and a_stage["caught"] is True
        assert a_stage["error"] == "E" and a_stage["cause"] == "A broke"
        assert a_stage["stopDate"] == _iso(6)
        assert stages["Recover"]["status"] == "SUCCEEDED"
        assert all(s["status"] != "RUNNING" for s in stages.values())

    def test_a_caught_inline_map_iteration_failure_folds_the_map_and_the_inner_task_as_failed_and_caught(self):
        # The inline-Map twin of the Parallel case: MapStateFailed carries no state name and no detail
        # block, so the Map is found up the previousEventId chain and takes the iteration task's error.
        h = _History()
        each = h.entered("Map", "Each", 1, prev=0)
        started = h.add("MapStateStarted", 1, prev=each, mapStateStartedEventDetails={"length": 1})
        it = h.add("MapIterationStarted", 2, prev=started, mapIterationStartedEventDetails={"name": "Each", "index": 0})
        inner = h.entered("Task", "Inner", 2, prev=it)
        s = h.add("TaskScheduled", 3, prev=inner,
                  taskScheduledEventDetails={"resource": "invoke", "resourceType": "lambda"})
        f = h.add("TaskFailed", 4, prev=s, taskFailedEventDetails={"error": "E", "cause": "item broke"})
        itf = h.add("MapIterationFailed", 4, prev=f, mapIterationFailedEventDetails={"name": "Each", "index": 0})
        mf = h.add("MapStateFailed", 5, prev=itf)
        h.exited("Map", "Each", 6, prev=mf)
        h.entered("Pass", "Recover", 7)
        h.exited("Pass", "Recover", 8)
        h.add("ExecutionSucceeded", 9, executionSucceededEventDetails={"output": "{}"})
        stages = _by_name(ses.fold_history(
            h.events, _frame("Each", "Inner", "Recover", types={"Each": "Map", "Recover": "Pass"})))
        each_stage = stages["Each"]
        assert each_stage["status"] == "FAILED" and each_stage["caught"] is True
        assert each_stage["iterations"] == {"started": 1, "succeeded": 0, "failed": 1, "aborted": 0}
        assert each_stage["error"] == "E" and each_stage["cause"] == "item broke"
        inner_stage = stages["Inner"]
        assert inner_stage["status"] == "FAILED" and inner_stage["caught"] is True
        assert inner_stage["stopDate"] == _iso(6)
        assert stages["Recover"]["status"] == "SUCCEEDED"
        assert all(s["status"] != "RUNNING" for s in stages.values())

    def test_a_parallel_whose_retry_recovers_folds_succeeded(self):
        # A Retry on the Parallel re-runs its branches after ParallelStateFailed; the
        # ParallelStateSucceeded that follows clears the recorded failure, so the container's exit
        # reads SUCCEEDED with no error, the same way TaskSucceeded clears a retried task's failure.
        h = _History()
        fan = h.entered("Parallel", "Fan", 1, prev=0)
        started = h.add("ParallelStateStarted", 1, prev=fan)
        a = h.entered("Task", "A", 2, prev=started)
        a_s = h.add("TaskScheduled", 3, prev=a,
                    taskScheduledEventDetails={"resource": "invoke", "resourceType": "lambda"})
        a_f = h.add("TaskFailed", 4, prev=a_s, taskFailedEventDetails={"error": "E", "cause": "first try"})
        failed = h.add("ParallelStateFailed", 4, prev=a_f)
        restarted = h.add("ParallelStateStarted", 5, prev=failed)
        a2 = h.entered("Task", "A", 5, prev=restarted)
        a2_s = h.add("TaskScheduled", 6, prev=a2,
                     taskScheduledEventDetails={"resource": "invoke", "resourceType": "lambda"})
        a2_ok = h.add("TaskSucceeded", 7, prev=a2_s, taskSucceededEventDetails={"output": "{}"})
        a2_x = h.exited("Task", "A", 7, prev=a2_ok)
        ok = h.add("ParallelStateSucceeded", 8, prev=a2_x)
        h.exited("Parallel", "Fan", 8, prev=ok)
        h.add("ExecutionSucceeded", 9, executionSucceededEventDetails={"output": "{}"})
        stages = _by_name(ses.fold_history(h.events, _frame("Fan", "A", types={"Fan": "Parallel"})))
        assert stages["Fan"]["status"] == "SUCCEEDED" and "caught" not in stages["Fan"]
        assert stages["Fan"]["error"] == "" and stages["Fan"]["cause"] == ""
        assert stages["Fan"]["stopDate"] == _iso(8)
        assert all(s["status"] != "RUNNING" for s in stages.values())

    def test_execution_failed_supplies_its_error_to_the_fail_state_that_raised_it(self):
        # A Fail state closes on entry, and its Error/Cause appear in the history only on the
        # ExecutionFailed that follows it; that closer hands them to the Fail state it descends from.
        h = _History()
        t = h.entered("Task", "T", 1, prev=0)
        s = h.add("TaskScheduled", 2, prev=t,
                  taskScheduledEventDetails={"resource": "invoke", "resourceType": "lambda"})
        ok = h.add("TaskSucceeded", 3, prev=s, taskSucceededEventDetails={"output": "{}"})
        h.exited("Task", "T", 4, prev=ok)
        h.entered("Fail", "FailState", 5)
        h.add("ExecutionFailed", 5, executionFailedEventDetails={"error": "PipelineFailed",
                                                                 "cause": "Pipeline execution failed"})
        stages = _by_name(ses.fold_history(h.events, _frame("T", "FailState", types={"FailState": "Fail"})))
        fail = stages["FailState"]
        assert fail["status"] == "FAILED" and "caught" not in fail
        assert fail["error"] == "PipelineFailed" and fail["cause"] == "Pipeline execution failed"
        assert fail["stopDate"] == _iso(5)
        assert stages["T"]["status"] == "SUCCEEDED" and stages["T"]["error"] == ""

    def test_an_abort_does_not_supply_its_cause_to_an_earlier_caught_fail_state(self):
        # Negative control for the test above: the closer's error goes only to the Fail state its
        # previousEventId chain reaches. A Fail state inside a caught branch, followed much later by a
        # user abort of a different state, keeps its own (empty) error.
        h = _History()
        fan = h.entered("Parallel", "Fan", 1, prev=0)
        started = h.add("ParallelStateStarted", 1, prev=fan)
        bf = h.entered("Fail", "BranchFail", 2, prev=started)
        failed = h.add("ParallelStateFailed", 2, prev=bf)
        h.exited("Parallel", "Fan", 3, prev=failed)
        t = h.entered("Task", "Long", 4)
        s = h.add("TaskScheduled", 5, prev=t,
                  taskScheduledEventDetails={"resource": "invoke", "resourceType": "lambda"})
        h.add("ExecutionAborted", 9, prev=s, executionAbortedEventDetails={"error": "", "cause": "user"})
        stages = _by_name(ses.fold_history(
            h.events, _frame("Fan", "BranchFail", "Long", types={"Fan": "Parallel", "BranchFail": "Fail"})))
        assert stages["BranchFail"]["status"] == "FAILED" and stages["BranchFail"]["cause"] == ""
        assert stages["Fan"]["status"] == "FAILED" and stages["Fan"]["caught"] is True
        assert stages["Long"]["status"] == "ABORTED"

    def test_a_task_state_aborted_without_a_name_closes_the_open_task(self):
        h = _History()
        t = h.entered("Task", "Long", 1, prev=0)
        s = h.add("TaskScheduled", 2, prev=t, taskScheduledEventDetails={"resource": "submitJob.sync", "resourceType": "batch"})
        h.add("TaskStateAborted", 3, prev=s)
        h.add("ExecutionAborted", 4, prev=3, executionAbortedEventDetails={"error": "", "cause": ""})
        assert _by_name(ses.fold_history(h.events, _frame("Long")))["Long"]["status"] == "ABORTED"

    def test_execution_aborted_closes_every_open_instance(self):
        h = _History()
        h.entered("Task", "T", 1, prev=0)
        h.add("ExecutionAborted", 9, executionAbortedEventDetails={"error": "", "cause": "user"})
        stage = _by_name(ses.fold_history(h.events, _frame("T")))["T"]
        assert stage["status"] == "ABORTED" and stage["stopDate"] == _iso(9)

    def test_execution_timed_out_closes_as_timed_out(self):
        h = _History()
        h.entered("Task", "T", 1, prev=0)
        h.add("ExecutionTimedOut", 9, executionTimedOutEventDetails={"error": "States.Timeout", "cause": ""})
        assert _by_name(ses.fold_history(h.events, _frame("T")))["T"]["status"] == "TIMED_OUT"

    def test_execution_failed_supplies_the_error_to_an_open_stage_without_one(self):
        h = _History()
        h.entered("Task", "T", 1, prev=0)
        h.add("ExecutionFailed", 9, executionFailedEventDetails={"error": "States.Runtime", "cause": "bad path"})
        stage = _by_name(ses.fold_history(h.events, _frame("T")))["T"]
        assert stage["status"] == "FAILED" and stage["error"] == "States.Runtime" and "caught" not in stage

    def test_batch_stream_is_captured_from_the_success_output_when_a_machine_keeps_the_result(self):
        # Opportunistic shortcut: only a machine whose Task state keeps the SubmitJob result puts the
        # DescribeJobs object in TaskSucceeded.output. None of the built-in machines does (next test).
        h = _History()
        t = h.entered("Task", "Preview3dThumbnailBatchJob", 1, prev=0)
        s = h.add("TaskScheduled", 2, prev=t, taskScheduledEventDetails={"resource": "submitJob.sync", "resourceType": "batch"})
        st = h.add("TaskStarted", 3, prev=s)
        sub = h.add("TaskSubmitted", 4, prev=st, taskSubmittedEventDetails={
            "resource": "submitJob.sync", "resourceType": "batch",
            "output": json.dumps({"JobArn": "arn:j", "JobId": "job-42", "JobName": "thumb"})})
        ok = h.add("TaskSucceeded", 60, prev=sub, taskSucceededEventDetails={
            "resource": "submitJob.sync", "resourceType": "batch",
            "output": json.dumps({"JobId": "job-42", "Status": "SUCCEEDED",
                                  "Container": {"LogStreamName": "vams-thumb-jd/default/abc123"}})})
        h.exited("Task", "Preview3dThumbnailBatchJob", 61, prev=ok)
        stage = _by_name(ses.fold_history(h.events, _frame("Preview3dThumbnailBatchJob")))["Preview3dThumbnailBatchJob"]
        assert stage["status"] == "SUCCEEDED"
        assert stage["batch"] == {"jobId": "job-42", "logStreamName": "vams-thumb-jd/default/abc123"}

    def test_a_discarded_sync_result_yields_the_job_id_and_no_stream(self):
        # What the built-in machines actually produce (verified live on prod5 with the potree machine):
        # TaskSubmitted carries {JobArn, JobId, JobName, SdkHttpMetadata, SdkResponseMetadata};
        # TaskSucceeded's output is the state's OWN INPUT, so no Container.LogStreamName exists anywhere
        # in the history. The stage keeps the jobId for _resolve_batch_stage_streams to fill and
        # reports an empty stream — no error, no warning.
        state_input = {"currentStageType": "Batch", "definition": {"pipelineId": "preview-pc-potree-viewer"},
                       "externalSfnTaskToken": "<redacted>",
                       "inputConfigurationS3Location": "s3://bkt/in/config.json",
                       "inputMetadataS3Location": "s3://bkt/in/metadata.json",
                       "jobName": "PcPotreeViewerJob_PDAL", "status": "STARTING"}
        h = _History()
        t = h.entered("Task", "PdalConverterBatchJob", 1, prev=0)
        s = h.add("TaskScheduled", 2, prev=t, taskScheduledEventDetails={"resource": "submitJob.sync", "resourceType": "batch"})
        st = h.add("TaskStarted", 3, prev=s)
        sub = h.add("TaskSubmitted", 4, prev=st, taskSubmittedEventDetails={
            "resource": "submitJob.sync", "resourceType": "batch",
            "output": json.dumps({"JobArn": "arn:aws:batch:us-west-2:123456789012:job/j-pdal", "JobId": "j-pdal",
                                  "JobName": "PcPotreeViewerJob_PDAL", "SdkHttpMetadata": {"HttpStatusCode": 200},
                                  "SdkResponseMetadata": {"RequestId": "r"}})})
        ok = h.add("TaskSucceeded", 60, prev=sub, taskSucceededEventDetails={
            "resource": "submitJob.sync", "resourceType": "batch", "output": json.dumps(state_input)})
        h.exited("Task", "PdalConverterBatchJob", 61, prev=ok)
        stage = _by_name(ses.fold_history(h.events, _frame("PdalConverterBatchJob")))["PdalConverterBatchJob"]
        assert stage["status"] == "SUCCEEDED"
        assert stage["batch"] == {"jobId": "j-pdal", "logStreamName": ""}

    def test_batch_stream_is_captured_from_the_failure_cause_when_it_carries_the_describe_jobs_object(self):
        # Opportunistic shortcut: a cause that IS the DescribeJobs object (a machine without the
        # container's SendTaskFailure) yields the stream before the cause is capped. The built-in
        # machines send a plain-string cause instead (the sequence in
        # test_the_prod5_caught_failure_sequence_folds_as_recorded), which parses to nothing.
        describe = {"JobId": "job-7", "Status": "FAILED", "StatusReason": "Essential container exited",
                    "Attempts": [{"Container": {"LogStreamName": "vams-thumb-jd/default/deadbeef", "ExitCode": 1}}],
                    "Container": {"LogStreamName": "vams-thumb-jd/default/deadbeef", "ExitCode": 1,
                                  "Command": ["python", "run.py"] * 40}}
        cause = json.dumps(describe)
        assert len(cause) > 256
        h = _History()
        t = h.entered("Task", "Preview3dThumbnailBatchJob", 1, prev=0)
        s = h.add("TaskScheduled", 2, prev=t, taskScheduledEventDetails={"resource": "submitJob.sync", "resourceType": "batch"})
        f = h.add("TaskFailed", 60, prev=s, taskFailedEventDetails={
            "resource": "submitJob.sync", "resourceType": "batch", "error": "States.TaskFailed", "cause": cause})
        h.exited("Task", "Preview3dThumbnailBatchJob", 61, prev=f)
        stage = _by_name(ses.fold_history(h.events, _frame("Preview3dThumbnailBatchJob"), max_error_chars=256))[
            "Preview3dThumbnailBatchJob"]
        assert stage["status"] == "FAILED" and stage["caught"] is True
        assert stage["batch"] == {"jobId": "job-7", "logStreamName": "vams-thumb-jd/default/deadbeef"}
        assert len(stage["cause"]) == 256

    def test_a_non_batch_task_never_gets_a_batch_block(self):
        h = _History()
        t = h.entered("Task", "Fn", 1, prev=0)
        s = h.add("TaskScheduled", 2, prev=t, taskScheduledEventDetails={"resource": "invoke", "resourceType": "lambda"})
        ok = h.add("TaskSucceeded", 3, prev=s, taskSucceededEventDetails={
            "output": json.dumps({"Container": {"LogStreamName": "looks/like/batch"}})})
        h.exited("Task", "Fn", 4, prev=ok)
        assert "batch" not in _by_name(ses.fold_history(h.events, _frame("Fn")))["Fn"]

    def test_history_events_for_stage_slices_between_entered_and_exited_ids(self):
        h = _History()
        h.entered("Task", "Prepare", 1, prev=0)          # id 1
        h.exited("Task", "Prepare", 2)                   # id 2
        h.entered("Task", "Batch", 3)                    # id 3
        h.add("TaskScheduled", 4, taskScheduledEventDetails={"resource": "submitJob.sync", "resourceType": "batch"})  # 4
        h.add("TaskFailed", 5, taskFailedEventDetails={"error": "E", "cause": "c"})  # 5
        h.exited("Task", "Batch", 6)                     # id 6
        h.entered("Pass", "After", 7)                    # id 7
        sliced = ses.history_events_for_stage(h.events, "Batch")
        assert [e["id"] for e in sliced] == [3, 4, 5, 6]
        assert ses.history_events_for_stage(h.events, "Missing") == []

    def test_history_events_for_stage_runs_to_the_end_when_the_stage_is_still_open(self):
        h = _History()
        h.entered("Task", "Batch", 1, prev=0)
        h.add("TaskScheduled", 2, taskScheduledEventDetails={"resource": "submitJob.sync", "resourceType": "batch"})
        assert [e["id"] for e in ses.history_events_for_stage(h.events, "Batch")] == [1, 2]


def _client_error(code, operation="Op"):
    return botocore.exceptions.ClientError({"Error": {"Code": code, "Message": code}}, operation)


@pytest.mark.unit
class TestSubExecutionSummary:
    SM = "arn:aws:states:us-west-2:123456789012:stateMachine:VAMSstateMachine-thumb"
    EX = "arn:aws:states:us-west-2:123456789012:execution:VAMSstateMachine-thumb:run-1"

    def setup_method(self):
        # The Batch describe memo is per invocation; these tests are each one invocation.
        le._batch_job_describe_cache.clear()

    def test_a_step_functions_execution_is_described_with_iso_dates_and_a_capped_cause(self):
        sub = {"resourceType": "stepFunctionsExecution", "stateMachineArn": self.SM, "executionArn": self.EX,
               "label": "thumb processing", "stageName": ""}
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_execution.return_value = {
                "status": "FAILED", "startDate": _ts(0), "stopDate": _ts(90),
                "error": "PipelineFailed", "cause": "c" * 1000}
            summary, warnings = le._sub_execution_summary(sub)
        m_sfn.describe_execution.assert_called_once_with(executionArn=self.EX)
        assert warnings == []
        assert summary["resourceType"] == "stepFunctionsExecution"
        assert summary["label"] == "thumb processing"
        assert summary["resourceName"] == "VAMSstateMachine-thumb"
        assert summary["status"] == "FAILED"
        assert summary["startDate"] == _iso(0) and summary["stopDate"] == _iso(90)
        assert summary["error"] == "PipelineFailed" and len(summary["cause"]) == 256
        assert summary["stages"] == [] and summary["stageSource"] == "none"
        assert summary["stagesTruncated"] is False and summary["historyTruncated"] is False
        json.dumps(summary)  # no datetime survives capture

    def test_the_resource_name_comes_from_the_execution_arn_when_no_state_machine_arn_was_registered(self):
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_execution.return_value = {"status": "RUNNING", "startDate": _ts(0)}
            summary, _ = le._sub_execution_summary({"resourceType": "stepFunctionsExecution", "executionArn": self.EX})
        assert summary["resourceName"] == "VAMSstateMachine-thumb"
        assert summary["status"] == "RUNNING" and summary["stopDate"] == ""

    def test_a_status_outside_the_documented_set_is_reported_unknown(self):
        # DescribeExecution also returns PENDING_REDRIVE, which is neither running nor terminal; the
        # response vocabulary is closed, so that value is the documented fallback.
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_execution.return_value = {"status": "PENDING_REDRIVE", "startDate": _ts(0)}
            summary, warnings = le._sub_execution_summary(
                {"resourceType": "stepFunctionsExecution", "stateMachineArn": self.SM, "executionArn": self.EX})
        assert summary["status"] == "UNKNOWN" and warnings == []
        assert summary["startDate"] == _iso(0)

    def test_a_describe_failure_is_unknown_plus_a_warning_never_an_exception(self):
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_execution.side_effect = _client_error("ThrottlingException", "DescribeExecution")
            summary, warnings = le._sub_execution_summary(
                {"resourceType": "stepFunctionsExecution", "stateMachineArn": self.SM, "executionArn": self.EX})
        assert summary["status"] == "UNKNOWN"
        assert len(warnings) == 1 and "ThrottlingException" in warnings[0] and "VAMSstateMachine-thumb" in warnings[0]

    def test_a_sub_execution_without_an_execution_arn_is_unknown_without_a_call(self):
        with patch.object(le, "sfn") as m_sfn:
            summary, warnings = le._sub_execution_summary({"resourceType": "stepFunctionsExecution",
                                                           "stateMachineArn": self.SM})
        m_sfn.describe_execution.assert_not_called()
        assert summary["status"] == "UNKNOWN" and warnings == []

    def test_a_batch_job_is_described_and_its_status_and_stream_folded(self):
        with patch.object(le, "batch_client") as m_batch:
            m_batch.describe_jobs.return_value = {"jobs": [{
                "jobId": "job-1", "jobName": "isaac-train", "status": "RUNNABLE",
                "startedAt": int(T0.timestamp() * 1000), "statusReason": "",
                "container": {"logStreamName": "isaac-jd/default/abc"}}]}
            summary, warnings = le._sub_execution_summary(
                {"resourceType": "batchJob", "jobId": "job-1", "stageName": "ExecuteBatchJobState"})
        m_batch.describe_jobs.assert_called_once_with(jobs=["job-1"])
        assert warnings == []
        assert summary["status"] == "RUNNING" and summary["resourceName"] == "isaac-train"
        assert summary["stageName"] == "ExecuteBatchJobState"
        assert summary["startDate"] == "2026-09-11T10:00:00Z" and summary["stopDate"] == ""
        assert summary["batch"] == {"jobId": "job-1", "logStreamName": "isaac-jd/default/abc"}
        json.dumps(summary)

    def test_a_batch_job_asked_about_twice_in_one_request_is_described_once(self):
        with patch.object(le, "batch_client") as m_batch:
            m_batch.describe_jobs.return_value = {"jobs": [{
                "jobId": "job-1", "jobName": "isaac-train", "status": "SUCCEEDED",
                "container": {"logStreamName": "isaac-jd/default/abc"}}]}
            first, _w1 = le._sub_execution_summary({"resourceType": "batchJob", "jobId": "job-1"})
            second, _w2 = le._sub_execution_summary({"resourceType": "batchJob", "jobId": "job-1"})
        assert m_batch.describe_jobs.call_count == 1
        assert first["batch"] == second["batch"] == {"jobId": "job-1", "logStreamName": "isaac-jd/default/abc"}

    def test_a_job_batch_no_longer_describes_is_unknown_with_no_stream(self):
        with patch.object(le, "batch_client") as m_batch:
            m_batch.describe_jobs.return_value = {"jobs": []}
            summary, warnings = le._sub_execution_summary({"resourceType": "batchJob", "jobId": "job-old"})
        assert summary["status"] == "UNKNOWN"
        assert "batch" not in summary
        assert len(warnings) == 1 and "job-old" in warnings[0]

    def test_a_batch_describe_failure_is_a_warning(self):
        with patch.object(le, "batch_client") as m_batch:
            m_batch.describe_jobs.side_effect = _client_error("AccessDeniedException", "DescribeJobs")
            summary, warnings = le._sub_execution_summary({"resourceType": "batchJob", "jobId": "job-1"})
        assert summary["status"] == "UNKNOWN" and "AccessDeniedException" in warnings[0]

    def test_other_resource_types_are_unknown_without_any_call(self):
        with patch.object(le, "sfn") as m_sfn, patch.object(le, "batch_client") as m_batch, \
                patch.object(le, "deadline_client") as m_deadline:
            summary, warnings = le._sub_execution_summary(
                {"resourceType": "ecsTask", "taskArn": "arn:aws:ecs:us-west-2:1:task/c/t", "label": "render"})
        m_sfn.describe_execution.assert_not_called()
        m_batch.describe_jobs.assert_not_called()
        m_deadline.get_job.assert_not_called()
        assert summary["status"] == "UNKNOWN" and summary["label"] == "render" and warnings == []

    def test_a_step_functions_cause_carrying_a_describe_jobs_task_token_is_redacted_before_the_cap(self):
        # A sub-execution that failed on a `batch:submitJob.sync` task reports the DescribeJobs object
        # as its Cause, task token included. Redacting before the cap is what lets the object's tail
        # survive: capped first, the 700-character token would fill the budget unclosed.
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_execution.return_value = {
                "status": "FAILED", "startDate": _ts(0), "stopDate": _ts(9),
                "error": "States.TaskFailed", "cause": _token_cause("VAMS_TASK_TOKEN")}
            summary, warnings = le._sub_execution_summary(
                {"resourceType": "stepFunctionsExecution", "stateMachineArn": self.SM, "executionArn": self.EX})
        assert warnings == []
        assert TASK_TOKEN[:32] not in summary["cause"], "task token leaked through the sub-execution cause"
        assert REDACTED in summary["cause"] and "isaac-jd/default/abc" in summary["cause"]
        assert len(summary["cause"]) <= le.MAX_SUB_STAGE_ERROR_CHARS

    def test_a_batch_status_reason_carrying_a_task_token_is_redacted_before_the_cap(self):
        with patch.object(le, "batch_client") as m_batch:
            m_batch.describe_jobs.return_value = {"jobs": [{
                "jobId": "job-1", "jobName": "isaac-train", "status": "FAILED",
                "statusReason": _token_cause(), "container": {"logStreamName": "isaac-jd/default/abc"}}]}
            summary, warnings = le._sub_execution_summary({"resourceType": "batchJob", "jobId": "job-1"})
        assert warnings == []
        assert TASK_TOKEN[:32] not in summary["cause"] and REDACTED in summary["cause"]
        assert len(summary["cause"]) <= le.MAX_SUB_STAGE_ERROR_CHARS
        # The stream is still read from the job object itself.
        assert summary["batch"] == {"jobId": "job-1", "logStreamName": "isaac-jd/default/abc"}

    def test_a_deadline_lifecycle_message_carrying_a_task_token_is_redacted_before_the_cap(self):
        le._deadline_job_cache.clear()
        with patch.object(le, "deadline_client") as m_deadline:
            m_deadline.get_job.return_value = {
                "jobId": "job-d", "name": "render", "lifecycleStatus": "CREATE_FAILED",
                "lifecycleStatusMessage": _token_cause(), "taskRunStatus": "PENDING", "createdAt": _ts(0)}
            summary, warnings = le._sub_execution_summary(
                {"resourceType": "deadlineCloudJob", "farmId": "farm-1", "queueId": "queue-1", "jobId": "job-d"})
        assert warnings == []
        assert summary["status"] == "FAILED"
        assert TASK_TOKEN[:32] not in summary["cause"] and REDACTED in summary["cause"]
        assert len(summary["cause"]) <= le.MAX_SUB_STAGE_ERROR_CHARS

    @pytest.mark.parametrize("cause", ["exit 1", "Essential container in task exited", S3_KEY_CAUSE])
    def test_an_ordinary_sub_execution_cause_is_kept_verbatim(self, cause):
        # Key-driven: a plain message, or a Name/Value pair whose Name is not sensitive even though its
        # Value mentions a token, is reported byte-identical.
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_execution.return_value = {"status": "FAILED", "startDate": _ts(0), "stopDate": _ts(1),
                                                     "error": "PipelineFailed", "cause": cause}
            summary, _ = le._sub_execution_summary(
                {"resourceType": "stepFunctionsExecution", "stateMachineArn": self.SM, "executionArn": self.EX})
        assert summary["cause"] == cause


def _page(events, token=None):
    page = {"events": events}
    if token:
        page["nextToken"] = token
    return page


def _simple_definition():
    return json.dumps({"StartAt": "Prepare", "States": {
        "Prepare": {"Type": "Task", "Resource": "arn:aws:states:::lambda:invoke", "Next": "Batch"},
        "Batch": {"Type": "Task", "Resource": "arn:aws:states:::batch:submitJob.sync", "End": True}}})


def _described(definition=None, log_group="arn:aws:logs:us-west-2:1:log-group:/aws/vendedlogs/VAMSstateMachine-x:*"):
    out = {"name": "VAMSstateMachine-x", "loggingConfiguration": {"destinations": [
        {"cloudWatchLogsLogGroup": {"logGroupArn": log_group}}]}}
    if definition is not None:
        out["definition"] = definition
    return out


@pytest.mark.unit
class TestSubExecutionStagesWiring:
    SM = "arn:aws:states:us-west-2:1:stateMachine:VAMSstateMachine-x"
    EX = "arn:aws:states:us-west-2:1:execution:VAMSstateMachine-x:run"

    def setup_method(self):
        le._state_machine_describe_cache.clear()
        le._batch_job_describe_cache.clear()

    def test_pages_are_concatenated_and_charged_to_the_shared_budget(self):
        budget = [le.MAX_SUB_STAGE_HISTORY_PAGES_PER_REQUEST]
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.get_execution_history.side_effect = [_page([{"id": 1}], "t1"), _page([{"id": 2}])]
            events, truncated, ok = le._sfn_history_pages(self.EX, budget)
        assert [e["id"] for e in events] == [1, 2] and truncated is False and ok is True
        assert budget == [le.MAX_SUB_STAGE_HISTORY_PAGES_PER_REQUEST - 2]
        first, second = m_sfn.get_execution_history.call_args_list
        assert first.kwargs == {"executionArn": self.EX, "maxResults": 1000, "includeExecutionData": True,
                                "reverseOrder": False}
        assert second.kwargs["nextToken"] == "t1"

    def test_the_per_sub_page_cap_stops_early_and_flags_truncation(self):
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.get_execution_history.side_effect = [_page([{"id": i}], f"t{i}") for i in range(10)]
            events, truncated, ok = le._sfn_history_pages(self.EX, [100])
        assert len(events) == le.MAX_SUB_STAGE_HISTORY_PAGES and truncated is True and ok is True
        assert m_sfn.get_execution_history.call_count == le.MAX_SUB_STAGE_HISTORY_PAGES

    def test_an_exhausted_request_budget_reads_nothing_and_flags_truncation(self):
        with patch.object(le, "sfn") as m_sfn:
            events, truncated, ok = le._sfn_history_pages(self.EX, [0])
        m_sfn.get_execution_history.assert_not_called()
        assert events == [] and truncated is True and ok is True

    def test_a_history_failure_keeps_what_was_read_and_reports_not_ok(self):
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.get_execution_history.side_effect = [_page([{"id": 1}], "t1"),
                                                       _client_error("ThrottlingException", "GetExecutionHistory")]
            events, truncated, ok = le._sfn_history_pages(self.EX, [10])
        assert [e["id"] for e in events] == [1] and truncated is True and ok is False

    def _sub(self):
        return {"resourceType": "stepFunctionsExecution", "stateMachineArn": self.SM, "executionArn": self.EX}

    def _summary(self, status="SUCCEEDED"):
        return {"resourceType": "stepFunctionsExecution", "label": "", "stageName": "", "resourceName": "x",
                "status": status, "startDate": "", "stopDate": "", "error": "", "cause": "",
                "stageSource": "none", "stagesTruncated": False, "historyTruncated": False, "stages": []}

    def test_definition_plus_history_yields_definition_sourced_stages(self):
        h = _History()
        h.entered("Task", "Prepare", 1, prev=0)
        h.exited("Task", "Prepare", 2)
        summary, warnings = self._summary(), []
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_state_machine.return_value = _described(_simple_definition())
            m_sfn.get_execution_history.return_value = _page(h.events)
            le._sub_execution_stages(summary, self._sub(), [10], warnings)
        assert summary["stageSource"] == "definition"
        assert [(s["stageName"], s["status"]) for s in summary["stages"]] == [
            ("Prepare", "SUCCEEDED"), ("Batch", "NOT_STARTED")]
        assert summary["stagesTruncated"] is False and summary["historyTruncated"] is False
        assert warnings == []

    def test_without_a_definition_the_stages_come_from_history(self):
        h = _History()
        h.entered("Task", "Prepare", 1, prev=0)
        summary, warnings = self._summary("RUNNING"), []
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_state_machine.return_value = _described(definition=None)
            m_sfn.get_execution_history.return_value = _page(h.events)
            le._sub_execution_stages(summary, self._sub(), [10], warnings)
        assert summary["stageSource"] == "history"
        assert [s["stageName"] for s in summary["stages"]] == ["Prepare"]

    def test_with_neither_the_source_is_none(self):
        summary, warnings = self._summary(), []
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_state_machine.side_effect = _client_error("AccessDeniedException")
            m_sfn.get_execution_history.side_effect = _client_error("AccessDeniedException")
            le._sub_execution_stages(summary, self._sub(), [10], warnings)
        assert summary["stageSource"] == "none" and summary["stages"] == []
        assert len(warnings) == 1 and "history" in warnings[0].lower()

    def test_a_history_failure_with_a_definition_reports_the_frame_not_started_and_warns(self):
        summary, warnings = self._summary(), []
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_state_machine.return_value = _described(_simple_definition())
            m_sfn.get_execution_history.side_effect = _client_error("ThrottlingException")
            le._sub_execution_stages(summary, self._sub(), [10], warnings)
        assert summary["stageSource"] == "definition"
        assert all(s["status"] == "NOT_STARTED" for s in summary["stages"])
        assert summary["historyTruncated"] is True
        assert len(warnings) == 1

    def test_a_non_step_functions_summary_gets_no_stages_and_no_calls(self):
        summary = dict(self._summary(), resourceType="batchJob")
        with patch.object(le, "sfn") as m_sfn:
            le._sub_execution_stages(summary, {"resourceType": "batchJob", "jobId": "j"}, [10], [])
        m_sfn.describe_state_machine.assert_not_called()
        m_sfn.get_execution_history.assert_not_called()
        assert summary["stages"] == []

    def test_phase_one_summarises_up_to_the_inspect_cap(self):
        subs = [dict(self._sub(), executionArn=f"{self.EX}-{i}") for i in range(25)]
        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_execution.return_value = {"status": "SUCCEEDED", "startDate": _ts(0), "stopDate": _ts(1)}
            pending, truncated, warnings = le._pipeline_sub_executions({"registeredSubExecutions": subs})
        assert len(pending) == le.MAX_REGISTERED_SUB_EXECUTIONS_INSPECTED and truncated is True
        assert m_sfn.describe_execution.call_count == le.MAX_REGISTERED_SUB_EXECUTIONS_INSPECTED
        assert warnings == []
        assert all(summary["status"] == "SUCCEEDED" for summary, _sub in pending)

    def test_phase_two_serves_terminal_sub_executions_first_from_one_shared_budget(self):
        # Five sub-executions each needing 5 pages against a 20-page budget: the four terminal ones
        # (listed AFTER the running one) get their history; the running one is flagged truncated.
        running = (self._summary("RUNNING"), dict(self._sub(), executionArn=f"{self.EX}-run"), [])
        terminal = [(self._summary("SUCCEEDED"), dict(self._sub(), executionArn=f"{self.EX}-{i}"), [])
                    for i in range(4)]
        pending = [running] + terminal
        pages_by_arn, histories = {}, {}

        def _history(**kwargs):
            # One growing history per execution, so event ids stay unique across its pages.
            arn = kwargs["executionArn"]
            h = histories.setdefault(arn, _History())
            n = pages_by_arn.get(arn, 0) + 1
            pages_by_arn[arn] = n
            start = len(h.events)
            h.entered("Task", f"S{n}", n, prev=0)
            return _page(h.events[start:], f"t{n}" if n < 5 else None)

        with patch.object(le, "sfn") as m_sfn:
            m_sfn.describe_state_machine.return_value = _described(definition=None)
            m_sfn.get_execution_history.side_effect = _history
            le._fill_sub_execution_stages(pending, [le.MAX_SUB_STAGE_HISTORY_PAGES_PER_REQUEST])
        assert all(s["historyTruncated"] is False and len(s["stages"]) == 5 for s, _sub, _w in terminal)
        assert running[0]["historyTruncated"] is True and running[0]["stages"] == []
        assert running[0]["stageSource"] == "none"
        assert m_sfn.get_execution_history.call_count == le.MAX_SUB_STAGE_HISTORY_PAGES_PER_REQUEST


@pytest.mark.unit
class TestBatchStageStreamResolution:
    """A Batch stage leaves the history with a job id and no stream — the built-in machines discard the
    SubmitJob result, as verified live on prod5 — so DescribeJobs on that id, memoised per request, is
    what fills batch.logStreamName."""

    STREAM = "Preview3dThumbnailJobvams_prod5-us-west-21955306f5f/default/40199f4c0a1b4c2d9e8f7a6b5c4d3e2f"
    ERROR = "Pipeline Failure: Failed to load 3D file (.glb): incorrect header on GLB file"
    CAUSE = "See AWS cloudwatch logs for full error log and cause."

    def setup_method(self):
        le._state_machine_describe_cache.clear()
        le._batch_job_describe_cache.clear()

    def _stage(self, name, job_id, stream=""):
        return {"stageName": name, "stateType": "Task", "status": "FAILED", "caught": True,
                "startDate": "2026-09-11T10:00:04Z", "stopDate": "2026-09-11T10:01:35Z",
                "error": self.ERROR, "cause": self.CAUSE, "attempts": 1,
                "batch": {"jobId": job_id, "logStreamName": stream}}

    @staticmethod
    def _summary(*stages):
        return {"resourceType": "stepFunctionsExecution", "resourceName": "VAMSstateMachine-thumb",
                "stages": list(stages)}

    @staticmethod
    def _job(job_id, stream=None, attempts=None):
        job = {"jobId": job_id, "jobName": "Preview3dThumbnailJob", "status": "FAILED"}
        if stream is not None:
            job["container"] = {"logStreamName": stream, "exitCode": 1}
        if attempts is not None:
            job["attempts"] = attempts
        return job

    def test_a_stage_with_a_job_id_and_no_stream_is_filled_from_describe_jobs(self):
        summary, warnings = self._summary(self._stage("Preview3dThumbnailBatchJob", "job-1")), []
        with patch.object(le, "batch_client") as m_batch:
            m_batch.describe_jobs.return_value = {"jobs": [self._job("job-1", self.STREAM)]}
            le._resolve_batch_stage_streams(summary, warnings)
        m_batch.describe_jobs.assert_called_once_with(jobs=["job-1"])
        stage = summary["stages"][0]
        assert stage["batch"] == {"jobId": "job-1", "logStreamName": self.STREAM}
        assert warnings == []
        # Everything else on the stage is left exactly as folded.
        assert stage["error"] == self.ERROR and stage["cause"] == self.CAUSE and stage["caught"] is True

    def test_the_last_attempt_supplies_the_stream_when_the_container_block_has_none(self):
        summary, warnings = self._summary(self._stage("B", "job-2")), []
        with patch.object(le, "batch_client") as m_batch:
            m_batch.describe_jobs.return_value = {"jobs": [self._job(
                "job-2", attempts=[{"container": {"logStreamName": "jd/default/a1"}},
                                   {"container": {"logStreamName": "jd/default/a2"}}])]}
            le._resolve_batch_stage_streams(summary, warnings)
        assert summary["stages"][0]["batch"]["logStreamName"] == "jd/default/a2" and warnings == []

    def test_one_describe_per_distinct_job_id_across_stages_and_summaries(self):
        # Two summaries of one request naming the same job, plus a second distinct job: two
        # DescribeJobs calls, not three — the memo is per request, not per summary.
        first = self._summary(self._stage("A", "job-1"), self._stage("B", "job-2"))
        second = self._summary(self._stage("A", "job-1"))
        with patch.object(le, "batch_client") as m_batch:
            m_batch.describe_jobs.side_effect = lambda jobs: {"jobs": [self._job(jobs[0], f"jd/default/{jobs[0]}")]}
            le._resolve_batch_stage_streams(first, [])
            le._resolve_batch_stage_streams(second, [])
        assert m_batch.describe_jobs.call_count == 2
        assert sorted(c.kwargs["jobs"][0] for c in m_batch.describe_jobs.call_args_list) == ["job-1", "job-2"]
        assert first["stages"][0]["batch"]["logStreamName"] == "jd/default/job-1"
        assert first["stages"][1]["batch"]["logStreamName"] == "jd/default/job-2"
        assert second["stages"][0]["batch"]["logStreamName"] == "jd/default/job-1"

    def test_a_stage_that_already_carries_a_stream_or_has_no_job_id_is_not_described(self):
        summary = self._summary(
            self._stage("Kept", "job-9", stream="jd/default/from-history"),
            {"stageName": "Fn", "stateType": "Task", "status": "SUCCEEDED", "startDate": "", "stopDate": "",
             "error": "", "cause": "", "attempts": 1})
        with patch.object(le, "batch_client") as m_batch:
            le._resolve_batch_stage_streams(summary, [])
        m_batch.describe_jobs.assert_not_called()
        assert summary["stages"][0]["batch"]["logStreamName"] == "jd/default/from-history"
        assert "batch" not in summary["stages"][1]

    def test_a_job_batch_no_longer_lists_leaves_the_stream_empty_without_a_warning(self):
        # Batch keeps terminal jobs for about seven days; afterwards DescribeJobs answers jobs: [].
        # That is an expected outcome, not a failure: the stream stays empty, nothing is warned, and
        # the logs view later reports the prefix-only source as `unscoped`.
        summary, warnings = self._summary(self._stage("Preview3dThumbnailBatchJob", "job-old")), []
        with patch.object(le, "batch_client") as m_batch:
            m_batch.describe_jobs.return_value = {"jobs": []}
            le._resolve_batch_stage_streams(summary, warnings)
        m_batch.describe_jobs.assert_called_once_with(jobs=["job-old"])
        assert summary["stages"][0]["batch"] == {"jobId": "job-old", "logStreamName": ""}
        assert warnings == []

    def test_a_describe_failure_warns_and_leaves_the_stream_empty(self):
        summary, warnings = self._summary(self._stage("Preview3dThumbnailBatchJob", "job-1")), []
        with patch.object(le, "batch_client") as m_batch:
            m_batch.describe_jobs.side_effect = _client_error("AccessDeniedException", "DescribeJobs")
            le._resolve_batch_stage_streams(summary, warnings)
        assert summary["stages"][0]["batch"] == {"jobId": "job-1", "logStreamName": ""}
        assert any("job-1" in w and "AccessDeniedException" in w for w in warnings)

    def test_a_described_job_without_any_stream_leaves_the_stage_unchanged(self):
        summary, warnings = self._summary(self._stage("B", "job-3")), []
        with patch.object(le, "batch_client") as m_batch:
            m_batch.describe_jobs.return_value = {"jobs": [self._job("job-3")]}
            le._resolve_batch_stage_streams(summary, warnings)
        assert summary["stages"][0]["batch"] == {"jobId": "job-3", "logStreamName": ""} and warnings == []

    def test_deriving_stages_resolves_the_stream_as_part_of_the_pass(self):
        # End to end through _sub_execution_stages with the prod5 caught-failure history: the fold
        # leaves the jobId only, the resolver fills the stream, and the warnings list the caller passed
        # (the one assemble_execution_details stores as subExecutionWarnings) receives nothing.
        h = _History()
        t = h.entered("Task", "Preview3dThumbnailBatchJob", 1, prev=0)
        s = h.add("TaskScheduled", 2, prev=t, taskScheduledEventDetails={"resource": "submitJob.sync", "resourceType": "batch"})
        st = h.add("TaskStarted", 3, prev=s)
        sub = h.add("TaskSubmitted", 4, prev=st, taskSubmittedEventDetails={
            "resource": "submitJob.sync", "resourceType": "batch",
            "output": json.dumps({"JobArn": "arn:j", "JobId": "job-1", "JobName": "Preview3dThumbnailJob"})})
        f = h.add("TaskFailed", 90, prev=sub, taskFailedEventDetails={
            "resource": "submitJob.sync", "resourceType": "batch", "error": self.ERROR, "cause": self.CAUSE})
        h.exited("Task", "Preview3dThumbnailBatchJob", 91, prev=f)
        h.entered("Fail", "FailState", 92)
        h.add("ExecutionFailed", 93, executionFailedEventDetails={"error": "PipelineFailed", "cause": "x"})
        summary = {"resourceType": "stepFunctionsExecution", "label": "", "stageName": "", "resourceName": "x",
                   "status": "FAILED", "startDate": "", "stopDate": "", "error": "", "cause": "",
                   "stageSource": "none", "stagesTruncated": False, "historyTruncated": False, "stages": []}
        sub_entry = {"resourceType": "stepFunctionsExecution",
                     "stateMachineArn": "arn:aws:states:us-west-2:1:stateMachine:VAMSstateMachine-x",
                     "executionArn": "arn:aws:states:us-west-2:1:execution:VAMSstateMachine-x:run"}
        warnings = []
        with patch.object(le, "sfn") as m_sfn, patch.object(le, "batch_client") as m_batch:
            m_sfn.describe_state_machine.return_value = _described(definition=None)
            m_sfn.get_execution_history.return_value = _page(h.events)
            m_batch.describe_jobs.return_value = {"jobs": [self._job("job-1", self.STREAM)]}
            le._sub_execution_stages(summary, sub_entry, [10], warnings)
        m_batch.describe_jobs.assert_called_once_with(jobs=["job-1"])
        stage = next(s for s in summary["stages"] if s["stageName"] == "Preview3dThumbnailBatchJob")
        assert stage["status"] == "FAILED" and stage["caught"] is True
        assert stage["error"] == self.ERROR and stage["cause"] == self.CAUSE
        assert stage["batch"] == {"jobId": "job-1", "logStreamName": self.STREAM}
        assert summary["stageSource"] == "history" and warnings == []
