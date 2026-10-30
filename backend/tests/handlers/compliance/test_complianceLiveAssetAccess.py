# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A live asset's compliance data is shown, and acted on, only where the asset routes would show
the asset (D101).

A Database Admin with a deny on one asset tag was refused the tagged asset by the asset routes
(403), but compliance showed it: the state read and its evaluations, the database overview row and
its count, the Quarantine listing, the audit trail and a cascade's node returned its name and
compliance state. Archived assets already needed both the compliance permission and the asset GET
(D90); live assets needed the compliance permission only.

Now one rule decides both (`common/compliance/assetAccess.py`): the compliance permission, and, for
a caller whose policy has asset rules (any allow or deny line for the object type `asset`), the
asset GET the asset routes make on the asset's row as stored. A caller without asset rules (the
global Compliance Admin template) is exempt from the asset GET, live and archived, so it reads the
archived assets' compliance history again.

These tests run the compliance services and the real asset listing (`assetService.get_assets`) on
moto tables built from the CDK definitions, with the production enforcer of shipped templates and
of the round-16b role, and compare, for every surface, what compliance shows with what the asset
listing shows the same role (archived assets for archived records) and what the compliance
permissions alone allow.
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.assets.test_assetService_history import _load as load_asset_service
from backend.tests.handlers.authz import shipped_constraints
from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    RULES_SCHEMA_BODY, body_of, claims_for, mirror_engine, real_constants, rest_event, schema_row,
)
from backend.tests.handlers.compliance.test_complianceArchivedAssetAccess import asset_read_deny
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceCascadeExecutor as executor
from handlers.compliance import complianceCascadeService as cascade_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceQuarantineService as quarantine_svc
from handlers.compliance import complianceSchemaBindingService as binding_svc
from handlers.compliance import complianceTrigger as trigger

DB1 = "lv-db-one"
DB2 = "lv-db-two"
TAG = "locked"
SCHEMA = "lv-schema"
USER_ID = "live-access-user"
SERVICES = (store, evaluate_svc, quarantine_svc, audit_svc, cascade_svc, binding_svc, executor,
            trigger)
HANDLERS = (evaluate_svc, quarantine_svc, audit_svc, cascade_svc, binding_svc)

# asset id -> (database, archived, tags)
ASSETS = {
    "lv-parent": (DB1, False, []),
    "lv-plain": (DB1, False, []),
    "lv-tagged": (DB1, False, [TAG]),
    "lv-archived": (DB1, True, []),
    "lv-archived-tagged": (DB1, True, [TAG]),
    "lv-other": (DB2, False, []),
    "lv-other-tagged": (DB2, False, [TAG]),
}
LIVE_IDS = tuple(asset_id for asset_id, (_db, archived, _t) in ASSETS.items() if not archived)
# One pending cascade per asset (its parent), and a finished one of the parent over five nodes.
CASCADE_IDS = {asset_id: f"7a0c0000-0000-4000-8000-0000000d1{index:03d}"
               for index, asset_id in enumerate(ASSETS)}
COMPLETED_ID = "7a0c0000-0000-4000-8000-0000000d1999"
COMPLETED_NODES = {"lv-plain": "compliant", "lv-tagged": "quarantined", "lv-other": "compliant",
                   "lv-other-tagged": "quarantined", "lv-archived": "skipped_archived"}


def _stored(database_id, archived):
    return f"{database_id}#deleted" if archived else database_id


def _key(asset_id):
    return f"{ASSETS[asset_id][0]}:{asset_id}"


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


# ---------------------------------------------------------------------------------------------
# Roles: shipped templates, each on its own role; the round-16b role; the edge
# ---------------------------------------------------------------------------------------------
def _template(template, role, database_id=None):
    variables = {"TAG_VALUE": TAG}
    if database_id:
        variables["DATABASE_ID"] = database_id
    return shipped_constraints.template_constraints(template, role, variables)


def _tag_read_deny(role):
    """A deny of every action, the GET included, on an asset tagged TAG (the round-16b role adds
    it to the shipped `deny-tagged-assets.json`, which denies PUT, POST and DELETE only)."""
    return asset_read_deny(role, "deny-tag-get", {"field": "tags", "operator": "is_one_of",
                                                  "value": TAG}, ("asset",))


