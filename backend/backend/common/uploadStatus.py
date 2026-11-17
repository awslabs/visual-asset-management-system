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

Import has no AWS or environment side effect: the table is passed in. Writers never raise unless
asked to (``raise_errors``): the copy engine and the queue consumer ask, so a write that failed is
retried by their own retry (a Step Functions retry, a redelivered message) instead of leaving the
file PENDING until the record expires.
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


def record_async_file_outcome(table, upload_id, asset_id, relative_key, succeeded, reason="",
                              raise_errors=False, over_failed=False):
    """Record one asynchronously ingested file's outcome on its upload record and, when no file is
    pending any more, the upload's final status. A record that does not track the file (an upload
    completed before this was recorded, or one whose record expired) is left alone. Returns True when
    the file's outcome was written.

    Idempotent and monotonic: a file's entry moves from PENDING to one outcome only, so repeating the
    write (a retried task, a redelivered message) writes the same outcome again and settles the
    upload if it is not settled yet, and a different outcome never overwrites a recorded one.
    ``raise_errors`` re-raises a write that failed for any other reason than the record not tracking
    the file, so the caller's retry records it (otherwise logged and False).

    ``over_failed`` (with ``succeeded``) is the one exception: the copy engine's job was superseded
    SUCCEEDED because its file landed after a remover recorded it failed (the landed object wins),
    so a recorded FAILED is overwritten and an upload settled with errors is settled again."""
    if not (upload_id and asset_id and relative_key):
        return False
    status = FILE_SUCCEEDED if succeeded else FILE_FAILED
    over_failed = bool(over_failed and succeeded)
    entry = {"status": status, "updatedAt": _iso()}
    if not succeeded:
        entry["error"] = reason or REASON_PROCESSING_FAILED
    try:
        response = table.update_item(
            Key={"uploadId": upload_id, "assetId": asset_id},
            UpdateExpression="SET asyncFiles.#rel = :entry, updatedAt = :u",
            ConditionExpression=("attribute_exists(asyncFiles.#rel) AND asyncFiles.#rel.#st IN (:p, :s"
                                 + (", :f)" if over_failed else ")")),
            ExpressionAttributeNames={"#rel": relative_key, "#st": "status"},
            ExpressionAttributeValues={":entry": entry, ":u": entry["updatedAt"],
                                       ":p": FILE_PENDING, ":s": status,
                                       **({":f": FILE_FAILED} if over_failed else {})},
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            logger.info(f"Upload {upload_id} does not track {relative_key} or already recorded "
                        f"another outcome for it; no status recorded")
            return False
        logger.warning(f"Could not record the outcome of {relative_key} on upload {upload_id}: {e}")
        if raise_errors:
            raise
        return False
    except Exception as e:
        logger.warning(f"Could not record the outcome of {relative_key} on upload {upload_id}: {e}")
        if raise_errors:
            raise
        return False
    _settle(table, response.get("Attributes") or {}, resettle=over_failed)
    return True


def claim_async_file_audit(table, upload_id, asset_id, relative_key):
    """Claim the right to write the completion audit entry of a tracked file whose outcome is still
    PENDING (stamped on its entry; not part of the API view). True for exactly one receive of the
    file's message, so a redelivery that finds the file stored writes the entry only if no earlier
    receive did. False when an earlier receive claimed it or the outcome is recorded; None when the
    record does not track the file. Raises when the record cannot be written."""
    if not (upload_id and asset_id and relative_key):
        return None
    try:
        table.update_item(
            Key={"uploadId": upload_id, "assetId": asset_id},
            UpdateExpression="SET asyncFiles.#rel.#ac = :n",
            ConditionExpression=("attribute_exists(asyncFiles.#rel) AND asyncFiles.#rel.#st = :p AND "
                                 "attribute_not_exists(asyncFiles.#rel.#ac)"),
            ExpressionAttributeNames={"#rel": relative_key, "#st": "status", "#ac": "auditClaimedAt"},
            ExpressionAttributeValues={":n": _iso(), ":p": FILE_PENDING},
        )
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
    return False if async_file_status(table, upload_id, asset_id, relative_key) else None


def release_async_file_audit(table, upload_id, asset_id, relative_key):
    """Undo a claim_async_file_audit whose audit entry could not be written, so a later receive can
    write it. Only while the file's outcome is still PENDING. Best-effort (logged, never raises)."""
    if not (upload_id and asset_id and relative_key):
        return
    try:
        table.update_item(
            Key={"uploadId": upload_id, "assetId": asset_id},
            UpdateExpression="REMOVE asyncFiles.#rel.#ac",
            ConditionExpression="attribute_exists(asyncFiles.#rel) AND asyncFiles.#rel.#st = :p",
            ExpressionAttributeNames={"#rel": relative_key, "#st": "status", "#ac": "auditClaimedAt"},
            ExpressionAttributeValues={":p": FILE_PENDING},
        )
    except Exception as e:
        logger.warning(f"Could not release the audit claim of {relative_key} on upload {upload_id}: "
                       f"{e.__class__.__name__}")


def async_file_status(table, upload_id, asset_id, relative_key):
    """The recorded status of one asynchronously ingested file (PENDING, SUCCEEDED or FAILED), or
    None when the upload record does not track it. Raises when the record cannot be read."""
    if not (upload_id and asset_id and relative_key):
        return None
    record = table.get_item(Key={"uploadId": upload_id, "assetId": asset_id},
                            ConsistentRead=True).get("Item") or {}
    entry = (record.get("asyncFiles") or {}).get(relative_key)
    return (entry or {}).get("status") if isinstance(entry, dict) else None


def _settle(table, record, resettle=False):
    """Set the final status once no tracked file is pending (conditioned on the upload still
    processing, so concurrent last writers settle it once). ``resettle`` also moves an upload
    settled with errors, for a file whose recorded failure was superseded by its landing."""
    files = record.get("asyncFiles") or {}
    if not files or any((f or {}).get("status") == FILE_PENDING for f in files.values()):
        return
    failed = record.get("hasSyncFailures") or any(
        (f or {}).get("status") == FILE_FAILED for f in files.values())
    try:
        table.update_item(
            Key={"uploadId": record["uploadId"], "assetId": record["assetId"]},
            UpdateExpression="SET #status = :s",
            ConditionExpression="#status IN (:p, :w)" if resettle else "#status = :p",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":s": STATUS_COMPLETED_WITH_ERRORS if failed else STATUS_COMPLETED,
                ":p": STATUS_PROCESSING_ASYNC,
                **({":w": STATUS_COMPLETED_WITH_ERRORS} if resettle else {})},
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
