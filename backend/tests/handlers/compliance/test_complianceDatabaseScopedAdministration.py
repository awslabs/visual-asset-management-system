# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Database-scoped compliance administration in the handlers.

A compliance schema is authorized with its scope (`databaseId`: a database id or GLOBAL) and a
cascade with its parent asset's database. The tests hand each service an enforcer whose Tier-2
answer depends on (object type, action, databaseId) -- the shape a role scoped to one database has
-- and check that:

* the schema and cascade listings return only what the caller may GET, each item with the
  caller's allowed actions (`permissions`), and the schema listing the scopes the caller may create
  in (`createScopes`);
* create, update and delete of a schema are checked in its scope (and a re-registration or a move
  in the scope it replaces or moves to);
* binding a GLOBAL schema needs GET on it, not write access;
* a sweep and a cascade reach only the databases the caller may evaluate, and report the rest as
  skipped (not authorized), never evaluating them with the system identity;
* the quarantine listing carries per-row `permissions`.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, CASCADE_ID, DB, RULES_SCHEMA_BODY, USER, body_of, claims_for, rest_event, schema_row,
    update_values,
)
from backend.tests.handlers.compliance.test_complianceCascadeExecutor import Graph, _run_executor
from handlers.compliance import complianceCascadeService as cascade_svc
from handlers.compliance import complianceQuarantineService as quarantine_svc
from handlers.compliance import complianceSchemaBindingService as binding_svc
from handlers.compliance import complianceSchemaService as schema_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc

STORE = "handlers.compliance.complianceEvaluationStore"
OTHER_DB = "db2"
ALL = "*"


def scoped_enforcer(rules):
    """A CasbinEnforcer stand-in: Tier 1 passes; Tier 2 allows (object type, action) on the
    databases `rules` lists for it (ALL for any)."""
    instance = MagicMock(name="CasbinEnforcer")
    instance.enforceAPI.return_value = True

    def enforce(obj, action):
        allowed = rules.get((obj.get("object__type"), action), ())
        return allowed == ALL or obj.get("databaseId") in allowed
    instance.enforce.side_effect = enforce
    return instance


def database_admin_rules(database_id=DB):
    """The grants of a compliance administrator of `database_id` (compliance-admin.json): its
    schemas CRUD, GLOBAL schemas read, its bindings, evaluations and cascades."""
    return {
        ("complianceSchema", "GET"): {database_id, "GLOBAL"},
        ("complianceSchema", "POST"): {database_id},
        ("complianceSchema", "PUT"): {database_id},
        ("complianceSchema", "DELETE"): {database_id},
        ("complianceBinding", "GET"): {database_id},
        ("complianceBinding", "PUT"): {database_id},
        ("complianceBinding", "DELETE"): {database_id},
        ("complianceEvaluation", "GET"): {database_id},
        ("complianceEvaluation", "POST"): {database_id},
        ("complianceEvaluation", "DELETE"): {database_id},
        ("complianceCascade", "GET"): {database_id},
        ("complianceCascade", "POST"): {database_id},
        ("database", "GET"): {database_id},
        ("asset", "GET"): {database_id},
    }


# ---------------------------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------------------------
SCHEMA_ROWS = [schema_row("global-1", database_id="GLOBAL"), schema_row("own-1", database_id=DB),
               schema_row("other-1", database_id=OTHER_DB)]


def _schema_tables(schema_rows, query_rows=None):
    schema_table = MagicMock(name="schema_table")
    schema_table.scan.return_value = {"Items": list(schema_rows)}
    schema_table.query.return_value = {"Items": list(query_rows if query_rows is not None
                                                     else schema_rows[:1])}
    database_table = MagicMock(name="database_table")
    database_table.scan.return_value = {"Items": [{"databaseId": DB}, {"databaseId": OTHER_DB}]}
    database_table.get_item.return_value = {"Item": {"databaseId": DB}}
    state_table = MagicMock(name="asset_state_table")
    state_table.query.return_value = {"Items": []}
    return schema_table, database_table, state_table


