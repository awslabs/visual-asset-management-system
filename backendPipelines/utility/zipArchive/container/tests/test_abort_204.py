#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A zip download job ends with one outcome when S3 answers 204 to an abort (#1123 D1).

S3 does not refuse an AbortMultipartUpload sent while a CompleteMultipartUpload is in flight: it
answers 204 and the Complete can still succeed. It also answers 204 to an abort sent after a
Complete succeeded, and the object stays. The S3 double of test_terminal_races.py removes the upload
on abort and so refuses the Complete afterwards, which only models an abort sent BEFORE the Complete.

Here the same REAL container job and REAL API-side finalizers (backend common/zipDownloads.py) run
against an S3 double that answers the abort as S3 does, with the decider (the user's cancel, the
terminal callback / read-time reconcile, the container's own SIGTERM) placed while the archive's
Complete is in flight and after it returned:

- a decider during the Complete answers "not landed" (the 204 does not stop the Complete); the
  archive then lands and the container, finding the job finalized, deletes its own version;
- a decider after the Complete (an upload still listed, the abort answered 204) finds the job's own
  archive by its upload token and records SUCCEEDED;
- a SIGTERM during the Complete neither aborts nor writes FAILED (the handler returns, the Complete
  finishes, its own path decides), and a container killed after the Complete reached S3 leaves the
  decision to the terminal callback, which finds the archive by its token.
"""

import pytest

from zip_archive_pipeline import runner
from test_terminal_races import (AUX, ARCHIVE_KEY, _assert_one_outcome, _cancel, _hook, _reconcile,
                                 _run, _setup, zd)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_sweep_wait(monkeypatch):
    """The finalizers' abort sweep (zd.abort_until_gone, #1125 D16) waits between its repeats."""
    monkeypatch.setattr(zd, "_sleep", lambda seconds: None)

STOP_STATUSES = ["FAILED", "TIMED_OUT", "ABORTED"]


def _answer_aborts_like_s3(s3, during_complete=None, list_completed=False):
    """Arm ``s3`` so AbortMultipartUpload answers as S3 does.

    During a Complete (``during_complete`` runs inside the CompleteMultipartUpload call) an abort
    answers 204 and does not stop it: the Complete still lands its object, even when the caller
    raised meanwhile (the request had reached S3). After a Complete an abort answers 204 and the
    object stays. ``list_completed``: ListMultipartUploads still lists a completed upload (a lagging
    listing), so a decider sends the abort after the Complete."""
    state = {"in_flight": False, "completed": set()}
    complete, listing = s3.complete_multipart_upload, s3.list_multipart_uploads
    s3.aborts_answered_204 = []

    def abort_multipart_upload(Bucket, Key, UploadId):
        s3.aborted.append(Key)
        if state["in_flight"] or (Bucket, Key) in state["completed"]:
            s3.aborts_answered_204.append(Key)
            return {"ResponseMetadata": {"HTTPStatusCode": 204}}
        s3.uploads.pop((Bucket, Key), None)
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    def complete_multipart_upload(**kwargs):
        state["in_flight"] = True
        try:
            if during_complete:
                during_complete()
        finally:
            state["in_flight"] = False
            response = complete(**kwargs)
            state["completed"].add((kwargs["Bucket"], kwargs["Key"]))
        return response

    def list_multipart_uploads(Bucket, Prefix, **kwargs):
        response = listing(Bucket=Bucket, Prefix=Prefix, **kwargs)
        if list_completed:
            response["Uploads"] = list(response["Uploads"]) + [
                {"Key": k, "UploadId": "u1"} for (b, k) in sorted(state["completed"])
                if b == Bucket and k.startswith(Prefix)]
        return response

    s3.abort_multipart_upload = abort_multipart_upload
    s3.complete_multipart_upload = complete_multipart_upload
    s3.list_multipart_uploads = list_multipart_uploads


def _sigterm(job):
    """What AWS Batch's SIGTERM runs in the container (runner.install_sigterm)."""
    return lambda: runner.sigterm_handler(job)(15, None)


# ------------------------------------------------------------------------------------------------ #
# A decider during the in-flight Complete: its abort answers 204 and the Complete still lands
# ------------------------------------------------------------------------------------------------ #

class TestDeciderDuringTheComplete:
    def test_cancel_ends_cancelled_and_the_late_archive_is_deleted(self):
        s3, table, job = _setup()
        results = []
        _answer_aborts_like_s3(s3, during_complete=_cancel(s3, table, results))
        assert _run(job)["status"] == "stopped"   # the container's SUCCEEDED write lost
        assert results == [zd.CANCEL_CANCELLED]
        # The upload stays listed while its Complete is in flight, so the decider's bounded sweep
        # repeats the abort, each answered 204 (#1125 D16); none stops the Complete.
        assert s3.aborts_answered_204 == [ARCHIVE_KEY] * zd.ABORT_SWEEP_ATTEMPTS, \
            "the abort was answered 204"
        _assert_one_outcome(s3, table, zd.STATUS_CANCELLED)
        assert s3.deleted == [(ARCHIVE_KEY, "m1")], "only this upload's version is removed"

    @pytest.mark.parametrize("execution_status", STOP_STATUSES)
    def test_the_terminal_callback_ends_failed_and_the_late_archive_is_deleted(self, execution_status):
        s3, table, job = _setup()
        _answer_aborts_like_s3(s3, during_complete=_reconcile(s3, table, execution_status))
        assert _run(job)["status"] == "stopped"
        assert s3.aborts_answered_204 == [ARCHIVE_KEY] * zd.ABORT_SWEEP_ATTEMPTS
        _assert_one_outcome(s3, table, zd.STATUS_FAILED)
        assert s3.deleted == [(ARCHIVE_KEY, "m1")]


# ------------------------------------------------------------------------------------------------ #
# A decider after the Complete: the abort answers 204, the job's own archive is found by its token
# ------------------------------------------------------------------------------------------------ #

class TestDeciderAfterTheComplete:
    def test_cancel_keeps_the_landed_archive(self, monkeypatch):
        s3, table, job = _setup()
        results = []
        _answer_aborts_like_s3(s3, list_completed=True)
        _hook(monkeypatch, "landed_unrecorded", _cancel(s3, table, results))
        assert _run(job)["status"] == "stopped"
        assert results == [zd.CANCEL_LANDED]
        assert s3.aborts_answered_204 == [ARCHIVE_KEY]
        _assert_one_outcome(s3, table, zd.STATUS_SUCCEEDED)
        assert not getattr(s3, "deleted", [])

    @pytest.mark.parametrize("execution_status", STOP_STATUSES + ["SUCCEEDED"])
    def test_the_terminal_callback_keeps_the_landed_archive(self, monkeypatch, execution_status):
        s3, table, job = _setup()
        _answer_aborts_like_s3(s3, list_completed=True)
        _hook(monkeypatch, "landed_unrecorded", _reconcile(s3, table, execution_status))
        assert _run(job)["status"] == "stopped"
        assert s3.aborts_answered_204 == [ARCHIVE_KEY]
        _assert_one_outcome(s3, table, zd.STATUS_SUCCEEDED)


# ------------------------------------------------------------------------------------------------ #
# The container's own stop during the Complete
# ------------------------------------------------------------------------------------------------ #

class TestSigtermDuringTheComplete:
    def test_the_stop_lets_the_complete_land_and_the_job_succeeds(self):
        # Before: the handler aborted (204, the Complete went on), saw no archive yet, wrote FAILED
        # and exited; the archive then landed under a FAILED job.
        s3, table, job = _setup()
        _answer_aborts_like_s3(s3, during_complete=_sigterm(job))
        assert job.run()["status"] == "succeeded"
        assert s3.aborted == [], "no abort is sent while the Complete is in flight"
        _assert_one_outcome(s3, table, zd.STATUS_SUCCEEDED)

    def test_after_the_users_cancel_the_late_archive_is_deleted(self):
        s3, table, job = _setup()
        results = []
        cancel = _cancel(s3, table, results)

        def cancel_then_terminate():
            cancel()          # delete_job: cancel_job, then the run's abort stops the Batch job
            _sigterm(job)()
        _answer_aborts_like_s3(s3, during_complete=cancel_then_terminate)
        assert _run(job)["status"] == "stopped"
        assert results == [zd.CANCEL_CANCELLED]
        _assert_one_outcome(s3, table, zd.STATUS_CANCELLED)
        assert s3.deleted == [(ARCHIVE_KEY, "m1")]

    @pytest.mark.parametrize("execution_status", STOP_STATUSES)
    def test_killed_after_the_complete_reached_s3_the_callback_decides_by_token(self, execution_status):
        s3, table, job = _setup()

        def terminate_then_kill():
            _sigterm(job)()            # deferred: no abort, no FAILED
            raise SystemExit(137)      # SIGKILL before the Complete's response arrived
        _answer_aborts_like_s3(s3, during_complete=terminate_then_kill)
        with pytest.raises(SystemExit):
            job.run()
        assert table.row["status"] == "RUNNING", "no FAILED over an archive that was landing"
        assert zd.finalize_ended_run(table, s3, AUX, dict(table.row), execution_status)[0] \
            == zd.STATUS_SUCCEEDED
        _assert_one_outcome(s3, table, zd.STATUS_SUCCEEDED)

    def test_a_stop_outside_the_complete_still_aborts_and_records_the_stop(self, monkeypatch):
        s3, table, job = _setup()
        _answer_aborts_like_s3(s3)
        exits = []

        def terminate():
            try:
                _sigterm(job)()
            except SystemExit as e:
                exits.append(e.code)
        _hook(monkeypatch, "upload_open", terminate)
        assert _run(job)["status"] == "raised"   # S3 refused the Complete of the aborted upload
        assert exits == [143]
        # #1125 D24: the container records the stop only; the run's callback decides the outcome.
        assert table.row["status"] == "RUNNING" and table.row["stopReason"] == runner.STOPPED
        assert zd.finalize_ended_run(table, s3, AUX, dict(table.row), "FAILED")[0] == zd.STATUS_FAILED
        _assert_one_outcome(s3, table, zd.STATUS_FAILED)
        assert table.row["errorMessage"] == runner.STOPPED

    def test_the_sink_reports_the_complete_in_flight_only_during_the_call(self):
        s3, _, job = _setup()
        seen = []
        _answer_aborts_like_s3(s3, during_complete=lambda: seen.append(job.sink.completing))
        job.run()
        assert seen == [True]
        assert job.sink.completing is False
