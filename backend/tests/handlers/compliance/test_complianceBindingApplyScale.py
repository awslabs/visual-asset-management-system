# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A database binding change applied in the background reads right at any size (D161 follow-up).

The first round of D161 queued each page's evaluations as the page's records were written, then
the apply's continuation after them, on the same queue: on a large database with automatic
evaluation on the continuation waited behind the apply's own evaluations, and an apply with no
progress for 15 minutes read `stalled` although it was only waiting. Now:

- the records phase queues no evaluation, so no continuation of it has any of the apply's own
  evaluations ahead of it;
- the evaluations phase (after every record carries the new binding) queues at most
  EVALUATIONS_PER_RUN per run before it hands over, so at most EVALUATIONS_PER_RUN + PAGE_SIZE - 1
  of its own evaluations are ever ahead of its continuation, whatever the database's size;
- `stalled` is derived from the apply's own messages (a message queued, a run in flight, a
  redelivery coming), never from how long ago it made progress, so an apply whose next message
  waits on a busy queue never reads `stalled`, and one that really stopped still does and resumes;
- each asset is evaluated once, after its record carries the new binding, even when a page of
  evaluations is queued twice.

The queue is a FIFO stand-in (a standard queue delivers in roughly that order) that delivers one
message at a time to the real trigger handler; each run handles one page (the deadline has passed
after the first), and simulated time advances a minute per evaluation delivered. Moto tables built
from the CDK definitions; a stand-in enforcer that allows everything.
"""

import json
import pathlib
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    REAL_TO_UPDATE_EXPR, USER, body_of, claims_for, rest_event, schema_row,
)
from backend.tests.handlers.compliance.test_complianceUnknownDatabase import _stand_in
from handlers.compliance import complianceBindingApply as binding_apply
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceSchemaBindingService as binding_svc
from handlers.compliance import complianceTrigger as trigger

USES_BINDING_APPLY_RECORD = True

DB = "scale-db"
OLD, NEW = "old-schema", "new-schema"
QUEUE = "https://sqs.example/compliance-trigger"
PAGE = 3
PER_RUN = 4
# The most of its own evaluations the apply may have ahead of its next message, whatever its size.
BOUND = PER_RUN + PAGE - 1
EVALUATION_SECONDS = 60
# Spelled out, so this file runs (and fails on its assertions) against code without them; one case
# ties them to the module's.
STALLED_FAILED = "Its last step failed on every delivery; repeat the same change to resume it"
STALLED_NOT_QUEUED = "Its next step could not be queued; repeat the same change to resume it"
STALLED_NOT_DELIVERED = ("Its next step was not delivered within the queue's retention period; "
                         "repeat the same change to resume it")
CANCELLED_AUTO_EVAL_OFF = "Automatic evaluation was turned off"
COMPLIANCE_FUNCTIONS = (pathlib.Path(__file__).resolve().parents[4] / "infra" / "lib"
                        / "lambdaBuilder" / "complianceFunctions.ts")


class FifoQueue:
    """The compliance trigger's queue, delivering in arrival order. For every apply message it is
    given it records how many evaluations of the database were ahead of it, and in which phase."""

    def __init__(self):
        self.messages = []
        self.ahead = []
        self.lost = []

    def _append(self, body):
        payload = json.loads(body)
        if payload.get("vamsInternalAction") == binding_apply.APPLY_ACTION:
            record = binding_apply.get_record(DB) or {}
            evaluations = sum(1 for message in self.messages
                              if json.loads(message["body"]).get("assetId")
                              and json.loads(message["body"]).get("databaseId") == DB)
            # While any record may still carry the binding before the change, the apply is
            # `applying`: that is its records phase.
            phase = "records" if record.get("applyStatus") == "applying" else "evaluations"
            self.ahead.append((phase, evaluations))
        self.messages.append({"body": body, "receives": 0})

    def send_message(self, QueueUrl, MessageBody, **kwargs):
        self._append(MessageBody)
        return {"MessageId": str(len(self.messages))}

    def send_message_batch(self, QueueUrl, Entries):
        for entry in Entries:
            self._append(entry["MessageBody"])
        return {"Failed": []}

    def change_message_visibility(self, **kwargs):
        return {}


class World:

    def __init__(self, assets=30, auto_eval=True):
        self.asset_ids = [f"a{index:03d}" for index in range(assets)]
        self.override = self.asset_ids[5]
        self.quarantined = self.asset_ids[3]
        self.auto_eval = auto_eval

    def __enter__(self):
        self._mock = mock_aws()
        self._mock.start()
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        self.patches, self.t = _cdkTables.table_doubles(resource, (store, binding_svc, trigger))
        self.queue = FifoQueue()
        self.evaluated = []
        self.patches += [
            patch.object(binding_svc, "CasbinEnforcer", return_value=_stand_in({"*"})),
            patch.object(binding_svc, "request_to_claims", claims_for(USER)),
            patch.object(binding_svc, "sqs_client", self.queue, create=True),
            patch.object(binding_svc, "trigger_queue_url", QUEUE, create=True),
            patch.object(trigger, "sqs_client", self.queue),
            patch.object(trigger, "trigger_queue_url", QUEUE),
            patch.object(trigger, "trigger_evaluation", side_effect=self._evaluate),
            patch.object(store, "to_update_expr", REAL_TO_UPDATE_EXPR),
            patch.object(binding_apply, "PAGE_SIZE", PAGE),
            patch.object(binding_apply, "EVALUATIONS_PER_RUN", PER_RUN, create=True),
        ]
        real_run = binding_apply.run

        def one_page_run(database_id, apply_id, run_id, deadline, **kwargs):
            # Each run handles one page: its deadline has passed after the first.
            return real_run(database_id, apply_id, run_id, 0.0,
                            **dict(kwargs, clock=lambda: 1.0, page_size=PAGE))
        self.patches.append(patch.object(binding_apply, "run", side_effect=one_page_run))
        for active in self.patches:
            active.start()
        for name in (OLD, NEW):
            self.t["complianceSchemaStorage"].put_item(Item=schema_row(name, database_id=DB))
        self.t["databaseStorage"].put_item(Item={"databaseId": DB, "complianceSchemaName": OLD,
                                                 "complianceAutoEval": self.auto_eval})
        for asset_id in self.asset_ids:
            self.t["assetStorage"].put_item(Item={"databaseId": DB, "assetId": asset_id,
                                                  "assetName": asset_id})
            row = {"databaseId": DB, "assetId": asset_id, "complianceState": "compliant",
                   "schemaName": OLD, "schemaSource": "database", "assetName": asset_id,
                   "lastEvaluatedAt": "2026-10-01T00:00:00+00:00", "lastEvaluationId": "ev-0",
                   "updatedAt": "2026-10-01T00:00:00+00:00", "recordRevision": 1}
            if asset_id == self.quarantined:
                row.update(complianceState="quarantined", quarantineReason="units missing")
            if asset_id == self.override:
                row.update(schemaSource="asset", schemaName=NEW)
            self.t["complianceAssetStateStorage"].put_item(Item=row)
        self.start = datetime.now(timezone.utc)
        self.elapsed = 0
        return self

    def __exit__(self, *exc):
        for active in reversed(self.patches):
            active.stop()
        self._mock.stop()

    def _evaluate(self, database_id, asset_id, schema_name):
        """The trigger's evaluation: recorded with the record as it was, then claimed as an
        evaluation's start claims it."""
        record = self.t["complianceAssetStateStorage"].get_item(
            Key={"databaseId": database_id, "assetId": asset_id})["Item"]
        self.evaluated.append((asset_id, schema_name, record.get("schemaName")))
        self.t["complianceAssetStateStorage"].update_item(
            Key={"databaseId": database_id, "assetId": asset_id},
            UpdateExpression="SET lastEvaluatedAt = :t, lastEvaluationId = :e",
            ExpressionAttributeValues={":t": store.now_iso(), ":e": f"ev-{len(self.evaluated)}"})

    def bind(self, schema=NEW, auto_eval=None):
        body = {"schemaName": schema}
        if auto_eval is not None:
            body["complianceAutoEval"] = auto_eval
        response = binding_svc.lambda_handler(
            rest_event("PUT", f"/compliance/bind/{DB}", {"databaseId": DB}, body=body),
            MagicMock())
        return response["statusCode"], body_of(response)

    def apply_record(self):
        return binding_apply.get_record(DB)

    def now(self):
        return self.start + timedelta(seconds=self.elapsed)

    def deliver(self, keep_failed=True):
        """Deliver the next message to the trigger, alone in its batch; a failed one goes back to
        the end of the queue (its redelivery) unless that was its last delivery."""
        message = self.queue.messages.pop(0)
        message["receives"] += 1
        payload = json.loads(message["body"])
        event = {"Records": [{"messageId": "m", "body": message["body"], "attributes": {
            "ApproximateReceiveCount": str(message["receives"])}}]}
        result = trigger.lambda_handler(event, MagicMock(
            get_remaining_time_in_millis=lambda: 300_000))
        if result["batchItemFailures"]:
            if keep_failed and message["receives"] < binding_apply.TRIGGER_QUEUE_MAX_RECEIVE_COUNT:
                self.queue.messages.append(message)
            else:
                self.queue.lost.append(message)
        if payload.get("assetId"):
            self.elapsed += EVALUATION_SECONDS
        return payload

    def drain(self, check=None, max_deliveries=5000):
        for _ in range(max_deliveries):
            if not self.queue.messages:
                return
            self.deliver()
            if check:
                check()
        raise AssertionError("the queue did not drain")


