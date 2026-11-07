# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A binding change never lifts a quarantine without a record (D118).

The binding service and the evaluation store run against moto tables built from the CDK
definitions. Covered, each with a quarantined asset and with automatic evaluation on and off:
binding a new schema to a database, re-binding the schema it has, changing only automatic
evaluation, removing the binding, and setting and clearing an asset-level override. A quarantined
asset stays quarantined (its download block with it under `quarantineBlocksDownload`) and is marked
awaiting re-evaluation until a verdict decides it; every asset the change reaches gets its own audit
entry; evaluations are queued on the trigger's queue only with automatic evaluation on; a re-bind
of the binding in force changes nothing.
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _bindingApply, _cdkTables
from backend.tests.handlers.compliance._harness import (
    REAL_TO_UPDATE_EXPR, USER, body_of, claims_for, rest_event, schema_row,
)
from backend.tests.handlers.compliance.test_complianceUnknownDatabase import _stand_in
from common.compliance import quarantineGuard
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceSchemaBindingService as binding_svc
from models.common import VAMSGeneralErrorResponse

# The apply record of a database binding change lives in the moto tables (conftest).
USES_BINDING_APPLY_RECORD = True

DB = "bind-db"
OLD, NEW = "old-schema", "new-schema"
QUEUE = "https://sqs.example/compliance-trigger"
# The re-evaluation marker's attributes (`store.AWAITING_REEVALUATION_FIELDS`).
AWAITING = ("awaitingReevaluationSince", "awaitingReevaluationSchema",
            "awaitingReevaluationReason")


class World:

    def __init__(self, auto_eval=True, bound=OLD):
        self.auto_eval = auto_eval
        self.bound = bound

    def __enter__(self):
        self._mock = mock_aws()
        self._mock.start()
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        self.patches, self.t = _cdkTables.table_doubles(resource, (store, binding_svc))
        self.sqs = MagicMock(name="sqs")
        self.sqs.send_message_batch.side_effect = lambda **kwargs: {"Failed": []}
        self.patches += [
            patch.object(binding_svc, "CasbinEnforcer", return_value=_stand_in({"*"})),
            patch.object(binding_svc, "request_to_claims", claims_for(USER)),
            patch.object(binding_svc, "sqs_client", self.sqs, create=True),
            patch.object(binding_svc, "trigger_queue_url", QUEUE, create=True),
            patch.object(store, "to_update_expr", REAL_TO_UPDATE_EXPR),
            patch.object(quarantineGuard, "quarantine_blocks_download", True),
            patch.object(quarantineGuard, "compliance_asset_state_table",
                         self.t["complianceAssetStateStorage"]),
        ]
        for active in self.patches:
            active.start()
        for name in (OLD, NEW):
            self.t["complianceSchemaStorage"].put_item(Item=schema_row(name, database_id=DB))
        database = {"databaseId": DB}
        if self.bound:
            database.update(complianceSchemaName=self.bound, complianceAutoEval=self.auto_eval)
        self.t["databaseStorage"].put_item(Item=database)
        for asset_id, state in (("quarantined", "quarantined"), ("compliant", "compliant")):
            self.t["assetStorage"].put_item(Item={"databaseId": DB, "assetId": asset_id,
                                                  "assetName": asset_id})
            row = {"databaseId": DB, "assetId": asset_id, "complianceState": state,
                   "schemaName": OLD, "schemaSource": "database", "assetName": asset_id,
                   "lastEvaluationId": f"ev-{asset_id}", "lastEvaluatedAt": "2026-10-01T00:00:00+00:00"}
            if state == "quarantined":
                row["quarantineReason"] = "units missing"
            self.t["complianceAssetStateStorage"].put_item(Item=row)
        return self

    def __exit__(self, *exc):
        for active in reversed(self.patches):
            active.stop()
        self._mock.stop()

    def call(self, method, path, params, body=None):
        response = binding_svc.lambda_handler(rest_event(method, path, params, body=body),
                                              MagicMock())
        return response["statusCode"], body_of(response)

    def bind_database(self, schema=NEW, auto_eval=None):
        """The bind request, then its apply run to the end as the trigger runs it (D161); the
        answer's `apply` is the apply as it ended."""
        body = {"schemaName": schema}
        if auto_eval is not None:
            body["complianceAutoEval"] = auto_eval
        return self._applied(self.call("PUT", f"/compliance/bind/{DB}", {"databaseId": DB}, body))

    def unbind_database(self):
        return self._applied(self.call("DELETE", f"/compliance/bind/{DB}", {"databaseId": DB}))

    def _applied(self, answer):
        status, body = answer
        if status == 202:
            body = dict(body, apply=_bindingApply.binding_apply.answer(
                _bindingApply.drain(DB, self.sqs, QUEUE)))
        return status, body

    def state(self, asset_id):
        return self.t["complianceAssetStateStorage"].get_item(
            Key={"databaseId": DB, "assetId": asset_id}).get("Item")

    def audit(self, event_type=None):
        items = self.t["complianceAuditStorage"].scan()["Items"]
        return sorted(((i["eventType"], i["assetId"], i.get("previousState"), i.get("newState"))
                       for i in items if event_type in (None, i["eventType"])),
                      key=lambda entry: (entry[0], entry[1]))

    def queued(self):
        return sorted(json.loads(entry["MessageBody"])["assetId"]
                      for call in self.sqs.send_message_batch.call_args_list
                      for entry in call.kwargs["Entries"])

    def download_blocked(self, asset_id):
        try:
            quarantineGuard.check_quarantine_block(DB, asset_id)
        except VAMSGeneralErrorResponse:
            return True
        return False


