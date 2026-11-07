# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Terminal callback of zip download runs: each terminal status, idempotent replays, payloads that
do not belong to the job, and multipart cleanup on every non-success."""

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

REQUEST_ID = "0mw4e6byk-" + "a" * 32


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    prefix = e.zd.request_prefix("db1", "asset1", "alice", REQUEST_ID)
    e.zip.items[("alice", REQUEST_ID)] = {
        "userId": "alice", "requestId": REQUEST_ID, "databaseId": "db1", "assetId": "asset1",
        "status": "RUNNING", "workflowExecutionId": "exec-1", "archiveKey": prefix + "x.zip",
        "archiveUploadToken": h.UPLOAD_TOKEN,
        "manifestKey": prefix + "manifest.json"}
    e.s3.uploads.append({"Bucket": h.AUX, "Key": prefix + "x.zip", "UploadId": "u1"})
    e.prefix = prefix
    return e


def _payload(status, error="", trigger="Zip-Download", execution_id="exec-1", **context):
    ctx = {"v": 1, "requestId": REQUEST_ID, "userId": "alice", "databaseId": "db1",
           "assetId": "asset1", "manifestKey": "m"}
    ctx.update(context)
    return {"callbackType": "systemPipelineTerminal", "schemaVersion": 1,
            "idempotencyKey": f"{execution_id}:{status}", "workflowExecutionId": execution_id,
            "triggerType": trigger, "executionStatus": status, "executionError": error,
            "writer": "abort", "systemExecutionContext": ctx}


def _row(env):
    return env.zip.items[("alice", REQUEST_ID)]


class TestCallback:
    def test_aborted_fails_cancelled_from_executions(self, env):
        result = env.cb.lambda_handler(_payload("ABORTED"), None)
        assert result["status"] == "finalized"
        assert _row(env)["status"] == "FAILED"
        assert _row(env)["errorMessage"] == "Cancelled from workflow executions"
        assert _row(env)["cancelledBy"] == "execution"
        assert env.s3.aborted == [(h.AUX, env.prefix + "x.zip", "u1")]

    @pytest.mark.parametrize("status", ["ABORTED", "FAILED", "TIMED_OUT", "SUCCEEDED"])
    def test_a_job_ended_without_archive_keeps_no_working_objects(self, env, status):
        """#1125 D13: an abort from the executions views (or any end without an archive) deletes the
        request and resolved manifests at once."""
        manifest_key, resolved_key = env.zd.working_object_keys(_row(env))
        env.s3.add(manifest_key, bucket=h.AUX)
        env.s3.add(resolved_key, bucket=h.AUX)
        env.cb.lambda_handler(_payload(status), None)
        assert _row(env)["status"] == "FAILED"
        assert (h.AUX, manifest_key) not in env.s3.objects
        assert (h.AUX, resolved_key) not in env.s3.objects

    @pytest.mark.parametrize("ended", ["FAILED", "CANCELLED"])
    def test_an_already_ended_job_loses_working_objects_written_after_it_ended(self, env, ended):
        # The container wrote its resolved manifest after the user's cancel (or wrote FAILED itself).
        _row(env)["status"] = ended
        manifest_key, resolved_key = env.zd.working_object_keys(_row(env))
        env.s3.add(resolved_key, bucket=h.AUX)
        assert env.cb.lambda_handler(_payload("ABORTED"), None)["status"] == "unchanged"
        assert (h.AUX, resolved_key) not in env.s3.objects

    def test_a_succeeded_job_keeps_its_objects(self, env):
        h.land_archive(env, _row(env), size=77)
        manifest_key, resolved_key = env.zd.working_object_keys(_row(env))
        env.s3.add(resolved_key, bucket=h.AUX)
        env.cb.lambda_handler(_payload("SUCCEEDED"), None)
        assert _row(env)["status"] == "SUCCEEDED"
        assert (h.AUX, resolved_key) in env.s3.objects

    def test_replay_is_a_no_op(self, env):
        env.cb.lambda_handler(_payload("ABORTED"), None)
        before = dict(_row(env))
        assert env.cb.lambda_handler(_payload("ABORTED"), None)["status"] == "unchanged"
        assert _row(env) == before

    def test_timed_out(self, env):
        env.cb.lambda_handler(_payload("TIMED_OUT"), None)
        assert _row(env)["errorMessage"] == "The archive job timed out"

    def test_failed_keeps_container_reason(self, env):
        _row(env)["errorMessage"] = "/a.exe has a blocked file type"
        env.cb.lambda_handler(_payload("FAILED", "States.TaskFailed"), None)
        assert _row(env)["errorMessage"] == "/a.exe has a blocked file type"

    def test_succeeded_without_archive_fails(self, env):
        env.cb.lambda_handler(_payload("SUCCEEDED"), None)
        assert _row(env)["status"] == "FAILED"

    def test_succeeded_with_archive(self, env):
        h.land_archive(env, _row(env), size=77)
        env.cb.lambda_handler(_payload("SUCCEEDED"), None)
        assert _row(env)["status"] == "SUCCEEDED" and _row(env)["archiveSizeBytes"] == 77

    @pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT", "ABORTED"])
    def test_landed_archive_wins_over_a_failed_run(self, env, status):
        # The container completed its upload but its SUCCEEDED write had not landed when the run
        # was recorded as ended: one outcome, SUCCEEDED, never FAILED.
        h.land_archive(env, _row(env), size=77)
        result = env.cb.lambda_handler(_payload(status), None)
        assert result == {"status": "finalized", "jobStatus": "SUCCEEDED"}
        assert _row(env)["status"] == "SUCCEEDED" and _row(env)["archiveSizeBytes"] == 77
        assert "errorMessage" not in _row(env)
        assert env.cb.lambda_handler(_payload(status), None)["status"] == "unchanged"

    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"])
    def test_earlier_same_size_object_is_not_this_jobs_archive(self, env, status):
        # An object of the archive's size sits at the key, but not from this job's upload (no
        # token, or another upload's token): it never counts as landed.
        env.s3.add(env.prefix + "x.zip", size=77, bucket=h.AUX, version="old")
        env.s3.add(env.prefix + "x.zip", size=77, bucket=h.AUX, version="other",
                   metadata={env.zd.ARCHIVE_UPLOAD_TOKEN_METADATA: "tok-another-upload"})
        assert env.cb.lambda_handler(_payload(status), None)["jobStatus"] == "FAILED"
        assert _row(env)["status"] == "FAILED" and "archiveSizeBytes" not in _row(env)

    def test_row_without_a_token_has_no_landed_archive(self, env):
        del _row(env)["archiveUploadToken"]
        env.s3.add(env.prefix + "x.zip", size=77, bucket=h.AUX)
        env.cb.lambda_handler(_payload("SUCCEEDED"), None)
        assert _row(env)["status"] == "FAILED"

    def test_open_upload_is_aborted_before_the_archive_check(self, env):
        order = []
        abort, head = env.s3.abort_multipart_upload, env.s3.head_object
        env.s3.abort_multipart_upload = lambda **kw: (order.append("abort"), abort(**kw))[1]
        env.s3.head_object = lambda **kw: (order.append("head"), head(**kw))[1]
        env.cb.lambda_handler(_payload("FAILED"), None)
        assert order[:2] == ["abort", "head"]
        assert _row(env)["status"] == "FAILED"

    def test_user_cancel_wins_over_callback(self, env):
        _row(env)["status"] = "CANCELLED"
        assert env.cb.lambda_handler(_payload("ABORTED"), None)["status"] == "unchanged"
        assert _row(env)["status"] == "CANCELLED"

    @pytest.mark.parametrize("payload", [
        _payload("ABORTED", trigger="Manual"),
        _payload("ABORTED", execution_id="exec-other"),
        _payload("ABORTED", databaseId="db-other"),
        _payload("ABORTED", requestId="not-an-id"),
        dict(_payload("ABORTED"), callbackType="other"),
        dict(_payload("ABORTED"), schemaVersion=2),
        "not a dict",
    ])
    def test_foreign_payloads_ignored(self, env, payload):
        assert env.cb.lambda_handler(payload, None)["status"] == "ignored"
        assert _row(env)["status"] == "RUNNING"

    def test_missing_row_ignored(self, env):
        env.zip.items.clear()
        assert env.cb.lambda_handler(_payload("FAILED"), None)["status"] == "ignored"
