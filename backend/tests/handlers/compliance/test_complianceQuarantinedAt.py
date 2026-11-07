# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""When an asset's quarantine started (D137).

The Quarantine page's "Quarantined At" showed the compliance record's `updatedAt`, which every
write to the record moves: a binding change that keeps the quarantine (D118), an override set or
cleared, a re-evaluation. The record now keeps `quarantinedAt`: the time of the write that put the
asset in quarantine (an evaluation, or an exception revoke that re-quarantines it), kept while it
stays quarantined, and null once it is released, granted an exception or evaluated to another
state. The evaluation store, the binding service and the quarantine service run against moto
tables built from the CDK definitions.
"""

from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    REAL_TO_UPDATE_EXPR, USER, body_of, claims_for, rest_event, schema_row,
)
from backend.tests.handlers.compliance.test_complianceUnknownDatabase import _stand_in
from common.compliance import evaluationEngine as engine
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceQuarantineService as quarantine_svc
from handlers.compliance import complianceSchemaBindingService as binding_svc

DB = "quarantine-time-db"
OLD, NEW = "old-schema", "new-schema"
ASSET = "a1"


class World:

    def __enter__(self):
        self._mock = mock_aws()
        self._mock.start()
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        self.patches, self.t = _cdkTables.table_doubles(
            resource, (store, binding_svc, quarantine_svc))
        sqs = MagicMock(name="sqs")
        sqs.send_message_batch.side_effect = lambda **kwargs: {"Failed": []}
        for module in (binding_svc, quarantine_svc):
            self.patches += [
                patch.object(module, "CasbinEnforcer", return_value=_stand_in({"*"})),
                patch.object(module, "request_to_claims", claims_for(USER)),
            ]
        self.patches += [
            patch.object(binding_svc, "sqs_client", sqs, create=True),
            patch.object(binding_svc, "trigger_queue_url", "", create=True),
            patch.object(store, "to_update_expr", REAL_TO_UPDATE_EXPR),
        ]
        for active in self.patches:
            active.start()
        for name in (OLD, NEW):
            self.t["complianceSchemaStorage"].put_item(Item=schema_row(name, database_id=DB))
        self.t["databaseStorage"].put_item(Item={"databaseId": DB, "complianceSchemaName": OLD,
                                                 "complianceAutoEval": False})
        self.t["assetStorage"].put_item(Item={"databaseId": DB, "assetId": ASSET,
                                              "assetName": ASSET})
        self.t["complianceAssetStateStorage"].put_item(Item={
            "databaseId": DB, "assetId": ASSET, "complianceState": "compliant",
            "schemaName": OLD, "schemaSource": "database", "assetName": ASSET,
            "assetNameSort": ASSET, "lastEvaluationId": "ev-0",
            "lastEvaluatedAt": "2026-10-01T00:00:00+00:00",
            "updatedAt": "2026-10-01T00:00:00+00:00"})
        self.evaluations = 0
        return self

    def __exit__(self, *exc):
        for active in reversed(self.patches):
            active.stop()
        self._mock.stop()

    def state(self):
        return self.t["complianceAssetStateStorage"].get_item(
            Key={"databaseId": DB, "assetId": ASSET}).get("Item")

    def evaluate(self, compliance_state, at):
        """An evaluation of the asset whose verdict applies at `at` (its state write's time)."""
        self.evaluations += 1
        evaluation_id = f"ev-{self.evaluations}"
        self.t["complianceEvaluationStorage"].put_item(Item={
            "evaluationId": evaluation_id, "databaseId": DB, "assetId": ASSET,
            "databaseId:assetId": f"{DB}:{ASSET}", "schemaName": OLD, "schemaVersion": 1,
            "evaluatedAt": at, "status": "completed", "verdict": compliance_state,
            "ruleResults": "[]"})
        # The state write is stamped with the time of the write (D155): `at` here.
        with patch.object(store, "now_iso", return_value=at):
            assert store._write_evaluation_state(
                DB, ASSET, USER, evaluation_id, at, OLD, 1, self.state(), compliance_state,
                "completed", [], {})

    def call(self, service, method, path, params=None, body=None, query=None):
        response = service.lambda_handler(
            rest_event(method, path, params, query, body=body), MagicMock())
        return response["statusCode"], body_of(response)

    def asset_path(self, prefix):
        return f"{prefix}/{DB}/{ASSET}", {"databaseId": DB, "assetId": ASSET}


QUARANTINED = "2026-11-05T00:48:32+00:00"


@pytest.mark.unit
class TestTheQuarantineTime:

    def test_an_evaluation_that_quarantines_records_it_and_the_quarantine_keeps_it(self):
        with World() as world:
            world.evaluate("quarantined", QUARANTINED)
            assert world.state()["quarantinedAt"] == QUARANTINED
            # A re-evaluation that quarantines it again does not restart it.
            world.evaluate("quarantined", "2026-11-05T00:50:00+00:00")
            row = world.state()
            assert row["quarantinedAt"] == QUARANTINED
            assert row["updatedAt"] == "2026-11-05T00:50:00+00:00"

    def test_binding_changes_that_keep_the_quarantine_do_not_move_it(self):
        """Bind another schema, set and clear an override, remove the binding (D118): the record
        changes each time, its quarantine start does not (the round-24 repro)."""
        with World() as world:
            world.evaluate("quarantined", QUARANTINED)
            updates = []
            path, params = world.asset_path("/compliance/bind")
            for method, request_path, request_params, body in (
                    ("PUT", f"/compliance/bind/{DB}", {"databaseId": DB}, {"schemaName": NEW}),
                    ("PUT", path, params, {"schemaName": OLD}),
                    ("DELETE", path, params, None),
                    ("DELETE", f"/compliance/bind/{DB}", {"databaseId": DB}, None)):
                status, _ = world.call(binding_svc, method, request_path, request_params, body)
                assert status == 200
                row = world.state()
                assert row["complianceState"] == "quarantined"
                assert row["quarantinedAt"] == QUARANTINED
                updates.append(row["updatedAt"])
            assert all(updated > QUARANTINED for updated in updates)
            # The listing returns it beside `updatedAt`.
            status, body = world.call(quarantine_svc, "GET", "/compliance/quarantine")
            assert status == 200
            (listed,) = body["quarantinedAssets"]
            assert (listed["quarantinedAt"], listed["updatedAt"]) == (QUARANTINED, updates[-1])

    def test_another_verdict_clears_it_and_the_next_quarantine_starts_anew(self):
        with World() as world:
            world.evaluate("quarantined", QUARANTINED)
            world.evaluate("compliant", "2026-11-05T01:00:00+00:00")
            assert world.state()["quarantinedAt"] is None
            world.evaluate("quarantined", "2026-11-05T02:00:00+00:00")
            assert world.state()["quarantinedAt"] == "2026-11-05T02:00:00+00:00"

    def test_an_evaluation_with_no_verdict_leaves_it(self):
        with World() as world:
            world.evaluate("quarantined", QUARANTINED)
            assert store._write_evaluation_state(
                DB, ASSET, USER, "ev-error", "2026-11-05T03:00:00+00:00", OLD, 1, world.state(),
                None, "error", [], {})
            assert world.state()["quarantinedAt"] == QUARANTINED

    def test_a_release_clears_it(self):
        with World() as world:
            world.evaluate("quarantined", QUARANTINED)
            path, params = world.asset_path("/compliance/quarantine")
            status, _ = world.call(quarantine_svc, "POST", f"{path}/release", params,
                                   {"reason": "checked by hand"})
            assert status == 200
            row = world.state()
            assert (row["complianceState"], row["quarantinedAt"]) == ("compliant", None)

    def test_an_exception_clears_it_and_its_revoke_starts_a_new_quarantine(self):
        with World() as world:
            world.evaluate("quarantined", QUARANTINED)
            path, params = world.asset_path("/compliance/quarantine")
            status, _ = world.call(quarantine_svc, "POST", f"{path}/exception", params,
                                   {"reason": "accepted risk"})
            assert status == 200
            row = world.state()
            assert (row["complianceState"], row["quarantinedAt"]) == ("exception", None)
            status, body = world.call(quarantine_svc, "DELETE", f"{path}/exception", params)
            assert (status, body["complianceState"]) == (200, "quarantined")
            row = world.state()
            assert row["quarantinedAt"] == row["updatedAt"]
            assert row["quarantinedAt"] > QUARANTINED


MARKER = ("awaitingReevaluationSince", "awaitingReevaluationSchema", "awaitingReevaluationReason")


@pytest.mark.unit
class TestReleaseAndAnExceptionEndTheReevaluationMarker:
    """D151: a release or an exception grant ends the quarantine, so the awaiting-re-evaluation
    marker a binding change (D118) or a pending evaluation (D147) left on it goes too; it no longer
    tells a released asset's readers that a quarantine holds."""

    @pytest.mark.parametrize("action", ["release", "exception"])
    @pytest.mark.parametrize("marked_by", ["binding change", "pending evaluation"])
    def test_the_marker_goes_with_the_quarantine(self, action, marked_by):
        with World() as world:
            world.evaluate("quarantined", QUARANTINED)
            if marked_by == "binding change":
                status, _ = world.call(binding_svc, "PUT", f"/compliance/bind/{DB}",
                                       {"databaseId": DB}, {"schemaName": NEW})
                assert status == 200
            else:
                world.t["complianceAssetStateStorage"].update_item(
                    Key={"databaseId": DB, "assetId": ASSET},
                    UpdateExpression="SET awaitingReevaluationSince = :t, "
                                     "awaitingReevaluationReason = :r, awaitingReevaluationSchema = :s",
                    ExpressionAttributeValues={":t": QUARANTINED, ":r": "evaluationPending",
                                               ":s": OLD})
            assert world.state()["awaitingReevaluationSince"]
            path, params = world.asset_path("/compliance/quarantine")
            status, _ = world.call(quarantine_svc, "POST", f"{path}/{action}", params,
                                   {"reason": "checked by hand"})
            assert status == 200
            row = world.state()
            assert row["complianceState"] == ("compliant" if action == "release" else "exception")
            assert {key: row.get(key) for key in MARKER} == dict.fromkeys(MARKER)


@pytest.mark.unit
@pytest.mark.parametrize("previous, new, expected", [
    (None, engine.STATE_QUARANTINED, {"quarantinedAt": "t"}),
    (engine.STATE_COMPLIANT, engine.STATE_QUARANTINED, {"quarantinedAt": "t"}),
    (engine.STATE_EXCEPTION, engine.STATE_QUARANTINED, {"quarantinedAt": "t"}),
    (engine.STATE_QUARANTINED, engine.STATE_QUARANTINED, {}),
    (engine.STATE_QUARANTINED, engine.STATE_COMPLIANT, {"quarantinedAt": None}),
    (engine.STATE_QUARANTINED, engine.STATE_PENDING_EVALUATION,
     {"quarantinedAt": None}),
])
def test_quarantined_at_fields(previous, new, expected):
    assert engine.QUARANTINED_AT_FIELD == "quarantinedAt"
    assert engine.quarantined_at_fields(previous, new, "t") == expected