def _awaiting(row):
    return {key: row.get(key) for key in AWAITING}


@pytest.mark.unit
class TestBindingANewSchema:

    @pytest.mark.parametrize("auto_eval", [True, False], ids=["auto-eval on", "auto-eval off"])
    def test_quarantine_and_its_download_block_hold_until_re_evaluation(self, auto_eval):
        with World(auto_eval=auto_eval) as world:
            status, body = world.bind_database(NEW)
            assert status == 202
            applied = body["apply"]
            assert (body["changed"], applied["status"], applied["assetsPendingEvaluation"],
                    applied["assetsKeptQuarantined"], applied["evaluationsQueued"]) == (
                True, "applied", 1, 1, 2 if auto_eval else 0)
            row = world.state("quarantined")
            assert row["complianceState"] == "quarantined"
            assert row["quarantineReason"] == "units missing"
            assert row["schemaName"] == NEW
            marker = _awaiting(row)
            assert marker["awaitingReevaluationSchema"] == NEW
            assert marker["awaitingReevaluationReason"] == "schemaBound"
            assert marker["awaitingReevaluationSince"]
            assert world.download_blocked("quarantined") is True
            assert world.state("compliant")["complianceState"] == "pending_evaluation"
            assert world.audit("schema_binding_applied_to_asset") == [
                ("schema_binding_applied_to_asset", "compliant", "compliant", "pending_evaluation"),
                ("schema_binding_applied_to_asset", "quarantined", "quarantined", "quarantined")]
            assert [e[0] for e in world.audit("schema_bound_to_database")] == [
                "schema_bound_to_database"]
            assert [e[0] for e in world.audit("schema_binding_apply_finished")] == [
                "schema_binding_apply_finished"]
            assert world.queued() == (["compliant", "quarantined"] if auto_eval else [])

    def test_a_first_binding_marks_every_asset_and_audits_each(self):
        with World(bound=None) as world:
            status, body = world.bind_database(NEW)
            assert status == 202 and body["apply"]["assetsKeptQuarantined"] == 1
            assert len(world.audit("schema_binding_applied_to_asset")) == 2
            # A new binding defaults automatic evaluation on.
            assert world.queued() == ["compliant", "quarantined"]


@pytest.mark.unit
class TestReBindingTheSchemaInForce:

    @pytest.mark.parametrize("auto_eval", [True, False])
    def test_the_same_schema_and_setting_changes_nothing(self, auto_eval):
        with World(auto_eval=auto_eval) as world:
            before = world.t["complianceAssetStateStorage"].scan()["Items"]
            status, body = world.bind_database(OLD, auto_eval=auto_eval)
            assert status == 200
            assert body["changed"] is False
            assert "nothing changed" in body["message"]
            assert world.t["complianceAssetStateStorage"].scan()["Items"] == before
            assert world.audit() == []
            assert world.queued() == []

    def test_omitting_the_setting_keeps_it_and_changes_nothing(self):
        with World(auto_eval=False) as world:
            status, body = world.bind_database(OLD)
            assert (status, body["changed"], body["complianceAutoEval"]) == (200, False, False)
            assert world.audit() == []

    def test_turning_automatic_evaluation_on_queues_the_pending_assets_only(self):
        with World(auto_eval=False) as world:
            world.t["complianceAssetStateStorage"].put_item(Item={
                "databaseId": DB, "assetId": "pending", "complianceState": "pending_evaluation",
                "schemaName": OLD, "schemaSource": "database"})
            before = {r["assetId"]: r for r in world.t["complianceAssetStateStorage"].scan()["Items"]}
            status, body = world.bind_database(OLD, auto_eval=True)
            assert (status, body["changed"], body["apply"]["operation"],
                    body["apply"]["evaluationsQueued"]) == (202, True, "evaluatePending", 1)
            after = {r["assetId"]: r for r in world.t["complianceAssetStateStorage"].scan()["Items"]}
            assert after == before
            assert world.t["databaseStorage"].get_item(
                Key={"databaseId": DB})["Item"]["complianceAutoEval"] is True
            assert world.queued() == ["pending"]
            assert [e[0] for e in world.audit()] == ["schema_binding_apply_finished",
                                                     "schema_binding_auto_eval_changed"]

    def test_turning_it_off_queues_nothing(self):
        with World(auto_eval=True) as world:
            status, body = world.bind_database(OLD, auto_eval=False)
            assert (status, body["changed"], body["evaluationsQueued"]) == (200, True, 0)
            assert world.state("quarantined")["complianceState"] == "quarantined"
            assert world.queued() == []


