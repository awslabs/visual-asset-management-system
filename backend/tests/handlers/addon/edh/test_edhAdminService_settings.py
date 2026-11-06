# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Admin lease settings routes: GET/PUT /addon/edh/admin/settings."""

import pytest

from backend.tests.handlers.addon.edh.edhAdminTestSupport import (
    ADMIN,
    install_admin_fakes,
    make_event,
    response_body,
)
import backend.backend.handlers.addon.edh.edhAdminService as service

SETTINGS_PATH = "/addon/edh/admin/settings"


@pytest.mark.unit
class TestGetSettings:
    def test_returns_lease_projection(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch)
        policy = {"enabled": True, "leaseHours": 12, "forced": False, "graceHours": 24,
                  "updatedAt": "2026-09-17T12:00:00+00:00", "updatedBy": "alice"}
        monkeypatch.setattr(service, "read_lease_policy", lambda: policy)

        response = fakes.module.lambda_handler(make_event("GET", SETTINGS_PATH), {})

        assert response["statusCode"] == 200
        body = response_body(response)
        assert set(body.keys()) == {"lease"}
        lease = body["lease"]
        assert lease["enabled"] is True
        assert lease["leaseHours"] == 12
        assert lease["forced"] is False
        assert lease["graceHours"] == 24
        assert lease["updatedAt"] == "2026-09-17T12:00:00+00:00"
        assert lease["updatedBy"] == "alice"


@pytest.mark.unit
class TestPutSettings:
    def test_updates_and_returns_lease(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch)
        written = []
        monkeypatch.setattr(service, "write_lease_policy",
                            lambda patch, user: written.append((patch, user)) or
                            {"enabled": True, "leaseHours": 12, "forced": True, "graceHours": 6,
                             "updatedAt": "2026-09-17T13:00:00+00:00", "updatedBy": user})

        response = fakes.module.lambda_handler(
            make_event("PUT", SETTINGS_PATH,
                       body={"enabled": True, "leaseHours": 12, "forced": True, "graceHours": 6}), {})

        assert response["statusCode"] == 200
        lease = response_body(response)["lease"]
        assert lease["enabled"] is True
        assert lease["leaseHours"] == 12
        assert lease["forced"] is True
        assert lease["graceHours"] == 6
        assert written[0][1] == ADMIN

    def test_rejects_out_of_range_lease_hours(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch)
        from backend.backend.handlers.addon.edh.common.edhLeasePolicy import LeaseValidationError
        monkeypatch.setattr(service, "write_lease_policy",
                            lambda p, u: (_ for _ in ()).throw(LeaseValidationError("leaseHours must be 1-720")))

        response = fakes.module.lambda_handler(
            make_event("PUT", SETTINGS_PATH, body={"leaseHours": 0}), {})

        assert response["statusCode"] == 400
        assert "leaseHours" in response_body(response)["message"]

    def test_rejects_out_of_range_grace_hours(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch)
        from backend.backend.handlers.addon.edh.common.edhLeasePolicy import LeaseValidationError
        monkeypatch.setattr(service, "write_lease_policy",
                            lambda p, u: (_ for _ in ()).throw(LeaseValidationError("graceHours must be 0-168")))

        response = fakes.module.lambda_handler(
            make_event("PUT", SETTINGS_PATH, body={"graceHours": 999}), {})

        assert response["statusCode"] == 400
        assert "graceHours" in response_body(response)["message"]

    def test_rejects_wrong_type_with_400_not_500(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch)

        response = fakes.module.lambda_handler(
            make_event("PUT", SETTINGS_PATH, body={"leaseHours": "abc"}), {})

        assert response["statusCode"] == 400

    def test_emits_audit(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch)
        monkeypatch.setattr(service, "write_lease_policy",
                            lambda p, u: {"enabled": True, "leaseHours": 12, "forced": True, "graceHours": 6,
                                          "updatedAt": "2026-09-17T13:00:00+00:00", "updatedBy": u})

        fakes.module.lambda_handler(
            make_event("PUT", SETTINGS_PATH,
                       body={"enabled": True, "leaseHours": 12, "forced": True, "graceHours": 6}), {})

        assert len(fakes.log_actions.call_args_list) == 1
        call = fakes.log_actions.call_args_list[0]
        operation = call.args[1]
        detail = call.args[2]
        assert operation == "edhLeasePolicyUpdate"
        assert detail["lease"]["leaseHours"] == 12

    def test_unknown_path_returns_unsupported_route(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch)

        response = fakes.module.lambda_handler(
            make_event("PUT", "/addon/edh/admin/unknown"), {})

        assert response["statusCode"] == 400
        assert "Unsupported route" in response_body(response)["message"]

    def test_ignores_unknown_body_keys(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch)
        written = []
        monkeypatch.setattr(service, "write_lease_policy",
                            lambda p, u: written.append(p) or
                            {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24,
                             "updatedAt": "2026-09-17T13:00:00+00:00", "updatedBy": u})

        response = fakes.module.lambda_handler(
            make_event("PUT", SETTINGS_PATH,
                       body={"leaseHours": 8, "unknownField": "ignored"}), {})

        assert response["statusCode"] == 200

    def test_partial_update_sends_none_for_the_fields_left_out(self, monkeypatch):
        # Absent fields must reach write_lease_policy as None, which is its signal to keep the stored
        # value. Sending defaults instead would silently reset the three fields the caller did not touch.
        fakes = install_admin_fakes(monkeypatch)
        written = []
        monkeypatch.setattr(service, "write_lease_policy",
                            lambda p, u: written.append(p) or
                            {"enabled": True, "leaseHours": 16, "forced": True, "graceHours": 6,
                             "updatedAt": "2026-09-17T13:00:00+00:00", "updatedBy": u})

        response = fakes.module.lambda_handler(
            make_event("PUT", SETTINGS_PATH, body={"leaseHours": 16}), {})

        assert response["statusCode"] == 200
        assert written[0] == {"enabled": None, "leaseHours": 16, "forced": None, "graceHours": None}
        # The fields the caller left out come back at their stored values, not at model defaults.
        lease = response_body(response)["lease"]
        assert lease["enabled"] is True and lease["forced"] is True and lease["graceHours"] == 6
