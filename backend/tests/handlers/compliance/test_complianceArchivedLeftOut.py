# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An archived asset keeps its compliance record, but the database overview, its counts and the
state, quarantine and exception listings leave it out for every role, as the asset listing does
without archived assets. A role allowed to read the archived asset's record gets it with the state
`archived` and the stored state as `recordedComplianceState`.

An archived asset is not evaluated (the trigger, Evaluate All, schema sweeps and cascades skip it),
so its record keeps the state it had when it was archived. The overview used to count it in that
state: an asset archived while pending stayed under Pending and in the tracked total, and a database
whose 25 live assets were all compliant showed 96 % compliant. A role allowed on the archive also
saw it in the quarantine list and the overview's state listings, and its record read said
`pending_evaluation` although nothing would evaluate it. Unarchiving the asset brings its stored
state back, and the unarchive's own stream event evaluates it once when auto-evaluation is on.

The listings run on moto tables built from the CDK definitions with the production enforcer of every
shipped role (each template of `documentation/permissionsTemplates/` and both seeded roles, plus two
synthetic roles named on both ids), and are compared with the real asset listing of the same role
(`assetService.get_assets`) without and with archived assets.
"""

from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.assets.test_assetService_history import _load as load_asset_service
from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    REAL_TO_UPDATE_EXPR, SYNC_RULES_SCHEMA_BODY, USER, body_of, claims_for, enforcer,
    mirror_engine, real_constants, rest_event, schema_row,
)
from backend.tests.handlers.compliance.test_complianceArchivedAssetAccess import (
    ARCHIVED_DB, DB, ROLE_SET_NAMES, USER_ID, _enforcer, _role_set,
)
from backend.tests.handlers.compliance.test_complianceTrigger import _sns_event, _stream_message
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceQuarantineService as quarantine_svc
from handlers.compliance import complianceTrigger as trigger
import backend.backend.handlers.authz as authz_module

SCHEMA = "left-out-schema"
# The live and the archived assets of DB, by the state their compliance record holds.
LIVE = {"live-compliant": "compliant", "live-pending": "pending_evaluation",
        "live-quarantined": "quarantined", "live-exception": "exception"}
ARCHIVED = {"archived-pending": "pending_evaluation", "archived-quarantined": "quarantined",
            "archived-exception": "exception"}
STATE_LISTINGS = ("pending_evaluation", "quarantined", "exception")
SERVICES = (store, evaluate_svc, quarantine_svc, trigger)


@pytest.fixture(scope="module", autouse=True)
def _real_permission_constants():
    """The real permission field matrix for the enforcer and the template import (see
    test_complianceTemplateRolesEndToEnd)."""
    with real_constants("backend.backend.handlers.authz",
                        "backend.backend.handlers.auth.authConstraintsTemplateService"):
        yield


@pytest.fixture
def role_enforcer():
    previous = (authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map)
    with patch.object(authz_module, "log_authorization", MagicMock()):
        yield _enforcer
    authz_module.casbin_user_policy_map, authz_module.casbin_user_enforcer_map = previous


def _record(asset_id, state):
    name = f"Asset {asset_id}"
    record = {"databaseId": DB, "assetId": asset_id, "schemaName": SCHEMA,
              "schemaSource": "database", "complianceState": state, "assetName": name,
              "assetNameSort": f"{name.lower()}#{asset_id}",
              "assetSearchText": f"{name.lower()} {asset_id}"}
    if state != "pending_evaluation":
        record["lastEvaluatedAt"] = "2026-10-01T00:00:00+00:00"
        record["lastEvaluationStatus"] = "completed"
    if state == "exception":
        record.update(exceptionGranted=True, exceptionReason="accepted",
                      exceptionSchemaName=SCHEMA, exceptionSchemaVersion=1)
    return record


def _seed(doubles, live, archived):
    doubles["databaseStorage"].put_item(Item={"databaseId": DB, "complianceSchemaName": SCHEMA,
                                              "complianceAutoEval": False})
    doubles["complianceSchemaStorage"].put_item(
        Item=schema_row(SCHEMA, body=SYNC_RULES_SCHEMA_BODY, database_id=DB))
    for asset_id, state in list(live.items()) + list(archived.items()):
        doubles["assetStorage"].put_item(Item={
            "databaseId": ARCHIVED_DB if asset_id in archived else DB, "assetId": asset_id,
            "assetName": f"Asset {asset_id}", "tags": [],
            **({"status": "archived"} if asset_id in archived else {})})
        doubles["complianceAssetStateStorage"].put_item(Item=_record(asset_id, state))


def _in_world(enforcer_instance, roles, body, live=LIVE, archived=ARCHIVED):
    """Run `body(call, list_assets)` in a fresh seeded world for one enforcer: `call(service,
    method, path, params, query)` invokes a compliance handler, `list_assets(show_archived)` the
    asset listing of DB."""
    stand_in = MagicMock(name="CasbinEnforcer")
    stand_in.enforceAPI.return_value = True
    stand_in.enforce.side_effect = enforcer_instance.enforce
    mirror_engine(stand_in, enforcer_instance)
    asset_service = load_asset_service()
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, SERVICES)
        for module in (evaluate_svc, quarantine_svc):
            patches += [patch.object(module, "CasbinEnforcer", return_value=stand_in),
                        patch.object(module, "request_to_claims", claims_for(USER_ID))]
        patches += [
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
            _seed(doubles, live, archived)

            def call(service, method, path, params=None, query=None):
                response = service.lambda_handler(rest_event(method, path, params, query),
                                                  MagicMock())
                assert response["statusCode"] < 500, (method, path, response)
                return response

            def list_assets(show_archived):
                items = asset_service.get_assets(DB, {"pageSize": 100, "maxItems": 100},
                                                 show_archived)
                return {item["assetId"] for item in items["Items"]}

            return body(call, list_assets)
        finally:
            for active in reversed(patches):
                active.stop()


def _ids(response, key):
    return {row["assetId"] for row in body_of(response)[key]} if response["statusCode"] == 200 \
        else None


def _observe(call, list_assets):
    out = {"listing": list_assets(False), "listing with archived": list_assets(True)}
    overview = call(evaluate_svc, "GET", f"/compliance/state/{DB}", {"databaseId": DB})
    out["overview"] = _ids(overview, "assets")
    out["summary"] = body_of(overview)["summary"] if overview["statusCode"] == 200 else None
    out["total"] = body_of(overview)["totalAssets"] if overview["statusCode"] == 200 else None
    for state in STATE_LISTINGS:
        out[f"overview {state}"] = _ids(call(
            evaluate_svc, "GET", f"/compliance/state/{DB}", {"databaseId": DB},
            {"state": state}), "assets")
    out["quarantine"] = _ids(call(quarantine_svc, "GET", "/compliance/quarantine",
                                  query={"databaseId": DB}), "quarantinedAssets")
    out["quarantine all databases"] = _ids(call(quarantine_svc, "GET", "/compliance/quarantine"),
                                           "quarantinedAssets")
    out["state"] = {}
    for asset_id in list(LIVE) + list(ARCHIVED):
        response = call(evaluate_svc, "GET", f"/compliance/state/{DB}/{asset_id}",
                        {"databaseId": DB, "assetId": asset_id})
        out["state"][asset_id] = (body_of(response) if response["statusCode"] == 200
                                  else response["statusCode"])
    return out


@pytest.fixture(scope="module")
def observations():
    return {}


def _observed(set_name, role_enforcer, cache):
    if set_name not in cache:
        roles, constraints = _role_set(set_name)
        cache[set_name] = _in_world(role_enforcer(roles, constraints), roles, _observe)
    return cache[set_name]


LISTINGS = ("overview",) + tuple(f"overview {state}" for state in STATE_LISTINGS) + (
    "quarantine", "quarantine all databases")


@pytest.mark.unit
@pytest.mark.parametrize("set_name", ROLE_SET_NAMES)
def test_the_listings_and_counts_leave_archived_assets_out_for_every_role(
        set_name, role_enforcer, observations):
    found = _observed(set_name, role_enforcer, observations)
    # The asset listing without archived assets lists no archived asset.
    assert not found["listing"] & set(ARCHIVED), found["listing"]
    for name in LISTINGS:
        assert not (found[name] or set()) & set(ARCHIVED), (set_name, name, found[name])
    if found["overview"] is not None:
        # The rows, the per-state counts and the total are those of the live assets.
        assert found["overview"] == set(LIVE)
        if found["listing"]:
            assert found["overview"] == found["listing"]
        assert found["total"] == len(LIVE)
        assert {state: count for state, count in found["summary"].items()
                if state != "error" and count} == {state: 1 for state in LIVE.values()}
        for state in STATE_LISTINGS:
            assert found[f"overview {state}"] == {
                asset_id for asset_id, held in LIVE.items() if held == state}
    if found["quarantine"] is not None and found["quarantine"]:
        assert found["quarantine"] == {"live-quarantined"}
        assert found["quarantine all databases"] == {"live-quarantined"}


@pytest.mark.unit
@pytest.mark.parametrize("set_name", ROLE_SET_NAMES)
def test_an_archived_record_reads_as_archived_wherever_it_can_be_read(
        set_name, role_enforcer, observations):
    found = _observed(set_name, role_enforcer, observations)
    for asset_id, state in ARCHIVED.items():
        record = found["state"][asset_id]
        if record == 403:
            continue
        assert record["complianceState"] == "archived", (set_name, asset_id, record)
        assert record["recordedComplianceState"] == state
        # The record itself is returned whole: its exception, for one.
        if state == "exception":
            assert record["exceptionGranted"] is True
    for asset_id, state in LIVE.items():
        record = found["state"][asset_id]
        if record != 403:
            assert record["complianceState"] == state
            assert "recordedComplianceState" not in record


@pytest.mark.unit
def test_the_matrix_reaches_both_outcomes(role_enforcer, observations):
    """Not vacuous: the Database Admin template reads the archived records (as archived) and lists
    the live assets in the overview and the asset listing alike; the Database User template is
    refused the archived records, as D82 decides."""
    admin = _observed("template:database-admin.json", role_enforcer, observations)
    assert admin["listing with archived"] == set(LIVE) | set(ARCHIVED)
    assert admin["overview"] == admin["listing"] == set(LIVE)
    assert admin["quarantine"] == {"live-quarantined"}
    assert all(admin["state"][asset_id]["complianceState"] == "archived" for asset_id in ARCHIVED)
    user = _observed("template:database-user.json", role_enforcer, observations)
    assert all(user["state"][asset_id] == 403 for asset_id in ARCHIVED)


@pytest.mark.unit
def test_a_database_whose_live_assets_are_all_compliant_is_100_percent_compliant(role_enforcer):
    """25 live assets, all compliant, and one asset archived while it was pending: the overview
    tracks 25 assets, none pending, all compliant (it used to track 26, one pending, 96 %)."""
    live = {f"live-{index:02d}": "compliant" for index in range(25)}
    archived = {"archived-pending": "pending_evaluation"}
    roles, constraints = _role_set("template:database-admin.json")

    def overview(call, _list_assets):
        return body_of(call(evaluate_svc, "GET", f"/compliance/state/{DB}", {"databaseId": DB}))

    body = _in_world(role_enforcer(roles, constraints), roles, overview, live, archived)
    assert body["totalAssets"] == 25
    assert body["summary"]["compliant"] == 25
    assert body["summary"]["pending_evaluation"] == 0
    assert round(100 * body["summary"]["compliant"] / body["totalAssets"]) == 100
    assert "archived-pending" not in {row["assetId"] for row in body["assets"]}


# ---------------------------------------------------------------------------------------------
# Unarchive: the asset's stored state comes back; the unarchive's own stream event evaluates it
# ---------------------------------------------------------------------------------------------
ASSET = "archived-pending"


@pytest.fixture
def unarchive_world():
    """Moto tables for the trigger and the evaluate service, with ASSET archived while pending in
    DB (bound to SCHEMA, auto-evaluation off), read by a caller allowed everywhere."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, SERVICES)
        patches += [patch.object(store, "to_update_expr", REAL_TO_UPDATE_EXPR),
                    patch.object(evaluate_svc, "request_to_claims", claims_for(USER)),
                    patch.object(evaluate_svc, "CasbinEnforcer", return_value=enforcer())]
        for active in patches:
            active.start()
        try:
            _seed(doubles, {}, {ASSET: "pending_evaluation"})
            yield doubles
        finally:
            for active in reversed(patches):
                active.stop()


