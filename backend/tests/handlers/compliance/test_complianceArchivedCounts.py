# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Evaluate All, schema sweeps and cascades count an archived asset only for a caller who may see
it.

Archived assets are not evaluated (Evaluate All, a sweep and a cascade skip them), and the responses
used to say how many were skipped whatever the caller: `skippedArchived` (and `skipped`) counted
every archived asset of the database, `assetsRemaining` counted the archived assets beyond the cap,
and a cascade listed each descendant it skipped as archived, by id and name, as `skipped_archived`.
A role that may not see a database's archive (the Database User and Compliance Admin templates,
and any role with a deny on `<databaseId>#deleted`) learned how many archived assets the database
holds, and which, from compliance.

Now an archived asset is counted, and a skipped archived descendant shown, only for a caller who
may see the database's archive: the evaluation GET on the stored id `<databaseId>#deleted`, the
decision the compliance reads make on an archived asset's record (`ArchivedAssets`). For any other
caller it is counted nowhere in the response, `skippedNotAuthorized`, `withoutSchema` and
`assetsRemaining` included.

These tests run the services on moto tables built from the CDK definitions with the production
enforcer of every shipped role (each template of `documentation/permissionsTemplates/` and both
seeded roles), two synthetic roles named on both ids (one with a live-id deny) and the Database
Admin template with a deny on the archive. For each role the counts are compared with what the
same role sees of the archive in the asset listing with archived assets (`showArchived`, the real
`assetService.get_assets`) and in the compliance record reads.
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

import backend.backend.handlers.authz as authz_module
from backend.tests.handlers.assets.test_assetService_history import _load as load_asset_service
from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    SYNC_RULES_SCHEMA_BODY, body_of, claims_for, mirror_engine, real_constants, rest_event,
    schema_row,
)
from backend.tests.handlers.compliance.test_complianceArchivedAssetAccess import (
    ARCHIVED_DB, DB, DENIED_TAG, ROLE_SET_NAMES, USER_ID, _enforcer, _role_set,
)
from handlers.compliance import complianceCascadeExecutor as executor
from handlers.compliance import complianceCascadeService as cascade_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceTrigger as trigger

SCHEMA = "arch-count-schema"
# Live ids sort before the archived ones, so a capped call reaches the live assets first and the
# archived ones fall beyond the cap.
LIVE = ("a-live-1", "a-live-2")
ARCHIVED = ("z-archived-1", "z-archived-2")
PARENT, LIVE_CHILD, ARCHIVED_CHILD = LIVE[0], LIVE[1], ARCHIVED[0]
CASCADE_ID = "7a0c0000-0000-4000-8000-0000000000c1"
SERVICES = (store, evaluate_svc, trigger, executor, cascade_svc)
ARCHIVE_DENY_SET = "synthetic:database-admin-with-archive-deny"
SET_NAMES = sorted(ROLE_SET_NAMES + [ARCHIVE_DENY_SET])


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    """The real permission field matrix for the enforcer and the template import (see
    test_complianceTemplateRolesEndToEnd)."""
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


def _archive_deny(role):
    """A deny on the archive: every action on the asset and on its compliance evaluation where
    databaseId equals the archived partition `<databaseId>#deleted`."""
    return [{
        "constraintId": f"deny-archive-{object_type}", "name": f"deny-archive-{object_type}",
        "objectType": object_type,
        "criteriaAnd": [{"field": "databaseId", "id": "d", "operator": "equals",
                         "value": ARCHIVED_DB}],
        "criteriaOr": [],
        "groupPermissions": [{"groupId": role, "id": f"d-{object_type}-{action}",
                              "permission": action, "permissionType": "deny"}
                             for action in ("GET", "PUT", "POST", "DELETE")],
        "userPermissions": [],
    } for object_type in ("asset", "complianceEvaluation")]


def _roles(set_name):
    """(roles, constraints) of one role set: the access tests' sets plus the archive deny."""
    if set_name == ARCHIVE_DENY_SET:
        roles, constraints = _role_set("template:database-admin.json")
        return roles, constraints + _archive_deny(roles[0])
    return _role_set(set_name)


@pytest.fixture
def role_enforcer():
    previous = (authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map)
    with patch.object(authz_module, "log_authorization", MagicMock()):
        yield _enforcer
    authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map = previous


