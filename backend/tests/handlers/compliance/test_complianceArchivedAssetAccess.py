# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The compliance records and actions of an archived asset are decided the way the asset routes
decide the archived asset itself.

Archiving an asset moves its asset-table row to `<databaseId>#deleted`; its compliance record,
evaluations, quarantine, audit entries, override and cascades keep the live id. The asset routes
hand the archived row to the engine as stored, so `CasbinEnforcerService.enforce` allows it only when
both ids pass. These tests run the compliance services on a seeded archive (moto tables built from
the CDK definitions) with the production enforcer of every shipped role: each template of
`documentation/permissionsTemplates/`, the compliance ones and the others, and both seeded roles
(`backend/tests/handlers/authz/shipped_constraints.py`), plus a role named on both ids and that role
with a deny on the live id.

For every role, every compliance read and action on the archived asset is compared with the same
check on a live asset of the database and with the asset GET the asset routes make on each row:
the archived asset's compliance data is available exactly where the live asset's is, less what the
archived-record rule withholds from the asset itself, and less the overview and the quarantine
listing, which leave an archived asset out for every role. Where the role's live compliance decision
and its live asset GET agree, that is the archived asset GET decision itself.
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.authz import shipped_constraints
from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    RULES_SCHEMA_BODY, body_of, claims_for, mirror_engine, real_constants, rest_event, schema_row,
)
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceCascadeService as cascade_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceQuarantineService as quarantine_svc
from handlers.compliance import complianceSchemaBindingService as binding_svc

DB = "arch-db"
ARCHIVED_DB = f"{DB}#deleted"
LIVE = "live-asset"
ARCHIVED = "archived-asset"
SCHEMA = "arch-schema"
USER_ID = "archived-access-user"
CASCADE_IDS = {LIVE: "7a0c0000-0000-4000-8000-000000000001",
               ARCHIVED: "7a0c0000-0000-4000-8000-000000000002"}
VARIABLES = {"DATABASE_ID": DB, "TAG_VALUE": "locked"}
# The tag of the archived asset that the tag-deny role is refused on (D90).
DENIED_TAG = "restricted"
SERVICES = (store, evaluate_svc, quarantine_svc, audit_svc, cascade_svc, binding_svc)
ASSET_ROWS = {
    LIVE: {"databaseId": DB, "assetId": LIVE, "assetName": "Live Asset", "tags": []},
    ARCHIVED: {"databaseId": ARCHIVED_DB, "assetId": ARCHIVED, "assetName": "Archived Asset",
               "tags": [DENIED_TAG], "status": "archived"},
}

READS = ("state", "evaluations", "asset audit", "overview", "quarantine listing",
         "audit listing", "bindings listing", "cascade listing", "cascade")
ACTIONS = ("release", "grant exception", "revoke exception", "evaluate", "approve cascade",
           "remove override")
# The listings that leave an archived asset out for every role, as the asset listing does without
# archived assets: the asset is not evaluated while it is archived (test_complianceArchivedLeftOut).
LEFT_OUT = ("overview", "quarantine listing")


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    """The real permission field matrix for the enforcer and the template import (see
    test_complianceTemplateRolesEndToEnd)."""
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


# ---------------------------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------------------------
def _both_ids(constraints):
    """Each criterion naming DB also names DB#deleted (the shape #719 gives the Database Admin
    template's asset constraint)."""
    out = []
    for constraint in constraints:
        constraint = json.loads(json.dumps(constraint))
        keep, both = [], []
        for criterion in constraint.get("criteriaAnd", []):
            if criterion["field"] == "databaseId" and criterion["value"] == DB:
                both = [criterion, dict(criterion, id=f"{criterion.get('id', 'c')}-archived",
                                        value=ARCHIVED_DB)]
            else:
                keep.append(criterion)
        if both:
            constraint["criteriaAnd"] = keep
            constraint["criteriaOr"] = both
        out.append(constraint)
    return out


