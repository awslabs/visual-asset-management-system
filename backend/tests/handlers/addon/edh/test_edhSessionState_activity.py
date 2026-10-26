# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""What a workstation is doing between two states (``workstation_activity``), derived from the row on every read,
and the start-failure vocabulary the session-row module shares: the codes a STOPPED row keeps until a start is
accepted, the ``startFailure`` beside them, the start mark the state merge clears, the sweep's resolution rule
and the windows a start holds."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.backend.handlers.addon.edh.common import edhSessionState as state
from backend.backend.handlers.addon.edh.common.edhSessionState import (
    observed_row_attributes, start_failure, start_resolve_due, start_stamp, workstation_activity,
)

NOW = datetime(2026, 10, 23, 15, 30, tzinfo=timezone.utc)
FRESH = start_stamp(NOW - timedelta(seconds=30))
STALE = start_stamp(NOW - timedelta(seconds=state.START_IN_PROGRESS_SECONDS + 1))
OLD = start_stamp(NOW - timedelta(seconds=state.START_RESOLVE_SECONDS + 1))
SESSION = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"


def _row(**fields):
    return {"bootstrapId": "b", "sessionId": SESSION, **fields}


@pytest.mark.unit
class TestActivityDerivation:
    @pytest.mark.parametrize("row, expected", [
        # starting: a STOPPED row marked with a start whose stamp is fresh ...
        (_row(state="STOPPED", edhState="stopped", activity="starting", startRequestedAt=FRESH), "starting"),
        # ... but not one whose stamp is past the window, has none, or carries no mark.
        (_row(state="STOPPED", edhState="stopped", activity="starting", startRequestedAt=STALE), None),
        (_row(state="STOPPED", edhState="stopped", activity="starting"), None),
        (_row(state="STOPPED", edhState="stopped", startRequestedAt=FRESH), None),
        (_row(state="STOPPED", edhState="stopped"), None),
        # starting: CREATING with a session id (EDH took the start, or placed a new desktop) ...
        (_row(state="CREATING", edhState="pending", connectionAvailable=False), "starting"),
        (_row(state="CREATING", edhState="placing"), "starting"),
        # ... but a launch that has no session id yet is not "starting" a desktop.
        ({"bootstrapId": "b", "state": "CREATING"}, None),
        # starting: ACTIVE while not connectable; an ACTIVE connectable desktop is doing nothing.
        (_row(state="ACTIVE", edhState="running", connectionAvailable=False), "starting"),
        (_row(state="ACTIVE", edhState="running", connectionAvailable=True), None),
        # stopping: EDH reports the desktop stopping (the row maps it to STOPPED).
        (_row(state="STOPPED", edhState="stopping"), "stopping"),
        (_row(state="STOPPED", edhState="STOPPING"), "stopping"),
        # stopping: a stop through VAMS less than five minutes ago while EDH still reports running ...
        (_row(state="STOPPED", edhState="running", stopRequestedAt=(NOW - timedelta(seconds=60)).isoformat()),
         "stopping"),
        (_row(state="ACTIVE", edhState="running", connectionAvailable=True,
              stopRequestedAt=(NOW - timedelta(seconds=299)).isoformat()), "stopping"),
        # ... and not once five minutes have passed, nor once EDH reports it stopped.
        (_row(state="ACTIVE", edhState="running", connectionAvailable=True,
              stopRequestedAt=(NOW - timedelta(seconds=301)).isoformat()), None),
        (_row(state="STOPPED", edhState="stopped", stopRequestedAt=(NOW - timedelta(seconds=60)).isoformat()),
         None),
        (_row(state="STOPPED", edhState="running", stopRequestedAt="not-a-time"), None),
        # Terminal and failed rows do nothing.
        (_row(state="DELETED", edhState="stopping"), None),
        (_row(state="ORPHANED", edhState="error"), None),
        (_row(state="STOPPED", edhState="interrupted"), None),
    ])
    def test_the_activity_every_record_reports(self, row, expected):
        assert workstation_activity(row, NOW) == expected

    def test_the_activities_are_the_models(self):
        from backend.backend.models.addon.edh import WORKSTATION_ACTIVITIES
        assert state.WORKSTATION_ACTIVITIES == WORKSTATION_ACTIVITIES == ("starting", "stopping")


