#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Per-stage status of a Step Functions sub-execution, derived from its state machine definition and
its execution history, plus the status folds for the other registered sub-process types.

Pure helpers with no AWS or environment dependencies: the caller supplies the parsed ASL definition
(DescribeStateMachine) and the raw history events (GetExecutionHistory) and receives an ordered list
of stages with a status each; a Batch or Deadline Cloud job's raw status is folded onto the same
vocabulary. Dates are rendered as ISO-8601 strings at capture so the result is JSON-serializable as is.
"""

import json
from datetime import datetime, timezone

from common.logRedaction import redact_log_text

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

STATUS_RUNNING = "RUNNING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_ABORTED = "ABORTED"
STATUS_TIMED_OUT = "TIMED_OUT"
STATUS_NOT_STARTED = "NOT_STARTED"
STATUS_UNKNOWN = "UNKNOWN"

# AWS Batch job statuses folded onto the execution status vocabulary.
BATCH_STATUS_MAP = {
    "SUBMITTED": STATUS_RUNNING, "PENDING": STATUS_RUNNING, "RUNNABLE": STATUS_RUNNING,
    "STARTING": STATUS_RUNNING, "RUNNING": STATUS_RUNNING,
    "SUCCEEDED": STATUS_SUCCEEDED, "FAILED": STATUS_FAILED,
}

# Deadline Cloud job task-run statuses folded onto the execution status vocabulary. A job whose
# lifecycle failed (create/update/upload) never ran to completion, whatever its task-run status says.
DEADLINE_TASK_RUN_STATUS_MAP = {
    "PENDING": STATUS_RUNNING, "READY": STATUS_RUNNING, "ASSIGNED": STATUS_RUNNING,
    "STARTING": STATUS_RUNNING, "SCHEDULED": STATUS_RUNNING, "INTERRUPTING": STATUS_RUNNING,
    "RUNNING": STATUS_RUNNING, "SUSPENDED": STATUS_RUNNING,
    "SUCCEEDED": STATUS_SUCCEEDED,
    "FAILED": STATUS_FAILED, "NOT_COMPATIBLE": STATUS_FAILED,
    "CANCELED": STATUS_ABORTED,
}
DEADLINE_FAILED_LIFECYCLE_STATUSES = frozenset(("CREATE_FAILED", "UPDATE_FAILED", "UPLOAD_FAILED"))


def iso_utc(value):
    """`value` as an ISO-8601 UTC string: an aware or naive datetime, or epoch milliseconds. Empty
    for None/""; anything else is stringified."""
    if value is None or value == "":
        return ""
    if hasattr(value, "strftime"):
        if getattr(value, "tzinfo", None) is not None:
            value = value.astimezone(timezone.utc)
        return value.strftime(ISO_FORMAT)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).strftime(ISO_FORMAT)
    return str(value)


def state_machine_arn_from_execution_arn(execution_arn):
    """arn:p:states:r:a:execution:<machine>:<name> -> arn:p:states:r:a:stateMachine:<machine>; ""
    when the value is not an execution ARN."""
    parts = (execution_arn or "").split(":")
    if len(parts) < 8 or parts[5] != "execution":
        return ""
    return ":".join(parts[:5] + ["stateMachine", parts[6]])


def is_batch_submit_job(resource, resource_type=""):
    """True for the Step Functions Batch SubmitJob integration, whether given the history detail pair
    (resource 'submitJob.sync', resourceType 'batch') or the definition's Resource ARN."""
    resource = resource or ""
    if resource_type == "batch" and resource.startswith("submitJob"):
        return True
    return ":batch:submitJob" in resource