def _deny_on_live_id(role):
    """A deny of every compliance-evaluation action where databaseId equals the live id."""
    return {
        "constraintId": "deny-live", "name": "deny-live", "objectType": "complianceEvaluation",
        "criteriaAnd": [{"field": "databaseId", "id": "d", "operator": "equals", "value": DB}],
        "criteriaOr": [],
        "groupPermissions": [{"groupId": role, "id": f"d-{action}", "permission": action,
                              "permissionType": "deny"} for action in ("GET", "POST", "DELETE")],
        "userPermissions": [],
    }


def asset_read_deny(role, name, criterion, object_types=("asset", "database")):
    """A deny of every action on `object_types` where `criterion` holds: the shape the permissions
    model documents for hiding records from a role (D90)."""
    return [{
        "constraintId": f"{name}-{object_type}", "name": f"{name}-{object_type}",
        "objectType": object_type, "criteriaAnd": [dict(criterion, id=f"{name}-c")],
        "criteriaOr": [],
        "groupPermissions": [{"groupId": role, "id": f"{name}-{object_type}-{action}",
                              "permission": action, "permissionType": "deny"}
                             for action in ("GET", "PUT", "POST", "DELETE")],
        "userPermissions": [],
    } for object_type in object_types]


# The two Database Admin roles of D90: the template plus a deny on the archive of every database
# (`databaseId contains #deleted`, on the asset and database object types only, so the compliance
# grant on the archive stays), and the template plus a deny on one tag that only the archived
# asset carries (on the asset object type).
ARCHIVE_CONTAINS_DENY_SET = "synthetic:database-admin-with-archive-contains-deny"
TAG_DENY_SET = "synthetic:database-admin-with-tag-deny"

# Every shipped template on its own role, both seeded roles, two synthetic Compliance Admin roles
# (named on both ids; and that with a live-id deny) and the two D90 roles. Built on first use,
# under the real permission constants.
ROLE_SET_NAMES = sorted(
    [f"template:{name}" for name in shipped_constraints.template_names()]
    + [f"seeded:{role}" for role in shipped_constraints.SEEDED_ROLE_CONSTRUCTS]
    + ["synthetic:both-ids", "synthetic:both-ids-with-live-deny",
       ARCHIVE_CONTAINS_DENY_SET, TAG_DENY_SET])


def _role_set(set_name):
    """(roles, constraints) of one role set."""
    kind, name = set_name.split(":", 1)
    if kind == "template":
        role = f"tpl-{name.split('.')[0]}"
        return [role], shipped_constraints.template_constraints(name, role, VARIABLES)
    if kind == "seeded":
        return [name], shipped_constraints.seeded_constraints(name)
    if set_name in (ARCHIVE_CONTAINS_DENY_SET, TAG_DENY_SET):
        role = "tpl-database-admin"
        constraints = shipped_constraints.template_constraints(
            "database-admin.json", role, VARIABLES)
        if set_name == ARCHIVE_CONTAINS_DENY_SET:
            return [role], constraints + asset_read_deny(role, "deny-archive", {
                "field": "databaseId", "operator": "contains", "value": "#deleted"})
        return [role], constraints + asset_read_deny(role, "deny-tag", {
            "field": "tags", "operator": "is_one_of", "value": DENIED_TAG}, ("asset",))
    both = _both_ids(shipped_constraints.template_constraints(
        "compliance-admin.json", "both", VARIABLES))
    if name == "both-ids":
        return ["both"], both
    return ["both"], both + [_deny_on_live_id("both")]


def _enforcer(roles, constraints):
    """The production CasbinEnforcer of a user holding `roles`, only the table reads stubbed."""
    authz_module.casbin_user_policy_map = {}
    authz_module.casbin_user_enforcer_map = {}
    with patch.object(authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
                      return_value=[{"userId": USER_ID, "roleName": r} for r in roles]), \
            patch.object(authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
                         return_value=constraints):
        return authz_module.CasbinEnforcer({"tokens": [USER_ID], "roles": list(roles),
                                            "mfaEnabled": True})


