# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A new launch EDH never places: the session-row rules for ``LAUNCH_STALLED``. ``launch_stall_due`` holds for a
CREATING row with a session id, no accepted start and no start mark, made three hours ago; the merge keeps the code
while the row stays CREATING and clears it once EDH moves the desktop on; the record reports no activity for it; a
stop clears it, and an orphaned row reads LAUNCH_ERROR in its place."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.backend.handlers.addon.edh.common import edhSessionState as state
from backend.backend.handlers.addon.edh.common.edhSessionState import (
    observed_row_attributes, start_stamp, workstation_activity,
)

NOW = datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)
SESSION = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"
# Three hours: the threshold the contract fixes, stated here so the derivation tests do not read it off the module.
LAUNCH_STALL_SECONDS = 3 * 3600
FAILURE = {"observedAt": "2026-10-24T15:30:00+00:00"}


def _made(seconds_ago):
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


def _launch(seconds_ago=LAUNCH_STALL_SECONDS, **fields):
    """A CREATING row of a new launch EDH lists, made ``seconds_ago``: no accepted start and no start mark."""
    return {"bootstrapId": "b", "sessionId": SESSION, "state": "CREATING", "edhState": "pending",
            "connectionAvailable": False, "createdAt": _made(seconds_ago), **fields}


def _stalled(**fields):
    return _launch(LAUNCH_STALL_SECONDS + 60, lastError="LAUNCH_STALLED", startFailure=dict(FAILURE), **fields)


def _listed(edh_state):
    return {"session_uuid": SESSION, "session_state": edh_state}


@pytest.mark.unit
class TestTheVocabulary:
    def test_launch_stalled_is_a_stored_code_and_a_stall(self):
        assert state.LAST_ERROR_LAUNCH_STALLED == "LAUNCH_STALLED"
        assert "LAUNCH_STALLED" in state.LAST_ERROR_CODES
        assert state.STALLED_LAST_ERRORS == {"START_STALLED", "LAUNCH_STALLED"}

    def test_a_stall_is_not_a_stopped_rows_code(self):
        assert "LAUNCH_STALLED" not in state.STOPPED_ROW_LAST_ERRORS

    def test_the_threshold_is_three_hours_and_longer_than_a_starts(self):
        assert state.LAUNCH_STALL_SECONDS == LAUNCH_STALL_SECONDS
        assert state.LAUNCH_STALL_SECONDS > state.START_STALL_SECONDS


@pytest.mark.unit
class TestLaunchStallDue:
    def test_a_launch_three_hours_old_is_due(self):
        assert state.launch_stall_due(_launch(LAUNCH_STALL_SECONDS), NOW)

    def test_a_second_short_of_three_hours_is_not(self):
        assert not state.launch_stall_due(_launch(LAUNCH_STALL_SECONDS - 1), NOW)

    def test_a_first_boot_two_hours_in_is_not(self):
        assert not state.launch_stall_due(_launch(2 * 3600), NOW)

    @pytest.mark.parametrize("row", [
        # A start carries the accepted start's stamp, or the start mark when the stamp was not written.
        _launch(startAcceptedAt=start_stamp(NOW - timedelta(hours=4))),
        _launch(activity="starting"),
        # A launch EDH has no desktop for yet is the worker's or the creating grace's.
        _launch(sessionId=""),
        _launch(sessionId=None),
        # A row some write has moved out of CREATING.
        _launch(state="ACTIVE"),
        _launch(state="STOPPED"),
        _launch(state="ORPHANED"),
        # An unreadable launch time holds nothing.
        _launch(createdAt="garbage"),
        _launch(createdAt=None),
        # A row that records a stall already is not recorded again.
        _stalled(),
        _launch(lastError="START_STALLED"),
    ])
    def test_any_other_row_is_not(self, row):
        assert not state.launch_stall_due(row, NOW)

    def test_a_launch_time_without_an_offset_is_read_as_utc(self):
        naive = (NOW - timedelta(seconds=LAUNCH_STALL_SECONDS + 1)).replace(tzinfo=None).isoformat()
        assert state.launch_stall_due(_launch(createdAt=naive), NOW)

    def test_a_stalled_launch_is_never_a_stalled_start(self):
        assert not state.start_stall_due(_launch(LAUNCH_STALL_SECONDS * 2), NOW)


@pytest.mark.unit
class TestTheMergeKeepsAndClearsTheStall:
    @pytest.mark.parametrize("edh_state", ["pending", "placing"])
    def test_the_code_and_its_failure_stay_while_the_row_stays_creating(self, edh_state):
        changed = observed_row_attributes(_stalled(), _listed(edh_state))
        assert "lastError" not in changed and "startFailure" not in changed

    def test_running_clears_the_code_and_the_failure(self):
        changed = observed_row_attributes(_stalled(), _listed("running"))
        assert changed["state"] == "ACTIVE"
        assert changed["lastError"] is None and changed["startFailure"] is None

    @pytest.mark.parametrize("edh_state", ["stopping", "stopped"])
    def test_stopped_clears_the_stall_which_is_not_a_stopped_rows_code(self, edh_state):
        changed = observed_row_attributes(_stalled(), _listed(edh_state))
        assert changed["state"] == "STOPPED"
        assert changed["lastError"] is None and changed["startFailure"] is None

    def test_error_orphans_the_row_with_a_launch_error_not_the_stall(self):
        changed = observed_row_attributes(_stalled(), _listed("error"))
        assert changed["state"] == "ORPHANED"
        assert changed["lastError"] == "LAUNCH_ERROR"
        assert changed["startFailure"] is None


@pytest.mark.unit
class TestTheActivityOfAStalledLaunch:
    def test_a_stalled_launch_is_not_under_way(self):
        assert workstation_activity(_stalled(), NOW) is None

    def test_a_launch_that_has_not_stalled_still_reads_starting(self):
        assert workstation_activity(_launch(60), NOW) == "starting"


@pytest.mark.unit
class TestTheClears:
    def test_a_stop_clears_the_stall_and_the_failure(self):
        assert state.start_stall_cleared(_stalled()) == {"lastError": None, "startFailure": None}

    @pytest.mark.parametrize("stored, kept", [
        ("LAUNCH_STALLED", "LAUNCH_ERROR"), ("START_STALLED", "LAUNCH_ERROR"), ("QUOTA_EXCEEDED", "QUOTA_EXCEEDED"),
    ])
    def test_an_orphaned_row_keeps_a_launch_code_but_not_a_stall(self, stored, kept):
        assert state.orphaned_last_error(stored) == kept