def _bound_assets(world):
    return sorted(asset_id for asset_id in world.asset_ids if asset_id != world.override)


@pytest.mark.unit
class TestAnApplyOfAnySizeWithAutomaticEvaluation:

    @pytest.mark.parametrize("assets", [30, 60], ids=["30 assets", "60 assets"])
    def test_no_continuation_waits_behind_more_than_a_run_of_its_own_evaluations(self, assets):
        with World(assets) as world:
            assert world.bind(NEW)[0] == 202
            world.drain()
            records = [ahead for phase, ahead in world.queue.ahead if phase == "records"]
            evaluations = [ahead for phase, ahead in world.queue.ahead if phase == "evaluations"]
            # The records phase took several runs and queued no evaluation ahead of any of them.
            assert len(records) >= assets // PAGE - 1
            assert records and max(records) == 0
            # The evaluations phase never had more than one run's share ahead, whatever the size.
            assert evaluations and max(evaluations) <= BOUND

    def test_it_never_reads_stalled_while_its_message_waits_and_finishes_without_a_resume(self):
        with World(60) as world:
            world.bind(NEW)

            def never_stalled():
                record = world.apply_record()
                read = binding_apply.answer(record, now=world.now())
                assert read["status"] != "stalled", world.elapsed
                assert read.get("evaluationStatus") != "stalled", world.elapsed
            world.drain(check=never_stalled)
            # Well past the first round's 15-minute bound, with no resume.
            assert world.elapsed >= 15 * 60 * 3
            record = world.apply_record()
            assert (record["applyStatus"], record.get("phase"), record["evaluationStatus"],
                    record.get("resumes", 0)) == ("applied", None, "queued", 0)

    def test_each_asset_is_evaluated_once_after_its_record_carries_the_binding(self):
        with World(30) as world:
            world.bind(NEW)
            world.drain()
            assert sorted(asset_id for asset_id, _, _ in world.evaluated) == _bound_assets(world)
            assert {(schema, stored) for _, schema, stored in world.evaluated} == {(NEW, NEW)}

    def test_a_page_of_evaluations_queued_twice_is_evaluated_once(self):
        with World(30) as world:
            world.bind(NEW)
            while world.apply_record()["applyStatus"] == "applying":
                world.deliver()
            real_save = binding_apply._save_progress
            failed = []

            def save_fails_once(record, cursor, counts, continuing=False, **kwargs):
                if record.get("phase") == "evaluations" and not failed:
                    failed.append(True)
                    raise ClientError({"Error": {"Code": "ThrottlingException"}}, "UpdateItem")
                return real_save(record, cursor, counts, continuing, **kwargs)
            with patch.object(binding_apply, "_save_progress", side_effect=save_fails_once):
                world.drain()
            assert failed
            # The failed run's page was queued again on its redelivery; each asset evaluated once.
            assert sorted(asset_id for asset_id, _, _ in world.evaluated) == _bound_assets(world)

    def test_without_automatic_evaluation_nothing_is_queued_and_it_ends_applied(self):
        with World(30, auto_eval=False) as world:
            world.bind(NEW)
            world.drain()
            record = world.apply_record()
            assert (record["applyStatus"], record.get("phase"), record.get("evaluationStatus")) == (
                "applied", None, None)
            assert world.evaluated == []
            assert [phase for phase, _ in world.queue.ahead] == ["records"] * len(
                world.queue.ahead)


