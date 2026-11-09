# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared logic of the large object copy engine.

A large object (a workflow output over LARGE_FILE_THRESHOLD_BYTES, or an API upload over the
configured engine threshold) is copied into its asset by a deployment-wide Step Functions state
machine that runs short Lambda tasks, each copying a batch of parts with UploadPartCopy. Progress is
kept in the destination multipart upload itself: a retried task lists the parts already copied and
copies only the missing ones, so a timeout or crash resumes instead of restarting.

This module holds what the starters (fileIngestion, the large-file queue consumer), the engine
(handlers/assets/largeObjectCopy.py), the workflow output step and the terminal writers share:
settings, the part planner, job identity, the job and ingestion-batch rows, the exactly-once batch
notification, cancellation of an execution's copy jobs, and the read-path hold for an execution
whose outputs are still being ingested.

Import has no AWS or environment side effect: every client and table name is passed in.
"""

import hashlib
import json
import math
import os
import uuid
from datetime import datetime, timedelta, timezone

from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from common.s3MetadataKeys import VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY
from customLogging.logger import safeLogger

# Logs under the calling handler's service name.
logger = safeLogger()

MIB = 1024 * 1024
GIB = 1024 * MIB
TIB = 1024 * GIB

# Size above which a file is ingested asynchronously (unchanged from the value fileIngestion has
# always used). Files at or below it keep the synchronous path.
LARGE_FILE_THRESHOLD_BYTES = 1 * GIB

# Amazon S3 limits (not configurable).
S3_MAX_OBJECT_BYTES = 5 * TIB
S3_MIN_PART_BYTES = 5 * MIB
S3_MAX_PART_BYTES = 5 * GIB
S3_MAX_PARTS = 10000
# Parts the planner aims for at most, leaving headroom under S3_MAX_PARTS.
MAX_PLANNED_PARTS = 9000
# Batches one copy job is planned into at most. Bounds the execution's state size; the planner
# widens batches rather than exceed it.
MAX_BATCHES = 1000
# Batches one child execution of the copy copies (infra largeObjectCopyEngine.ts BATCHES_PER_CHUNK,
# which carries the history arithmetic). Each child execution has its own 25,000-event history, so
# throttling retries of one chunk can never exhaust the history of the job's own execution.
DEFAULT_BATCHES_PER_CHUNK = 50
# Largest part an in-VPC copy between Regions streams through the function (it holds each part in
# memory, two at a time). Above the 596 MiB a 5 TiB object needs at MAX_PLANNED_PARTS parts.
STREAMED_MAX_PART_BYTES = 1 * GIB

# Job kinds. workflowOutput jobs belong to an ingestion batch the workflow output step waits on;
# upload jobs are started by the large-file queue consumer (API uploads, and workflow outputs queued
# before this engine existed) and report through the audit log and the job row only.
KIND_WORKFLOW_OUTPUT = "workflowOutput"
KIND_UPLOAD = "upload"
JOB_KINDS = (KIND_WORKFLOW_OUTPUT, KIND_UPLOAD)

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"
TERMINAL_JOB_STATUSES = (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED)

# Main execution row phase while its outputs are being ingested by the engine.
EXECUTION_PHASE_INGESTING_OUTPUTS = "INGESTING_OUTPUTS"

# Partition key prefix of an ingestion batch row (job rows use the bare job id).
BATCH_KEY_PREFIX = "batch#"
# GSI of the jobs table keyed on workflowExecutionId (job rows only; batch rows do not carry it).
WORKFLOW_EXECUTION_ID_INDEX = "WorkflowExecutionIdIndex"
# Days a terminal job or batch row is kept before the table's TTL removes it.
ROW_RETENTION_DAYS = 30

# Generic, user-facing failure reasons (specifics go to the logs).
REASON_SOURCE_CHANGED = "The staged file changed while it was being copied."
REASON_TOO_LARGE = "The file exceeds the Amazon S3 object size limit of 5 TiB."
REASON_BLOCKED = "The file has a blocked extension or content type."
REASON_CANCELLED = "The copy was cancelled."
REASON_COPY_FAILED = "The copy did not complete."
REASON_START_FAILED = "Ingestion of the file could not be started."
REASON_NOT_REPORTED = "Output ingestion did not report completion."
# Reasons that mean the file was refused, not that the copy broke. A refused copy ends its execution
# in its own end state, so the failed-copy alarm counts only operational failures.
REFUSAL_REASONS = (REASON_SOURCE_CHANGED, REASON_TOO_LARGE, REASON_BLOCKED, REASON_CANCELLED)

# Copy execution statuses that mean it ended outside its own states (the history quota, a stop in the
# console or the API, a runtime error): no Fail or notify state ran, so the reconcile finalizes the job.
RECONCILE_EXECUTION_STATUSES = ("FAILED", "TIMED_OUT", "ABORTED")
# StopExecution error the cancel path (cancel_execution_copy_jobs) writes. The reconcile does not
# treat such a stop as an unreported end (is_unreported_end): it only finishes what the cancel path
# decided, or makes the cancel path's decision for a job the cancel path left open
# (_reconcile_cancelled_stop in the engine).
STOP_ERROR_CANCELLED = "Cancelled"
# Copy executions the execution details read checks per read for one that ended unreported.
MAX_RECONCILE_CHECKS_PER_READ = 5
# Least seconds between two such checks of one run, however many viewers read it: the read claims
# the check on the run's row (lastCopyReconcileCheckDate), so a run costs at most
# MAX_RECONCILE_CHECKS_PER_READ DescribeExecution calls per interval.
COPY_RECONCILE_CHECK_INTERVAL_SECONDS = 60
# Main execution row attribute the details read stamps when it checks the run's copy executions.
COPY_RECONCILE_CHECK_ATTRIBUTE = "lastCopyReconcileCheckDate"

# Environment variables (set by CDK on every function that starts, runs or waits on copy jobs).
ENV_STATE_MACHINE_ARN = "LARGE_OBJECT_COPY_STATE_MACHINE_ARN"
# Copy engine function name, on the functions that hand it an ended execution to reconcile.
ENV_ENGINE_FUNCTION_NAME = "LARGE_OBJECT_COPY_FUNCTION_NAME"
# Name of the SSM parameter holding that function name, for the one caller that cannot take the name
# as an environment variable: the workflow output step (the engine already references the output
# step, so a reference back would be a CloudFormation dependency cycle).
ENV_ENGINE_FUNCTION_NAME_PARAMETER = "LARGE_OBJECT_COPY_FUNCTION_NAME_PARAMETER"

# Engine settings. Fixed values, not deployment configuration.
# API uploads larger than this are copied by the engine; smaller ones keep the large-file queue's
# own copy. At least LARGE_FILE_THRESHOLD_BYTES.
API_UPLOAD_ENGINE_THRESHOLD_BYTES = 5 * GIB
# Smallest part of a copy. The planner widens parts to stay within MAX_PLANNED_PARTS.
MIN_PART_SIZE_BYTES = 256 * MIB
# Parts one Lambda task copies, and the parts it copies at the same time.
PARTS_PER_BATCH = 16
THREADS_PER_BATCH = 8
# Lambda tasks one copy runs at the same time: the MaxConcurrency of the copy-parts state machine's
# Map (infra largeObjectCopyEngine.ts MAX_CONCURRENT_BATCHES, a fixed value in the definition).
MAX_CONCURRENT_BATCHES = 10
# How long a workflow execution waits for its large outputs (the AwaitOutputIngestion timeout baked
# into each generated definition; workflowAslBuilder DEFAULT_MAX_OUTPUT_INGESTION_WAIT_HOURS).
MAX_OUTPUT_INGESTION_WAIT_HOURS = 48

_serializer = TypeSerializer()


class CopyRefused(Exception):
    """A copy that no retry can complete (blocked type, too large, cancelled). The state machine's
    retrier does not retry this error name."""


class SourceChanged(Exception):
    """The pinned source version changed or vanished during the copy. Not retried."""


def settings():
    """The engine settings as a dict (the fixed module constants above)."""
    return {
        "apiUploadEngineThresholdBytes": API_UPLOAD_ENGINE_THRESHOLD_BYTES,
        "minPartSizeBytes": MIN_PART_SIZE_BYTES,
        "partsPerBatch": PARTS_PER_BATCH,
        "threadsPerBatch": THREADS_PER_BATCH,
        "maxOutputIngestionWaitHours": MAX_OUTPUT_INGESTION_WAIT_HOURS,
        "batchesPerChunk": DEFAULT_BATCHES_PER_CHUNK,
    }


_engine_function_names = {}


def engine_function_name(environ=None, ssm_client=None):
    """The copy engine function name, or "" when this function was not given one: the environment
    variable, else the SSM parameter it names (read once per container with ``ssm_client``). A failed
    parameter read is logged and returns "" (the next call tries again)."""
    environ = os.environ if environ is None else environ
    name = environ.get(ENV_ENGINE_FUNCTION_NAME, "") or ""
    if name:
        return name
    parameter = environ.get(ENV_ENGINE_FUNCTION_NAME_PARAMETER, "") or ""
    if not parameter or ssm_client is None:
        return ""
    if parameter not in _engine_function_names:
        try:
            value = ssm_client.get_parameter(Name=parameter)["Parameter"]["Value"]
        except Exception as e:
            logger.warning(f"Could not read the copy engine function name: {e}")
            return ""
        _engine_function_names[parameter] = value or ""
    return _engine_function_names[parameter]


def request_reconcile(lambda_client, function_name, execution_arns):
    """Hand each ended copy execution to the copy engine's reconcile (an asynchronous invoke, so a
    failed reconcile is retried by Lambda and then dead-lettered into the alarmed reconcile queue).
    Best-effort: never raises; returns the number handed over. The reconcile is safe to repeat, so
    an execution also reconciled from its status change event is finished once."""
    handed = 0
    if not execution_arns:
        return handed
    if not function_name or lambda_client is None:
        logger.warning(f"{len(execution_arns)} ended copy execution(s) could not be handed to the "
                       f"copy engine (no engine function); their status change events reconcile them")
        return handed
    for execution_arn in execution_arns:
        try:
            lambda_client.invoke(FunctionName=function_name, InvocationType="Event",
                                 Payload=json.dumps({"action": "reconcile", "executionArn": execution_arn}))
            handed += 1
        except Exception as e:
            logger.warning(f"Could not hand an ended copy execution to the copy engine (its status "
                           f"change event still reconciles it): {e}")
    return handed


def state_machine_arn(environ=None):
    """The copy engine state machine ARN, or "" when this function was not given one."""
    environ = os.environ if environ is None else environ
    return environ.get(ENV_STATE_MACHINE_ARN, "") or ""


def is_large(size):
    """True for a file the asynchronous path ingests."""
    try:
        return int(size or 0) > LARGE_FILE_THRESHOLD_BYTES
    except (TypeError, ValueError):
        return False


def plan_parts(size, min_part_size=256 * MIB):
    """(part_size, part_count) for copying an object of ``size`` bytes.

    The part size is at least ``min_part_size`` (and S3's 5 MiB minimum), grows so the object takes
    at most MAX_PLANNED_PARTS parts, and is rounded up to a whole MiB. Raises CopyRefused for an
    object over the S3 object size limit."""
    size = int(size)
    if size <= 0:
        raise ValueError("A copy job needs a non-empty source object")
    if size > S3_MAX_OBJECT_BYTES:
        raise CopyRefused(REASON_TOO_LARGE)
    part_size = max(int(min_part_size), S3_MIN_PART_BYTES, math.ceil(size / MAX_PLANNED_PARTS))
    part_size = math.ceil(part_size / MIB) * MIB
    part_size = min(part_size, S3_MAX_PART_BYTES)
    part_count = math.ceil(size / part_size)
    if part_count > S3_MAX_PARTS:
        raise CopyRefused(REASON_TOO_LARGE)
    return part_size, part_count


def part_range(part_number, part_size, size):
    """The inclusive (first_byte, last_byte) of a 1-based part."""
    first = (part_number - 1) * part_size
    last = min(first + part_size, size) - 1
    return first, last


def plan_batches(part_count, parts_per_batch=16):
    """Contiguous [{startPart, endPart}] batches covering parts 1..part_count, at most MAX_BATCHES
    of them (batches are widened when the configured size would need more)."""
    per_batch = max(int(parts_per_batch), math.ceil(part_count / MAX_BATCHES), 1)
    batches = []
    start = 1
    while start <= part_count:
        end = min(start + per_batch - 1, part_count)
        batches.append({"startPart": start, "endPart": end})
        start = end + 1
    return batches


def plan_chunks(batches, batches_per_chunk=DEFAULT_BATCHES_PER_CHUNK):
    """[{batches: [...]}] groups of at most ``batches_per_chunk`` consecutive batches, one per child
    execution of the copy."""
    per_chunk = max(1, int(batches_per_chunk))
    return [{"batches": batches[i:i + per_chunk]} for i in range(0, len(batches), per_chunk)]


def ended_copy_executions(sfn_client, job_rows, limit=MAX_RECONCILE_CHECKS_PER_READ):
    """Execution ARNs of unfinished copy jobs whose execution ended outside its states (failed, timed
    out, or stopped), checking at most ``limit`` jobs. Best-effort: a failed describe is logged and
    skipped.

    A stop the cancel path made counts too (an open job of a still-running run). Every cancel path
    caller acts on a run that is ending, whose details read no longer checks its copies, so the
    cancel path hands its stops to the engine itself (request_reconcile); this only covers a run
    still RUNNING. The reconcile of a cancelled stop is safe to repeat (every write in it is
    conditional or claimed)."""
    ended = []
    checked = 0
    for row in job_rows or []:
        if checked >= limit:
            break
        execution_arn = row.get("executionArn", "")
        if row.get("status") in TERMINAL_JOB_STATUSES or not execution_arn:
            continue
        checked += 1
        try:
            described = sfn_client.describe_execution(executionArn=execution_arn)
        except Exception as e:
            logger.warning(f"Could not describe copy execution of job {row.get('jobId', '')}: {e}")
            continue
        if is_unreported_end(described) or is_cancelled_stop(described):
            ended.append(execution_arn)
    return ended


def is_cancelled_stop(described):
    """True when a described copy execution was stopped by the cancel path."""
    return ((described or {}).get("status") == "ABORTED"
            and (described or {}).get("error") == STOP_ERROR_CANCELLED)


def is_unreported_end(described):
    """True when a described copy execution ended without running its own Fail or notify states."""
    status = (described or {}).get("status", "")
    if status not in RECONCILE_EXECUTION_STATUSES:
        return False
    return not (status == "ABORTED" and (described or {}).get("error") == STOP_ERROR_CANCELLED)


def job_id_for(scope_id, relative_key):
    """Deterministic job id: one per (execution or upload, file), so a repeated start is a no-op."""
    digest = hashlib.sha256((relative_key or "").encode("utf-8")).hexdigest()[:16]
    return f"{scope_id}#{digest}"


def execution_name_for(job_id):
    """Step Functions execution name for a job (<= 80 chars of [A-Za-z0-9-_])."""
    return "loc-" + hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:48]


def batch_key(batch_id):
    return f"{BATCH_KEY_PREFIX}{batch_id}"


def _expires_at(days=ROW_RETENTION_DAYS, now=None):
    now = now or datetime.now(timezone.utc)
    return int((now + timedelta(days=days)).timestamp())


def _iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_condition_failure(error):
    return (isinstance(error, ClientError)
            and error.response.get("Error", {}).get("Code") in (
                "ConditionalCheckFailedException", "TransactionCanceledException"))


def build_job(kind, source, dest, metadata, content_headers, database_id, asset_id, upload_id,
              upload_type, relative_key, change_user_id, scope_id, workflow_id="",
              workflow_execution_id="", workflow_database_id="", batch_id="",
              delete_source_on_success=True):
    """The job document a copy execution carries. ``source``/``dest`` are {bucket, key, region}
    (source also versionId/etag/size when known); ``metadata`` is the complete user metadata of the
    final object and ``content_headers`` the system headers to restate (both exactly what the
    synchronous path writes)."""
    if kind not in JOB_KINDS:
        raise ValueError(f"Unknown copy job kind {kind}")
    return {
        "jobId": job_id_for(scope_id, relative_key),
        "kind": kind,
        "source": dict(source),
        "dest": dict(dest),
        "metadata": dict(metadata or {}),
        "contentHeaders": dict(content_headers or {}),
        "databaseId": database_id,
        "assetId": asset_id,
        "uploadId": upload_id,
        "uploadType": upload_type,
        "relativeKey": relative_key,
        "changeUserId": change_user_id or "SYSTEM_USER",
        "workflowId": workflow_id or "",
        "workflowExecutionId": workflow_execution_id or "",
        "workflowDatabaseId": workflow_database_id or "",
        "batchId": batch_id or "",
        "deleteSourceOnSuccess": bool(delete_source_on_success),
    }


def start_copy_job(table, sfn_client, machine_arn, job):
    """Record the job row (QUEUED) and start its copy execution. Idempotent: a job already recorded
    or an execution already started with the job's name is not started twice. Returns the
    execution ARN. Raises on any other failure so the caller fails the file."""
    if not machine_arn:
        raise RuntimeError("The large object copy state machine is not configured")
    row = {
        "jobId": job["jobId"],
        "kind": job["kind"],
        "status": STATUS_QUEUED,
        "relativeKey": job["relativeKey"],
        "databaseId": job["databaseId"],
        "assetId": job["assetId"],
        "uploadId": job["uploadId"],
        "sourceBucket": job["source"]["bucket"],
        "sourceKey": job["source"]["key"],
        "sourceSize": int(job["source"].get("size") or 0),
        "destBucket": job["dest"]["bucket"],
        "destKey": job["dest"]["key"],
        "createdAt": _iso_now(),
        "expiresAt": _expires_at(days=ROW_RETENTION_DAYS + 7),
    }
    if job["dest"].get("region"):
        # The cancel path signs its abort for this Region without a buckets table read (#1123 D6).
        row["destRegion"] = job["dest"]["region"]
    if job.get("workflowExecutionId"):
        row["workflowExecutionId"] = job["workflowExecutionId"]
    if job.get("batchId"):
        row["batchId"] = job["batchId"]
    try:
        table.put_item(Item=row, ConditionExpression="attribute_not_exists(jobId)")
    except ClientError as e:
        if not _is_condition_failure(e):
            raise
        logger.info(f"Copy job {job['jobId']} is already recorded; not recording it again")
    name = execution_name_for(job["jobId"])
    try:
        response = sfn_client.start_execution(
            stateMachineArn=machine_arn, name=name, input=json.dumps({"job": job}))
        execution_arn = response.get("executionArn", "")
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ExecutionAlreadyExists":
            raise
        execution_arn = machine_arn.replace(":stateMachine:", ":execution:") + ":" + name
        logger.info(f"Copy job {job['jobId']} already has an execution; not starting another")
    table.update_item(
        Key={"jobId": job["jobId"]},
        UpdateExpression="SET executionArn = :a",
        ExpressionAttributeValues={":a": execution_arn},
    )
    return execution_arn


def get_job_row(table, job_id):
    return table.get_item(Key={"jobId": job_id}, ConsistentRead=True).get("Item") or {}


def update_job_row(table, job_id, values, condition=None, condition_values=None):
    """SET each of ``values`` on the job row (and refresh its TTL). Returns False when ``condition``
    did not hold. In ``condition`` the job's status attribute is written ``#s``."""
    values = dict(values)
    values["expiresAt"] = _expires_at()
    names = {f"#k{i}": k for i, k in enumerate(values)}
    if condition and "#s" in condition:
        names["#s"] = "status"
    attr_values = {f":v{i}": v for i, v in enumerate(values.values())}
    expression = "SET " + ", ".join(f"#k{i} = :v{i}" for i in range(len(values)))
    kwargs = {
        "Key": {"jobId": job_id},
        "UpdateExpression": expression,
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": attr_values,
    }
    if condition:
        kwargs["ConditionExpression"] = condition
        if condition_values:
            attr_values.update(condition_values)
    try:
        table.update_item(**kwargs)
        return True
    except ClientError as e:
        if _is_condition_failure(e):
            return False
        raise


# A job row's status only moves forward: QUEUED -> RUNNING -> SUCCEEDED | FAILED | CANCELLED. Every
# writer of a status conditions its write on the row still being open (this condition, with
# OPEN_JOB_VALUES), so a task of a stopped execution that is still running (StopExecution does not
# stop a Lambda invocation in flight) can never move a finalized job back to RUNNING or over to
# another outcome. The cancel path's CANCELLED write keeps its own condition (not over SUCCEEDED or
# FAILED).
OPEN_JOB_CONDITION = "attribute_exists(jobId) AND (attribute_not_exists(#s) OR #s IN (:open_q, :open_r))"
OPEN_JOB_VALUES = {":open_q": STATUS_QUEUED, ":open_r": STATUS_RUNNING}


def is_open_job(row):
    """True for a recorded job that is not in a terminal status."""
    return bool(row) and row.get("status", STATUS_QUEUED) not in TERMINAL_JOB_STATUSES


def finalize_job(table, job_id, status, values=None, from_cancelled=False):
    """Move an open job to its terminal ``status``, exactly once, and stamp finalizedAt.

    Returns the terminal status this call finalized the job with, or None when the job was already
    finalized (another writer won; the caller then acts on the stored outcome, not its own). With
    ``from_cancelled`` a job the cancel path marked CANCELLED (which does no cleanup or audit of its
    own) is finalized as CANCELLED once, keeping its status and reason."""
    now = _iso_now()
    written = {"status": status, "finishedAt": now, "finalizedAt": now}
    written.update(values or {})
    if update_job_row(table, job_id, written, condition=OPEN_JOB_CONDITION,
                      condition_values=dict(OPEN_JOB_VALUES)):
        return status
    if from_cancelled and update_job_row(
            table, job_id, {"finalizedAt": now},
            condition="#s = :c AND attribute_not_exists(finalizedAt)",
            condition_values={":c": STATUS_CANCELLED}):
        return STATUS_CANCELLED
    return None


def supersede_with_landed(table, job_id, version_id):
    """Move a job a remover finalized FAILED or CANCELLED to SUCCEEDED, because a Complete of this
    job's own upload then landed its object (the one exception to the forward-only status).

    S3 does not refuse an abort sent while a CompleteMultipartUpload is in flight: it answers 204
    and the Complete can still succeed, so the cancel path or the reconcile can decide "not landed"
    before the object is at the key. Only the Complete that created the object (its own response,
    or its own upload token on the key) calls this. The rejection's error and audit claim are
    removed so the post-landing work (_finish) writes the completion audit entry once;
    supersededStatus keeps the overridden outcome. Returns True for exactly one caller."""
    now = _iso_now()
    try:
        table.update_item(
            Key={"jobId": job_id},
            UpdateExpression=("SET #s = :ok, supersededStatus = #s, destVersionId = :v, "
                              "finishedAt = :n, finalizedAt = :n, expiresAt = :e "
                              "REMOVE #err, auditedAt"),
            ConditionExpression="attribute_exists(jobId) AND #s IN (:f, :c)",
            ExpressionAttributeNames={"#s": "status", "#err": "error"},
            ExpressionAttributeValues={":ok": STATUS_SUCCEEDED, ":v": version_id or "", ":n": now,
                                       ":e": _expires_at(), ":f": STATUS_FAILED,
                                       ":c": STATUS_CANCELLED},
        )
        return True
    except ClientError as e:
        if _is_condition_failure(e):
            return False
        raise


def claim_job_flag(table, job_id, flag):
    """Claim a once-only side effect of a finalized job (its audit entry, its post-landing work) by
    stamping ``flag`` on the row. True for exactly one caller."""
    try:
        table.update_item(
            Key={"jobId": job_id},
            UpdateExpression="SET #f = :n",
            ConditionExpression="attribute_exists(jobId) AND attribute_not_exists(#f)",
            ExpressionAttributeNames={"#f": flag},
            ExpressionAttributeValues={":n": _iso_now()},
        )
        return True
    except ClientError as e:
        if _is_condition_failure(e):
            return False
        raise


def release_job_flag(table, job_id, flag):
    """Undo a claim whose side effect could not be done, so a later caller can do it. Best-effort."""
    try:
        table.update_item(Key={"jobId": job_id}, UpdateExpression="REMOVE #f",
                          ExpressionAttributeNames={"#f": flag})
    except Exception as e:
        logger.warning(f"Could not release {flag} of copy job {job_id}: {e}")


def new_upload_token():
    """A fresh per-job upload token (recorded as destUploadToken on the job row and stamped on the
    multipart upload as VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY)."""
    return uuid.uuid4().hex


def is_own_upload_object(head, token):
    """Whether a HEAD describes the object a job's own multipart upload created: the object carries
    the job's upload token. Never decided by presence or size, since an earlier object of the same
    size and part count can sit at the key (a re-run of the same output)."""
    return bool(head) and bool(token) and \
        (head.get("Metadata") or {}).get(VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY) == token


def landed_object_version(s3_client, row):
    """(landed, version_id) for a job whose multipart upload no longer exists while the job is open.

    The upload is gone either because Complete completed it or because a remover aborted it (fail,
    the reconcile, the cancel path, or something outside VAMS such as a bucket lifecycle rule). It
    counts as landed only when the key's current object is the one THIS job's upload created: Prepare
    records a fresh destUploadToken on the job row in the same conditional write as the upload id and
    stamps it as object metadata on CreateMultipartUpload, so only that upload's completed object
    carries it. Works the same on unversioned buckets. A row without a token has no landed object."""
    token = row.get("destUploadToken")
    if not token:
        return False, ""
    try:
        head = s3_client.head_object(Bucket=row.get("destBucket"), Key=row.get("destKey"))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False, ""
        raise
    if not is_own_upload_object(head, token):
        return False, ""
    return True, head.get("VersionId", "") or ""


def record_batch(table, batch_id, job_ids, context):
    """Record (or refresh) an ingestion batch: the copy jobs it waits on and the context the finalize
    step needs. Upsert: a job that finished first may already have created the row with its terminal
    count.

    The batch keeps the UNION of every job id any attempt of the output step recorded (jobIds), and
    its totalCount only grows to that union's size. A retried output step no longer lists a file
    whose copy already finished (its staged source is deleted), so a total taken from one attempt's
    listing alone would let the batch be claimed while a copy is still running. Returns the row as
    stored.

    Size limit: each job id is about 55 bytes, so an execution with roughly 7,000 outputs over 1 GiB
    would pass DynamoDB's 400 KB item limit and fail its output step (documented on the quotas page).
    Typical runs carry a handful of large outputs."""
    job_ids = sorted({j for j in (job_ids or []) if j})
    values = {":c": context, ":e": _expires_at(), ":z": 0}
    expression = "SET #ctx = :c, expiresAt = :e, terminalCount = if_not_exists(terminalCount, :z)"
    if job_ids:
        expression += " ADD jobIds :ids"
        values[":ids"] = set(job_ids)
    response = table.update_item(
        Key={"jobId": batch_key(batch_id)},
        UpdateExpression=expression,
        ExpressionAttributeNames={"#ctx": "context"},
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    row = response.get("Attributes") or {}
    total = len(row.get("jobIds") or ())
    try:
        response = table.update_item(
            Key={"jobId": batch_key(batch_id)},
            UpdateExpression="SET totalCount = :t",
            ConditionExpression="attribute_not_exists(totalCount) OR totalCount < :t",
            ExpressionAttributeValues={":t": total},
            ReturnValues="ALL_NEW",
        )
        row = response.get("Attributes") or row
    except ClientError as e:
        if not _is_condition_failure(e):
            raise
        # A concurrent attempt already recorded a total at least this large.
        row = get_batch(table, batch_id) or row
    return row


def recorded_batch_jobs(table, batch_id, workflow_execution_id):
    """{jobId: job row} of every copy job already recorded for a batch (all attempts of the output
    step), from the execution index."""
    return {row["jobId"]: row for row in jobs_for_execution(table, workflow_execution_id)
            if row.get("batchId") == batch_id and row.get("jobId")}


def register_task_token(table, batch_id, task_token):
    """Store the waiting workflow's task token on its batch. Returns the row as stored."""
    response = table.update_item(
        Key={"jobId": batch_key(batch_id)},
        UpdateExpression="SET taskToken = :t, expiresAt = :e",
        ExpressionAttributeValues={":t": task_token, ":e": _expires_at()},
        ReturnValues="ALL_NEW",
    )
    return response.get("Attributes") or {}


def get_batch(table, batch_id):
    return table.get_item(Key={"jobId": batch_key(batch_id)}, ConsistentRead=True).get("Item") or {}


def count_job_terminal(dynamodb_client, table_name, job_id, batch_id, status=None, extra=None):
    """Count a finalized job on its batch, in one transaction, exactly once.

    The job row's ``counted`` flag is set only if it is not set yet, and the batch's terminalCount is
    incremented in the same transaction, so a retried notify neither double counts nor loses the
    count. The job's status is not written unless ``status`` is given (finalize_job writes it, so the
    count never changes an outcome). Returns True when this call counted the job, False when it was
    counted before."""
    values = {"counted": True, "expiresAt": _expires_at()}
    if status:
        values["status"] = status
        values["finishedAt"] = _iso_now()
    values.update(extra or {})
    names = {f"#k{i}": k for i, k in enumerate(values)}
    attr_values = {f":v{i}": _serializer.serialize(v) for i, v in enumerate(values.values())}
    items = [{
        "Update": {
            "TableName": table_name,
            "Key": {"jobId": {"S": job_id}},
            "UpdateExpression": "SET " + ", ".join(f"#k{i} = :v{i}" for i in range(len(values))),
            "ConditionExpression": "attribute_not_exists(#counted)",
            "ExpressionAttributeNames": {**names, "#counted": "counted"},
            "ExpressionAttributeValues": attr_values,
        }
    }]
    if batch_id:
        items.append({
            "Update": {
                "TableName": table_name,
                "Key": {"jobId": {"S": batch_key(batch_id)}},
                "UpdateExpression": "ADD terminalCount :one SET expiresAt = :e",
                "ExpressionAttributeValues": {
                    ":one": {"N": "1"}, ":e": {"N": str(_expires_at())}},
            }
        })
    try:
        dynamodb_client.transact_write_items(TransactItems=items)
        return True
    except ClientError as e:
        if _is_condition_failure(e):
            logger.info(f"Copy job {job_id} was already counted on its batch")
            return False
        raise


def claim_notification(table, batch_id):
    """Claim the right to report a finished batch. Succeeds for exactly one caller, and only once
    every job is counted and either a task token is registered or the batch is a legacy one (its
    workflow was not waiting). Returns the batch row when this caller won, else None."""
    try:
        response = table.update_item(
            Key={"jobId": batch_key(batch_id)},
            UpdateExpression="SET notified = :t, notifiedAt = :n",
            ConditionExpression=(
                "attribute_exists(totalCount) AND terminalCount >= totalCount AND "
                "attribute_not_exists(notified) AND "
                "(attribute_exists(taskToken) OR #ctx.#legacy = :t)"),
            ExpressionAttributeNames={"#ctx": "context", "#legacy": "legacy"},
            ExpressionAttributeValues={":t": True, ":n": _iso_now()},
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes") or {}
    except ClientError as e:
        if _is_condition_failure(e):
            return None
        raise


def release_notification_claim(table, batch_id):
    """Undo a claim whose report could not be delivered, so a later caller can deliver it."""
    try:
        table.update_item(Key={"jobId": batch_key(batch_id)}, UpdateExpression="REMOVE notified")
    except Exception as e:
        logger.warning(f"Could not release the notification claim of batch {batch_id}: {e}")


def deliver_batch_report(table, batch, sfn_client, lambda_client=None, finalize_function_name=""):
    """Deliver the report of a batch whose notification this caller claimed: SendTaskSuccess to the
    waiting workflow when it registered a task token, otherwise (a workflow on a definition that does
    not wait) an asynchronous finalize of its output step. Returns True when delivered. A token the
    workflow no longer accepts (it timed out or was stopped, and its error path cleans up) is not an
    error; any other failure releases the claim and raises, so a retry can deliver."""
    batch_id = batch["jobId"][len(BATCH_KEY_PREFIX):]
    context = batch.get("context") or {}
    try:
        if batch.get("taskToken"):
            sfn_client.send_task_success(
                taskToken=batch["taskToken"],
                output=json.dumps({"outputIngestionComplete": True, "batchId": batch_id}))
        else:
            lambda_client.invoke(
                FunctionName=finalize_function_name, InvocationType="Event",
                Payload=json.dumps({"body": {
                    "mode": "finalize", "batchId": batch_id,
                    "workflowExecutionId": context.get("workflowExecutionId", "")}}))
        logger.info(f"Reported ingestion batch {batch_id} complete")
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("TaskTimedOut", "TaskDoesNotExist", "InvalidToken"):
            logger.warning(f"Ingestion batch {batch_id}: the waiting workflow no longer accepts the "
                           f"report ({e.response['Error']['Code']})")
            return False
        release_notification_claim(table, batch_id)
        raise


def jobs_for_execution(table, workflow_execution_id):
    """Every job row recorded for a workflow execution (all pages)."""
    items = []
    kwargs = {
        "IndexName": WORKFLOW_EXECUTION_ID_INDEX,
        "KeyConditionExpression": Key("workflowExecutionId").eq(workflow_execution_id),
    }
    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []) or [])
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def batch_outcomes(table, batch_id, workflow_execution_id):
    """{relativeKey: job row} for the jobs of one batch. The index is eventually consistent, so each
    row is re-read with a consistent read before its outcome is trusted."""
    outcomes = {}
    for item in jobs_for_execution(table, workflow_execution_id):
        if item.get("batchId") != batch_id:
            continue
        row = get_job_row(table, item["jobId"]) or item
        outcomes[row.get("relativeKey", "")] = row
    return outcomes


def cancel_execution_copy_jobs(table, sfn_client, s3_client, workflow_execution_id,
                               lambda_client=None, engine_function=""):
    """Stop every unfinished copy job of a workflow execution and abort its multipart upload.

    Used by the abort route, the error handler, the output step (a run that ended while it ran) and
    the workflow status change handler. A stopped Standard execution runs none of its Catch states,
    so the cleanup a Fail state would do happens here: the upload is aborted and the job row is
    marked CANCELLED. Best-effort per job; returns one warning per job that could not be stopped or
    cleaned up. Returns [] when the jobs table cannot be read (logged), as a run with no large
    outputs has no jobs.

    After its abort every job is decided by its landed object, whatever the abort answered (S3
    answers 204 to an abort sent during or after a successful Complete). When this call finalized it
    SUCCEEDED, or could not tell and left it open, the copy engine's reconcile of the stop made here
    does the rest (the decision, or the post-landing work: staged-source delete, asset type, email,
    audit entry, upload outcome). Each such execution is handed to the engine directly
    (``lambda_client`` + ``engine_function``, request_reconcile), next to the stop's status change
    event, so neither trigger alone carries it; the reconcile is safe to repeat. A failed hand-over
    is logged and does not fail the cancel.

    A cancel that lands after Prepare created the multipart upload but before it recorded the
    upload id cannot see that upload; Prepare then fails to record it (the job is CANCELLED) and
    aborts it itself."""
    warnings = []
    handoff = []
    try:
        jobs = jobs_for_execution(table, workflow_execution_id)
    except Exception as e:
        logger.exception(f"Could not read the copy jobs of execution {workflow_execution_id}: {e}")
        return ["The output copy jobs of this execution could not be read; any running copy "
                "finishes on its own."]
    for job in jobs:
        if job.get("status") in TERMINAL_JOB_STATUSES:
            continue
        job_id = job.get("jobId", "")
        if not job_id:
            # Not a job row (every job row carries its key); nothing to stop or mark.
            continue
        execution_arn = job.get("executionArn", "")
        # Recorded before the stop and the abort, so a CopyBatch task still running copies no further
        # part and aborts the upload again after its own last copy (#1123 D5).
        request_stop(table, job_id)
        # The abort and the landed check are signed for the destination bucket's Region from the job
        # row, without a buckets table read (#1123 D6). A row written before destRegion existed
        # resolves through the buckets table (the routing client's lookup).
        if job.get("destRegion") and job.get("destBucket"):
            from common.s3 import register_bucket_region
            register_bucket_region(job["destBucket"], job["destRegion"])
        # Whether this call stopped the execution. Only a stop made here leaves a cancelled stop the
        # engine's reconcile can act on (handed over below, and from its status change event).
        stopped = False
        if execution_arn:
            try:
                sfn_client.stop_execution(executionArn=execution_arn, error=STOP_ERROR_CANCELLED,
                                          cause="The workflow execution ended.")
                stopped = True
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") not in ("ExecutionDoesNotExist",):
                    logger.exception(f"Could not stop copy job {job_id}: {e}")
                    warnings.append(f"The copy of output '{job.get('relativeKey', '')}' could not "
                                    f"be stopped and may still be running.")
                    continue
        # A job with no upload id yet may be inside Prepare: Prepare records its upload only while
        # the job is open and otherwise aborts the upload itself.
        upload_id = job.get("uploadIdS3", "")
        if upload_id:
            aborted = True
            try:
                s3_client.abort_multipart_upload(
                    Bucket=job.get("destBucket"), Key=job.get("destKey"), UploadId=upload_id)
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") != "NoSuchUpload":
                    logger.exception(f"Could not abort the multipart upload of copy job {job_id}: {e}")
                    warnings.append(f"The partial copy of output '{job.get('relativeKey', '')}' "
                                    f"could not be removed.")
                    aborted = False
            # This abort's answer (and a NoSuchUpload) is not final: a part copy accepted before the
            # stop can still land minutes later. The delayed sweep aborts the recorded upload again
            # after the copy tasks have ended; it leaves a job whose object landed alone (#1123 D5).
            schedule_delayed_sweep(sfn_client, job_id)
            if aborted:
                # Decided by the landed object, whatever the abort answered: S3 answers 204 (not
                # NoSuchUpload) to an abort sent during or after a Complete that succeeds, and the
                # completed object stays at the key (#1123 D1).
                found = _cancel_found_landed(table, s3_client, job_id)
                if found is True:
                    # This job's own object had landed: the landed object wins over the cancel.
                    # Its post-landing work is the engine's reconcile of the stop made here.
                    # (With no stop made here the execution no longer exists; nothing can
                    # reconcile it.)
                    if stopped:
                        handoff.append(execution_arn)
                    continue
                if found is None and stopped:
                    # Could not tell (the abort route and the error handler cannot read the
                    # asset object): the copy engine's reconcile of the stop just made decides
                    # the job with the same rule (_reconcile_cancelled_stop). With no stop made
                    # here there is nothing to reconcile, so the job is cancelled below rather
                    # than left open.
                    handoff.append(execution_arn)
                    continue
                # Not landed (yet): a Complete still in flight can land it after this; that Complete
                # then supersedes the CANCELLED below with its own object (supersede_with_landed).
        mark_cancelled(table, job_id)
    if handoff:
        request_reconcile(lambda_client, engine_function, handoff)
    return warnings


def request_stop(table, job_id):
    """Record on an open job that its copy is being stopped (stopRequestedAt), BEFORE the stopper
    aborts the multipart upload (#1123 D5). StopExecution does not stop a running CopyBatch task, and
    S3 keeps a part whose UploadPartCopy was in flight during an abort (an upload "may need aborting
    more than once"). Every CopyBatch task reads the job before and after each part and, once it sees
    this marker or a finalized job, copies no further part and aborts the upload when its own copies
    have ended; with the stopper's abort after this write, the last copy to end is followed by an
    abort, so no part is left. Changes no status: the outcome is still decided by the landed object.
    Best-effort (False when the job is not open or the write failed)."""
    try:
        return update_job_row(table, job_id, {"stopRequestedAt": _iso_now()},
                              condition=OPEN_JOB_CONDITION, condition_values=dict(OPEN_JOB_VALUES))
    except Exception as e:
        logger.warning(f"Could not record the stop of copy job {job_id}: {e}")
        return False


def copy_should_stop(row):
    """True when a CopyBatch task must copy no further part: the job is finalized, or a stopper
    recorded its stop (request_stop)."""
    return bool(row) and (not is_open_job(row) or bool(row.get("stopRequestedAt")))


# Bounded abort sweep (#1123 D5): after an abort, a part still being copied can land; ListParts then
# still answers for the upload, and the abort is repeated once those copies have ended.
ABORT_SWEEP_ATTEMPTS = 4
ABORT_SWEEP_WAIT_SECONDS = 5


def _sleep(seconds):
    import time
    time.sleep(seconds)


def abort_until_gone(s3_client, bucket, key, upload_id, attempts=ABORT_SWEEP_ATTEMPTS,
                     wait_seconds=ABORT_SWEEP_WAIT_SECONDS, sleep=None):
    """Abort a multipart upload until S3 no longer knows it (ListParts answers NoSuchUpload), at most
    ``attempts`` aborts, waiting ``wait_seconds`` before each repeat (only when parts are still
    listed). A NoSuchUpload here is not final (a part copy accepted earlier can still land minutes
    later), so every caller also schedules the delayed sweep (schedule_delayed_sweep). Returns True
    when ListParts answered NoSuchUpload, False when it is still listed after the last
    attempt (logged; the copy tasks' own abort after their last part, and the bucket's lifecycle
    rule where it has one, still apply). Raises on an abort error other than NoSuchUpload.

    Removes no object: an upload that a Complete completed is gone as an upload (ListParts answers
    NoSuchUpload) and its object stays, so a landed file is never touched."""
    sleep = sleep or _sleep
    for attempt in range(max(1, attempts)):
        if attempt:
            sleep(wait_seconds)
        try:
            s3_client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchUpload":
                return True
            raise
        try:
            s3_client.list_parts(Bucket=bucket, Key=key, UploadId=upload_id, MaxParts=1)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchUpload":
                return True
            raise
    logger.warning(f"Multipart upload {upload_id} is still listed after {max(1, attempts)} aborts; "
                   f"a part copy still running aborts it again when it ends")
    return False


# Delayed sweep (#1123 D5, live recheck on demo21). An abort answered, and a ListParts right after it
# answered NoSuchUpload, yet the upload was listed again 2 to 8 minutes later with a part whose copy
# had been accepted before the stop; one later abort removed it for good. So no immediate answer is
# final: every stopped or failed job with an upload also gets a delayed sweep, an execution of the
# copy state machine itself (its sweep branch: Wait SWEEP_FIRST_WAIT_SECONDS, sweep, Wait
# SWEEP_SECOND_WAIT_SECONDS, sweep; infra lambdaBuilder/largeObjectCopyEngine.ts). The second pass
# runs about 16 minutes after the stop, a minute past the longest a copy task alive at the stop can
# still run (the engine's 15-minute timeout).
SWEEP_EXECUTION_PREFIX = "locsweep-"
SWEEP_FIRST_WAIT_SECONDS = 300
SWEEP_SECOND_WAIT_SECONDS = 660
# Upload pages read per pass for one key (the prefix is the exact key, so normally one page).
SWEEP_MAX_LIST_PAGES = 10
SWEEPABLE_STATUSES = (STATUS_FAILED, STATUS_CANCELLED)


def sweep_execution_name_for(job_id):
    """Step Functions execution name of a job's delayed sweep (<= 80 chars of [A-Za-z0-9-_])."""
    return SWEEP_EXECUTION_PREFIX + hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:48]


