# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The constraint editor's listings and the allowed-route checks follow the deployed add-ons.

The core stack passes the switches of the add-ons a deployment builds to the listing Lambdas in
``ADDON_FEATURES_ENABLED`` (``common/addonFeatures.py``). With an add-on off its object types are not
in ``GET /auth/constraints/permissionObjects``, its routes are not in ``GET /auth/routes/api``, and
``GET /auth/routes/api/allowed`` neither evaluates nor audits them; with it on they are back. A
constraint already stored with the type of an add-on that is now off still reads, lists and deletes;
a new one, or a permission template naming the type, is refused with a sentence that says why.

Every "absent with the add-on off" assertion has its control: the same call with the add-on on lists
the entry, so a listing that lost the entry for another reason fails here too.
"""

import json
import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("AUDIT_LOG_AUTHORIZATION", "test-auditAuthorization")

from common.addonFeatures import ADDON_FEATURES_ENABLED_ENV  # noqa: E402
from backend.backend.handlers.auth import authConstraintsService as svc  # noqa: E402
from backend.backend.handlers.auth import routes as routes_module  # noqa: E402
from backend.backend.models.roleConstraints import ImportConstraintsTemplateRequestModel  # noqa: E402

_CLAIMS = {"tokens": ["u1"], "roles": ["admin"], "externalAttributes": [], "mfaEnabled": False}

EDH_TYPES = {"edhWorkstation", "edhImage", "edhCapacityReservation", "edhContainer"}
OFF = ""
EDH_ON = "EDH_WORKSTATIONS"
EDH_ALL = "EDH_WORKSTATIONS,EDH_CONTAINERS,EDH_AMI_RECIPES"

_TEMPLATES = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "documentation", "permissionsTemplates"))


@pytest.fixture
def switches(monkeypatch):
    """Set (a string) or clear (None) the deployment's add-on switches for one call."""
    def _set(value):
        if value is None:
            monkeypatch.delenv(ADDON_FEATURES_ENABLED_ENV, raising=False)
        else:
            monkeypatch.setenv(ADDON_FEATURES_ENABLED_ENV, value)
    return _set


