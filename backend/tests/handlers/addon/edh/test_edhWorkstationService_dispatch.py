# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tier-1 and dispatch of edhWorkstationService.

Denials are asserted together with what was NOT consulted (no Casbin construction on an empty
token list, no EDH client on a denied request), because a MagicMock's ``enforce`` answers truthy and
a verdict-only assertion can pass for the wrong reason.
"""

import pytest

from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    NO_IDENTITY_CLAIMS,
    SYSTEM_CLAIMS,
    WORKSTATIONS_PATH,
    install_service_fakes,
    make_event,
    response_body,
)


@pytest.mark.unit
class TestTierOne:
    def test_a_tier1_denial_is_403_and_reaches_no_edh_client(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        fakes.enforcer.enforceAPI.return_value = False

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert response["statusCode"] == 403
        fakes.enforcer.enforceAPI.assert_called_once()
        fakes.client_factory.assert_not_called()
        fakes.table.query.assert_not_called()

    def test_an_empty_token_list_is_denied_before_casbin_is_consulted(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, claims=NO_IDENTITY_CLAIMS)

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert response["statusCode"] == 403
        fakes.enforcer_factory.assert_not_called()
        fakes.client_factory.assert_not_called()

    def test_the_system_identity_is_never_relayed_to_edh(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, claims=SYSTEM_CLAIMS)

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert response["statusCode"] == 403
        fakes.client_factory.assert_not_called()

    def test_a_permitted_call_is_not_denied_by_tier1(self, monkeypatch):
        """Control: with Tier-1 allowing, the request reaches dispatch (an unknown path answers 400)."""
        fakes = install_service_fakes(monkeypatch)

        response = fakes.module.lambda_handler(make_event("GET", "/addon/edh/elsewhere"), {})

        assert response["statusCode"] == 400
        fakes.enforcer.enforceAPI.assert_called_once()


@pytest.mark.unit
class TestDispatch:
    def test_an_unsupported_method_is_400(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)

        response = fakes.module.lambda_handler(make_event("PUT", WORKSTATIONS_PATH), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "Method not allowed"

    @pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
    def test_an_unknown_path_is_400_for_every_method(self, monkeypatch, method):
        fakes = install_service_fakes(monkeypatch)

        response = fakes.module.lambda_handler(make_event(method, "/addon/edh/elsewhere"), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "Unsupported route"
        fakes.client_factory.assert_not_called()

    def test_a_rest_shaped_event_is_normalized_before_the_method_is_read(self, monkeypatch):
        """Top-level httpMethod/path and null params must not become a 500 on requestContext['http']."""
        fakes = install_service_fakes(monkeypatch)
        fakes.enforcer.enforceAPI.return_value = False

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH, rest=True), {})

        assert response["statusCode"] == 403