def _run_schema(event, rules, schema_rows=SCHEMA_ROWS, query_rows=None, tokens=(USER,)):
    schema_table, database_table, state_table = _schema_tables(schema_rows, query_rows)
    with patch(f"{schema_svc.__name__}.request_to_claims", claims_for(*tokens)), \
            patch(f"{schema_svc.__name__}.CasbinEnforcer", return_value=scoped_enforcer(rules)), \
            patch(f"{schema_svc.__name__}.schema_table", schema_table), \
            patch(f"{schema_svc.__name__}.asset_state_table", state_table), \
            patch(f"{schema_svc.__name__}.database_table", database_table), \
            patch(f"{schema_svc.__name__}.audit_table", MagicMock()):
        response = schema_svc.lambda_handler(event, MagicMock())
    return response, schema_table


@pytest.mark.unit
class TestTheSchemaListing:

    def test_only_the_schemas_the_caller_may_get_are_listed(self):
        response, _ = _run_schema(rest_event("GET", "/compliance/schemas"), database_admin_rules())
        assert response["statusCode"] == 200
        listed = {s["schemaName"]: s for s in body_of(response)["schemas"]}
        assert sorted(listed) == ["global-1", "own-1"]

    def test_each_schema_carries_the_callers_allowed_actions(self):
        response, _ = _run_schema(rest_event("GET", "/compliance/schemas"), database_admin_rules())
        listed = {s["schemaName"]: s for s in body_of(response)["schemas"]}
        # GLOBAL: readable and bindable, not editable or deletable by a database administrator.
        assert listed["global-1"]["permissions"] == {"edit": False, "delete": False, "bind": True}
        assert listed["own-1"]["permissions"] == {"edit": True, "delete": True, "bind": True}

    def test_bind_follows_the_binding_permission_of_the_database_the_listing_names(self):
        rules = {**database_admin_rules(), ("complianceBinding", "PUT"): set()}
        response, _ = _run_schema(
            rest_event("GET", "/compliance/schemas", query_params={"databaseId": DB}), rules,
            schema_rows=SCHEMA_ROWS[:2])
        assert all(s["permissions"]["bind"] is False for s in body_of(response)["schemas"])

    def test_a_global_filter_names_no_database_to_bind_in(self):
        # The new-database form lists the schemas any database may bind with `databaseId=GLOBAL`;
        # there is no `complianceBinding` named GLOBAL to check.
        rules = {**database_admin_rules(), ("complianceBinding", "PUT"): set()}
        response, _ = _run_schema(
            rest_event("GET", "/compliance/schemas", query_params={"databaseId": "GLOBAL"}),
            rules, schema_rows=SCHEMA_ROWS[:1])
        assert body_of(response)["schemas"][0]["permissions"]["bind"] is True

    def test_a_system_schema_is_never_editable_by_another_caller(self):
        rows = [dict(schema_row("own-1", database_id=DB), isSystem=True)]
        response, _ = _run_schema(rest_event("GET", "/compliance/schemas"), database_admin_rules(),
                                  schema_rows=rows)
        permissions = body_of(response)["schemas"][0]["permissions"]
        assert permissions["edit"] is False and permissions["delete"] is False

    def test_the_creatable_scopes_are_the_ones_the_caller_may_post_in(self):
        response, _ = _run_schema(rest_event("GET", "/compliance/schemas"), database_admin_rules())
        assert body_of(response)["createScopes"] == [DB]

    def test_a_global_administrator_may_create_global_schemas(self):
        rules = {("complianceSchema", "GET"): ALL, ("complianceSchema", "POST"): {"GLOBAL"}}
        response, _ = _run_schema(rest_event("GET", "/compliance/schemas"), rules)
        assert body_of(response)["createScopes"] == ["GLOBAL"]
        assert len(body_of(response)["schemas"]) == 3

    def test_a_soft_deleted_database_is_not_a_creatable_scope(self):
        # The database service keeps a deleted database as `<id>#deleted`; a schema POST into it
        # is refused, so the create form must not offer it.
        database_table = MagicMock(name="database_table")
        database_table.scan.return_value = {"Items": [
            {"databaseId": DB}, {"databaseId": f"{OTHER_DB}#deleted"},
            {"databaseId": "old-db#deleted"}, {"databaseId": OTHER_DB}]}
        allow_all = MagicMock(name="CasbinEnforcer")
        allow_all.enforce.return_value = True
        with patch.object(schema_svc, "database_table", database_table):
            scopes = schema_svc.creatable_scopes(allow_all)
        assert scopes == ["GLOBAL", DB, OTHER_DB]
        checked = [call.args[0]["databaseId"] for call in allow_all.enforce.call_args_list]
        assert DB in checked and OTHER_DB in checked
        assert not any("#deleted" in database_id for database_id in checked)


