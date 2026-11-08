# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The zip job finalizers decide by the job's own archive whatever the abort answered (#1123 D1).

S3 answers 204 (not NoSuchUpload) to an AbortMultipartUpload sent while a CompleteMultipartUpload is
in flight or after it succeeded, and the completed object stays at the key (the live probe behind
#1123 D1). moto answers NoSuchUpload, and the zip harness's S3 double removed the upload on abort, so
no earlier zip test sent an abort that S3 answered 204 while the archive landed.

Every API-side finalizer of a zip job aborts the job's open uploads first and then decides: the
terminal callback, the read-time reconcile (job GET), and the user's cancel (DELETE). Each is driven
here through its REAL handler with an S3 double that answers the abort as S3 does: 204 and the upload
keeps completing, or NoSuchUpload, or an error. The outcome is the job's own landed archive (its
upload token; P2b's shared helper common.s3MetadataKeys.is_own_upload_object), never the abort's
answer, and never presence or size."""

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

REQUEST_ID = "0mw4e6byk-" + "d" * 32
EXECUTION_STATUSES = ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"]


class S3AbortAnswers(h.FakeS3):
    """The zip harness's S3 double, with AbortMultipartUpload answered as S3 does.

    answer="204": the upload is being (or was) completed: S3 answers 204 and does not stop it, so the
    upload is not removed by the abort and the object, once the Complete landed it, stays.
    answer=<error code>: the abort raises that error (NoSuchUpload is what moto answers)."""

    def __init__(self, answer="204"):
        super().__init__()
        self.answer = answer

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.aborted.append((Bucket, Key, UploadId))
        if self.answer == "204":
            return {"ResponseMetadata": {"HTTPStatusCode": 204}}
        raise h._client_error(self.answer, "AbortMultipartUpload")


def _env(answer="204"):
    h.AUTH.__init__()
    env = h.load_handlers()
    env.s3 = S3AbortAnswers(answer)
    for module in (env.svc, env.cb):
        module.s3 = env.s3
    h.put_asset(env)
    prefix = env.zd.request_prefix("db1", "asset1", "alice", REQUEST_ID)
    env.prefix = prefix
    env.zip.items[("alice", REQUEST_ID)] = {
        "userId": "alice", "requestId": REQUEST_ID, "databaseId": "db1", "assetId": "asset1",
        "userAssetKey": "alice#db1#asset1", "status": "RUNNING", "workflowExecutionId": "exec-1",
        "archiveKey": prefix + "x.zip", "manifestKey": prefix + "manifest.json",
        "createdAt": "2026-11-06T00:00:00Z", "updatedAt": "2026-11-06T00:00:00Z",
        "expiresAt": 4102444800}
    # The container's archive upload, listed while its Complete is in flight.
    env.s3.uploads.append({"Bucket": h.AUX, "Key": prefix + "x.zip", "UploadId": "u1"})
    return env


def _row(env):
    return env.zip.items[("alice", REQUEST_ID)]


def _landed(env, size=42):
    """The Complete of this job's own upload landed the archive (it carries the row's token)."""
    h.land_archive(env, _row(env), size=size)


def _foreign_object(env, size=42):
    """An earlier object of the same size at the key: another upload's token, not this job's."""
    h.record_upload_token(_row(env))
    env.s3.add(_row(env)["archiveKey"], size=size, bucket=h.AUX, version="old",
               metadata={env.zd.ARCHIVE_UPLOAD_TOKEN_METADATA: "token-of-an-earlier-run"})


def _payload(status):
    return {"callbackType": "systemPipelineTerminal", "schemaVersion": 1,
            "idempotencyKey": f"exec-1:{status}", "workflowExecutionId": "exec-1",
            "workflowDatabaseId": "GLOBAL", "workflowId": "utility-zip-archive",
            "triggerType": "Zip-Download", "executionStatus": status, "executionError": "",
            "writer": "abort",
            "systemExecutionContext": {"v": 1, "requestId": REQUEST_ID, "userId": "alice",
                                       "databaseId": "db1", "assetId": "asset1", "manifestKey": "m"}}


def _abort_sent(env):
    return (h.AUX, env.prefix + "x.zip", "u1") in env.s3.aborted


def _main_row(env, status):
    env.executions.items[("exec-1", "GLOBAL:utility-zip-archive")] = {
        "workflowExecutionId": "exec-1", "workflowDatabaseId:workflowId": "GLOBAL:utility-zip-archive",
        "executionStatus": status, "executionError": ""}


def _job_route(env, method):
    h.AUTH.user = "alice"
    path = h.asset_path("db1", "asset1", f"/{REQUEST_ID}")
    return env.svc.lambda_handler(h.api_event(method, path, {
        "databaseId": "db1", "assetId": "asset1", "requestId": REQUEST_ID}), None)


# ------------------------------------------------------------------------------------------------ #
# The terminal callback
# ------------------------------------------------------------------------------------------------ #

@pytest.mark.parametrize("status", EXECUTION_STATUSES)
class TestCallbackAfterA204Abort:
    def test_a_204_is_not_read_as_nothing_landed(self, status):
        env = _env("204")
        _landed(env, size=77)
        assert env.cb.lambda_handler(_payload(status), None)["status"] == "finalized"
        assert _abort_sent(env), "the abort was sent and answered 204"
        assert _row(env)["status"] == "SUCCEEDED"
        assert _row(env)["archiveSizeBytes"] == 77

    def test_nothing_landed_yet_the_job_ends_without_the_archive(self, status):
        # A Complete still in flight after the 204 lands later; its container then loses the
        # SUCCEEDED write and deletes it (container tests/test_abort_204.py).
        env = _env("204")
        env.cb.lambda_handler(_payload(status), None)
        assert _abort_sent(env)
        assert _row(env)["status"] == "FAILED"

    def test_an_earlier_object_of_the_same_size_is_not_this_jobs_archive(self, status):
        env = _env("204")
        _foreign_object(env)
        env.cb.lambda_handler(_payload(status), None)
        assert _row(env)["status"] == "FAILED"


@pytest.mark.parametrize("answer", ["NoSuchUpload", "AccessDenied", "InternalError"])
class TestCallbackWhateverTheAbortRaised:
    def test_the_landed_archive_still_wins(self, answer):
        env = _env(answer)
        _landed(env, size=5)
        env.cb.lambda_handler(_payload("ABORTED"), None)
        assert _abort_sent(env)
        assert _row(env)["status"] == "SUCCEEDED" and _row(env)["archiveSizeBytes"] == 5

    def test_no_landed_archive_still_fails_the_job(self, answer):
        env = _env(answer)
        env.cb.lambda_handler(_payload("ABORTED"), None)
        assert _row(env)["status"] == "FAILED"
        assert _row(env)["errorMessage"] == "Cancelled from workflow executions"


# ------------------------------------------------------------------------------------------------ #
# The read-time reconcile and the user's cancel
# ------------------------------------------------------------------------------------------------ #

@pytest.mark.parametrize("status", EXECUTION_STATUSES)
def test_the_read_time_reconcile_decides_by_the_landed_archive_after_a_204(status):
    env = _env("204")
    _landed(env, size=9)
    _main_row(env, status)
    item = h.body_of(_job_route(env, "GET"))
    assert _abort_sent(env)
    assert item["status"] == "SUCCEEDED" and item["archiveSizeBytes"] == 9


class TestUserCancelAfterA204Abort:
    def test_cancel_job_keeps_a_landed_archive(self):
        env = _env("204")
        _landed(env, size=11)
        assert env.zd.cancel_job(env.zip, env.s3, h.AUX, dict(_row(env)),
                                 env.zd.CANCELLED_BY_USER) == env.zd.CANCEL_LANDED
        assert _abort_sent(env)
        assert _row(env)["status"] == "SUCCEEDED" and _row(env)["archiveSizeBytes"] == 11

    def test_cancel_job_cancels_when_nothing_landed(self):
        env = _env("204")
        assert env.zd.cancel_job(env.zip, env.s3, h.AUX, dict(_row(env)),
                                 env.zd.CANCELLED_BY_USER) == env.zd.CANCEL_CANCELLED
        assert _row(env)["status"] == "CANCELLED"

    def test_the_delete_route_never_writes_cancelled_over_a_landed_archive(self):
        # The archive landed before the cancel: the job is finished (SUCCEEDED). The cancel records
        # no cancel over it and deletes nothing (#1125 D22): the job stays SUCCEEDED and the answer
        # says it had already ended.
        env = _env("204")
        _landed(env)
        written, update = [], env.zip.update_item

        def recording(**kwargs):
            written.extend(v for v in (kwargs.get("ExpressionAttributeValues") or {}).values()
                           if v in ("SUCCEEDED", "FAILED", "CANCELLED"))
            return update(**kwargs)
        env.zip.update_item = recording
        response = _job_route(env, "DELETE")
        assert response["statusCode"] == 200
        assert h.body_of(response)["action"] == "ended"
        assert h.body_of(response)["status"] == "SUCCEEDED"
        assert _abort_sent(env)
        assert "CANCELLED" not in written and "SUCCEEDED" in written
        assert _row(env)["status"] == "SUCCEEDED"
