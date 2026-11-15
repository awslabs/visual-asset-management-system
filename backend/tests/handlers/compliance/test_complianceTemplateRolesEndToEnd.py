# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The compliance services against the real enforcer of a shipped template role.

The role is imported from `documentation/permissionsTemplates/` the way the import route does it
and enforced by the production `CasbinEnforcer` (object-type routed); the services see that
enforcer whatever claims they pass. Covered: the schema, cascade and quarantine listings with their
`permissions`, the creatable scopes, a refused GLOBAL create, and the sweep and cascade reach across
databases.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance._harness import (
    ASSET, CASCADE_ID, RULES_SCHEMA_BODY, body_of, claims_for, live_only_asset_table, mirror_engine,
    real_constants, rest_event, schema_row, update_values,
)
from handlers.compliance import (
    complianceCascadeService, complianceEvaluateService, complianceEvaluationStore,
    complianceQuarantineService, complianceSchemaService,
)
from backend.tests.handlers.auth.test_permission_templates_compliance_binding import (  # noqa: F401,E402
    DB_A, DB_B, USER_ID, roles_enforcer,
)

GLOBAL = "GLOBAL"
STORE = "handlers.compliance.complianceEvaluationStore"
TRIGGER = "handlers.compliance.complianceTrigger"


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    """The real permission field matrix for the enforcer and the template import, whatever was
    collected before this module: alone, or with the compliance tests only, the root harness's mock
    `common.constants` is in place and the enforcer would build the roles against it."""
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


@pytest.fixture(autouse=True)
def _no_archived_assets():
    """No asset of these tests is archived: the archived-asset reads find none (the archived case,
    over every shipped template, is `test_complianceArchivedAssetAccess.py`)."""
    with patch.object(complianceEvaluationStore, "asset_table", live_only_asset_table()), \
            patch.object(complianceQuarantineService, "asset_table", live_only_asset_table()), \
            patch.object(complianceEvaluateService, "asset_table", live_only_asset_table()):
        yield


# ---------------------------------------------------------------------------------------------
def _real(enforcer):
    """A CasbinEnforcer factory for the handler modules: Tier 2 is the template role's real
    enforcer, whatever claims the handler passes; Tier 1 passes (the role's route constraints are
    the template tests' subject, and the harness event is the REST shape)."""
    stand_in = MagicMock(name="CasbinEnforcer")
    stand_in.enforceAPI.return_value = True
    stand_in.enforce.side_effect = enforcer.enforce
    mirror_engine(stand_in, enforcer)
    return lambda _claims: stand_in


@pytest.fixture
def admin_of_a(roles_enforcer):
    return roles_enforcer({"compliance": ("compliance-admin.json", DB_A)})


