# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The owner's stop of a new launch EDH has not placed: a CREATING row the sweep recorded ``LAUNCH_STALLED`` never
ran and EDH refuses to stop it, so the route answers 409 naming Delete and sends EDH no stop, writes nothing and
keeps the key. The stored stall is checked against EDH's state read first: a launch EDH has placed since is stopped
as any other, and a start that stalled is still stopped."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.backend.handlers.addon.edh.common.edhSessionState import start_stamp
from backend.tests.handlers.addon.edh.edhServiceTestSupport import SESSION_OWN, own_row, response_body, state_record
from backend.tests.handlers.addon.edh.test_edhWorkstationService_start_async import _run

FAILURE = {"observedAt": "2026-11-18T13:00:00+00:00"}
REFUSAL = "EDH has not placed this new workstation, so it cannot be stopped; delete it instead"


def _stalled_launch(**fields):
    """A new launch, never started, the sweep recorded stalled; its key is kept."""
    created = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    return own_row(state="CREATING", edhState="pending", connectionAvailable=False, lastError="LAUNCH_STALLED",
                   startFailure=dict(FAILURE), createdAt=created, apiKeyId="key-2",
                   secretName="vams/edh-session/x", **fields)


def _edh_calls(fakes, wrapper):
    return [args for name, args in fakes.edh.calls if name == wrapper]


@pytest.mark.unit
class TestAStopOfAStalledLaunch:
    @pytest.mark.parametrize("edh_state", ["pending", "placing"])
    def test_it_is_refused_with_409_naming_delete_and_never_reaches_edh(self, monkeypatch, edh_state):
        response, fakes = _run(monkeypatch, _stalled_launch(), action="stop",
                               desktop_states=[state_record(SESSION_OWN, edh_state)])
        assert response["statusCode"] == 409
        assert response_body(response) == {"message": REFUSAL}
        assert _edh_calls(fakes, "get_desktop_states"), "the stall was not checked against EDH's view"
        assert not _edh_calls(fakes, "stop_desktop")
        stored = fakes.stored.item
        assert stored["state"] == "CREATING" and stored["lastError"] == "LAUNCH_STALLED"
        assert stored["apiKeyId"] == "key-2"
        assert not fakes.revoked

    def test_the_refusal_names_no_identifier(self, monkeypatch):
        response, _ = _run(monkeypatch, _stalled_launch(), action="stop",
                           desktop_states=[state_record(SESSION_OWN, "pending")])
        message = response_body(response)["message"]
        assert SESSION_OWN not in message and own_row()["bootstrapId"] not in message

    def test_a_launch_edh_no_longer_lists_is_refused_too(self, monkeypatch):
        response, fakes = _run(monkeypatch, _stalled_launch(), action="stop", desktop_states=[])
        assert response["statusCode"] == 409
        assert not _edh_calls(fakes, "stop_desktop")

    def test_a_launch_edh_has_placed_since_the_sweep_is_stopped(self, monkeypatch):
        response, fakes = _run(monkeypatch, _stalled_launch(), action="stop",
                               desktop_states=[state_record(SESSION_OWN, "running")])
        assert response["statusCode"] == 200
        assert _edh_calls(fakes, "stop_desktop")
        stored = fakes.stored.item
        assert stored["state"] == "STOPPED" and stored["lastError"] is None
        assert fakes.revoked

    def test_a_stalled_start_is_still_stopped(self, monkeypatch):
        accepted = start_stamp(datetime.now(timezone.utc) - timedelta(hours=1))
        row = own_row(state="CREATING", edhState="pending", connectionAvailable=False, lastError="START_STALLED",
                      startFailure=dict(FAILURE), startAcceptedAt=accepted)
        response, fakes = _run(monkeypatch, row, action="stop", desktop_states=[state_record(SESSION_OWN, "pending")])
        assert response["statusCode"] == 200
        assert _edh_calls(fakes, "stop_desktop")

    def test_a_running_desktop_is_stopped_without_a_state_read(self, monkeypatch):
        row = own_row(state="ACTIVE", edhState="running")
        response, fakes = _run(monkeypatch, row, action="stop", desktop_states=[state_record(SESSION_OWN, "running")])
        assert response["statusCode"] == 200
        assert not _edh_calls(fakes, "get_desktop_states")
        assert _edh_calls(fakes, "stop_desktop")
