# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A cancel never deletes a job (#1125 D23), and an abort from the workflow executions views always
ends with its documented reason and cleans the job's working objects (#1125 D24).

D23: a cancel confirmed after the job finished (a confirm dialog left open, the CLI's state check
then its DELETE) reached a finished job without saying it meant a cancel, and the route deleted the
job and its archive. The DELETE now takes ``intent=cancel``: with it a finished job is kept and
answered ``ended`` (a CANCELLED one answers ``cancelled``); without it the route is unchanged.

D24: the container's SIGTERM used to write FAILED "The archive job was stopped" and won the race
with the finalizer of the abort, so the job lost its documented reason and cancelledBy, and the
finalizer that lost left the manifests. The container now only records the stop; the read-time
reconcile finalizes from the run's status, and cleans the working objects even when another writer
finalized first.

Drives the REAL service routes and read-time reconcile against one table and one S3 double.
"""

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

FILE_SELECTION = {"selection": [{"type": "file", "relativePath": "/a.glb"}]}
STOPPED = "The archive job was stopped"


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    return e


def _job(env, status="RUNNING"):
    h.AUTH.user = "alice"
    created = env.svc.lambda_handler(h.api_event(
        "POST", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"}, body=FILE_SELECTION), None)
    request_id = h.body_of(created)["requestId"]
    row = env.zip.items[("alice", request_id)]
    row["status"] = status
    h.record_upload_token(row)
    return request_id, row


def _delete(env, request_id, query=None):
    h.AUTH.user = "alice"
    path = h.asset_path("db1", "asset1", f"/{request_id}")
    return env.svc.lambda_handler(h.api_event("DELETE", path, {"databaseId": "db1", "assetId": "asset1",
                                                               "requestId": request_id},
                                              query=query), None)


def _reconcile(env, request_id, status):
    """The job's run ended with ``status``; the job's next read reconciles it."""
    return h.end_run_and_reconcile(env, "alice", request_id, status)


def _working_objects_left(env, row):
    """The job's request and resolved manifests still stored in the auxiliary bucket."""
    return [k for k in env.zd.working_object_keys(row) if env.s3.objects.get((h.AUX, k))]


def _store_working_objects(env, row):
    for key in env.zd.working_object_keys(row):
        env.s3.put_object(Bucket=h.AUX, Key=key, Body=b"{}")


def _deleted(env):
    return "delete" in [w[0] for w in env.zip.writes]


class TestCancelIntent:
    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
    def test_cancel_of_a_finished_job_keeps_it(self, env, status):
        """The D23 repro: the job finished while the confirm dialog was open; the cancel arrives."""
        request_id, row = _job(env, status)
        if status == "SUCCEEDED":
            h.land_archive(env, row, size=7)
        response = _delete(env, request_id, {"intent": "cancel"})
        assert response["statusCode"] == 200
        body = h.body_of(response)
        assert body == {"message": env.zd.MSG_ALREADY_FINISHED, "action": "ended", "status": status}
        assert ("alice", request_id) in env.zip.items, "a cancel never deletes the job"
        assert env.zip.items[("alice", request_id)]["status"] == status
        assert not _deleted(env)
        if status == "SUCCEEDED":
            assert env.zd.archive_head(env.s3, h.AUX, row["archiveKey"])[0], "archive kept"

    def test_repeated_cancel_of_a_cancelled_job_answers_cancelled(self, env):
        request_id, _ = _job(env, "CANCELLED")
        body = h.body_of(_delete(env, request_id, {"intent": "cancel"}))
        assert body["action"] == "cancelled"
        assert ("alice", request_id) in env.zip.items and not _deleted(env)

    def test_cancel_of_an_active_job_cancels_it(self, env):
        request_id, _ = _job(env, "RUNNING")
        body = h.body_of(_delete(env, request_id, {"intent": "cancel"}))
        assert body["action"] == "cancelled"
        assert env.zip.items[("alice", request_id)]["status"] == "CANCELLED"

    @pytest.mark.parametrize("query", [None, {}])
    def test_delete_without_the_intent_still_deletes_a_finished_job(self, env, query):
        request_id, _ = _job(env, "SUCCEEDED")
        body = h.body_of(_delete(env, request_id, query))
        assert body["action"] == "deleted"
        assert ("alice", request_id) not in env.zip.items

    @pytest.mark.parametrize("value", ["delete", "CANCEL", "", "cancel,delete"])
    def test_an_unknown_intent_is_refused_before_anything_changes(self, env, value):
        request_id, _ = _job(env, "SUCCEEDED")
        response = _delete(env, request_id, {"intent": value})
        assert response["statusCode"] == 400
        assert ("alice", request_id) in env.zip.items and not _deleted(env)

    def test_cancel_intent_on_another_users_job_is_the_same_404(self, env):
        request_id, _ = _job(env, "SUCCEEDED")
        h.AUTH.user = "bob"
        path = h.asset_path("db1", "asset1", f"/{request_id}")
        response = env.svc.lambda_handler(h.api_event(
            "DELETE", path, {"databaseId": "db1", "assetId": "asset1", "requestId": request_id},
            query={"intent": "cancel"}), None)
        assert response["statusCode"] == 404
        assert ("alice", request_id) in env.zip.items