def sweep_input(job_id):
    """The sweep execution's input. Only the job id: every scheduler sends the same input, so a
    second StartExecution with the job's name is deduplicated by Step Functions, and the sweep reads
    the bucket, key and upload id from the job row, never from its input."""
    return json.dumps({"sweep": {"jobId": job_id}})


def schedule_delayed_sweep(sfn_client, job_id, machine_arn=None):
    """Start the delayed sweep of a job's multipart upload, once per job (a fixed execution name and
    input: an execution already started under that name answers ExecutionAlreadyExists, which counts
    as scheduled). Best-effort: returns False (logged) when it could not be started; the immediate
    aborts already ran and the asset bucket's AbortIncompleteMultipartUpload rule still applies."""
    machine_arn = machine_arn or state_machine_arn()
    if not job_id:
        return False
    if not machine_arn or sfn_client is None:
        # A deployed caller always has both; without them no later pass runs for this job.
        missing = (f"no state machine ARN ({ENV_STATE_MACHINE_ARN})" if not machine_arn
                   else "no Step Functions client")
        logger.warning(f"Could not schedule the delayed sweep of copy job {job_id}: {missing}")
        return False
    try:
        sfn_client.start_execution(stateMachineArn=machine_arn, name=sweep_execution_name_for(job_id),
                                   input=sweep_input(job_id))
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ExecutionAlreadyExists":
            return True
        logger.warning(f"Could not schedule the delayed sweep of copy job {job_id}: {e}")
        return False
    except Exception as e:
        logger.warning(f"Could not schedule the delayed sweep of copy job {job_id}: {e}")
        return False


