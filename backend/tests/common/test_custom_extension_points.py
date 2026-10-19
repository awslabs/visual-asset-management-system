#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The fork-owned backend lists are spliced in and cannot shadow a core entry.

``custom/customApiRoutes.py`` and ``custom/customResourceNames.py`` ship empty and are filled in by a
fork, so every assertion here holds for any content rather than for the shipped (empty) content:
the custom routes reach ``ALL_API_ROUTES`` (and so the ``GET /auth/routes/api`` listing), and
neither a custom route nor a custom resource key duplicates a core one. A duplicate route would be
dispatched to whichever handler matched first; a duplicate key would fail the deploy with
"parameter already exists". Each check carries a control proving it can fail.
"""

import pytest

from backend.backend.common import apiRoutes
from backend.backend.common.addonResourceNames import AddonResourceKeys
from common.resourceNames import ResourceKeys
from custom.customResourceNames import CustomResourceKeys


def _pairs(routes):
    return {(method, route.path) for route in routes for method in route.methods}


def _duplicated_pairs(custom_routes, other_routes):
    return sorted(_pairs(custom_routes) & _pairs(other_routes))


def _param_keys(keys_class):
    """``param_key`` of every ResourceParamKey attribute declared on ``keys_class``."""
    return {
        value.param_key
        for name, value in vars(keys_class).items()
        if not name.startswith("_") and hasattr(value, "param_key")
    }


@pytest.mark.unit
class TestCustomRoutes:
    def test_custom_routes_is_a_tuple_of_api_routes(self):
        assert isinstance(apiRoutes.CUSTOM_ROUTES, tuple)
        for route in apiRoutes.CUSTOM_ROUTES:
            assert isinstance(route, tuple) and hasattr(route, "matches"), route

    def test_every_custom_route_is_in_the_master_list(self):
        missing = [route for route in apiRoutes.CUSTOM_ROUTES if route not in apiRoutes.ALL_API_ROUTES]
        assert not missing, f"custom routes not spliced into ALL_API_ROUTES: {missing}"

    def test_no_custom_route_duplicates_a_core_or_addon_route(self):
        others = [route for route in apiRoutes.ALL_API_ROUTES if route not in apiRoutes.CUSTOM_ROUTES]
        duplicated = _duplicated_pairs(apiRoutes.CUSTOM_ROUTES, others)
        assert not duplicated, f"custom routes repeat a core method+path: {duplicated}"

    def test_the_duplicate_check_detects_a_duplicate(self):
        # Control: a custom route on a core method+path must be reported.
        shadow = (apiRoutes.ApiRoute("/database", (apiRoutes.GET,), "custom"),)
        assert _duplicated_pairs(shadow, apiRoutes.DATABASE_ROUTES) == [("GET", "/database")]


@pytest.mark.unit
class TestCustomResourceKeys:
    def test_every_custom_key_is_a_resource_param_key(self):
        for name, value in vars(CustomResourceKeys).items():
            if name.startswith("_"):
                continue
            assert hasattr(value, "param_key") and hasattr(value, "env_var_names"), name

    def test_no_custom_key_duplicates_a_core_key(self):
        duplicated = sorted(_param_keys(CustomResourceKeys) & _param_keys(ResourceKeys))
        assert not duplicated, f"custom resource keys repeat a core key: {duplicated}"

    def test_no_custom_key_duplicates_an_addon_key(self):
        duplicated = sorted(_param_keys(CustomResourceKeys) & _param_keys(AddonResourceKeys))
        assert not duplicated, f"custom resource keys repeat an add-on key: {duplicated}"

    def test_the_core_key_set_is_read(self):
        # Control: an empty core set would make the duplicate check above pass vacuously.
        assert "dynamoTables/assetStorage" in _param_keys(ResourceKeys)


@pytest.mark.unit
class TestAddonResourceKeys:
    """``common/addonResourceNames.py`` is the add-on lane: upstream-owned, shipped empty today."""

    def test_every_addon_key_is_a_resource_param_key(self):
        for name, value in vars(AddonResourceKeys).items():
            if name.startswith("_"):
                continue
            assert hasattr(value, "param_key") and hasattr(value, "env_var_names"), name

    def test_no_addon_key_duplicates_a_core_key(self):
        duplicated = sorted(_param_keys(AddonResourceKeys) & _param_keys(ResourceKeys))
        assert not duplicated, f"add-on resource keys repeat a core key: {duplicated}"

    def test_the_duplicate_check_detects_a_duplicate(self):
        # Control: a lane class declaring a core key is reported.
        from common.resourceNames import ResourceParamKey

        class _Shadow:
            PROBE = ResourceParamKey("dynamoTables/assetStorage", ("PROBE",))

        assert _param_keys(_Shadow) & _param_keys(ResourceKeys) == {"dynamoTables/assetStorage"}
