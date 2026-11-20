# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A start EDH accepted that EC2 returns to stopped records why (#1730).

EC2 can accept a start and then return the instance to stopped with the state reason
``Server.InsufficientInstanceCapacity``. ``start_returned_to_stopped`` names the pass that moves a row carrying
``startAcceptedAt`` from CREATING to STOPPED with no stop through VAMS recorded; ``instance_state_reason`` reads the
code and the Availability Zone out of EC2's DescribeInstances answer; ``start_return_attributes`` turns them into the
row's ``lastError`` and ``startFailure``: CAPACITY_UNAVAILABLE (RESERVATION_FULL on a row launched into a capacity
reservation) for the capacity code, START_FAILED with the code for any other and without one when the reason was not
read, and nothing for a shutdown a user or the instance initiated."""

from datetime import datetime, timezone

import pytest

from backend.backend.handlers.addon.edh.common import edhSessionState as state

NOW = datetime(2026, 11, 19, 16, 0, tzinfo=timezone.utc)
ACCEPTED = "2026-11-19T15:50:00.000000+00:00"
ZONE = "us-west-2b"


def _accepted_row(**fields):
    return {"bootstrapId": "b-1", "sessionId": "s-1", "state": "CREATING", "edhState": "pending",
            "connectionAvailable": False, "instanceId": "i-0abc", "startAcceptedAt": ACCEPTED, **fields}


def _described(code=None, zone=ZONE, message="Server.InsufficientInstanceCapacity: Insufficient capacity."):
    instance = {"InstanceId": "i-0abc", "State": {"Name": "stopped"}, "Placement": {"AvailabilityZone": zone}}
    if code is not None:
        instance["StateReason"] = {"Code": code, "Message": message}
    return {"Reservations": [{"Instances": [instance]}]}


@pytest.mark.unit
class TestTheTransition:
    def test_an_accepted_start_moving_to_stopped_is_the_transition(self):
        assert state.start_returned_to_stopped(_accepted_row(), {"state": "STOPPED", "edhState": "stopped"})

    @pytest.mark.parametrize("row, attributes", [
        (_accepted_row(startAcceptedAt=None), {"state": "STOPPED"}),
        (_accepted_row(state="ACTIVE"), {"state": "STOPPED"}),
        (_accepted_row(), {"state": "ACTIVE"}),
        (_accepted_row(), {"edhState": "pending"}),
        (_accepted_row(stopRequestedAt="2026-11-19T15:55:00+00:00"), {"state": "STOPPED"}),
    ], ids=["a new launch", "an active row", "to active", "no state change", "a stop through VAMS"])
    def test_anything_else_is_not(self, row, attributes):
        assert not state.start_returned_to_stopped(row, attributes)

    def test_the_merge_clears_the_stamp_in_the_same_write(self):
        # So the transition is read from the row as it was before the write, never from the row after it.
        changed = state.observed_row_attributes(_accepted_row(), {state.EDH_SESSION_ID_FIELD: "s-1",
                                                                  state.EDH_SESSION_STATE_FIELD: "stopped"})
        assert changed["state"] == "STOPPED" and changed["startAcceptedAt"] is None
        assert state.start_returned_to_stopped(_accepted_row(), changed)


@pytest.mark.unit
class TestTheStateReason:
    def test_the_code_and_the_zone_are_read_from_the_answer(self):
        assert state.instance_state_reason(_described("Server.InsufficientInstanceCapacity")) == (
            "Server.InsufficientInstanceCapacity", ZONE)

    def test_an_instance_without_a_state_reason_names_only_its_zone(self):
        assert state.instance_state_reason(_described()) == (None, ZONE)

    @pytest.mark.parametrize("answer", [None, {}, {"Reservations": []}, {"Reservations": [{"Instances": []}]},
                                        {"Reservations": [{}]}, {"Reservations": "x"}, "not an answer",
                                        {"Reservations": [{"Instances": [{"StateReason": "x", "Placement": 1}]}]}])
    def test_anything_else_names_nothing(self, answer):
        assert state.instance_state_reason(answer) == (None, None)


@pytest.mark.unit
class TestWhatTheRowRecords:
    def test_no_capacity_records_capacity_unavailable_with_the_code_and_the_zone(self):
        assert state.start_return_attributes(_accepted_row(), "Server.InsufficientInstanceCapacity", ZONE, NOW) == {
            "lastError": "CAPACITY_UNAVAILABLE",
            "startFailure": {"errorCode": "Server.InsufficientInstanceCapacity", "availabilityZone": ZONE,
                             "observedAt": NOW.isoformat()},
        }

    def test_no_capacity_on_a_reservation_bound_row_records_reservation_full(self):
        row = _accepted_row(capacityReservationId="cr-0123456789abcdef0")
        attributes = state.start_return_attributes(row, "Server.InsufficientInstanceCapacity", ZONE, NOW)
        assert attributes["lastError"] == "RESERVATION_FULL"
        assert attributes["startFailure"]["errorCode"] == "Server.InsufficientInstanceCapacity"

    @pytest.mark.parametrize("code", ["Server.InternalError", "Server.ScheduledStop", "Client.VolumeLimitExceeded",
                                      "Client.InternalError", "Client.InvalidSnapshot.NotFound"])
    def test_another_code_records_the_start_failed_with_that_code(self, code):
        attributes = state.start_return_attributes(_accepted_row(), code, ZONE, NOW)
        assert attributes == {"lastError": "START_FAILED",
                              "startFailure": {"errorCode": code, "availabilityZone": ZONE,
                                               "observedAt": NOW.isoformat()}}

    @pytest.mark.parametrize("code, zone", [(None, None), (None, ZONE), ("", None), ("bad code!", ZONE)])
    def test_a_reason_not_read_records_the_start_failed_without_a_code(self, code, zone):
        attributes = state.start_return_attributes(_accepted_row(), code, zone, NOW)
        assert attributes["lastError"] == "START_FAILED"
        assert "errorCode" not in attributes["startFailure"]
        assert attributes["startFailure"].get("availabilityZone") == zone

    @pytest.mark.parametrize("code", sorted(state.EC2_STATE_REASONS_NOT_A_FAILURE))
    def test_a_shutdown_a_user_or_the_instance_initiated_records_nothing(self, code):
        assert state.start_return_attributes(_accepted_row(), code, ZONE, NOW) == {}

    def test_the_codes_a_stopped_row_keeps_include_both(self):
        assert {"CAPACITY_UNAVAILABLE", "RESERVATION_FULL", "START_FAILED"} <= state.STOPPED_ROW_LAST_ERRORS

    def test_the_recorded_failure_is_kept_by_the_next_merge_while_the_row_stays_stopped(self):
        row = {**_accepted_row(state="STOPPED", edhState="stopped", startAcceptedAt=None),
               **state.start_return_attributes(_accepted_row(), "Server.InsufficientInstanceCapacity", ZONE, NOW)}
        changed = state.observed_row_attributes(row, {state.EDH_SESSION_ID_FIELD: "s-1",
                                                      state.EDH_SESSION_STATE_FIELD: "stopped"})
        assert "lastError" not in changed and "startFailure" not in changed