def _is_sweepable(row):
    """A job whose recorded upload the delayed sweep may abort: finalized FAILED or CANCELLED, or
    still open with a stop recorded (its stopper aborted it already; the decision is still being
    made). Never a SUCCEEDED job (its upload was completed and its object landed) and never an open
    job without a stop (a copy still running)."""
    status = (row or {}).get("status")
    if status in SWEEPABLE_STATUSES:
        return True
    return is_open_job(row) and bool((row or {}).get("stopRequestedAt"))


def recorded_upload_listed(s3_client, bucket, key, upload_id, max_pages=SWEEP_MAX_LIST_PAGES):
    """True when ListMultipartUploads (prefix = the exact key) lists this upload id at this key.
    Uploads of other writers at the same key, or at keys that only start with it, never match."""
    kwargs = {"Bucket": bucket, "Prefix": key}
    for _ in range(max(1, max_pages)):
        response = s3_client.list_multipart_uploads(**kwargs)
        for upload in response.get("Uploads", []) or []:
            if upload.get("Key") == key and upload.get("UploadId") == upload_id:
                return True
        if not response.get("IsTruncated"):
            return False
        kwargs["KeyMarker"] = response.get("NextKeyMarker", "")
        kwargs["UploadIdMarker"] = response.get("NextUploadIdMarker", "")
    logger.warning(f"Stopped listing the uploads of one key after {max_pages} pages")
    return False


