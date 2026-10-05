# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A role built from a shipped permission template reaches the add-on routes its pages call.

The web Physna viewer plugin requests ``GET /addon/physna/viewer`` (``API_ADDON_PHYSNA_VIEWER``) for a
synced file, and the handler checks Tier 1 with ``enforceAPI`` before it reads the asset, so a role
whose ``api`` constraints do not match the path receives ``403`` whatever its asset grants are. The
seeded roles reach it: the read-only seed appends criterion ``16_${roleNameIDClean}_api_paths`` when
``app.addons.usePhysnaSync.enabled`` is set (``dynamodb-authdefaults-ro-construct.ts``) and the
administrator seed matches every api path. The templates are static JSON, so they carry the criterion
unconditionally; on a deployment without the add-on it matches no deployed route.

Each template is run through the import's own substitution and conversion (``substitute_variables``,
``build_constraint_data``) and then through the real policy generation and Casbin evaluation, with
only the two table reads stubbed. Reading the JSON alone would accept a criterion Casbin never
matches: a wrong operator, a misspelt value, or a constraint whose methods exclude GET.

``ADDON_ROUTE_GRANTS`` records, per add-on route, the methods each template allows. A route added to
``ADDON_ROUTES`` without an entry fails ``test_every_registered_addon_route_has_a_template_decision``,
which is where the templates that should reach it are decided.
"""

import json
import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module  # noqa: E402
from backend.backend.common.apiRoutes import ADDON_ROUTES, API_ADDON_PHYSNA_VIEWER  # noqa: E402
from backend.backend.handlers.auth import authConstraintsTemplateService as svc  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "documentation" / "permissionsTemplates"

ROLE_NAME = "template-role"
USER_ID = "template-user"
# What an operator supplies when importing the shipped templates.
VARIABLE_VALUES = {"ROLE_NAME": ROLE_NAME, "DATABASE_ID": "template-db", "TAG_VALUE": "locked"}
WRITE_METHODS = ("POST", "PUT", "DELETE")

# The methods each template allows on each add-on route; a template absent from a route's entry
# allows none. database-tag-admin reaches only the tag pages and deny-tagged-assets is an asset-deny
# overlay with no api constraint, so neither grants an add-on route.
ADDON_ROUTE_GRANTS = {
    API_ADDON_PHYSNA_VIEWER: {
        "database-admin.json": ("GET",),
        "database-user.json": ("GET",),
        "database-readonly.json": ("GET",),
        "global-readonly.json": ("GET",),
    },
}


def _template_names():
    return sorted(path.name for path in TEMPLATES_DIR.glob("*.json"))


def _stored_constraints(template_name, drop_value=None):
    """The constraints an import of the template stores, in the shape the policy builder reads.

    ``drop_value`` first removes every criterion carrying that value, which is the template as it
    reads without that grant.
    """
    document = json.loads((TEMPLATES_DIR / template_name).read_text(encoding="utf-8"))
    constraints = [
        {
            "name": constraint["name"],
            "description": constraint["description"],
            "objectType": constraint["objectType"],
            "criteriaAnd": [c for c in constraint.get("criteriaAnd", [])
                            if drop_value is None or c.get("value") != drop_value],
            "criteriaOr": [c for c in constraint.get("criteriaOr", [])
                           if drop_value is None or c.get("value") != drop_value],
            "groupPermissions": constraint.get("groupPermissions", []),
        }
        for constraint in document["constraints"]
    ]
    substituted = svc.substitute_variables(constraints, VARIABLE_VALUES)
    return [svc.build_constraint_data(constraint, ROLE_NAME, {"tokens": [USER_ID]})
            for constraint in substituted]


def _api_request(path):
    """The Tier-1 object ``enforceAPI`` evaluates for a request path."""
    return {"object__type": "api", "route__path": path}


def _concrete_path(route_path):
    """The route template with each ``{parameter}`` segment filled, as a request carries it."""
    return re.sub(r"\{[^/{}]+\}", "probe-id", route_path)


def _addon_criteria(template_name):
    """(operator, value) of every ``/addon/`` route__path criterion in the template's api constraints."""
    document = json.loads((TEMPLATES_DIR / template_name).read_text(encoding="utf-8"))
    return [
        (criterion["operator"], criterion["value"])
        for constraint in document["constraints"]
        if constraint["objectType"] == "api"
        for criterion in constraint.get("criteriaAnd", []) + constraint.get("criteriaOr", [])
        if criterion.get("field") == "route__path"
        and str(criterion.get("value", "")).startswith("/addon/")
    ]