def _role_set(name):
    """(roles, constraints) of a role set."""
    single = {
        "database-admin": ("database-admin.json", DB1),
        "database-user": ("database-user.json", DB1),
        "database-readonly": ("database-readonly.json", DB1),
        "global-readonly": ("global-readonly.json", None),
        "compliance-admin": ("compliance-admin.json", DB1),
        "compliance-global-admin": ("compliance-global-admin.json", None),
        "compliance-readonly": ("compliance-readonly.json", DB1),
    }
    if name in single:
        template, database_id = single[name]
        return ["r-main"], _template(template, "r-main", database_id)
    if name == "database-admin+deny-tagged-assets":
        return ["r-dba"], (_template("database-admin.json", "r-dba", DB1)
                           + _template("deny-tagged-assets.json", "r-dba"))
    if name == "database-admin+deny-tagged-assets+get":
        return ["r-dba"], (_template("database-admin.json", "r-dba", DB1)
                           + _template("deny-tagged-assets.json", "r-dba") + _tag_read_deny("r-dba"))
    if name == "round-16b":
        # Database Admin of both databases, the shipped tag deny and a GET deny on the same tag.
        return ["r-dba1", "r-dba2"], (
            _template("database-admin.json", "r-dba1", DB1)
            + _template("database-admin.json", "r-dba2", DB2)
            + _template("deny-tagged-assets.json", "r-dba2") + _tag_read_deny("r-dba2"))
    if name == "edge:compliance-global-admin+database-user":
        return ["r-global", "r-user"], (_template("compliance-global-admin.json", "r-global")
                                        + _template("database-user.json", "r-user", DB1))
    raise KeyError(name)


ROLE_SETS = ("database-admin", "database-user", "database-readonly", "global-readonly",
             "compliance-admin", "compliance-global-admin", "compliance-readonly",
             "database-admin+deny-tagged-assets", "database-admin+deny-tagged-assets+get",
             "round-16b", "edge:compliance-global-admin+database-user")


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


# ---------------------------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------------------------
def _seed(doubles):
    for database_id in (DB1, DB2):
        doubles["databaseStorage"].put_item(Item={
            "databaseId": database_id, "complianceSchemaName": SCHEMA, "complianceAutoEval": False})
    doubles["complianceSchemaStorage"].put_item(
        Item=schema_row(SCHEMA, body=RULES_SCHEMA_BODY, database_id="GLOBAL"))
    for index, (asset_id, (database_id, archived, tags)) in enumerate(ASSETS.items()):
        name = f"Name of {asset_id}"
        doubles["assetStorage"].put_item(Item={
            "databaseId": _stored(database_id, archived), "assetId": asset_id, "assetName": name,
            "assetType": ".glb", "tags": list(tags), **({"status": "archived"} if archived else {})})
        doubles["complianceAssetStateStorage"].put_item(Item={
            "databaseId": database_id, "assetId": asset_id, "schemaName": SCHEMA,
            "schemaSource": "asset", "complianceState": "quarantined",
            "quarantineReason": "rule failed", "lastEvaluationId": f"eval-{asset_id}",
            "assetName": name, "assetNameSort": f"{name.lower()}#{asset_id}",
            "assetSearchText": f"{name.lower()} {asset_id}",
            "lastEvaluatedAt": "2026-10-01T00:00:00+00:00"})
        doubles["complianceEvaluationStorage"].put_item(Item={
            "evaluationId": f"eval-{asset_id}", "databaseId:assetId": f"{database_id}:{asset_id}",
            "databaseId": database_id, "assetId": asset_id, "schemaName": SCHEMA,
            "evaluatedAt": "2026-10-01T00:00:00+00:00", "verdict": "quarantined",
            "status": "completed", "ruleResults": "[]"})
        doubles["complianceAuditStorage"].put_item(Item=build_audit_item(
            database_id, asset_id, "compliance_check", "SYSTEM_USER", {},
            timestamp=f"2026-10-01T00:00:{index:02d}+00:00", asset_name=name))
        doubles["complianceCascadeStorage"].put_item(Item={
            "cascadeId": CASCADE_IDS[asset_id], "state": "pending_approval",
            "createdAt": f"2026-10-01T00:00:{index:02d}+00:00", "triggeredByDatabaseId": database_id,
            "triggeredByAssetId": asset_id, "triggeredByAssetName": name,
            "approvalTimeoutAt": "2999-01-01T00:00:00+00:00", "nodes": "{}",
            "executionOrder": "[]"})
    keys = [_key(asset_id) for asset_id in COMPLETED_NODES]
    doubles["complianceCascadeStorage"].put_item(Item={
        "cascadeId": COMPLETED_ID, "state": "completed", "createdAt": "2026-10-02T00:00:00+00:00",
        "triggeredByDatabaseId": DB1, "triggeredByAssetId": "lv-parent",
        "triggeredByAssetName": "Name of lv-parent", "totalNodes": len(keys),
        "nodes": json.dumps({_key(a): s for a, s in COMPLETED_NODES.items()}),
        "executionOrder": json.dumps(keys),
        "results": json.dumps([{"node": _key(a), "status": s} for a, s in COMPLETED_NODES.items()]),
        "nodeNames": json.dumps({_key(a): f"Name of {a}" for a in COMPLETED_NODES})})
    # The parent's links, for the approval of its pending cascade.
    for index, child in enumerate(("lv-plain", "lv-tagged", "lv-other", "lv-other-tagged")):
        doubles["assetLinksStorageV2"].put_item(Item={
            "assetLinkId": f"lv-link-{index}", "relationshipType": "parentChild",
            "fromAssetDatabaseId": DB1, "fromAssetId": "lv-parent",
            "toAssetDatabaseId": ASSETS[child][0], "toAssetId": child,
            "fromAssetDatabaseId:fromAssetId": f"{DB1}:lv-parent",
            "toAssetDatabaseId:toAssetId": _key(child)})