@pytest.mark.unit
class TestRemovingTheBinding:

    def test_a_quarantined_asset_stays_quarantined_and_the_others_lose_their_record(self):
        with World() as world:
            status, body = world.unbind_database()
            assert status == 202
            assert (body["apply"]["removedComplianceRecords"],
                    body["apply"]["assetsKeptQuarantined"]) == (1, 1)
            row = world.state("quarantined")
            assert row["complianceState"] == "quarantined"
            assert _awaiting(row) == {"awaitingReevaluationSince": row["awaitingReevaluationSince"],
                                      "awaitingReevaluationSchema": None,
                                      "awaitingReevaluationReason": "schemaUnbound"}
            # No schema applies any more, so the record names none (D127).
            assert "schemaName" not in row and "schemaSource" not in row
            assert world.download_blocked("quarantined") is True
            assert world.state("compliant") is None
            assert world.audit("schema_binding_applied_to_asset") == [
                ("schema_binding_applied_to_asset", "compliant", "compliant", "unknown"),
                ("schema_binding_applied_to_asset", "quarantined", "quarantined", "quarantined")]
            assert world.queued() == []


@pytest.mark.unit
class TestAssetOverrides:

    def _path(self, asset_id):
        return f"/compliance/bind/{DB}/{asset_id}", {"databaseId": DB, "assetId": asset_id}

    @pytest.mark.parametrize("auto_eval", [True, False])
    def test_setting_one_keeps_the_quarantine(self, auto_eval):
        with World(auto_eval=auto_eval) as world:
            path, params = self._path("quarantined")
            status, body = world.call("PUT", path, params, {"schemaName": NEW})
            assert (status, body["changed"], body["complianceState"]) == (200, True, "quarantined")
            row = world.state("quarantined")
            assert (row["schemaSource"], row["schemaName"], row["complianceState"]) == (
                "asset", NEW, "quarantined")
            assert row["awaitingReevaluationReason"] == "overrideSet"
            assert world.download_blocked("quarantined") is True
            assert world.audit("schema_bound_to_asset") == [
                ("schema_bound_to_asset", "quarantined", "quarantined", "quarantined")]
            assert world.queued() == (["quarantined"] if auto_eval else [])
            # The same override again changes nothing.
            status, body = world.call("PUT", path, params, {"schemaName": NEW})
            assert (status, body["changed"]) == (200, False)
            assert len(world.audit("schema_bound_to_asset")) == 1

    def test_setting_one_on_another_asset_marks_it_pending_as_before(self):
        with World() as world:
            path, params = self._path("compliant")
            status, body = world.call("PUT", path, params, {"schemaName": NEW})
            assert (status, body["complianceState"]) == (200, "pending_evaluation")
            assert world.audit("schema_bound_to_asset") == [
                ("schema_bound_to_asset", "compliant", "compliant", "pending_evaluation")]

    @pytest.mark.parametrize("database_schema", [OLD, None], ids=["falls back", "no binding"])
    def test_clearing_one_keeps_the_quarantine(self, database_schema):
        with World(bound=database_schema) as world:
            world.t["complianceAssetStateStorage"].update_item(
                Key={"databaseId": DB, "assetId": "quarantined"},
                UpdateExpression="SET schemaSource = :a, schemaName = :n",
                ExpressionAttributeValues={":a": "asset", ":n": NEW})
            path, params = self._path("quarantined")
            status, body = world.call("DELETE", path, params)
            assert (status, body["complianceState"]) == (200, "quarantined")
            row = world.state("quarantined")
            assert row["complianceState"] == "quarantined"
            assert row["awaitingReevaluationReason"] == "overrideCleared"
            assert row.get("awaitingReevaluationSchema") == database_schema
            # The record names the schema that now applies, or none (D127).
            assert row.get("schemaName") == database_schema
            assert row.get("schemaSource") == ("database" if database_schema else None)
            assert world.download_blocked("quarantined") is True
            assert world.audit("schema_unbound_from_asset") == [
                ("schema_unbound_from_asset", "quarantined", "quarantined", "quarantined")]
            assert world.queued() == (["quarantined"] if database_schema else [])


