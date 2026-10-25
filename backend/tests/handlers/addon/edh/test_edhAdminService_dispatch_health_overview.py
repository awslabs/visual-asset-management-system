# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tier-1, dispatch, the edhProxy audit shape, health and capacity of edhAdminService.

Denials are asserted together with what was NOT consulted (no Casbin construction on an empty token
list, no EDH client on a denied request), because a MagicMock's ``enforce`` answers truthy and a
verdict-only assertion can pass for the wrong reason.
"""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.edh.common import edhClient as ec
from backend.backend.handlers.addon.edh.common.edhClient import EdhApiError, EdhAuthError, EdhUnavailableError
from backend.tests.handlers.addon.edh.edhAdminTestSupport import (
    ADMIN,
    CAPACITY_PATH,
    HEALTH_PATH,
    NO_IDENTITY_CLAIMS,
    SYSTEM_CLAIMS,
    FakeEdhClient,
    edh_proxy_records,
    fixture_payload,
    install_admin_fakes,
    make_event,
    response_body,
)
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
import backend.backend.handlers.addon.edh.edhAdminService as service


@pytest.mark.unit
class TestTierOne:
    def test_a_tier1_denial_is_403_and_reaches_no_edh_client(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch)
        fakes.enforcer.enforceAPI.return_value = False

        response = fakes.module.lambda_handler(make_event("GET", HEALTH_PATH), {})

        assert response["statusCode"] == 403
        fakes.enforcer.enforceAPI.assert_called_once()
        fakes.client_factory.assert_not_called()

    def test_an_empty_token_list_is_denied_before_casbin_is_consulted(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, claims=NO_IDENTITY_CLAIMS)

        response = fakes.module.lambda_handler(make_event("GET", HEALTH_PATH), {})

        assert response["statusCode"] == 403
        fakes.enforcer_factory.assert_not_called()
        fakes.client_factory.assert_not_called()

    def test_the_system_identity_is_never_relayed_to_edh(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, claims=SYSTEM_CLAIMS)

        response = fakes.module.lambda_handler(make_event("GET", HEALTH_PATH), {})

        assert response["statusCode"] == 403
        fakes.client_factory.assert_not_called()

    def test_a_permitted_call_is_not_denied_by_tier1(self, monkeypatch):
        """Control: with Tier-1 allowing, the request reaches dispatch (an unknown path answers 400)."""
        fakes = install_admin_fakes(monkeypatch)

        response = fakes.module.lambda_handler(make_event("GET", "/addon/edh/admin/elsewhere"), {})

        assert response["statusCode"] == 400
        fakes.enforcer.enforceAPI.assert_called_once()


@pytest.mark.unit
class TestDispatch:
    def test_an_unsupported_method_is_400(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch)

        response = fakes.module.lambda_handler(make_event("PATCH", HEALTH_PATH), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "Method not allowed"

    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
    def test_an_unknown_path_is_400_for_every_method(self, monkeypatch, method):
        fakes = install_admin_fakes(monkeypatch)

        response = fakes.module.lambda_handler(make_event(method, "/addon/edh/admin/elsewhere"), {})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "Unsupported route"
        fakes.client_factory.assert_not_called()

    def test_a_rest_shaped_event_is_normalized_before_the_method_is_read(self, monkeypatch):
        """Top-level httpMethod/path and null params must not become a 500 on requestContext['http']."""
        fakes = install_admin_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=[{"id": 1}]))

        response = fakes.module.lambda_handler(make_event("GET", HEALTH_PATH, rest=True), {})

        assert response["statusCode"] == 200


@pytest.mark.unit
class TestHealth:
    def test_reachable_edh_is_200_with_the_cluster_id_and_latency(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=[{"id": 1}]))

        response = fakes.module.lambda_handler(make_event("GET", HEALTH_PATH), {})

        assert response["statusCode"] == 200
        body = response_body(response)
        assert body["status"] == "ok"
        assert body["status"] == service.HEALTH_STATUS_OK
        assert body["edhClusterId"] == service.edh_cluster_id
        assert isinstance(body["latencyMs"], int)
        assert fakes.edh.calls_to("list_profiles") == [("dcv",)]
        fakes.client_factory.assert_called_once_with(profile="api")

    @pytest.mark.parametrize("error,reason", [
        (EdhUnavailableError("boom"), "unreachable"),
        (EdhAuthError("nope", status=401), "credentialRejected"),
        (EdhApiError("Feature not available", status=400), "refused"),
    ])
    def test_a_failed_probe_is_502_naming_the_reason_without_upstream_text(self, monkeypatch, error, reason):
        fakes = install_admin_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=error))

        response = fakes.module.lambda_handler(make_event("GET", HEALTH_PATH), {})

        assert response["statusCode"] == 502
        body = response_body(response)
        assert (body["status"], body["reason"], body["edhClusterId"]) == ("error", reason, service.edh_cluster_id)
        assert body["message"] == service.HEALTH_MESSAGES[reason]
        assert "boom" not in response["body"] and "nope" not in response["body"]
        assert "Feature not available" not in response["body"]

    def test_a_root_key_secret_that_cannot_be_read_is_502_credential_rejected(self, monkeypatch):
        """The real client against a Secrets Manager that refuses the read: the probe never reaches EDH
        and the operator is pointed at the credential."""
        refusing = MagicMock()
        refusing.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied by policy"}}, "GetSecretValue")
        monkeypatch.setattr(ec, "_get_secretsmanager_client", lambda: refusing)
        http = MagicMock()
        monkeypatch.setattr(ec, "_http_request", http)
        fakes = install_admin_fakes(monkeypatch, edh=ec.EdhClient(profile="api"))

        response = fakes.module.lambda_handler(make_event("GET", HEALTH_PATH), {})

        assert response["statusCode"] == 502
        body = response_body(response)
        assert (body["status"], body["reason"]) == ("error", "credentialRejected")
        assert "denied by policy" not in response["body"]
        http.assert_not_called()
        assert [record["edhStatus"] for record in edh_proxy_records(fakes)] == ["EdhAuthError"]

    def test_the_probe_is_audited_as_an_edh_proxy_call_with_method_and_route(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=[{"id": 1}]))

        fakes.module.lambda_handler(make_event("GET", HEALTH_PATH), {})

        assert edh_proxy_records(fakes) == [{
            "operation": "adminHealth",
            "edhUid": edh_uid_for(ADMIN),
            "edhMethod": "GET",
            "edhRoute": ec.EDH_PROFILE_ROUTES["dcv"],
            "edhStatus": "ok",
        }]

    def test_a_failed_probe_is_audited_by_its_error_type(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=EdhUnavailableError("x")))

        fakes.module.lambda_handler(make_event("GET", HEALTH_PATH), {})

        assert [record["edhStatus"] for record in edh_proxy_records(fakes)] == ["EdhUnavailableError"]

    def test_an_unexpected_failure_is_audited_and_is_a_500(self, monkeypatch):
        """A wrapper failure that is not a typed EDH error still leaves an edhProxy record before the 500."""
        fakes = install_admin_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=RuntimeError("unexpected")))

        response = fakes.module.lambda_handler(make_event("GET", HEALTH_PATH), {})

        assert response["statusCode"] == 500
        assert "unexpected" not in response["body"]
        assert [record["edhStatus"] for record in edh_proxy_records(fakes)] == ["RuntimeError"]


def _sessions():
    return [
        {"session_uuid": "s1", "session_owner": "vaaa", "session_state": "running"},
        {"session_uuid": "s2", "session_owner": "vaaa", "session_state": "running"},
        {"session_uuid": "s3", "session_owner": "vbbb", "session_state": "stopped"},
        {"session_uuid": "s4", "session_owner": None},
    ]


@pytest.mark.unit
class TestCapacity:
    def test_capacity_aggregates_list_all_and_passes_profiles_through(self, monkeypatch):
        profiles = [{"id": 1, "allowed_instance_types": ["g4dn.xlarge"]}]
        fakes = install_admin_fakes(
            monkeypatch, edh=FakeEdhClient(list_profiles=profiles, list_all_desktops=_sessions()))

        response = fakes.module.lambda_handler(make_event("GET", CAPACITY_PATH), {})

        assert response["statusCode"] == 200
        body = response_body(response)
        assert body["profiles"] == profiles
        assert body["totalSessions"] == 4
        assert body["byState"] == {"running": 2, "stopped": 1, "unknown": 1}
        assert body["distinctOwners"] == 2

    def test_both_calls_are_audited_in_order(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=FakeEdhClient(list_all_desktops=_sessions()))

        fakes.module.lambda_handler(make_event("GET", CAPACITY_PATH), {})

        assert [(r["edhMethod"], r["edhRoute"], r["operation"]) for r in edh_proxy_records(fakes)] == [
            ("GET", ec.EDH_PROFILE_ROUTES["dcv"], "adminCapacity"),
            ("GET", ec.EDH_ROUTE_DESKTOPS_LIST_ALL, "adminCapacity"),
        ]
        assert fakes.edh.calls_to("list_all_desktops") == [(edh_uid_for(ADMIN),)]

    def test_an_edh_failure_maps_through_the_typed_error_response(self, monkeypatch):
        fakes = install_admin_fakes(
            monkeypatch, edh=FakeEdhClient(list_profiles=EdhUnavailableError("upstream said something")))

        response = fakes.module.lambda_handler(make_event("GET", CAPACITY_PATH), {})

        assert response["statusCode"] == 502
        assert "upstream said something" not in response["body"]
        assert response_body(response)["message"] == "EDH is unavailable. Try again later."

    def test_entries_that_are_not_records_count_as_unknown(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=FakeEdhClient(list_all_desktops=["garbage", None]))

        response = fakes.module.lambda_handler(make_event("GET", CAPACITY_PATH), {})

        assert response_body(response)["byState"] == {"unknown": 2}


@pytest.mark.unit
class TestFixtureShapes:
    """The list_all and profiles keys this module reads, checked against the sanitized captures."""

    def test_the_list_all_capture_carries_the_state_and_owner_keys(self):
        records = fixture_payload("virtual_desktops_list_all.json")
        assert records, "the list_all capture holds no sessions"
        first = records[0]
        assert first.get(service.EDH_FIELD_SESSION_UUID)
        assert first.get(service.EDH_FIELD_SESSION_OWNER)
        assert first.get(service.EDH_FIELD_SESSION_STATE)

    def test_the_list_all_capture_carries_the_name_created_on_and_placement_keys(self):
        # The stale view times a stopped desktop by the VAMS row; EDH's list_all carries no idle field,
        # so the record keys read here are the name, the creation time and the placement fields.
        records = fixture_payload("virtual_desktops_list_all.json")
        assert records, "the list_all capture holds no sessions"
        first = records[0]
        for key in (service.EDH_FIELD_SESSION_NAME, service.EDH_FIELD_SESSION_CREATED_ON,
                    service.EDH_FIELD_SESSION_OS_FAMILY, service.EDH_FIELD_SESSION_INSTANCE_TYPE,
                    service.EDH_FIELD_SESSION_SOFTWARE_STACK):
            assert key in first, key
        assert service._stack_id_from_label(first[service.EDH_FIELD_SESSION_SOFTWARE_STACK]) is not None

    def test_the_profiles_capture_is_a_list_of_records(self):
        records = fixture_payload("profiles_dcv_list.json")
        assert records, "the profiles capture holds no profiles"
        assert all(isinstance(record, dict) for record in records)
