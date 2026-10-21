# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Starting a workstation under the lease policy. A DELETED row is terminal and an expired lease is
refused, both before a credential is minted or EDH is reached; a start that proceeds clears the stamp
the lease stop left and the expiry code with it, since a started desktop is no longer lease-stopped."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import backend.backend.handlers.addon.edh.edhWorkstationService as service
from backend.backend.handlers.addon.edh.common.edhSessionState import (
    EDH_SESSION_ID_FIELD, EDH_SESSION_STATE_FIELD, LAST_ERROR_LEASE_EXPIRED,
)
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    SESSION_OWN,
    FakeEdhClient,
    install_service_fakes,
    make_event,
    own_row,
    response_body,
    workstation_path,
)

ENABLED = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24, "updatedAt": None, "updatedBy": None}
DISABLED = {**ENABLED, "enabled": False}


def _hours_from_now(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _lease_stopped_row(**overrides):
    """The caller's desktop as the lease left it: stopped an hour ago on a lease that ran out then."""
    row = own_row(state="STOPPED", edhState="stopped", connectionAvailable=False,
                  leaseExpiresAt=_hours_from_now(-1), leaseStoppedAt=_hours_from_now(-1))
    row.update(overrides)
    return row


def _start(monkeypatch, row, policy=ENABLED, rest=False):
    """Drive a start of ``row`` with the credential mint replaced; (response, fakes, mint)."""
    fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(
        get_desktop_states=[{EDH_SESSION_ID_FIELD: SESSION_OWN, EDH_SESSION_STATE_FIELD: "stopped"}]))
    fakes.table.query.return_value = {"Items": [row]}
    monkeypatch.setattr(service, "read_lease_policy", lambda: dict(policy))
    mint = MagicMock(return_value=("skipped", {}))
    monkeypatch.setattr(service.bootstrap, "bootstrap_session", mint)
    event = make_event("POST", workstation_path(SESSION_OWN, "start"),
                       path_params={"sessionId": SESSION_OWN}, rest=rest)
    return service.lambda_handler(event, MagicMock()), fakes, mint


def _written(fakes, attribute):
    """(True, value) when the last row write set ``attribute``, else (False, None)."""
    call = fakes.table.update_item.call_args
    names, values = call.kwargs["ExpressionAttributeNames"], call.kwargs["ExpressionAttributeValues"]
    for placeholder, name in names.items():
        if name == attribute and placeholder.startswith("#a"):
            return True, values[placeholder.replace("#a", ":v")]
    return False, None


@pytest.mark.unit
class TestStartUnderALease:

    def test_an_expired_lease_is_refused_before_edh_or_the_mint_is_reached(self, monkeypatch):
        response, fakes, mint = _start(monkeypatch, _lease_stopped_row())

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == (
            "The lease on this workstation has expired; renew it before starting")
        assert fakes.edh.calls == []
        fakes.client_factory.assert_not_called()
        mint.assert_not_called()
        fakes.table.update_item.assert_not_called()

    def test_a_lease_that_holds_starts_the_desktop_and_clears_the_lease_stop(self, monkeypatch):
        response, fakes, mint = _start(monkeypatch, _lease_stopped_row(leaseExpiresAt=_hours_from_now(3)))

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("start_desktop") == [(fakes.edh.calls[0][1][0], SESSION_OWN)]
        mint.assert_called_once()
        assert _written(fakes, "leaseStoppedAt") == (True, None)
        assert _written(fakes, "state") == (True, "CREATING")
        assert not response_body(response).get("leaseStoppedAt")

    def test_start_clears_the_expiry_code_on_its_final_update(self, monkeypatch):
        row = _lease_stopped_row(leaseExpiresAt=_hours_from_now(3), lastError=LAST_ERROR_LEASE_EXPIRED)

        response, fakes, _ = _start(monkeypatch, row)

        assert response["statusCode"] == 200
        assert _written(fakes, "state") == (True, "CREATING")
        assert _written(fakes, "lastError") == (True, None)
        assert response_body(response)["lastError"] is None

    def test_a_policy_that_does_not_enforce_leases_starts_an_expired_one(self, monkeypatch):
        response, fakes, _ = _start(monkeypatch, _lease_stopped_row(), policy=DISABLED)

        assert response["statusCode"] == 200
        assert len(fakes.edh.calls_to("start_desktop")) == 1

    def test_an_unreadable_expiry_is_no_lease(self, monkeypatch):
        response, fakes, _ = _start(monkeypatch, _lease_stopped_row(leaseExpiresAt="not-a-date"))

        assert response["statusCode"] == 200
        assert len(fakes.edh.calls_to("start_desktop")) == 1

    @pytest.mark.parametrize("rest", [False, True], ids=["http", "rest"])
    def test_a_deleted_row_is_refused_before_edh_is_reached(self, monkeypatch, rest):
        response, fakes, mint = _start(monkeypatch, own_row(state="DELETED", connectionAvailable=False), rest=rest)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "A deleted workstation cannot be started"
        assert fakes.edh.calls == []
        fakes.client_factory.assert_not_called()
        mint.assert_not_called()