# ---------------------------------------------------------------------------------------------
# The world: two live and two archived assets of DB, each recorded under the database binding,
# and a cascade of the first live asset that the executor ran over a live and an archived child
# ---------------------------------------------------------------------------------------------
def _seed(doubles):
    doubles["databaseStorage"].put_item(Item={"databaseId": DB, "complianceSchemaName": SCHEMA,
                                              "complianceAutoEval": False})
    doubles["complianceSchemaStorage"].put_item(
        Item=schema_row(SCHEMA, body=SYNC_RULES_SCHEMA_BODY, database_id=DB))
    for asset_id in LIVE + ARCHIVED:
        name = f"Asset {asset_id}"
        archived = asset_id in ARCHIVED
        doubles["assetStorage"].put_item(Item={
            "databaseId": ARCHIVED_DB if archived else DB, "assetId": asset_id, "assetName": name,
            "tags": [DENIED_TAG] if asset_id == ARCHIVED[0] else [],
            **({"status": "archived"} if archived else {})})
        doubles["complianceAssetStateStorage"].put_item(Item={
            "databaseId": DB, "assetId": asset_id, "schemaName": SCHEMA,
            "schemaSource": "database", "complianceState": "compliant", "assetName": name,
            "assetNameSort": f"{name.lower()}#{asset_id}",
            "assetSearchText": f"{name.lower()} {asset_id}",
            "lastEvaluatedAt": "2026-10-01T00:00:00+00:00"})
    # Archiving keeps an asset's links, so the cascade discovers the archived child.
    for index, child in enumerate((LIVE_CHILD, ARCHIVED_CHILD)):
        doubles["assetLinksStorageV2"].put_item(Item={
            "assetLinkId": f"link-{index}", "relationshipType": "parentChild",
            "fromAssetDatabaseId": DB, "fromAssetId": PARENT,
            "toAssetDatabaseId": DB, "toAssetId": child,
            "fromAssetDatabaseId:fromAssetId": f"{DB}:{PARENT}",
            "toAssetDatabaseId:toAssetId": f"{DB}:{child}"})
    doubles["complianceCascadeStorage"].put_item(Item={
        "cascadeId": CASCADE_ID, "state": "executing", "createdAt": "2026-10-28T08:00:00+00:00",
        "triggeredByDatabaseId": DB, "triggeredByAssetId": PARENT,
        "triggeredByAssetName": f"Asset {PARENT}", "authorizedDatabaseIds": json.dumps([DB])})
    result = executor.execute_cascade(CASCADE_ID)
    assert {entry["node"]: entry["status"] for entry in result["results"]} == {
        f"{DB}:{LIVE_CHILD}": "compliant", f"{DB}:{ARCHIVED_CHILD}": "skipped_archived"}