@pytest.mark.unit
class TestTheMarkerEndsWithAVerdict:

    def _write(self, world, compliance_state, previous):
        return store._write_evaluation_state(
            DB, "quarantined", USER, "ev-new", "2026-11-05T00:00:00+00:00", NEW, 1, previous,
            compliance_state,
            "completed", [], {})

    def test_a_verdict_removes_it_and_an_error_keeps_it(self):
        with World() as world:
            world.bind_database(NEW)
            marked = world.state("quarantined")
            # An evaluation that ends in error decides nothing: the asset stays awaiting.
            assert self._write(world, None, marked)
            assert world.state("quarantined")["awaitingReevaluationReason"] == "schemaBound"
            # One that applies a verdict decides it.
            assert self._write(world, "compliant", world.state("quarantined"))
            row = world.state("quarantined")
            assert row["complianceState"] == "compliant"
            assert _awaiting(row) == dict.fromkeys(AWAITING)
            assert world.download_blocked("quarantined") is False

    def test_a_marker_set_after_the_evaluation_started_stays(self):
        with World() as world:
            unmarked = world.state("quarantined")
            world.bind_database(NEW)
            assert self._write(world, "quarantined", unmarked)
            assert world.state("quarantined")["awaitingReevaluationReason"] == "schemaBound"


@pytest.mark.unit
class TestTheMarkerTimeIsWhenTheChangeReachedTheRecord:
    """D158: a database binding change reaches its records one by one, and each record's marker
    is stamped when the change writes it, not when the request began. An evaluation that began in
    between, under the binding before the change, then keeps the change's marker past its
    quarantining verdict (the D152 rule: a marker set after the evaluation began stays); with the
    request's start time the verdict removed it, and the asset no longer said what it awaits."""

    @staticmethod
    def _evaluation_begins_while_the_change_runs(world, request):
        """Run `request` with an evaluation of the quarantined asset beginning right after the
        change reads the database's records; returns that evaluation's start."""
        apply_module = _bindingApply.binding_apply
        real_bind, real_unbind = apply_module.bind_record, apply_module.unbind_record
        started = []

        def bind(database_id, asset_id, existing, *args):
            if asset_id == "quarantined":
                started.append(store.now_iso())
            return real_bind(database_id, asset_id, existing, *args)

        def unbind(database_id, item, *args):
            if item["assetId"] == "quarantined":
                started.append(store.now_iso())
            return real_unbind(database_id, item, *args)
        # The record has been read; the evaluation begins before the change writes it.
        with patch.object(apply_module, "bind_record", side_effect=bind), \
                patch.object(apply_module, "unbind_record", side_effect=unbind):
            status, _ = request()
        assert status == 202 and started
        return started[-1]

    @staticmethod
    def _quarantining_verdict(world, evaluated_at):
        """That evaluation (under the old binding) ends quarantined."""
        with patch.object(store, "now_iso", return_value="2026-12-01T00:00:00+00:00"):
            assert store._write_evaluation_state(
                DB, "quarantined", USER, "ev-mid", evaluated_at, OLD, 1,
                world.state("quarantined"), "quarantined", "completed", [], {})

    def test_binding_a_new_schema(self):
        with World(auto_eval=False) as world:
            evaluated_at = self._evaluation_begins_while_the_change_runs(
                world, lambda: world.bind_database(NEW))
            row = world.state("quarantined")
            assert row["awaitingReevaluationSince"] > evaluated_at
            assert row["awaitingReevaluationSince"] == row["updatedAt"]
            self._quarantining_verdict(world, evaluated_at)
            row = world.state("quarantined")
            assert (row["awaitingReevaluationReason"], row["awaitingReevaluationSchema"]) == (
                "schemaBound", NEW)

    def test_removing_the_binding(self):
        with World(auto_eval=False) as world:
            evaluated_at = self._evaluation_begins_while_the_change_runs(
                world, world.unbind_database)
            row = world.state("quarantined")
            assert row["awaitingReevaluationSince"] > evaluated_at
            self._quarantining_verdict(world, evaluated_at)
            assert world.state("quarantined")["awaitingReevaluationReason"] == "schemaUnbound"
