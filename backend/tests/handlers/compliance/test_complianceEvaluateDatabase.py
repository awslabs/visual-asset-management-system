# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /compliance/evaluate/{databaseId}: the database overview's "Evaluate All Assets" evaluates
every tracked asset of THAT database, each against the schema its own row binds (the database
binding, or the asset's override), and nothing of any other database — the schema-wide sweep it
used to call evaluated other databases bound to the same GLOBAL schema and skipped this database's
overrides. Same bounds as a sweep: pending first, at most 200 evaluations, a 20 s launch budget."""

from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    DB, SCHEMA, USER, body_of, claims_for, enforcer, rest_event)
from handlers.compliance import complianceEvaluateService as svc

MOD = "handlers.compliance.complianceEvaluateService"
STORE = "handlers.compliance.complianceEvaluationStore"
PATH = f"/compliance/evaluate/{DB}"
PARAMS = {"databaseId": DB}
RESULT = {"evaluationId": "eval-1", "verdict": "compliant"}


def _row(asset_id, schema=SCHEMA, state="compliant", source="database"):
    row = {"databaseId": DB, "assetId": asset_id, "complianceState": state, "schemaSource": source}
    if schema:
        row["schemaName"] = schema
    return row


def _run(rows, database_item=None, casbin=None, registered=0, result=RESULT, clock=None,
         body=None):
    state_table = MagicMock(name="asset_state_table")
    state_table.query.return_value = {"Items": list(rows)}
    patches = [
        patch(f"{MOD}.request_to_claims", claims_for(USER)),
        patch(f"{MOD}.CasbinEnforcer", return_value=casbin or enforcer()),
        patch(f"{MOD}.asset_state_table", state_table),
        patch(f"{STORE}.get_database_item",
              return_value={"databaseId": DB, "complianceSchemaName": SCHEMA}
              if database_item is None else database_item),
        patch(f"{STORE}.register_unrecorded_assets", return_value=registered),
        patch(f"{STORE}.run_evaluation", return_value=result),
        # Every asset exists: a sweep evaluates it rather than removing its record.
        patch(f"{STORE}.get_asset_item", return_value={"databaseId": "db", "assetId": "present"}),
        patch(f"{MOD}.check_and_trigger_cascade"),
    ]
    if clock is not None:
        patches.append(patch(f"{MOD}.monotonic", side_effect=clock))
    started = [p.start() for p in patches]
    try:
        response = svc.lambda_handler(rest_event("POST", PATH, PARAMS, body=body or {}),
                                      MagicMock())
    finally:
        for p in reversed(patches):
            p.stop()
    return response, {"state": state_table, "register": started[4], "run": started[5]}


@pytest.mark.unit
class TestEvaluateDatabase:

    def test_every_tracked_asset_is_evaluated_against_its_own_schema(self):
        rows = [_row("a"), _row("b", schema="override-s", source="asset"), _row("c")]
        response, mocks = _run(rows)
        assert response["statusCode"] == 200
        body = body_of(response)
        assert body["databaseId"] == DB
        evaluated = [(c.args[1], c.args[2]) for c in mocks["run"].call_args_list]
        assert evaluated == [("a", SCHEMA), ("b", "override-s"), ("c", SCHEMA)]
        assert {c.args[0] for c in mocks["run"].call_args_list} == {DB}
        assert [t["assetId"] for t in body["assetsTriggered"]] == ["a", "b", "c"]
        assert body["assetsRemaining"] == 0

    def test_only_this_databases_rows_are_read(self):
        _, mocks = _run([_row("a")])
        (call,) = mocks["state"].query.call_args_list
        built = call.kwargs["KeyConditionExpression"].get_expression()
        assert built["values"][0].name == "databaseId" and built["values"][1] == DB
        assert "IndexName" not in call.kwargs

    def test_pending_assets_are_evaluated_first(self):
        rows = [_row("a"), _row("p", state="pending_evaluation"), _row("b")]
        _, mocks = _run(rows)
        assert [c.args[1] for c in mocks["run"].call_args_list] == ["p", "a", "b"]

    def test_unrecorded_assets_of_a_bound_database_are_registered_first(self):
        response, mocks = _run([_row("a")], registered=2)
        mocks["register"].assert_called_once_with(DB, SCHEMA)
        assert body_of(response)["registered"] == 2

    def test_an_unbound_database_registers_nothing(self):
        _, mocks = _run([_row("a", schema="override-s", source="asset")],
                        database_item={"databaseId": DB})
        mocks["register"].assert_not_called()
        assert [c.args[2] for c in mocks["run"].call_args_list] == ["override-s"]

    def test_a_row_without_a_schema_is_counted_not_evaluated(self):
        response, mocks = _run([_row("a"), _row("n", schema=None)])
        assert [c.args[1] for c in mocks["run"].call_args_list] == ["a"]
        assert body_of(response)["withoutSchema"] == 1

    def test_at_most_200_assets_are_evaluated_and_the_rest_reported(self):
        rows = [_row(f"a{i:03d}") for i in range(205)]
        response, mocks = _run(rows)
        assert mocks["run"].call_count == 200
        assert body_of(response)["assetsRemaining"] == 5

    def test_no_launch_after_the_time_budget(self):
        readings = iter([0.0, 0.0, 1.0, 25.0, 25.0, 25.0])
        response, mocks = _run([_row("a"), _row("b"), _row("c"), _row("d")],
                               clock=lambda: next(readings))
        assert mocks["run"].call_count == 2
        assert body_of(response)["assetsRemaining"] == 2

    def test_a_superseded_evaluation_is_listed_and_counted(self):
        response, _ = _run([_row("a")], result={"evaluationId": "x", "verdict": "superseded",
                                                "superseded": True, "error": "newer"})
        body = body_of(response)
        assert body["superseded"] == 1
        assert body["assetsTriggered"][0]["superseded"] is True

    def test_a_missing_database_is_refused(self):
        response, mocks = _run([_row("a")], database_item={})
        assert response["statusCode"] == 400
        assert body_of(response)["message"] == "Database not found"
        mocks["run"].assert_not_called()

    def test_the_databases_evaluation_object_is_checked(self):
        casbin = enforcer()
        casbin.enforce.side_effect = lambda obj, action: not (
            obj.get("object__type") == "complianceEvaluation" and obj.get("databaseId") == DB)
        response, mocks = _run([_row("a")], casbin=casbin)
        assert response["statusCode"] == 403
        mocks["run"].assert_not_called()
        mocks["state"].query.assert_not_called()

    def test_tier_one_denial_writes_nothing(self):
        response, mocks = _run([_row("a")], casbin=enforcer(api=False))
        assert response["statusCode"] == 403
        mocks["run"].assert_not_called()

    def test_a_bad_database_id_is_rejected(self):
        event = rest_event("POST", "/compliance/evaluate/bad%20id", {"databaseId": "bad id"},
                           body={})
        with patch(f"{MOD}.request_to_claims", claims_for(USER)), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer()):
            response = svc.lambda_handler(event, MagicMock())
        assert response["statusCode"] == 400
