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
    # Status writes only: the stop marker each finalizer records before its abort changes no status
    # (common/zipDownloads.request_stop, #1125 D16).
    return [w for w in env.zip.writes if w[0] == "update" and "#status" in w[2]]


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
            # The zip callback does not depend on the stop: the job is told at once, and the held
            # callback stays on the run for the confirmation.
            assert len(_finalize_writes(zipenv)) == 1
            held = hooks._stored(table)
            assert held[sp.CALLBACK_PENDING_ATTRIBUTE] == "ABORTED"
            with hooks._reconcile_env(table), patch.object(le, "SFN_SYNC_MIN_INTERVAL_SECONDS", 0), \
                    patch.object(le.sfn, "describe_execution", return_value=hooks._stopped("ABORTED")):
                le._reconcile_main_status(EXEC, dict(held))   # P2b read-path flush
                le._reconcile_main_status(EXEC, dict(held))   # a stale read-time copy
                le.lambda_handler(hooks._status_event("ABORTED"), MagicMock())  # P2a push
                le.lambda_handler(hooks._status_event("ABORTED"), MagicMock())  # duplicate event
        assert [p["writer"] for p in zipenv.deliveries] == ["abortUnconfirmed", "abort"]
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


class _QueueingLambda:
    """The callback's Lambda client, holding each asynchronous delivery so a test runs them in the
    order it wants (Lambda gives no order between two Event invokes). `lose` names writers whose
    invoke fails (the delivery is lost; notify_terminal_transition swallows the error)."""

    def __init__(self, env, lose=()):
        self.env, self.lose, self.queue = env, set(lose), []

    def invoke(self, FunctionName, InvocationType, Payload):  # noqa: N803 - mirrors boto3
        assert FunctionName == hooks.CALLBACK_FN and InvocationType == "Event"
        payload = json.loads(Payload.decode("utf-8"))
        if payload["writer"] in self.lose:
            raise RuntimeError("invoke failed")
        self.queue.append(payload)
        return {"StatusCode": 202}

    def deliver(self, order=None):
        for payload in (order if order is not None else list(self.queue)):
            self.env.deliveries.append(payload)
            self.env.cb.lambda_handler(payload, None)


