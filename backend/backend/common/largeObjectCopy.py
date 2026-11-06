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
from datetime import datetime, timedelta, timezone

from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

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
# Batches one copy execution carries in its inline Map. Bounds the execution's state size and
# history; the planner widens batches rather than exceed it.
MAX_BATCHES = 1000

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

# Environment variables (set by CDK on every function that starts, runs or waits on copy jobs).
ENV_STATE_MACHINE_ARN = "LARGE_OBJECT_COPY_STATE_MACHINE_ARN"
ENV_API_UPLOAD_THRESHOLD = "LARGE_OBJECT_COPY_API_UPLOAD_THRESHOLD_BYTES"
ENV_MIN_PART_SIZE = "LARGE_OBJECT_COPY_MIN_PART_SIZE_BYTES"
ENV_PARTS_PER_BATCH = "LARGE_OBJECT_COPY_PARTS_PER_BATCH"
ENV_THREADS_PER_BATCH = "LARGE_OBJECT_COPY_THREADS_PER_BATCH"
ENV_MAX_WAIT_HOURS = "LARGE_OBJECT_COPY_MAX_WAIT_HOURS"

# Defaults and bounds; the same values as infra/config/config.ts (app.largeFileIngestion).
SETTINGS_BOUNDS = {
    "apiUploadEngineThresholdBytes": (ENV_API_UPLOAD_THRESHOLD, 5 * GIB, LARGE_FILE_THRESHOLD_BYTES, S3_MAX_OBJECT_BYTES),
    "minPartSizeBytes": (ENV_MIN_PART_SIZE, 256 * MIB, 64 * MIB, S3_MAX_PART_BYTES),
    "partsPerBatch": (ENV_PARTS_PER_BATCH, 16, 1, 64),
    "threadsPerBatch": (ENV_THREADS_PER_BATCH, 8, 1, 32),
    "maxOutputIngestionWaitHours": (ENV_MAX_WAIT_HOURS, 48, 1, 168),
}

_serializer = TypeSerializer()


class CopyRefused(Exception):
    """A copy that no retry can complete (blocked type, too large, cancelled). The state machine's
    retrier does not retry this error name."""


class SourceChanged(Exception):
    """The pinned source version changed or vanished during the copy. Not retried."""


def settings(environ=None):
    """The engine settings from the environment, each an int within its bounds. A missing or
    unusable value falls back to its default (config validation rejects bad values at synth)."""
    environ = os.environ if environ is None else environ
    resolved = {}
    for name, (env_name, default, low, high) in SETTINGS_BOUNDS.items():
        raw = environ.get(env_name, "")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        if value < low or value > high:
            value = default
        resolved[name] = value
    return resolved


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


def record_batch(table, batch_id, job_ids, context):
    """Record (or refresh) an ingestion batch: the copy jobs it waits on and the context the finalize
    step needs. Upsert: a job that finished first may already have created the row with its terminal
    count.

    The batch keeps the UNION of every job id any attempt of the output step recorded (jobIds), and
    its totalCount only grows to that union's size. A retried output step no longer lists a file
    whose copy already finished (its staged source is deleted), so a total taken from one attempt's
    listing alone would let the batch be claimed while a copy is still running. Returns the row as
    stored."""
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


def count_job_terminal(dynamodb_client, table_name, job_id, batch_id, status, extra=None):
    """Mark a job terminal and count it on its batch, in one transaction, exactly once.

    The job row's ``counted`` flag is set only if it is not set yet, and the batch's terminalCount is
    incremented in the same transaction, so a retried notify neither double counts nor loses the
    count. Returns True when this call counted the job, False when it was counted before."""
    values = {"status": status, "counted": True, "finishedAt": _iso_now(), "expiresAt": _expires_at()}
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


def cancel_execution_copy_jobs(table, sfn_client, s3_client, workflow_execution_id):
    """Stop every unfinished copy job of a workflow execution and abort its multipart upload.

    Used by the abort route and the error handler. A stopped Standard execution runs none of its
    Catch states, so the cleanup a Fail state would do happens here: the upload is aborted and the
    job row is marked CANCELLED. Best-effort per job; returns one warning per job that could not be
    stopped or cleaned up. Returns [] when the jobs table cannot be read (logged), as a run with no
    large outputs has no jobs."""
    warnings = []
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
        if execution_arn:
            try:
                sfn_client.stop_execution(executionArn=execution_arn, error="Cancelled",
                                          cause="The workflow execution ended.")
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") not in ("ExecutionDoesNotExist",):
                    logger.exception(f"Could not stop copy job {job_id}: {e}")
                    warnings.append(f"The copy of output '{job.get('relativeKey', '')}' could not "
                                    f"be stopped and may still be running.")
                    continue
        # A job with no upload id yet may be inside Prepare: Prepare records its upload only while
        # the job is not CANCELLED and otherwise aborts the upload itself.
        upload_id = job.get("uploadIdS3", "")
        if upload_id:
            try:
                s3_client.abort_multipart_upload(
                    Bucket=job.get("destBucket"), Key=job.get("destKey"), UploadId=upload_id)
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") != "NoSuchUpload":
                    logger.exception(f"Could not abort the multipart upload of copy job {job_id}: {e}")
                    warnings.append(f"The partial copy of output '{job.get('relativeKey', '')}' "
                                    f"could not be removed.")
        update_job_row(table, job_id, {"status": STATUS_CANCELLED, "error": REASON_CANCELLED,
                                       "finishedAt": _iso_now()},
                       condition="attribute_not_exists(#s) OR NOT #s IN (:a, :b)",
                       condition_values={":a": STATUS_SUCCEEDED, ":b": STATUS_FAILED})
    return warnings


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
    hours = max_wait_hours if max_wait_hours is not None else settings()["maxOutputIngestionWaitHours"]
    started = _parse_iso((main_item or {}).get("outputIngestionStartedDate", ""))
    now = now or datetime.now(timezone.utc)
    if started is None or now - started > timedelta(hours=hours + 1):
        return "expired"
    return "hold"
