# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A zip download job follows its run by the stored workflowExecutionId alone: the run sends the job
nothing when it ends (no terminal callback). The read-time reconcile (the service's _reconcile, run
by every get, list, presigned URL and cap check) finalizes the job from the run's main execution row
on the next read: each terminal run status, a landed archive, a recorded user cancel, the multipart
cleanup and the delayed sweep, and idempotent repeats."""

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
    e.sweeps = []
    e.zd.schedule_delayed_sweep = lambda row, logger=None: e.sweeps.append(row["requestId"]) or True
    return e


def _row(env):
    return env.zip.items[("alice", REQUEST_ID)]


def _end(env, status, error=""):
    return h.end_run_and_reconcile(env, "alice", REQUEST_ID, status, error)


class TestEachRunStatus:
    def test_aborted_from_the_executions_page_fails_cancelled_from_executions(self, env):
        answered = _end(env, "ABORTED")
        assert answered["status"] == "FAILED"
        assert _row(env)["status"] == "FAILED"
        assert _row(env)["errorMessage"] == "Cancelled from workflow executions"
        assert _row(env)["cancelledBy"] == "execution"
        assert env.s3.aborted == [(h.AUX, env.prefix + "x.zip", "u1")]

    def test_succeeded_without_archive_fails_not_produced(self, env):
        _end(env, "SUCCEEDED")
        assert _row(env)["status"] == "FAILED"
        assert _row(env)["errorMessage"] == env.zd.MSG_NOT_PRODUCED

    def test_timed_out_not_reported_by_the_container(self, env):
        _end(env, "TIMED_OUT")
        assert _row(env)["status"] == "FAILED"
        assert _row(env)["errorMessage"] == env.zd.MSG_JOB_TIMED_OUT

    def test_failed_not_reported_by_the_container_shows_the_fixed_message(self, env):
        _end(env, "FAILED", "States.TaskFailed arn:aws:states:us-east-1:123:internal")
        assert _row(env)["status"] == "FAILED"
        assert _row(env)["errorMessage"] == env.zd.MSG_JOB_FAILED

    def test_failed_keeps_the_container_reason(self, env):
        _row(env)["errorMessage"] = "/a.exe has a blocked file type"
        _end(env, "FAILED", "States.TaskFailed")
        assert _row(env)["errorMessage"] == "/a.exe has a blocked file type"

    def test_failed_shows_a_prefixed_execution_error(self, env):
        _end(env, "FAILED", "ZIPDOWNLOAD: /x.exe has a blocked file type")
        assert _row(env)["errorMessage"] == "/x.exe has a blocked file type"

    def test_failed_falls_back_to_the_recorded_stop_reason(self, env):
        _row(env)["stopReason"] = "The archive job was stopped"
        _end(env, "FAILED")
        assert _row(env)["errorMessage"] == "The archive job was stopped"

    def test_a_running_run_leaves_the_job_alone(self, env):
        _end(env, "RUNNING")
        assert _row(env)["status"] == "RUNNING"
        assert env.s3.aborted == [] and env.sweeps == []


class TestCleanup:
    @pytest.mark.parametrize("status", ["ABORTED", "FAILED", "TIMED_OUT", "SUCCEEDED"])
    def test_open_uploads_aborted_and_the_delayed_sweep_started(self, env, status):
        _end(env, status)
        assert env.s3.aborted == [(h.AUX, env.prefix + "x.zip", "u1")]
        assert env.sweeps == [REQUEST_ID]

    @pytest.mark.parametrize("status", ["ABORTED", "FAILED", "TIMED_OUT", "SUCCEEDED"])
    def test_a_job_ended_without_archive_keeps_no_working_objects(self, env, status):
        manifest_key, resolved_key = env.zd.working_object_keys(_row(env))
        env.s3.add(manifest_key, bucket=h.AUX)
        env.s3.add(resolved_key, bucket=h.AUX)
        _end(env, status)
        assert _row(env)["status"] == "FAILED"
        assert (h.AUX, manifest_key) not in env.s3.objects
        assert (h.AUX, resolved_key) not in env.s3.objects

    def test_open_upload_is_aborted_before_the_archive_check(self, env):
        order = []
        abort, head = env.s3.abort_multipart_upload, env.s3.head_object
        env.s3.abort_multipart_upload = lambda **kw: (order.append("abort"), abort(**kw))[1]
        env.s3.head_object = lambda **kw: (order.append("head"), head(**kw))[1]
        _end(env, "FAILED")
        assert order[:2] == ["abort", "head"]
        assert _row(env)["status"] == "FAILED"

    @pytest.mark.parametrize("ended", ["FAILED", "CANCELLED", "SUCCEEDED"])
    def test_an_already_ended_job_is_not_touched(self, env, ended):
        """A job the container or the user's cancel already finalized is final: the reconcile reads
        nothing and writes nothing. An upload such a writer left open is aborted by the
        vams.preparedDownloads/ lifecycle rule after a day (infra zipDownloadsInfra.test.ts)."""
        _row(env)["status"] = ended
        before = dict(_row(env))
        answered = _end(env, "ABORTED")
        assert answered["status"] == ended
        assert _row(env) == before
        assert env.s3.aborted == [] and env.sweeps == []


class TestOutcomeOrder:
    def test_succeeded_with_the_archive(self, env):
        h.land_archive(env, _row(env), size=77)
        _end(env, "SUCCEEDED")
        assert _row(env)["status"] == "SUCCEEDED" and _row(env)["archiveSizeBytes"] == 77

    @pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT", "ABORTED"])
    def test_a_landed_archive_wins_over_a_failed_run(self, env, status):
        h.land_archive(env, _row(env), size=77)
        answered = _end(env, status)
        assert answered["status"] == "SUCCEEDED"
        assert _row(env)["status"] == "SUCCEEDED" and _row(env)["archiveSizeBytes"] == 77
        assert "errorMessage" not in _row(env)

    def test_a_succeeded_job_keeps_its_objects(self, env):
        h.land_archive(env, _row(env), size=77)
        _, resolved_key = env.zd.working_object_keys(_row(env))
        env.s3.add(resolved_key, bucket=h.AUX)
        _end(env, "SUCCEEDED")
        assert _row(env)["status"] == "SUCCEEDED"
        assert (h.AUX, resolved_key) in env.s3.objects

    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"])
    def test_an_earlier_same_size_object_is_not_this_jobs_archive(self, env, status):
        env.s3.add(env.prefix + "x.zip", size=77, bucket=h.AUX, version="old")
        env.s3.add(env.prefix + "x.zip", size=77, bucket=h.AUX, version="other",
                   metadata={env.zd.ARCHIVE_UPLOAD_TOKEN_METADATA: "tok-another-upload"})
        _end(env, status)
        assert _row(env)["status"] == "FAILED" and "archiveSizeBytes" not in _row(env)

    def test_a_row_without_a_token_has_no_landed_archive(self, env):
        del _row(env)["archiveUploadToken"]
        env.s3.add(env.prefix + "x.zip", size=77, bucket=h.AUX)
        _end(env, "SUCCEEDED")
        assert _row(env)["status"] == "FAILED"

    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"])
    def test_a_recorded_user_cancel_wins_over_the_run_status(self, env, status):
        _row(env).update(cancelRequestedAt="2026-11-10T00:00:00Z", cancelRequestedBy="user")
        _end(env, status)
        assert _row(env)["status"] == "CANCELLED"
        assert _row(env)["cancelledBy"] == "user"
        assert "errorMessage" not in _row(env)

    def test_a_landed_archive_wins_over_a_recorded_cancel(self, env):
        h.land_archive(env, _row(env), size=77)
        _row(env).update(cancelRequestedAt="2026-11-10T00:00:00Z", cancelRequestedBy="user")
        _end(env, "ABORTED")
        assert _row(env)["status"] == "SUCCEEDED"

    def test_a_job_finalized_by_the_cancel_stays_cancelled(self, env):
        _row(env)["status"] = "CANCELLED"
        _end(env, "ABORTED")
        assert _row(env)["status"] == "CANCELLED"

    def test_a_repeat_read_is_a_no_op(self, env):
        _end(env, "ABORTED")
        before = dict(_row(env))
        _end(env, "ABORTED")
        assert _row(env) == before


class TestTriggerValueIsNotRead:
    """The job follows its run by the stored workflowExecutionId only. A run recorded under the earlier
    trigger value (Zip-Download, on test stacks before the generic System- contract) or the current
    one (System-ZipDownload) reconciles the same way."""

    @pytest.mark.parametrize("trigger", ["Zip-Download", "System-ZipDownload", None])
    @pytest.mark.parametrize("status", ["ABORTED", "TIMED_OUT"])
    def test_a_run_under_any_trigger_value_reconciles(self, env, trigger, status):
        h.set_run_status(env, "exec-1", status)
        main = env.executions.items[("exec-1", h.MAIN_SORT_KEY)]
        main["isSystem"] = True
        if trigger:
            main["triggerType"] = trigger
        env.svc._reconcile(dict(_row(env)))
        assert _row(env)["status"] == "FAILED"
        expected = (env.zd.MSG_CANCELLED_FROM_EXECUTIONS if status == "ABORTED"
                    else env.zd.MSG_JOB_TIMED_OUT)
        assert _row(env)["errorMessage"] == expected

    def test_the_service_never_reads_a_trigger_value(self):
        import os
        here = os.path.dirname(__file__)
        for rel in (("..", "..", "..", "backend", "handlers", "assets", "zipDownloadService.py"),
                    ("..", "..", "..", "backend", "common", "zipDownloads.py")):
            with open(os.path.join(here, *rel), encoding="utf-8") as source:
                text = source.read()
            assert 'get("triggerType")' not in text and "['triggerType']" not in text
            assert '["triggerType"]' not in text


class TestThroughTheRoutes:
    def test_a_get_of_the_job_finalizes_it(self, env):
        h.put_asset(env)
        h.AUTH.user = "alice"
        h.set_run_status(env, "exec-1", "ABORTED")
        response = env.svc.lambda_handler(h.api_event(
            "GET", h.asset_path(suffix=f"/{REQUEST_ID}"),
            {"databaseId": "db1", "assetId": "asset1", "requestId": REQUEST_ID}), None)
        item = h.body_of(response)
        assert item["status"] == "FAILED"
        assert item["errorMessage"] == "Cancelled from workflow executions"
        assert item["cancelledBy"] == "execution"

    def test_a_list_finalizes_the_job(self, env):
        h.put_asset(env)
        h.AUTH.user = "alice"
        h.set_run_status(env, "exec-1", "TIMED_OUT")
        response = env.svc.lambda_handler(h.api_event("GET", "/download/zip"), None)
        items = h.body_of(response)["Items"]
        assert [i["status"] for i in items] == ["FAILED"]
        assert items[0]["errorMessage"] == env.zd.MSG_JOB_TIMED_OUT


def test_a_former_terminal_callback_payload_is_ignored(env):
    """A callback-shaped direct invoke (as the workflow core sent before) has no branch any more: it
    is logged and ignored, with no read or write."""
    payload = {"callbackType": "systemPipelineTerminal", "schemaVersion": 1,
               "workflowExecutionId": "exec-1", "triggerType": "Zip-Download",
               "executionStatus": "ABORTED", "executionError": "", "writer": "abort",
               "systemExecutionContext": {"v": 1, "requestId": REQUEST_ID, "userId": "alice",
                                          "databaseId": "db1", "assetId": "asset1"}}
    assert env.svc.lambda_handler(payload, None) == {"status": "ignored",
                                                       "reason": "unknown event shape"}
    assert _row(env)["status"] == "RUNNING"
    assert env.s3.aborted == []
    assert not hasattr(env.svc, "handle_callback")
