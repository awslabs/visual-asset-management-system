# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A database binding change is applied in the background, for any database size (D161).

A bind of about 2,500 assets ran past the API's 29 s timeout: the request wrote every record
itself. Now the request saves the binding, audits it as the caller and answers `202` with the
number of assets; the compliance job Lambda applies the change page by page
(`complianceBindingApply.run`), as it moves a deleted database's data. Covered on moto tables built
from the CDK definitions, with a stand-in enforcer that allows everything:

- the route answers before any record changes, with the count, and queues one message carrying
  ids only;
- bind, change and removal applied across several runs (one page each), the cursor saved and
  resumed; quarantined assets kept quarantined and marked (D118, D158);
- a page applied twice (a redelivered message) writes nothing twice and audits nothing twice;
- a message is acted on only while the stored apply is `applying` with its apply and run ids; a
  message naming another schema or actor changes nothing of what is applied;
- every per-asset entry names the caller; the end writes one entry with the totals;
- `409` for another change while applying (and for Evaluate All); an asset override, a release and
  an evaluation of one asset still run; a new asset gets the new binding and is left alone;
- a database delete during an apply: the move wins, nothing more is written under the live id;
- a stalled apply reads `stalled` and resumes when the same change is requested again;
- automatic evaluation queues each asset once; turning it on for the schema in force queues the
  pending assets in the background;
- the binding read reports the apply; no state answer returns the apply's stamp;
- the first message and every continuation go to the job queue, the evaluations to the trigger's
  queue; an apply message found on the trigger's queue is logged and dropped, and nothing runs or
  is sent for it; a run past the apply's run limit runs nothing and the apply reads stopped until
  the same change resumes it with a fresh count.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from backend.tests.handlers.compliance import _bindingApply, _cdkTables
from backend.tests.handlers.compliance._harness import (
    REAL_TO_UPDATE_EXPR, USER, body_of, claims_for, rest_event, schema_row,
)
from backend.tests.handlers.compliance.test_complianceUnknownDatabase import _stand_in
from common.compliance import databaseRetirement as retirement
from handlers.compliance import complianceBindingApply as binding_apply
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceJobs as jobs
from handlers.compliance import complianceQuarantineService as quarantine_svc
from handlers.compliance import complianceSchemaBindingService as binding_svc
from handlers.compliance import complianceTrigger as trigger

# The apply record and the retirement record live in the moto tables (conftest).
USES_BINDING_APPLY_RECORD = True
USES_RETIREMENT_RECORD = True

DB = "apply-db"
OLD, NEW = "old-schema", "new-schema"
QUEUE = "https://sqs.example/compliance-trigger"
JOB_QUEUE = "https://sqs.example/compliance-jobs"
# Literal so the tests do not lean on the module's own constants (one case ties them).
APPLYING_MESSAGE = ("The compliance schema binding of this database is still being applied; "
                    "try again when it finishes")
STALLED_MESSAGE = ("The compliance schema binding of this database stopped while it was being "
                   "applied; repeat the same change to resume it")
ASSETS = [f"a{index:02d}" for index in range(7)]
QUARANTINED = "a03"
OVERRIDE = "a05"