@pytest.mark.unit
@pytest.mark.aws
class TestUnconfirmedAbortHandsTheJobOverDirectly:
    """An abort that could not confirm the run stopped holds the run's ABORTED callback until the
    stop is confirmed (the status change event, a repeated abort, a details read). The zip download
    job does not wait for that: the abort hands the status to the zip callback at once (writer
    abortUnconfirmed), and the held callback still goes out on confirmation. Both deliveries are
    asynchronous, so they may arrive in either order, and either may be lost; the job is finalized
    exactly once in every case."""

    def _abort_unconfirmed(self, env, lam, table):
        with patch.dict("os.environ", {sp.CALLBACK_REGISTRY_ENV: hooks.REGISTRY}), \
                patch.object(sp, "_default_lambda_client", return_value=lam):
            with hooks._abort(table, [_zip_row()], stop_confirmed=False) as drive:
                assert drive["single"](EXEC)["statusCode"] == 200

    def _late_event(self, env, lam, table):
        with patch.dict("os.environ", {sp.CALLBACK_REGISTRY_ENV: hooks.REGISTRY}), \
                patch.object(sp, "_default_lambda_client", return_value=lam), \
                hooks._reconcile_env(table), \
                patch.object(le.sfn, "describe_execution", return_value=hooks._stopped("ABORTED")):
            le.lambda_handler(hooks._status_event("ABORTED"), MagicMock())

    def _assert_cancelled_once(self, env):
        assert len(_finalize_writes(env)) == 1
        assert _job(env)["status"] == "FAILED"
        assert _job(env)["errorMessage"] == "Cancelled from workflow executions"
        assert _job(env)["cancelledBy"] == "execution"

    def test_direct_hand_over_then_the_late_event(self, zipenv):
        lam = _QueueingLambda(zipenv)
        with hooks._main_table(_zip_row()) as table:
            self._abort_unconfirmed(zipenv, lam, table)
            self._late_event(zipenv, lam, table)
            assert sp.CALLBACK_PENDING_ATTRIBUTE not in hooks._stored(table)
        assert [p["writer"] for p in lam.queue] == ["abortUnconfirmed", "abort"]
        lam.deliver()
        self._assert_cancelled_once(zipenv)

    def test_the_late_event_delivered_before_the_direct_hand_over(self, zipenv):
        lam = _QueueingLambda(zipenv)
        with hooks._main_table(_zip_row()) as table:
            self._abort_unconfirmed(zipenv, lam, table)
            self._late_event(zipenv, lam, table)
        lam.deliver(order=list(reversed(lam.queue)))
        assert [p["writer"] for p in zipenv.deliveries] == ["abort", "abortUnconfirmed"]
        self._assert_cancelled_once(zipenv)

    def test_the_event_lost_the_direct_hand_over_alone_finalizes_the_job(self, zipenv):
        # The still-running container has an archive upload open.
        zipenv.s3.uploads.append({"Bucket": h.AUX, "Key": zipenv.prefix + "x.zip", "UploadId": "u1"})
        lam = _QueueingLambda(zipenv)
        with hooks._main_table(_zip_row()) as table:
            self._abort_unconfirmed(zipenv, lam, table)
            # No status change event, no repeated abort, no details read: the run keeps its held
            # callback, and the job does not wait for it.
            assert hooks._stored(table)[sp.CALLBACK_PENDING_ATTRIBUTE] == "ABORTED"
        lam.deliver()
        assert [p["writer"] for p in zipenv.deliveries] == ["abortUnconfirmed"]
        self._assert_cancelled_once(zipenv)
        # Aborted before the decision, so a CompleteMultipartUpload the container sends after it is
        # refused (one already in flight can still land: test_zip_abort_204.py).
        assert [a for a in zipenv.s3.aborted if "u1" in str(a)]


    def test_the_direct_hand_over_lost_the_late_event_finalizes_the_job(self, zipenv):
        lam = _QueueingLambda(zipenv, lose={"abortUnconfirmed"})
        with hooks._main_table(_zip_row()) as table:
            self._abort_unconfirmed(zipenv, lam, table)  # the abort still answers 200
            assert lam.queue == []
            self._late_event(zipenv, lam, table)
        lam.deliver()
        assert [p["writer"] for p in zipenv.deliveries] == ["abort"]
        self._assert_cancelled_once(zipenv)

    def test_an_archive_that_landed_before_the_hand_over_wins_in_either_order(self, zipenv):
        h.land_archive(zipenv, _job(zipenv), size=77)
        lam = _QueueingLambda(zipenv)
        with hooks._main_table(_zip_row()) as table:
            self._abort_unconfirmed(zipenv, lam, table)
            self._late_event(zipenv, lam, table)
        lam.deliver(order=list(reversed(lam.queue)))
        assert len(_finalize_writes(zipenv)) == 1
        assert _job(zipenv)["status"] == "SUCCEEDED"
        assert _job(zipenv)["archiveSizeBytes"] == 77

    def test_the_container_finishing_first_leaves_both_deliveries_nothing_to_change(self, zipenv):
        lam = _QueueingLambda(zipenv)
        with hooks._main_table(_zip_row()) as table:
            self._abort_unconfirmed(zipenv, lam, table)
            self._late_event(zipenv, lam, table)
        _job(zipenv).update(status="FAILED", errorMessage="The archive job was stopped")
        lam.deliver()
        assert _finalize_writes(zipenv) == []
        assert _job(zipenv)["errorMessage"] == "The archive job was stopped"

    def test_a_confirmed_stop_sends_only_the_ordinary_abort_callback(self, zipenv):
        lam = _QueueingLambda(zipenv)
        with hooks._main_table(_zip_row()) as table, \
                patch.dict("os.environ", {sp.CALLBACK_REGISTRY_ENV: hooks.REGISTRY}), \
                patch.object(sp, "_default_lambda_client", return_value=lam):
            with hooks._abort(table, [_zip_row()]) as drive:
                drive["single"](EXEC)
            assert sp.CALLBACK_PENDING_ATTRIBUTE not in hooks._stored(table)
        assert [p["writer"] for p in lam.queue] == ["abort"]
        lam.deliver()
        self._assert_cancelled_once(zipenv)

    def test_an_abort_that_lost_the_main_row_race_hands_nothing_over(self, zipenv):
        # The end state finished the run between the abort's read and its write.
        lam = _QueueingLambda(zipenv)
        with hooks._main_table(hooks._row(context=ZIP_CONTEXT, status="SUCCEEDED")) as table, \
                patch.dict("os.environ", {sp.CALLBACK_REGISTRY_ENV: hooks.REGISTRY}), \
                patch.object(sp, "_default_lambda_client", return_value=lam):
            with hooks._abort(table, [_zip_row()], stop_confirmed=False) as drive:
                drive["single"](EXEC)
        assert lam.queue == []


@pytest.mark.unit
class TestStopIndependentCallbackTriggers:
    def test_every_opted_out_trigger_is_a_system_trigger(self):
        assert sp.STOP_INDEPENDENT_CALLBACK_TRIGGERS <= set(sp.SYSTEM_TRIGGER_TYPES)

    def test_the_zip_download_callback_is_stop_independent(self):
        assert "Zip-Download" in sp.STOP_INDEPENDENT_CALLBACK_TRIGGERS

    @pytest.mark.parametrize("trigger", ["Manual", "Auto-Trigger", "", None])
    def test_other_runs_get_no_early_callback_and_no_client(self, trigger):
        client = MagicMock()
        row = dict(_zip_row(), triggerType=trigger)
        with patch.dict("os.environ", {sp.CALLBACK_REGISTRY_ENV: hooks.REGISTRY}):
            assert sp.notify_unconfirmed_abort(row, "ABORTED", lambda_client=client) is False
        client.invoke.assert_not_called()

    def test_a_trigger_not_opted_out_keeps_waiting_for_the_confirmation(self):
        client = MagicMock()
        with patch.dict("os.environ", {sp.CALLBACK_REGISTRY_ENV: hooks.REGISTRY}), \
                patch.object(sp, "STOP_INDEPENDENT_CALLBACK_TRIGGERS", frozenset()):
            assert sp.notify_unconfirmed_abort(_zip_row(), "ABORTED", lambda_client=client) is False
        client.invoke.assert_not_called()

    def test_the_early_callback_names_its_writer(self):
        client = MagicMock()
        with patch.dict("os.environ", {sp.CALLBACK_REGISTRY_ENV: hooks.REGISTRY}):
            assert sp.notify_unconfirmed_abort(_zip_row(), "ABORTED", lambda_client=client) is True
        payload = json.loads(client.invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["writer"] == sp.WRITER_ABORT_UNCONFIRMED
        assert payload["executionStatus"] == "ABORTED"
        assert payload["idempotencyKey"] == f"{EXEC}:ABORTED"