@pytest.fixture
def template_enforcer():
    """Build the real CasbinEnforcer from a list of stored constraints.

    The per-user policy and enforcer caches are cleared on every build, so each policy is generated
    from the constraints passed in. The two table reads are the only stubs, and the denial audit
    writer is replaced so a denied check writes nothing. Both caches are restored afterwards.
    """
    state = {"constraints": []}
    previous_policy_map = authz_module.casbin_user_policy_map
    previous_enforcer_map = authz_module.casbin_user_enforcer_map

    def build(constraints):
        state["constraints"] = constraints
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}
        return authz_module.CasbinEnforcer(
            {"tokens": [USER_ID], "roles": [ROLE_NAME], "mfaEnabled": True})

    with patch.object(
        authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
        return_value=[{"userId": USER_ID, "roleName": ROLE_NAME}],
    ), patch.object(
        authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
        side_effect=lambda role_names: state["constraints"],
    ), patch.object(authz_module, "log_authorization", MagicMock()):
        try:
            yield build
        finally:
            authz_module.casbin_user_policy_map = previous_policy_map
            authz_module.casbin_user_enforcer_map = previous_enforcer_map


@pytest.mark.unit
class TestTemplatesGrantTheAddonRoutes:

    def test_the_templates_are_found(self):
        names = _template_names()
        assert len(names) >= 6, f"expected the shipped permission templates, found {names}"
        for route, grants in ADDON_ROUTE_GRANTS.items():
            missing = sorted(set(grants) - set(names))
            assert not missing, f"{route.path}: granting templates not found: {missing}"

    def test_every_registered_addon_route_has_a_template_decision(self):
        undecided = sorted(route.path for route in set(ADDON_ROUTES) - set(ADDON_ROUTE_GRANTS))
        assert not undecided, f"add these add-on routes to ADDON_ROUTE_GRANTS: {undecided}"

    @pytest.mark.parametrize("template_name", _template_names())
    @pytest.mark.parametrize("route", sorted(ADDON_ROUTE_GRANTS, key=lambda route: route.path),
                             ids=lambda route: route.path)
    def test_a_template_role_is_allowed_exactly_the_granted_methods(
            self, template_enforcer, route, template_name):
        expected = set(ADDON_ROUTE_GRANTS[route].get(template_name, ())) & set(route.methods)
        enforcer = template_enforcer(_stored_constraints(template_name))
        request = _api_request(_concrete_path(route.path))
        allowed = {method for method in route.methods if enforcer.enforce(request, method)}
        assert allowed == expected, (
            f"{template_name} allows {sorted(allowed)} on {route.path}, expected {sorted(expected)}")

    @pytest.mark.parametrize("template_name", [
        "database-user.json", "database-readonly.json", "global-readonly.json"])
    def test_a_non_admin_template_opens_no_write_method_on_the_viewer(
            self, template_enforcer, template_name):
        """The viewer grant sits on the template's GET constraint, so no write method reaches the
        path through it."""
        enforcer = template_enforcer(_stored_constraints(template_name))
        request = _api_request(API_ADDON_PHYSNA_VIEWER.path)
        assert enforcer.enforce(request, "GET") is True
        opened = [method for method in WRITE_METHODS if enforcer.enforce(request, method)]
        assert not opened, f"{template_name} allows {opened} on {API_ADDON_PHYSNA_VIEWER.path}"


@pytest.mark.unit
class TestTheTemplateEvaluationCanFail:
    """Controls for the class above: an enforcer built from a template is neither allow-all nor
    deny-all, and the add-on criterion is what grants the route."""

    def test_a_template_role_is_allowed_a_granted_route_and_denied_another(self, template_enforcer):
        enforcer = template_enforcer(_stored_constraints("database-readonly.json"))
        assert enforcer.enforce(_api_request("/database/template-db/assets"), "GET") is True
        assert enforcer.enforce(_api_request("/buckets"), "GET") is False

    @pytest.mark.parametrize("template_name", sorted(ADDON_ROUTE_GRANTS[API_ADDON_PHYSNA_VIEWER]))
    def test_without_the_viewer_criterion_the_route_is_denied(self, template_enforcer, template_name):
        """No other prefix a template carries covers the viewer path, so the criterion is the grant
        rather than a duplicate of one."""
        enforcer = template_enforcer(
            _stored_constraints(template_name, drop_value=API_ADDON_PHYSNA_VIEWER.path))
        assert enforcer.enforce(_api_request(API_ADDON_PHYSNA_VIEWER.path), "GET") is False


@pytest.mark.unit
class TestTemplatesMatchTheSeededGrant:
    """The templates spell the viewer grant as the seeded read-only role does, so a template-built
    role and the seeded role reach the same paths."""

    @pytest.mark.parametrize("template_name", sorted(ADDON_ROUTE_GRANTS[API_ADDON_PHYSNA_VIEWER]))
    def test_the_template_spells_the_criterion_as_the_seed_does(self, template_name):
        assert _addon_criteria(template_name) == [("starts_with", API_ADDON_PHYSNA_VIEWER.path)]