@pytest.fixture
def role_enforcer():
    previous = (authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map)
    with patch.object(authz_module, "log_authorization", MagicMock()):
        yield _enforcer
    authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map = previous


def _asset_get(enforcer, asset_id):
    """The asset GET the asset routes make: the row as stored, annotated as an asset."""
    return bool(enforcer.enforce(dict(ASSET_ROWS[asset_id], object__type="asset"), "GET"))


# ---------------------------------------------------------------------------------------------
# The world: moto tables with a live and an archived asset of DB, each fully recorded
# ---------------------------------------------------------------------------------------------
def _seed(doubles):
    for row in ASSET_ROWS.values():
        doubles["assetStorage"].put_item(Item=dict(row))
    doubles["databaseStorage"].put_item(Item={"databaseId": DB, "complianceSchemaName": SCHEMA,
                                              "complianceAutoEval": False})
    doubles["complianceSchemaStorage"].put_item(
        Item=schema_row(SCHEMA, body=RULES_SCHEMA_BODY, database_id=DB))
    for asset_id in (LIVE, ARCHIVED):
        name = ASSET_ROWS[asset_id]["assetName"]
        doubles["complianceAssetStateStorage"].put_item(Item={
            "databaseId": DB, "assetId": asset_id, "schemaName": SCHEMA, "schemaSource": "asset",
            "complianceState": "quarantined", "quarantineReason": "rule failed",
            "lastEvaluationId": f"eval-{asset_id}", "assetName": name,
            "assetNameSort": f"{name.lower()}#{asset_id}",
            "assetSearchText": f"{name.lower()} {asset_id}",
            "lastEvaluatedAt": "2026-10-01T00:00:00+00:00"})
        doubles["complianceEvaluationStorage"].put_item(Item={
            "evaluationId": f"eval-{asset_id}", "databaseId:assetId": f"{DB}:{asset_id}",
            "databaseId": DB, "assetId": asset_id, "schemaName": SCHEMA,
            "evaluatedAt": "2026-10-01T00:00:00+00:00", "verdict": "quarantined",
            "status": "completed", "ruleResults": "[]"})
        doubles["complianceAuditStorage"].put_item(Item={
            "entryId": f"audit-{asset_id}", "databaseId:assetId": f"{DB}:{asset_id}",
            "databaseId": DB, "assetId": asset_id, "timestamp": "2026-10-01T00:00:00+00:00",
            "eventType": "compliance_check", "allListPartition": "audit", "actor": "SYSTEM_USER"})
        doubles["complianceCascadeStorage"].put_item(Item={
            "cascadeId": CASCADE_IDS[asset_id], "state": "pending_approval",
            "createdAt": "2026-10-01T00:00:00+00:00", "triggeredByDatabaseId": DB,
            "triggeredByAssetId": asset_id, "triggeredByAssetName": name,
            "approvalTimeoutAt": "2999-01-01T00:00:00+00:00", "nodes": "{}",
            "executionOrder": "[]"})