def _create(database_id, name="new-1"):
    return rest_event("POST", "/compliance/schemas", body={
        "schemaName": name, "databaseId": database_id, "schemaBody": RULES_SCHEMA_BODY})


@pytest.mark.unit
class TestSchemaWritesAreCheckedInTheirScope:

    def test_creating_in_the_callers_database_is_allowed(self):
        response, table = _run_schema(_create(DB), database_admin_rules(), query_rows=[])
        assert response["statusCode"] == 200
        table.put_item.assert_called_once()

    @pytest.mark.parametrize("scope", ["GLOBAL", OTHER_DB])
    def test_creating_in_another_scope_is_refused(self, scope):
        response, table = _run_schema(_create(scope), database_admin_rules(), query_rows=[])
        assert response["statusCode"] == 403
        table.put_item.assert_not_called()

    def test_re_registering_another_databases_schema_name_is_refused(self):
        # A new version would replace the other database's schema and move it into this one.
        response, table = _run_schema(_create(DB, name="other-1"), database_admin_rules(),
                                      query_rows=[schema_row("other-1", database_id=OTHER_DB)])
        assert response["statusCode"] == 403
        table.put_item.assert_not_called()

    @pytest.mark.parametrize("method,body", [("PUT", {"description": "x"}), ("DELETE", None)])
    def test_a_global_schema_cannot_be_changed_by_a_database_administrator(self, method, body):
        response, table = _run_schema(
            rest_event(method, "/compliance/schemas/global-1", {"schemaName": "global-1"},
                       body=body),
            database_admin_rules(), query_rows=[schema_row("global-1", database_id="GLOBAL")])
        assert response["statusCode"] == 403
        table.put_item.assert_not_called()
        table.batch_writer.assert_not_called()

    def test_the_own_databases_schema_can_be_updated(self):
        response, table = _run_schema(
            rest_event("PUT", "/compliance/schemas/own-1", {"schemaName": "own-1"},
                       body={"description": "x"}),
            database_admin_rules(), query_rows=[schema_row("own-1", database_id=DB)])
        assert response["statusCode"] == 200
        table.put_item.assert_called_once()

    def test_moving_a_schema_to_a_scope_the_caller_cannot_write_is_refused(self):
        response, table = _run_schema(
            rest_event("PUT", "/compliance/schemas/own-1", {"schemaName": "own-1"},
                       body={"databaseId": "GLOBAL"}),
            database_admin_rules(), query_rows=[schema_row("own-1", database_id=DB)])
        assert response["statusCode"] == 403
        table.put_item.assert_not_called()

    def test_another_databases_schema_cannot_be_read(self):
        response, _ = _run_schema(
            rest_event("GET", "/compliance/schemas/other-1", {"schemaName": "other-1"}),
            database_admin_rules(), query_rows=[schema_row("other-1", database_id=OTHER_DB)])
        assert response["statusCode"] == 403

    def test_an_absent_schema_is_checked_in_the_global_scope(self):
        rules = {("complianceSchema", "GET"): {DB}}
        response, _ = _run_schema(
            rest_event("GET", "/compliance/schemas/none", {"schemaName": "none"}), rules,
            query_rows=[])
        assert response["statusCode"] == 403


