# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A database binding change applied in the background reads right at any size (D161 follow-ups).

The change runs in the compliance job Lambda from its own queue; the evaluations it queues go to
the compliance trigger's queue, which every database's automatic evaluations share:

- no step of the apply ever waits behind an evaluation: its continuations are on the job queue,
  which carries no evaluation;
- the evaluations phase queues a share only while fewer than EVALUATION_BACKLOG_LIMIT messages wait
  on the trigger's queue, so the trigger's queue never holds more than about that many plus one
  share, and another database's change never waits behind more, whatever the apply's size; while
  the queue is busy the phase hands over to a delayed continuation and never reads stopped;
- `stalled` is derived from the apply's own messages (a message queued, a run in flight, a
  redelivery coming), never from how long ago it made progress, so an apply whose next message
  waits never reads `stalled`, and one that really stopped still does and resumes;
- each asset is evaluated once, after its record carries the new binding, even when a share of
  evaluations is queued twice.

Both queues are FIFO stand-ins (a standard queue delivers in roughly that order) that deliver one
message at a time: the job queue to the real job handler, the trigger's queue to the real trigger
handler. Each job run handles one page (its deadline has passed after the first); simulated time
advances a minute per evaluation delivered, and a delayed job message is delivered once its delay
has passed. Moto tables built from the CDK definitions; a stand-in enforcer that allows everything.
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
from handlers.compliance import complianceJobs as jobs
from handlers.compliance import complianceSchemaBindingService as binding_svc
from handlers.compliance import complianceTrigger as trigger

USES_BINDING_APPLY_RECORD = True

DB = "scale-db"
OLD, NEW = "old-schema", "new-schema"
QUEUE = "https://sqs.example/compliance-trigger"
JOB_QUEUE = "https://sqs.example/compliance-jobs"
PAGE = 3
PER_SHARE = 4
BACKLOG_LIMIT = 5
# The most messages the trigger's queue may hold, whatever the apply's size: a share is queued only
# below the limit, and a share is at most PER_SHARE + PAGE - 1 evaluations.
BOUND = BACKLOG_LIMIT - 1 + PER_SHARE + PAGE - 1
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


class FifoQueues:
    """The job queue and the trigger's queue, each delivering in arrival order. A job message may
    carry a delay (seconds of simulated time). For every apply message it records the evaluations
    on its own queue ahead of it, and for every evaluation queued the trigger queue's depth after
    it."""

    def __init__(self, world):
        self.world = world
        self.jobs = []
        self.evaluations = []
        self.lost = []
        self.job_ahead = []
        self.depths = []

    @staticmethod
    def _is_evaluation(body):
        return bool(json.loads(body).get("assetId"))

    def send_message(self, QueueUrl, MessageBody, DelaySeconds=0, **kwargs):
        message = {"body": MessageBody, "receives": 0,
                   "readyAt": self.world.elapsed + int(DelaySeconds or 0)}
        if QueueUrl == JOB_QUEUE:
            if json.loads(MessageBody).get("vamsInternalAction") == binding_apply.APPLY_ACTION:
                self.job_ahead.append(sum(1 for m in self.jobs if self._is_evaluation(m["body"])))
            self.jobs.append(message)
        else:
            assert QueueUrl == QUEUE, QueueUrl
            self.evaluations.append(message)
            self.depths.append(len(self.evaluations))
        return {"MessageId": "m"}

    def send_message_batch(self, QueueUrl, Entries):
        assert QueueUrl == QUEUE, QueueUrl
        for entry in Entries:
            self.send_message(QueueUrl, entry["MessageBody"])
        return {"Failed": []}

    def get_queue_attributes(self, QueueUrl, AttributeNames):
        assert QueueUrl == QUEUE, QueueUrl
        return {"Attributes": {"ApproximateNumberOfMessages": str(len(self.evaluations))}}

    def change_message_visibility(self, **kwargs):
        return {}

    @property
    def messages(self):
        return self.jobs + self.evaluations