def sweep_recorded_upload(table, s3_client, job_id, final=False):
    """One pass of a job's delayed sweep. Reads the job row, decides first by the landed object (a
    job whose own object is at the key is left alone), then lists the uploads at the job's exact key
    and aborts the one the row recorded, if listed. Aborts nothing else and removes no object.
    Raises when the landed check or a listing fails (the state machine retries the pass)."""
    row = get_job_row(table, job_id)
    upload_id = row.get("uploadIdS3", "")
    bucket, key = row.get("destBucket", ""), row.get("destKey", "")
    if not upload_id or not bucket or not key:
        return {"swept": False, "reason": "no recorded upload"}
    if not _is_sweepable(row):
        return {"swept": False, "reason": f"job is {row.get('status') or 'unrecorded'}"}
    landed, _version = landed_object_version(s3_client, row)
    if landed:
        return {"swept": False, "reason": "the job's object landed"}
    if not recorded_upload_listed(s3_client, bucket, key, upload_id):
        return {"swept": True, "listed": False, "remaining": False}
    try:
        s3_client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "NoSuchUpload":
            raise
    remaining = recorded_upload_listed(s3_client, bucket, key, upload_id)
    if remaining:
        log = logger.error if final else logger.warning
        log(f"Multipart upload of copy job {job_id} is still listed after the delayed sweep's abort"
            + ("; the bucket's lifecycle rule, where it has one, removes it" if final else ""))
    else:
        logger.info(f"Delayed sweep aborted the leftover multipart upload of copy job {job_id}")
    return {"swept": True, "listed": True, "remaining": remaining}