def _in_world(enforcer, roles, body):
    """Run `body(call, list_assets)` in a fresh seeded world for one enforcer: `call(service,
    method, path, params, query, body)` invokes a compliance handler, `list_assets()` the asset
    listing of DB with archived assets."""
    stand_in = MagicMock(name="CasbinEnforcer")
    stand_in.enforceAPI.return_value = True
    stand_in.enforce.side_effect = enforcer.enforce
    mirror_engine(stand_in, enforcer)
    asset_service = load_asset_service()
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, SERVICES)
        for module in (evaluate_svc, cascade_svc):
            patches += [patch.object(module, "CasbinEnforcer", return_value=stand_in),
                        patch.object(module, "request_to_claims", claims_for(USER_ID))]
        patches += [
            patch.object(store, "write_audit", MagicMock()),
            patch.object(store, "run_evaluation", return_value={
                "evaluationId": "e-new", "verdict": "compliant", "complianceState": "compliant"}),
            patch.object(evaluate_svc, "check_and_trigger_cascade", MagicMock()),
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
                assert response["statusCode"] < 500, (method, path, response)
                return response

            def list_assets():
                items = asset_service.get_assets(DB, {"pageSize": 100, "maxItems": 100}, True)
                return {item["assetId"] for item in items["Items"]}

            return body(call, list_assets)
        finally:
            for active in reversed(patches):
                active.stop()


def _cascade_view(response):
    """The descendant keys a cascade GET (or listing row) names, attribute by attribute."""
    if response is None:
        return None
    return {
        "nodes": set(json.loads(response.get("nodes") or "{}")),
        "executionOrder": set(json.loads(response.get("executionOrder") or "[]")),
        "nodeNames": set(json.loads(response.get("nodeNames") or "{}")),
        "results": {entry["node"] for entry in json.loads(response.get("results") or "[]")},
        "totalNodes": int(response.get("totalNodes", 0)),
        # The whole response as text: an id (or a name, which contains the id here) left in any
        # attribute shows up in it.
        "text": json.dumps(response, default=str),
    }


def _observe(call, list_assets):
    out = {"listed": list_assets()}
    out["state"] = {asset_id: call(
        evaluate_svc, "GET", f"/compliance/state/{DB}/{asset_id}",
        {"databaseId": DB, "assetId": asset_id})["statusCode"] == 200
        for asset_id in LIVE + ARCHIVED}
    for name, path, params in (
            ("evaluate all", f"/compliance/evaluate/{DB}", {"databaseId": DB}),
            ("sweep", f"/compliance/sweep/{SCHEMA}", {"schemaName": SCHEMA})):
        response = call(evaluate_svc, "POST", path, params, request_body={})
        out[name] = body_of(response) if response["statusCode"] == 200 else None
    single = call(cascade_svc, "GET", f"/compliance/cascades/{CASCADE_ID}",
                  {"cascadeId": CASCADE_ID})
    out["cascade"] = _cascade_view(body_of(single) if single["statusCode"] == 200 else None)
    listing = call(cascade_svc, "GET", "/compliance/cascades", query={"state": "completed"})
    rows = body_of(listing)["cascades"] if listing["statusCode"] == 200 else []
    out["cascade listing"] = _cascade_view(rows[0]) if rows else None
    return out


@pytest.fixture(scope="module")
def observations():
    return {}


def _observed(set_name, role_enforcer, cache):
    if set_name not in cache:
        roles, constraints = _roles(set_name)
        cache[set_name] = _in_world(role_enforcer(roles, constraints), roles, _observe)
    return cache[set_name]


def _visible_archived(found):
    """The archived assets the role may see: those whose compliance record it may read."""
    return {asset_id for asset_id in ARCHIVED if found["state"][asset_id]}


# ---------------------------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.parametrize("set_name", SET_NAMES)
def test_the_archived_counts_are_the_archived_assets_the_caller_sees(
        set_name, role_enforcer, observations):
    found = _observed(set_name, role_enforcer, observations)
    visible = _visible_archived(found)
    listed_archived = found["listed"] & set(ARCHIVED)
    reads_compliance = found["state"][LIVE[0]]
    # The archived assets the role may see in compliance are those the asset listing with archived
    # assets shows it, for a role that reads compliance records (D90: a role that reads no asset
    # row reads no archived asset's compliance either; one that reads no compliance record sees
    # none of either).
    assert visible == {asset_id for asset_id in ARCHIVED
                       if reads_compliance and asset_id in listed_archived}, (set_name, found)

    evaluate_all = found["evaluate all"]
    if evaluate_all is not None:
        assert {entry["assetId"] for entry in evaluate_all["assetsTriggered"]} == set(LIVE)
        assert evaluate_all["skippedArchived"] == len(visible), (set_name, evaluate_all)
        assert evaluate_all["skipped"] == len(visible), (set_name, evaluate_all)
        assert evaluate_all["withoutSchema"] == 0
        assert evaluate_all["assetsRemaining"] == 0
    sweep = found["sweep"]
    if sweep is not None:
        assert sweep["skippedArchived"] == (len(visible) if evaluate_all is not None else 0)
        # A role that may not evaluate DB counts its assets as not authorized: the live ones, and
        # the archived ones only when it may see them.
        assert sweep["skippedNotAuthorized"] == (
            0 if evaluate_all is not None else len(LIVE) + len(visible)), (set_name, sweep)
        assert sweep["skipped"] == sweep["skippedNotAuthorized"] + sweep["skippedArchived"]
        assert sweep["assetsRemaining"] == 0

    for name in ("cascade", "cascade listing"):
        view = found[name]
        if view is None:
            continue
        expected = {f"{DB}:{LIVE_CHILD}"}
        if ARCHIVED_CHILD in visible:
            expected.add(f"{DB}:{ARCHIVED_CHILD}")
        for attribute in ("nodes", "executionOrder", "nodeNames", "results"):
            assert view[attribute] == expected, (set_name, name, attribute, view)
        assert view["totalNodes"] == len(expected), (set_name, name, view)


@pytest.mark.unit
@pytest.mark.parametrize("set_name", [
    "template:compliance-admin.json", "template:compliance-readonly.json",
    "template:database-user.json", ARCHIVE_DENY_SET])
def test_a_role_refused_the_archive_cannot_see_the_archived_child_in_a_cascade(
        set_name, role_enforcer, observations):
    """The cascade alone: these roles read the cascade but not the archive, so the descendant the
    executor skipped as archived is in none of the attributes they read, by id or by name."""
    found = _observed(set_name, role_enforcer, observations)
    assert found["state"][ARCHIVED_CHILD] is False
    for name in ("cascade", "cascade listing"):
        view = found[name]
        assert view is not None, (set_name, name)
        for attribute in ("nodes", "executionOrder", "nodeNames", "results"):
            assert f"{DB}:{ARCHIVED_CHILD}" not in view[attribute], (set_name, name, attribute)
        assert ARCHIVED_CHILD not in view["text"], (set_name, name)
        assert view["totalNodes"] == 1


@pytest.mark.unit
def test_the_matrix_reaches_both_outcomes(role_enforcer, observations):
    """The comparison is not vacuous: an archived asset is counted for some roles and hidden from
    others, on every surface."""
    def of(name):
        return _observed(name, role_enforcer, observations)

    admin = of("template:database-admin.json")
    assert admin["listed"] == set(LIVE + ARCHIVED)
    assert admin["evaluate all"]["skippedArchived"] == 2
    assert admin["sweep"]["skippedArchived"] == 2
    assert f"{DB}:{ARCHIVED_CHILD}" in admin["cascade"]["nodes"]
    assert f"{DB}:{ARCHIVED_CHILD}" in admin["cascade listing"]["nodes"]

    # Compliance Admin of the database evaluates it but may not see its archive.
    compliance_admin = of("template:compliance-admin.json")
    assert compliance_admin["evaluate all"]["skippedArchived"] == 0
    assert compliance_admin["evaluate all"]["skipped"] == 0
    assert compliance_admin["sweep"]["skipped"] == 0
    assert compliance_admin["cascade"]["nodes"] == {f"{DB}:{LIVE_CHILD}"}
    assert compliance_admin["cascade"]["totalNodes"] == 1

    # The Database Admin template with a deny on the archive: hidden as from a role never granted it.
    denied = of(ARCHIVE_DENY_SET)
    assert denied["listed"] == set(LIVE)
    assert denied["evaluate all"]["skippedArchived"] == 0
    assert denied["sweep"]["skippedArchived"] == 0
    assert denied["cascade"]["nodes"] == {f"{DB}:{LIVE_CHILD}"}


def _capped(set_name, role_enforcer, path, params):
    """A call evaluating at most one asset: the other live asset and both archived ones (their ids
    sort last) are beyond the cap."""
    roles, constraints = _roles(set_name)

    def run(call, _):
        with patch.object(evaluate_svc, "MAX_SWEEP_ASSETS", 1):
            response = call(evaluate_svc, "POST", path, params, request_body={})
        assert response["statusCode"] == 200, response
        return body_of(response)
    return _in_world(role_enforcer(roles, constraints), roles, run)


@pytest.mark.unit
@pytest.mark.parametrize("path,params", [
    (f"/compliance/evaluate/{DB}", {"databaseId": DB}),
    (f"/compliance/sweep/{SCHEMA}", {"schemaName": SCHEMA}),
], ids=["evaluate all", "sweep"])
class TestTheRemainingCount:

    def test_archived_assets_the_caller_may_not_see_are_not_remaining(
            self, role_enforcer, path, params):
        body = _capped("template:compliance-admin.json", role_enforcer, path, params)
        assert [entry["assetId"] for entry in body["assetsTriggered"]] == [LIVE[0]]
        assert body["assetsRemaining"] == 1
        assert body["skipped"] == 0
        assert "NextToken" in body

    def test_archived_assets_the_caller_may_see_stay_remaining(self, role_enforcer, path, params):
        """The control: the Database Admin sees the archive, so the archived assets beyond the cap
        are remaining (their archived status is not read until a call reaches them)."""
        body = _capped("template:database-admin.json", role_enforcer, path, params)
        assert [entry["assetId"] for entry in body["assetsTriggered"]] == [LIVE[0]]
        assert body["assetsRemaining"] == 3


@pytest.mark.unit
def test_the_skipped_archived_status_is_the_executors():
    """The status the cascade service hides is the one the executor records."""
    assert cascade_svc.NODE_STATE_SKIPPED_ARCHIVED == executor.NODE_STATE_SKIPPED_ARCHIVED
