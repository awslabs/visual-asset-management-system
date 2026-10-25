# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A start EDH accepts but never finishes (in-dev D16): the session-row rules for the stamp of an accepted start
(``startAcceptedAt``), the stall threshold and ``start_stall_due``, the ``START_STALLED`` code the merge keeps while
the row stays CREATING and clears once EDH moves the desktop on, and the activity of a stalled row, which is no
longer ``starting``. The new names are read off the module inside each test, so a module without them fails each
test on its own rather than the whole file at collection."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.backend.handlers.addon.edh.common import edhSessionState as state
from backend.backend.handlers.addon.edh.common.edhSessionState import (
    observed_row_attributes, start_stamp, workstation_activity,
)

NOW = datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)
SESSION = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"
# Half an hour: the threshold the contract fixes, stated here so the derivation tests do not read it off the module.
STALL_SECONDS = 1800
FAILURE = {"observedAt": "2026-10-24T15:30:00+00:00"}


def _accepted(seconds_ago):
    return start_stamp(NOW - timedelta(seconds=seconds_ago))


def _creating(**fields):
    """A CREATING row a start EDH accepted left, the desktop still booting."""
    return {"bootstrapId": "b", "sessionId": SESSION, "state": "CREATING", "edhState": "pending",
            "connectionAvailable": False, "activity": "starting", **fields}


def _stalled(**fields):
    return _creating(lastError="START_STALLED", startFailure=dict(FAILURE),
                     startAcceptedAt=_accepted(STALL_SECONDS + 60), **fields)


def _listed(edh_state):
    return {"session_uuid": SESSION, "session_state": edh_state}


@pytest.mark.unit
class TestTheVocabulary:
    def test_start_stalled_is_a_stored_code(self):
        assert state.LAST_ERROR_START_STALLED == "START_STALLED"
        assert "START_STALLED" in state.LAST_ERROR_CODES

    def test_a_stall_is_not_a_stopped_rows_code(self):
        assert "START_STALLED" not in state.STOPPED_ROW_LAST_ERRORS

    def test_the_threshold_is_half_an_hour_and_well_past_the_starts_own_windows(self):
        assert state.START_STALL_SECONDS == STALL_SECONDS
        assert state.START_STALL_SECONDS > state.START_RESOLVE_SECONDS > state.START_IN_PROGRESS_SECONDS

    def test_the_stamp_is_the_accepted_start_field(self):
        assert state.START_ACCEPTED_FIELD == "startAcceptedAt"

    def test_a_stall_is_recorded_only_for_a_desktop_edh_lists_pending_or_placing(self):
        assert state.START_STALL_EDH_STATES == {"pending", "placing"}


@pytest.mark.unit
class TestStartStallDue:
    def test_a_creating_row_whose_accepted_start_is_thirty_minutes_old_is_due(self):
        assert state.start_stall_due(_creating(startAcceptedAt=_accepted(STALL_SECONDS)), NOW)

    def test_twenty_nine_minutes_old_is_not(self):
        assert not state.start_stall_due(_creating(startAcceptedAt=_accepted(29 * 60)), NOW)

    def test_one_a_second_short_of_the_threshold_is_not(self):
        assert not state.start_stall_due(_creating(startAcceptedAt=_accepted(STALL_SECONDS - 1)), NOW)

    @pytest.mark.parametrize("row", [
        # A new launch never carries the stamp, however long it boots.
        _creating(),
        _creating(startAcceptedAt=None),
        _creating(startAcceptedAt=""),
        _creating(startAcceptedAt="garbage"),
        # A row some write has moved out of CREATING.
        _creating(state="ACTIVE", startAcceptedAt=_accepted(STALL_SECONDS + 60)),
        _creating(state="STOPPED", startAcceptedAt=_accepted(STALL_SECONDS + 60)),
        _creating(state="ORPHANED", startAcceptedAt=_accepted(STALL_SECONDS + 60)),
        # A row that records the stall already is not recorded again.
        _stalled(),
    ])
    def test_any_other_row_is_not(self, row):
        assert not state.start_stall_due(row, NOW)

    def test_a_stamp_without_an_offset_is_read_as_utc(self):
        naive = (NOW - timedelta(seconds=STALL_SECONDS + 1)).replace(tzinfo=None).isoformat()
        assert state.start_stall_due(_creating(startAcceptedAt=naive), NOW)


