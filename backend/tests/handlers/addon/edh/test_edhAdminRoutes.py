# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The admin route templates: the DELETE templates carry the kind segment before the id, so they sit
one segment deeper than the register literals and a dispatcher may test the routes in any order;
/instances/stale is a literal no template can shadow. Their paths, methods, grouping and category are
asserted once, over every EDH constant, in test_edh_api_routes.py.
"""

import pytest

from backend.backend.common import apiRoutes


@pytest.mark.unit
class TestEdhAdminRouteConstants:

    @pytest.mark.parametrize(
        "literal",
        ["/addon/edh/admin/images/vdi", "/addon/edh/admin/images/simulation"],
    )
    def test_the_delete_templates_do_not_match_the_register_literals(self, literal):
        assert not apiRoutes.API_EDH_ADMIN_IMAGE_VDI.matches(literal)
        assert not apiRoutes.API_EDH_ADMIN_IMAGE_SIMULATION.matches(literal)

    def test_the_delete_templates_match_one_concrete_id_segment(self):
        assert apiRoutes.API_EDH_ADMIN_IMAGE_VDI.matches("/addon/edh/admin/images/vdi/7")
        assert apiRoutes.API_EDH_ADMIN_IMAGE_SIMULATION.matches(
            "/addon/edh/admin/images/simulation/win-2022"
        )
        assert not apiRoutes.API_EDH_ADMIN_IMAGE_VDI.matches("/addon/edh/admin/images/vdi/7/extra")
        assert not apiRoutes.API_EDH_ADMIN_IMAGE_VDI.matches("/addon/edh/admin/images/vdi/")

    def test_stale_is_claimed_by_its_own_literal_only(self):
        # /instances/stale is a literal; the templated instance routes carry a trailing action
        # segment, so the literal cannot be shadowed whatever order a dispatcher tests them in.
        claimants = [route for route in apiRoutes.EDH_ROUTES
                     if route.matches("/addon/edh/admin/instances/stale")]
        assert claimants == [apiRoutes.API_EDH_ADMIN_INSTANCES_STALE]

    @pytest.mark.parametrize("route_name,path", [
        ("API_EDH_ADMIN_INSTANCE_STOP", "/addon/edh/admin/instances/9e8d/stop"),
        ("API_EDH_ADMIN_INSTANCE_DEACTIVATE", "/addon/edh/admin/instances/9e8d/deactivate"),
        ("API_EDH_ADMIN_CAPACITY", "/addon/edh/admin/capacity"),
        ("API_EDH_ADMIN_HEALTH", "/addon/edh/admin/health"),
        ("API_EDH_ADMIN_IMAGES", "/addon/edh/admin/images"),
        ("API_EDH_ADMIN_INSTANCES", "/addon/edh/admin/instances"),
    ])
    def test_each_admin_path_is_claimed_by_exactly_one_edh_route(self, route_name, path):
        claimants = [route for route in apiRoutes.EDH_ROUTES if route.matches(path)]
        assert claimants == [getattr(apiRoutes, route_name)]
