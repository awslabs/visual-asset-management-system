# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A read-only role can request an asset export, and still nothing it may not.

``POST /database/{databaseId}/assets/{assetId}/export`` (``API_ASSET_EXPORT``) lists an asset tree with
its metadata and files and can sign download links for them. It is a POST because its body carries the
export options; it changes no asset, file or metadata. The handler checks Tier 1 with ``enforceAPI`` for
the POST and Tier 2 with a GET on each asset it includes, so a read-only role whose ``api`` constraints
did not reach the route received ``403`` whatever its asset grants were.

The two shipped read-only templates (run through the import's own substitution and conversion) and the
seeded ``basicReadOnly`` role (read from its CDK construct the way the deploy writes it) are each
evaluated by the real policy generation and Casbin enforcer, with only the two table reads stubbed.
The controls show the grant is the export constraint alone, and that it reaches no write route.

The download and export grants each match their one route by pattern, whatever the path parameters
are. ``TestNoOtherWriteRouteIsReached`` probes every POST, PUT and DELETE route with each path
parameter filled with a dummy value and with each segment those grants name, because a grant that
matched on a path suffix also reached a write route whose last parameter was set to that segment.
"""

import itertools
import re
from unittest.mock import MagicMock, patch

import pytest

import backend.backend.handlers.authz as authz_module  # noqa: E402
from backend.backend.common.apiRoutes import (  # noqa: E402
    ALL_API_ROUTES,
    API_ASSET_EXPORT,
    API_AUTH_LOGIN_PROFILE,
    API_AUTH_ROUTES,
    API_CHECK_SUBSCRIPTION,
    API_DOWNLOAD_ASSET,
    API_REVERT_ASSET_VERSION,
    API_REVERT_FILE_VERSION,
    API_SEARCH,
    API_SEARCH_NLP,
    API_SEARCH_SIMPLE,
    DELETE,
    POST,
    PUT,
)
from backend.tests.handlers.authz import shipped_constraints  # noqa: E402

ROLE_NAME = "template-role"
SEEDED_ROLE = "basicReadOnly"
USER_ID = "template-user"
DATABASE_ID = "template-db"
VARIABLE_VALUES = {"DATABASE_ID": DATABASE_ID, "TAG_VALUE": "locked"}

READ_ONLY_TEMPLATES = ("database-readonly.json", "global-readonly.json")
TEMPLATE_EXPORT_CONSTRAINT = f"{ROLE_NAME}-api-routes-export"
SEEDED_EXPORT_CONSTRAINT = "initial_basicro_allow_export_api"


def _template(template_name, drop_export=False):
    constraints = shipped_constraints.template_constraints(template_name, ROLE_NAME, VARIABLE_VALUES)
    if drop_export:
        constraints = [c for c in constraints if c["name"] != TEMPLATE_EXPORT_CONSTRAINT]
    return constraints, ROLE_NAME


def _seeded(drop_export=False):
    constraints = shipped_constraints.seeded_constraints(SEEDED_ROLE)
    if drop_export:
        constraints = [c for c in constraints if c["constraintId"] != SEEDED_EXPORT_CONSTRAINT]
    return constraints, SEEDED_ROLE


# Every read-only constraint set VAMS ships, by a readable name.
READ_ONLY_SETS = {f"template {name}": (lambda name=name, **kw: _template(name, **kw))
                  for name in READ_ONLY_TEMPLATES}
READ_ONLY_SETS["seeded basicReadOnly"] = _seeded


def _api_request(path):
    """The Tier-1 object ``enforceAPI`` evaluates for a request path."""
    return {"object__type": "api", "route__path": path}


def _concrete_path(route_path):
    """The route template with each ``{parameter}`` segment filled, as a request carries it."""
    return re.sub(r"\{[^/{}]+\}", "probe-id", route_path)


def _export_path():
    return _concrete_path(API_ASSET_EXPORT.path.replace("{databaseId}", DATABASE_ID))


@pytest.fixture
def enforcer_for():
    """Build the real CasbinEnforcer for a (constraints, role) pair.

    The per-user policy and enforcer caches are cleared on every build so each policy is generated from
    the constraints passed in. The two table reads are the only stubs, and the denial audit writer is
    replaced so a denied check writes nothing. Both caches are restored afterwards.
    """
    state = {"constraints": [], "role": ROLE_NAME}
    previous_policy_map = authz_module.casbin_user_policy_map
    previous_enforcer_map = authz_module.casbin_user_enforcer_map

    def build(constraint_set):
        state["constraints"], state["role"] = constraint_set
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}
        return authz_module.CasbinEnforcer(
            {"tokens": [USER_ID], "roles": [state["role"]], "mfaEnabled": True})

    with patch.object(
        authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
        side_effect=lambda *args, **kwargs: [{"userId": USER_ID, "roleName": state["role"]}],
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
class TestReadOnlyRolesMayExport:

    @pytest.mark.parametrize("name", sorted(READ_ONLY_SETS))
    def test_a_read_only_role_may_request_an_asset_export(self, enforcer_for, name):
        enforcer = enforcer_for(READ_ONLY_SETS[name]())
        assert enforcer.enforce(_api_request(_export_path()), POST) is True

    @pytest.mark.parametrize("name", sorted(READ_ONLY_SETS))
    def test_a_read_only_role_reads_the_asset_it_exports(self, enforcer_for, name):
        """Tier 2 of the export is a GET on each asset, which the read-only asset constraint allows."""
        enforcer = enforcer_for(READ_ONLY_SETS[name]())
        asset = {"object__type": "asset", "databaseId": DATABASE_ID, "assetName": "probe",
                 "assetType": ".glb", "tags": []}
        assert enforcer.enforce(asset, "GET") is True


@pytest.mark.unit
class TestTheExportGrantIsNarrow:

    @pytest.mark.parametrize("name", sorted(READ_ONLY_SETS))
    def test_without_the_export_constraint_the_route_is_denied(self, enforcer_for, name):
        enforcer = enforcer_for(READ_ONLY_SETS[name](drop_export=True))
        assert enforcer.enforce(_api_request(_export_path()), POST) is False

    @pytest.mark.parametrize("name", sorted(READ_ONLY_SETS))
    def test_the_export_constraint_does_not_reach_a_sibling_route(self, enforcer_for, name):
        enforcer = enforcer_for(READ_ONLY_SETS[name]())
        for suffix in ("exports", "export/extra", "createFolder", "copyFile", "unarchiveAsset"):
            path = f"/database/{DATABASE_ID}/assets/probe-id/{suffix}"
            assert enforcer.enforce(_api_request(path), POST) is False, path
        # Only the database-scoped route: an /export suffix outside /database/ is not granted.
        assert enforcer.enforce(_api_request("/assets/export"), POST) is False

    @pytest.mark.parametrize("name", sorted(READ_ONLY_SETS))
    def test_the_export_constraint_grants_no_write_method(self, enforcer_for, name):
        enforcer = enforcer_for(READ_ONLY_SETS[name]())
        for method in (PUT, DELETE):
            assert enforcer.enforce(_api_request(_export_path()), method) is False, method

    @pytest.mark.parametrize("name", sorted(READ_ONLY_SETS))
    def test_no_other_post_route_is_opened_by_the_export_constraint(self, enforcer_for, name):
        """The POST routes a read-only role reaches differ, with and without the export constraint,
        by the export route alone."""
        def allowed_post_routes(constraint_set):
            enforcer = enforcer_for(constraint_set)
            return {route for route in ALL_API_ROUTES
                    if POST in route.methods
                    and enforcer.enforce(_api_request(_concrete_path(route.path)), POST)}

        with_export = allowed_post_routes(READ_ONLY_SETS[name]())
        without_export = allowed_post_routes(READ_ONLY_SETS[name](drop_export=True))
        assert with_export - without_export == {API_ASSET_EXPORT}
        assert without_export <= with_export



# The path segments the read-only POST grants name. A path parameter set to one of them is the shape
# a suffix-matching grant would mistake for the granted route.
GRANTED_SEGMENTS = ("export", "download")
DUMMY_SEGMENT = "probe-id"
WRITE_METHODS = (POST, PUT, DELETE)
_PARAMETER = re.compile(r"\{[^/{}]+\}")


def _fillings(route_path):
    """Every concrete path of the route with each parameter set to the dummy value or to one of the
    granted segments, independently."""
    parameters = _PARAMETER.findall(route_path)
    choices = (DUMMY_SEGMENT,) + GRANTED_SEGMENTS
    for values in itertools.product(choices, repeat=len(parameters)):
        path = route_path
        for value in values:
            path = _PARAMETER.sub(value, path, count=1)
        yield path


# The write-method routes a read-only role is meant to reach: POST reads that carry a body (the
# natural-language search included), and, for the seeded role only, management of the caller's own API keys.
_TEMPLATE_ALLOWED = {(POST, route) for route in (
    API_AUTH_ROUTES, API_AUTH_LOGIN_PROFILE, API_SEARCH, API_SEARCH_SIMPLE, API_SEARCH_NLP,
    API_CHECK_SUBSCRIPTION, API_DOWNLOAD_ASSET, API_ASSET_EXPORT)}
EXPECTED_WRITE_METHOD_ROUTES = {
    "template database-readonly.json": _TEMPLATE_ALLOWED,
    "template global-readonly.json": _TEMPLATE_ALLOWED,
    "seeded basicReadOnly": _TEMPLATE_ALLOWED | {
        (method, route) for route in ALL_API_ROUTES if route.path.startswith("/auth/user/api-keys")
        for method in WRITE_METHODS if method in route.methods},
}


@pytest.mark.unit
class TestNoOtherWriteRouteIsReached:
    """Every POST, PUT and DELETE route in the core and add-on route lists, with each path parameter
    filled with a dummy value and with each segment the read-only grants name, is denied unless it
    is one of the routes the role is meant to reach."""

    @pytest.mark.parametrize("name", sorted(READ_ONLY_SETS))
    def test_a_read_only_role_is_denied_every_other_write_method_route(self, enforcer_for, name):
        enforcer = enforcer_for(READ_ONLY_SETS[name]())
        expected = EXPECTED_WRITE_METHOD_ROUTES[name]
        reached = []
        for route in ALL_API_ROUTES:
            for method in WRITE_METHODS:
                if method not in route.methods:
                    continue
                for path in _fillings(route.path):
                    if not enforcer.enforce(_api_request(path), method):
                        continue
                    # The concrete path may belong to an expected route as well as to this one.
                    if not any(m == method and r.matches(path) for m, r in expected):
                        reached.append(f"{method} {path} ({route.path})")
        assert not reached, f"{name} reaches routes it must not: {reached}"

    @pytest.mark.parametrize("name", sorted(READ_ONLY_SETS))
    def test_a_read_only_role_reaches_each_expected_route(self, enforcer_for, name):
        """Control for the test above: the expected routes are reached, so the denials are not the
        enforcer denying everything."""
        enforcer = enforcer_for(READ_ONLY_SETS[name]())
        for method, route in EXPECTED_WRITE_METHOD_ROUTES[name]:
            path = _concrete_path(route.path)
            assert enforcer.enforce(_api_request(path), method) is True, f"{method} {path}"

    @pytest.mark.parametrize("name", sorted(READ_ONLY_SETS))
    def test_the_revert_routes_are_denied_with_a_granted_segment_as_their_version(self, enforcer_for, name):
        """The two POST routes under an asset whose last segment is a parameter."""
        enforcer = enforcer_for(READ_ONLY_SETS[name]())
        for segment in GRANTED_SEGMENTS:
            for route in (API_REVERT_ASSET_VERSION, API_REVERT_FILE_VERSION):
                path = route.path.replace("{databaseId}", DATABASE_ID)
                path = _PARAMETER.sub("probe-id", path, count=1)
                path = _PARAMETER.sub(segment, path, count=1)
                assert enforcer.enforce(_api_request(path), POST) is False, path

    def test_the_fillings_cover_the_trailing_parameter_routes(self):
        """Control for the enumeration: a route ending in a parameter is probed with that parameter
        set to each granted segment."""
        paths = set(_fillings(API_REVERT_ASSET_VERSION.path))
        assert any(path.endswith("/revertAssetVersion/export") for path in paths)
        assert any(path.endswith("/revertAssetVersion/download") for path in paths)