@pytest.mark.unit
class TestTheServicesWithTheRealEnforcer:

    def test_the_schema_listing(self, admin_of_a):
        svc = complianceSchemaService
        rows = [schema_row("global-1", database_id=GLOBAL), schema_row("own-1", database_id=DB_A),
                schema_row("other-1", database_id=DB_B)]
        schema_table = MagicMock(scan=MagicMock(return_value={"Items": rows}))
        database_table = MagicMock(scan=MagicMock(return_value={
            "Items": [{"databaseId": DB_A}, {"databaseId": DB_B}]}))
        with patch(f"{svc.__name__}.request_to_claims", claims_for(USER_ID)), \
                patch(f"{svc.__name__}.CasbinEnforcer", _real(admin_of_a)), \
                patch(f"{svc.__name__}.schema_table", schema_table), \
                patch(f"{svc.__name__}.database_table", database_table):
            response = svc.lambda_handler(rest_event("GET", "/compliance/schemas"), MagicMock())
        body = body_of(response)
        listed = {s["schemaName"]: s["permissions"] for s in body["schemas"]}
        assert listed == {"global-1": {"edit": False, "delete": False, "bind": True},
                          "own-1": {"edit": True, "delete": True, "bind": True}}
        assert body["createScopes"] == [DB_A]

    def test_creating_a_global_schema_is_refused(self, admin_of_a):
        svc = complianceSchemaService
        schema_table = MagicMock(query=MagicMock(return_value={"Items": []}))
        with patch(f"{svc.__name__}.request_to_claims", claims_for(USER_ID)), \
                patch(f"{svc.__name__}.CasbinEnforcer", _real(admin_of_a)), \
                patch(f"{svc.__name__}.schema_table", schema_table), \
                patch(f"{svc.__name__}.database_table", MagicMock()):
            response = svc.lambda_handler(rest_event("POST", "/compliance/schemas", body={
                "schemaName": "new-1", "databaseId": GLOBAL, "schemaBody": RULES_SCHEMA_BODY}),
                MagicMock())
        assert response["statusCode"] == 403
        schema_table.put_item.assert_not_called()

    def test_the_cascade_listing_and_approval_reach(self, admin_of_a):
        svc = complianceCascadeService
        rows = [{"cascadeId": CASCADE_ID, "state": "pending_approval", "triggeredByDatabaseId": db,
                 "triggeredByAssetId": ASSET, "createdAt": "2026-10-27T00:00:00+00:00"}
                for db in (DB_A, DB_B)]
        cascade_table = MagicMock()
        cascade_table.query.return_value = {"Items": rows}
        cascade_table.get_item.return_value = {"Item": dict(rows[0])}
        common = [patch(f"{svc.__name__}.request_to_claims", claims_for(USER_ID)),
                  patch(f"{svc.__name__}.CasbinEnforcer", _real(admin_of_a)),
                  patch(f"{svc.__name__}.cascade_table", cascade_table),
                  patch(f"{svc.__name__}.lambda_client", MagicMock()),
                  patch(f"{svc.__name__}.cascade_executor_function_name", "executor"),
                  patch(f"{STORE}.all_database_ids", return_value=[DB_A, DB_B]),
                  patch(f"{STORE}.write_audit")]
        for p in common:
            p.start()
        try:
            listing = svc.lambda_handler(rest_event("GET", "/compliance/cascades"), MagicMock())
            approval = svc.lambda_handler(
                rest_event("POST", f"/compliance/cascades/{CASCADE_ID}/approve",
                           {"cascadeId": CASCADE_ID}, body={}), MagicMock())
        finally:
            for p in reversed(common):
                p.stop()
        cascades = body_of(listing)["cascades"]
        assert [(c["databaseId"], c["permissions"]) for c in cascades] == [
            (DB_A, {"approve": True})]
        assert approval["statusCode"] == 202
        transition = next(u for u in update_values(cascade_table) if u.get("state") == "executing")
        assert json.loads(transition["authorizedDatabaseIds"]) == [DB_A]

    def test_a_global_schema_sweep_evaluates_its_own_database_only(self, admin_of_a):
        svc = complianceEvaluateService
        state_table = MagicMock()
        state_table.query.return_value = {"Items": [
            {"databaseId": DB_A, "assetId": "a", "schemaName": "global-1"},
            {"databaseId": DB_B, "assetId": "b", "schemaName": "global-1"}]}
        with patch(f"{svc.__name__}.request_to_claims", claims_for(USER_ID)), \
                patch(f"{svc.__name__}.CasbinEnforcer", _real(admin_of_a)), \
                patch(f"{svc.__name__}.asset_state_table", state_table), \
                patch(f"{STORE}.load_schema_item",
                      return_value=schema_row("global-1", database_id=GLOBAL)), \
                patch(f"{STORE}.databases_bound_to_schema", return_value=[]), \
                patch(f"{TRIGGER}.asset_row_exists", return_value=True), \
                patch(f"{svc.__name__}.run_evaluation_for_asset",
                      return_value={"evaluationId": "e", "verdict": "compliant"}) as run:
            response = svc.lambda_handler(
                rest_event("POST", "/compliance/sweep/global-1", {"schemaName": "global-1"},
                           body={}), MagicMock())
        assert response["statusCode"] == 200
        assert [c.args[:2] for c in run.call_args_list] == [(DB_A, "a")]
        assert body_of(response)["skippedNotAuthorized"] == 1

    def test_the_quarantine_listing_permissions(self, roles_enforcer):
        svc = complianceQuarantineService
        # Admin of database A and read-only on database B: both rows listed, actions on A only.
        enforcer = roles_enforcer({"admin": ("compliance-admin.json", DB_A),
                                   "reader": ("compliance-readonly.json", DB_B)})
        state_table = MagicMock()
        state_table.query.return_value = {"Items": [
            {"databaseId": DB_A, "assetId": "a", "complianceState": "quarantined"},
            {"databaseId": DB_B, "assetId": "b", "complianceState": "quarantined"}]}
        with patch(f"{svc.__name__}.request_to_claims", claims_for(USER_ID)), \
                patch(f"{svc.__name__}.CasbinEnforcer", _real(enforcer)), \
                patch(f"{svc.__name__}.asset_state_table", state_table):
            response = svc.lambda_handler(rest_event("GET", "/compliance/quarantine"), MagicMock())
        listed = {r["databaseId"]: r["permissions"] for r in body_of(response)["quarantinedAssets"]}
        assert listed == {DB_A: {"release": True, "exception": True},
                          DB_B: {"release": False, "exception": False}}
