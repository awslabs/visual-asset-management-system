# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared rules of Download as Zip (prepared downloads).

A zip download is a job owned by the user who requested it: a row in the zip download requests
table (PK ``userId``, SK ``requestId``), a request manifest and, once the zipArchive system pipeline
has run, an archive under ``vams.preparedDownloads/`` in the asset auxiliary bucket. The API
(``handlers/assets/zipDownloadService.py``, with its read-time reconcile and delayed sweep), and
the zipArchive container all write the row, so
the status machine is defined once here:

- ``QUEUED`` -> ``RUNNING`` -> one of ``SUCCEEDED`` / ``FAILED`` / ``CANCELLED``.
- Terminal statuses are final (monotonic). Every writer conditions on the row still being
  ``QUEUED`` or ``RUNNING``, so the first terminal write wins and every later one is a no-op.
- A landed archive wins, with one outcome. Every writer that finalizes a job for a reason other
  than the archive (the read-time reconcile, the user's cancel, the
  container's own failure and stop paths) first makes sure no archive landed: the API side aborts the
  job's open uploads and then checks that the key's object is the one THIS job's upload created
  (landed_archive: the row's archiveUploadToken, never presence or size) (finalize_ended_run, cancel_job); the
  container skips its FAILED write once its upload completed. The decision after an abort never
  depends on what the abort answered: S3 answers 204 to an abort sent during or after a
  CompleteMultipartUpload that succeeds, and the completed object stays (#1123 D1). A Complete still
  in flight when a writer decided "not landed" can therefore still land; the container that wrote it
  then finds the job finalized and deletes it (its own version only). If that container was killed
  during the Complete, the archive stays unserved (only a SUCCEEDED job gets a download URL) until the
  job is deleted or the prepared-downloads retention rule expires it.
- A user's cancel is recorded on the row (``cancelRequestedAt``, request_cancel) BEFORE anything stops
  the run. From then on no writer records FAILED: the read-time reconcile and the container's own
  failure write both condition on the marker being absent, and a finalizer that
  finds it records CANCELLED (by the user) instead (decide_finalization). So a cancel whose own stop
  made the run end before the cancel's CANCELLED write landed still ends CANCELLED (#1125 D22). A
  landed archive still wins over the cancel.
- The container is the primary status source (live progress, its own refusal reason). The
  read-time reconcile in the API finalizes a row the container could not (a job that never started,
  a timeout, an abort from the workflow executions views) from the run's status on its execution
  row, found by the job's stored ``workflowExecutionId``, when the job is next read. The run sends
  the job nothing when it ends (no terminal callback). Incomplete uploads no reader aborted are
  removed by the ``vams.preparedDownloads/`` lifecycle rule after a day, and working objects of a job
  that ended expire with the retention rule.

This module has NO import-time AWS or environment dependency.
"""

import hashlib
import json
import math
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from botocore.exceptions import ClientError

from common.s3MetadataKeys import VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY, is_own_upload_object
from common.s3PathPatterns import AUXILIARY_PREPARED_DOWNLOADS_PREFIX

PREPARED_DOWNLOADS_PREFIX = AUXILIARY_PREPARED_DOWNLOADS_PREFIX

# How long a zip download job and its prepared archive are kept: a fixed 7 days, not configuration.
# The job row's expiresAt (the table's TTL) is createdAt plus this, list and get hide a row past it,
# and the vams.preparedDownloads/ lifecycle rule expires the archive after the same number of days
# (infra storageBuilder-nestedStack.ts ZIP_DOWNLOAD_RETENTION_DAYS; a test keeps the two equal).
# Mode 2 archives written into an asset are asset versions and are not affected.
ZIP_DOWNLOAD_RETENTION_DAYS = 7

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)
TERMINAL_STATUSES = (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED)

ARCHIVE_FORMATS = ("zip", "tar", "tar.gz")
ARCHIVE_EXTENSIONS = {"zip": ".zip", "tar": ".tar", "tar.gz": ".tar.gz"}
COMPRESSIONS = ("store", "deflate")

CANCELLED_BY_USER = "user"
CANCELLED_BY_EXECUTION = "execution"

# User metadata key (x-amz-meta-...) the zipArchive container stamps on its upload with the row's
# archiveUploadToken: the same key the large object copy engine stamps its own token under
# (common.s3MetadataKeys), compared by the same helper (is_own_upload_object). Mirrored in the
# container (zip_archive_pipeline/runner.py), pinned by its tests.
ARCHIVE_UPLOAD_TOKEN_METADATA = VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY

# The zipArchive system workflow every zip download job runs.
ZIP_ARCHIVE_WORKFLOW_ID_DEFAULT = "utility-zip-archive"
ZIP_ARCHIVE_WORKFLOW_DATABASE_ID_DEFAULT = "GLOBAL"
# The system trigger value a zip download run is launched under. Download as Zip owns it: the
# workflow core only checks its format (common.workflows.systemPipelines.is_system_trigger) and
# stores it as given, so the run's execution rows carry this exact value. Nothing in the zip
# feature reads it back: a job follows its run by the stored workflowExecutionId, so a run
# recorded under an earlier value (Zip-Download) still reconciles.
ZIP_SYSTEM_TRIGGER = "System-ZipDownload"

# Version of the system execution context and of the request manifest the container reads.
CONTEXT_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
MANIFEST_FILE_NAME = "manifest.json"
RESOLVED_MANIFEST_FILE_NAME = "resolved-manifest.jsonl"

# A refusal or failure reason the zipArchive container writes is user-safe text it prefixes with this
# marker; only such text is ever shown from a workflow's execution error.
CONTAINER_REASON_PREFIX = "ZIPDOWNLOAD: "

# How many requested entries a job row keeps for display (the full selection is in the manifest).
SELECTION_PREVIEW_ENTRIES = 20

# A QUEUED row whose run was never recorded is failed by the read-time reconcile after this long.
LAUNCH_GRACE_SECONDS = 600

MSG_DISABLED = "Zip downloads are not enabled for this deployment"
MSG_NOT_FOUND = "Zip download not found"
MSG_CAP_REACHED = "You already have the maximum number of active zip downloads"
MSG_LAUNCH_FAILED = "The archive job could not be started"
# The zipArchive workflow refused the launch (a 4xx from the execute handler). Generic on purpose: the
# refusal's own text names workflow configuration, so it goes to the service log only.
MSG_LAUNCH_REFUSED = "The archive job could not be started for this selection"
# A transient launch condition (a stale workflow deployment, the state machine's execution limit, a
# throttle): answered 503. The selection itself is fine.
MSG_LAUNCH_BUSY = "The archive job could not be started right now. Try again in a moment."
# The same words as the web's refusal (CreateZipDownloadModal), without naming the file.
MSG_ARCHIVED_IN_SELECTION = ("The selection includes an archived or deleted item. Remove it from the "
                             "selection, or restore it first.")
MSG_NOT_PRODUCED = "The archive was not produced"
MSG_JOB_FAILED = "The archive job failed"
MSG_JOB_TIMED_OUT = "The archive job timed out"
# The zipArchive container was stopped outside VAMS (an AWS Batch terminate) and could not record why
# (#1125 D26): the same words the container records as its stop reason (runner.STOPPED).
MSG_JOB_STOPPED = "The archive job was stopped"
# The zipArchive container stopped sending heartbeats (killed without a SIGTERM: out of memory, host
# loss), so its run ended with States.HeartbeatTimeout (#1125 D26).
MSG_JOB_LOST = "The archive job stopped responding"
# Error names in a run's executionError that identify those two ends. The zipArchive pipelineEnd
# reports the state's error as "Pipeline Failure: <Error>", which the workflow error handler records.
CONTAINER_STOPPED_ERROR = "ZipArchiveContainerStopped"
HEARTBEAT_TIMEOUT_ERROR = "States.HeartbeatTimeout"
MSG_CANCELLED_FROM_EXECUTIONS = "Cancelled from workflow executions"
MSG_NOT_READY = "The archive is not ready for download"
MSG_EXPIRED = "This zip download has expired"
# A cancel of a job that ended before the cancel was recorded: the job keeps its outcome.
MSG_ALREADY_FINISHED = "The zip download finished before it could be cancelled"

_ARCHIVE_NAME_ALLOWED = re.compile(r"[^A-Za-z0-9 ._()\-]")
MAX_ARCHIVE_FILE_NAME_CHARS = 200


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_request_id(now_ms: Optional[int] = None, suffix: Optional[str] = None) -> str:
    """A time-sortable job id: zero-padded base-36 epoch milliseconds, then a uuid4 hex.

    Lexicographic order equals creation order, so a reverse range query lists newest first."""
    millis = int(time.time() * 1000) if now_ms is None else int(now_ms)
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    text = ""
    while True:
        millis, rem = divmod(millis, 36)
        text = digits[rem] + text
        if millis == 0:
            break
    return f"{text.rjust(9, '0')}-{suffix or uuid.uuid4().hex}"


REQUEST_ID_PATTERN = re.compile(r"^[0-9a-z]{9}-[0-9a-f]{32}$")


def is_request_id(value) -> bool:
    return isinstance(value, str) and bool(REQUEST_ID_PATTERN.match(value))


def user_hash(user_id: str) -> str:
    """The per-user path segment: first 16 hex characters of SHA-256 of the user id."""
    return hashlib.sha256((user_id or "").encode("utf-8")).hexdigest()[:16]


def user_asset_key(user_id: str, database_id: str, asset_id: str) -> str:
    """Partition key of the per-asset index."""
    return f"{user_id}#{database_id}#{asset_id}"


def request_prefix(database_id: str, asset_id: str, user_id: str, request_id: str) -> str:
    return (f"{PREPARED_DOWNLOADS_PREFIX}{database_id}/{asset_id}/{user_hash(user_id)}/"
            f"{request_id}/")


def manifest_key(database_id, asset_id, user_id, request_id) -> str:
    return request_prefix(database_id, asset_id, user_id, request_id) + MANIFEST_FILE_NAME


def resolved_manifest_key(database_id, asset_id, user_id, request_id) -> str:
    return request_prefix(database_id, asset_id, user_id, request_id) + RESOLVED_MANIFEST_FILE_NAME


def archive_key(database_id, asset_id, user_id, request_id, archive_file_name) -> str:
    return request_prefix(database_id, asset_id, user_id, request_id) + archive_file_name


def sanitize_archive_file_name(requested: Optional[str], archive_format: str, asset_name: str,
                               now: Optional[datetime] = None) -> str:
    """The archive's file name: the request's name, or ``<assetName>-<YYYYMMDD-HHMMSS>``, reduced to a
    safe character set and given the format's extension.

    Any directory part is dropped, characters outside ``[A-Za-z0-9 ._()-]`` become ``_``, leading
    dots are removed (no hidden or relative names), and the base is cut so the whole name fits
    MAX_ARCHIVE_FILE_NAME_CHARS."""
    extension = ARCHIVE_EXTENSIONS[archive_format]
    name = (requested or "").replace("\\", "/").split("/")[-1].strip()
    if not name:
        stamp = (now or utc_now()).strftime("%Y%m%d-%H%M%S")
        name = f"{(asset_name or 'asset').strip()}-{stamp}"
    lowered = name.lower()
    for ext in sorted(ARCHIVE_EXTENSIONS.values(), key=len, reverse=True):
        if lowered.endswith(ext):
            name = name[: -len(ext)]
            break
    name = _ARCHIVE_NAME_ALLOWED.sub("_", name).lstrip(". ").rstrip(" ")
    if not name:
        name = "archive"
    name = name[: MAX_ARCHIVE_FILE_NAME_CHARS - len(extension)]
    return name + extension


def expires_at_epoch(created: datetime, retention_days: int = ZIP_DOWNLOAD_RETENTION_DAYS) -> int:
    return int(created.timestamp()) + int(retention_days) * 86400


def is_expired(row: dict, now_epoch: Optional[float] = None) -> bool:
    """TTL deletion lags; a row past its expiresAt reads as absent."""
    expires = (row or {}).get("expiresAt")
    if expires is None:
        return False
    try:
        return float(expires) <= (time.time() if now_epoch is None else now_epoch)
    except (TypeError, ValueError):
        return False


def part_size_bytes(estimated_total_bytes: int) -> int:
    """Multipart part size for an archive of about ``estimated_total_bytes``: at least 16 MiB, large
    enough to stay within 9,500 parts (headroom under S3's 10,000), rounded up to 1 MiB and capped at
    S3's 5 GiB part maximum. The container uses the same arithmetic."""
    mib = 1024 * 1024
    size = max(16 * mib, math.ceil(max(int(estimated_total_bytes), 0) / 9500))
    size = math.ceil(size / mib) * mib
    return min(size, 5 * 1024 * mib)


# ---------------------------------------------------------------------------
# Row writes
# ---------------------------------------------------------------------------

def row_key(user_id: str, request_id: str) -> dict:
    return {"userId": user_id, "requestId": request_id}


def finalize_row(table, user_id: str, request_id: str, status: str, *, error_message: str = "",
                 cancelled_by: str = "", archive_size_bytes: Optional[int] = None,
                 now: Optional[datetime] = None, expected_execution_id: str = "",
                 unless_cancel_requested: bool = False) -> bool:
    """Move a non-terminal row to a terminal ``status``. True when this call made the transition;
    False when the row is missing or already terminal (the first terminal write wins).

    With ``expected_execution_id`` the write also requires the row to belong to that run, so a
    finalizer acting on an older run never finalizes a row relaunched since. With
    ``unless_cancel_requested`` it also requires that no user cancel was recorded
    (``cancelRequestedAt``, request_cancel): a FAILED write then loses to a pending cancel (#1125 D22)."""
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"not a terminal zip download status: {status}")
    stamp = iso(now or utc_now())
    names = {"#status": "status"}
    values = {":status": status, ":queued": STATUS_QUEUED, ":running": STATUS_RUNNING,
              ":completedAt": stamp, ":updatedAt": stamp}
    sets = ["#status = :status", "completedAt = :completedAt", "updatedAt = :updatedAt"]
    if error_message:
        sets.append("errorMessage = :errorMessage")
        values[":errorMessage"] = error_message
    if cancelled_by:
        sets.append("cancelledBy = :cancelledBy")
        values[":cancelledBy"] = cancelled_by
    if archive_size_bytes is not None:
        sets.append("archiveSizeBytes = :archiveSizeBytes")
        values[":archiveSizeBytes"] = int(archive_size_bytes)
    condition = "attribute_exists(requestId) AND #status IN (:queued, :running)"
    if expected_execution_id:
        condition += " AND workflowExecutionId = :eid"
        values[":eid"] = expected_execution_id
    if unless_cancel_requested:
        condition += " AND attribute_not_exists(cancelRequestedAt)"
    try:
        table.update_item(
            Key=row_key(user_id, request_id),
            UpdateExpression="SET " + ", ".join(sets),
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def container_reason(text) -> str:
    """The user-safe part of a reason the zipArchive container wrote, or '' when the text is not one."""
    if isinstance(text, str) and text.startswith(CONTAINER_REASON_PREFIX):
        return text[len(CONTAINER_REASON_PREFIX):].strip()[:1024]
    return ""


RUN_TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED")


def decide_finalization(execution_status: str, row: dict, execution_error: str = "",
                        archive_exists: Optional[Callable[[], Tuple[bool, Optional[int]]]] = None):
    """How a terminal workflow run finalizes a still non-terminal job row.

    Returns ``(status, fields)`` for finalize_row, or ``None`` when nothing should be written
    (a non-terminal run, or a row that is already terminal).

    - A landed archive wins (``archive_exists() -> (landed, size)``, landed_archive): whatever the
      run's status, the job is SUCCEEDED when the key's current object is the one this job's upload
      created (its upload token), never because some object of some size is there.
      CompleteMultipartUpload is atomic, so that object is the whole archive. Callers abort the open
      uploads first (finalize_ended_run) and decide here whatever the abort answered. An archive can
      still land after this check from a Complete that was already in flight (S3 answers 204 to the
      abort and lets it finish) or from an upload created since; the container deletes it when it
      finds the job finalized.
    - A recorded user cancel (``cancelRequestedAt``, request_cancel) without the archive: CANCELLED,
      cancelledBy the canceller, whatever the run's status. The cancel's own stop makes the container
      stop and exit 0, so the run can end SUCCEEDED before the cancel's own write (#1125 D22).
    - SUCCEEDED without the archive: FAILED (not produced).
    - ABORTED: FAILED "Cancelled from workflow executions", cancelledBy execution.
    - FAILED / TIMED_OUT: FAILED with the container's own reason when it gave one (on the row or as
      a prefixed execution error), else for TIMED_OUT the timeout message, else the stop the
      container recorded on SIGTERM (``stopReason``, a Batch terminate outside an abort), else, by
      the error name in the execution error, MSG_JOB_STOPPED for the container's own stop answer
      (CONTAINER_STOPPED_ERROR) or MSG_JOB_LOST for a heartbeat timeout (HEARTBEAT_TIMEOUT_ERROR, a
      container killed without a SIGTERM; #1125 D26), else a fixed message. Raw execution errors are
      never shown: they can carry internal resource names; only these fixed names are matched.

    The container never finalizes a job on SIGTERM; it only records ``stopReason`` and leaves the
    outcome to this rule, which knows ABORTED from TIMED_OUT or FAILED, so an abort from the
    executions views always ends "Cancelled from workflow executions" (#1125 D24).
    """
    if not row or row.get("status") not in ACTIVE_STATUSES:
        return None
    if execution_status not in RUN_TERMINAL_STATUSES:
        return None
    exists, size = (archive_exists() if archive_exists else (False, None))
    if exists:
        return STATUS_SUCCEEDED, {"archive_size_bytes": size}
    if row.get("cancelRequestedAt"):
        return STATUS_CANCELLED, {"cancelled_by": row.get("cancelRequestedBy") or CANCELLED_BY_USER}
    if execution_status == "SUCCEEDED":
        return STATUS_FAILED, {"error_message": MSG_NOT_PRODUCED}
    if execution_status == "ABORTED":
        return STATUS_FAILED, {"error_message": MSG_CANCELLED_FROM_EXECUTIONS,
                               "cancelled_by": CANCELLED_BY_EXECUTION}
    if execution_status in ("FAILED", "TIMED_OUT"):
        reason = (row.get("errorMessage") or "").strip() or container_reason(execution_error)
        if not reason and execution_status == "TIMED_OUT":
            reason = MSG_JOB_TIMED_OUT
        if not reason:
            reason = (row.get("stopReason") or "").strip()[:1024]
        if not reason:
            error = execution_error if isinstance(execution_error, str) else ""
            if CONTAINER_STOPPED_ERROR in error:
                reason = MSG_JOB_STOPPED
            elif HEARTBEAT_TIMEOUT_ERROR in error:
                reason = MSG_JOB_LOST
            else:
                reason = MSG_JOB_FAILED
        return STATUS_FAILED, {"error_message": reason}
    return None


def request_stop(table, row: dict, logger=None) -> bool:
    """Record on an open job that a stopper is ending it (``stopRequestedAt``), BEFORE its abort
    (the #1123 D5 rule, #1125 D16). S3 keeps a part whose UploadPart was in flight during an abort,
    and the container's sink reads the job before every part (zipArchive status.must_stop): once it
    sees this marker it uploads no further part and aborts again after its own last one. No status
    change, so a landed archive still wins. Best-effort: False when the job is not open or the write
    failed."""
    try:
        table.update_item(
            Key=row_key(row["userId"], row["requestId"]),
            UpdateExpression="SET stopRequestedAt = :stopRequestedAt",
            ConditionExpression="attribute_exists(requestId) AND #status IN (:queued, :running)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":stopRequestedAt": iso(utc_now()), ":queued": STATUS_QUEUED,
                                       ":running": STATUS_RUNNING},
        )
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException" and logger:
            logger.warning(f"Could not record the stop of a zip download: {e}")
        return False


def request_cancel(table, row: dict, cancelled_by: str, logger=None) -> bool:
    """Record the user's cancel on an open job (``cancelRequestedAt``, ``cancelRequestedBy``) together
    with the stop marker, BEFORE anything stops the run (#1125 D22). From then on a FAILED write loses
    (finalize_row ``unless_cancel_requested``; the container's failure write conditions the same way)
    and a finalizer records CANCELLED instead (decide_finalization), so the job ends CANCELLED whichever
    writer finalizes it. True when recorded; False when the job is no longer open (it ended first)."""
    stamp = iso(utc_now())
    try:
        table.update_item(
            Key=row_key(row["userId"], row["requestId"]),
            UpdateExpression=("SET cancelRequestedAt = :cancelRequestedAt, "
                              "cancelRequestedBy = :cancelRequestedBy, "
                              "stopRequestedAt = :stopRequestedAt"),
            ConditionExpression="attribute_exists(requestId) AND #status IN (:queued, :running)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":cancelRequestedAt": stamp, ":cancelRequestedBy": cancelled_by,
                                       ":stopRequestedAt": stamp, ":queued": STATUS_QUEUED,
                                       ":running": STATUS_RUNNING},
        )
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        return False


def _abort_then_reread(table, s3_client, bucket: str, row: dict, logger=None) -> Optional[dict]:
    """Record the stop, abort the job's open uploads until they are gone, then read the row again
    (consistent): the decision must see the archiveUploadToken the container may have recorded after
    the caller read the row."""
    request_stop(table, row, logger)
    abort_open_uploads(s3_client, bucket, request_prefix(row["databaseId"], row["assetId"],
                                                         row["userId"], row["requestId"]), logger)
    fresh = table.get_item(Key=row_key(row["userId"], row["requestId"]),
                           ConsistentRead=True).get("Item")
    # The aborts' answers are not final (a part accepted before the stop can be listed again minutes
    # later): the delayed sweep aborts the recorded upload again, unless the archive landed.
    schedule_delayed_sweep(fresh or row, logger)
    return fresh


def finalize_ended_run(table, s3_client, bucket: str, row: dict, execution_status: str,
                       execution_error: str = "", logger=None) -> Tuple[Optional[str], bool]:
    """Finalize a non-terminal job whose workflow run ended: the API's read-time reconcile
    (zipDownloadService._reconcile) comes here. Returns ``(status, changed)``; ``(None, False)`` when there
    is nothing to write.

    The job's open multipart uploads are aborted BEFORE the decision, so S3 refuses a
    CompleteMultipartUpload the container sends after it. StopExecution and a Batch terminate do not
    stop a container that is still writing, which is why the order matters. The decision is the job's
    own landed object (landed_archive, its upload token) whatever the abort answered: S3 answers 204,
    not NoSuchUpload, to an abort sent during or after a Complete that succeeds, and the object stays
    (#1123 D1). A Complete already in flight can land after a "not landed" decision; the container then
    loses its SUCCEEDED write and deletes that archive (module docstring)."""
    if not row or row.get("status") not in ACTIVE_STATUSES \
            or execution_status not in RUN_TERMINAL_STATUSES:
        return None, False
    row = _abort_then_reread(table, s3_client, bucket, row, logger)
    decision = decide_finalization(
        execution_status, row, execution_error,
        archive_exists=lambda: landed_archive(s3_client, bucket, row))
    if not decision:
        return None, False
    status, fields = decision
    expected = row.get("workflowExecutionId") or ""
    changed = finalize_row(table, row["userId"], row["requestId"], status,
                           expected_execution_id=expected,
                           unless_cancel_requested=status == STATUS_FAILED, **fields)
    if not changed and status == STATUS_FAILED:
        # A user cancel recorded after this call read the row wins over the FAILED (#1125 D22).
        fresh = table.get_item(Key=row_key(row["userId"], row["requestId"]),
                               ConsistentRead=True).get("Item")
        if fresh and fresh.get("status") in ACTIVE_STATUSES and fresh.get("cancelRequestedAt"):
            status = STATUS_CANCELLED
            changed = finalize_row(table, row["userId"], row["requestId"], status,
                                   cancelled_by=fresh.get("cancelRequestedBy") or CANCELLED_BY_USER,
                                   expected_execution_id=expected)
    if changed and status != STATUS_SUCCEEDED:
        delete_working_objects(s3_client, bucket, row, logger)
    elif not changed and status != STATUS_SUCCEEDED:
        # Another writer finalized the job first (the container's own failure, the user's cancel):
        # a job that ended without its archive keeps no working objects, whoever wrote the outcome
        # (#1125 D24). A job another writer recorded SUCCEEDED keeps them, as on the success path.
        final = table.get_item(Key=row_key(row["userId"], row["requestId"]),
                               ConsistentRead=True).get("Item")
        if final and final.get("status") in (STATUS_FAILED, STATUS_CANCELLED):
            delete_working_objects(s3_client, bucket, row, logger)
    return status, changed


CANCEL_CANCELLED = "cancelled"
CANCEL_LANDED = "landed"
CANCEL_ENDED = "ended"


def cancel_job(table, s3_client, bucket: str, row: dict, cancelled_by: str, logger=None) -> str:
    """The user's cancel of a non-terminal job. Same rule as finalize_ended_run: abort the open
    uploads, then a landed archive wins, decided by the job's upload token whatever the abort answered.

    Returns CANCEL_CANCELLED when the job is CANCELLED (by this call, or by a reconcile that found
    the recorded cancel), CANCEL_LANDED when the archive had landed (the job is
    SUCCEEDED, recorded here or by the container), or CANCEL_ENDED when the job had already ended
    before the cancel was recorded. Never deletes the job: the caller reports a job that ended.

    The cancel is recorded first (request_cancel, #1125 D22), before the stop marker reaches the
    container: the run its stop ends can be finalized by a read-time reconcile at any point after
    that, and that reconcile then records CANCELLED rather than FAILED."""
    def _outcome():
        fresh = table.get_item(Key=row_key(row["userId"], row["requestId"]),
                               ConsistentRead=True).get("Item") or {}
        status = fresh.get("status")
        if status == STATUS_CANCELLED:
            return CANCEL_CANCELLED
        return CANCEL_LANDED if status == STATUS_SUCCEEDED else CANCEL_ENDED

    if not request_cancel(table, row, cancelled_by, logger):
        return _outcome()
    fresh = _abort_then_reread(table, s3_client, bucket, row, logger)
    if not fresh or fresh.get("status") not in ACTIVE_STATUSES:
        return _outcome()
    row = fresh
    exists, size = landed_archive(s3_client, bucket, row)
    if exists:
        if finalize_row(table, row["userId"], row["requestId"], STATUS_SUCCEEDED, archive_size_bytes=size,
                        expected_execution_id=row.get("workflowExecutionId") or ""):
            return CANCEL_LANDED
        return _outcome()
    if finalize_row(table, row["userId"], row["requestId"], STATUS_CANCELLED, cancelled_by=cancelled_by):
        # A resolved manifest the container writes after this stays until the job is deleted or the
        # vams.preparedDownloads/ retention rule expires it; it is never served.
        delete_working_objects(s3_client, bucket, row, logger)
        return CANCEL_CANCELLED
    return _outcome()


# ---------------------------------------------------------------------------
# Response projection
# ---------------------------------------------------------------------------

def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _iso_from_epoch(value) -> str:
    try:
        return iso(datetime.fromtimestamp(float(value), tz=timezone.utc))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def to_job_item(row: dict) -> dict:
    """The response shape of one job. Never the owner id, S3 keys, bucket names or the run id."""
    summary = row.get("selectionSummary") or {}
    preview = []
    for entry in row.get("selectionPreview") or []:
        if not isinstance(entry, dict):
            continue
        shaped = {"type": entry.get("type", ""), "relativePath": entry.get("relativePath", "")}
        if entry.get("versionId"):
            shaped["versionId"] = entry["versionId"]
        preview.append(shaped)
    item = {
        "requestId": row.get("requestId", ""),
        "databaseId": row.get("databaseId", ""),
        "assetId": row.get("assetId", ""),
        "assetName": row.get("assetName", ""),
        "status": row.get("status", ""),
        "archiveFormat": row.get("archiveFormat", ""),
        "compression": row.get("compression", ""),
        "archiveFileName": row.get("archiveFileName", ""),
        "archiveSizeBytes": _int(row.get("archiveSizeBytes"), None),
        "selectionSummary": {
            "files": _int(summary.get("files")),
            "folders": _int(summary.get("folders")),
            "wholeAsset": bool(summary.get("wholeAsset", False)),
        },
        "selectionPreview": preview,
        "assetVersionId": row.get("assetVersionId") or None,
        "progress": {
            "filesDone": _int(row.get("filesDone")),
            "filesTotal": _int(row.get("filesTotal")),
            "bytesDone": _int(row.get("bytesDone")),
            "bytesTotal": _int(row.get("bytesTotal")),
        },
        "errorMessage": row.get("errorMessage") or None,
        "cancelledBy": row.get("cancelledBy") or None,
        "createdAt": row.get("createdAt", ""),
        "startedAt": row.get("startedAt") or None,
        "completedAt": row.get("completedAt") or None,
        "expiresAt": _iso_from_epoch(row.get("expiresAt")),
    }
    return item


# ---------------------------------------------------------------------------
# Prefix cleanup (auxiliary bucket, one job's prefix)
# ---------------------------------------------------------------------------

def _require_job_prefix(prefix: str) -> None:
    """Every cleanup is confined to one job's prefix: never the bare prepared-downloads root."""
    if not isinstance(prefix, str) or not prefix.startswith(PREPARED_DOWNLOADS_PREFIX) \
            or not prefix.endswith("/") or prefix.count("/") != 5 or "//" in prefix:
        raise ValueError("cleanup is limited to one zip download's prefix")


# Bounded abort sweep (#1125 D16, the #1123 D5 rule; common/largeObjectCopy.abort_until_gone): after an
# abort, a part the container was still uploading can land and keep the upload listed, so the abort is
# repeated until ListParts answers NoSuchUpload. Short, because the cancel and the read-time reconcile
# run inside an API call; the container's sink aborts again after its own last part.
ABORT_SWEEP_ATTEMPTS = 3
ABORT_SWEEP_WAIT_SECONDS = 2


def _sleep(seconds):
    time.sleep(seconds)


def abort_until_gone(s3_client, bucket: str, key: str, upload_id: str, attempts=ABORT_SWEEP_ATTEMPTS,
                     wait_seconds=ABORT_SWEEP_WAIT_SECONDS, sleep: Optional[Callable] = None) -> bool:
    """Abort one multipart upload until S3 no longer knows it (ListParts answers NoSuchUpload), at
    most ``attempts`` aborts, waiting ``wait_seconds`` before each repeat (only while it is still
    listed). True when the upload is gone, False when it is still listed after the last attempt.
    Raises on an S3 error other than NoSuchUpload. Removes no object: a completed upload is gone as an
    upload and its archive stays."""
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
    return False


def abort_open_uploads(s3_client, bucket: str, prefix: str, logger=None, sleep=None) -> int:
    """Abort every incomplete multipart upload under one job's prefix until it is gone
    (abort_until_gone). Returns the count aborted.

    Best-effort: a failure is logged and the bucket's abort-incomplete-multipart lifecycle rule is
    the backstop. The answer never decides anything: a 204 does not mean nothing landed (S3 answers
    204 to an abort during or after a Complete that succeeds), and NoSuchUpload does not mean
    something did. Callers decide by landed_archive afterwards."""
    _require_job_prefix(prefix)
    aborted = 0
    kwargs = {"Bucket": bucket, "Prefix": prefix}
    try:
        while True:
            response = s3_client.list_multipart_uploads(**kwargs)
            for upload in response.get("Uploads", []) or []:
                key = upload.get("Key", "")
                if not key.startswith(prefix):
                    continue
                try:
                    if not abort_until_gone(s3_client, bucket, key, upload.get("UploadId"), sleep=sleep):
                        if logger:
                            logger.warning("A multipart upload of a zip download is still listed after "
                                           f"{ABORT_SWEEP_ATTEMPTS} aborts; the container aborts it "
                                           "again after its last part")
                    aborted += 1
                except ClientError as e:
                    if logger:
                        logger.warning(f"Could not abort a multipart upload of a zip download: {e}")
            if not response.get("IsTruncated"):
                break
            kwargs["KeyMarker"] = response.get("NextKeyMarker", "")
            kwargs["UploadIdMarker"] = response.get("NextUploadIdMarker", "")
    except ClientError as e:
        if logger:
            logger.warning(f"Could not list multipart uploads of a zip download: {e}")
    return aborted


def delete_prefix(s3_client, bucket: str, prefix: str) -> int:
    """Delete every object version and delete marker under one job's prefix. Returns the count."""
    _require_job_prefix(prefix)
    deleted = 0
    kwargs = {"Bucket": bucket, "Prefix": prefix}
    while True:
        response = s3_client.list_object_versions(**kwargs)
        targets = [{"Key": v["Key"], "VersionId": v["VersionId"]}
                   for v in (response.get("Versions", []) or []) + (response.get("DeleteMarkers", []) or [])
                   if v.get("Key", "").startswith(prefix)]
        for start in range(0, len(targets), 1000):
            batch = targets[start:start + 1000]
            s3_client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
            deleted += len(batch)
        if not response.get("IsTruncated"):
            break
        kwargs["KeyMarker"] = response.get("NextKeyMarker", "")
        kwargs["VersionIdMarker"] = response.get("NextVersionIdMarker", "")
    return deleted


def working_object_keys(row: dict) -> Tuple[str, str]:
    """The job's working objects: the request manifest and the resolved manifest. Neither is needed
    once the job ended without an archive."""
    args = (row["databaseId"], row["assetId"], row["userId"], row["requestId"])
    return manifest_key(*args), resolved_manifest_key(*args)


def delete_working_objects(s3_client, bucket: str, row: dict, logger=None) -> int:
    """Delete every version (and delete marker) of a job's working objects right away, rather than
    leaving them to the prepared-downloads lifecycle rule. For a job that ended without an archive
    (cancelled, failed, refused); idempotent. Returns the count deleted. Best-effort: a failure is
    logged and the lifecycle rule is the backstop."""
    keys = working_object_keys(row)
    prefix = request_prefix(row["databaseId"], row["assetId"], row["userId"], row["requestId"])
    _require_job_prefix(prefix)
    deleted = 0
    try:
        for key in keys:
            kwargs = {"Bucket": bucket, "Prefix": key}
            while True:
                response = s3_client.list_object_versions(**kwargs)
                targets = [{"Key": v["Key"], "VersionId": v["VersionId"]}
                           for v in (response.get("Versions", []) or [])
                           + (response.get("DeleteMarkers", []) or [])
                           if v.get("Key") == key]
                if targets:
                    s3_client.delete_objects(Bucket=bucket, Delete={"Objects": targets, "Quiet": True})
                    deleted += len(targets)
                if not response.get("IsTruncated"):
                    break
                kwargs["KeyMarker"] = response.get("NextKeyMarker", "")
                kwargs["VersionIdMarker"] = response.get("NextVersionIdMarker", "")
    except ClientError as e:
        if logger:
            logger.warning(f"Could not delete a zip download's working objects: {e}")
    return deleted


# S3 answers a HeadObject on a missing key with 403, not 404, to a caller without s3:ListBucket on
# the bucket (#1125 D21). A 403 alone never means "not landed": the key's absence is then confirmed
# by a listing at the exact key (s3:ListBucket, granted on the prepared-downloads prefix, which is a
# request the s3:prefix condition sees). Only a listing without the key answers "no object"; a key
# that is listed, or a listing that fails, raises ArchiveCheckDenied, so a check that cannot tell
# fails closed and is logged as such.
_ACCESS_DENIED_CODES = ("403", "AccessDenied", "Forbidden")


class ArchiveCheckDenied(ClientError):
    """A zip download's archive HeadObject was refused (403) and a listing at the key did not show it
    absent, so whether this job's archive landed is unknown. A ClientError, so callers that already
    fail on a ClientError keep doing so."""

    def __str__(self):
        return ("Access denied checking a zip download's archive object (HeadObject 403, and a listing "
                "at the key did not show it absent): cannot tell whether the archive exists; the role "
                "needs s3:GetObject and s3:ListBucket on the prepared-downloads prefix "
                f"({super().__str__()})")


def _head_archive(s3_client, bucket: str, key: str):
    """HEAD of the archive key, or None when there is no object (a 404, or a 403 whose listing at the
    exact key shows no object). Any other 403 raises ArchiveCheckDenied."""
    try:
        return s3_client.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        if code not in _ACCESS_DENIED_CODES:
            raise
        denied = e
    try:
        # Keys list in order, so the key itself is the first one at that prefix when it exists.
        listed = s3_client.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
    except Exception as e:
        raise ArchiveCheckDenied(denied.response, denied.operation_name) from e
    if any(o.get("Key") == key for o in (listed.get("Contents") or [])):
        # The object exists and its HEAD was refused: a real denial, never "not landed".
        raise ArchiveCheckDenied(denied.response, denied.operation_name) from denied
    return None


def landed_archive(s3_client, bucket: str, row: dict) -> Tuple[bool, Optional[int]]:
    """(landed, size): whether the object at the job's archive key is the one THIS job's upload
    created. Never decided by presence or size: an earlier object of the same size can sit at the
    key. The container records a fresh ``archiveUploadToken`` on the row (conditional on the row
    being open) before it creates the upload, and stamps the same token as object metadata
    (ARCHIVE_UPLOAD_TOKEN_METADATA) on CreateMultipartUpload, so only that upload's completed object
    carries it. A row without a token (its container never started the upload, or ran an image from
    before the token) has no landed archive by this rule."""
    token = row.get("archiveUploadToken")
    if not token:
        return False, None
    head = _head_archive(s3_client, bucket, row.get("archiveKey", ""))
    if head is None:
        return False, None
    if not is_own_upload_object(head, token):
        return False, None
    return True, int(head.get("ContentLength", 0))


def archive_head(s3_client, bucket: str, key: str) -> Tuple[bool, Optional[int]]:
    """(exists, size) of a job's archive object (_head_archive: a 403 it cannot settle raises)."""
    head = _head_archive(s3_client, bucket, key)
    if head is None:
        return False, None
    return True, int(head.get("ContentLength", 0))


# ---------------------------------------------------------------------------
# Delayed sweep (#1125 D16, the #1123 D5 rule after the demo21 recheck)
# ---------------------------------------------------------------------------
#
# An abort's immediate answer is not final: on demo21 S3 answered an abort, ListParts right after it
# answered NoSuchUpload, and the upload was listed again 2 to 8 minutes later with a part accepted
# before the stop. So every stopper of a zip job (the user's cancel, the read-time reconcile, the
# delete of a job) also starts the job's delayed sweep: an execution of the
# zip download sweep state machine (infra lambdaBuilder/assetFunctions.ts: Wait
# SWEEP_FIRST_WAIT_SECONDS, sweep, Wait SWEEP_SECOND_WAIT_SECONDS, sweep), whose passes run the
# zip download service's ``sweep`` action (sweep_recorded_uploads). Deployed only with app.zipDownloads on.

SWEEP_STATE_MACHINE_ARN_ENV = "ZIP_DOWNLOAD_SWEEP_STATE_MACHINE_ARN"
SWEEP_EXECUTION_PREFIX = "zipsweep-"
SWEEP_FIRST_WAIT_SECONDS = 300
SWEEP_SECOND_WAIT_SECONDS = 600
# Upload pages read per pass for one archive key (the prefix is the exact key: normally one page).
SWEEP_MAX_LIST_PAGES = 10
# Upload ids carried in a sweep input (a job records one per archive attempt; normally one).
SWEEP_MAX_UPLOAD_IDS = 20

_sweep_sfn_client = None


def _sweep_client():
    global _sweep_sfn_client
    if _sweep_sfn_client is None:
        import boto3
        from botocore.config import Config
        _sweep_sfn_client = boto3.client("stepfunctions",
                                         config=Config(retries={"max_attempts": 5, "mode": "adaptive"}))
    return _sweep_sfn_client


def recorded_upload_ids(row: dict) -> list:
    """The archive multipart upload ids the container recorded on the job row (archiveUploadIds, a
    string set; a list in some test doubles), sorted, at most SWEEP_MAX_UPLOAD_IDS."""
    ids = (row or {}).get("archiveUploadIds") or []
    return sorted(str(i) for i in ids if isinstance(i, str) and i)[:SWEEP_MAX_UPLOAD_IDS]


def sweep_execution_name_for(user_id: str, request_id: str) -> str:
    """Step Functions execution name of a job's delayed sweep (<= 80 chars of [A-Za-z0-9-_])."""
    digest = hashlib.sha256(f"{user_id}#{request_id}".encode("utf-8")).hexdigest()[:48]
    return SWEEP_EXECUTION_PREFIX + digest


def sweep_request(row: dict) -> dict:
    """The sweep execution's input: the job's identity, and a snapshot of its archive key and recorded
    upload ids for a job deleted before the sweep runs. A pass reads the row when it still exists and
    trusts the snapshot only when it does not (sweep_recorded_uploads)."""
    return {"userId": row["userId"], "requestId": row["requestId"], "databaseId": row["databaseId"],
            "assetId": row["assetId"], "archiveKey": row.get("archiveKey", ""),
            "uploadIds": recorded_upload_ids(row)}


def schedule_delayed_sweep(row: Optional[dict], logger=None, sfn_client=None,
                           machine_arn: Optional[str] = None) -> bool:
    """Start the delayed sweep of a job's archive upload, once per job (a fixed execution name: an
    execution already started under it answers ExecutionAlreadyExists, which counts as scheduled).
    Best-effort: False when the feature's sweep is not deployed or the start failed (logged); the
    immediate aborts already ran and the prepared-downloads lifecycle rule is the backstop."""
    machine_arn = machine_arn if machine_arn is not None else os.environ.get(SWEEP_STATE_MACHINE_ARN_ENV, "")
    if not row or not row.get("userId") or not is_request_id(row.get("requestId")):
        return False
    if not machine_arn:
        # Only a job stopped while the feature is off (a job left from when it was on) gets here; no
        # later pass runs for it, so say so. The prepared-downloads lifecycle rule still applies.
        if logger:
            logger.warning(f"Could not schedule the delayed sweep of a zip download: no state machine "
                           f"ARN ({SWEEP_STATE_MACHINE_ARN_ENV})")
        return False
    try:
        client = sfn_client or _sweep_client()
        client.start_execution(stateMachineArn=machine_arn,
                               name=sweep_execution_name_for(row["userId"], row["requestId"]),
                               input=json.dumps({"zipSweep": sweep_request(row)}))
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ExecutionAlreadyExists":
            return True
        if logger:
            logger.warning(f"Could not schedule the delayed sweep of a zip download: {e}")
        return False
    except Exception as e:
        if logger:
            logger.warning(f"Could not schedule the delayed sweep of a zip download: {e!r}")
        return False


def _upload_ids_listed(s3_client, bucket: str, key: str, upload_ids, max_pages=SWEEP_MAX_LIST_PAGES) -> list:
    """The given upload ids that ListMultipartUploads (prefix = the exact key) lists at that key.
    Uploads of other writers, or at keys that only start with it, never match."""
    wanted, listed = set(upload_ids), []
    kwargs = {"Bucket": bucket, "Prefix": key}
    for _ in range(max(1, max_pages)):
        response = s3_client.list_multipart_uploads(**kwargs)
        for upload in response.get("Uploads", []) or []:
            if upload.get("Key") == key and upload.get("UploadId") in wanted:
                listed.append(upload["UploadId"])
        if not response.get("IsTruncated"):
            return listed
        kwargs["KeyMarker"] = response.get("NextKeyMarker", "")
        kwargs["UploadIdMarker"] = response.get("NextUploadIdMarker", "")
    return listed


def _sweep_target(table, request: dict):
    """(row, archive_key, upload_ids, reason): what one pass may abort. The row when it still exists
    (its archive key and recorded ids; ``reason`` names why nothing may be aborted), else the input's
    snapshot, confined to the job's own prefix. Raises ValueError on an input that names no job."""
    user_id, request_id = (request or {}).get("userId"), (request or {}).get("requestId")
    if not isinstance(user_id, str) or not user_id or not is_request_id(request_id):
        raise ValueError("The delayed sweep needs a zip download's identity")
    row = table.get_item(Key=row_key(user_id, request_id), ConsistentRead=True).get("Item")
    if row:
        status = row.get("status")
        if status == STATUS_SUCCEEDED:
            return row, "", [], "the job succeeded"
        if status in ACTIVE_STATUSES and not row.get("stopRequestedAt"):
            return row, "", [], "the job is still running"
        return row, row.get("archiveKey", ""), recorded_upload_ids(row), ""
    prefix = request_prefix(request.get("databaseId", ""), request.get("assetId", ""), user_id, request_id)
    _require_job_prefix(prefix)
    key = request.get("archiveKey", "")
    if not isinstance(key, str) or not key.startswith(prefix) or "/" in key[len(prefix):]:
        return None, "", [], "no archive key within the job's prefix"
    ids = [i for i in (request.get("uploadIds") or []) if isinstance(i, str) and i][:SWEEP_MAX_UPLOAD_IDS]
    return None, key, ids, ""


def sweep_recorded_uploads(table, s3_client, bucket: str, request: dict, final: bool = False,
                           logger=None) -> dict:
    """One pass of a zip job's delayed sweep. Decides by the landed archive first (a job whose own
    archive is at the key is left alone), then lists the uploads at the job's exact archive key and
    aborts only the ones the job row recorded (archiveUploadIds). Aborts nothing else and removes no
    object. Raises when the landed check or a listing fails (the state machine retries the pass)."""
    row, key, upload_ids, reason = _sweep_target(table, request)
    if reason:
        return {"swept": False, "reason": reason}
    if not key or not upload_ids:
        return {"swept": False, "reason": "no recorded upload"}
    if row is not None:
        try:
            landed, _size = landed_archive(s3_client, bucket, row)
        except ArchiveCheckDenied as e:
            # Fail closed: never abort uploads of a job whose archive may have landed. The pass fails
            # (the state machine retries it, then the run ends FAILED) and says why.
            if logger:
                logger.error(f"The zip download delayed sweep could not check the job's archive: {e}")
            raise
        if landed:
            return {"swept": False, "reason": "the job's archive landed"}
    listed = _upload_ids_listed(s3_client, bucket, key, upload_ids)
    for upload_id in listed:
        try:
            s3_client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "NoSuchUpload":
                raise
    remaining = _upload_ids_listed(s3_client, bucket, key, listed) if listed else []
    if remaining and logger:
        message = "A zip download's archive upload is still listed after the delayed sweep's abort"
        (logger.error if final else logger.warning)(
            message + ("; the prepared-downloads lifecycle rule removes it" if final else ""))
    return {"swept": True, "aborted": len(listed), "remaining": len(remaining)}