@pytest.mark.unit
class TestAnApplyThatReallyStopped:

    def test_a_run_that_fails_on_every_delivery_reads_stalled_and_resumes(self):
        with World(9) as world:
            world.bind(NEW)
            with patch.object(binding_apply, "_save_progress", side_effect=ClientError(
                    {"Error": {"Code": "InternalServerError"}}, "UpdateItem")):
                world.deliver()
                # A redelivery comes: not stalled, even 20 minutes on.
                later = datetime.now(timezone.utc) + timedelta(minutes=20)
                assert binding_apply.status(world.apply_record(), now=later) == "applying"
                world.deliver()
                world.deliver()
            assert world.queue.messages == [] and len(world.queue.lost) == 1
            record = world.apply_record()
            # Its last run may still be running for a while; once it cannot be, the apply stopped.
            assert binding_apply.status(record) == "applying"
            later = datetime.now(timezone.utc) + timedelta(minutes=10)
            assert binding_apply.stop_reason(record, now=later) == STALLED_FAILED
            assert binding_apply.status(record, now=later) == "stalled"
            # Ten minutes on (both times moved back), the same change resumes it from its cursor
            # and it finishes.
            shift = timedelta(minutes=10)
            world.t["complianceEvaluationStorage"].update_item(
                Key=binding_apply.record_key(DB),
                UpdateExpression="SET runStartedAt = :s, nextMessageAt = :q",
                ExpressionAttributeValues={
                    ":s": (datetime.fromisoformat(record["runStartedAt"]) - shift).isoformat(),
                    ":q": (datetime.fromisoformat(record["nextMessageAt"]) - shift).isoformat()})
            status, body = world.bind(NEW)
            assert (status, body["resumed"], body["applyStatus"]) == (202, True, "applying")
            world.drain()
            record = world.apply_record()
            assert (record["applyStatus"], record["evaluationStatus"], record["resumes"]) == (
                "applied", "queued", 1)

    def test_a_first_message_that_could_not_be_queued_reads_stalled_at_once_and_resumes(self):
        with World(9) as world:
            with patch.object(world.queue, "send_message", side_effect=RuntimeError("down")):
                status, body = world.bind(NEW)
            assert (status, body["applyStatus"], body["apply"]["reason"]) == (
                202, "stalled", STALLED_NOT_QUEUED)
            assert world.queue.messages == []
            status, body = world.bind(NEW)
            assert (status, body["resumed"], body["applyStatus"]) == (202, True, "applying")
            world.drain()
            assert world.apply_record()["applyStatus"] == "applied"

    def test_a_stopped_evaluations_phase_reads_stalled_without_holding_the_database(self):
        with World(9) as world:
            world.bind(NEW)
            while world.apply_record()["applyStatus"] == "applying":
                world.deliver()
            # The evaluations phase's message is lost (its last delivery failed).
            world.queue.messages.clear()
            stopped = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
            world.t["complianceEvaluationStorage"].update_item(
                Key=binding_apply.record_key(DB),
                UpdateExpression="SET runStartedAt = :s, runReceiveCount = :n REMOVE nextMessageAt",
                ExpressionAttributeValues={":s": stopped, ":n": 3})
            response = binding_svc.lambda_handler(rest_event(
                "GET", f"/compliance/bind/{DB}", {"databaseId": DB}), MagicMock())
            read = body_of(response)["apply"]
            assert (read["status"], read["evaluationStatus"]) == ("applied", "stalled")
            record = world.apply_record()
            assert binding_apply.status(record) == "applied"
            assert binding_apply.evaluation_status(record) == "stalled"
            assert binding_apply.answer(record)["evaluationReason"] == STALLED_FAILED
            # The same change resumes it.
            status, body = world.bind(NEW)
            assert (status, body["resumed"]) == (202, True)
            world.drain()
            record = world.apply_record()
            assert record["evaluationStatus"] == "queued"
            assert sorted(asset_id for asset_id, _, _ in world.evaluated) == _bound_assets(world)


