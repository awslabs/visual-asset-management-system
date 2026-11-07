# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Status writes to the zip download job row (mode 1 only).

Every write is conditional on the row still being QUEUED or RUNNING and belonging to this run
(``workflowExecutionId`` equal to this run's, or not yet recorded), the same terminal-is-final rule the
API and the terminal callback use (backend ``common/zipDownloads.py``). A write that loses (the user
cancelled, or another writer finalized the job) is ignored: the container then stops quietly.
A landed archive wins: the runner never writes FAILED once its upload completed, and deletes an
archive that landed after another writer finalized the job without it (runner.Job).
"""

import time
from datetime import datetime, timezone

from botocore.exceptions import ClientError

ACTIVE_CONDITION = ("attribute_exists(requestId) AND #status IN (:queued, :running) AND "
                    "(attribute_not_exists(workflowExecutionId) OR workflowExecutionId = :eid)")
MAX_REASON_CHARS = 1024


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StatusWriter:
    def __init__(self, table, user_id, request_id, execution_id, min_interval_seconds=5.0,
                 clock=time.monotonic):
        self._table = table
        self._key = {"userId": user_id, "requestId": request_id}
        self._execution_id = execution_id or ""
        self._interval = min_interval_seconds
        self._clock = clock
        self._last_progress = None
        self.lost = False

    def _update(self, sets, values):
        values = dict(values)
        values.update({":queued": "QUEUED", ":running": "RUNNING", ":eid": self._execution_id,
                       ":updatedAt": _now()})
        sets = list(sets) + ["updatedAt = :updatedAt"]
        try:
            self._table.update_item(
                Key=self._key, UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression=ACTIVE_CONDITION,
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=values)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                self.lost = True
                return False
            raise

    def running(self, files_total, bytes_total, resolved_manifest_key, upload_token):
        """Move the job to RUNNING and record the token the archive upload will carry, BEFORE the
        upload exists, so a finalizer can tell this job's archive from any other object at the key."""
        return self._update(
            ["#status = :s", "filesTotal = :ft", "bytesTotal = :bt", "filesDone = :z", "bytesDone = :z",
             "resolvedManifestKey = :rk", "archiveUploadToken = :tok"],
            {":s": "RUNNING", ":ft": int(files_total), ":bt": int(bytes_total), ":z": 0,
             ":rk": resolved_manifest_key, ":tok": upload_token})

    def record_upload(self, upload_id):
        """Record the archive's multipart upload id on the job row (``archiveUploadIds``, a string
        set), right after the upload is created and before any part, so a stopper's delayed sweep
        can abort exactly this upload (#1125 D16; backend common/zipDownloads.sweep_recorded_uploads).
        Written whatever the job's status (a stop that came first still needs the id), only for this
        run's row. Raises when it cannot be written: the sink then aborts the upload."""
        self._table.update_item(
            Key=self._key, UpdateExpression="ADD archiveUploadIds :ids",
            ConditionExpression=("attribute_exists(requestId) AND "
                                 "(attribute_not_exists(workflowExecutionId) OR "
                                 "workflowExecutionId = :eid)"),
            ExpressionAttributeValues={":ids": {upload_id}, ":eid": self._execution_id})

    def progress(self, files_done, bytes_done, force=False):
        now = self._clock()
        if not force and self._last_progress is not None and now - self._last_progress < self._interval:
            return False
        self._last_progress = now
        return self._update(["filesDone = :fd", "bytesDone = :bd"],
                            {":fd": int(files_done), ":bd": int(bytes_done)})

    def succeeded(self, archive_size_bytes, files_done, bytes_done):
        return self._update(
            ["#status = :s", "archiveSizeBytes = :size", "filesDone = :fd", "bytesDone = :bd",
             "completedAt = :c"],
            {":s": "SUCCEEDED", ":size": int(archive_size_bytes), ":fd": int(files_done),
             ":bd": int(bytes_done), ":c": _now()})

    def read(self):
        """The job row as it is now (consistent read), or None when it is gone."""
        return self._table.get_item(Key=self._key, ConsistentRead=True).get("Item")

    def must_stop(self):
        """Whether the archive must upload no further part, read before each part (#1123 D5 rule):
        the row is gone, no longer QUEUED or RUNNING, owned by another run, or carries a stopper's
        ``stopRequestedAt`` (backend common/zipDownloads.request_stop, written before its abort).
        Raises on a read error (the sink then keeps going and logs it)."""
        row = self.read()
        if not row or row.get("status") not in ("QUEUED", "RUNNING"):
            return True
        if row.get("workflowExecutionId") and row["workflowExecutionId"] != self._execution_id:
            return True
        return bool(row.get("stopRequestedAt"))

    def failed(self, reason):
        return self._update(["#status = :s", "errorMessage = :e", "completedAt = :c"],
                            {":s": "FAILED", ":e": (reason or "The archive job failed")[:MAX_REASON_CHARS],
                             ":c": _now()})