# ---------------------------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestBindingAGlobalSchema:

    def _bind(self, rules, schema_database_id):
        schema_table = MagicMock(name="schema_table")
        schema_table.query.return_value = {"Items": [schema_row(database_id=schema_database_id)]}
        database_table = MagicMock(name="database_table")
        database_table.get_item.return_value = {"Item": {"databaseId": DB}}
        state_table = MagicMock(name="asset_state_table")
        state_table.query.return_value = {"Items": []}
        mod = binding_svc.__name__
        with patch(f"{mod}.request_to_claims", claims_for(USER)), \
                patch(f"{mod}.CasbinEnforcer", return_value=scoped_enforcer(rules)), \
                patch(f"{mod}.schema_table", schema_table), \
                patch(f"{mod}.database_table", database_table), \
                patch(f"{mod}.asset_state_table", state_table), \
                patch(f"{mod}.asset_table", MagicMock()), \
                patch(f"{STORE}.update_asset_state"), patch(f"{STORE}.write_audit"):
            response = binding_svc.lambda_handler(
                rest_event("PUT", f"/compliance/bind/{DB}", {"databaseId": DB},
                           body={"schemaName": "schema-1"}), MagicMock())
        return response, database_table

    def test_read_access_to_the_global_schema_is_enough(self):
        response, database_table = self._bind(database_admin_rules(), "GLOBAL")
        assert response["statusCode"] == 200
        database_table.update_item.assert_called_once()

    def test_another_databases_schema_is_refused_before_it_is_bound(self):
        response, database_table = self._bind(database_admin_rules(), OTHER_DB)
        assert response["statusCode"] == 403
        database_table.update_item.assert_not_called()

    def test_without_the_databases_binding_permission_nothing_is_bound(self):
        rules = {**database_admin_rules(), ("complianceBinding", "PUT"): set()}
        response, database_table = self._bind(rules, "GLOBAL")
        assert response["statusCode"] == 403
        database_table.update_item.assert_not_called()


# ---------------------------------------------------------------------------------------------
# Cascades
# ---------------------------------------------------------------------------------------------
def _cascade(database_id, cascade_id=CASCADE_ID, state="pending_approval"):
    return {"cascadeId": cascade_id, "state": state, "triggeredByDatabaseId": database_id,
            "triggeredByAssetId": ASSET, "createdAt": "2026-10-27T00:00:00+00:00"}


def _run_cascade(event, rules, cascade_item=None, rows=(), databases=(DB, OTHER_DB)):
    cascade_table = MagicMock(name="cascade_table")
    cascade_table.get_item.return_value = {"Item": dict(cascade_item)} if cascade_item else {}
    cascade_table.query.return_value = {"Items": [dict(r) for r in rows]}
    mod = cascade_svc.__name__
    with patch(f"{mod}.request_to_claims", claims_for(USER)), \
            patch(f"{mod}.CasbinEnforcer", return_value=scoped_enforcer(rules)), \
            patch(f"{mod}.cascade_table", cascade_table), \
            patch(f"{mod}.lambda_client", MagicMock()), \
            patch(f"{mod}.cascade_executor_function_name", "vams-complianceCascadeExecutor"), \
            patch(f"{STORE}.get_asset_item", return_value={"assetId": ASSET}), \
            patch(f"{STORE}.all_database_ids", return_value=list(databases)), \
            patch(f"{STORE}.write_audit"):
        response = cascade_svc.lambda_handler(event, MagicMock())
    return response, cascade_table


