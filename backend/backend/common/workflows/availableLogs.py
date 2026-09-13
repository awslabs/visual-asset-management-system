#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The CloudWatch log sources one pipeline execution can offer, identified by location.

Pure helpers with no AWS dependencies. A source is (kind, log group, exact stream, stream prefix);
its logId is a hash of exactly that, so the same place registered twice — or spelled with and without
the ':*' wildcard — is one source. Entries carry private keys the read path needs and the public
projection omits: the log-group ARN to read, the sub-execution ARNs whose state machine logs there, and
the farm/queue/job ids of the Deadline Cloud jobs whose sessions log there.
"""

import hashlib
import json
import re

KIND_INVOCATION = "invocation"
KIND_REGISTERED = "registered"
KIND_SUB_STATE_MACHINE = "subStateMachine"
KIND_DEADLINE_CLOUD_JOB = "deadlineCloudJob"

SOURCE_TYPE_STATE_MACHINE = "stateMachine"
SOURCE_TYPE_LAMBDA = "lambda"
SOURCE_TYPE_BATCH = "batch"
SOURCE_TYPE_CONTAINER = "container"
SOURCE_TYPE_CUSTOM = "custom"
SOURCE_TYPE_DEADLINE_CLOUD = "deadlineCloud"

# A Deadline Cloud queue's session logs: one group per farm/queue, shared by every job of the queue,
# with one stream per session named by the session id. The lines carry no VAMS id, so the group is only
# ever read by the exact stream names of a job's own sessions, never by this prefix.
DEADLINE_SESSION_STREAM_PREFIX = "session-"
DEADLINE_JOB_SOURCE_LABEL = "Deadline Cloud job sessions"

PUBLIC_KEYS = ("logId", "kind", "label", "sourceType", "stageName",
               "logGroupName", "logStreamName", "logStreamPrefix")
_MERGED_FIELDS = ("stageName", "label", "sourceType")

_STATE_MACHINE_GROUP = re.compile(r"VAMS[Ss]tateMachine-")


def strip_wildcard(arn):
    arn = arn or ""
    return arn[:-2] if arn.endswith(":*") else arn


def log_group_name_from_arn(arn):
    """The log group name inside a log-group ARN ("" when the value is not one)."""
    parts = (arn or "").split(":log-group:")
    return strip_wildcard(parts[1]) if len(parts) >= 2 else ""


def derive_log_group_arn(name, reference_arn):
    """A log-group ARN for `name` in the partition/region/account of `reference_arn` (the execution's
    own log group is always same-account, same-partition); "" when either is unusable."""
    if not name or not (reference_arn or "").startswith("arn:"):
        return ""
    parts = reference_arn.split(":")
    if len(parts) < 5 or not (parts[1] and parts[3] and parts[4]):
        return ""
    return f"arn:{parts[1]}:logs:{parts[3]}:{parts[4]}:log-group:{name}"


def deadline_log_group_name(farm_id, queue_id):
    return f"/aws/deadline/{farm_id}/{queue_id}"


def location_key(arn, stream, prefix):
    return (strip_wildcard(arn), stream or "", prefix or "")


def log_id(kind, arn, stream, prefix):
    """Sixteen hex characters of sha256 over the location; stable across requests and safe to echo
    back as a query parameter (it satisfies the ID validator)."""
    payload = json.dumps([kind, strip_wildcard(arn), stream or "", prefix or ""])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def derive_source_type(group_name):
    name = group_name or ""
    if name.startswith("/aws/lambda/"):
        return SOURCE_TYPE_LAMBDA
    if _STATE_MACHINE_GROUP.search(name):
        return SOURCE_TYPE_STATE_MACHINE
    if name == "/aws/batch/job" or name.startswith("/aws/batch/job/"):
        return SOURCE_TYPE_BATCH
    if name.startswith("/aws/vendedlogs/Pipelines/"):
        return SOURCE_TYPE_CONTAINER
    return SOURCE_TYPE_CUSTOM


def derive_label(group_name):
    name = (group_name or "").rstrip("/")
    return name.split("/")[-1] if name else ""


def _entry(kind, arn, stream, prefix, stage_name, label, source_type, execution_arn=""):
    return {
        "kind": kind,
        "_logGroupArn": arn,
        "_executionArns": [execution_arn] if execution_arn else [],
        "logStreamName": stream or "",
        "logStreamPrefix": prefix or "",
        "stageName": stage_name or "",
        "label": label or "",
        "sourceType": source_type or "",
    }


def dedup_merge(entries):
    """Collapse entries that point at one location. The first is kept; later copies fill descriptive
    fields it left empty and contribute their sub-execution ARNs."""
    kept, index_by_location = [], {}
    for entry in entries:
        key = location_key(entry["_logGroupArn"], entry["logStreamName"], entry["logStreamPrefix"])
        i = index_by_location.get(key)
        if i is None:
            index_by_location[key] = len(kept)
            kept.append(entry)
            continue
        target = kept[i]
        for field in _MERGED_FIELDS:
            if not target.get(field) and entry.get(field):
                target[field] = entry[field]
        for execution_arn in entry.get("_executionArns") or []:
            if execution_arn not in target["_executionArns"]:
                target["_executionArns"].append(execution_arn)
        for job in entry.get("_deadlineJobs") or []:
            jobs = target.setdefault("_deadlineJobs", [])
            if job not in jobs:
                jobs.append(job)
    return kept


def build_available_logs(invocation_log_group_arn, registered_logs, sub_state_machine_logs,
                         reference_log_group_arn, deadline_jobs=None):
    """Every log source of one pipeline execution, deduplicated by location, with logId, group name and
    derived sourceType/label filled in. `registered_logs` are the row's stored entries;
    `sub_state_machine_logs` are {logGroupArn, stageName, label, executionArn} for each registered
    Step Functions sub-execution whose state machine has a logging destination; `deadline_jobs` are
    {farmId, queueId, jobId} for each registered Deadline Cloud job, each offered as its queue's session
    log group (in the reference ARN's partition, region and account) with the private `_deadline` ids
    the read path lists sessions with. Two jobs on one queue are one source carrying both jobs, and a
    Deadline entry precedes the registered ones so that a pipeline which also registered its queue's
    session group by name keeps the exact-stream read (the kind drives it)."""
    entries = []
    if invocation_log_group_arn:
        entries.append(_entry(KIND_INVOCATION, invocation_log_group_arn, "", "", "", "", ""))
    for job in deadline_jobs or []:
        ids = {key: str((job or {}).get(key) or "") for key in ("farmId", "queueId", "jobId")}
        if not all(ids.values()):
            continue
        arn = derive_log_group_arn(deadline_log_group_name(ids["farmId"], ids["queueId"]),
                                   reference_log_group_arn)
        if not arn:
            continue
        entry = _entry(KIND_DEADLINE_CLOUD_JOB, arn, "", DEADLINE_SESSION_STREAM_PREFIX, "",
                       DEADLINE_JOB_SOURCE_LABEL, SOURCE_TYPE_DEADLINE_CLOUD)
        entry["_deadline"] = ids
        entry["_deadlineJobs"] = [ids]
        entries.append(entry)
    for log in registered_logs or []:
        if not isinstance(log, dict):
            continue
        arn = log.get("logGroupArn") or derive_log_group_arn(log.get("logGroupName") or "",
                                                            reference_log_group_arn)
        if not arn:
            continue
        entries.append(_entry(KIND_REGISTERED, arn, log.get("logStreamName"), log.get("logStreamPrefix"),
                              log.get("stageName"), log.get("label"), log.get("sourceType")))
    for sub in sub_state_machine_logs or []:
        arn = (sub or {}).get("logGroupArn") or ""
        if not arn:
            continue
        entries.append(_entry(KIND_SUB_STATE_MACHINE, arn, "", "", sub.get("stageName"), sub.get("label"),
                              SOURCE_TYPE_STATE_MACHINE, execution_arn=sub.get("executionArn") or ""))
    merged = dedup_merge(entries)
    for entry in merged:
        name = log_group_name_from_arn(entry["_logGroupArn"])
        entry["logGroupName"] = name
        entry["sourceType"] = entry["sourceType"] or derive_source_type(name)
        entry["label"] = entry["label"] or derive_label(name)
        entry["logId"] = log_id(entry["kind"], entry["_logGroupArn"], entry["logStreamName"],
                                entry["logStreamPrefix"])
    return merged


def public_entry(entry):
    """The response projection of an entry: names and identifiers only, never an ARN."""
    return {key: entry.get(key, "") for key in PUBLIC_KEYS}


STATUS_READ = "read"
STATUS_DENIED = "denied"
STATUS_NOT_FOUND = "notFound"
STATUS_ERROR = "error"
STATUS_EMPTY = "empty"
STATUS_SKIPPED = "skipped"
STATUS_UNSCOPED = "unscoped"


def registered_prefixes(registered_logs):
    """Every non-empty stream prefix the pipeline execution registered."""
    return [log.get("logStreamPrefix") for log in (registered_logs or [])
            if isinstance(log, dict) and log.get("logStreamPrefix")]


def resolve_batch_stream(entry, sub_summaries):
    """The exact container stream for a Batch entry registered with only a prefix: the
    `batch.logStreamName` a sub-state-machine stage of the same name carries (filled by DescribeJobs on
    the submitted job id, or from the history when a machine kept the SubmitJob result), else the stream
    of a registered batchJob whose stageName matches the entry's (an unlabelled entry or job matches
    anything). "" when unresolved — a job Batch no longer lists, or a stage that never submitted one."""
    stage_name = entry.get("stageName") or ""
    for summary in sub_summaries or []:
        for stage in (summary or {}).get("stages") or []:
            batch = (stage or {}).get("batch") or {}
            if stage_name and stage.get("stageName") == stage_name and batch.get("logStreamName"):
                return batch["logStreamName"]
    for summary in sub_summaries or []:
        if (summary or {}).get("resourceType") != "batchJob":
            continue
        stream = ((summary.get("batch") or {}).get("logStreamName")) or ""
        job_stage = summary.get("stageName") or ""
        if stream and (not stage_name or not job_stage or job_stage == stage_name):
            return stream
    return ""


def plan_source_read(entry, prefixes, resolved_stream="", stream_names=None):
    """How one source is read from CloudWatch.

    A registered entry with an exact stream is read without the execution-id scope terms (the producer
    registered that very stream). A Batch entry registered with only a prefix is read as the resolved
    exact stream — without scope terms only when that stream lies under a prefix registered on this
    pipeline execution — or, unresolved, as the prefix with scope terms, which the response reports as
    `unscoped`. A Deadline Cloud job entry is read as the exact session streams the caller resolved
    (`stream_names`, from ListSessions on the job) with no prefix and no scope terms: the queue group is
    shared by every job of the queue and its lines carry no VAMS id, so neither the `session-` prefix
    nor the scope terms could narrow it to this job. Everything else is read as today: group or prefix,
    scope terms applied."""
    stream = entry.get("logStreamName") or ""
    prefix = entry.get("logStreamPrefix") or ""
    kind = entry.get("kind")
    if kind == KIND_DEADLINE_CLOUD_JOB:
        return {"logStreamName": "", "logStreamNames": [str(n) for n in (stream_names or []) if n],
                "logStreamPrefix": "", "scoped": False, "unscoped": False}
    if kind == KIND_REGISTERED and stream:
        return {"logStreamName": stream, "logStreamPrefix": "", "scoped": False, "unscoped": False}
    if kind == KIND_REGISTERED and prefix and entry.get("sourceType") == SOURCE_TYPE_BATCH:
        if resolved_stream:
            under_prefix = any(resolved_stream.startswith(p) for p in prefixes or [] if p)
            return {"logStreamName": resolved_stream, "logStreamPrefix": "",
                    "scoped": not under_prefix, "unscoped": False}
        return {"logStreamName": "", "logStreamPrefix": prefix, "scoped": True, "unscoped": True}
    return {"logStreamName": "", "logStreamPrefix": prefix, "scoped": True, "unscoped": False}


def classify_read(ok, payload, next_token, unscoped=False):
    """The status a source read is reported with. `payload` is the error code on failure, the event
    list on success. A failure is `denied` only for an access denial and `notFound` only for a missing
    group; every other failure -- a throttle, a continuation token minted for another source, a
    location that is not a log-group ARN -- is `error`. An empty page with a continuation token is a
    read, not an empty source."""
    if not ok:
        code = str(payload or "")
        if "ResourceNotFound" in code:
            return STATUS_NOT_FOUND
        if "AccessDenied" in code:
            return STATUS_DENIED
        return STATUS_ERROR
    if unscoped:
        return STATUS_UNSCOPED
    if not payload and not next_token:
        return STATUS_EMPTY
    return STATUS_READ


def sort_events(events):
    """Events in timestamp order (stable; a missing timestamp sorts first)."""
    def _key(event):
        ts = event.get("timestamp") if isinstance(event, dict) else None
        return ts if isinstance(ts, (int, float)) else 0
    return sorted(events or [], key=_key)
