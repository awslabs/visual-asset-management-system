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
        env.s3.add(env.prefix + "x.zip", size=77, bucket=h.AUX)
        env.cb.lambda_handler(_payload("SUCCEEDED"), None)
        assert _row(env)["status"] == "SUCCEEDED" and _row(env)["archiveSizeBytes"] == 77
        assert env.s3.aborted == []

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
