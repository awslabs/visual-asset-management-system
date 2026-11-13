# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A user's cancel racing a read-time reconcile of the run its own stop ended (#1125 D22).

The cancel's stop marker makes the Zip Archive container stop and exit 0, so the run ends SUCCEEDED
and a concurrent read of the job (the web's poll, another tab, the CLI's wait) can reconcile it while
the cancel request is still in flight. Seen live (then through the since removed terminal callback,
which applied the same rule): FAILED ("not produced") was written before the cancel's CANCELLED
write, the cancel lost its conditional write and the DELETE then fell through and deleted the job.

Required outcome: once the cancel is recorded the job ends CANCELLED, whichever writer finalizes it;
the cancel never deletes a job; deletion stays a DELETE of a job that had already finished. Each
interleaving drives the REAL service DELETE route and the REAL read-time reconcile against one table.
"""

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

FILE_SELECTION = {"selection": [{"type": "file", "relativePath": "/a.glb"}]}


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    return e


def _running_job(env):
    h.AUTH.user = "alice"
    created = env.svc.lambda_handler(h.api_event(
        "POST", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"}, body=FILE_SELECTION), None)
    request_id = h.body_of(created)["requestId"]
    row = env.zip.items[("alice", request_id)]
    row["status"] = "RUNNING"
    h.record_upload_token(row)
    env.s3.uploads.append({"Bucket": h.AUX, "Key": row["archiveKey"], "UploadId": "u1"})
    return request_id


def _delete(env, request_id):
    h.AUTH.user = "alice"
    path = h.asset_path("db1", "asset1", f"/{request_id}")
    return env.svc.lambda_handler(h.api_event("DELETE", path, {"databaseId": "db1", "assetId": "asset1",
                                                               "requestId": request_id}), None)


def _reconcile(env, request_id, status="SUCCEEDED"):
    """A concurrent read of the job after its run ended (the container stopped and exited 0:
    SUCCEEDED): the read-time reconcile."""
    return h.end_run_and_reconcile(env, "alice", request_id, status)


def _run_once_on_update(env, predicate, action, before=False):
    """Run ``action`` once, around the first table update that matches ``predicate``."""
    original, fired = env.zip.update_item, []

    def update_item(**kw):
        trigger = not fired and predicate(kw)
        if trigger:
            fired.append(True)
            if before:
                action()
        result = original(**kw)
        if trigger and not before:
            action()
        return result
    env.zip.update_item = update_item
    return fired


def _stop_marker(kw):
    return "stopRequestedAt" in kw["UpdateExpression"]


def _final_status_write(kw):
    return kw["UpdateExpression"].startswith("SET #status = :status")


class TestReconcileAfterTheCancelWasRecorded:
    def test_reconcile_between_the_stop_and_the_cancelled_write_ends_cancelled(self, env):
        """The live D22 order: stop marker, the run ends SUCCEEDED, a reconcile finalizes, then the
        cancel's own CANCELLED write. The job ends CANCELLED by the user and is never deleted."""
        request_id = _running_job(env)
        fired = _run_once_on_update(env, _stop_marker, lambda: _reconcile(env, request_id))
        body = h.body_of(_delete(env, request_id))
        assert fired
        assert body["action"] == "cancelled"
        row = env.zip.items[("alice", request_id)]
        assert row["status"] == "CANCELLED"
        assert row["cancelledBy"] == "user"
        assert not row.get("errorMessage")
        kinds = [w[0] for w in env.zip.writes]
        assert "update" in kinds, "the table recorded the cancel's writes"
        assert "delete" not in kinds, "the cancel deleted nothing"

    def test_reconcile_writing_after_the_cancel_was_recorded_ends_cancelled(self, env):
        """The reconcile read the row before the cancel was recorded and writes after it: its FAILED
        write loses to the recorded cancel and it records CANCELLED instead."""
        request_id = _running_job(env)
        # The cancel lands while the reconcile is between its decision and its final write.
        fired = _run_once_on_update(env, _final_status_write,
                                    lambda: env.zd.request_cancel(
                                        env.zip, env.zip.items[("alice", request_id)],
                                        env.zd.CANCELLED_BY_USER), before=True)
        _reconcile(env, request_id)
        assert fired
        row = env.zip.items[("alice", request_id)]
        assert row["status"] == "CANCELLED" and row["cancelledBy"] == "user"
        assert not row.get("errorMessage")
        # The cancel that recorded the marker then finds the job CANCELLED and reports it so.
        assert env.zd.cancel_job(env.zip, env.s3, h.AUX, dict(row),
                                 env.zd.CANCELLED_BY_USER) == env.zd.CANCEL_CANCELLED
        assert env.zip.items[("alice", request_id)]["status"] == "CANCELLED"

    def test_late_reconcile_after_the_cancel_is_recorded_ends_cancelled(self, env):
        """The shared finalizer (finalize_ended_run) applies the rule directly."""
        request_id = _running_job(env)
        row = env.zip.items[("alice", request_id)]
        assert env.zd.request_cancel(env.zip, row, env.zd.CANCELLED_BY_USER)
        status, changed = env.zd.finalize_ended_run(env.zip, env.s3, h.AUX, dict(row), "SUCCEEDED")
        assert (status, changed) == ("CANCELLED", True)
        assert env.zip.items[("alice", request_id)]["cancelledBy"] == "user"


class TestReconcileBeforeTheCancelWasRecorded:
    def test_job_that_ended_first_is_kept_with_its_outcome(self, env):
        """The run ended and a reconcile finalized the job before the cancel recorded anything:
        the job keeps its outcome and the cancel deletes nothing (it used to delete it)."""
        request_id = _running_job(env)
        fired = _run_once_on_update(env, lambda kw: True, lambda: _reconcile(env, request_id),
                                    before=True)
        response = _delete(env, request_id)
        assert fired
        body = h.body_of(response)
        assert response["statusCode"] == 200
        assert body["action"] == "ended" and body["status"] == "FAILED"
        assert body["message"] == env.zd.MSG_ALREADY_FINISHED
        row = env.zip.items[("alice", request_id)]
        assert row["status"] == "FAILED" and row["errorMessage"] == env.zd.MSG_NOT_PRODUCED
        kinds = [w[0] for w in env.zip.writes]
        assert "update" in kinds, "the table recorded the reconcile's writes"
        assert "delete" not in kinds, "the cancel deleted nothing"

    def test_delete_of_a_finished_job_still_deletes(self, env):
        request_id = _running_job(env)
        _reconcile(env, request_id)
        assert env.zip.items[("alice", request_id)]["status"] == "FAILED"
        assert h.body_of(_delete(env, request_id))["action"] == "deleted"
        assert ("alice", request_id) not in env.zip.items


class TestTerminalRulesKept:
    def test_executions_abort_without_a_user_cancel_still_fails(self, env):
        request_id = _running_job(env)
        _reconcile(env, request_id, "ABORTED")
        row = env.zip.items[("alice", request_id)]
        assert row["status"] == "FAILED" and row["cancelledBy"] == "execution"
        assert row["errorMessage"] == env.zd.MSG_CANCELLED_FROM_EXECUTIONS

    def test_landed_archive_wins_over_a_recorded_cancel(self, env):
        request_id = _running_job(env)
        row = env.zip.items[("alice", request_id)]
        h.land_archive(env, row, size=7)
        assert env.zd.request_cancel(env.zip, row, env.zd.CANCELLED_BY_USER)
        _reconcile(env, request_id)
        assert env.zip.items[("alice", request_id)]["status"] == "SUCCEEDED"