class World:

    def __init__(self, auto_eval=False, bound=OLD):
        self.auto_eval = auto_eval
        self.bound = bound

    def __enter__(self):
        self._mock = mock_aws()
        self._mock.start()
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        self.patches, self.t = _cdkTables.table_doubles(
            resource, (store, binding_svc, evaluate_svc, quarantine_svc, trigger, jobs))
        self.sqs = MagicMock(name="sqs")
        self.sqs.send_message_batch.side_effect = lambda **kwargs: {"Failed": []}
        for module in (binding_svc, evaluate_svc, quarantine_svc):
            self.patches += [
                patch.object(module, "CasbinEnforcer", return_value=_stand_in({"*"})),
                patch.object(module, "request_to_claims", claims_for(USER)),
            ]
        self.patches += [
            patch.object(binding_svc, "sqs_client", self.sqs, create=True),
            patch.object(binding_svc, "trigger_queue_url", QUEUE, create=True),
            patch.object(binding_svc, "job_queue_url", JOB_QUEUE, create=True),
            patch.object(trigger, "sqs_client", self.sqs),
            patch.object(trigger, "trigger_queue_url", QUEUE),
            patch.object(jobs, "sqs_client", self.sqs, create=True),
            patch.object(jobs, "trigger_queue_url", QUEUE, create=True),
            patch.object(jobs, "job_queue_url", JOB_QUEUE, create=True),
            patch.object(store, "to_update_expr", REAL_TO_UPDATE_EXPR),
        ]
        for active in self.patches:
            active.start()
        for name in (OLD, NEW):
            self.t["complianceSchemaStorage"].put_item(Item=schema_row(name, database_id=DB))
        database = {"databaseId": DB}
        if self.bound:
            database.update(complianceSchemaName=self.bound, complianceAutoEval=self.auto_eval)
        self.t["databaseStorage"].put_item(Item=database)
        for asset_id in ASSETS:
            self.t["assetStorage"].put_item(Item={"databaseId": DB, "assetId": asset_id,
                                                  "assetName": f"Asset {asset_id}"})
            if not self.bound:
                continue
            row = {"databaseId": DB, "assetId": asset_id, "complianceState": "compliant",
                   "schemaName": OLD, "schemaSource": "database", "assetName": f"Asset {asset_id}",
                   "updatedAt": "2026-10-01T00:00:00+00:00", "recordRevision": 1}
            if asset_id == QUARANTINED:
                row.update(complianceState="quarantined", quarantineReason="units missing",
                           quarantinedAt="2026-10-01T00:00:00+00:00")
            if asset_id == OVERRIDE:
                row.update(schemaSource="asset", schemaName=NEW)
            self.t["complianceAssetStateStorage"].put_item(Item=row)
        return self

    def __exit__(self, *exc):
        for active in reversed(self.patches):
            active.stop()
        self._mock.stop()

    # --- requests ---

    def call(self, service, method, path, params, body=None):
        response = service.lambda_handler(rest_event(method, path, params, body=body), MagicMock())
        return response["statusCode"], body_of(response)

    def bind(self, schema=NEW, auto_eval=None):
        body = {"schemaName": schema}
        if auto_eval is not None:
            body["complianceAutoEval"] = auto_eval
        return self.call(binding_svc, "PUT", f"/compliance/bind/{DB}", {"databaseId": DB}, body)

    def unbind(self):
        return self.call(binding_svc, "DELETE", f"/compliance/bind/{DB}", {"databaseId": DB})

    def bindings(self):
        return self.call(binding_svc, "GET", f"/compliance/bind/{DB}", {"databaseId": DB})

    # --- the job Lambda's side ---

    def apply_record(self):
        return binding_apply.get_record(DB)

    def run_once(self, page_size=2, record=None):
        """One job run of the apply: one page (its deadline has passed after the first)."""
        record = record or self.apply_record()
        return binding_apply.run(DB, record["applyId"], record["runId"], 0.0, clock=lambda: 1.0,
                                 sqs=self.sqs, queue_url=QUEUE, page_size=page_size)

    def drain(self, page_size=2):
        runs = 0
        while binding_apply.active_phase(self.apply_record()) is not None:
            self.run_once(page_size)
            runs += 1
            assert runs < 100
        return runs

    # --- reads ---

    def state(self, asset_id):
        return self.t["complianceAssetStateStorage"].get_item(
            Key={"databaseId": DB, "assetId": asset_id}).get("Item")

    def records(self):
        return {item["assetId"]: item
                for item in self.t["complianceAssetStateStorage"].scan()["Items"]
                if item["databaseId"] == DB}

    def audit(self, event_type=None, database_id=DB):
        return [item for item in self.t["complianceAuditStorage"].scan()["Items"]
                if item["databaseId"] == database_id
                and event_type in (None, item["eventType"])]

    def queued(self):
        return sorted(json.loads(entry["MessageBody"])["assetId"]
                      for call in self.sqs.send_message_batch.call_args_list
                      for entry in call.kwargs["Entries"])

    def apply_messages(self):
        return [json.loads(call.kwargs["MessageBody"]) for call in self.sqs.send_message.call_args_list]


@pytest.fixture
def world():
    with World() as active:
        yield active


def _queue_event(body):
    return {"Records": [{"messageId": "m1", "body": json.dumps(body),
                         "attributes": {"ApproximateReceiveCount": "1"}}]}


def _context(seconds=300):
    return MagicMock(get_remaining_time_in_millis=lambda: seconds * 1000)


@pytest.mark.unit
class TestTheRequestAnswersAtOnce:

    def test_a_change_answers_202_with_the_count_before_any_record_changes(self, world):
        before = world.records()
        status, body = world.bind(NEW)
        assert status == 202
        assert (body["applyStatus"], body["assetCount"], body["changed"]) == (
            "applying", len(ASSETS), True)
        assert body["apply"]["operation"] == "change"
        assert body["apply"]["previousSchema"] == OLD
        assert body["applyId"] == body["apply"]["applyId"]
        # No record changed in the request; the binding is saved.
        assert world.records() == before
        assert world.t["databaseStorage"].get_item(
            Key={"databaseId": DB})["Item"]["complianceSchemaName"] == NEW
        (entry,) = world.audit()
        assert (entry["eventType"], entry["actor"], entry["schemaName"]) == (
            "schema_bound_to_database", USER, NEW)
        assert json.loads(entry["details"]) == {
            "previousSchema": OLD, "complianceAutoEval": False, "applyId": body["applyId"],
            "assetCount": len(ASSETS)}
        # One message, carrying ids only.
        (message,) = world.apply_messages()
        record = world.apply_record()
        assert message == {"vamsInternalAction": "applyDatabaseBinding", "databaseId": DB,
                           "applyId": record["applyId"], "runId": record["runId"]}

    def test_a_removal_answers_202_with_the_records_that_inherit_the_binding(self, world):
        before = world.records()
        status, body = world.unbind()
        assert status == 202
        # Every record but the override inherits the binding.
        assert (body["applyStatus"], body["assetCount"], body["previousSchema"]) == (
            "applying", len(ASSETS) - 1, OLD)
        assert world.records() == before
        assert "complianceSchemaName" not in world.t["databaseStorage"].get_item(
            Key={"databaseId": DB})["Item"]
        assert [entry["eventType"] for entry in world.audit()] == ["schema_unbound_from_database"]

    def test_a_first_binding_is_a_bind(self):
        with World(bound=None) as world:
            status, body = world.bind(NEW)
            assert (status, body["apply"]["operation"], body["assetCount"]) == (
                202, "bind", len(ASSETS))
            assert world.records() == {}


