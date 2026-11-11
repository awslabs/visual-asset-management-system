# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The lease policy as the catalog offers it to a launch: whether leases apply, the lease hours,
whether they are forced, and the grace window. The policy's audit fields stay on the admin route."""

from unittest.mock import MagicMock

import pytest

import backend.backend.handlers.addon.edh.edhWorkstationService as service
from backend.backend.common.apiRoutes import ALL_API_ROUTES, API_EDH_CATALOG_LEASE, EDH_ROUTES, GET
from backend.tests.handlers.addon.edh.edhServiceTestSupport import install_service_fakes, make_event, response_body

LEASE_PATH = "/addon/edh/catalog/lease"
POLICY = {"enabled": True, "leaseHours": 12, "forced": True, "graceHours": 48,
          "updatedAt": "2026-09-08T10:00:00+00:00", "updatedBy": "admin"}


def _read(monkeypatch, policy=POLICY, rest=False):
    fakes = install_service_fakes(monkeypatch)
    monkeypatch.setattr(service, "read_lease_policy", lambda: dict(policy))
    return service.lambda_handler(make_event("GET", LEASE_PATH, rest=rest), MagicMock()), fakes


@pytest.mark.unit
class TestCatalogLease:

    def test_the_catalog_offers_only_what_a_launch_needs_of_the_policy(self, monkeypatch):
        response, fakes = _read(monkeypatch)

        assert response["statusCode"] == 200
        assert response_body(response) == {
            "lease": {"enabled": True, "leaseHours": 12, "forced": True, "graceHours": 48}
        }
        fakes.client_factory.assert_not_called()
        fakes.table.query.assert_not_called()

    def test_a_disabled_policy_reads_as_disabled(self, monkeypatch):
        response, _ = _read(monkeypatch, policy={**POLICY, "enabled": False}, rest=True)

        assert response["statusCode"] == 200
        assert response_body(response)["lease"] == {
            "enabled": False, "leaseHours": 12, "forced": True, "graceHours": 48
        }

    def test_the_route_sits_on_the_catalog_prefix_and_is_the_sole_claimant(self):
        assert API_EDH_CATALOG_LEASE.path == LEASE_PATH
        assert API_EDH_CATALOG_LEASE.methods == (GET,)
        assert API_EDH_CATALOG_LEASE in EDH_ROUTES
        assert [route for route in ALL_API_ROUTES if route.matches(LEASE_PATH)] == [API_EDH_CATALOG_LEASE]
        assert API_EDH_CATALOG_LEASE.matches("/addon/edh/catalog/lease/x") is False
