# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The EDH permission templates grant exactly the routes and object types the add-on enforces.

`edh-user.json` reaches the workstation and catalog routes and never an admin route; `edh-admin.json`
adds the admin route prefix and the admin page. Both parse through the import model with only
ROLE_NAME supplied, and both grant /auth/loginProfile on GET and POST like every other route-granting
template, because the web app POSTs it at sign-in and the CLI GETs it to validate a session. Both grant
GET on /auth/routes, which covers the GET /auth/routes/api/allowed call the EDH pages make to decide which
actions to enable.
"""

import json
import os

import pytest
from aws_lambda_powertools.utilities.parser import parse

from backend.backend.common.constants import get_constraint_fields_for_object_type
from backend.backend.models.roleConstraints import ImportConstraintsTemplateRequestModel

_TEMPLATES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..", "..", "documentation", "permissionsTemplates")

USER_ROUTE_PREFIXES = {"/addon/edh/workstations", "/addon/edh/catalog"}
SIGN_IN_PREFIXES = {"/auth/loginProfile", "/secure-config"}
ROUTE_LISTING_PREFIX = "/auth/routes"
ADMIN_ROUTE_PREFIX = "/addon/edh/admin"
BARE_PREFIXES = ("/edh", "/edh/", "/addon/edh", "/addon/edh/")


def _template(name):
    path = os.path.join(_TEMPLATES_DIR, name)
    assert os.path.exists(path), f"{name} is missing from documentation/permissionsTemplates"
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _criteria(constraint):
    return (constraint.get("criteriaAnd") or []) + (constraint.get("criteriaOr") or [])


def _route_values(template, object_type, action):
    """route__path values allowed for ``action`` by the template's ``object_type`` constraints."""
    values = set()
    for constraint in template["constraints"]:
        if constraint["objectType"] != object_type:
            continue
        if not any(p["action"] == action and p["type"] == "allow"
                   for p in constraint["groupPermissions"]):
            continue
        for criterion in _criteria(constraint):
            if criterion["field"] == "route__path":
                values.add(criterion["value"])
    return values


def _entity_constraints(template):
    return [c for c in template["constraints"] if c["objectType"] not in ("api", "web")]


def _single(template, object_type):
    found = [c for c in template["constraints"] if c["objectType"] == object_type]
    assert len(found) == 1, f"expected one {object_type} constraint, found {len(found)}"
    return found[0]


def _allowed_actions(constraint):
    return {p["action"] for p in constraint["groupPermissions"] if p["type"] == "allow"}


@pytest.mark.unit
class TestEdhTemplatesImport:
    @pytest.mark.parametrize("name,count", [("edh-user.json", 6), ("edh-admin.json", 7)])
    def test_parses_through_the_import_model_with_role_name_only(self, name, count):
        template = _template(name)
        assert [v["name"] for v in template["variables"]] == ["ROLE_NAME"]
        model = parse({
            "template": template["metadata"],
            "variables": template["variables"],
            "variableValues": {"ROLE_NAME": "edh-role"},
            "constraints": template["constraints"],
        }, model=ImportConstraintsTemplateRequestModel)
        assert len(model.constraints) == len(template["constraints"]) == count

    @pytest.mark.parametrize("name", ["edh-user.json", "edh-admin.json"])
    def test_entity_constraints_use_only_matrix_fields_of_edh_types(self, name):
        entity = _entity_constraints(_template(name))
        assert entity, f"{name} grants no Tier-2 object type"
        for constraint in entity:
            assert constraint["objectType"] in {"edhWorkstation", "edhImage"}, constraint["name"]
            fields = get_constraint_fields_for_object_type(constraint["objectType"])
            criteria = _criteria(constraint)
            assert criteria, constraint["name"]
            for criterion in criteria:
                assert criterion["field"] in fields, f"{constraint['name']}: {criterion['field']}"

    @pytest.mark.parametrize("name", ["edh-user.json", "edh-admin.json"])
    def test_login_profile_is_granted_on_get_and_post(self, name):
        template = _template(name)
        assert "/auth/loginProfile" in _route_values(template, "api", "GET")
        assert "/auth/loginProfile" in _route_values(template, "api", "POST")

    @pytest.mark.parametrize("name", ["edh-user.json", "edh-admin.json"])
    def test_route_listing_is_granted_on_get_only(self, name):
        template = _template(name)
        assert ROUTE_LISTING_PREFIX in _route_values(template, "api", "GET")
        for action in ("POST", "PUT", "DELETE"):
            assert ROUTE_LISTING_PREFIX not in _route_values(template, "api", action), action

    @pytest.mark.parametrize("name", ["edh-user.json", "edh-admin.json"])
    def test_no_bare_prefix_is_granted(self, name):
        for constraint in _template(name)["constraints"]:
            for criterion in _criteria(constraint):
                assert criterion["value"] not in BARE_PREFIXES, constraint["name"]

    @pytest.mark.parametrize("name", ["edh-user.json", "edh-admin.json"])
    def test_role_creation_guidance_lives_in_the_description(self, name):
        description = _template(name)["metadata"]["description"]
        assert "mfaRequired" in description
        assert "ROLE_NAME" in description
        assert len(description) <= 1024