@pytest.mark.unit
class TestTheTriggerAppliesItPageByPage:

    def test_a_change_across_several_runs(self, world):
        world.bind(NEW)
        world.run_once()
        record = world.apply_record()
        assert record["applyStatus"] == "applying" and record["cursor"]
        assert binding_apply.counts_of(record)["assetsProcessed"] == 2
        runs = 1 + world.drain()
        assert runs == 4
        record = world.apply_record()
        assert (record["applyStatus"], record.get("cursor")) == ("applied", None)
        assert binding_apply.counts_of(record) == {
            "assetsProcessed": len(ASSETS), "assetsPendingEvaluation": len(ASSETS) - 2,
            "assetsKeptQuarantined": 1, "removedComplianceRecords": 0, "evaluationsQueued": 0}
        records = world.records()
        for asset_id in ASSETS:
            if asset_id == OVERRIDE:
                assert (records[asset_id]["schemaSource"], records[asset_id]["schemaName"]) == (
                    "asset", NEW)
                continue
            assert (records[asset_id]["schemaName"], records[asset_id]["schemaSource"]) == (
                NEW, "database")
            assert records[asset_id]["bindingApplyId"] == record["applyId"]
        quarantined = records[QUARANTINED]
        assert (quarantined["complianceState"], quarantined["quarantineReason"]) == (
            "quarantined", "units missing")
        assert (quarantined["awaitingReevaluationReason"],
                quarantined["awaitingReevaluationSchema"]) == ("schemaBound", NEW)
        # D158: the marker time is the time of the record's own write.
        assert quarantined["awaitingReevaluationSince"] == quarantined["updatedAt"]
        assert quarantined["updatedAt"] > record["startedAt"]

    def test_every_asset_entry_names_the_caller_and_the_end_records_the_totals(self, world):
        world.bind(NEW)
        world.drain()
        entries = world.audit("schema_binding_applied_to_asset")
        assert sorted(entry["assetId"] for entry in entries) == sorted(
            asset_id for asset_id in ASSETS if asset_id != OVERRIDE)
        assert {entry["actor"] for entry in entries} == {USER}
        (finished,) = world.audit("schema_binding_apply_finished")
        details = json.loads(finished["details"])
        assert (finished["actor"], details["outcome"], details["operation"],
                details["assetsPendingEvaluation"], details["assetsKeptQuarantined"]) == (
            USER, "applied", "change", len(ASSETS) - 2, 1)

    def test_a_removal_across_several_runs(self, world):
        world.unbind()
        assert world.drain() == 4
        records = world.records()
        assert sorted(records) == [QUARANTINED, OVERRIDE]
        assert "schemaName" not in records[QUARANTINED]
        assert records[QUARANTINED]["awaitingReevaluationReason"] == "schemaUnbound"
        assert records[QUARANTINED]["complianceState"] == "quarantined"
        counts = binding_apply.counts_of(world.apply_record())
        assert (counts["removedComplianceRecords"], counts["assetsKeptQuarantined"]) == (
            len(ASSETS) - 2, 1)
        assert len(world.audit("schema_binding_applied_to_asset")) == len(ASSETS) - 1

    def test_a_first_binding_creates_the_records(self):
        with World(bound=None) as world:
            world.bind(NEW)
            world.drain(page_size=3)
            assert sorted(world.records()) == ASSETS
            assert {row["complianceState"] for row in world.records().values()} == {
                "pending_evaluation"}


@pytest.mark.unit
class TestAPageAppliedTwiceChangesNothingTwice:

    def test_a_redelivered_page_writes_and_audits_nothing_again(self, world):
        world.bind(NEW)
        real_save = binding_apply._save_progress
        failed = []

        def save_fails_once(*args, **kwargs):
            if not failed:
                failed.append(True)
                raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}},
                                  "UpdateItem")
            return real_save(*args, **kwargs)
        # The run applies its page, then fails saving its cursor: the message is redelivered.
        with patch.object(binding_apply, "_save_progress", side_effect=save_fails_once):
            with pytest.raises(ClientError):
                world.run_once(page_size=3)
        written_once = {asset_id: row["recordRevision"] for asset_id, row in world.records().items()}
        world.run_once(page_size=3)
        # The redelivered run read the same page again and wrote none of its records again.
        for asset_id in ASSETS[:3]:
            assert world.records()[asset_id]["recordRevision"] == written_once[asset_id]
        world.drain(page_size=3)
        entries = world.audit("schema_binding_applied_to_asset")
        assert sorted(entry["assetId"] for entry in entries) == sorted(
            asset_id for asset_id in ASSETS if asset_id != OVERRIDE)
        # The records the failed run wrote are counted from their stamp.
        counts = binding_apply.counts_of(world.apply_record())
        assert counts["assetsPendingEvaluation"] + counts["assetsKeptQuarantined"] == len(ASSETS) - 1

    def test_automatic_evaluation_queues_each_asset_once_after_every_record_is_written(self):
        with World(auto_eval=True) as world:
            world.bind(NEW)
            while world.apply_record()["applyStatus"] == "applying":
                world.run_once(page_size=3)
                # Nothing is queued while records still carry the binding before the change.
                assert world.queued() == []
            record = world.apply_record()
            assert (record["phase"], record["evaluationStatus"], record["evaluationsToQueue"]) == (
                "evaluations", "queueing", len(ASSETS) - 1)
            world.drain(page_size=3)
            assert world.queued() == sorted(asset_id for asset_id in ASSETS if asset_id != OVERRIDE)
            record = world.apply_record()
            assert (record.get("phase"), record["evaluationStatus"]) == (None, "queued")
            assert binding_apply.counts_of(record)["evaluationsQueued"] == len(ASSETS) - 1

    def test_without_a_failure_the_count_is_every_queued_evaluation(self):
        with World(auto_eval=True) as world:
            world.bind(NEW)
            world.drain(page_size=3)
            assert binding_apply.counts_of(world.apply_record())["evaluationsQueued"] == len(
                world.queued()) == len(ASSETS) - 1


