#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""One ApiRoute constant per path, and the asset-link surface that invariant is asserted against.

``GET /auth/routes/api`` emits one object per entry in ``get_public_api_routes()``, so two constants
sharing a path emit two objects for the same path carrying disjoint methods. Every consumer today
survives that -- the web constraint editor builds its options with a list map and the CLI groups by
category with a list append -- but a consumer that folds the listing into a path -> methods map, the
natural shape, keeps one of the two objects and silently loses the other's methods. Every other
multi-method path in the registry collapses its methods into ONE constant, so the uniqueness
invariant is the registry's existing convention rather than a new rule.

Collapsing constants is only correct if it changes no method+path pair, and that set is load-bearing
in three places: the authorizer resolves a request against it, handlers dispatch on
``ApiRoute.matches()``, and ``infra/test/api/apiRouteBackendCdkParity.test.ts`` compares it to the
routes registered with API Gateway. The asset-link tests below pin that set, so a collapse that drops
a method fails here rather than at the API Gateway seam.
"""
from collections import Counter

import pytest

from backend.backend.common import apiRoutes
from backend.backend.common.apiRoutes import DELETE, GET, POST, PUT

ASSET_LINK_BY_ID_PATH = "/asset-links/{assetLinkId}"


def _duplicate_paths(routes):
    """Paths declared by more than one route in ``routes``."""
    return sorted(path for path, count in Counter(r.path for r in routes).items() if count > 1)


def _pairs(routes):
    return {(method, route.path) for route in routes for method in route.methods}


def _methods_for(path):
    """Every method offered on ``path``, however many constants declare it."""
    return {method for route in apiRoutes.ALL_API_ROUTES if route.path == path
            for method in route.methods}


@pytest.mark.unit
class TestOneConstantPerPath:
    """No path is declared twice, in the master list or in the public listing."""

    def test_no_path_is_declared_by_two_constants(self):
        duplicates = _duplicate_paths(apiRoutes.ALL_API_ROUTES)
        assert not duplicates, (
            f"paths declared by more than one ApiRoute constant: {duplicates} -- collapse the "
            f"methods into one constant, as every other multi-method path does")

    def test_the_public_listing_offers_each_path_once(self):
        # The response shape of GET /auth/routes/api: one object per public route, keyed on path by
        # any consumer that folds it into a map.
        public = apiRoutes.get_public_api_routes()
        paths = [route.path for route in public]
        assert len(paths) == len(set(paths)), f"duplicated in the listing: {_duplicate_paths(public)}"

    def test_the_duplicate_check_can_detect_a_duplicate(self):
        # Positive control: without it, a check over a mis-derived (or empty) set of paths would
        # report no duplicates no matter what the registry contains.
        split = (
            apiRoutes.ApiRoute(ASSET_LINK_BY_ID_PATH, (PUT,), "assetLinks"),
            apiRoutes.ApiRoute(ASSET_LINK_BY_ID_PATH, (DELETE,), "assetLinks"),
        )
        assert _duplicate_paths(split) == [ASSET_LINK_BY_ID_PATH]


@pytest.mark.unit
class TestAssetLinkRouteSurface:
    """The asset-link surface, which the single by-id constant must reproduce exactly."""

    def test_the_by_id_path_offers_both_update_and_delete(self):
        assert _methods_for(ASSET_LINK_BY_ID_PATH) == {PUT, DELETE}

    def test_the_asset_link_group_offers_exactly_its_five_method_path_pairs(self):
        # Stated over pairs rather than constants, so collapsing two constants into one is
        # indistinguishable here from leaving them split -- and dropping a method is not.
        assert _pairs(apiRoutes.ASSET_LINK_ROUTES) == {
            (POST, "/asset-links"),
            (GET, "/asset-links/single/{assetLinkId}"),
            (PUT, ASSET_LINK_BY_ID_PATH),
            (DELETE, ASSET_LINK_BY_ID_PATH),
            (GET, "/database/{databaseId}/assets/{assetId}/asset-links"),
        }

    @pytest.mark.parametrize("path,expected", [
        ("/asset-links/2e1f0a34", True),
        ("/asset-links", False),
        ("/asset-links/single/2e1f0a34", False),
        ("/asset-links/2e1f0a34/metadata", False),
        ("/asset-links/2e1f0a34/", False),
    ])
    def test_the_by_id_route_matches_only_a_single_link_id_segment(self, path, expected):
        # Handler dispatch and the authorizer both go through matches(), so the template has to keep
        # matching one segment and nothing adjacent.
        route = next(r for r in apiRoutes.ALL_API_ROUTES if r.path == ASSET_LINK_BY_ID_PATH)
        assert route.matches(path) is expected

    def test_the_collapsed_constant_is_declared_and_grouped(self):
        # getattr rather than a module-level import: a missing constant must fail this test alone,
        # not the whole file at collection.
        route = getattr(apiRoutes, "API_ASSET_LINKS_BY_ID", None)
        assert route is not None, "API_ASSET_LINKS_BY_ID is not declared"
        assert route.path == ASSET_LINK_BY_ID_PATH
        assert set(route.methods) == {PUT, DELETE}
        assert route.category == "assetLinks"
        # A constant absent from its category group is excluded from get_public_api_routes() while
        # still reading as present in the file.
        assert route in apiRoutes.ASSET_LINK_ROUTES
        assert route in apiRoutes.ALL_API_ROUTES