def _state(asset_id=ASSET):
    return body_of(evaluate_svc.lambda_handler(rest_event(
        "GET", f"/compliance/state/{DB}/{asset_id}", {"databaseId": DB, "assetId": asset_id}),
        MagicMock()))


def _overview_summary():
    body = body_of(evaluate_svc.lambda_handler(rest_event(
        "GET", f"/compliance/state/{DB}", {"databaseId": DB}), MagicMock()))
    return body["totalAssets"], body["summary"]["pending_evaluation"]


def _unarchive(doubles):
    """The asset service's unarchive: the live row is written, the archived one deleted, and the
    asset table's stream carries a REMOVE of the archived key and an INSERT of the live one, which
    the trigger receives in one batch."""
    doubles["assetStorage"].put_item(Item={"databaseId": DB, "assetId": ASSET,
                                           "assetName": f"Asset {ASSET}", "tags": []})
    doubles["assetStorage"].delete_item(Key={"databaseId": ARCHIVED_DB, "assetId": ASSET})
    response = trigger.lambda_handler(_sns_event(
        _stream_message("REMOVE", database_id=ARCHIVED_DB, asset_id=ASSET),
        _stream_message("INSERT", database_id=DB, asset_id=ASSET)), MagicMock())
    assert response == {"batchItemFailures": []}