@pytest.mark.unit
class TestTheCascadeScope:

    def test_the_listing_returns_only_the_callers_databases_cascades(self):
        rows = [_cascade(DB), _cascade(OTHER_DB, cascade_id="5b1e2c3d-0000-4000-8000-000000000002")]
        response, _ = _run_cascade(rest_event("GET", "/compliance/cascades"),
                                   database_admin_rules(), rows=rows)
        listed = body_of(response)["cascades"]
        assert [c["databaseId"] for c in listed] == [DB]
        assert listed[0]["permissions"] == {"approve": True}

    def test_a_reader_may_not_approve(self):
        rules = {("complianceCascade", "GET"): {DB}, ("complianceEvaluation", "GET"): {DB}}
        response, _ = _run_cascade(rest_event("GET", "/compliance/cascades"), rules,
                                   rows=[_cascade(DB)])
        assert body_of(response)["cascades"][0]["permissions"] == {"approve": False}

    def test_a_cascade_that_is_no_longer_pending_cannot_be_approved(self):
        response, _ = _run_cascade(
            rest_event("GET", "/compliance/cascades", query_params={"state": "completed"}),
            database_admin_rules(), rows=[_cascade(DB, state="completed")])
        assert body_of(response)["cascades"][0]["permissions"] == {"approve": False}

    @pytest.mark.parametrize("method,suffix", [("GET", ""), ("POST", "/approve"),
                                               ("POST", "/reject")])
    def test_another_databases_cascade_is_refused(self, method, suffix):
        response, table = _run_cascade(
            rest_event(method, f"/compliance/cascades/{CASCADE_ID}{suffix}",
                       {"cascadeId": CASCADE_ID}, body={} if method == "POST" else None),
            database_admin_rules(), cascade_item=_cascade(OTHER_DB))
        assert response["statusCode"] == 403
        table.update_item.assert_not_called()

    def test_approving_records_the_databases_the_approver_may_evaluate(self):
        response, table = _run_cascade(
            rest_event("POST", f"/compliance/cascades/{CASCADE_ID}/approve",
                       {"cascadeId": CASCADE_ID}, body={}),
            database_admin_rules(), cascade_item=_cascade(DB))
        assert response["statusCode"] == 202
        transition = next(u for u in update_values(table) if u.get("state") == "executing")
        assert json.loads(transition["authorizedDatabaseIds"]) == [DB]

    def test_a_cascade_started_without_approval_records_the_creators_reach(self):
        response, table = _run_cascade(
            rest_event("POST", "/compliance/cascades",
                       body={"databaseId": DB, "assetId": ASSET, "requireApproval": False}),
            database_admin_rules())
        assert response["statusCode"] == 202
        item = table.put_item.call_args.kwargs["Item"]
        assert json.loads(item["authorizedDatabaseIds"]) == [DB]

    def test_a_cascade_names_its_parent_asset(self):
        row = dict(_cascade(DB), triggeredByAssetName="Turbine")
        response, _ = _run_cascade(
            rest_event("GET", f"/compliance/cascades/{CASCADE_ID}", {"cascadeId": CASCADE_ID}),
            database_admin_rules(), cascade_item=row)
        assert body_of(response)["assetName"] == "Turbine"

    def test_the_recorded_reach_is_never_returned(self):
        row = dict(_cascade(DB), authorizedDatabaseIds=json.dumps([DB]))
        response, _ = _run_cascade(
            rest_event("GET", f"/compliance/cascades/{CASCADE_ID}", {"cascadeId": CASCADE_ID}),
            database_admin_rules(), cascade_item=row)
        assert "authorizedDatabaseIds" not in body_of(response)
        listing, _ = _run_cascade(rest_event("GET", "/compliance/cascades"),
                                  database_admin_rules(), rows=[row])
        assert "authorizedDatabaseIds" not in body_of(listing)["cascades"][0]