@pytest.mark.unit
class TestTheMessageCarriesIdsOnly:

    def test_a_message_continues_only_the_stored_apply(self, world):
        world.bind(NEW)
        record = world.apply_record()
        before = world.records()
        for wrong in ({"applyId": "00000000-0000-4000-8000-000000000000"},
                      {"runId": "00000000-0000-4000-8000-000000000000"}):
            message = dict(binding_apply.message(DB, record["applyId"], record["runId"]), **wrong)
            assert jobs.lambda_handler(_queue_event(message), _context()) == {
                "batchItemFailures": []}
        assert world.records() == before

    def test_a_schema_or_an_actor_in_the_message_changes_nothing(self, world):
        world.bind(NEW)
        record = world.apply_record()
        message = dict(binding_apply.message(DB, record["applyId"], record["runId"]),
                       schemaName=OLD, actor="someone-else", operation="unbind")
        jobs.lambda_handler(_queue_event(message), _context())
        assert world.apply_record()["applyStatus"] == "applied"
        assert {row["schemaName"] for asset_id, row in world.records().items()} == {NEW}
        assert {entry["actor"] for entry in world.audit("schema_binding_applied_to_asset")} == {
            USER}

    def test_a_message_never_starts_an_apply_and_an_sns_envelope_is_not_one(self, world):
        record_body = binding_apply.message(DB, "00000000-0000-4000-8000-000000000000",
                                            "00000000-0000-4000-8000-000000000001")
        jobs.lambda_handler(_queue_event(record_body), _context())
        envelope = {"Type": "Notification", "Message": json.dumps(record_body)}
        assert binding_apply.message_target({"body": json.dumps(envelope)}) is None
        assert world.apply_record() is None
        assert world.audit() == []

    def test_an_ended_apply_ignores_a_late_message(self, world):
        world.bind(NEW)
        record = world.apply_record()
        world.drain()
        revisions = {asset_id: row["recordRevision"] for asset_id, row in world.records().items()}
        jobs.lambda_handler(_queue_event(binding_apply.message(
            DB, record["applyId"], record["runId"])), _context())
        assert {asset_id: row["recordRevision"]
                for asset_id, row in world.records().items()} == revisions

    def test_an_unfinished_run_queues_the_next_message_and_a_failed_one_is_redelivered(self, world):
        world.bind(NEW)
        record = world.apply_record()
        world.sqs.send_message.reset_mock()
        event = _queue_event(binding_apply.message(DB, record["applyId"], record["runId"]))
        with patch.object(binding_apply, "run", return_value=False):
            assert jobs.lambda_handler(event, _context()) == {"batchItemFailures": []}
        assert world.apply_messages() == [binding_apply.message(DB, record["applyId"],
                                                                record["runId"])]
        # The continuation goes to the job queue.
        assert [call.kwargs["QueueUrl"] for call in world.sqs.send_message.call_args_list] == [
            JOB_QUEUE]
        with patch.object(binding_apply, "run", side_effect=ClientError(
                {"Error": {"Code": "InternalServerError"}}, "Query")):
            assert jobs.lambda_handler(event, _context()) == {
                "batchItemFailures": [{"itemIdentifier": "m1"}]}

    def test_the_first_message_goes_to_the_job_queue_and_the_evaluations_to_the_trigger(self):
        with World(auto_eval=True) as world:
            world.bind(NEW)
            assert [call.kwargs["QueueUrl"] for call in world.sqs.send_message.call_args_list] == [
                JOB_QUEUE]
            world.drain(page_size=3)
            assert {call.kwargs["QueueUrl"] for call in world.sqs.send_message_batch.call_args_list
                    } == {QUEUE}
            assert world.queued() == sorted(asset for asset in ASSETS if asset != OVERRIDE)

    def test_the_trigger_logs_and_drops_a_leftover_apply_message_and_runs_nothing(self, world):
        world.bind(NEW)
        record = world.apply_record()
        before = world.records()
        world.sqs.reset_mock()
        body = json.dumps(binding_apply.message(DB, record["applyId"], record["runId"]))
        event = {"Records": [{"messageId": "m1", "body": body,
                              "attributes": {"ApproximateReceiveCount": "1"}}]}
        with patch.object(binding_apply, "run") as run, \
                patch.object(trigger.logger, "warning") as warning:
            assert trigger.lambda_handler(event, _context()) == {"batchItemFailures": []}
        run.assert_not_called()
        # Nothing sent, nothing written, the apply untouched.
        assert world.sqs.method_calls == []
        assert world.records() == before
        assert world.apply_record() == record
        assert [call.args[0] for call in warning.call_args_list] == [
            f"Compliance job message applyDatabaseBinding for database {DB} on the trigger queue "
            "dropped: compliance jobs run from the compliance job queue"]