@pytest.mark.unit
class TestTheEvaluationsPhaseGivesWay:

    def test_another_change_goes_through_and_the_old_evaluations_are_skipped(self):
        with World(12) as world:
            world.bind(NEW)
            while world.apply_record()["applyStatus"] == "applying":
                world.deliver()
            world.deliver()  # one run of the evaluations phase: its share is queued
            old_apply = world.apply_record()["applyId"]
            assert world.bind(OLD)[0] == 202
            assert world.apply_record()["applyId"] != old_apply
            world.drain()
            # The evaluations the first change queued were skipped; the second change's ran,
            # each under the schema now bound.
            assert {stored for _, _, stored in world.evaluated} == {OLD}
            assert sorted(asset_id for asset_id, _, _ in world.evaluated) == _bound_assets(world)

    def test_turning_automatic_evaluation_off_cancels_it(self):
        with World(12) as world:
            world.bind(NEW)
            while world.apply_record()["applyStatus"] == "applying":
                world.deliver()
            assert world.bind(NEW, auto_eval=False)[0] == 200
            world.drain()
            record = world.apply_record()
            assert (record["evaluationStatus"], record["evaluationEndReason"]) == (
                "cancelled", CANCELLED_AUTO_EVAL_OFF)
            assert world.evaluated == []


NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def _record(**fields):
    record = {"applyId": "a", "runId": "r", "phase": "records", "applyStatus": "applying"}
    record.update({key: (value.isoformat() if isinstance(value, datetime) else value)
                   for key, value in fields.items()})
    return record


@pytest.mark.unit
@pytest.mark.parametrize("fields, minutes_later, stopped", [
    ({"nextMessageAt": NOW}, 600, None),
    ({"nextMessageAt": NOW, "runStartedAt": NOW - timedelta(minutes=1)}, 600, None),
    ({"nextMessageAt": NOW}, 4 * 24 * 60 + 1, STALLED_NOT_DELIVERED),
    ({"runStartedAt": NOW, "runReceiveCount": 3}, 5, None),
    ({"runStartedAt": NOW, "runReceiveCount": 1}, 45, None),
    ({"runStartedAt": NOW, "runReceiveCount": 2}, 600, None),
    ({"runStartedAt": NOW, "runReceiveCount": 3}, 7, STALLED_FAILED),
    ({}, 0, STALLED_NOT_QUEUED),
], ids=["message waiting 10 h", "message queued by the run in flight", "message older than the "
        "queue keeps one", "run in flight", "failed run, redelivery coming", "failed run, second "
        "delivery waiting", "last delivery failed", "no message queued"])
def test_stopped_is_decided_on_the_apply_s_own_messages(fields, minutes_later, stopped):
    record = _record(**fields)
    assert binding_apply.stop_reason(record, NOW + timedelta(minutes=minutes_later)) == stopped
    assert (binding_apply.status(record, NOW + timedelta(minutes=minutes_later)) == "stalled") == (
        stopped is not None)


def test_the_timings_are_the_trigger_s_and_its_queue_s():
    source = COMPLIANCE_FUNCTIONS.read_text(encoding="utf-8")

    def constant(name):
        return re.search(rf"export const {name} = ([^;]+);", source).group(1).strip()
    assert constant("COMPLIANCE_TRIGGER_TIMEOUT") == "Duration.minutes(5)"
    assert binding_apply.TRIGGER_TIMEOUT_SECONDS == 5 * 60
    assert constant("COMPLIANCE_TRIGGER_QUEUE_VISIBILITY_TIMEOUT") == "Duration.minutes(30)"
    assert binding_apply.TRIGGER_QUEUE_VISIBILITY_TIMEOUT_SECONDS == 30 * 60
    assert constant("COMPLIANCE_TRIGGER_QUEUE_MAX_RECEIVE_COUNT") == "3"
    assert binding_apply.TRIGGER_QUEUE_MAX_RECEIVE_COUNT == 3
    # The queue sets no retention period: SQS keeps a message 4 days.
    queue = source[source.index('new sqs.Queue(scope, "ComplianceTriggerQueue"'):]
    queue = queue[:queue.index("});")]
    assert "retentionPeriod" not in queue
    assert binding_apply.TRIGGER_QUEUE_RETENTION_SECONDS == 4 * 24 * 3600


def test_the_reasons_are_the_module_s():
    assert (binding_apply.STALLED_FAILED, binding_apply.STALLED_NOT_QUEUED,
            binding_apply.STALLED_NOT_DELIVERED, binding_apply.CANCELLED_AUTO_EVAL_OFF) == (
        STALLED_FAILED, STALLED_NOT_QUEUED, STALLED_NOT_DELIVERED, CANCELLED_AUTO_EVAL_OFF)
