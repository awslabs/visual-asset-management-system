# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1125 in-dev D16 and D17: the zip cancel and finalizer paths leave no part behind and sign every
auxiliary bucket call for its Region without an asset buckets table read.

D16 (the #1123 D5 rule). S3 keeps a part whose upload was in flight during an abort, and the upload
stays listed with that part. ``abort_open_uploads`` aborted each upload once. Now every stopper (the
user's cancel, the terminal callback, the read-time reconcile) first records ``stopRequestedAt`` on
the open job (``request_stop``; the container's sink reads it before each part and uploads nothing
further), then aborts until ListParts answers NoSuchUpload (bounded, waiting only while the upload is
still listed). The landed-archive decision is unchanged.

D17 (the #1123 D6 lesson). Both zip functions use the region-routing S3 client for the auxiliary
bucket, which is not in the asset buckets table. Each call then queried bucketNameGSI; the callback's
role holds no grant on that table, so every abort, list and delete logged AccessDenied at error level
(a fault is never cached). The auxiliary bucket is the stack's own, in the deployment Region; both
handlers now register it, so no lookup happens.

Every test fails on dc9f8d591 except three guards that pass there too: a landed archive still wins,
the response projection leaves the marker out, and the real routing resolves a registered name
without a lookup."""

import importlib.util
import os
import sys

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

REQUEST_ID = "0mw4e6byk-" + "b" * 32


class LateS3(h.FakeS3):
    """The harness S3 with S3's abort semantics for a part upload in flight: the first
    ``late_aborts`` aborts of an upload answer 204 and leave it listed (a part landed after them);
    ``always_listed`` keeps it listed forever. Records the order of row writes and aborts."""

    def __init__(self, events, late_aborts=0, always_listed=False):
        super().__init__()
        self.events = events
        self.late_aborts = late_aborts
        self.always_listed = always_listed

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.events.append(("abort", Key))
        if not any(u["UploadId"] == UploadId for u in self.uploads):
            raise h._client_error("NoSuchUpload", "AbortMultipartUpload")
        if self.always_listed or self.late_aborts > 0:
            self.late_aborts -= 1
            self.aborted.append((Bucket, Key, UploadId))
            return {}
        return super().abort_multipart_upload(Bucket, Key, UploadId)


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    e.events = []
    e.prefix = e.zd.request_prefix("db1", "asset1", "alice", REQUEST_ID)
    e.zip.items[("alice", REQUEST_ID)] = {
        "userId": "alice", "requestId": REQUEST_ID, "databaseId": "db1", "assetId": "asset1",
        "status": "RUNNING", "workflowExecutionId": "exec-1", "archiveKey": e.prefix + "x.zip",
        "archiveUploadToken": h.UPLOAD_TOKEN, "manifestKey": e.prefix + "manifest.json"}
    original = e.zip.update_item

    def update_item(**kwargs):
        e.events.append(("row", kwargs["UpdateExpression"]))
        return original(**kwargs)
    e.zip.update_item = update_item
    return e


def _with_s3(env, **kwargs):
    s3 = LateS3(env.events, **kwargs)
    s3.uploads.append({"Bucket": h.AUX, "Key": env.prefix + "x.zip", "UploadId": "u1"})
    env.svc.s3 = env.cb.s3 = s3
    return s3


def _row(env):
    return env.zip.items[("alice", REQUEST_ID)]


def _no_sleep(env, monkeypatch):
    waits = []
    monkeypatch.setattr(env.zd, "_sleep", waits.append)
    return waits


class TestNoPartLeftBehind:
    def test_cancel_records_the_stop_before_it_aborts(self, env, monkeypatch):
        _no_sleep(env, monkeypatch)
        s3 = _with_s3(env)
        assert env.zd.cancel_job(env.zip, s3, h.AUX, dict(_row(env)), env.zd.CANCELLED_BY_USER) \
            == env.zd.CANCEL_CANCELLED
        first_abort = env.events.index(("abort", env.prefix + "x.zip"))
        stop_write = env.events.index(("row", "SET stopRequestedAt = :stopRequestedAt"))
        assert stop_write < first_abort, "the container must see the stop before the abort"
        assert _row(env)["status"] == "CANCELLED"

    def test_cancel_aborts_again_while_a_late_part_keeps_the_upload_listed(self, env, monkeypatch):
        waits = _no_sleep(env, monkeypatch)
        s3 = _with_s3(env, late_aborts=1)
        env.zd.cancel_job(env.zip, s3, h.AUX, dict(_row(env)), env.zd.CANCELLED_BY_USER)
        assert [e for e in env.events if e[0] == "abort"] == [("abort", env.prefix + "x.zip")] * 2
        assert s3.uploads == [], "no upload left in vams.preparedDownloads/"
        assert waits == [env.zd.ABORT_SWEEP_WAIT_SECONDS]

    @pytest.mark.parametrize("execution_status", ["FAILED", "TIMED_OUT", "ABORTED"])
    def test_the_terminal_callback_aborts_until_the_upload_is_gone(self, env, monkeypatch,
                                                                   execution_status):
        _no_sleep(env, monkeypatch)
        s3 = _with_s3(env, late_aborts=2)
        payload = {"callbackType": "systemPipelineTerminal", "schemaVersion": 1,
                   "idempotencyKey": f"exec-1:{execution_status}", "workflowExecutionId": "exec-1",
                   "triggerType": "Zip-Download", "executionStatus": execution_status,
                   "executionError": "", "writer": "abort",
                   "systemExecutionContext": {"v": 1, "requestId": REQUEST_ID, "userId": "alice",
                                              "databaseId": "db1", "assetId": "asset1",
                                              "manifestKey": "m"}}
        env.cb.lambda_handler(payload, None)
        assert s3.uploads == []
        assert _row(env)["status"] == "FAILED"
        assert _row(env).get("stopRequestedAt")

    def test_a_landed_archive_still_wins_after_the_stop(self, env, monkeypatch):
        _no_sleep(env, monkeypatch)
        s3 = _with_s3(env)
        s3.uploads.clear()
        s3.add(_row(env)["archiveKey"], size=77, bucket=h.AUX,
               metadata={env.zd.ARCHIVE_UPLOAD_TOKEN_METADATA: h.UPLOAD_TOKEN})
        assert env.zd.cancel_job(env.zip, s3, h.AUX, dict(_row(env)), env.zd.CANCELLED_BY_USER) \
            == env.zd.CANCEL_LANDED
        assert _row(env)["status"] == "SUCCEEDED"

    def test_the_sweep_is_bounded_and_logged(self, env, monkeypatch):
        waits = _no_sleep(env, monkeypatch)
        s3 = _with_s3(env, always_listed=True)
        warnings = []

        class Log:
            def warning(self, message):
                warnings.append(message)
        env.zd.abort_open_uploads(s3, h.AUX, env.prefix, Log())
        assert len([e for e in env.events if e[0] == "abort"]) == env.zd.ABORT_SWEEP_ATTEMPTS
        assert len(waits) == env.zd.ABORT_SWEEP_ATTEMPTS - 1
        assert any("still listed" in w for w in warnings)

    def test_the_stop_is_written_only_on_an_open_job(self, env):
        _row(env)["status"] = "SUCCEEDED"
        assert env.zd.request_stop(env.zip, dict(_row(env))) is False
        assert "stopRequestedAt" not in _row(env)

    def test_the_marker_never_reaches_the_response(self, env):
        _row(env)["stopRequestedAt"] = "2026-11-07T08:00:00Z"
        assert "stopRequestedAt" not in env.zd.to_job_item(_row(env))


class TestAuxiliaryBucketRegion:
    """D17: no asset buckets table read for the auxiliary bucket, in either function."""

    @pytest.mark.parametrize("module", ["svc", "cb"])
    def test_each_function_registers_the_auxiliary_bucket_region_at_load(self, module):
        # The handlers import the region helpers of common.s3 (the tests' common.s3 double here);
        # the registration lands in that module's name -> Region map.
        loaded = getattr(h.load_handlers(), module)
        regions = sys.modules[loaded.register_bucket_region.__module__]._bucket_regions_by_name
        regions.clear()
        loaded = getattr(h.load_handlers(), module)
        assert regions[loaded.aux_bucket_name] == loaded.deployment_region()

    def test_a_registered_bucket_is_never_looked_up_in_the_buckets_table(self, monkeypatch):
        # The real routing (backend/common/s3.py): a registered name resolves without the
        # bucketNameGSI Query that the callback's role cannot make.
        spec = importlib.util.spec_from_file_location(
            "common_s3_real_under_test",
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "common", "s3.py"))
        real = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(real)

        def denied(name):
            raise AssertionError(f"asset buckets table read for {name}")
        monkeypatch.setattr(real, "_lookup_bucket_region", denied)
        real.register_bucket_region("vams-aux-bucket", "us-gov-west-1")
        assert real.bucket_region_for_name("vams-aux-bucket") == "us-gov-west-1"