class World:

    def __init__(self, assets=30, auto_eval=True):
        self.asset_ids = [f"a{index:03d}" for index in range(assets)]
        self.override = self.asset_ids[5]
        self.quarantined = self.asset_ids[3]
        self.auto_eval = auto_eval
        self.elapsed = 0

    def __enter__(self):
        self._mock = mock_aws()
        self._mock.start()
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        self.patches, self.t = _cdkTables.table_doubles(resource,
                                                        (store, binding_svc, trigger, jobs))
        self.queue = FifoQueues(self)
        self.evaluated = []
        self.patches += [
            patch.object(binding_svc, "CasbinEnforcer", return_value=_stand_in({"*"})),
            patch.object(binding_svc, "request_to_claims", claims_for(USER)),
            patch.object(binding_svc, "sqs_client", self.queue, create=True),
            patch.object(binding_svc, "trigger_queue_url", QUEUE, create=True),
            patch.object(binding_svc, "job_queue_url", JOB_QUEUE, create=True),
            patch.object(trigger, "sqs_client", self.queue),
            patch.object(trigger, "trigger_queue_url", QUEUE),
            patch.object(trigger, "job_queue_url", JOB_QUEUE, create=True),
            patch.object(trigger, "trigger_evaluation", side_effect=self._evaluate),
            patch.object(jobs, "sqs_client", self.queue, create=True),
            patch.object(jobs, "trigger_queue_url", QUEUE, create=True),
            patch.object(jobs, "job_queue_url", JOB_QUEUE, create=True),
            patch.object(store, "to_update_expr", REAL_TO_UPDATE_EXPR),
            patch.object(binding_apply, "PAGE_SIZE", PAGE),
            patch.object(binding_apply, "EVALUATIONS_PER_SHARE", PER_SHARE, create=True),
            patch.object(binding_apply, "EVALUATION_BACKLOG_LIMIT", BACKLOG_LIMIT, create=True),
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

    def _next(self):
        """The queue and message delivered next: a job message whose delay has passed, else the
        next evaluation, else the first job message once its delay passes."""
        ready = [m for m in self.queue.jobs if m["readyAt"] <= self.elapsed]
        if ready:
            return self.queue.jobs, ready[0]
        if self.queue.evaluations:
            return self.queue.evaluations, self.queue.evaluations[0]
        if self.queue.jobs:
            message = min(self.queue.jobs, key=lambda m: m["readyAt"])
            self.elapsed = max(self.elapsed, message["readyAt"])
            return self.queue.jobs, message
        return None, None

    def deliver(self, keep_failed=True):
        """Deliver the next message alone in its batch, to the job handler or the trigger; a failed
        one goes back to the end of its queue (its redelivery) unless that was its last
        delivery."""
        source, message = self._next()
        source.remove(message)
        message["receives"] += 1
        payload = json.loads(message["body"])
        event = {"Records": [{"messageId": "m", "body": message["body"], "attributes": {
            "ApproximateReceiveCount": str(message["receives"])}}]}
        handler = jobs.lambda_handler if source is self.queue.jobs else trigger.lambda_handler
        result = handler(event, MagicMock(get_remaining_time_in_millis=lambda: 300_000))
        if result["batchItemFailures"]:
            if keep_failed and message["receives"] < binding_apply.JOB_QUEUE_MAX_RECEIVE_COUNT:
                message["readyAt"] = self.elapsed
                source.append(message)
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
        raise AssertionError("the queues did not drain")


def _bound_assets(world):
    return sorted(asset_id for asset_id in world.asset_ids if asset_id != world.override)


@pytest.mark.unit
class TestAnApplyOfAnySizeWithAutomaticEvaluation:

    @pytest.mark.parametrize("assets", [30, 60], ids=["30 assets", "60 assets"])
    def test_no_step_waits_behind_an_evaluation_and_the_trigger_queue_stays_bounded(self, assets):
        with World(assets) as world:
            assert world.bind(NEW)[0] == 202
            world.drain()
            # Every continuation went to the job queue, which never held an evaluation.
            assert len(world.queue.job_ahead) >= assets // PAGE
            assert max(world.queue.job_ahead) == 0
            # The trigger's queue never held more than the limit and one share, whatever the
            # size, so another database's change never waited behind more.
            assert world.queue.depths and max(world.queue.depths) <= BOUND
            assert len(world.queue.depths) == len(_bound_assets(world))

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
            assert world.queue.depths == []


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



@pytest.mark.unit
class TestARunawayChainStopsAtItsRunLimit:
    """With Lambda's recursive-loop detection off on the job Lambda, an apply whose chain never
    ends (here: a records page that always reports more pages) is stopped by its own run limit."""

    def test_the_chain_ends_at_the_limit_and_reads_stopped(self):
        with World(9, auto_eval=False) as world:
            real_page = binding_apply._bind_page

            def endless(database_id, record, cursor, page_size, counts):
                page = real_page(database_id, record, cursor, page_size, counts)
                page.cursor = page.cursor or {"databaseId": database_id, "assetId": "a000"}
                return page
            with patch.dict(binding_apply._RECORD_PAGES, {binding_apply.OPERATION_CHANGE: endless}):
                assert world.bind(NEW)[0] == 202
                limit = world.apply_record()["runLimit"]
                world.drain()
            record = world.apply_record()
            assert world.queue.jobs == []
            # Every run up to the limit ran; the one past it ran nothing and queued nothing.
            assert (record["runs"], record["stopReason"]) == (
                limit + 1, binding_apply.STALLED_RUN_LIMIT)
            assert binding_apply.status(record) == "stalled"
            assert len(world.queue.job_ahead) == limit + 1


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
    ({"nextMessageAt": NOW, "stopReason": "It ran more steps than a change of its size needs, so "
      "it was stopped; repeat the same change to resume it"}, 0,
     "It ran more steps than a change of its size needs, so it was stopped; repeat the same "
     "change to resume it"),
], ids=["message waiting 10 h", "message queued by the run in flight", "message older than the "
        "queue keeps one", "run in flight", "failed run, redelivery coming", "failed run, second "
        "delivery waiting", "last delivery failed", "no message queued",
        "stopped at its run limit"])