@pytest.mark.unit
class TestWhileAChangeIsApplied:

    def test_another_change_and_evaluate_all_answer_409(self, world):
        assert world.bind(NEW)[0] == 202
        for status, body in (world.bind(OLD), world.unbind(), world.bind(NEW),
                             world.bind(NEW, auto_eval=True)):
            assert (status, body["message"]) == (409, APPLYING_MESSAGE)
        status, body = world.call(evaluate_svc, "POST", f"/compliance/evaluate/{DB}",
                                  {"databaseId": DB}, {})
        assert (status, body["message"]) == (409, APPLYING_MESSAGE)
        # Once applied, the next change goes through.
        world.drain()
        assert world.bind(OLD)[0] == 202

    def test_an_asset_override_runs_and_the_apply_leaves_it_alone(self, world):
        world.bind(NEW)
        path = f"/compliance/bind/{DB}/a06"
        status, _ = world.call(binding_svc, "PUT", path, {"databaseId": DB, "assetId": "a06"},
                               {"schemaName": OLD})
        assert status == 200
        world.drain()
        assert (world.state("a06")["schemaSource"], world.state("a06")["schemaName"]) == (
            "asset", OLD)

    def test_a_release_runs_and_the_apply_decides_the_record_as_it_is_then(self, world):
        world.bind(NEW)
        status, _ = world.call(quarantine_svc, "POST",
                               f"/compliance/quarantine/{DB}/{QUARANTINED}/release",
                               {"databaseId": DB, "assetId": QUARANTINED}, {"reason": "checked"})
        assert status == 200
        world.drain()
        assert world.state(QUARANTINED)["complianceState"] == "pending_evaluation"

    def test_an_evaluation_of_one_asset_is_not_refused(self, world):
        world.bind(NEW)
        with patch.object(evaluate_svc, "run_evaluation_for_asset",
                          return_value={"evaluationId": "ev-1", "verdict": "compliant",
                                        "complianceState": "compliant"}) as run:
            status, _ = world.call(evaluate_svc, "POST", f"/compliance/evaluate/{DB}/a01",
                                   {"databaseId": DB, "assetId": "a01"}, {})
        assert status == 200 and run.called

    def test_a_new_asset_takes_the_new_binding_and_the_apply_leaves_it(self, world):
        world.bind(NEW)
        world.t["assetStorage"].put_item(Item={"databaseId": DB, "assetId": "a99",
                                               "assetName": "New"})
        store.register_under_database_binding(DB, "a99", NEW, asset_name="New")
        registered = world.state("a99")
        world.drain()
        assert world.state("a99") == registered
        assert "a99" not in {entry["assetId"]
                             for entry in world.audit("schema_binding_applied_to_asset")}


@pytest.mark.unit
class TestADatabaseDeleteDuringAnApply:

    def test_the_move_wins_and_nothing_more_lands_under_the_live_id(self, world):
        world.bind(NEW)
        world.run_once(page_size=2)
        stamped_before = {asset_id for asset_id, row in world.records().items()
                          if row.get("bindingApplyId")}
        tables = retirement.Tables(schema=world.t["complianceSchemaStorage"],
                                   asset_state=world.t["complianceAssetStateStorage"],
                                   evaluation=world.t["complianceEvaluationStorage"],
                                   cascade=world.t["complianceCascadeStorage"],
                                   audit=world.t["complianceAuditStorage"])
        retirement.begin(tables, DB, USER)
        world.run_once(page_size=2)
        record = world.apply_record()
        assert (record["applyStatus"], record["failureReason"]) == (
            "failed", binding_apply.FAILED_DATABASE_DELETED)
        assert {asset_id for asset_id, row in world.records().items()
                if row.get("bindingApplyId")} == stamped_before
        # Its end is recorded with the deleted database's history.
        assert world.audit("schema_binding_apply_finished") == []
        assert len(world.audit("schema_binding_apply_finished", f"{DB}#deleted")) == 1
        assert retirement.run(tables, DB, float("inf"))
        assert world.records() == {}
        # A database re-created with the id shows no apply.
        world.t["databaseStorage"].put_item(Item={"databaseId": DB})
        status, body = world.bindings()
        assert (status, body["apply"], body["applyStatus"]) == (200, None, None)
        # Its first change is not held by the deleted database's apply (the move took that
        # database's own schemas, so it binds a GLOBAL one).
        world.t["complianceSchemaStorage"].put_item(Item=schema_row("shared", database_id="GLOBAL"))
        assert world.bind("shared")[0] == 202

    def test_a_page_the_move_interrupts_audits_its_writes_under_the_deleted_id(self, world):
        world.bind(NEW)
        calls = []
        real = store.database_moving

        def moving(database_id):
            calls.append(database_id)
            # The run's own check, then two records; the move begins before the third.
            return len(calls) > 3 or real(database_id)
        with patch.object(store, "database_moving", side_effect=moving):
            world.run_once(page_size=5)
        record = world.apply_record()
        assert record["applyStatus"] == "failed"
        assert len(world.audit("schema_binding_applied_to_asset", f"{DB}#deleted")) == 2
        assert world.audit("schema_binding_applied_to_asset") == []