@pytest.mark.unit
class TestTheExecutorStaysWithinTheRecordedReach:

    class _CrossDatabaseGraph(Graph):
        """ASSET (db1) -> B (db1) and ASSET -> C (db2)."""

        def __init__(self):
            self.edges = [
                {"fromAssetDatabaseId": DB, "fromAssetId": ASSET, "toAssetDatabaseId": DB,
                 "toAssetId": "B", "relationshipType": "parentChild"},
                {"fromAssetDatabaseId": DB, "fromAssetId": ASSET, "toAssetDatabaseId": OTHER_DB,
                 "toAssetId": "C", "relationshipType": "parentChild"},
            ]

    def test_a_descendant_outside_the_reach_is_skipped_not_authorized(self):
        cascade = dict(_cascade(DB, state="executing"), authorizedDatabaseIds=json.dumps([DB]))
        result, mocks = _run_executor(self._CrossDatabaseGraph(), cascade)
        statuses = {r["node"]: r["status"] for r in result["results"]}
        assert statuses == {f"{DB}:B": "compliant", f"{OTHER_DB}:C": "skipped_not_authorized"}
        evaluated = [c.args[:2] for c in mocks["run_evaluation"].call_args_list]
        assert evaluated == [(DB, "B")]

    def test_an_empty_reach_evaluates_nothing(self):
        cascade = dict(_cascade(DB, state="executing"), authorizedDatabaseIds=json.dumps([]))
        result, mocks = _run_executor(self._CrossDatabaseGraph(), cascade)
        assert {r["status"] for r in result["results"]} == {"skipped_not_authorized"}
        mocks["run_evaluation"].assert_not_called()

    def test_a_cascade_without_a_recorded_reach_evaluates_every_descendant(self):
        # A cascade started before the reach was recorded keeps its earlier behaviour.
        result, mocks = _run_executor(self._CrossDatabaseGraph(), _cascade(DB, state="executing"))
        assert {r["status"] for r in result["results"]} == {"compliant"}
        assert mocks["run_evaluation"].call_count == 2


# ---------------------------------------------------------------------------------------------
# Sweep and quarantine
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestTheSweepReachesOnlyTheCallersDatabases:

    def test_a_global_schema_sweep_skips_the_databases_the_caller_may_not_evaluate(self):
        state_table = MagicMock(name="asset_state_table")
        state_table.query.return_value = {"Items": [
            {"databaseId": DB, "assetId": "a", "schemaName": "schema-1"},
            {"databaseId": OTHER_DB, "assetId": "c", "schemaName": "schema-1"}]}
        mod = evaluate_svc.__name__
        with patch(f"{mod}.request_to_claims", claims_for(USER)), \
                patch(f"{mod}.CasbinEnforcer", return_value=scoped_enforcer(database_admin_rules())), \
                patch(f"{mod}.asset_state_table", state_table), \
                patch(f"{STORE}.load_schema_item", return_value=schema_row()), \
                patch(f"{STORE}.databases_bound_to_schema", return_value=[]), \
                patch(f"{STORE}.get_asset_item", return_value={"databaseId": "db", "assetId": "present"}), \
                patch(f"{mod}.run_evaluation_for_asset",
                      return_value={"evaluationId": "e", "verdict": "compliant"}) as run:
            response = evaluate_svc.lambda_handler(
                rest_event("POST", "/compliance/sweep/schema-1", {"schemaName": "schema-1"},
                           body={}), MagicMock())
        body = body_of(response)
        assert response["statusCode"] == 200
        assert [(a["databaseId"], a["assetId"]) for a in body["assetsTriggered"]] == [(DB, "a")]
        assert body["skipped"] == 1 and body["skippedNotAuthorized"] == 1
        assert [c.args[:2] for c in run.call_args_list] == [(DB, "a")]


@pytest.mark.unit
class TestTheQuarantineRowsCarryTheirPermissions:

    def test_release_and_exception_follow_the_rows_database(self):
        rows = [{"databaseId": DB, "assetId": "a", "complianceState": "quarantined"},
                {"databaseId": OTHER_DB, "assetId": "c", "complianceState": "quarantined"}]
        rules = {("complianceEvaluation", "GET"): ALL, ("complianceEvaluation", "POST"): {DB}}
        state_table = MagicMock(name="asset_state_table")
        state_table.query.return_value = {"Items": rows}
        mod = quarantine_svc.__name__
        with patch(f"{mod}.request_to_claims", claims_for(USER)), \
                patch(f"{mod}.CasbinEnforcer", return_value=scoped_enforcer(rules)), \
                patch(f"{mod}.asset_state_table", state_table):
            response = quarantine_svc.lambda_handler(
                rest_event("GET", "/compliance/quarantine"), MagicMock())
        listed = {r["databaseId"]: r["permissions"] for r in body_of(response)["quarantinedAssets"]}
        assert listed == {DB: {"release": True, "exception": True},
                          OTHER_DB: {"release": False, "exception": False}}
