# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What the allowed-routes listing reports, to a role built from each database template, for the
routes the web app gates its database, asset and search actions on.

The web pages disable or hide an action when ``GET /auth/routes/api/allowed`` does not list its route
and method (``useAllowedRoutes().can()``). Their tests drive the pages with the listings in
``web/src/features/orchestration/permissions/templateRoleRoutes.testdata.ts``. This test computes the
same listing from the shipped templates: each template is imported with the service's own
substitution and conversion, then every route/method pair is evaluated by the real Casbin policy with
each ``{param}`` segment replaced by the listing's probe segment, as ``get_allowed_api_routes`` does.
A template change that moves one of these outcomes fails here, naming the route.
"""

import json
import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module  # noqa: E402
from backend.backend.common.apiRoutes import ALL_API_ROUTES  # noqa: E402
from backend.backend.handlers.auth import authConstraintsTemplateService as svc  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "documentation" / "permissionsTemplates"

ROLE_NAME = "template-role"
USER_ID = "template-user"
DATABASE_ID = "template-db"
VARIABLE_VALUES = {"ROLE_NAME": ROLE_NAME, "DATABASE_ID": DATABASE_ID, "TAG_VALUE": "locked"}
# routes.API_ROUTE_PROBE_SEGMENT: the value the listing evaluates each {param} segment with.
PROBE_SEGMENT = "vams:any"

# Route -> methods the listing reports. Mirrors templateRoleRoutes.testdata.ts; a route with no
# allowed method is left out of the listing, as it is here.
EXPECTED_LISTING = {
    "database-readonly.json": {
        "/database": {"GET"},
        "/database/{databaseId}": {"GET"},
        "/assets": {"GET"},
        "/database/{databaseId}/assets/{assetId}": {"GET"},
        "/database/{databaseId}/assets/{assetId}/metadata": {"GET"},
    },
    "database-user.json": {
        "/database": {"GET"},
        "/database/{databaseId}": {"GET"},
        "/assets": {"GET", "POST"},
        "/uploads": {"POST"},
        "/database/{databaseId}/assets/{assetId}": {"GET"},
        "/database/{databaseId}/assets/{assetId}/archiveAsset": {"DELETE"},
        "/database/{databaseId}/assets/{assetId}/metadata": {"GET", "POST"},
    },
}
# Every route either listing names; each template is evaluated on all of them, so a route missing from
# a template's listing asserts that the role is offered no method on it.
GATED_ROUTE_PATHS = sorted(set().union(*(listing.keys() for listing in EXPECTED_LISTING.values())))


def _stored_constraints(template_name):
    document = json.loads((TEMPLATES_DIR / template_name).read_text(encoding="utf-8"))
    constraints = [
        {
            "name": constraint["name"],
            "description": constraint["description"],
            "objectType": constraint["objectType"],
            "criteriaAnd": constraint.get("criteriaAnd", []),
            "criteriaOr": constraint.get("criteriaOr", []),
            "groupPermissions": constraint.get("groupPermissions", []),
        }
        for constraint in document["constraints"]
    ]
    substituted = svc.substitute_variables(constraints, VARIABLE_VALUES)
    return [svc.build_constraint_data(constraint, ROLE_NAME, {"tokens": [USER_ID]})
            for constraint in substituted]


def _probe_path(route_path):
    return re.sub(r"\{[^/{}]+\}", PROBE_SEGMENT, route_path)


@pytest.fixture
def template_enforcer():
    """The real CasbinEnforcer over the given constraints; only the two table reads are stubbed."""
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


def _listing(enforcer):
    routes = {route.path: route for route in ALL_API_ROUTES}
    listing = {}
    for path in GATED_ROUTE_PATHS:
        assert path in routes, f"{path} is not a VAMS API route"
        allowed = {
            method for method in routes[path].methods
            if enforcer.enforce({"object__type": "api", "route__path": _probe_path(path)}, method)
        }
        if allowed:
            listing[path] = allowed
    return listing


@pytest.mark.unit
class TestTemplateListingForWebActionRoutes:

    @pytest.mark.parametrize("template_name", sorted(EXPECTED_LISTING))
    def test_the_listing_matches_the_web_test_data(self, template_enforcer, template_name):
        enforcer = template_enforcer(_stored_constraints(template_name))
        assert _listing(enforcer) == EXPECTED_LISTING[template_name]

    def test_the_web_test_data_names_the_routes_it_gates(self):
        """The TypeScript test data lists the same routes and methods as EXPECTED_LISTING."""
        source = (REPO_ROOT / "web" / "src" / "features" / "orchestration" / "permissions"
                  / "templateRoleRoutes.testdata.ts").read_text(encoding="utf-8")
        for const, template_name in (("DATABASE_READONLY_ROUTES", "database-readonly.json"),
                                     ("DATABASE_USER_ROUTES", "database-user.json")):
            block = source.split(f"export const {const}", 1)[1].split("];", 1)[0]
            listed = {
                path: set(re.findall(r'"([A-Z]+)"', methods))
                for path, methods in re.findall(r'route\("([^"]+)", \[([^\]]*)\]\)', block)
            }
            assert listed == EXPECTED_LISTING[template_name], const