def _evaluations(doubles):
    return [row for row in doubles["complianceEvaluationStorage"].scan(ConsistentRead=True)["Items"]
            if row.get("assetId") == ASSET]


@pytest.mark.unit
class TestUnarchivingMakesTheAssetPendingAgain:

    def test_archived_then_unarchived_with_auto_evaluation_off(self, unarchive_world):
        archived = _state()
        assert archived["complianceState"] == "archived"
        assert archived["recordedComplianceState"] == "pending_evaluation"
        assert _overview_summary() == (0, 0)

        _unarchive(unarchive_world)

        assert _state()["complianceState"] == "pending_evaluation"
        assert "recordedComplianceState" not in _state()
        assert _overview_summary() == (1, 1)
        # Auto-evaluation is off: the asset waits for a sweep or an on-demand evaluation.
        assert _evaluations(unarchive_world) == []

    def test_with_auto_evaluation_on_the_unarchive_evaluates_it_once(self, unarchive_world):
        unarchive_world["databaseStorage"].update_item(
            Key={"databaseId": DB}, UpdateExpression="SET complianceAutoEval = :on",
            ExpressionAttributeValues={":on": True})
        assert _state()["complianceState"] == "archived"

        _unarchive(unarchive_world)

        # One evaluation for the one unarchive, through the trigger's own path: nothing else
        # starts one, and the REMOVE of the archived key evaluates nothing.
        evaluations = _evaluations(unarchive_world)
        assert len(evaluations) == 1
        state = _state()
        assert state["complianceState"] not in ("archived", "pending_evaluation")
        assert state["lastEvaluationId"] == evaluations[0]["evaluationId"]
        assert _overview_summary()[0] == 1