def _in_world(enforcer, roles, body):
    """Run `body(call, list_assets, doubles)` in a fresh seeded world for one enforcer."""
    stand_in = MagicMock(name="CasbinEnforcer")
    stand_in.enforceAPI.return_value = True
    stand_in.enforce.side_effect = enforcer.enforce
    mirror_engine(stand_in, enforcer)
    asset_service = load_asset_service()
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, SERVICES)
        for module in HANDLERS:
            patches += [patch.object(module, "CasbinEnforcer", return_value=stand_in),
                        patch.object(module, "request_to_claims", claims_for(USER_ID))]
        patches += [
            patch.object(store, "write_audit", MagicMock()),
            patch.object(store, "update_asset_state", MagicMock()),
            patch.object(store, "run_evaluation", return_value={
                "evaluationId": "e-new", "verdict": "compliant", "complianceState": "compliant"}),
            patch.object(evaluate_svc, "check_and_trigger_cascade", MagicMock()),
            patch.object(cascade_svc, "lambda_client", MagicMock()),
            patch.object(cascade_svc, "cascade_executor_function_name", "executor"),
            patch("handlers.compliance.complianceNotifications.notify_quarantine", MagicMock()),
            patch("handlers.compliance.complianceNotifications.notify_cascade_completed",
                  MagicMock()),
            patch.object(asset_service, "dynamodb_client",
                         boto3.client("dynamodb", region_name="us-east-1")),
            patch.object(asset_service, "asset_database", doubles["assetStorage"].name),
            patch.object(asset_service, "CasbinEnforcer", return_value=stand_in),
            patch.object(asset_service, "claims_and_roles",
                         {"tokens": [USER_ID], "roles": list(roles)}),
        ]
        for active in patches:
            active.start()
        try:
            _seed(doubles)

            def call(service, method, path, params=None, query=None, request_body=None):
                response = service.lambda_handler(
                    rest_event(method, path, params, query, request_body), MagicMock())
                # A refusal is a 403 and anything past authorization a 2xx or 400: an internal
                # error would read as "refused" and hide a broken path.
                assert response["statusCode"] < 500, (method, path, response)
                return response

            def list_assets(database_id, archived):
                items = asset_service.get_assets(database_id, {"pageSize": 100, "maxItems": 100},
                                                 archived)
                return {item["assetId"] for item in items["Items"]}

            return body(call, list_assets, doubles)
        finally:
            for active in reversed(patches):
                active.stop()


def _ids(response, key, id_of=lambda row: row["assetId"]):
    if response["statusCode"] != 200:
        return None
    return {id_of(row) for row in body_of(response)[key]}