@pytest.fixture
def world():
    """A function that runs `body(call)` in a fresh seeded world for one enforcer, where
    `call(service, method, path, params, query, body)` invokes that service's handler."""
    def run(enforcer, body):
        stand_in = MagicMock(name="CasbinEnforcer")
        stand_in.enforceAPI.return_value = True
        stand_in.enforce.side_effect = enforcer.enforce
        mirror_engine(stand_in, enforcer)
        with mock_aws():
            resource = boto3.resource("dynamodb", region_name="us-east-1")
            patches, doubles = _cdkTables.table_doubles(resource, SERVICES)
            patches += [patch.object(module, "CasbinEnforcer", return_value=stand_in)
                        for module in (evaluate_svc, quarantine_svc, audit_svc, cascade_svc,
                                       binding_svc)]
            patches += [patch.object(module, "request_to_claims", claims_for(USER_ID))
                        for module in (evaluate_svc, quarantine_svc, audit_svc, cascade_svc,
                                       binding_svc)]
            patches += [
                patch.object(store, "write_audit", MagicMock()),
                # The update-expression builder lives in the stubbed `common.dynamodb`; only the
                # authorization verdict of an action is compared here.
                patch.object(store, "update_asset_state", MagicMock()),
                patch.object(store, "run_evaluation", return_value={
                    "evaluationId": "e-new", "verdict": "compliant",
                    "complianceState": "compliant"}),
                patch.object(evaluate_svc, "check_and_trigger_cascade", MagicMock()),
                patch.object(cascade_svc, "lambda_client", MagicMock()),
                patch.object(cascade_svc, "cascade_executor_function_name", "executor"),
                patch("handlers.compliance.complianceNotifications.notify_quarantine",
                      MagicMock()),
            ]
            for active in patches:
                active.start()
            try:
                _seed(doubles)

                def call(service, method, path, params=None, query=None, request_body=None):
                    response = service.lambda_handler(
                        rest_event(method, path, params, query, request_body), MagicMock())
                    # A refusal is a 403 and anything past authorization a 2xx or 400: an
                    # internal error would read as "refused" and hide a broken path.
                    assert response["statusCode"] < 500, (method, path, response)
                    return response

                return body(call)
            finally:
                for active in reversed(patches):
                    active.stop()
    return run


def _reads(call):
    """asset id -> {read: allowed}, all from one world (no read writes anything)."""
    out = {asset_id: {} for asset_id in (LIVE, ARCHIVED)}
    listed = {}

    def listed_ids(name, response, key, id_of):
        if response["statusCode"] != 200:
            return set()
        return {id_of(row) for row in body_of(response)[key]}

    listed["overview"] = listed_ids(
        "overview", overview := call(evaluate_svc, "GET", f"/compliance/state/{DB}",
                                     {"databaseId": DB}),
        "assets", lambda row: row["assetId"])
    listed["quarantine listing"] = listed_ids(
        "quarantine", call(quarantine_svc, "GET", "/compliance/quarantine", query={"databaseId": DB}),
        "quarantinedAssets", lambda row: row["assetId"])
    listed["audit listing"] = listed_ids(
        "audit", call(audit_svc, "GET", "/compliance/audit", query={"databaseId": DB}),
        "entries", lambda row: row["assetId"])
    listed["bindings listing"] = listed_ids(
        "bindings", call(binding_svc, "GET", f"/compliance/bind/{DB}", {"databaseId": DB}),
        "assetOverrides", lambda row: row["assetId"])
    listed["cascade listing"] = listed_ids(
        "cascades", call(cascade_svc, "GET", "/compliance/cascades"),
        "cascades", lambda row: row["assetId"])
    if overview["statusCode"] == 200:
        # The counts follow the rows: each quarantined asset listed is counted, and none other.
        summary = body_of(overview)["summary"]
        assert summary["quarantined"] == len(listed["overview"])
        assert body_of(overview)["totalAssets"] == len(listed["overview"])
    for asset_id in (LIVE, ARCHIVED):
        params = {"databaseId": DB, "assetId": asset_id}
        out[asset_id]["state"] = call(
            evaluate_svc, "GET", f"/compliance/state/{DB}/{asset_id}", params)["statusCode"] == 200
        out[asset_id]["evaluations"] = call(
            evaluate_svc, "GET", f"/compliance/evaluations/{DB}/{asset_id}",
            params)["statusCode"] == 200
        out[asset_id]["asset audit"] = call(
            audit_svc, "GET", f"/compliance/audit/{DB}/{asset_id}", params)["statusCode"] == 200
        out[asset_id]["cascade"] = call(
            cascade_svc, "GET", f"/compliance/cascades/{CASCADE_IDS[asset_id]}",
            {"cascadeId": CASCADE_IDS[asset_id]})["statusCode"] == 200
        for name, ids in listed.items():
            out[asset_id][name] = asset_id in ids
    return out


