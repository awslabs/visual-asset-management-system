# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The abort route with open copy jobs (#1123) and a stop-independent system trigger (#1122).

An abort whose main StopExecution cannot be confirmed still stops the run's large output copies,
hands the stops it could not decide to the copy engine, writes ABORTED with the terminal callback
held, and only then gives an opted-in trigger its status at once (notify_unconfirmed_abort). The
order matters: the hand-over must see a run whose copies were already stopped and decided, so its
callback never reports ABORTED while a copy is still being written into the asset.

Driven through the real abort route, the real cancel path against moto's DynamoDB and S3, and the
real copy engine reconcile. Each copy job is finalized once across the unconfirmed abort, the
engine's reconcile and the confirming repeat abort, and the hook fires once; the held callback is
sent once on confirmation."""

import contextlib
import json
import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError

from backend.tests.handlers.assets.test_largeObjectCopy_engine import (  # noqa: F401 (fixture)
    REGION, engine, env, loc)
from backend.tests.handlers.assets.test_largeObjectCopy_reconcile import (  # noqa: F401 (fixture)
    EXEC_ARN, MACHINE_ARN, _described, machine)
from backend.tests.handlers.assets.test_largeObjectCopy_race_prior_object import (
    VERSIONED, _DeniedHead, _kwargs, _Run)
from backend.tests.handlers.workflows import test_system_pipeline_hooks as hooks

le = hooks.le  # executionService: the abort route
sp = hooks.sp

ENGINE_FUNCTION = "vams-largeObjectCopy-engine"
RUN_ID = "e2"  # the workflow execution _Run's copy job belongs to
SECOND_ARN = "arn:aws:states:r:1:execution:LargeObjectCopy-x:loc-second"
TRIGGER = "Zip-Download"


def _main_row():
    return hooks._row(execution_id=RUN_ID, trigger=TRIGGER)


def _second_job(env):
    """A second open copy of the same run that Prepare has not reached yet (no upload id)."""
    kwargs = _kwargs(VERSIONED, RUN_ID)
    kwargs.update(relative_key="second.bin", dest={"bucket": VERSIONED, "key": "a1/second.bin"},
                  source={"bucket": "run-bucket", "key": "pipelines/p/j/output/e2/files/second.bin"})
    job = loc.build_job(**kwargs)
    starter = MagicMock(**{"start_execution.return_value": {"executionArn": SECOND_ARN}})
    loc.start_copy_job(env["jobs"], starter, MACHINE_ARN, job)
    return job


def _main_table():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    return ddb.create_table(
        TableName=le.workflow_execution_database_v2,
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "workflowDatabaseId:workflowId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")


class _Calls:
    """One ordered record of the side effects the abort makes."""

    def __init__(self, main_arn):
        self.main_arn = main_arn
        self.events = []
        self.confirm_main_stop = False
        self.callback = MagicMock()
        self.callback.invoke.side_effect = self._callback
        self.handoff = MagicMock()
        self.handoff.invoke.side_effect = self._handoff

    def stop(self, executionArn, **_kwargs):
        if executionArn == self.main_arn:
            self.events.append(("stopMain", executionArn))
            if not self.confirm_main_stop:
                raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "t"}},
                                  "StopExecution")
            return {}
        self.events.append(("stopCopy", executionArn))
        return {}

    def _callback(self, **kwargs):
        payload = json.loads(kwargs["Payload"].decode("utf-8"))
        self.events.append(("callback", payload["writer"]))
        return {}

    def _handoff(self, **kwargs):
        self.events.append(("handoff", json.loads(kwargs["Payload"])["executionArn"]))
        return {}

    def callback_payloads(self):
        return [json.loads(c.kwargs["Payload"].decode("utf-8"))
                for c in self.callback.invoke.call_args_list]

    def handoff_payloads(self):
        return [json.loads(c.kwargs["Payload"]) for c in self.handoff.invoke.call_args_list]


@contextlib.contextmanager
def _abort_route(env, main_table, calls, read_row):
    """The abort route around the real guarded main-row write and the real copy cancel path."""
    real_get_table_name = le.get_table_name

    def table_name(key):
        if key == le.ResourceKeys.LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE:
            return "t-copy-jobs"
        return real_get_table_name(key)

    def table(name):
        if name == le.workflow_execution_database_v2:
            return main_table
        if name == "t-copy-jobs":
            return env["jobs"]
        return MagicMock()

    with patch.object(le.dynamodb, "Table", side_effect=table), \
            patch.object(le, "get_table_name", side_effect=table_name), \
            patch(f"{hooks.ES}.get_execution_main_row", side_effect=lambda _eid: dict(read_row())), \
            patch(f"{hooks.ES}.authorize_abort", return_value=(True, "")), \
            patch(f"{hooks.ES}.get_pipeline_execution_rows", return_value=[]), \
            patch.object(le.sfn, "stop_execution", side_effect=calls.stop), \
            patch.object(le.el, "sfn_execution_has_ended", return_value=False), \
            patch(f"{hooks.ES}.log_actions"), \
            patch.object(le.el, "release_locks_for_execution"), \
            patch.object(le, "_copy_s3_client", return_value=_DeniedHead(env["s3"])), \
            patch.object(le, "lambda_client", calls.handoff), \
            patch.dict(os.environ, {sp.CALLBACK_REGISTRY_ENV: json.dumps({TRIGGER: "vams-zip-cb"}),
                                    loc.ENV_ENGINE_FUNCTION_NAME: ENGINE_FUNCTION}), \
            patch.object(sp, "_default_lambda_client", return_value=calls.callback), \
            patch.object(sp, "STOP_INDEPENDENT_CALLBACK_TRIGGERS", frozenset({TRIGGER})):
        yield lambda: le.abort_execution({"requestContext": {"http": {}}}, RUN_ID)


def _stored(main_table):
    return main_table.get_item(Key={"workflowExecutionId": RUN_ID,
                                    "workflowDatabaseId:workflowId": f"{hooks.WF_DB}:{hooks.WF_ID}"})["Item"]


@pytest.mark.unit
@pytest.mark.aws
def test_an_unconfirmed_abort_finalizes_each_copy_once_and_fires_the_hook_once(env, machine):
    assert le.loc is loc  # the route's cancel path and the engine share one module
    first = _Run(env, VERSIONED)  # open multipart upload, every part copied
    first.complete_by_hand()  # ... and its Complete already landed the object
    # Once the cancel path stopped it, the copy execution reads as cancelled.
    env["sfn"].describe_execution.return_value = _described(
        first.job, status="ABORTED", error=loc.STOP_ERROR_CANCELLED)
    second = _second_job(env)

    main_table = _main_table()
    main_table.put_item(Item=_main_row())
    calls = _Calls(_main_row()["workflow_execution_arn"])

    with patch.object(loc, "mark_cancelled", wraps=loc.mark_cancelled) as cancelled, \
            patch.object(loc, "request_reconcile", wraps=loc.request_reconcile) as handed:
        # 1) The abort whose main stop cannot be confirmed.
        with _abort_route(env, main_table, calls, _main_row) as abort:
            assert abort()["statusCode"] == 200

        stored = _stored(main_table)
        assert stored["executionStatus"] == "ABORTED"
        assert stored[sp.CALLBACK_PENDING_ATTRIBUTE] == "ABORTED"
        # Both copies stopped; the one whose landing the route cannot read is handed to the engine,
        # the one Prepare never reached is CANCELLED here.
        assert loc.get_job_row(env["jobs"], second["jobId"])["status"] == loc.STATUS_CANCELLED
        assert first.row()["status"] == loc.STATUS_RUNNING
        assert calls.handoff_payloads() == [{"action": "reconcile", "executionArn": EXEC_ARN}]
        # Order: main stop, every copy stopped and decided, then the hook last.
        kinds = [kind for kind, _ in calls.events]
        assert kinds[0] == "stopMain"
        assert sorted(arn for kind, arn in calls.events if kind == "stopCopy") == sorted(
            [EXEC_ARN, SECOND_ARN])
        assert kinds[-1] == "callback"
        assert kinds.index("handoff") < kinds.index("callback")
        assert max(i for i, k in enumerate(kinds) if k == "stopCopy") < kinds.index("callback")
        assert [p["writer"] for p in calls.callback_payloads()] == ["abortUnconfirmed"]

        # 2) Lambda delivers the hand-over: the engine decides the first copy (it had landed).
        for payload in calls.handoff_payloads():
            engine.lambda_handler(payload, None)
        first.assert_landed()

        # 3) The repeated abort that confirms the stop: the held callback goes out once, and no
        # copy job is stopped, cancelled or handed over again.
        calls.confirm_main_stop = True
        events_before = len(calls.events)
        with _abort_route(env, main_table, calls, lambda: _stored(main_table)) as abort:
            assert abort()["statusCode"] == 200
        assert [kind for kind, _ in calls.events[events_before:]] == ["stopMain", "callback"]
        assert sp.CALLBACK_PENDING_ATTRIBUTE not in _stored(main_table)

    # Each copy finalized once: one CANCELLED write for the second, one hand-over for the first
    # (whose single completion audit entry and email assert_landed already pinned).
    assert cancelled.call_count == 1
    assert [c.args[2] for c in handed.call_args_list] == [[EXEC_ARN]]
    first.assert_landed()
    assert loc.get_job_row(env["jobs"], second["jobId"])["status"] == loc.STATUS_CANCELLED
    # The hook fired once; the held callback once on confirmation, with the same idempotency key.
    payloads = calls.callback_payloads()
    assert [(p["writer"], p["executionStatus"]) for p in payloads] == [
        ("abortUnconfirmed", "ABORTED"), ("abort", "ABORTED")]
    assert len({p["idempotencyKey"] for p in payloads}) == 1