@pytest.mark.unit
class TestAStalledApply:

    @staticmethod
    def _stall(world, minutes=20):
        """The apply's last run started `minutes` ago on its message's last delivery and ended
        without handing over: nothing will run it again."""
        past = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        world.t["complianceEvaluationStorage"].update_item(
            Key=binding_apply.record_key(DB),
            UpdateExpression="SET runStartedAt = :p, runReceiveCount = :n REMOVE nextMessageAt",
            ExpressionAttributeValues={":p": past, ":n": 3})

    def test_it_reads_stalled_and_another_change_is_told_how_to_resume(self, world):
        world.bind(NEW)
        self._stall(world)
        status, body = world.bindings()
        assert status == 200
        assert (body["applyStatus"], body["apply"]["status"]) == ("stalled", "stalled")
        assert body["apply"]["reason"] == (
            "Its last step failed on every delivery; repeat the same change to resume it")
        assert (world.unbind()[0], world.unbind()[1]["message"]) == (409, STALLED_MESSAGE)
        assert world.bind(OLD)[0] == 409

    def test_the_same_change_resumes_it_from_its_cursor(self, world):
        world.bind(NEW)
        world.run_once(page_size=2)
        before = world.apply_record()
        self._stall(world)
        world.sqs.send_message.reset_mock()
        status, body = world.bind(NEW)
        assert (status, body["resumed"], body["applyStatus"]) == (202, True, "applying")
        resumed = world.apply_record()
        assert (resumed["applyId"], resumed["cursor"]) == (before["applyId"], before["cursor"])
        assert resumed["runId"] != before["runId"]
        assert world.apply_messages() == [binding_apply.message(DB, resumed["applyId"],
                                                                resumed["runId"])]
        # The run before is ignored now.
        assert world.run_once(record=before)
        assert world.apply_record()["cursor"] == before["cursor"]
        world.drain()
        assert world.apply_record()["applyStatus"] == "applied"
        assert len(world.audit("schema_binding_applied_to_asset")) == len(ASSETS) - 1

    def test_a_running_apply_never_reads_stalled(self, world):
        world.bind(NEW)
        world.run_once()
        record = world.apply_record()
        assert binding_apply.status(record) == "applying"
        # Its next message is queued: however long it waits, the apply has not stopped.
        later = datetime.now(timezone.utc) + timedelta(hours=6)
        assert binding_apply.status(record, now=later) == "applying"


@pytest.mark.unit
class TestAutomaticEvaluationTurnedOn:

    def test_it_queues_the_pending_assets_in_the_background(self, world):
        world.t["complianceAssetStateStorage"].update_item(
            Key={"databaseId": DB, "assetId": "a01"}, UpdateExpression="SET complianceState = :p",
            ExpressionAttributeValues={":p": "pending_evaluation"})
        status, body = world.bind(OLD, auto_eval=True)
        assert (status, body["apply"]["operation"], body["assetCount"]) == (
            202, "evaluatePending", 1)
        assert world.queued() == []
        world.drain()
        assert world.queued() == ["a01"]
        (entry,) = world.audit("schema_binding_auto_eval_changed")
        assert json.loads(entry["details"])["applyId"] == body["applyId"]

    def test_with_nothing_pending_it_answers_at_once(self, world):
        status, body = world.bind(OLD, auto_eval=True)
        assert (status, body["evaluationsQueued"]) == (200, 0)
        assert world.apply_record() is None


@pytest.mark.unit
class TestReaders:

    def test_the_binding_read_reports_the_apply_and_nothing_internal(self, world):
        world.bind(NEW)
        status, body = world.bindings()
        assert body["applyStatus"] == "applying"
        world.drain()
        status, body = world.bindings()
        apply = body["apply"]
        assert (status, body["applyStatus"], apply["status"], apply["operation"]) == (
            200, "applied", "applied", "change")
        assert set(apply) == {"applyId", "operation", "status", "schemaName", "previousSchema",
                              "assetCount", "assetsProcessed", "assetsPendingEvaluation",
                              "assetsKeptQuarantined", "removedComplianceRecords",
                              "evaluationsQueued", "startedAt", "lastProgressAt", "finishedAt"}

    def test_no_state_answer_returns_the_stamp(self, world):
        world.bind(NEW)
        world.drain()
        status, body = world.call(evaluate_svc, "GET", f"/compliance/state/{DB}/a01",
                                  {"databaseId": DB, "assetId": "a01"})
        assert status == 200 and "bindingApplyId" not in json.dumps(body)
        status, body = world.call(evaluate_svc, "GET", f"/compliance/state/{DB}",
                                  {"databaseId": DB})
        assert status == 200 and "bindingApplyId" not in json.dumps(body)