def _observe(call, list_assets, _doubles):
    out = {"listing": {}}
    for database_id in (DB1, DB2):
        live = list_assets(database_id, False)
        out["listing"].update({asset_id: True for asset_id in live})
        out["listing"].update({asset_id: True for asset_id in list_assets(database_id, True)
                               if ASSETS.get(asset_id, (None, False))[1]})
    for asset_id, (database_id, _archived, _tags) in ASSETS.items():
        params = {"databaseId": database_id, "assetId": asset_id}
        for name, service, path in (
                ("state", evaluate_svc, f"/compliance/state/{database_id}/{asset_id}"),
                ("evaluations", evaluate_svc, f"/compliance/evaluations/{database_id}/{asset_id}"),
                ("asset audit", audit_svc, f"/compliance/audit/{database_id}/{asset_id}")):
            out.setdefault(name, {})[asset_id] = call(
                service, "GET", path, params)["statusCode"] == 200
        out.setdefault("cascade", {})[asset_id] = call(
            cascade_svc, "GET", f"/compliance/cascades/{CASCADE_IDS[asset_id]}",
            {"cascadeId": CASCADE_IDS[asset_id]})["statusCode"] == 200
    for database_id in (DB1, DB2):
        overview = call(evaluate_svc, "GET", f"/compliance/state/{database_id}",
                        {"databaseId": database_id})
        rows = _ids(overview, "assets")
        out.setdefault("overview", {})[database_id] = rows
        if rows is not None:
            summary = body_of(overview)["summary"]
            # The counts follow the rows: each quarantined asset listed is counted, and none other.
            assert summary["quarantined"] == len(rows), (database_id, summary, rows)
            assert body_of(overview)["totalAssets"] == len(rows)
        out.setdefault("bindings listing", {})[database_id] = _ids(
            call(binding_svc, "GET", f"/compliance/bind/{database_id}", {"databaseId": database_id}),
            "assetOverrides")
    out["quarantine listing"] = _ids(call(quarantine_svc, "GET", "/compliance/quarantine"),
                                     "quarantinedAssets")
    out["audit listing"] = _ids(call(audit_svc, "GET", "/compliance/audit"), "entries")
    out["audit listing by event type"] = _ids(call(
        audit_svc, "GET", "/compliance/audit", query={"eventType": "compliance_check"}), "entries")
    out["audit listing by database"] = set()
    for database_id in (DB1, DB2):
        out["audit listing by database"] |= _ids(call(
            audit_svc, "GET", "/compliance/audit", query={"databaseId": database_id}),
            "entries") or set()
    out["cascade listing"] = _ids(call(cascade_svc, "GET", "/compliance/cascades"), "cascades")
    completed = call(cascade_svc, "GET", f"/compliance/cascades/{COMPLETED_ID}",
                     {"cascadeId": COMPLETED_ID})
    out["completed cascade"] = None
    if completed["statusCode"] == 200:
        body = body_of(completed)
        out["completed cascade"] = {
            "nodes": {key.split(":", 1)[1] for key in json.loads(body["nodes"])},
            "names": {key.split(":", 1)[1] for key in json.loads(body["nodeNames"])},
            "unauthorizedNodes": body["unauthorizedNodes"], "totalNodes": body["totalNodes"],
            "text": json.dumps(body)}
    listed_completed = call(cascade_svc, "GET", "/compliance/cascades", query={"state": "completed"})
    out["completed listing"] = None
    if listed_completed["statusCode"] == 200 and body_of(listed_completed)["cascades"]:
        row = body_of(listed_completed)["cascades"][0]
        out["completed listing"] = {
            "nodes": {key.split(":", 1)[1] for key in json.loads(row["nodes"])},
            "unauthorizedNodes": row["unauthorizedNodes"]}
    evaluate_all = call(evaluate_svc, "POST", f"/compliance/evaluate/{DB1}", {"databaseId": DB1},
                        request_body={})
    out["evaluate all"] = body_of(evaluate_all) if evaluate_all["statusCode"] == 200 else None
    sweep = call(evaluate_svc, "POST", f"/compliance/sweep/{SCHEMA}", {"schemaName": SCHEMA},
                 request_body={})
    out["sweep"] = body_of(sweep) if sweep["statusCode"] == 200 else None
    # Actions last: approving changes its cascade's state (the others write nothing here).
    for asset_id, (database_id, _archived, _tags) in ASSETS.items():
        params = {"databaseId": database_id, "assetId": asset_id}
        base = f"/compliance/quarantine/{database_id}/{asset_id}"
        for name, method, service, path, request_body, path_params in (
                ("release", "POST", quarantine_svc, f"{base}/release", {}, params),
                ("grant exception", "POST", quarantine_svc, f"{base}/exception",
                 {"reason": "accepted"}, params),
                ("revoke exception", "DELETE", quarantine_svc, f"{base}/exception", None, params),
                ("evaluate", "POST", evaluate_svc, f"/compliance/evaluate/{database_id}/{asset_id}",
                 {}, params),
                ("approve cascade", "POST", cascade_svc,
                 f"/compliance/cascades/{CASCADE_IDS[asset_id]}/approve", {},
                 {"cascadeId": CASCADE_IDS[asset_id]})):
            out.setdefault(name, {})[asset_id] = call(
                service, method, path, path_params, request_body=request_body)["statusCode"] != 403
    return out


@pytest.fixture(scope="module")
def observations():
    return {}


def _observed(name, role_enforcer, cache):
    if name not in cache:
        roles, constraints = _role_set(name)
        enforcer = role_enforcer(roles, constraints)
        cache[name] = (_in_world(enforcer, roles, _observe), _Oracle(enforcer, constraints))
    return cache[name]


