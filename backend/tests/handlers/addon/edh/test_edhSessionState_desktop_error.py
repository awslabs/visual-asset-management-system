# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""EDH's ``error`` session state orphans only a new launch whose desktop never ran (#1731).

EDH sets ``error`` on a launch whose placement failed, and also on a desktop that runs when its Amazon DCV health
check fails, which EDH moves on again once the check passes. ``desktop_has_run`` tells the two apart from the row:
ACTIVE and STOPPED rows, a CREATING row whose start EDH accepted (``startAcceptedAt``) and a DELETED row the sweep
recorded because EDH stopped listing its desktop (``edhUnlistedAt``) have run. ``state_for_edh_desktop`` keeps such a
row in its state (a DELETED one EDH lists again is re-adopted ACTIVE), ``connection_verdict`` records it not
connectable with ``DESKTOP_ERROR``, and the record returns that reason and no ``activity``. A new launch -- a CREATING
row without the stamp -- is still ORPHANED with ``LAUNCH_ERROR`` and no reason."""

import pytest
from datetime import datetime, timezone

from backend.backend.handlers.addon.edh.common import edhSessionState as state

SESSION_ID = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"
ACCEPTED = "2026-11-19T15:00:00.000000+00:00"
HAS_RUN = [
    ("ACTIVE", {}),
    ("STOPPED", {"edhState": "stopped"}),
    ("CREATING", {"edhState": "pending", "startAcceptedAt": ACCEPTED}),
    ("DELETED", {"edhUnlistedAt": "2026-11-19T15:10:00+00:00"}),
]
HAS_RUN_IDS = ["active", "stopped", "accepted start", "deleted while unlisted"]
NEVER_RAN = [
    ("CREATING", {"edhState": "placing"}),
    ("ORPHANED", {"edhState": "error", "lastError": "LAUNCH_ERROR"}),
    ("DELETED", {}),
]
NEVER_RAN_IDS = ["new launch", "failed launch", "deleted through VAMS"]


def _row(state_value, **fields):
    return {"bootstrapId": "b-1", "sessionId": SESSION_ID, "state": state_value, "edhState": "running",
            "connectionAvailable": state_value == "ACTIVE", "lastError": None, **fields}


def _listed(session_state, **fields):
    return {state.EDH_SESSION_ID_FIELD: SESSION_ID, state.EDH_SESSION_STATE_FIELD: session_state, **fields}



@pytest.mark.unit
class TestWhichDesktopsHaveRun:
    @pytest.mark.parametrize("state_value, fields", HAS_RUN, ids=HAS_RUN_IDS)
    def test_a_row_whose_desktop_has_run(self, state_value, fields):
        assert state.desktop_has_run(_row(state_value, **fields)) is True

    @pytest.mark.parametrize("state_value, fields", NEVER_RAN, ids=NEVER_RAN_IDS)
    def test_a_row_whose_desktop_never_ran(self, state_value, fields):
        assert state.desktop_has_run(_row(state_value, **fields)) is False

    def test_a_record_without_a_stored_state_has_not_run(self):
        assert state.desktop_has_run({}) is False


@pytest.mark.unit
class TestTheStateEdhsErrorImplies:
    @pytest.mark.parametrize("state_value, fields", HAS_RUN[:3], ids=HAS_RUN_IDS[:3])
    @pytest.mark.parametrize("wording", ["error", "ERROR", "Error"])
    def test_a_desktop_that_has_run_keeps_its_state(self, state_value, fields, wording):
        assert state.state_for_edh_desktop(_row(state_value, **fields), wording) == state_value

    def test_a_deleted_row_edh_lists_again_in_error_is_re_adopted_active(self):
        row = _row("DELETED", edhUnlistedAt="2026-11-19T15:10:00+00:00")
        assert state.state_for_edh_desktop(row, "error") == state.STATE_ACTIVE

    @pytest.mark.parametrize("state_value, fields", NEVER_RAN, ids=NEVER_RAN_IDS)
    def test_a_launch_that_never_ran_is_orphaned(self, state_value, fields):
        assert state.state_for_edh_desktop(_row(state_value, **fields), "error") == state.STATE_ORPHANED

    def test_a_record_with_no_row_is_orphaned_as_before(self):
        assert state.state_for_edh_desktop({}, "error") == state.STATE_ORPHANED

    @pytest.mark.parametrize("edh_state, vams_state", [("running", "ACTIVE"), ("pending", "CREATING"),
                                                       ("stopped", "STOPPED"), ("stopping", "STOPPED")])
    def test_every_other_state_maps_as_the_row_blind_mapping(self, edh_state, vams_state):
        for state_value, fields in HAS_RUN + NEVER_RAN:
            row = _row(state_value, **fields)
            assert state.state_for_edh_desktop(row, edh_state) == vams_state
            assert state.state_for_edh_desktop(row, edh_state) == state.state_for_edh_session_state(
                edh_state, row["state"])

    def test_no_state_keeps_the_stored_one(self):
        assert state.state_for_edh_desktop(_row("ACTIVE"), None) == "ACTIVE"

    def test_the_row_blind_mapping_is_unchanged(self):
        assert state.state_for_edh_session_state("error", "ACTIVE") == state.STATE_ORPHANED


@pytest.mark.unit
class TestTheVerdict:
    @pytest.mark.parametrize("state_value, fields", HAS_RUN, ids=HAS_RUN_IDS)
    def test_a_desktop_that_has_run_in_error_is_not_connectable_for_the_desktop_error(self, state_value, fields):
        for desktop in (_listed("error"), _listed("error", connection_string=""),
                        _listed("error", connection_string=f"https://edh.example/?authToken=t#{SESSION_ID}")):
            assert state.connection_verdict(_row(state_value, **fields), desktop) == (False, "DESKTOP_ERROR")

    @pytest.mark.parametrize("state_value, fields", NEVER_RAN, ids=NEVER_RAN_IDS)
    def test_a_launch_that_never_ran_in_error_is_not_connectable_without_a_reason(self, state_value, fields):
        assert state.connection_verdict(_row(state_value, **fields), _listed("error")) == (False, None)


@pytest.mark.unit
class TestTheMerge:
    def test_an_active_desktop_in_error_keeps_state_and_credential_and_records_the_reason(self):
        row = _row("ACTIVE", apiKeyId="key-1", secretName="vams/edh-session/b-1")
        changed = state.observed_row_attributes(row, _listed("error"))
        assert changed == {"edhState": "error", "connectionAvailable": False,
                           "connectionUnavailableReason": "DESKTOP_ERROR"}

    def test_a_stopped_desktop_in_error_keeps_its_stopped_code(self):
        row = _row("STOPPED", edhState="stopped", lastError="CAPACITY_UNAVAILABLE",
                   startFailure={"errorCode": "InsufficientInstanceCapacity", "observedAt": ACCEPTED})
        changed = state.observed_row_attributes(row, _listed("error"))
        assert "state" not in changed and "lastError" not in changed and "startFailure" not in changed
        assert changed["connectionUnavailableReason"] == "DESKTOP_ERROR"

    def test_an_accepted_start_in_error_keeps_creating_and_its_stamp(self):
        row = _row("CREATING", edhState="pending", connectionAvailable=False, startAcceptedAt=ACCEPTED)
        changed = state.observed_row_attributes(row, _listed("error"))
        assert "state" not in changed and "startAcceptedAt" not in changed
        assert changed["connectionUnavailableReason"] == "DESKTOP_ERROR"

    def test_a_stopped_row_marked_starting_loses_the_mark_when_edh_reports_error(self):
        # EDH sets error only on a desktop that runs, so the start the mark held for is over.
        row = _row("STOPPED", edhState="stopped", activity="starting", startRequestedAt=ACCEPTED)
        changed = state.observed_row_attributes(row, _listed("error"))
        assert changed["activity"] is None
        assert changed["startRequestedAt"] is None

    def test_a_new_launch_in_error_is_orphaned_with_a_launch_error_and_no_reason(self):
        row = _row("CREATING", edhState="placing", connectionAvailable=False)
        changed = state.observed_row_attributes(row, _listed("error"))
        assert changed["state"] == state.STATE_ORPHANED
        assert changed["lastError"] == state.LAST_ERROR_LAUNCH_ERROR
        assert "connectionUnavailableReason" not in changed

    def test_edh_moving_on_from_error_clears_the_reason(self):
        row = _row("ACTIVE", edhState="error", connectionAvailable=False, connectionUnavailableReason="DESKTOP_ERROR")
        for edh_state, expected in (("running", "ACTIVE"), ("pending", "CREATING"), ("stopped", "STOPPED")):
            changed = state.observed_row_attributes(row, _listed(edh_state))
            assert changed["connectionUnavailableReason"] is None
            assert changed.get("state", "ACTIVE") == expected


@pytest.mark.unit
class TestADesktopThatHasRunKeepsThatThroughCreating:
    """EDH holds a running desktop whose Amazon DCV check failed back as ``pending``, and a start made from EDH's own
    console moves a stopped one to ``pending``: VAMS maps both to CREATING. The merge stamps ``startAcceptedAt`` on
    such a row, so a later ``error`` or an unlisted desktop does not read it as a new launch."""

    NOW = datetime(2026, 11, 19, 17, 0, tzinfo=timezone.utc)

    @pytest.mark.parametrize("state_value, fields", [
        ("ACTIVE", {}), ("STOPPED", {"edhState": "stopped"}),
        ("DELETED", {"edhUnlistedAt": "2026-11-19T15:10:00+00:00"}),
    ], ids=["active", "stopped", "deleted while unlisted"])
    @pytest.mark.parametrize("edh_state", ["pending", "placing"])
    def test_a_row_that_has_run_moving_into_creating_is_stamped(self, state_value, fields, edh_state):
        changed = state.observed_row_attributes(_row(state_value, **fields), _listed(edh_state), self.NOW)
        assert changed["state"] == state.STATE_CREATING
        assert changed["startAcceptedAt"] == state.start_stamp(self.NOW)

    def test_the_stamp_keeps_a_later_error_off_orphaned(self):
        stamped = {**_row("ACTIVE"), **state.observed_row_attributes(_row("ACTIVE"), _listed("pending"), self.NOW)}
        changed = state.observed_row_attributes(stamped, _listed("error"))
        assert "state" not in changed
        assert changed["connectionUnavailableReason"] == "DESKTOP_ERROR"

    def test_a_new_launch_is_never_stamped(self):
        row = _row("CREATING", edhState="placing", connectionAvailable=False)
        assert "startAcceptedAt" not in state.observed_row_attributes(row, _listed("pending"), self.NOW)

    def test_a_row_already_stamped_keeps_its_stamp(self):
        row = _row("CREATING", edhState="pending", connectionAvailable=False, startAcceptedAt=ACCEPTED)
        assert "startAcceptedAt" not in state.observed_row_attributes(row, _listed("placing"), self.NOW)

    def test_an_orphaned_failed_launch_edh_lists_again_is_not_stamped(self):
        row = _row("ORPHANED", edhState="error", lastError="LAUNCH_ERROR", connectionAvailable=False)
        assert "startAcceptedAt" not in state.observed_row_attributes(row, _listed("pending"), self.NOW)


@pytest.mark.unit
class TestTheRecord:
    @pytest.mark.parametrize("state_value", ["ACTIVE", "STOPPED", "CREATING"])
    def test_the_record_returns_the_reason_while_edh_reports_error(self, state_value):
        row = _row(state_value, edhState="error", connectionAvailable=False,
                   connectionUnavailableReason="DESKTOP_ERROR")
        assert state.connection_unavailable_reason(row) == "DESKTOP_ERROR"

    @pytest.mark.parametrize("overrides", [
        {"state": "ORPHANED"}, {"state": "DELETED"}, {"edhState": "running"}, {"edhState": "stopped"},
        {"connectionAvailable": True},
    ], ids=["orphaned", "deleted", "running again", "stopped", "connectable"])
    def test_a_stale_reason_is_not_returned(self, overrides):
        row = _row("ACTIVE", edhState="error", connectionAvailable=False, connectionUnavailableReason="DESKTOP_ERROR")
        assert state.connection_unavailable_reason({**row, **overrides}) is None

    @pytest.mark.parametrize("state_value, fields", [
        ("ACTIVE", {}), ("CREATING", {"startAcceptedAt": ACCEPTED}), ("STOPPED", {}),
    ], ids=["active", "accepted start", "stopped"])
    def test_a_desktop_in_error_reads_no_activity(self, state_value, fields):
        row = _row(state_value, edhState="error", connectionAvailable=False,
                   connectionUnavailableReason="DESKTOP_ERROR", **fields)
        assert state.workstation_activity(row) is None

    def test_a_booting_accepted_start_still_reads_starting(self):
        row = _row("CREATING", edhState="pending", connectionAvailable=False, startAcceptedAt=ACCEPTED)
        assert state.workstation_activity(row) == state.ACTIVITY_STARTING

    def test_the_reason_and_its_sentence_are_in_the_vocabulary(self):
        assert state.CONNECTION_UNAVAILABLE_DESKTOP_ERROR == "DESKTOP_ERROR"
        assert "DESKTOP_ERROR" in state.CONNECTION_UNAVAILABLE_REASONS
        assert "DISPLAY_SESSION_ENDED" in state.CONNECTION_UNAVAILABLE_REASONS
        assert state.DESKTOP_ERROR_MESSAGE.startswith("The workstation's remote display is not responding")