@pytest.mark.unit
class TestStoppedRowCodes:
    @pytest.mark.parametrize("code", ["CAPACITY_UNAVAILABLE", "RESERVATION_FULL", "RESERVATION_ENDED", "START_FAILED",
                                      "LEASE_EXPIRED"])
    def test_a_start_failure_code_stays_while_edh_reports_the_desktop_stopped(self, code):
        row = _row(state="STOPPED", edhState="stopped", lastError=code,
                   startFailure={"observedAt": NOW.isoformat()})
        changed = observed_row_attributes(row, {"session_uuid": SESSION, "session_state": "stopped"})
        assert "lastError" not in changed and "startFailure" not in changed

    @pytest.mark.parametrize("edh_state, mapped", [("pending", "CREATING"), ("running", "ACTIVE")])
    @pytest.mark.parametrize("code", ["CAPACITY_UNAVAILABLE", "RESERVATION_FULL", "RESERVATION_ENDED", "START_FAILED"])
    def test_the_code_and_the_failure_go_once_the_desktop_moves_on(self, code, edh_state, mapped):
        row = _row(state="STOPPED", edhState="stopped", lastError=code,
                   startFailure={"observedAt": NOW.isoformat()})
        changed = observed_row_attributes(row, {"session_uuid": SESSION, "session_state": edh_state})
        assert changed["state"] == mapped
        assert changed["lastError"] is None and changed["startFailure"] is None

    def test_another_code_on_a_stopped_row_is_cleared_as_before(self):
        row = _row(state="ACTIVE", edhState="running", lastError="LAUNCH_ERROR")
        changed = observed_row_attributes(row, {"session_uuid": SESSION, "session_state": "stopped"})
        assert changed["lastError"] is None

    def test_start_failed_is_a_stored_code(self):
        assert "START_FAILED" in state.LAST_ERROR_CODES
        assert state.STOPPED_ROW_LAST_ERRORS == {"LEASE_EXPIRED", "CAPACITY_UNAVAILABLE", "RESERVATION_FULL",
                                                 "RESERVATION_ENDED", "START_FAILED"}


@pytest.mark.unit
class TestTheStartMark:
    @pytest.mark.parametrize("edh_state", ["running", "error"])
    def test_a_desktop_seen_running_or_failed_loses_the_mark(self, edh_state):
        row = _row(state="CREATING", edhState="pending", activity="starting")
        assert observed_row_attributes(row, {"session_uuid": SESSION, "session_state": edh_state})["activity"] is None

    @pytest.mark.parametrize("edh_state", ["stopped", "pending"])
    def test_a_desktop_still_stopped_or_starting_keeps_it(self, edh_state):
        row = _row(state="STOPPED", edhState="stopped", activity="starting", startRequestedAt=FRESH)
        assert "activity" not in observed_row_attributes(row, {"session_uuid": SESSION, "session_state": edh_state})


@pytest.mark.unit
class TestTheSweepsResolution:
    def test_a_marked_stopped_row_whose_stamp_is_ten_minutes_old_is_due(self):
        assert start_resolve_due(_row(state="STOPPED", activity="starting", startRequestedAt=OLD), NOW)

    @pytest.mark.parametrize("row", [
        _row(state="STOPPED", activity="starting", startRequestedAt=FRESH),
        _row(state="STOPPED", activity="starting", startRequestedAt=STALE),
        _row(state="STOPPED", activity="starting"),
        _row(state="STOPPED", activity="starting", startRequestedAt="garbage"),
        _row(state="STOPPED", startRequestedAt=OLD),
        _row(state="CREATING", activity="starting", startRequestedAt=OLD),
    ])
    def test_any_other_row_is_not(self, row):
        assert not start_resolve_due(row, NOW)


@pytest.mark.unit
class TestTheStartFailure:
    def test_the_aws_code_edhs_text_names_is_kept_and_the_text_is_not(self):
        text = ("Unable to start instance due to An error occurred (InsufficientInstanceCapacity) when calling the "
                "StartInstances operation (reached max retries: 4): We currently do not have capacity")
        failure = start_failure(text, "us-west-2b", NOW)
        assert failure == {"errorCode": "InsufficientInstanceCapacity", "availabilityZone": "us-west-2b",
                           "observedAt": NOW.isoformat()}

    @pytest.mark.parametrize("text", [None, "", "EDH is busy", 42])
    def test_a_text_that_names_no_aws_error_records_none(self, text):
        assert start_failure(text, None, NOW) == {"observedAt": NOW.isoformat()}


@pytest.mark.unit
class TestTheWindows:
    def test_a_start_holds_its_row_for_the_workers_whole_run(self):
        # The launch worker's six-minute timeout and a minute for the hand-over.
        assert state.START_IN_PROGRESS_SECONDS == 420

    def test_the_sweep_resolves_a_start_only_after_the_window(self):
        assert state.START_RESOLVE_SECONDS == 600
        assert state.START_RESOLVE_SECONDS > state.START_IN_PROGRESS_SECONDS

    def test_a_stop_reads_as_stopping_for_five_minutes(self):
        assert state.STOP_SETTLE_SECONDS == 300