# ---------------------------------------------------------------------------------------------
# The oracle: the compliance permissions alone (the engine on the compliance objects), and the
# asset listing for the asset part, for a role with asset rules
# ---------------------------------------------------------------------------------------------
class _Oracle:
    def __init__(self, enforcer, constraints):
        self._enforcer = enforcer
        self.asset_rules = any(c.get("objectType") == "asset" for c in constraints)

    def compliance(self, object_type, asset_id, action, state="", cascade_id=None):
        database_id, archived, _tags = ASSETS[asset_id]
        obj = {"object__type": object_type, "databaseId": _stored(database_id, archived)}
        if object_type == "complianceEvaluation":
            obj["complianceState"] = state
        if object_type == "complianceCascade":
            obj["cascadeId"] = cascade_id or CASCADE_IDS[asset_id]
        return bool(self._enforcer.enforce(obj, action))

    def asset_part(self, asset_id, listing):
        return (not self.asset_rules) or bool(listing.get(asset_id))

    def visible(self, asset_id, listing, action="GET", state=""):
        return (self.compliance("complianceEvaluation", asset_id, action, state)
                and self.asset_part(asset_id, listing))

    def cascade_visible(self, asset_id, listing, action="GET"):
        return (self.compliance("complianceCascade", asset_id, action)
                and self.visible(asset_id, listing, action))

    def binding_visible(self, asset_id, listing):
        """The bindings listing is read on the database object (its row as stored), and an
        override of an archived asset on the database's stored id too."""
        database_id, archived, _tags = ASSETS[asset_id]
        obj = {"object__type": "database", "databaseId": _stored(database_id, archived),
               "complianceSchemaName": SCHEMA, "complianceAutoEval": False}
        return bool(self._enforcer.enforce(obj, "GET")) and self.asset_part(asset_id, listing)


def _database_readable(oracle, database_id, object_type="complianceEvaluation"):
    obj = {"object__type": object_type, "databaseId": database_id}
    if object_type == "complianceEvaluation":
        obj["complianceState"] = ""
    return bool(oracle._enforcer.enforce(obj, "GET"))


# ---------------------------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize("name", ROLE_SETS)
def test_compliance_shows_an_asset_where_the_asset_routes_do(name, role_enforcer, observations):
    """Every record read: allowed exactly when the compliance permission allows it and, for a role
    with asset rules, the asset listing shows the asset (archived assets for archived records)."""
    found, oracle = _observed(name, role_enforcer, observations)
    listing = found["listing"]
    for asset_id in ASSETS:
        expected = oracle.visible(asset_id, listing)
        for read in ("state", "evaluations", "asset audit"):
            assert found[read][asset_id] is expected, (name, read, asset_id)
        assert found["cascade"][asset_id] is oracle.cascade_visible(asset_id, listing), (
            name, "cascade", asset_id)


@pytest.mark.unit
@pytest.mark.parametrize("name", ROLE_SETS)
def test_the_listings_and_counts_hold_the_assets_the_asset_listing_shows(
        name, role_enforcer, observations):
    """The overview rows and counts, the Quarantine listing and the bindings listing (live assets:
    they leave archived assets out for every role), the three audit listing paths and the cascade
    listing (live and archived)."""
    found, oracle = _observed(name, role_enforcer, observations)
    listing = found["listing"]
    for database_id in (DB1, DB2):
        rows = found["overview"][database_id]
        if not _database_readable(oracle, database_id):
            assert rows is None, (name, database_id)
            continue
        assert rows == {asset_id for asset_id in LIVE_IDS if ASSETS[asset_id][0] == database_id
                        and oracle.visible(asset_id, listing)}, (name, "overview", database_id)
        bindings = found["bindings listing"][database_id]
        if bindings is not None:
            assert bindings == {asset_id for asset_id in ASSETS
                                if ASSETS[asset_id][0] == database_id
                                and oracle.binding_visible(asset_id, listing)}, (name, database_id)
    assert found["quarantine listing"] == {
        asset_id for asset_id in LIVE_IDS
        if oracle.visible(asset_id, listing, state="quarantined")}, name
    expected_audit = {asset_id for asset_id in ASSETS if oracle.visible(asset_id, listing)}
    for path in ("audit listing", "audit listing by event type", "audit listing by database"):
        assert found[path] == expected_audit, (name, path)
    assert found["cascade listing"] == {
        asset_id for asset_id in ASSETS if oracle.cascade_visible(asset_id, listing)}, name


