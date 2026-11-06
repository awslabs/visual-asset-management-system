# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Outcome of the files of an upload that are ingested asynchronously.

A file over the asynchronous threshold (1 GiB) is accepted at completion and copied into its asset
later: by the large-file queue consumer, or for an API upload over the copy engine threshold by the
large object copy engine. The upload record (AssetUploads table, key uploadId + assetId) is kept for
such an upload with status ``processing_async`` and one ``asyncFiles`` entry per file, and each
writer of a file's outcome records it here. Once no file is pending the record's status becomes
``completed`` or ``completed_with_errors``. ``GET /uploads/{uploadId}`` returns it, and the web app,
``vamscli file upload --wait-async`` and ``vamscli file upload-status`` show it.

Import has no AWS or environment side effect: the table is passed in. Every writer is best-effort
and never raises, since the file's own outcome must not fail because its status could not be
recorded.
"""

from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError

from customLogging.logger import safeLogger

logger = safeLogger()

STATUS_PROCESSING_ASYNC = "processing_async"
STATUS_COMPLETED = "completed"
STATUS_COMPLETED_WITH_ERRORS = "completed_with_errors"

FILE_PENDING = "PENDING"
FILE_SUCCEEDED = "SUCCEEDED"
FILE_FAILED = "FAILED"
FILE_STATUSES = (FILE_PENDING, FILE_SUCCEEDED, FILE_FAILED)

# Days an upload record that tracks asynchronous files is kept (the table's TTL removes it).
ASYNC_RECORD_RETENTION_DAYS = 7

# Generic reason recorded when a queued file's last delivery attempt failed.
REASON_PROCESSING_FAILED = "The file could not be processed into the asset."


def _now():
    return datetime.now(timezone.utc)


def _iso(moment=None):
    return (moment or _now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def expires_at(now=None):
    return int(((now or _now()) + timedelta(days=ASYNC_RECORD_RETENTION_DAYS)).timestamp())


def async_relative_keys(file_results):
    """Relative keys of the completion results the upload path accepted for asynchronous ingestion:
    queued for the large-file consumer (no copy job of a workflow ingestion batch, which reports
    through its workflow execution instead)."""
    keys = []
    for result in file_results or []:
        if (getattr(result, "success", False) and getattr(result, "largeFileAsynchronousHandling", False)
                and not getattr(result, "ingestionJobId", None)):
            keys.append(result.relativeKey)
    return keys


def begin_async_tracking(table, upload_id, asset_id, relative_keys, has_sync_failures):
    """Keep the upload record and track each file accepted for asynchronous ingestion as PENDING.
    Returns True when written."""
    now = _now()
    try:
        table.update_item(
            Key={"uploadId": upload_id, "assetId": asset_id},
            UpdateExpression=("SET #status = :s, asyncFiles = :f, hasSyncFailures = :h, "
                              "expiresAt = :e, updatedAt = :u"),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":s": STATUS_PROCESSING_ASYNC,
                ":f": {key: {"status": FILE_PENDING, "updatedAt": _iso(now)} for key in relative_keys},
                ":h": bool(has_sync_failures),
                ":e": expires_at(now),
                ":u": _iso(now),
            },
            ConditionExpression="attribute_exists(uploadId)",
        )
        return True
    except Exception as e:
        logger.warning(f"Could not record the asynchronous files of upload {upload_id}: {e}")
        return False


def record_async_file_outcome(table, upload_id, asset_id, relative_key, succeeded, reason=""):
    """Record one asynchronously ingested file's outcome on its upload record and, when no file is
    pending any more, the upload's final status. A record that does not track the file (an upload
    completed before this was recorded, or one whose record expired) is left alone. Returns True when
    the file's outcome was written."""
    if not (upload_id and asset_id and relative_key):
        return False
    status = FILE_SUCCEEDED if succeeded else FILE_FAILED
    entry = {"status": status, "updatedAt": _iso()}
    if not succeeded:
        entry["error"] = reason or REASON_PROCESSING_FAILED
    try:
        response = table.update_item(
            Key={"uploadId": upload_id, "assetId": asset_id},
            UpdateExpression="SET asyncFiles.#rel = :entry, updatedAt = :u",
            ConditionExpression="attribute_exists(asyncFiles.#rel)",
            ExpressionAttributeNames={"#rel": relative_key},
            ExpressionAttributeValues={":entry": entry, ":u": entry["updatedAt"]},
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info(f"Upload {upload_id} does not track {relative_key}; no status recorded")
        else:
            logger.warning(f"Could not record the outcome of {relative_key} on upload {upload_id}: {e}")
        return False
    except Exception as e:
        logger.warning(f"Could not record the outcome of {relative_key} on upload {upload_id}: {e}")
        return False
    _settle(table, response.get("Attributes") or {})
    return True


def _settle(table, record):
    """Set the final status once no tracked file is pending (conditioned on the upload still
    processing, so concurrent last writers settle it once)."""
    files = record.get("asyncFiles") or {}
    if not files or any((f or {}).get("status") == FILE_PENDING for f in files.values()):
        return
    failed = record.get("hasSyncFailures") or any(
        (f or {}).get("status") == FILE_FAILED for f in files.values())
    try:
        table.update_item(
            Key={"uploadId": record["uploadId"], "assetId": record["assetId"]},
            UpdateExpression="SET #status = :s",
            ConditionExpression="#status = :p",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":s": STATUS_COMPLETED_WITH_ERRORS if failed else STATUS_COMPLETED,
                ":p": STATUS_PROCESSING_ASYNC},
        )
    except Exception as e:
        logger.info(f"Upload {record.get('uploadId')} final status not written ({e.__class__.__name__})")


def status_view(record):
    """The API view of an upload record: {uploadId, databaseId, assetId, status, asyncFiles}."""
    files = []
    for key in sorted((record.get("asyncFiles") or {}).keys()):
        entry = record["asyncFiles"][key] or {}
        files.append({
            "relativeKey": key,
            "status": entry.get("status") if entry.get("status") in FILE_STATUSES else FILE_PENDING,
            "error": entry.get("error", "") if entry.get("status") == FILE_FAILED else "",
            "updatedAt": entry.get("updatedAt", ""),
        })
    return {
        "uploadId": record.get("uploadId", ""),
        "databaseId": record.get("databaseId", ""),
        "assetId": record.get("assetId", ""),
        "status": record.get("status", ""),
        "asyncFiles": files,
    }
