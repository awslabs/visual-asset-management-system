#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The EDH Workstations route surface, stated over method+path pairs.

Three consumers read this set and none tolerates drift: the authorizer resolves a request against it,
the handler dispatches with ``ApiRoute.matches()``, and the CDK parity test compares it to the routes the
add-on stack registers. Stated over pairs so collapsing or splitting constants is invisible here and
dropping a method is not. The routes are declared in the add-on route lane (``common/addonApiRoutes.py``)
and reach the master list through ``ADDON_ROUTES``. The matching cases pin that a ``{sessionId}`` template
takes exactly one segment, which is what keeps ``/workstations/{id}/stop`` from being dispatched as a
workstation read.
"""

import pytest

from backend.backend.common import addonApiRoutes, apiRoutes
from backend.backend.common.apiRoutes import ADDON_ROUTES, DELETE, EDH_ROUTES, GET, POST, PUT

WORKSTATIONS = "/addon/edh/workstations"
WORKSTATION = "/addon/edh/workstations/{sessionId}"
STOP = "/addon/edh/workstations/{sessionId}/stop"
START = "/addon/edh/workstations/{sessionId}/start"
RENEW = "/addon/edh/workstations/{sessionId}/renew"
CONNECT = "/addon/edh/workstations/{sessionId}/connect"
OS_SIGN_IN = "/addon/edh/workstations/{sessionId}/os-sign-in"
IMAGES = "/addon/edh/catalog/images"
INSTANCE_TYPES = "/addon/edh/catalog/instance-types"
LEASE = "/addon/edh/catalog/lease"
CAPACITY_RESERVATIONS = "/addon/edh/catalog/capacity-reservations"

PHASE_1_CONSTANTS = {
    "API_EDH_WORKSTATIONS": (WORKSTATIONS, {GET, POST}),
    "API_EDH_WORKSTATION": (WORKSTATION, {GET, DELETE}),
    "API_EDH_WORKSTATION_STOP": (STOP, {POST}),
    "API_EDH_WORKSTATION_START": (START, {POST}),
    "API_EDH_WORKSTATION_RENEW": (RENEW, {POST}),
    "API_EDH_WORKSTATION_CONNECT": (CONNECT, {GET}),
    "API_EDH_WORKSTATION_OS_SIGN_IN": (OS_SIGN_IN, {GET}),
    "API_EDH_CATALOG_IMAGES": (IMAGES, {GET}),
    "API_EDH_CATALOG_INSTANCE_TYPES": (INSTANCE_TYPES, {GET}),
    "API_EDH_CATALOG_LEASE": (LEASE, {GET}),
    "API_EDH_CATALOG_CAPACITY_RESERVATIONS": (CAPACITY_RESERVATIONS, {GET}),
}

ADMIN_IMAGES = "/addon/edh/admin/images"
ADMIN_IMAGES_VDI = "/addon/edh/admin/images/vdi"
ADMIN_IMAGES_SIMULATION = "/addon/edh/admin/images/simulation"
ADMIN_IMAGE_VDI = "/addon/edh/admin/images/vdi/{imageId}"
ADMIN_IMAGE_SIMULATION = "/addon/edh/admin/images/simulation/{imageId}"
ADMIN_INSTANCES = "/addon/edh/admin/instances"
ADMIN_INSTANCES_STALE = "/addon/edh/admin/instances/stale"
ADMIN_INSTANCE_STOP = "/addon/edh/admin/instances/{sessionId}/stop"
ADMIN_INSTANCE_DEACTIVATE = "/addon/edh/admin/instances/{sessionId}/deactivate"
ADMIN_CAPACITY = "/addon/edh/admin/capacity"
ADMIN_HEALTH = "/addon/edh/admin/health"
ADMIN_SETTINGS = "/addon/edh/admin/settings"
ADMIN_CAPACITY_RESERVATIONS = "/addon/edh/admin/capacity-reservations"
ADMIN_CAPACITY_RESERVATION = "/addon/edh/admin/capacity-reservations/{capacityReservationId}"
ADMIN_AMI_RECIPES = "/addon/edh/admin/ami-recipes"
ADMIN_AMI_RECIPE = "/addon/edh/admin/ami-recipes/{recipeId}"
ADMIN_AMI_RECIPE_BUILDS = "/addon/edh/admin/ami-recipes/{recipeId}/builds"
ADMIN_AMI_RECIPE_BUILD = "/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}"
ADMIN_AMI_RECIPE_BUILD_REGISTER = "/addon/edh/admin/ami-recipes/{recipeId}/builds/{version}/register"

PHASE_2_CONSTANTS = {
    "API_EDH_ADMIN_IMAGES": (ADMIN_IMAGES, {GET}),
    "API_EDH_ADMIN_IMAGES_VDI": (ADMIN_IMAGES_VDI, {POST}),
    "API_EDH_ADMIN_IMAGES_SIMULATION": (ADMIN_IMAGES_SIMULATION, {POST}),
    "API_EDH_ADMIN_IMAGE_VDI": (ADMIN_IMAGE_VDI, {DELETE}),
    "API_EDH_ADMIN_IMAGE_SIMULATION": (ADMIN_IMAGE_SIMULATION, {DELETE}),
    "API_EDH_ADMIN_INSTANCES": (ADMIN_INSTANCES, {GET}),
    "API_EDH_ADMIN_INSTANCES_STALE": (ADMIN_INSTANCES_STALE, {GET}),
    "API_EDH_ADMIN_INSTANCE_STOP": (ADMIN_INSTANCE_STOP, {POST}),
    "API_EDH_ADMIN_INSTANCE_DEACTIVATE": (ADMIN_INSTANCE_DEACTIVATE, {POST}),
    "API_EDH_ADMIN_CAPACITY": (ADMIN_CAPACITY, {GET}),
    "API_EDH_ADMIN_HEALTH": (ADMIN_HEALTH, {GET}),
    "API_EDH_ADMIN_SETTINGS": (ADMIN_SETTINGS, {GET, PUT}),
    "API_EDH_ADMIN_CAPACITY_RESERVATIONS": (ADMIN_CAPACITY_RESERVATIONS, {GET, POST}),
    "API_EDH_ADMIN_CAPACITY_RESERVATION": (ADMIN_CAPACITY_RESERVATION, {PUT, DELETE}),
    "API_EDH_ADMIN_AMI_RECIPES": (ADMIN_AMI_RECIPES, {GET, POST}),
    "API_EDH_ADMIN_AMI_RECIPE": (ADMIN_AMI_RECIPE, {GET, PUT, DELETE}),
    "API_EDH_ADMIN_AMI_RECIPE_BUILDS": (ADMIN_AMI_RECIPE_BUILDS, {GET, POST}),
    "API_EDH_ADMIN_AMI_RECIPE_BUILD": (ADMIN_AMI_RECIPE_BUILD, {DELETE}),
    "API_EDH_ADMIN_AMI_RECIPE_BUILD_REGISTER": (ADMIN_AMI_RECIPE_BUILD_REGISTER, {POST}),
}

CONTAINERS_CATALOG = "/addon/edh/containers/catalog"
CONTAINERS_JOBS = "/addon/edh/containers/jobs"
CONTAINERS_JOB = "/addon/edh/containers/jobs/{jobId}"

CONTAINER_CONSTANTS = {
    "API_EDH_CONTAINERS_CATALOG": (CONTAINERS_CATALOG, {GET}),
    "API_EDH_CONTAINERS_JOBS": (CONTAINERS_JOBS, {GET, POST}),
    "API_EDH_CONTAINERS_JOB": (CONTAINERS_JOB, {DELETE}),
}

ALL_EDH_CONSTANTS = {**PHASE_1_CONSTANTS, **PHASE_2_CONSTANTS, **CONTAINER_CONSTANTS}


def _pairs(routes):
    return {(method, route.path) for route in routes for method in route.methods}


@pytest.mark.unit
class TestEdhRouteSurface:
    def test_the_group_offers_exactly_the_declared_pairs(self):
        expected = set()
        for path, methods in ALL_EDH_CONSTANTS.values():
            expected.update((method, path) for method in methods)
        assert _pairs(EDH_ROUTES) == expected

    @pytest.mark.parametrize("constant_name", sorted(ALL_EDH_CONSTANTS))
    def test_each_constant_is_declared_grouped_and_categorised(self, constant_name):
        # getattr rather than a module-level import: a missing constant must fail this case alone.
        route = getattr(apiRoutes, constant_name, None)
        assert route is not None, f"{constant_name} is not declared"
        path, methods = ALL_EDH_CONSTANTS[constant_name]
        assert route.path == path
        assert set(route.methods) == methods
        assert route.category == "addons"
        assert route.internal is False
        assert route.unauthenticated is False
        # A constant absent from its group is excluded from get_public_api_routes() while still
        # reading as present in the file.
        assert route in EDH_ROUTES
        assert route in apiRoutes.ALL_API_ROUTES
        assert route in apiRoutes.get_public_api_routes()

    def test_every_edh_route_lives_under_the_addon_prefix(self):
        assert all(route.path.startswith("/addon/edh/") for route in EDH_ROUTES)
        assert EDH_ROUTES, "the group must not be empty"

    def test_the_group_is_folded_into_the_master_list_once(self):
        assert sum(1 for route in apiRoutes.ALL_API_ROUTES if route in EDH_ROUTES) == len(EDH_ROUTES)

    def test_the_windows_only_sign_in_route_is_gone(self):
        # The desktop sign-in serves every OS family under one route; the Windows-only one is not declared.
        assert not hasattr(addonApiRoutes, "API_EDH_WORKSTATION_WINDOWS_SIGN_IN")
        assert not hasattr(apiRoutes, "API_EDH_WORKSTATION_WINDOWS_SIGN_IN")
        assert [route.path for route in apiRoutes.ALL_API_ROUTES if "windows-sign-in" in route.path] == []

    def test_the_group_is_part_of_the_add_on_routes(self):
        # ALL_API_ROUTES takes the add-on routes only through ADDON_ROUTES.
        assert set(EDH_ROUTES) <= set(ADDON_ROUTES)
        assert sum(1 for route in ADDON_ROUTES if route in EDH_ROUTES) == len(EDH_ROUTES)

    @pytest.mark.parametrize("constant_name", sorted(ALL_EDH_CONSTANTS) + ["EDH_ROUTES"])
    def test_each_name_is_declared_in_the_add_on_lane_and_re_exported(self, constant_name):
        # The add-on lane file declares the name; apiRoutes re-exports it for the handlers' imports.
        assert getattr(addonApiRoutes, constant_name, None) is not None, constant_name
        assert getattr(apiRoutes, constant_name) == getattr(addonApiRoutes, constant_name)


@pytest.mark.unit
class TestEdhRouteMatching:
    """Handler dispatch and the authorizer both go through matches()."""

    @pytest.mark.parametrize("path,expected", [
        ("/addon/edh/workstations", True),
        ("/addon/edh/workstations/", False),
        ("/addon/edh/workstations/3f2a", False),
    ])
    def test_the_collection_route_matches_only_itself(self, path, expected):
        assert apiRoutes.API_EDH_WORKSTATIONS.matches(path) is expected

    @pytest.mark.parametrize("path,expected", [
        ("/addon/edh/workstations/3f2a", True),
        ("/addon/edh/workstations/3f2a-9c1e-4b7d", True),
        ("/addon/edh/workstations", False),
        ("/addon/edh/workstations/3f2a/stop", False),
        ("/addon/edh/workstations/3f2a/start", False),
        ("/addon/edh/workstations/3f2a/connect", False),
        ("/addon/edh/workstations/3f2a/os-sign-in", False),
        ("/addon/edh/workstations/3f2a/", False),
    ])
    def test_the_by_id_route_matches_one_session_segment(self, path, expected):
        assert apiRoutes.API_EDH_WORKSTATION.matches(path) is expected

    @pytest.mark.parametrize("route_name,path", [
        ("API_EDH_WORKSTATION_STOP", "/addon/edh/workstations/3f2a/stop"),
        ("API_EDH_WORKSTATION_START", "/addon/edh/workstations/3f2a/start"),
        ("API_EDH_WORKSTATION_CONNECT", "/addon/edh/workstations/3f2a/connect"),
        ("API_EDH_WORKSTATION_OS_SIGN_IN", "/addon/edh/workstations/3f2a/os-sign-in"),
        ("API_EDH_CATALOG_IMAGES", "/addon/edh/catalog/images"),
        ("API_EDH_CATALOG_INSTANCE_TYPES", "/addon/edh/catalog/instance-types"),
        ("API_EDH_CATALOG_LEASE", "/addon/edh/catalog/lease"),
        ("API_EDH_WORKSTATION_RENEW", "/addon/edh/workstations/3f2a/renew"),
        ("API_EDH_ADMIN_SETTINGS", "/addon/edh/admin/settings"),
        ("API_EDH_CATALOG_CAPACITY_RESERVATIONS", "/addon/edh/catalog/capacity-reservations"),
        ("API_EDH_ADMIN_CAPACITY", "/addon/edh/admin/capacity"),
        ("API_EDH_ADMIN_CAPACITY_RESERVATIONS", "/addon/edh/admin/capacity-reservations"),
        ("API_EDH_ADMIN_CAPACITY_RESERVATION", "/addon/edh/admin/capacity-reservations/cr-0123456789abcdef0"),
    ])
    def test_each_action_path_is_claimed_by_exactly_one_edh_route(self, route_name, path):
        claimants = [route for route in EDH_ROUTES if route.matches(path)]
        assert claimants == [getattr(apiRoutes, route_name)]

    def test_the_action_routes_do_not_swallow_a_nested_segment(self):
        assert apiRoutes.API_EDH_WORKSTATION_STOP.matches("/addon/edh/workstations/3f2a/stop/now") is False
        assert apiRoutes.API_EDH_CATALOG_IMAGES.matches("/addon/edh/catalog/images/abc") is False
        assert apiRoutes.API_EDH_ADMIN_CAPACITY_RESERVATION.matches(
            "/addon/edh/admin/capacity-reservations/cr-0123456789abcdef0/extra") is False