@pytest.mark.unit
@pytest.mark.parametrize("name", ROLE_SETS)
def test_a_cascade_counts_the_descendants_the_reader_may_not_read(
        name, role_enforcer, observations):
    """A finished cascade's live descendants are listed where their compliance read would be
    allowed; the others are counted in `unauthorizedNodes`, without their ids or names. The node
    skipped as archived is decided as the archived asset it is, and is not counted."""
    found, oracle = _observed(name, role_enforcer, observations)
    listing = found["listing"]
    view = found["completed cascade"]
    if not oracle.cascade_visible("lv-parent", listing):
        assert view is None, name
        return
    live_nodes = [asset_id for asset_id in COMPLETED_NODES if not ASSETS[asset_id][1]]
    shown_live = {asset_id for asset_id in live_nodes if oracle.visible(asset_id, listing)}
    shown = shown_live | ({"lv-archived"} if oracle.visible("lv-archived", listing) else set())
    assert view["nodes"] == shown and view["names"] == shown, (name, view)
    assert view["unauthorizedNodes"] == len(live_nodes) - len(shown_live), (name, view)
    assert view["totalNodes"] == len(shown), (name, view)
    for asset_id in set(COMPLETED_NODES) - shown:
        assert asset_id not in view["text"], (name, asset_id)
    assert found["completed listing"] == {"nodes": shown,
                                          "unauthorizedNodes": view["unauthorizedNodes"]}, name


@pytest.mark.unit
@pytest.mark.parametrize("name", ROLE_SETS)
def test_the_actions_are_refused_like_the_asset_routes(name, role_enforcer, observations):
    found, oracle = _observed(name, role_enforcer, observations)
    listing = found["listing"]
    for asset_id in ASSETS:
        expected = {
            "release": oracle.visible(asset_id, listing, "POST", "quarantined"),
            "grant exception": oracle.visible(asset_id, listing, "POST", "quarantined"),
            "revoke exception": oracle.visible(asset_id, listing, "DELETE", "exception"),
            "evaluate": oracle.visible(asset_id, listing, "POST"),
            "approve cascade": oracle.cascade_visible(asset_id, listing, "POST"),
        }
        for action, allowed in expected.items():
            assert found[action][asset_id] is allowed, (name, action, asset_id)


@pytest.mark.unit
@pytest.mark.parametrize("name", ROLE_SETS)
def test_the_sweeps_evaluate_only_the_assets_the_caller_may_read(
        name, role_enforcer, observations):
    """Evaluate All (DB1) and the schema sweep (both databases): an asset the caller may not read
    is not evaluated and is counted in `skippedNotAuthorized`; the archived counts are unchanged
    (an archived asset is counted only for a caller who may see it)."""
    found, oracle = _observed(name, role_enforcer, observations)
    listing = found["listing"]

    def may_evaluate(database_id):
        return bool(oracle._enforcer.enforce({"object__type": "complianceEvaluation",
                                              "databaseId": database_id, "complianceState": ""},
                                             "POST"))

    def archived_seen(asset_id):
        return oracle.visible(asset_id, listing)

    evaluate_all = found["evaluate all"]
    if not may_evaluate(DB1):
        assert evaluate_all is None, name
    else:
        db1_live = [a for a in LIVE_IDS if ASSETS[a][0] == DB1]
        triggered = {entry["assetId"] for entry in evaluate_all["assetsTriggered"]}
        assert triggered == {a for a in db1_live if oracle.asset_part(a, listing)}, name
        assert evaluate_all["skippedNotAuthorized"] == len(db1_live) - len(triggered), name
        archived = [a for a in ASSETS if ASSETS[a][1] and ASSETS[a][0] == DB1]
        assert evaluate_all["skippedArchived"] == sum(archived_seen(a) for a in archived), name
        assert evaluate_all["skipped"] == (evaluate_all["skippedArchived"]
                                           + evaluate_all["skippedNotAuthorized"])
    sweep = found["sweep"]
    if sweep is None:
        return
    triggered, not_authorized, skipped_archived = set(), 0, 0
    for asset_id, (database_id, archived, _tags) in ASSETS.items():
        if archived and not archived_seen(asset_id):
            continue
        if not may_evaluate(database_id) or (not archived and not oracle.asset_part(asset_id,
                                                                                   listing)):
            not_authorized += 1
        elif archived:
            skipped_archived += 1
        else:
            triggered.add(asset_id)
    assert {entry["assetId"] for entry in sweep["assetsTriggered"]} == triggered, (name, sweep)
    assert sweep["skippedNotAuthorized"] == not_authorized, (name, sweep)
    assert sweep["skippedArchived"] == skipped_archived, (name, sweep)