@pytest.mark.unit
class TestTheMergeKeepsAndClearsTheStall:
    @pytest.mark.parametrize("edh_state", ["pending", "placing"])
    def test_the_code_and_its_failure_stay_while_the_row_stays_creating(self, edh_state):
        changed = observed_row_attributes(_stalled(), _listed(edh_state))
        assert "lastError" not in changed and "startFailure" not in changed and "startAcceptedAt" not in changed

    def test_running_clears_the_code_the_failure_and_the_stamp(self):
        changed = observed_row_attributes(_stalled(), _listed("running"))
        assert changed["state"] == "ACTIVE"
        assert changed["lastError"] is None and changed["startFailure"] is None
        assert changed["startAcceptedAt"] is None

    @pytest.mark.parametrize("edh_state", ["stopping", "stopped"])
    def test_stopped_clears_the_stall_which_is_not_a_stopped_rows_code(self, edh_state):
        changed = observed_row_attributes(_stalled(), _listed(edh_state))
        assert changed["state"] == "STOPPED"
        assert changed["lastError"] is None and changed["startFailure"] is None
        assert changed["startAcceptedAt"] is None

    def test_error_orphans_the_row_with_a_launch_error_not_the_stall(self):
        changed = observed_row_attributes(_stalled(), _listed("error"))
        assert changed["state"] == "ORPHANED"
        assert changed["lastError"] == "LAUNCH_ERROR"
        assert changed["startFailure"] is None and changed["startAcceptedAt"] is None

    def test_an_accepted_starts_stamp_goes_once_the_desktop_runs(self):
        changed = observed_row_attributes(_creating(startAcceptedAt=_accepted(60)), _listed("running"))
        assert changed["startAcceptedAt"] is None

    def test_an_accepted_starts_stamp_stays_while_the_desktop_boots(self):
        assert "startAcceptedAt" not in observed_row_attributes(_creating(startAcceptedAt=_accepted(60)),
                                                                _listed("pending"))

    def test_another_code_on_a_creating_row_is_still_cleared(self):
        changed = observed_row_attributes(_creating(lastError="CAPACITY_UNAVAILABLE", startFailure=dict(FAILURE)),
                                          _listed("pending"))
        assert changed["lastError"] is None and changed["startFailure"] is None


@pytest.mark.unit
class TestTheActivityOfAStalledRow:
    def test_a_stalled_start_is_not_under_way(self):
        assert workstation_activity(_stalled(), NOW) is None

    def test_a_start_that_has_not_stalled_still_reads_starting(self):
        assert workstation_activity(_creating(startAcceptedAt=_accepted(60)), NOW) == "starting"

    def test_a_new_launch_still_reads_starting(self):
        assert workstation_activity(_creating(), NOW) == "starting"

    def test_a_desktop_edh_reports_stopping_still_reads_stopping(self):
        assert workstation_activity(_stalled(state="STOPPED", edhState="stopping"), NOW) == "stopping"


@pytest.mark.unit
class TestTheClears:
    def test_a_stop_clears_the_stall_the_failure_and_the_stamp(self):
        assert state.start_stall_cleared(_stalled()) == {
            "startAcceptedAt": None, "lastError": None, "startFailure": None}

    def test_a_stop_leaves_any_other_code_alone(self):
        row = _creating(lastError="LEASE_EXPIRED", startFailure=dict(FAILURE), startAcceptedAt=_accepted(60))
        assert state.start_stall_cleared(row) == {"startAcceptedAt": None}

    def test_a_row_without_the_stamp_or_the_code_writes_nothing(self):
        assert state.start_stall_cleared(_creating()) == {}
        assert state.start_accepted_cleared(_creating()) == {}

    @pytest.mark.parametrize("stored, kept", [
        (None, "LAUNCH_ERROR"), ("", "LAUNCH_ERROR"), ("START_STALLED", "LAUNCH_ERROR"),
        ("QUOTA_EXCEEDED", "QUOTA_EXCEEDED"), ("LAUNCH_ERROR", "LAUNCH_ERROR"),
    ])
    def test_an_orphaned_row_keeps_a_launch_code_but_not_a_stall(self, stored, kept):
        assert state.orphaned_last_error(stored) == kept