class TestExecutionsAbortReason:
    def _container_stop(self, env, row):
        """What the container's SIGTERM now writes: the stop reason, no status change. It loses
        (as StatusWriter.stopped does) once the job is final."""
        from botocore.exceptions import ClientError
        try:
            self._stop_write(env, row)
        except ClientError as e:
            assert e.response["Error"]["Code"] == "ConditionalCheckFailedException"

    def _stop_write(self, env, row):
        env.zip.update_item(
            Key=env.zd.row_key(row["userId"], row["requestId"]),
            UpdateExpression="SET stopReason = :r, updatedAt = :u",
            ConditionExpression="attribute_exists(requestId) AND #status IN (:queued, :running)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":r": STOPPED, ":u": "2026-11-08T00:00:00Z",
                                       ":queued": "QUEUED", ":running": "RUNNING"})

    @pytest.mark.parametrize("stop_first", [True, False])
    def test_reconcile_of_an_abort_records_the_documented_reason(self, env, stop_first):
        request_id, row = _job(env)
        _store_working_objects(env, row)
        if stop_first:
            self._container_stop(env, row)
        _reconcile(env, request_id, "ABORTED")
        if not stop_first:
            self._container_stop(env, row)   # loses: the job is already final
        final = env.zip.items[("alice", request_id)]
        assert final["status"] == "FAILED"
        assert final["errorMessage"] == env.zd.MSG_CANCELLED_FROM_EXECUTIONS
        assert final["cancelledBy"] == env.zd.CANCELLED_BY_EXECUTION
        assert _working_objects_left(env, final) == []

    def test_the_shared_finalizer_of_an_aborted_run_records_the_documented_reason(self, env):
        request_id, row = _job(env)
        self._container_stop(env, row)
        status, changed = env.zd.finalize_ended_run(env.zip, env.s3, h.AUX, dict(row), "ABORTED")
        assert (status, changed) == ("FAILED", True)
        final = env.zip.items[("alice", request_id)]
        assert final["errorMessage"] == env.zd.MSG_CANCELLED_FROM_EXECUTIONS
        assert final["cancelledBy"] == env.zd.CANCELLED_BY_EXECUTION

    def test_a_failed_run_after_a_stop_shows_the_stop(self, env):
        """A Batch terminate outside VAMS: the run FAILED, the container recorded its stop."""
        request_id, row = _job(env)
        self._container_stop(env, row)
        _reconcile(env, request_id, "FAILED")
        final = env.zip.items[("alice", request_id)]
        assert final["status"] == "FAILED" and final["errorMessage"] == STOPPED
        assert not final.get("cancelledBy")

    def test_a_timed_out_run_after_a_stop_shows_the_timeout(self, env):
        request_id, row = _job(env)
        self._container_stop(env, row)
        _reconcile(env, request_id, "TIMED_OUT")
        assert env.zip.items[("alice", request_id)]["errorMessage"] == env.zd.MSG_JOB_TIMED_OUT

    def test_a_recorded_user_cancel_still_wins_over_the_stop(self, env):
        request_id, row = _job(env)
        assert env.zd.request_cancel(env.zip, row, env.zd.CANCELLED_BY_USER)
        self._container_stop(env, row)
        _reconcile(env, request_id, "ABORTED")
        final = env.zip.items[("alice", request_id)]
        assert final["status"] == "CANCELLED" and final["cancelledBy"] == "user"

    def test_stop_reason_is_not_in_the_api_answer(self, env):
        request_id, row = _job(env)
        self._container_stop(env, row)
        item = env.zd.to_job_item(env.zip.items[("alice", request_id)])
        assert "stopReason" not in item and not item.get("errorMessage")


class TestCleanupWhenAnotherWriterFinalizedFirst:
    def test_reconcile_whose_write_lost_still_removes_the_working_objects(self, env):
        """The live D24 shape: the reconcile read the row active, another writer (the old container's
        FAILED) finalized it between the reconcile's re-read and its write. The reconcile's write loses
        and it still removes the working objects."""
        request_id, row = _job(env)
        _store_working_objects(env, row)
        original, fired = env.zip.update_item, []

        def update_item(**kw):
            if not fired and kw["UpdateExpression"].startswith("SET #status = :status"):
                fired.append(True)
                stored = env.zip.items[("alice", request_id)]
                stored.update(status="FAILED", errorMessage="The archive job failed",
                              completedAt="2026-11-08T00:00:00Z")
            return original(**kw)
        env.zip.update_item = update_item
        _reconcile(env, request_id, "ABORTED")
        assert fired
        final = env.zip.items[("alice", request_id)]
        assert final["status"] == "FAILED" and final["errorMessage"] == "The archive job failed"
        assert _working_objects_left(env, final) == []