def _actions(asset_id):
    """The actions on one asset, in an order where each runs on the state the earlier ones left
    (only the authorization verdict is compared: refused is 403, anything else got past it)."""
    def run(call):
        params = {"databaseId": DB, "assetId": asset_id}
        out = {}
        out["release"] = call(quarantine_svc, "POST", f"/compliance/quarantine/{DB}/{asset_id}/release",
                              params, request_body={})["statusCode"] != 403
        out["grant exception"] = call(
            quarantine_svc, "POST", f"/compliance/quarantine/{DB}/{asset_id}/exception", params,
            request_body={"reason": "accepted"})["statusCode"] != 403
        out["revoke exception"] = call(
            quarantine_svc, "DELETE", f"/compliance/quarantine/{DB}/{asset_id}/exception",
            params)["statusCode"] != 403
        out["evaluate"] = call(evaluate_svc, "POST", f"/compliance/evaluate/{DB}/{asset_id}",
                               params, request_body={})["statusCode"] != 403
        out["approve cascade"] = call(
            cascade_svc, "POST", f"/compliance/cascades/{CASCADE_IDS[asset_id]}/approve",
            {"cascadeId": CASCADE_IDS[asset_id]}, request_body={})["statusCode"] != 403
        out["remove override"] = call(binding_svc, "DELETE", f"/compliance/bind/{DB}/{asset_id}",
                                      params)["statusCode"] != 403
        return out
    return run


@pytest.fixture(scope="module")
def decisions():
    """set name -> {"live": {check: allowed}, "archived": {...}, "asset_get": {LIVE: .., ARCHIVED:
    ..}}, for every role set (computed once; each set runs in fresh worlds)."""
    return {}


def _has_asset_rules(constraints):
    """Whether a role set has any constraint on the asset object type: the oracle of D101's
    exemption, read from the constraints themselves."""
    return any(constraint.get("objectType") == "asset" for constraint in constraints)


def _stored_evaluation_read(enforcer):
    """The evaluation GET on the archive's stored id, the compliance check an archived record
    still needs when its caller has no asset rules."""
    return bool(enforcer.enforce({"object__type": "complianceEvaluation",
                                  "databaseId": ARCHIVED_DB, "complianceState": ""}, "GET"))


def _decisions_of(set_name, role_enforcer, world, cache):
    if set_name not in cache:
        roles, constraints = _role_set(set_name)
        enforcer = role_enforcer(roles, constraints)
        reads = world(enforcer, _reads)
        live = dict(reads[LIVE], **world(enforcer, _actions(LIVE)))
        archived = dict(reads[ARCHIVED], **world(enforcer, _actions(ARCHIVED)))
        cache[set_name] = {"live": live, "archived": archived,
                           "asset_get": {LIVE: _asset_get(enforcer, LIVE),
                                         ARCHIVED: _asset_get(enforcer, ARCHIVED)},
                           "asset rules": _has_asset_rules(constraints),
                           "stored read": _stored_evaluation_read(enforcer)}
    return cache[set_name]


