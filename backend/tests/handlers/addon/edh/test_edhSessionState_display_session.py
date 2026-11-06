# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A running desktop whose DCV session has ended is not connectable (D20).

EDH's per-user listing asks the DCV broker for a connection token for each running desktop. When the broker
holds no session for it, EDH logs a warning and lists the fallback link: its entry point and the broker session
id, with no query at all. Only a link whose query carries an ``authToken`` or a ``username`` signs a user in.
A running desktop listed with any other link that is not blank records ``connectionUnavailableReason``
``DISPLAY_SESSION_ENDED`` and stays ACTIVE; a record without the connection-string field (EDH's token-free
session-state read, the admin listing) says nothing of the display session, so it neither records the reason
nor clears it while EDH still reports the desktop running.
"""

import pytest

from backend.backend.handlers.addon.edh.common.edhClient import EDH_SESSION_ID_FIELD
import backend.backend.handlers.addon.edh.common.edhSessionState as state

SESSION_ID = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"
ENTRY = "https://edh.example.internal"
HOST = "host.example.internal"

# EDH's link forms (its api/v1/dcv/list_virtual_desktops.py).
HIGH_SCALE = f"{ENTRY}/?authToken=t0k3n&username=vabc#{SESSION_ID}"
STANDARD_CONSOLE = f"{ENTRY}/{HOST}/?username=vabc#{SESSION_ID}"
STANDARD_VIRTUAL = f"{ENTRY}/{HOST}/?authToken=t0k3n#{SESSION_ID}"
FALLBACK = f"{ENTRY}/?#{SESSION_ID}"
PLACEHOLDER = f"{ENTRY}/{HOST}/#{SESSION_ID}"


def _desktop(session_state="running", **fields):
    return {EDH_SESSION_ID_FIELD: SESSION_ID, state.EDH_SESSION_STATE_FIELD: session_state, **fields}


def _linked(link, session_state="running"):
    return _desktop(session_state, **{state.EDH_CONNECTION_STRING_FIELD: link})


def _active_row(**overrides):
    return {"bootstrapId": "b-1", "sessionId": SESSION_ID, "state": "ACTIVE", "edhState": "running",
            "connectionAvailable": True, "lastError": None, **overrides}


def _ended_row(**overrides):
    return _active_row(**{"connectionAvailable": False,
                          "connectionUnavailableReason": state.CONNECTION_UNAVAILABLE_DISPLAY_SESSION_ENDED,
                          **overrides})


@pytest.mark.unit
class TestConnectionLink:
    @pytest.mark.parametrize("link", [HIGH_SCALE, STANDARD_CONSOLE, STANDARD_VIRTUAL],
                             ids=["high-scale", "standard console", "standard virtual"])
    def test_a_link_whose_query_signs_a_user_in_is_connectable(self, link):
        assert state.connection_string_connectable(link) is True

    @pytest.mark.parametrize("link", [
        FALLBACK, PLACEHOLDER, f"{ENTRY}/{HOST}/?#{SESSION_ID}", f"{ENTRY}/?other=1#{SESSION_ID}",
        f"{ENTRY}/?authToken=&username=#{SESSION_ID}", f"{ENTRY}/?username=%20%20#{SESSION_ID}",
        f"{ENTRY}/#?authToken=t0k3n",
    ], ids=["fallback", "placeholder", "empty query", "other field", "blank values", "blank username",
            "token in the fragment"])
    def test_a_link_without_an_auth_token_or_a_username_is_not_connectable(self, link):
        assert state.connection_string_connectable(link) is False

    @pytest.mark.parametrize("value", ["https://[bad/?authToken=t0k3n", "", "   ", None, 42, b"?authToken=x"],
                             ids=["malformed", "empty", "blank", "none", "number", "bytes"])
    def test_a_malformed_or_missing_link_is_not_connectable(self, value):
        assert state.connection_string_connectable(value) is False


@pytest.mark.unit
class TestObservedDisplaySession:
    @pytest.mark.parametrize("link", [FALLBACK, PLACEHOLDER, "https://[bad/?authToken=t0k3n"],
                             ids=["fallback", "placeholder", "malformed"])
    def test_a_running_desktop_with_a_dead_link_is_not_connectable_and_records_why(self, link):
        changed = state.observed_row_attributes(_active_row(), _linked(link))

        assert changed["connectionAvailable"] is False
        assert changed["connectionUnavailableReason"] == "DISPLAY_SESSION_ENDED"
        # The desktop is running: the row stays ACTIVE, nothing is orphaned, stopped or deleted.
        assert "state" not in changed and "lastError" not in changed

    @pytest.mark.parametrize("link", [HIGH_SCALE, STANDARD_CONSOLE, STANDARD_VIRTUAL])
    def test_a_running_desktop_with_a_good_link_is_connectable(self, link):
        assert state.observed_row_attributes(_active_row(), _linked(link)) == {}

    def test_a_good_link_clears_the_reason(self):
        changed = state.observed_row_attributes(_ended_row(), _linked(HIGH_SCALE))

        assert changed == {"connectionAvailable": True, "connectionUnavailableReason": None}

    def test_a_dead_link_on_a_row_that_records_it_writes_nothing(self):
        assert state.observed_row_attributes(_ended_row(), _linked(FALLBACK)) == {}

    @pytest.mark.parametrize("value", ["", None], ids=["empty", "none"])
    def test_an_empty_link_is_not_connectable_without_a_reason(self, value):
        # Not EDH's fallback: the connect route's generic not-ready answer stands.
        changed = state.observed_row_attributes(_active_row(), _linked(value))

        assert changed == {"connectionAvailable": False}

    def test_a_record_without_the_field_keeps_a_connectable_row_connectable(self):
        assert state.observed_row_attributes(_active_row(), _desktop()) == {}
        assert state.observed_row_attributes(_active_row(connectionAvailable=False), _desktop()) == {
            "connectionAvailable": True}

    def test_a_record_without_the_field_keeps_the_reason_while_edh_reports_running(self):
        # The token-free state read the list and get routes make, and the admin listing, cannot see the session.
        assert state.observed_row_attributes(_ended_row(), _desktop()) == {}

    def test_a_reason_on_a_row_not_stored_active_is_not_kept(self):
        # A row a stop or start moved out of ACTIVE before any read cleared its reason: running is connectable.
        changed = state.observed_row_attributes(_ended_row(state="CREATING", edhState="pending"), _desktop())

        assert changed["state"] == "ACTIVE"
        assert changed["connectionAvailable"] is True
        assert changed["connectionUnavailableReason"] is None

    @pytest.mark.parametrize("edh_state", ["stopping", "stopped", "pending", "error"])
    def test_a_desktop_seen_other_than_running_clears_the_reason(self, edh_state):
        for desktop in (_desktop(edh_state), _linked(FALLBACK, edh_state)):
            changed = state.observed_row_attributes(_ended_row(), desktop)

            assert changed["connectionUnavailableReason"] is None
            assert "connectionAvailable" not in changed


@pytest.mark.unit
class TestRecordedReason:
    def test_an_active_running_row_that_records_it_returns_the_reason(self):
        assert state.connection_unavailable_reason(_ended_row()) == "DISPLAY_SESSION_ENDED"

    @pytest.mark.parametrize("overrides", [
        {"state": "STOPPED", "edhState": "stopped"}, {"state": "DELETED"}, {"edhState": "stopping"},
        {"connectionAvailable": True}, {"connectionUnavailableReason": "SOMETHING_ELSE"},
        {"connectionUnavailableReason": None},
    ], ids=["stopped", "deleted", "stopping", "connectable", "unknown code", "none"])
    def test_any_other_row_returns_none(self, overrides):
        assert state.connection_unavailable_reason(_ended_row(**overrides)) is None

    def test_a_row_whose_session_ended_is_not_starting(self):
        # Without the reason an ACTIVE row that is not connectable reads as starting; with it the desktop is not.
        assert state.workstation_activity(_active_row(connectionAvailable=False)) == state.ACTIVITY_STARTING
        assert state.workstation_activity(_ended_row()) is None

    def test_the_message_says_what_to_do(self):
        assert state.DISPLAY_SESSION_ENDED_MESSAGE == (
            "The workstation is running but its remote display session has ended. "
            "Stop it, then start it again, to reconnect.")