# ---------------------------------------------------------------------------------------------
# The engine's answer: has the caller's policy any line for the asset object type?
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize("template", shipped_constraints.template_names())
def test_the_engine_says_which_templates_have_asset_rules(template, role_enforcer):
    """`policy_has_object_type` is read from the policy routes (the lines a request of the type
    walks), never from role or template names: a template has asset rules exactly when it carries a
    constraint on the asset object type. Of the shipped templates only the global Compliance Admin
    and the Database Tag Admin have none; the database-scoped Compliance Admin and Read-only grant
    the asset GET on their database."""
    constraints = _template(template, "r-main", DB1)
    enforcer = role_enforcer(["r-main"], constraints)
    expected = any(constraint.get("objectType") == "asset" for constraint in constraints)
    assert enforcer.policy_has_object_type("asset") is expected, template
    assert expected is (template not in ("compliance-global-admin.json",
                                         "database-tag-admin.json")), template


@pytest.mark.unit
def test_the_asset_rules_answer_is_conservative(role_enforcer):
    enforcer = role_enforcer(*_role_set("compliance-global-admin"))
    assert enforcer.policy_has_object_type("asset") is False
    assert enforcer.policy_has_object_type("complianceEvaluation") is True
    # Routes that no longer describe the loaded policy are not trusted: any line may apply.
    service = enforcer.service_object
    service._enforcer._policy_routes = None
    assert enforcer.policy_has_object_type("asset") is True
    service._enforcer = None
    assert enforcer.policy_has_object_type("asset") is True
    enforcer.service_object = None
    assert enforcer.policy_has_object_type("asset") is True


# ---------------------------------------------------------------------------------------------
# The named cases
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
def test_the_round_16b_reader_no_longer_reads_the_tagged_asset(role_enforcer, observations):
    """Database Admin of both databases with a GET deny on a tag (the deny names no database, so
    it applies in both): the tagged live assets leave every compliance surface (403 on their reads
    and actions), as the asset GET refuses them; the untagged ones stay."""
    found, _oracle = _observed("round-16b", role_enforcer, observations)
    for tagged, plain in (("lv-tagged", "lv-plain"), ("lv-other-tagged", "lv-other")):
        assert found["listing"].get(tagged) is None and found["listing"][plain] is True
        for read in ("state", "evaluations", "asset audit", "cascade", "release", "evaluate"):
            assert found[read][tagged] is False, (read, tagged)
            assert found[read][plain] is True, (read, plain)
        assert tagged not in found["quarantine listing"]
        assert tagged not in found["audit listing"]
        assert tagged not in found["completed cascade"]["text"]
        assert tagged not in {e["assetId"] for e in found["sweep"]["assetsTriggered"]}
    assert found["overview"][DB1] == {"lv-parent", "lv-plain"}
    assert found["overview"][DB2] == {"lv-other"}
    assert found["completed cascade"]["unauthorizedNodes"] == 2


@pytest.mark.unit
def test_the_shipped_tag_deny_leaves_the_read_and_refuses_nothing_more(role_enforcer, observations):
    """`deny-tagged-assets.json` denies PUT, POST and DELETE only: the asset GET still shows the
    tagged asset, so its compliance data stays readable and actionable (the compliance actions ask
    the asset GET)."""
    found, _oracle = _observed("database-admin+deny-tagged-assets", role_enforcer, observations)
    for read in ("state", "evaluations", "asset audit", "cascade", "release", "evaluate"):
        assert found[read]["lv-tagged"] is True, read
    assert "lv-tagged" in found["overview"][DB1]


@pytest.mark.unit
def test_the_global_compliance_admin_reads_its_archived_history(role_enforcer, observations):
    """No asset rules: exempt from the asset GET, live and archived. The archived assets' records
    and history are its again (the compliance check on `<databaseId>#deleted` still applies)."""
    found, oracle = _observed("compliance-global-admin", role_enforcer, observations)
    assert oracle.asset_rules is False
    assert not any(found["listing"].values())
    for asset_id in ASSETS:
        for read in ("state", "evaluations", "asset audit", "cascade"):
            assert found[read][asset_id] is True, (read, asset_id)
    assert found["audit listing"] == set(ASSETS)
    assert found["completed cascade"]["unauthorizedNodes"] == 0
    assert "lv-archived" in found["completed cascade"]["nodes"]


@pytest.mark.unit
def test_the_edge_a_global_compliance_admin_with_a_database_user_role(role_enforcer, observations):
    """Edge for the owner (rule unchanged): the Database User role on DB1 gives the user asset
    rules, so every asset needs the asset GET, which that role gives on DB1 only. DB2's assets,
    readable by the global Compliance Admin role alone, now leave compliance; DB1's archived
    assets too (the Database User template does not reach the archive)."""
    found, oracle = _observed("edge:compliance-global-admin+database-user", role_enforcer,
                              observations)
    assert oracle.asset_rules is True
    for asset_id in ("lv-other", "lv-other-tagged", "lv-archived", "lv-archived-tagged"):
        assert found["state"][asset_id] is False, asset_id
    for asset_id in ("lv-parent", "lv-plain", "lv-tagged"):
        assert found["state"][asset_id] is True, asset_id
    # DB2's overview is empty with zero counts (the role may read DB2's compliance, not its assets).
    assert found["overview"][DB2] == set()
    assert found["completed cascade"]["unauthorizedNodes"] == 2
    assert found["completed cascade"]["nodes"] == {"lv-plain", "lv-tagged"}


