# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The combined stack: every terminal writer of a zip download run finalizes its job exactly once.

The workflow side is real: the end state (P2a's guarded completion), the abort with its held callback,
the status change event push (P2a) and the details read that flushes a held callback (P2b), all on a
moto main execution table. Their system-pipeline callback is delivered to the REAL zip download
callback (P4) through a Lambda double, so a test fails if any pair of writers finalizes the job twice,
or if a writer that wins the main-row race never reaches the zip job.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.workflows import test_system_pipeline_hooks as hooks
from backend.tests.handlers.zipDownloads import zipHarness as h

le, po, sp = hooks.le, hooks.po, hooks.sp
EXEC = hooks.EXEC
REQUEST_ID = "0mw4e6byk-" + "b" * 32
ZIP_CONTEXT = {"v": 1, "requestId": REQUEST_ID, "userId": "alice", "databaseId": "db1",
               "assetId": "asset1", "manifestKey": "m"}


@pytest.fixture
def zipenv():
    h.AUTH.__init__()
    env = h.load_handlers()
    prefix = env.zd.request_prefix("db1", "asset1", "alice", REQUEST_ID)
    env.zip.items[("alice", REQUEST_ID)] = {
        "userId": "alice", "requestId": REQUEST_ID, "databaseId": "db1", "assetId": "asset1",
        "status": "RUNNING", "workflowExecutionId": EXEC, "archiveKey": prefix + "x.zip",
        "manifestKey": prefix + "manifest.json"}
    env.prefix = prefix
    env.deliveries = []
    return env


def _zip_row():
    return hooks._row(context=ZIP_CONTEXT)


def _job(env):
    return env.zip.items[("alice", REQUEST_ID)]


def _finalize_writes(env):
    return [w for w in env.zip.writes if w[0] == "update"]


class _ForwardingLambda:
    """The callback's Lambda client: an asynchronous invoke runs the real zip download callback."""

    def __init__(self, env):
        self.env = env

    def invoke(self, FunctionName, InvocationType, Payload):  # noqa: N803 - mirrors boto3
        assert FunctionName == hooks.CALLBACK_FN and InvocationType == "Event"
        payload = json.loads(Payload.decode("utf-8"))
        self.env.deliveries.append(payload)
        self.env.cb.lambda_handler(payload, None)
        return {"StatusCode": 202}


def _wired(env):
    return patch.dict("os.environ", {sp.CALLBACK_REGISTRY_ENV: hooks.REGISTRY}), \
        patch.object(sp, "_default_lambda_client", return_value=_ForwardingLambda(env))


@pytest.mark.unit
@pytest.mark.aws
class TestZipJobFinalizedExactlyOnce:
    def test_held_abort_then_details_read_then_events(self, zipenv):
        registry, client = _wired(zipenv)
        with hooks._main_table(_zip_row()) as table, registry, client:
            with hooks._abort(table, [_zip_row()], stop_confirmed=False) as drive:
                drive["single"](EXEC)
            assert _finalize_writes(zipenv) == []  # held: the job is not told yet
            held = hooks._stored(table)
            with hooks._reconcile_env(table), patch.object(le, "SFN_SYNC_MIN_INTERVAL_SECONDS", 0), \
                    patch.object(le.sfn, "describe_execution", return_value=hooks._stopped("ABORTED")):
                le._reconcile_main_status(EXEC, dict(held))   # P2b read-path flush
                le._reconcile_main_status(EXEC, dict(held))   # a stale read-time copy
                le.lambda_handler(hooks._status_event("ABORTED"), MagicMock())  # P2a push
                le.lambda_handler(hooks._status_event("ABORTED"), MagicMock())  # duplicate event
        assert [p["writer"] for p in zipenv.deliveries] == ["abort"]
        assert len(_finalize_writes(zipenv)) == 1
        assert _job(zipenv)["status"] == "FAILED"
        assert _job(zipenv)["errorMessage"] == "Cancelled from workflow executions"

    def test_status_event_push_then_reads_then_a_lambda_retry(self, zipenv):
        registry, client = _wired(zipenv)
        with hooks._main_table(_zip_row()) as table, registry, client, hooks._reconcile_env(table), \
                patch.object(le, "SFN_SYNC_MIN_INTERVAL_SECONDS", 0), \
                patch.object(le.sfn, "describe_execution", return_value=hooks._stopped("TIMED_OUT")):
            le.lambda_handler(hooks._status_event("TIMED_OUT"), MagicMock())
            le._reconcile_main_status(EXEC, _zip_row())  # a read that still saw RUNNING
            le.lambda_handler(hooks._status_event("TIMED_OUT"), MagicMock())
        assert [p["writer"] for p in zipenv.deliveries] == ["statusEvent"]
        # Lambda's asynchronous retry replays the same delivery.
        zipenv.cb.lambda_handler(zipenv.deliveries[0], None)
        assert len(_finalize_writes(zipenv)) == 1
        assert _job(zipenv)["status"] == "FAILED"
        assert _job(zipenv)["errorMessage"] == "The archive job timed out"

    def test_end_state_success_then_the_status_event(self, zipenv):
        h.land_archive(zipenv, zipenv.zip.items[("alice", REQUEST_ID)], size=42)
        registry, client = _wired(zipenv)
        with hooks._main_table(_zip_row()) as table, registry, client:
            hooks._end_state(hooks._Dynamo(po.workflow_execution_database_v2, table))
            with hooks._reconcile_env(table), \
                    patch.object(le.sfn, "describe_execution", return_value=hooks._stopped("SUCCEEDED")):
                le.lambda_handler(hooks._status_event("SUCCEEDED"), MagicMock())
        assert [p["writer"] for p in zipenv.deliveries] == ["endState"]
        assert len(_finalize_writes(zipenv)) == 1
        assert _job(zipenv)["status"] == "SUCCEEDED"
        assert _job(zipenv)["archiveSizeBytes"] == 42

    def test_the_container_winning_between_the_callbacks_read_and_write_is_kept(self, zipenv):
        # The callback read the job RUNNING; the container recorded SUCCEEDED before the callback's
        # write. The callback's guarded write must lose, not overwrite the success with FAILED.
        stale = dict(_job(zipenv))
        _job(zipenv).update(status="SUCCEEDED", archiveSizeBytes=9)
        real_get = zipenv.zip.get_item
        zipenv.zip.get_item = lambda **kw: {"Item": dict(stale)}
        registry, client = _wired(zipenv)
        try:
            with hooks._main_table(_zip_row()) as table, registry, client, \
                    hooks._reconcile_env(table), \
                    patch.object(le.sfn, "describe_execution", return_value=hooks._stopped("ABORTED")):
                le.lambda_handler(hooks._status_event("ABORTED"), MagicMock())
        finally:
            zipenv.zip.get_item = real_get
        assert len(zipenv.deliveries) == 1
        assert _finalize_writes(zipenv) == []
        assert _job(zipenv)["status"] == "SUCCEEDED" and _job(zipenv)["archiveSizeBytes"] == 9

    def test_the_container_finishing_first_leaves_every_writer_nothing_to_change(self, zipenv):
        _job(zipenv)["status"] = "SUCCEEDED"
        registry, client = _wired(zipenv)
        with hooks._main_table(_zip_row()) as table, registry, client, hooks._reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=hooks._stopped("SUCCEEDED")):
            le.lambda_handler(hooks._status_event("SUCCEEDED"), MagicMock())
        assert len(zipenv.deliveries) == 1
        assert _finalize_writes(zipenv) == []
        assert _job(zipenv)["status"] == "SUCCEEDED"