def _event(method, path, path_parameters=None, body=None):
    event = {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_parameters or {},
        "queryStringParameters": {},
        "headers": {"authorization": "Bearer test-token"},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def _payload(response):
    body = json.loads(response["body"])
    return body.get("message", body) if isinstance(body.get("message"), dict) else body


@contextmanager
def _api_allowed(module):
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    with patch.object(module, "request_to_claims", return_value=dict(_CLAIMS)), \
            patch.object(module, "CasbinEnforcer", return_value=enforcer):
        yield enforcer


# --- GET /auth/constraints/permissionObjects ---------------------------------------------------


def _object_types():
    with _api_allowed(svc):
        response = svc.lambda_handler(_event("GET", "/auth/constraints/permissionObjects"), {})
    assert response["statusCode"] == 200, response
    return {entry["value"] for entry in _payload(response)["objectTypes"]}


def test_permission_objects_with_the_add_on_off_list_no_edh_type(switches):
    switches(OFF)
    listed = _object_types()
    assert listed & EDH_TYPES == set()
    assert {"database", "asset", "api", "web"} <= listed


def test_permission_objects_with_the_add_on_on_list_its_types(switches):
    switches(EDH_ON)
    assert _object_types() & EDH_TYPES == {"edhWorkstation", "edhImage", "edhCapacityReservation"}
    switches("EDH_WORKSTATIONS,EDH_CONTAINERS")
    assert _object_types() & EDH_TYPES == EDH_TYPES


def test_permission_objects_without_the_variable_list_every_type(switches):
    """A Lambda that was not told which add-ons are deployed lists every type, as before."""
    switches(None)
    assert _object_types() & EDH_TYPES == EDH_TYPES


# --- GET /auth/routes/api and GET /auth/routes/api/allowed ----------------------------------------


def _listed_paths():
    with _api_allowed(routes_module):
        response = routes_module.lambda_handler(_event("GET", "/auth/routes/api"), {})
    assert response["statusCode"] == 200, response
    return {route["path"] for route in _payload(response)["routes"]}


def test_the_route_listing_with_the_add_ons_off_lists_no_add_on_route(switches):
    switches(OFF)
    paths = _listed_paths()
    assert [path for path in paths if path.startswith("/addon/")] == []
    assert "/database" in paths


def test_the_route_listing_lists_the_routes_of_each_deployed_switch(switches):
    switches(EDH_ON)
    edh = {path for path in _listed_paths() if path.startswith("/addon/")}
    assert "/addon/edh/workstations" in edh
    assert [path for path in edh if path.startswith(("/addon/edh/containers", "/addon/edh/admin/ami-recipes"))] == []
    assert "/addon/physna/viewer" not in edh
    switches(EDH_ALL)
    edh = {path for path in _listed_paths() if path.startswith("/addon/edh/")}
    assert {"/addon/edh/containers/jobs", "/addon/edh/admin/ami-recipes"} <= edh
    assert len(edh) == 33
    switches("PHYSNA_ADDON")
    assert {path for path in _listed_paths() if path.startswith("/addon/")} == {"/addon/physna/viewer"}


def _allowed_probe(deny):
    """Run GET /auth/routes/api/allowed; return the probed paths, the listed paths and the denial
    records written."""
    written = []
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.service_object.enforce.side_effect = lambda obj, act: not deny
    with patch.object(routes_module, "request_to_claims", return_value=dict(_CLAIMS)), \
            patch.object(routes_module, "CasbinEnforcer", return_value=enforcer), \
            patch.object(routes_module.auditLogging, "_write_batch_to_cloudwatch", create=True,
                         side_effect=lambda group, batch, event: written.extend(batch)):
        response = routes_module.lambda_handler(_event("GET", "/auth/routes/api/allowed"), {})
    assert response["statusCode"] == 200, response
    probed = [call.args[0]["route__path"] for call in enforcer.service_object.enforce.call_args_list]
    listed = {route["path"] for route in _payload(response)["routes"]}
    return probed, listed, written


def test_the_allowed_route_check_with_the_add_ons_off_evaluates_and_audits_no_add_on_route(switches):
    switches(OFF)
    probed, _, written = _allowed_probe(deny=True)
    assert probed, "no pair was evaluated, so the assertions below would be vacuous"
    assert [path for path in probed if path.startswith("/addon/")] == []
    assert written and [record for record in written if "/addon/" in record] == []


def test_the_allowed_route_check_evaluates_and_audits_a_deployed_add_on(switches):
    switches(OFF)
    probed_off, _, written_off = _allowed_probe(deny=True)
    switches(EDH_ALL)
    probed_on, listed_on, _ = _allowed_probe(deny=False)
    edh_pairs = [path for path in probed_on if path.startswith("/addon/edh/")]
    assert len(edh_pairs) == 43
    assert len(probed_on) == len(probed_off) + 43
    assert "/addon/edh/workstations" in listed_on
    _, _, written_on = _allowed_probe(deny=True)
    assert len(written_on) == len(written_off) + 43


# --- Stored constraints of a disabled add-on's type, new constraints, templates ---------------------


def _edh_constraint_row(constraint_id):
    return {
        "constraintId": constraint_id,
        "name": constraint_id,
        "description": "EDH workstations for the admin role",
        "objectType": "edhWorkstation",
        "criteriaAnd": json.dumps([{"field": "edhImageId", "operator": "contains", "value": ".*"}]),
        "criteriaOr": json.dumps([]),
        "groupPermissions": json.dumps([{"groupId": "admin", "permission": "GET", "permissionType": "allow"}]),
        "userPermissions": json.dumps([]),
    }


@pytest.fixture
def stored_edh_constraint(monkeypatch):
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        constraints = resource.create_table(
            TableName=svc.constraints_table_name,
            KeySchema=[{"AttributeName": "constraintId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "constraintId", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        roles = resource.create_table(
            TableName=svc.roles_table_name,
            KeySchema=[{"AttributeName": "roleName", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "roleName", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        roles.put_item(Item={"roleName": "admin"})
        constraints.put_item(Item=_edh_constraint_row("edh_ws_admin"))
        constraints.put_item(Item={**_edh_constraint_row("edh_ws_admin#group#admin"), "groupId": "admin"})
        monkeypatch.setattr(svc, "constraints_table", constraints)
        monkeypatch.setattr(svc, "roles_table", roles)
        yield constraints


def test_a_stored_constraint_of_a_disabled_add_on_type_still_reads_lists_and_deletes(switches, stored_edh_constraint):
    switches(OFF)
    with _api_allowed(svc):
        one = svc.lambda_handler(_event("GET", "/auth/constraints/edh_ws_admin", {"constraintId": "edh_ws_admin"}), {})
        listing = svc.lambda_handler(_event("GET", "/auth/constraints"), {})
    assert one["statusCode"] == 200, one
    assert json.loads(one["body"])["constraint"]["objectType"] == "edhWorkstation"
    assert listing["statusCode"] == 200, listing
    items = json.loads(listing["body"])["message"]["Items"]
    assert [item["objectType"] for item in items if item["constraintId"] == "edh_ws_admin"] == ["edhWorkstation"]
    # Disabling the add-on deleted nothing: both rows are still there until an administrator deletes them.
    assert stored_edh_constraint.scan()["Count"] == 2
    with _api_allowed(svc):
        deleted = svc.lambda_handler(
            _event("DELETE", "/auth/constraints/edh_ws_admin", {"constraintId": "edh_ws_admin"}), {})
    assert deleted["statusCode"] == 200, deleted
    assert stored_edh_constraint.scan()["Count"] == 0


def _create_body(object_type, field):
    return {
        "identifier": "edh_ws_new",
        "name": "edh_ws_new",
        "description": "a new constraint",
        "objectType": object_type,
        "criteriaAnd": [{"field": field, "operator": "contains", "value": ".*"}],
        "groupPermissions": [{"groupId": "admin", "permission": "GET", "permissionType": "allow"}],
    }


def test_a_new_constraint_of_a_disabled_add_on_type_is_refused_with_a_reason(switches, stored_edh_constraint):
    switches(OFF)
    with _api_allowed(svc):
        refused = svc.lambda_handler(_event("POST", "/auth/constraints/edh_ws_new", {"constraintId": "edh_ws_new"},
                                            _create_body("edhWorkstation", "edhImageId")), {})
        unknown = svc.lambda_handler(_event("POST", "/auth/constraints/edh_ws_new", {"constraintId": "edh_ws_new"},
                                            _create_body("notAType", "databaseId")), {})
    assert refused["statusCode"] == 400, refused
    assert "'edhWorkstation' belongs to an add-on that is not enabled on this deployment" in refused["body"]
    assert unknown["statusCode"] == 400, unknown
    assert "Allowed values: database, asset" in unknown["body"] and "edh" not in unknown["body"]
    # Control: with the add-on on the same request is stored.
    switches(EDH_ON)
    with _api_allowed(svc):
        accepted = svc.lambda_handler(_event("POST", "/auth/constraints/edh_ws_new", {"constraintId": "edh_ws_new"},
                                             _create_body("edhWorkstation", "edhImageId")), {})
    assert accepted["statusCode"] == 200, accepted


def _template(name):
    with open(os.path.join(_TEMPLATES, name), encoding="utf-8") as handle:
        template = json.load(handle)
    values = {variable["name"]: "admin" for variable in template.get("variables", [])}
    return {**template, "variableValues": {**values, "ROLE_NAME": "admin"}}


@pytest.mark.parametrize("template_file", ["edh-admin.json", "edh-user.json"])
def test_an_edh_permission_template_is_refused_while_the_add_on_is_off(switches, template_file):
    body = _template(template_file)
    switches(OFF)
    with pytest.raises(ValueError) as refused:
        ImportConstraintsTemplateRequestModel(**body)
    assert "belongs to an add-on that is not enabled on this deployment" in str(refused.value)
    switches(EDH_ALL)
    ImportConstraintsTemplateRequestModel(**body)