@pytest.mark.unit
def test_the_engine_tells_no_line_applies_from_denied(role_enforcer):
    """For the edge: the engine's walk already says why it refused. `enforce_ex` returns the deny
    line that refused an object, and no line when none matched (an asset of a database no asset
    line names). Same single walk as `enforce`, which calls it."""
    def explain(roles_constraints, obj):
        enforcer = role_enforcer(*roles_constraints)
        service = enforcer.service_object
        scrubbed = service._scrub_object_fields(dict(obj, object__type="asset"))
        enhanced = authz_module.PERMISSION_CONSTRAINT_FIELDS.copy()
        enhanced.update({k: ("" if v is None else v) for k, v in scrubbed.items()})
        return service._enforcer.enforce_ex(f"user::{USER_ID}", enhanced, "GET")

    allowed, line = explain(_role_set("edge:compliance-global-admin+database-user"),
                            {"databaseId": DB2, "assetId": "lv-other", "tags": []})
    assert allowed is False and list(line) == []
    allowed, line = explain(_role_set("round-16b"),
                            {"databaseId": DB2, "assetId": "lv-other-tagged", "tags": [TAG]})
    assert allowed is False and list(line) and "deny" in list(line)
    # An allow names no line (the effect keeps looking for a deny), so only a refusal is told
    # apart: no line matched, or a deny line matched.
    allowed, _line = explain(_role_set("round-16b"),
                             {"databaseId": DB2, "assetId": "lv-other", "tags": []})
    assert allowed is True


# ---------------------------------------------------------------------------------------------
# The cascade executor runs with the starting caller's reach
# ---------------------------------------------------------------------------------------------
def _approve_and_run(name_or_set, role_enforcer):
    roles, constraints = name_or_set
    enforcer = role_enforcer(roles, constraints)

    def body(call, _list_assets, doubles):
        response = call(cascade_svc, "POST",
                        f"/compliance/cascades/{CASCADE_IDS['lv-parent']}/approve",
                        {"cascadeId": CASCADE_IDS["lv-parent"]}, request_body={})
        assert response["statusCode"] == 202, response
        row = doubles["complianceCascadeStorage"].get_item(
            Key={"cascadeId": CASCADE_IDS["lv-parent"]})["Item"]
        with patch.object(store, "get_compliance_record", return_value={"schemaName": SCHEMA}):
            result = executor.execute_cascade(CASCADE_IDS["lv-parent"])
        return row, {entry["node"].split(":", 1)[1]: entry["status"]
                     for entry in result["results"]}
    return _in_world(enforcer, roles, body)


@pytest.mark.unit
def test_the_executor_skips_a_descendant_the_approvers_asset_rules_refuse(role_enforcer):
    """Approved by a reader whose rules read tags: the descendants are decided on their rows when
    the cascade starts (`authorizedAssetKeys`); the tagged ones are skipped as not authorized."""
    row, statuses = _approve_and_run(_role_set("round-16b"), role_enforcer)
    assert set(json.loads(row["authorizedAssetKeys"])) == {_key("lv-other"), _key("lv-plain")}
    assert statuses == {"lv-plain": "compliant", "lv-tagged": "skipped_not_authorized",
                        "lv-other": "compliant", "lv-other-tagged": "skipped_not_authorized"}


@pytest.mark.unit
def test_the_executor_skips_a_database_whose_assets_the_approver_may_not_read(role_enforcer):
    """Approved by a reader decided per database: a database it may evaluate but whose assets it
    may not read is left out of `authorizedDatabaseIds`; no asset is read for it."""
    roles = ["r-c1", "r-c2"]
    constraints = (_template("compliance-admin.json", "r-c1", DB1)
                   + _template("compliance-admin.json", "r-c2", DB2)
                   + asset_read_deny("r-c2", "deny-db2-assets", {
                       "field": "databaseId", "operator": "equals", "value": DB2}, ("asset",)))
    row, statuses = _approve_and_run((roles, constraints), role_enforcer)
    assert "authorizedAssetKeys" not in row
    assert json.loads(row["authorizedDatabaseIds"]) == [DB1]
    assert statuses == {"lv-plain": "compliant", "lv-tagged": "compliant",
                        "lv-other": "skipped_not_authorized",
                        "lv-other-tagged": "skipped_not_authorized"}