def test_stopped_is_decided_on_the_apply_s_own_messages(fields, minutes_later, stopped):
    record = _record(**fields)
    assert binding_apply.stop_reason(record, NOW + timedelta(minutes=minutes_later)) == stopped
    assert (binding_apply.status(record, NOW + timedelta(minutes=minutes_later)) == "stalled") == (
        stopped is not None)


def test_the_timings_are_the_job_lambda_s_and_its_queue_s():
    source = COMPLIANCE_FUNCTIONS.read_text(encoding="utf-8")

    def constant(name):
        return re.search(rf"export const {name} = ([^;]+);", source).group(1).strip()
    assert constant("COMPLIANCE_JOB_TIMEOUT") == "Duration.minutes(5)"
    assert binding_apply.JOB_TIMEOUT_SECONDS == 5 * 60
    assert constant("COMPLIANCE_JOB_QUEUE_VISIBILITY_TIMEOUT") == "Duration.minutes(30)"
    assert binding_apply.JOB_QUEUE_VISIBILITY_TIMEOUT_SECONDS == 30 * 60
    assert constant("COMPLIANCE_JOB_QUEUE_MAX_RECEIVE_COUNT") == "3"
    assert binding_apply.JOB_QUEUE_MAX_RECEIVE_COUNT == 3
    # The queue sets no retention period: SQS keeps a message 4 days.
    queue = source[source.index('new sqs.Queue(scope, "ComplianceJobQueue"'):]
    queue = queue[:queue.index("});")]
    assert "retentionPeriod" not in queue
    assert binding_apply.JOB_QUEUE_RETENTION_SECONDS == 4 * 24 * 3600
    # The job Lambda reads its queue: the timeout above is its own.
    job = source[source.index("export function buildComplianceJobFunction"):]
    assert "timeout: COMPLIANCE_JOB_TIMEOUT," in job[:job.index("});")]


def test_a_continuation_delayed_while_the_trigger_queue_is_busy_is_waiting_not_stopped():
    queued = NOW - timedelta(days=4) + timedelta(seconds=600)
    record = _record(nextMessageAt=queued, nextMessageDelay=900)
    assert binding_apply.stop_reason(record, NOW) is None
    # Its delay does not count against the queue's retention: 600 s queued + 900 s delay.
    assert binding_apply.stop_reason(record, NOW + timedelta(seconds=1499)) is None
    assert binding_apply.stop_reason(record, NOW + timedelta(seconds=1501)) == STALLED_NOT_DELIVERED


def test_the_reasons_are_the_module_s():
    assert (binding_apply.STALLED_FAILED, binding_apply.STALLED_NOT_QUEUED,
            binding_apply.STALLED_NOT_DELIVERED, binding_apply.CANCELLED_AUTO_EVAL_OFF) == (
        STALLED_FAILED, STALLED_NOT_QUEUED, STALLED_NOT_DELIVERED, CANCELLED_AUTO_EVAL_OFF)