def mark_cancelled(table, job_id):
    """The cancel path's CANCELLED write: never over SUCCEEDED or FAILED. Also written by the copy
    engine's reconcile of a stop the cancel path made, so both write the same thing."""
    return update_job_row(table, job_id, {"status": STATUS_CANCELLED, "error": REASON_CANCELLED,
                                          "finishedAt": _iso_now()},
                          condition="attribute_not_exists(#s) OR NOT #s IN (:a, :b)",
                          condition_values={":a": STATUS_SUCCEEDED, ":b": STATUS_FAILED})


def _cancel_found_landed(table, s3_client, job_id):
    """The cancel path aborted the job's multipart upload (204) or found it already gone
    (NoSuchUpload); neither says whether a Complete landed the file. True when the key's current
    object is the one this job's upload created (its upload token, landed_object_version; never size
    or presence): the job is finalized SUCCEEDED here (the landed object wins). False when it is not
    (or the job is no longer open). None when it could not be checked (HeadObject denied: the abort
    route and the error handler hold only AbortMultipartUpload on the asset buckets; or any other
    error). The row is read again here, after the abort. The post-landing work is done by the copy
    engine's reconcile of this stop (_reconcile_cancelled_stop in the engine; the caller hands the
    stop over), and also by Complete when it is still running."""
    try:
        row = get_job_row(table, job_id)
        if not is_open_job(row):
            return False
        landed, version_id = landed_object_version(s3_client, row)
        if not landed:
            return False
        finalize_job(table, job_id, STATUS_SUCCEEDED, {"destVersionId": version_id})
        logger.info(f"Copy job {job_id} had landed its file before the cancel; kept as succeeded")
        return True
    except Exception as e:
        logger.warning(f"Could not check whether copy job {job_id} had landed its file: {e}")
        return None


def _parse_iso(value):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def ingestion_hold(main_item, sfn_status, max_wait_hours=None, now=None):
    """How a read-path reconcile treats a Step Functions status for a main row that may still be
    ingesting outputs. Returns None (adopt the status as usual), "hold" (keep the row RUNNING: its
    state machine ended on a definition that does not wait, and the copy engine finalizes the row),
    or "expired" (the copies never reported within the maximum wait plus an hour: record FAILED with
    REASON_NOT_REPORTED).

    Only a SUCCEEDED state machine is held. A failed, timed-out or aborted one is adopted as usual,
    since the run did not reach its output step's end."""
    if (main_item or {}).get("executionPhase") != EXECUTION_PHASE_INGESTING_OUTPUTS:
        return None
    if sfn_status != "SUCCEEDED":
        return None
    hours = max_wait_hours if max_wait_hours is not None else MAX_OUTPUT_INGESTION_WAIT_HOURS
    started = _parse_iso((main_item or {}).get("outputIngestionStartedDate", ""))
    now = now or datetime.now(timezone.utc)
    if started is None or now - started > timedelta(hours=hours + 1):
        return "expired"
    return "hold"
