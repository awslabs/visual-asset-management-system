# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1125 in-dev D16, round 2 (the #1123 D5 rule after the demo21 recheck): an abort's immediate
answer is not final.

On demo21 every copy cancel path ended its cleanup on ListParts = NoSuchUpload right after its own
abort, yet the upload was listed again 2 to 8 minutes later with a part accepted before the stop. The
zip stoppers (the user's cancel, the terminal callback, the read-time reconcile, the delete) had the
same immediate-only sweep. Now each also starts the job's delayed sweep (schedule_delayed_sweep, once
per job), whose passes (the callback's ``sweep`` action, about 5 and 15 minutes later) abort only
the archive uploads the container recorded on the job row (archiveUploadIds) when still listed at
the job's exact archive key, unless the job's archive landed.

``GhostS3`` is the harness S3 with what demo21 showed: the first abort of an upload answers 204 and
leaves it; for ``hidden_for`` seconds ListParts and a repeated abort answer NoSuchUpload and
ListMultipartUploads does not list it; after that it is listed again and an abort removes it.

The end-to-end tests fail on 0620cfaee (no sweep is scheduled, the upload stays listed)."""

import json
from unittest.mock import MagicMock

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

REQUEST_ID = "0mw4e6byk-" + "c" * 32
MACHINE_ARN = "arn:aws:states:us-east-1:111122223333:stateMachine:ZipDownloadSweep-x"


class GhostS3(h.FakeS3):
    def __init__(self, hidden_for):
        super().__init__()
        self.hidden_for = hidden_for
        self.now = 0
        self.hidden_until = {}

    def _hidden(self, upload_id):
        return upload_id in self.hidden_until and self.now < self.hidden_until[upload_id]

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        if not any(u["UploadId"] == UploadId for u in self.uploads):
            raise h._client_error("NoSuchUpload", "AbortMultipartUpload")
        if UploadId not in self.hidden_until:
            self.hidden_until[UploadId] = self.now + self.hidden_for
            self.aborted.append((Bucket, Key, UploadId))
            return {}
        if self._hidden(UploadId):
            raise h._client_error("NoSuchUpload", "AbortMultipartUpload")
        return super().abort_multipart_upload(Bucket, Key, UploadId)

    def list_parts(self, Bucket, Key, UploadId, **kwargs):
        if self._hidden(UploadId):
            raise h._client_error("NoSuchUpload", "ListParts")
        return super().list_parts(Bucket, Key, UploadId, **kwargs)

    def list_multipart_uploads(self, Bucket, Prefix, **kwargs):
        response = super().list_multipart_uploads(Bucket, Prefix, **kwargs)
        return {**response, "Uploads": [u for u in response["Uploads"] if not self._hidden(u["UploadId"])]}


@pytest.fixture
def env(monkeypatch):
    h.AUTH.__init__()
    e = h.load_handlers(env={"ZIP_DOWNLOAD_SWEEP_STATE_MACHINE_ARN": MACHINE_ARN})
    monkeypatch.setenv("ZIP_DOWNLOAD_SWEEP_STATE_MACHINE_ARN", MACHINE_ARN)
    e.sfn = MagicMock()
    monkeypatch.setattr(e.zd, "_sweep_sfn_client", e.sfn)
    e.prefix = e.zd.request_prefix("db1", "asset1", "alice", REQUEST_ID)
    e.key = e.prefix + "x.zip"
    e.zip.items[("alice", REQUEST_ID)] = {
        "userId": "alice", "requestId": REQUEST_ID, "databaseId": "db1", "assetId": "asset1",
        "status": "RUNNING", "workflowExecutionId": "exec-1", "archiveKey": e.key,
        "archiveUploadToken": h.UPLOAD_TOKEN, "manifestKey": e.prefix + "manifest.json",
        # What the container's StatusWriter.record_upload writes (a string set in DynamoDB).
        "archiveUploadIds": ["u1"]}
    return e


def _ghost(env, hidden_for):
    s3 = GhostS3(hidden_for)
    s3.uploads.append({"Bucket": h.AUX, "Key": env.key, "UploadId": "u1"})
    env.svc.s3 = env.cb.s3 = s3
    return s3


def _row(env):
    return env.zip.items.get(("alice", REQUEST_ID))


def _starts(env):
    return [c.kwargs for c in env.sfn.start_execution.call_args_list]


def _run_sweep_execution(env, s3, start):
    """What the sweep state machine does with the execution's input."""
    assert start["stateMachineArn"] == MACHINE_ARN
    request = json.loads(start["input"])["zipSweep"]
    s3.now = env.zd.SWEEP_FIRST_WAIT_SECONDS
    env.cb.lambda_handler({"action": "sweep", "zipSweep": request, "final": False}, None)
    s3.now = env.zd.SWEEP_FIRST_WAIT_SECONDS + env.zd.SWEEP_SECOND_WAIT_SECONDS
    return env.cb.lambda_handler({"action": "sweep", "zipSweep": request, "final": True}, None)


def _callback_payload(status):
    return {"callbackType": "systemPipelineTerminal", "schemaVersion": 1,
            "idempotencyKey": f"exec-1:{status}", "workflowExecutionId": "exec-1",
            "triggerType": "Zip-Download", "executionStatus": status, "executionError": "",
            "writer": "abort",
            "systemExecutionContext": {"v": 1, "requestId": REQUEST_ID, "userId": "alice",
                                       "databaseId": "db1", "assetId": "asset1", "manifestKey": "m"}}


def _listed(s3, key):
    return [u for u in s3.list_multipart_uploads(h.AUX, key)["Uploads"] if u["Key"] == key]


# ---------------------------------------------------------------------------
# End to end: each stopper leaves zero uploads once the delayed sweep ran
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hidden_for", [120, 480])  # listed again 2 and 8 minutes later (demo21)
def test_the_users_cancel_leaves_no_upload_after_the_delayed_sweep(env, hidden_for):
    s3 = _ghost(env, hidden_for)
    assert env.zd.cancel_job(env.zip, s3, h.AUX, dict(_row(env)), env.zd.CANCELLED_BY_USER) \
        == env.zd.CANCEL_CANCELLED
    # The immediate sweep ended on NoSuchUpload; the upload is still there.
    s3.now = hidden_for
    assert len(_listed(s3, env.key)) == 1
    s3.now = 0
    starts = _starts(env)
    assert len(starts) == 1
    assert starts[0]["name"] == env.zd.sweep_execution_name_for("alice", REQUEST_ID)
    result = _run_sweep_execution(env, s3, starts[0])
    assert s3.uploads == []
    # Listed again at 2 minutes: the first pass (5 minutes) removes it; at 8: the last pass does.
    assert result == {"swept": True, "aborted": 0 if hidden_for < env.zd.SWEEP_FIRST_WAIT_SECONDS
                      else 1, "remaining": 0}
    assert _row(env)["status"] == "CANCELLED"


@pytest.mark.parametrize("execution_status", ["FAILED", "TIMED_OUT", "ABORTED"])
def test_the_terminal_callback_leaves_no_upload_after_the_delayed_sweep(env, execution_status):
    s3 = _ghost(env, 300)
    env.cb.lambda_handler(_callback_payload(execution_status), None)
    assert _row(env)["status"] == "FAILED"
    starts = _starts(env)
    assert len(starts) == 1
    _run_sweep_execution(env, s3, starts[0])
    assert s3.uploads == []


def test_a_container_failure_already_recorded_still_gets_the_delayed_sweep(env):
    """The container wrote FAILED itself; the callback's already-terminal path schedules too."""
    s3 = _ghost(env, 200)
    _row(env)["status"] = "FAILED"
    s3.abort_multipart_upload(h.AUX, env.key, "u1")  # the sink's own abort (answered, not removed)
    env.cb.lambda_handler(_callback_payload("FAILED"), None)
    starts = _starts(env)
    assert len(starts) == 1
    _run_sweep_execution(env, s3, starts[0])
    assert s3.uploads == []


def test_the_read_time_reconcile_schedules_the_delayed_sweep(env):
    s3 = _ghost(env, 200)
    status, _ = env.zd.finalize_ended_run(env.zip, s3, h.AUX, dict(_row(env)), "FAILED")
    assert status == "FAILED"
    _run_sweep_execution(env, s3, _starts(env)[0])
    assert s3.uploads == []


def test_a_job_deleted_before_the_sweep_is_swept_from_its_snapshot(env):
    """Cancel, then delete at once: the row is gone when the passes run."""
    s3 = _ghost(env, 200)
    env.zd.cancel_job(env.zip, s3, h.AUX, dict(_row(env)), env.zd.CANCELLED_BY_USER)
    request = json.loads(_starts(env)[0]["input"])["zipSweep"]
    assert request["uploadIds"] == ["u1"] and request["archiveKey"] == env.key
    del env.zip.items[("alice", REQUEST_ID)]
    _run_sweep_execution(env, s3, _starts(env)[0])
    assert s3.uploads == []


# ---------------------------------------------------------------------------
# One pass: what it may and may not touch
# ---------------------------------------------------------------------------

def _plain(env):
    s3 = h.FakeS3()
    s3.uploads.append({"Bucket": h.AUX, "Key": env.key, "UploadId": "u1"})
    env.svc.s3 = env.cb.s3 = s3
    return s3


def _pass(env, final=True):
    return env.cb.lambda_handler({"action": "sweep", "final": final,
                                  "zipSweep": env.zd.sweep_request(_row(env))}, None)


def test_the_sweep_never_aborts_another_writers_upload(env):
    s3 = _plain(env)
    _row(env)["status"] = "CANCELLED"
    _row(env)["archiveUploadIds"] = ["u1", "u2"]
    # Not recorded on this row: another run's upload at the same key.
    s3.uploads.append({"Bucket": h.AUX, "Key": env.key, "UploadId": "other-run"})
    # Recorded, but at a key that only starts with the archive key: not this job's archive.
    s3.uploads.append({"Bucket": h.AUX, "Key": env.key + ".part", "UploadId": "u2"})
    assert _pass(env) == {"swept": True, "aborted": 1, "remaining": 0}
    assert s3.aborted == [(h.AUX, env.key, "u1")]
    assert sorted((u["Key"], u["UploadId"]) for u in s3.uploads) == [
        (env.key, "other-run"), (env.key + ".part", "u2")]


def test_the_sweep_leaves_a_succeeded_job_alone(env):
    s3 = _plain(env)
    _row(env)["status"] = "SUCCEEDED"
    assert _pass(env)["swept"] is False
    assert s3.aborted == []


def test_the_sweep_leaves_a_running_job_alone(env):
    s3 = _plain(env)
    assert _pass(env) == {"swept": False, "reason": "the job is still running"}
    assert s3.aborted == []


def test_the_sweep_aborts_a_running_job_whose_stop_was_recorded(env):
    s3 = _plain(env)
    _row(env)["stopRequestedAt"] = "2026-11-07T09:00:00Z"
    assert _pass(env)["aborted"] == 1
    assert s3.uploads == []
    assert _row(env)["status"] == "RUNNING"  # the decision stays with the stoppers


def test_the_landed_archive_is_decided_first_and_never_touched(env):
    s3 = _plain(env)
    _row(env)["status"] = "CANCELLED"
    s3.add(env.key, size=10, bucket=h.AUX,
           metadata={env.zd.ARCHIVE_UPLOAD_TOKEN_METADATA: h.UPLOAD_TOKEN})
    assert _pass(env) == {"swept": False, "reason": "the job's archive landed"}
    assert s3.aborted == []
    assert s3.head_object(h.AUX, env.key)


def test_a_snapshot_outside_the_jobs_prefix_is_refused(env):
    s3 = _plain(env)
    request = env.zd.sweep_request(_row(env))
    del env.zip.items[("alice", REQUEST_ID)]
    for key in ("vams.preparedDownloads/other/x.zip", env.prefix + "sub/x.zip", ""):
        result = env.cb.lambda_handler({"action": "sweep", "final": True,
                                        "zipSweep": {**request, "archiveKey": key}}, None)
        assert result["swept"] is False
    assert s3.aborted == []


def test_a_sweep_input_without_a_job_raises(env):
    with pytest.raises(ValueError):
        env.cb.lambda_handler({"action": "sweep", "zipSweep": {"userId": "alice"}}, None)


def test_a_job_without_recorded_uploads_is_not_swept(env):
    """A job the container never started an upload for (or an image from before archiveUploadIds)."""
    s3 = _plain(env)
    _row(env)["status"] = "CANCELLED"
    del _row(env)["archiveUploadIds"]
    assert _pass(env) == {"swept": False, "reason": "no recorded upload"}
    assert s3.aborted == []


def test_the_listing_follows_pages_and_is_bounded(env):
    pages = [{"Uploads": [{"Key": env.key, "UploadId": "x"}], "IsTruncated": True,
              "NextKeyMarker": env.key, "NextUploadIdMarker": "x"},
             {"Uploads": [{"Key": env.key, "UploadId": "u1"}], "IsTruncated": False}]
    client = MagicMock(**{"list_multipart_uploads.side_effect": pages})
    assert env.zd._upload_ids_listed(client, h.AUX, env.key, ["u1"]) == ["u1"]
    endless = MagicMock(**{"list_multipart_uploads.return_value": {
        "Uploads": [], "IsTruncated": True, "NextKeyMarker": "k", "NextUploadIdMarker": "u"}})
    assert env.zd._upload_ids_listed(endless, h.AUX, env.key, ["u1"], max_pages=3) == []
    assert endless.list_multipart_uploads.call_count == 3


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def test_scheduling_is_once_per_job_and_best_effort(env):
    row = dict(_row(env))
    assert env.zd.schedule_delayed_sweep(row) is True
    start = _starts(env)[0]
    assert len(start["name"]) <= 80 and start["name"].startswith(env.zd.SWEEP_EXECUTION_PREFIX)
    env.sfn.start_execution.side_effect = h._client_error("ExecutionAlreadyExists", "StartExecution")
    assert env.zd.schedule_delayed_sweep(row) is True
    env.sfn.start_execution.side_effect = h._client_error("AccessDeniedException", "StartExecution")
    assert env.zd.schedule_delayed_sweep(row) is False
    env.sfn.start_execution.side_effect = RuntimeError("down")
    assert env.zd.schedule_delayed_sweep(row) is False


def test_no_sweep_is_scheduled_with_the_feature_off(env, monkeypatch):
    monkeypatch.delenv("ZIP_DOWNLOAD_SWEEP_STATE_MACHINE_ARN")
    assert env.zd.schedule_delayed_sweep(dict(_row(env))) is False
    env.sfn.start_execution.assert_not_called()


def test_a_cancel_whose_sweep_cannot_be_scheduled_still_cancels(env):
    s3 = _plain(env)
    env.sfn.start_execution.side_effect = h._client_error("AccessDeniedException", "StartExecution")
    assert env.zd.cancel_job(env.zip, s3, h.AUX, dict(_row(env)), env.zd.CANCELLED_BY_USER) \
        == env.zd.CANCEL_CANCELLED


def test_the_delete_of_a_succeeded_job_schedules_nothing(env):
    s3 = _plain(env)
    s3.uploads.clear()
    _row(env)["status"] = "SUCCEEDED"
    put = h.put_asset(env)  # noqa: F841 - the asset the delete checks
    response = env.svc.lambda_handler(h.api_event(
        "DELETE", h.asset_path(suffix=f"/{REQUEST_ID}"),
        {"databaseId": "db1", "assetId": "asset1", "requestId": REQUEST_ID}), None)
    assert response["statusCode"] == 200
    env.sfn.start_execution.assert_not_called()