def test_the_literals_are_the_modules():
    assert (binding_apply.APPLYING_MESSAGE, binding_apply.STALLED_MESSAGE,
            binding_apply.APPLY_ACTION, binding_apply.APPLY_STAMP_FIELD) == (
        APPLYING_MESSAGE, STALLED_MESSAGE, "applyDatabaseBinding", "bindingApplyId")
    assert (binding_apply.EVALUATIONS_PER_SHARE, binding_apply.APPLIED_AT_FIELD) == (
        100, "bindingAppliedAt")
    assert binding_apply.STALLED_RUN_LIMIT == RUN_LIMIT_STOPPED


RUN_LIMIT_STOPPED = ("It ran more steps than a change of its size needs, so it was stopped; "
                     "repeat the same change to resume it")


@pytest.mark.unit
class TestTheRunLimit:
    """The job Lambda runs with Lambda's recursive-loop detection off; an apply counts its runs
    against a limit derived from its size and stops past it."""

    @pytest.mark.parametrize("operation, assets, auto_eval, limit", [
        ("bind", 3000, False, 2 * 30 + 20),
        ("change", 3000, True, 2 * (30 + 30 * 9) + 20),
        ("unbind", 3000, True, 2 * 30 + 20),
        ("evaluatePending", 250, True, 2 * (3 * 9) + 20),
        ("bind", 0, False, 2 * 1 + 20),
    ], ids=["bind", "change with automatic evaluation", "removal", "automatic evaluation on",
            "empty database"])
    def test_the_limit_follows_the_size(self, operation, assets, auto_eval, limit):
        assert binding_apply.run_limit(operation, assets, auto_eval) == limit

    def test_every_run_counts_and_the_limit_is_stored(self, world):
        world.bind(NEW)
        record = world.apply_record()
        assert (record["runs"], record["runLimit"]) == (
            0, binding_apply.run_limit("change", len(ASSETS), False))
        runs = world.drain(page_size=2)
        assert world.apply_record()["runs"] == runs

    def test_a_run_past_the_limit_runs_nothing_and_the_apply_reads_stopped(self, world):
        world.bind(NEW)
        world.run_once()
        before = world.records()
        world.t["complianceEvaluationStorage"].update_item(
            Key=binding_apply.record_key(DB), UpdateExpression="SET runLimit = :l",
            ExpressionAttributeValues={":l": 1})
        record = world.apply_record()
        world.sqs.send_message.reset_mock()
        event = _queue_event(binding_apply.message(DB, record["applyId"], record["runId"]))
        assert jobs.lambda_handler(event, _context()) == {"batchItemFailures": []}
        # Nothing written and no continuation queued.
        assert world.records() == before
        world.sqs.send_message.assert_not_called()
        record = world.apply_record()
        assert (record["applyStatus"], record["stopReason"]) == ("applying", RUN_LIMIT_STOPPED)
        assert "nextMessageAt" not in record
        status, body = world.bindings()
        assert (body["applyStatus"], body["apply"]["reason"]) == ("stalled", RUN_LIMIT_STOPPED)
        # A later delivery of a message of the stopped run changes nothing.
        assert jobs.lambda_handler(event, _context()) == {"batchItemFailures": []}
        assert world.records() == before
        # Another change answers the stalled 409; the same change resumes it with a fresh count.
        assert world.bind(OLD) == (409, {"message": STALLED_MESSAGE})
        status, body = world.bind(NEW)
        assert (status, body["resumed"], body["applyStatus"]) == (202, True, "applying")
        record = world.apply_record()
        assert (record["runs"], "stopReason" in record) == (0, False)
        world.t["complianceEvaluationStorage"].update_item(
            Key=binding_apply.record_key(DB), UpdateExpression="SET runLimit = :l",
            ExpressionAttributeValues={":l": 50})
        world.drain()
        assert world.apply_record()["applyStatus"] == "applied"
        assert {row["schemaName"] for row in world.records().values()} == {NEW}

    def test_an_apply_recorded_before_the_limit_gets_it_from_its_size(self, world):
        world.bind(NEW)
        world.t["complianceEvaluationStorage"].update_item(
            Key=binding_apply.record_key(DB), UpdateExpression="REMOVE runLimit, runs")
        world.drain()
        record = world.apply_record()
        assert record["applyStatus"] == "applied" and "stopReason" not in record
        assert binding_apply.limit_of(record) == binding_apply.run_limit(
            "change", len(ASSETS), False)