# ---------------------------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize("set_name", ROLE_SET_NAMES)
def test_an_archived_assets_compliance_follows_the_archived_record_rule(
        set_name, role_enforcer, world, decisions):
    """For every read and action: allowed on the archived asset exactly when allowed on the live
    one and the asset GET reaches the archived row, as opening the asset with archived assets does
    (D90). A role without asset rules is not asked the asset GET (D101): its archived asset is
    decided by the compliance check on the stored id. Where the live compliance decision equals
    the live asset GET, that is the archived asset GET decision itself."""
    found = _decisions_of(set_name, role_enforcer, world, decisions)
    asset_live, asset_archived = found["asset_get"][LIVE], found["asset_get"][ARCHIVED]
    archive_part = asset_archived if found["asset rules"] else found["stored read"]
    for check in READS + ACTIONS:
        if check in LEFT_OUT:
            assert found["archived"][check] is False, (set_name, check, found)
            continue
        expected = found["live"][check] and archive_part
        assert found["archived"][check] is expected, (set_name, check, found)
        if found["asset rules"] and found["live"][check] is asset_live:
            assert found["archived"][check] is asset_archived, (set_name, check)


@pytest.mark.unit
def test_the_matrix_reaches_both_outcomes(role_enforcer, world, decisions):
    """The comparison is not vacuous: shipped roles land on both sides of the archived rule."""
    def of(name):
        return _decisions_of(name, role_enforcer, world, decisions)

    # The Database User template "cannot view, unarchive or permanently delete archived assets":
    # it reads the live asset's compliance data and none of the archived asset's.
    user = of("template:database-user.json")
    assert all(user["live"][check] for check in READS)
    assert not any(user["archived"][check] for check in READS + ACTIONS)
    assert user["asset_get"] == {LIVE: True, ARCHIVED: False}
    # The Database Admin template names both ids on its asset constraint, and on its compliance
    # ones: it reads and acts on the archived asset as on the live one, less the listings that
    # leave an archived asset out for every role.
    admin = of("template:database-admin.json")
    assert all(admin["live"][check] for check in READS + ACTIONS)
    assert ({check: allowed for check, allowed in admin["archived"].items() if check not in LEFT_OUT}
            == {check: allowed for check, allowed in admin["live"].items() if check not in LEFT_OUT})
    assert not any(admin["archived"][check] for check in LEFT_OUT)
    assert admin["asset_get"] == {LIVE: True, ARCHIVED: True}
    # Compliance Admin of the database: full reach on the live asset, none on the archived one.
    compliance_admin = of("template:compliance-admin.json")
    assert all(compliance_admin["live"][check] for check in READS + ACTIONS)
    assert not any(compliance_admin["archived"][check] for check in READS + ACTIONS)
    # The global Compliance Admin has no asset rules: it reads the archived asset's compliance
    # history as the live asset's (D101), less the listings that leave it out for every role.
    global_admin = of("template:compliance-global-admin.json")
    assert global_admin["asset rules"] is False and global_admin["asset_get"][ARCHIVED] is False
    assert all(global_admin["archived"][check] for check in
               ("state", "evaluations", "asset audit", "audit listing", "cascade listing",
                "cascade"))
    assert ({check: allowed for check, allowed in global_admin["archived"].items()
             if check not in LEFT_OUT}
            == {check: allowed for check, allowed in global_admin["live"].items()
                if check not in LEFT_OUT})


@pytest.mark.unit
def test_a_role_allowed_on_both_ids_sees_and_acts(role_enforcer, world, decisions):
    found = _decisions_of("synthetic:both-ids", role_enforcer, world, decisions)
    assert all(found["archived"][check] for check in READS + ACTIONS
               if check not in LEFT_OUT), found["archived"]


@pytest.mark.unit
def test_a_deny_on_the_live_id_still_denies_the_archived_asset(role_enforcer, world, decisions):
    found = _decisions_of("synthetic:both-ids-with-live-deny", role_enforcer, world, decisions)
    # Every check that reads or acts on the asset's compliance evaluation object is denied, on
    # both assets; the denial reaches the archived asset through its live id.
    for check in ("state", "evaluations", "asset audit", "overview", "quarantine listing",
                  "audit listing", "release", "grant exception", "revoke exception", "evaluate",
                  "cascade listing", "cascade", "approve cascade"):
        assert found["live"][check] is False, check
        assert found["archived"][check] is False, check