def _as_dict(payload):
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str) and payload:
        try:
            parsed = json.loads(payload)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _first(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if value:
            return value
    return None


def batch_job_id_from_output(payload):
    """The Batch job id in a SubmitJob / DescribeJobs payload (PascalCase as Step Functions renders
    it, or camelCase as boto3 returns it)."""
    job = _as_dict(payload)
    return str(_first(job, "JobId", "jobId") or "")


def batch_log_stream_from_job(payload):
    """The container log stream of a Batch job payload: Container.LogStreamName, else the last
    attempt's; "" when the payload carries none."""
    job = _as_dict(payload)
    container = _first(job, "Container", "container") or {}
    stream = _first(container, "LogStreamName", "logStreamName") if isinstance(container, dict) else None
    if stream:
        return str(stream)
    for attempt in reversed(_first(job, "Attempts", "attempts") or []):
        if not isinstance(attempt, dict):
            continue
        attempt_container = _first(attempt, "Container", "container") or {}
        stream = _first(attempt_container, "LogStreamName", "logStreamName") \
            if isinstance(attempt_container, dict) else None
        if stream:
            return str(stream)
    return ""


def map_batch_status(status):
    return BATCH_STATUS_MAP.get(status or "", STATUS_UNKNOWN)


def map_deadline_status(task_run_status, lifecycle_status=""):
    """A Deadline Cloud job's status on the execution vocabulary: FAILED for a failed lifecycle status,
    else the task-run status folded through DEADLINE_TASK_RUN_STATUS_MAP; UNKNOWN for anything else."""
    if (lifecycle_status or "") in DEADLINE_FAILED_LIFECYCLE_STATUSES:
        return STATUS_FAILED
    return DEADLINE_TASK_RUN_STATUS_MAP.get(task_run_status or "", STATUS_UNKNOWN)


MAX_SUB_STAGES_REPORTED_DEFAULT = 50
MAX_SUB_STAGE_DEPTH_DEFAULT = 3


def stages_from_definition(definition, max_stages=MAX_SUB_STAGES_REPORTED_DEFAULT,
                           max_depth=MAX_SUB_STAGE_DEPTH_DEFAULT):
    """The states of a parsed ASL definition as an ordered frame of stages.

    Breadth-first over StartAt/Next, a Choice's Default and every Choices[].Next, and a Catch's
    Next, recursing into each Parallel branch and each Map ItemProcessor (or legacy Iterator) as it
    is met, with a visited set so a Choice loop terminates. Returns (stages, truncated); each stage is {stageName, stateType, depth}
    plus `parent` inside a branch/processor and `resource` for a Task. `truncated` is True when the
    stage cap or the depth cap stopped the walk."""
    if not isinstance(definition, dict):
        return [], False
    states = definition.get("States")
    start_at = definition.get("StartAt")
    if not isinstance(states, dict) or not start_at:
        return [], False
    stages, seen = [], set()
    truncated = [False]

    def walk(state_map, first, depth, parent):
        if depth > max_depth:
            truncated[0] = True
            return
        queue = [first]
        while queue:
            name = queue.pop(0)
            if not name or name in seen:
                continue
            state = state_map.get(name)
            if not isinstance(state, dict):
                continue
            if len(stages) >= max_stages:
                truncated[0] = True
                return
            seen.add(name)
            state_type = state.get("Type", "") or ""
            stage = {"stageName": name, "stateType": state_type, "depth": depth}
            if parent:
                stage["parent"] = parent
            if state_type == "Task":
                stage["resource"] = state.get("Resource", "") or ""
            stages.append(stage)
            if state_type == "Parallel":
                for branch in state.get("Branches") or []:
                    if isinstance(branch, dict):
                        walk(branch.get("States") or {}, branch.get("StartAt", ""), depth + 1, name)
            elif state_type == "Map":
                processor = state.get("ItemProcessor") or state.get("Iterator") or {}
                if isinstance(processor, dict):
                    walk(processor.get("States") or {}, processor.get("StartAt", ""), depth + 1, name)
            if state_type == "Choice":
                if state.get("Default"):
                    queue.append(state["Default"])
                for choice in state.get("Choices") or []:
                    if isinstance(choice, dict) and choice.get("Next"):
                        queue.append(choice["Next"])
            elif state.get("Next"):
                queue.append(state["Next"])
            for catcher in state.get("Catch") or []:
                if isinstance(catcher, dict) and catcher.get("Next"):
                    queue.append(catcher["Next"])

    walk(states, start_at, 0, "")
    return stages, truncated[0]


MAX_SUB_STAGE_ERROR_CHARS_DEFAULT = 256

_ENTERED_SUFFIX = "StateEntered"
_EXITED_SUFFIX = "StateExited"
_ABORTED_SUFFIX = "StateAborted"
_SCHEDULED_TYPES = ("TaskScheduled", "LambdaFunctionScheduled", "ActivityScheduled")
_SUCCEEDED_TYPES = ("TaskSucceeded", "LambdaFunctionSucceeded", "ActivitySucceeded")
_FAILED_TYPES = (
    "TaskFailed", "TaskTimedOut", "TaskStartFailed", "TaskSubmitFailed",
    "LambdaFunctionFailed", "LambdaFunctionTimedOut", "LambdaFunctionScheduleFailed",
    "LambdaFunctionStartFailed",
    "ActivityFailed", "ActivityTimedOut", "ActivityScheduleFailed",
)
# Container states whose branch/iteration outcome events carry no state name and no detail block.
_CONTAINER_KINDS = ("Parallel", "Map")
_CONTAINER_FAILED_TYPES = tuple(kind + "StateFailed" for kind in _CONTAINER_KINDS)
_CONTAINER_SUCCEEDED_TYPES = tuple(kind + "StateSucceeded" for kind in _CONTAINER_KINDS)
_CONTAINER_EXITED_TYPES = tuple(kind + _EXITED_SUFFIX for kind in _CONTAINER_KINDS)
_EXECUTION_CLOSERS = {
    "ExecutionSucceeded": STATUS_SUCCEEDED,
    "ExecutionFailed": STATUS_FAILED,
    "ExecutionAborted": STATUS_ABORTED,
    "ExecutionTimedOut": STATUS_TIMED_OUT,
}
_STATUS_PRECEDENCE = (STATUS_RUNNING, STATUS_FAILED, STATUS_ABORTED, STATUS_TIMED_OUT, STATUS_SUCCEEDED)


def _details(event):
    """The one *EventDetails block a history event carries, or {}."""
    for key, value in event.items():
        if key.endswith("EventDetails") and isinstance(value, dict):
            return value
    return {}


def _new_instance(name, entered_type, entered_id, timestamp):
    return {
        "name": name, "enteredType": entered_type, "enteredId": entered_id,
        "status": STATUS_RUNNING, "startDate": iso_utc(timestamp), "stopDate": "",
        "error": "", "cause": "", "attempts": 0, "failed": False, "closed": False, "caught": False,
        "isBatch": False, "batch": {}, "iterations": None, "distributed": False,
    }


def fold_history(events, frame, max_stages=MAX_SUB_STAGES_REPORTED_DEFAULT,
                 max_error_chars=MAX_SUB_STAGE_ERROR_CHARS_DEFAULT):
    """Fold raw GetExecutionHistory events onto a stage frame.

    Each *StateEntered event opens an INSTANCE of a state; Task/LambdaFunction/Activity events carry no
    state name and are attributed to the nearest TaskStateEntered up their previousEventId chain; a
    *StateExited closes the instance as SUCCEEDED, or FAILED with `caught` when a failure was recorded
    and not recovered by a later success; a Fail state, an abort, or an execution-level terminal event
    closes with that status. Parallel/MapStateFailed and Parallel/MapStateSucceeded carry no name
    either: they mark (or clear) the failure of the nearest open Parallel/Map up the chain, taking the
    failing branch state's error, and a container's *StateExited also closes its still-open branch or
    iteration states (a failed branch has no *StateExited of its own). ExecutionSucceeded closes an
    open instance that recorded a failure as FAILED and caught, since the run continued past it; the
    ExecutionFailed that follows a Fail state hands that state its Error/Cause. Instances of one state
    (Map iterations) fold into one reported stage: RUNNING if any is open, else
    FAILED/ABORTED/TIMED_OUT if any ended so, else SUCCEEDED; `attempts` is the largest per-instance
    schedule count so an iteration is not counted as a retry.

    Returns {"stages": [...], "stagesTruncated": bool}: the frame's stages in frame order (never-entered
    ones NOT_STARTED), then states seen only in history in first-entered order, capped at max_stages."""
    by_id = {ev.get("id"): ev for ev in events if isinstance(ev, dict) and ev.get("id") is not None}
    instances = {}
    instance_ids_by_name = {}
    open_by_name = {}
    first_entered_order = []
    owner_cache = {}

    def owner(event, task_only=True):
        """The entered-event id owning `event`: the nearest *StateEntered up the previousEventId chain
        (TaskStateEntered only, for Task/LambdaFunction/Activity events)."""
        eid = event.get("id")
        if eid in owner_cache:
            return owner_cache[eid]
        current = by_id.get(event.get("previousEventId"))
        hops = 0
        found = None
        while current is not None and hops <= len(by_id):
            ctype = current.get("type", "") or ""
            if current.get("id") in instances and ctype.endswith(_ENTERED_SUFFIX) \
                    and (not task_only or ctype == "TaskStateEntered"):
                found = current.get("id")
                break
            current = by_id.get(current.get("previousEventId"))
            hops += 1
        owner_cache[eid] = found
        return found

    def close(instance, status, timestamp, caught=False):
        if instance["closed"]:
            return
        instance["status"] = status
        instance["stopDate"] = iso_utc(timestamp)
        instance["closed"] = True
        instance["caught"] = caught
        open_ids = open_by_name.get(instance["name"], [])
        if instance["enteredId"] in open_ids:
            open_ids.remove(instance["enteredId"])

    def pop_open(name):
        open_ids = open_by_name.get(name) or []
        return instances.get(open_ids[-1]) if open_ids else None

    def latest_open_of_kind(kind):
        candidates = [inst for inst in instances.values()
                      if not inst["closed"] and inst["enteredType"] == kind + _ENTERED_SUFFIX]
        return candidates[-1] if candidates else None

    def latest_instance(name):
        ids = instance_ids_by_name.get(name) or []
        return instances.get(ids[-1]) if ids else None

    def owning_container(event, kind):
        """The nearest OPEN Parallel/Map instance up the previousEventId chain (the chain runs through
        the failing branch's states first), else the latest open one of that kind."""
        entered_type = kind + _ENTERED_SUFFIX
        current = by_id.get(event.get("previousEventId"))
        hops = 0
        while current is not None and hops <= len(by_id):
            instance = instances.get(current.get("id"))
            if instance is not None and not instance["closed"] and instance["enteredType"] == entered_type:
                return instance
            current = by_id.get(current.get("previousEventId"))
            hops += 1
        return latest_open_of_kind(kind)

    def descends_from(instance, container_entered_id):
        """True when `instance` was entered inside the container instance opened by that event id."""
        current = by_id.get(instance["enteredId"])
        hops = 0
        while current is not None and hops <= len(by_id):
            current = by_id.get(current.get("previousEventId"))
            if current is not None and current.get("id") == container_entered_id:
                return True
            hops += 1
        return False

    def latest_closed_fail_without_error():
        candidates = [inst for inst in instances.values()
                      if inst["closed"] and inst["enteredType"] == "FailStateEntered"
                      and not inst["error"] and not inst["cause"]]
        return candidates[-1] if candidates else None

    for event in events:
        if not isinstance(event, dict):
            continue
        etype = event.get("type", "") or ""
        details = _details(event)
        timestamp = event.get("timestamp")
        eid = event.get("id")

        if etype.endswith(_ENTERED_SUFFIX):
            name = details.get("name", "") or ""
            if not name:
                continue
            instance = _new_instance(name, etype, eid, timestamp)
            instances[eid] = instance
            instance_ids_by_name.setdefault(name, []).append(eid)
            open_by_name.setdefault(name, []).append(eid)
            if name not in instance_ids_by_name or len(instance_ids_by_name[name]) == 1:
                first_entered_order.append(name)
            if etype == "FailStateEntered":
                close(instance, STATUS_FAILED, timestamp)
            elif etype == "SucceedStateEntered":
                close(instance, STATUS_SUCCEEDED, timestamp)
            continue

        if etype.endswith(_EXITED_SUFFIX):
            instance = pop_open(details.get("name", "") or "")
            if instance is not None:
                close(instance, STATUS_FAILED if instance["failed"] else STATUS_SUCCEEDED, timestamp,
                      caught=instance["failed"])
                if etype in _CONTAINER_EXITED_TYPES:
                    # A branch or iteration state that failed never exits on its own; it ends with
                    # its container, not with the execution.
                    for inner in list(instances.values()):
                        if not inner["closed"] and descends_from(inner, instance["enteredId"]):
                            close(inner, STATUS_FAILED if inner["failed"] else STATUS_SUCCEEDED,
                                  timestamp, caught=inner["failed"])
            continue

        if etype.endswith(_ABORTED_SUFFIX):
            name = details.get("name", "") or ""
            instance = pop_open(name) if name else latest_open_of_kind(etype[:-len(_ABORTED_SUFFIX)])
            if instance is not None:
                close(instance, STATUS_ABORTED, timestamp)
            continue

        if etype in _SCHEDULED_TYPES:
            instance = instances.get(owner(event))
            if instance is not None:
                instance["attempts"] += 1
                if is_batch_submit_job(details.get("resource", ""), details.get("resourceType", "")):
                    instance["isBatch"] = True
            continue

        if etype == "TaskSubmitted":
            instance = instances.get(owner(event))
            if instance is not None and (instance["isBatch"] or is_batch_submit_job(
                    details.get("resource", ""), details.get("resourceType", ""))):
                instance["isBatch"] = True
                job_id = batch_job_id_from_output(details.get("output"))
                if job_id:
                    instance["batch"]["jobId"] = job_id
            continue

        if etype in _SUCCEEDED_TYPES:
            instance = instances.get(owner(event))
            if instance is not None:
                instance["failed"] = False
                instance["error"] = ""
                instance["cause"] = ""
                if instance["isBatch"]:
                    # Opportunistic: only a machine that keeps the SubmitJob result has the
                    # DescribeJobs object here; the built-in machines return the state's own input,
                    # which parses to no stream and no job id.
                    stream = batch_log_stream_from_job(details.get("output"))
                    if stream:
                        instance["batch"]["logStreamName"] = stream
                    job_id = batch_job_id_from_output(details.get("output"))
                    if job_id:
                        instance["batch"]["jobId"] = job_id
            continue

        if etype in _FAILED_TYPES:
            instance = instances.get(owner(event))
            if instance is not None:
                cause = details.get("cause", "") or ""
                instance["failed"] = True
                instance["error"] = details.get("error", "") or ""
                if instance["isBatch"] or is_batch_submit_job(details.get("resource", ""),
                                                              details.get("resourceType", "")):
                    # Opportunistic: a cause that happens to carry the DescribeJobs object yields the
                    # stream and job id before the cause is shortened for display. The built-in
                    # machines' containers fail the task with a plain string, which parses to nothing
                    # and is kept as the cause; the stream then comes from DescribeJobs on the
                    # submitted job id (executionService._resolve_batch_stage_streams).
                    stream = batch_log_stream_from_job(cause)
                    if stream:
                        instance["batch"]["logStreamName"] = stream
                    job_id = batch_job_id_from_output(cause)
                    if job_id:
                        instance["batch"]["jobId"] = job_id
                # Redact AFTER the parse above (it needs the raw JSON) and BEFORE the slice: that same
                # DescribeJobs object carries the task token as a container environment
                # {"Name","Value"} pair, and a cut mid-value would keep whatever fits.
                instance["cause"] = redact_log_text(cause)[:max_error_chars]
            continue

        if etype.startswith("MapIteration"):
            instance = latest_instance(details.get("name", "") or "")
            if instance is not None:
                counters = instance["iterations"] or {"started": 0, "succeeded": 0, "failed": 0, "aborted": 0}
                key = etype[len("MapIteration"):].lower()
                if key in counters:
                    counters[key] += 1
                instance["iterations"] = counters
            continue

        if etype.startswith("MapRun"):
            instance = instances.get(owner(event, task_only=False))
            if instance is None:
                continue
            if etype == "MapRunStarted":
                instance["distributed"] = True
            elif etype == "MapRunFailed":
                instance["failed"] = True
                instance["error"] = details.get("error", "") or ""
                instance["cause"] = redact_log_text(details.get("cause", "") or "")[:max_error_chars]
            elif etype == "MapRunAborted":
                close(instance, STATUS_ABORTED, timestamp)
            continue

        if etype in _CONTAINER_FAILED_TYPES or etype in _CONTAINER_SUCCEEDED_TYPES:
            instance = owning_container(event, etype[:etype.index("State")])
            if instance is None:
                continue
            if etype in _CONTAINER_SUCCEEDED_TYPES:
                # A Retry on the container re-ran its branches and they came through.
                instance["failed"] = False
                instance["error"] = ""
                instance["cause"] = ""
                continue
            instance["failed"] = True
            # The event carries no detail block; the failing branch's state is the nearest instance up
            # the chain and holds the error the container's Catch received.
            source = details if (details.get("error") or details.get("cause")) else None
            if source is None:
                branch_state = instances.get(owner(event, task_only=False))
                if branch_state is not None and branch_state is not instance:
                    source = branch_state
            if source is not None:
                instance["error"] = source.get("error", "") or ""
                instance["cause"] = redact_log_text(source.get("cause", "") or "")[:max_error_chars]
            continue

        if etype in _EXECUTION_CLOSERS:
            status = _EXECUTION_CLOSERS[etype]
            error = details.get("error", "") or ""
            cause = redact_log_text(details.get("cause", "") or "")[:max_error_chars]
            if error or cause:
                # A Fail state closes on entry with no error of its own; the ExecutionFailed that
                # follows it is where its Error/Cause appear.
                raised = instances.get(owner(event, task_only=False))
                if raised is None and status == STATUS_FAILED:
                    raised = latest_closed_fail_without_error()
                if (raised is not None and raised["enteredType"] == "FailStateEntered"
                        and not raised["error"] and not raised["cause"]):
                    raised["error"] = error
                    raised["cause"] = cause
            for instance in list(instances.values()):
                if instance["closed"]:
                    continue
                if status == STATUS_FAILED and not instance["error"]:
                    instance["error"] = error
                    instance["cause"] = cause
                if status == STATUS_SUCCEEDED and instance["failed"]:
                    # The run finished cleanly, so this failure was caught by a container that had no
                    # *StateExited for the branch to close on.
                    close(instance, STATUS_FAILED, timestamp, caught=True)
                else:
                    close(instance, status, timestamp)

    frame = [f for f in (frame or []) if isinstance(f, dict) and f.get("stageName")]
    frame_by_name = {f["stageName"]: f for f in frame}
    names = [f["stageName"] for f in frame] + [n for n in first_entered_order if n not in frame_by_name]
    truncated = len(names) > max_stages
    names = names[:max_stages]

    stages = []
    for name in names:
        frame_stage = frame_by_name.get(name, {})
        ids = instance_ids_by_name.get(name) or []
        if not ids:
            stages.append({"stageName": name, "stateType": frame_stage.get("stateType", ""),
                           "status": STATUS_NOT_STARTED, "startDate": "", "stopDate": "",
                           "error": "", "cause": "", "attempts": 0})
            continue
        insts = [instances[i] for i in ids]
        stages.append(_fold_instances(name, insts, frame_stage))
    return {"stages": stages, "stagesTruncated": truncated}


def _fold_instances(name, insts, frame_stage):
    """One reported stage from every instance of a state."""
    statuses = [inst["status"] for inst in insts]
    status = next((s for s in _STATUS_PRECEDENCE if s in statuses), STATUS_SUCCEEDED)
    state_type = frame_stage.get("stateType") or insts[0]["enteredType"][:-len(_ENTERED_SUFFIX)]
    failed_insts = [inst for inst in insts if inst["status"] == STATUS_FAILED]
    with_error = [inst for inst in insts if inst["error"] or inst["cause"]]
    last_error = with_error[-1] if with_error else None
    stage = {
        "stageName": name,
        "stateType": state_type,
        "status": status,
        "startDate": insts[0]["startDate"],
        "stopDate": insts[-1]["stopDate"] if insts[-1]["closed"] else "",
        "error": last_error["error"] if last_error else "",
        "cause": last_error["cause"] if last_error else "",
        "attempts": max(inst["attempts"] for inst in insts),
    }
    if status == STATUS_FAILED and failed_insts and all(inst["caught"] for inst in failed_insts):
        stage["caught"] = True
    if any(inst["distributed"] for inst in insts):
        stage["distributed"] = True
    else:
        counters = None
        for inst in insts:
            if inst["iterations"]:
                counters = counters or {"started": 0, "succeeded": 0, "failed": 0, "aborted": 0}
                for key, value in inst["iterations"].items():
                    counters[key] = counters.get(key, 0) + value
        if counters:
            stage["iterations"] = counters
    batch = {}
    for inst in insts:
        for key, value in inst["batch"].items():
            if value:
                batch[key] = value
    if batch:
        stage["batch"] = {"jobId": batch.get("jobId", ""), "logStreamName": batch.get("logStreamName", "")}
    return stage


def history_events_for_stage(events, stage_name):
    """The events between each *StateEntered and matching *StateExited/*StateAborted of `stage_name`
    (ids inclusive; to the end of the list when the state is still open), across every instance."""
    ranges, open_start = [], None
    for event in events:
        if not isinstance(event, dict):
            continue
        etype = event.get("type", "") or ""
        if (_details(event).get("name", "") or "") != stage_name:
            continue
        if etype.endswith(_ENTERED_SUFFIX) and open_start is None:
            open_start = event.get("id")
        elif (etype.endswith(_EXITED_SUFFIX) or etype.endswith(_ABORTED_SUFFIX)) and open_start is not None:
            ranges.append((open_start, event.get("id")))
            open_start = None
    if open_start is not None:
        ranges.append((open_start, None))
    if not ranges:
        return []
    out = []
    for event in events:
        eid = event.get("id") if isinstance(event, dict) else None
        if eid is None:
            continue
        if any(start <= eid and (end is None or eid <= end) for start, end in ranges):
            out.append(event)
    return out