@pytest.mark.unit
class TestEdhUserTemplate:
    def test_api_grants_are_the_user_routes_the_sign_in_probes_and_the_route_listing(self):
        template = _template("edh-user.json")
        assert _route_values(template, "api", "GET") == (
            USER_ROUTE_PREFIXES | SIGN_IN_PREFIXES | {ROUTE_LISTING_PREFIX})
        assert _route_values(template, "api", "POST") == {
            "/auth/loginProfile", "/addon/edh/workstations",
        }
        assert _route_values(template, "api", "DELETE") == {"/addon/edh/workstations"}
        assert _route_values(template, "api", "PUT") == set()

    def test_no_admin_route_is_reachable(self):
        for constraint in _template("edh-user.json")["constraints"]:
            for criterion in _criteria(constraint):
                assert not criterion["value"].startswith(ADMIN_ROUTE_PREFIX), constraint["name"]
                assert not criterion["value"].startswith("/edh/admin"), constraint["name"]

    def test_web_grant_is_the_workstations_page_only(self):
        assert _route_values(_template("edh-user.json"), "web", "GET") == {"/edh/workstations"}

    def test_workstation_and_image_grants(self):
        template = _template("edh-user.json")
        assert _allowed_actions(_single(template, "edhWorkstation")) == {"GET", "POST", "DELETE"}
        assert _allowed_actions(_single(template, "edhImage")) == {"GET"}


@pytest.mark.unit
class TestEdhAdminTemplate:
    def test_api_grants_add_the_admin_prefix_on_every_verb_the_admin_routes_use(self):
        template = _template("edh-admin.json")
        assert _route_values(template, "api", "GET") == (
            USER_ROUTE_PREFIXES | SIGN_IN_PREFIXES | {ROUTE_LISTING_PREFIX, ADMIN_ROUTE_PREFIX})
        assert _route_values(template, "api", "POST") == {
            "/auth/loginProfile", "/addon/edh/workstations", ADMIN_ROUTE_PREFIX,
        }
        assert _route_values(template, "api", "DELETE") == {
            "/addon/edh/workstations", ADMIN_ROUTE_PREFIX,
        }

    def test_web_grants_both_pages(self):
        assert _route_values(_template("edh-admin.json"), "web", "GET") == {
            "/edh/workstations", "/edh/admin",
        }

    def test_workstation_and_image_grants(self):
        template = _template("edh-admin.json")
        assert _allowed_actions(_single(template, "edhWorkstation")) == {"GET", "POST", "DELETE"}
        assert _allowed_actions(_single(template, "edhImage")) == {"GET", "POST", "PUT", "DELETE"}

    def test_description_names_the_three_inert_mfa_configurations(self):
        description = _template("edh-admin.json")["metadata"]["description"]
        for phrase in ("external OAuth", "useForAllLambdas", "API key"):
            assert phrase in description, phrase
