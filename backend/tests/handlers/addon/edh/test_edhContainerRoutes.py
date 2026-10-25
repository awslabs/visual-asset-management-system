# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The container route templates: {jobId} matches exactly one segment, and the collection route never
swallows the catalog literal. Their paths, methods, grouping and category are asserted once, over every
EDH constant, in test_edh_api_routes.py."""

import pytest

from backend.backend.common import apiRoutes


@pytest.mark.unit
class TestEdhContainerRouteConstants:

    def test_the_job_template_matches_one_id_segment(self):
        assert apiRoutes.API_EDH_CONTAINERS_JOB.matches("/addon/edh/containers/jobs/j-1")
        assert apiRoutes.API_EDH_CONTAINERS_JOB.matches(
            "/addon/edh/containers/jobs/3f2a9c1e-4b7d-4e0a-9d2b-6c1f8a7e5b30")
        assert not apiRoutes.API_EDH_CONTAINERS_JOB.matches("/addon/edh/containers/jobs")
        assert not apiRoutes.API_EDH_CONTAINERS_JOB.matches("/addon/edh/containers/jobs/j-1/extra")
        assert not apiRoutes.API_EDH_CONTAINERS_JOB.matches("/addon/edh/containers/jobs/")

    @pytest.mark.parametrize("route_name,path", [
        ("API_EDH_CONTAINERS_CATALOG", "/addon/edh/containers/catalog"),
        ("API_EDH_CONTAINERS_JOBS", "/addon/edh/containers/jobs"),
        ("API_EDH_CONTAINERS_JOB", "/addon/edh/containers/jobs/j-1"),
    ])
    def test_each_container_path_is_claimed_by_exactly_one_edh_route(self, route_name, path):
        claimants = [route for route in apiRoutes.EDH_ROUTES if route.matches(path)]
        assert claimants == [getattr(apiRoutes, route_name)]

    def test_the_collection_route_does_not_swallow_the_catalog_literal(self):
        assert not apiRoutes.API_EDH_CONTAINERS_JOBS.matches("/addon/edh/containers/catalog")
        assert not apiRoutes.API_EDH_CONTAINERS_JOB.matches("/addon/edh/containers/catalog")