@pytest.mark.unit
class TestTheEvaluationsKeepWithinTheirShareOfTheTriggerQueue:
    """The evaluations phase never has more than EVALUATION_OUTSTANDING_LIMIT of its own
    evaluations sent and not taken by the trigger (D164), whatever the queue's approximate message
    count reads; it waits for as long as the trigger takes and never queues a share anyway."""

    LIMIT = 3

    @pytest.fixture
    def evaluating(self):
        with World(auto_eval=True) as world, \
                patch.object(binding_apply, "EVALUATION_OUTSTANDING_LIMIT", self.LIMIT, create=True):
            # The queue's approximate count reads 0, as SQS's did right after the job's sends.
            world.sqs.get_queue_attributes.return_value = {
                "Attributes": {"ApproximateNumberOfMessages": "0"}}
            world.bind(NEW)
            while world.apply_record()["applyStatus"] == "applying":
                world.run_once(page_size=3)
            yield world

    def _take(self, world, count):
        record = world.apply_record()
        assert binding_apply.count_taken(DB, record["applyId"], count)

    def test_a_full_share_delays_the_next_one_and_taken_evaluations_free_it(self, evaluating):
        world = evaluating
        # Six records to evaluate (all but the override); room for three.
        assert world.apply_record()["evaluationsToQueue"] == len(ASSETS) - 1
        world.run_once(page_size=3)
        assert len(world.queued()) == 3
        assert world.run_once(page_size=3) is False
        record = world.apply_record()
        assert binding_apply.outstanding_evaluations(record) == 3
        assert (record["evaluationWaits"], record["nextMessageDelay"]) == (
            1, binding_apply.EVALUATION_FIRST_WAIT_SECONDS)
        assert len(world.queued()) == 3
        # The continuation is delayed, and a delayed message is waiting, not stopped.
        assert binding_apply.send_message(world.sqs, JOB_QUEUE, record)
        assert world.sqs.send_message.call_args.kwargs["DelaySeconds"] == \
            binding_apply.EVALUATION_FIRST_WAIT_SECONDS
        assert binding_apply.evaluation_status(record) == "queueing"
        # Two taken: the last three still do not fit.
        self._take(world, 2)
        world.run_once(page_size=3)
        assert len(world.queued()) == 3
        assert world.apply_record()["evaluationWaits"] == 2
        # All three taken: the last share goes, the waits start again, and the phase ends.
        self._take(world, 1)
        world.drain(page_size=3)
        record = world.apply_record()
        assert world.queued() == sorted(asset for asset in ASSETS if asset != OVERRIDE)
        assert record["evaluationWaits"] == 0 and "nextMessageDelay" not in record
        assert (record["evaluationStatus"], record["evaluationsSent"]) == ("queued", 6)

    def test_a_share_waits_for_as_long_as_the_trigger_takes_and_is_never_queued_anyway(
            self, evaluating):
        world = evaluating
        world.run_once(page_size=3)
        runs_before = world.apply_record()["runs"]
        delays = []
        for _ in range(binding_apply.EVALUATION_WAITS_PER_SHARE + 6):
            world.run_once(page_size=3)
            delays.append(world.apply_record()["nextMessageDelay"])
        # Waiting, never queued anyway (the earlier rule queued it after eight waits).
        assert len(world.queued()) == 3
        assert delays == [binding_apply.wait_delay(n) for n in range(len(delays))]
        assert delays[-6:] == [binding_apply.EVALUATION_MAX_WAIT_SECONDS] * 6 == [900] * 6
        # Only the waits below the largest delay counted against the run limit.
        assert world.apply_record()["runs"] - runs_before == \
            binding_apply.EVALUATION_WAITS_PER_SHARE + 1
        assert binding_apply.evaluation_status(world.apply_record()) == "queueing"
        self._take(world, 3)
        world.drain(page_size=3)
        assert world.apply_record()["evaluationStatus"] == "queued"
        assert len(world.queued()) == len(ASSETS) - 1

    def test_evaluations_left_uncounted_hold_it_only_until_the_queue_retention_passed(
            self, evaluating):
        world = evaluating
        world.run_once(page_size=3)
        world.run_once(page_size=3)
        assert len(world.queued()) == 3
        # The trigger never counted them (their last delivery's batch failed as a whole): once
        # the trigger queue's retention period has passed since that share, none can remain.
        sent_at = datetime.now(timezone.utc) - timedelta(
            seconds=binding_apply.TRIGGER_QUEUE_RETENTION_SECONDS + 120)
        world.t["complianceEvaluationStorage"].update_item(
            Key=binding_apply.record_key(DB), UpdateExpression="SET evaluationsSentAt = :at",
            ExpressionAttributeValues={":at": sent_at.isoformat()})
        assert binding_apply.outstanding_evaluations(world.apply_record()) == 0
        world.drain(page_size=3)
        record = world.apply_record()
        assert len(world.queued()) == 6 and record["evaluationStatus"] == "queued"
        # The earlier ones are counted taken; the new share is outstanding.
        assert (record["evaluationsSent"], record["evaluationsTaken"]) == (6, 3)
        assert binding_apply.outstanding_evaluations(record) == 3

    def test_evaluations_are_counted_before_they_are_sent_and_unqueued_ones_taken_back(
            self, evaluating):
        world = evaluating
        seen = []

        def batch(**kwargs):
            seen.append(int(world.apply_record().get("evaluationsSent", 0)))
            return {"Failed": [{"Id": kwargs["Entries"][0]["Id"]}]}
        world.sqs.send_message_batch.side_effect = batch
        world.run_once(page_size=3)
        # Counted (three) before the batch was sent; the one not queued was taken back.
        assert seen == [3]
        record = world.apply_record()
        assert (record["evaluationsSent"], binding_apply.counts_of(record)["evaluationsQueued"]) == (
            2, 2)

    def test_a_run_that_lost_the_count_to_another_run_stops_there(self, evaluating):
        world = evaluating
        record = world.apply_record()
        # Another delivery of the same run counted a share after this run read the record.
        assert binding_apply.reserve_evaluations(record, record, 1)
        assert not binding_apply.reserve_evaluations(record, record, 1)
        assert world.apply_record()["evaluationsSent"] == 1
