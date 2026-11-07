# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared rules of Download as Zip (prepared downloads).

A zip download is a job owned by the user who requested it: a row in the zip download requests
table (PK ``userId``, SK ``requestId``), a request manifest and, once the zipArchive system pipeline
has run, an archive under ``vams.preparedDownloads/`` in the asset auxiliary bucket. The API
(``handlers/zipDownloads/zipDownloadService.py``), the terminal callback
(``handlers/zipDownloads/zipDownloadCallback.py``) and the zipArchive container all write the row, so
the status machine is defined once here:

- ``QUEUED`` -> ``RUNNING`` -> one of ``SUCCEEDED`` / ``FAILED`` / ``CANCELLED``.
- Terminal statuses are final (monotonic). Every writer conditions on the row still being
  ``QUEUED`` or ``RUNNING``, so the first terminal write wins and every later one is a no-op.
- A landed archive wins, with one outcome. Every writer that finalizes a job for a reason other
  than the archive (the terminal callback, the read-time reconcile, the user's cancel, the
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
- The container is the primary status source (live progress, its own refusal reason). The terminal
  callback of the workflow run, and the read-time reconcile in the API, finalize a row the container
  could not (a job that never started, a timeout, an abort from the workflow executions views).

This module has NO import-time AWS or environment dependency.
"""

import hashlib
import math
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from botocore.exceptions import ClientError

from common.s3MetadataKeys import VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY, is_own_upload_object
from common.s3PathPatterns import AUXILIARY_PREPARED_DOWNLOADS_PREFIX

PREPARED_DOWNLOADS_PREFIX = AUXILIARY_PREPARED_DOWNLOADS_PREFIX

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

# The zipArchive system workflow and the system trigger its zip-download runs are recorded under
# (common.workflows.systemPipelines.SYSTEM_TRIGGER_TYPE_TO_STORED).
ZIP_ARCHIVE_WORKFLOW_ID_DEFAULT = "utility-zip-archive"
ZIP_ARCHIVE_WORKFLOW_DATABASE_ID_DEFAULT = "GLOBAL"
ZIP_DOWNLOAD_REQUEST_TRIGGER = "zipDownload"
ZIP_DOWNLOAD_STORED_TRIGGER = "Zip-Download"

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
MSG_CANCELLED_FROM_EXECUTIONS = "Cancelled from workflow executions"
MSG_NOT_READY = "The archive is not ready for download"
MSG_EXPIRED = "This zip download has expired"

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


def expires_at_epoch(created: datetime, retention_days: int) -> int:
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
                 now: Optional[datetime] = None, expected_execution_id: str = "") -> bool:
    """Move a non-terminal row to a terminal ``status``. True when this call made the transition;
    False when the row is missing or already terminal (the first terminal write wins).

    With ``expected_execution_id`` the write also requires the row to belong to that run, so a
    callback for an older run never finalizes a row relaunched since."""
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
    - SUCCEEDED without the archive: FAILED (not produced).
    - ABORTED: FAILED "Cancelled from workflow executions", cancelledBy execution.
    - FAILED / TIMED_OUT: FAILED with the container's own reason when it gave one (on the row or as
      a prefixed execution error), else a fixed message. Raw execution errors are never shown: they
      can carry internal resource names.
    """
    if not row or row.get("status") not in ACTIVE_STATUSES:
        return None
    if execution_status not in RUN_TERMINAL_STATUSES:
        return None
    exists, size = (archive_exists() if archive_exists else (False, None))
    if exists:
        return STATUS_SUCCEEDED, {"archive_size_bytes": size}
    if execution_status == "SUCCEEDED":
        return STATUS_FAILED, {"error_message": MSG_NOT_PRODUCED}
    if execution_status == "ABORTED":
        return STATUS_FAILED, {"error_message": MSG_CANCELLED_FROM_EXECUTIONS,
                               "cancelled_by": CANCELLED_BY_EXECUTION}
    if execution_status in ("FAILED", "TIMED_OUT"):
        reason = (row.get("errorMessage") or "").strip() or container_reason(execution_error)
        if not reason:
            reason = MSG_JOB_TIMED_OUT if execution_status == "TIMED_OUT" else MSG_JOB_FAILED
        return STATUS_FAILED, {"error_message": reason}
    return None


def _abort_then_reread(table, s3_client, bucket: str, row: dict, logger=None) -> Optional[dict]:
    """Abort the job's open uploads, then read the row again (consistent): the decision must see
    the archiveUploadToken the container may have recorded after the caller read the row."""
    abort_open_uploads(s3_client, bucket, request_prefix(row["databaseId"], row["assetId"],
                                                         row["userId"], row["requestId"]), logger)
    return table.get_item(Key=row_key(row["userId"], row["requestId"]),
                          ConsistentRead=True).get("Item")


def finalize_ended_run(table, s3_client, bucket: str, row: dict, execution_status: str,
                       execution_error: str = "", logger=None) -> Tuple[Optional[str], bool]:
    """Finalize a non-terminal job whose workflow run ended: the terminal callback and the API's
    read-time reconcile both come here. Returns ``(status, changed)``; ``(None, False)`` when there
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
    changed = finalize_row(table, row["userId"], row["requestId"], status,
                           expected_execution_id=row.get("workflowExecutionId") or "", **fields)
    if changed and status != STATUS_SUCCEEDED:
        delete_working_objects(s3_client, bucket, row, logger)
    return status, changed


CANCEL_CANCELLED = "cancelled"
CANCEL_LANDED = "landed"
CANCEL_ENDED = "ended"


def cancel_job(table, s3_client, bucket: str, row: dict, cancelled_by: str, logger=None) -> str:
    """The user's cancel of a non-terminal job. Same rule as finalize_ended_run: abort the open
    uploads, then a landed archive wins, decided by the job's upload token whatever the abort answered.

    Returns CANCEL_CANCELLED when this call cancelled the job, CANCEL_LANDED when the archive had
    landed (the job is SUCCEEDED, recorded here or by the container), or CANCEL_ENDED when another
    writer finalized the job first."""
    row = _abort_then_reread(table, s3_client, bucket, row, logger)
    if not row or row.get("status") not in ACTIVE_STATUSES:
        return CANCEL_ENDED
    exists, size = landed_archive(s3_client, bucket, row)
    if exists:
        finalize_row(table, row["userId"], row["requestId"], STATUS_SUCCEEDED, archive_size_bytes=size,
                     expected_execution_id=row.get("workflowExecutionId") or "")
        return CANCEL_LANDED
    if finalize_row(table, row["userId"], row["requestId"], STATUS_CANCELLED, cancelled_by=cancelled_by):
        # A resolved manifest the container writes after this is removed by the terminal callback of
        # the aborted run (zipDownloadCallback), which deletes them again for an ended job.
        delete_working_objects(s3_client, bucket, row, logger)
        return CANCEL_CANCELLED
    return CANCEL_ENDED


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


def abort_open_uploads(s3_client, bucket: str, prefix: str, logger=None) -> int:
    """Abort every incomplete multipart upload under one job's prefix. Returns the count aborted.

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
                    s3_client.abort_multipart_upload(Bucket=bucket, Key=key,
                                                     UploadId=upload.get("UploadId"))
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
    try:
        head = s3_client.head_object(Bucket=bucket, Key=row.get("archiveKey", ""))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False, None
        raise
    if not is_own_upload_object(head, token):
        return False, None
    return True, int(head.get("ContentLength", 0))


def archive_head(s3_client, bucket: str, key: str) -> Tuple[bool, Optional[int]]:
    """(exists, size) of a job's archive object."""
    try:
        head = s3_client.head_object(Bucket=bucket, Key=key)
        return True, int(head.get("ContentLength", 0))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False, None
        raise
