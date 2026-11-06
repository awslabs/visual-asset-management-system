# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The launch-failure reason VAMS captures from EDH (in-dev D8).

EDH's per-user listing carries a desktop's last few events. The shared session-state module picks the
newest ``placement_failed`` or ``session-failed`` event among them, parses the AWS error code and
operation out of EDH's text, keeps a ``session-failed`` event's checkpoint name, and drops everything
else; EDH's text itself is handed back only for the caller to log. It also says which ``lastError`` a
row VAMS recorded ORPHANED becomes once the reason is known, when a row is due a read for one, and
what one read writes onto the row.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

import models.addon.edh as edh_models
from backend.backend.handlers.addon.edh.common import edhSessionState as state

NOW = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)
SESSION_ID = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"
# EDH's placement text for the demo cluster's failed g6.4xlarge launch.
CAPACITY_TEXT = ("An error occurred (InsufficientInstanceCapacity) when calling the CreateCapacityReservation "
                 "operation (reached max retries: 4): Insufficient capacity.")
CONTRACT_CAPACITY_CODES = {"InsufficientInstanceCapacity", "InsufficientCapacity", "InsufficientHostCapacity",
                           "InsufficientReservedInstanceCapacity", "Unsupported"}
CONTRACT_QUOTA_CODES = {"VcpuLimitExceeded", "InstanceLimitExceeded", "ReservationCapacityExceeded",
                        "MaxSpotInstanceCountExceeded"}


def _event(event_type, sub_status="", checkpoint="", received_at="2026-10-22T15:22:09.000000+00:00",
           event_id="e-1"):
    """One entry of EDH's ``recent_events`` as its per-user listing renders it."""
    return {"id": event_id, "event_type": event_type, "checkpoint": checkpoint, "sub_status": sub_status,
            "received_at": received_at}


def _aws_text(code, operation="RunInstances"):
    return f"An error occurred ({code}) when calling the {operation} operation: denied."


def _failure(events):
    return state.edh_failure_from_events(events)[0]


def _orphaned(**overrides):
    row = {"bootstrapId": "b-1", "sessionId": SESSION_ID, "state": state.STATE_ORPHANED,
           "lastError": state.LAST_ERROR_LAUNCH_ERROR}
    row.update(overrides)
    return row


@pytest.mark.unit
class TestTheFailureEvent:
    def test_a_placement_failure_yields_its_event_aws_error_code_and_operation(self):
        failure, text = state.edh_failure_from_events([_event("placement_failed", CAPACITY_TEXT, "timeout")])
        assert failure == {"event": "placement_failed", "errorCode": "InsufficientInstanceCapacity",
                           "operation": "CreateCapacityReservation"}
        # EDH's own text comes back for the caller's log, and only beside the map.
        assert text == CAPACITY_TEXT

    def test_a_bootstrap_failure_keeps_its_checkpoint_name(self):
        failure = _failure([_event("session-failed", "dcv server did not start", "dcv_server_start")])
        assert failure == {"event": "session-failed", "checkpoint": "dcv_server_start"}

    def test_a_placement_failure_carries_no_checkpoint(self):
        # EDH's own placement backstop names its checkpoint "timeout"; only a bootstrap failure keeps one.
        failure = _failure([_event("placement_failed", "Placement pipeline did not respond", "timeout")])
        assert failure == {"event": "placement_failed"}

    def test_the_newest_failure_event_is_taken(self):
        events = [
            _event("placement_failed", _aws_text("InsufficientInstanceCapacity"),
                   received_at="2026-10-22T15:20:00.000000+00:00", event_id="old"),
            _event("session-ready", received_at="2026-10-22T15:21:00.000000+00:00", event_id="ready"),
            _event("session-failed", "bootstrap failed", "configure_dcv",
                   received_at="2026-10-22T15:25:00.000000+00:00", event_id="new"),
            _event("session-heartbeat", received_at="2026-10-22T15:26:00.000000+00:00", event_id="beat"),
        ]
        assert _failure(events) == {"event": "session-failed", "checkpoint": "configure_dcv"}

    def test_the_newest_is_read_from_the_event_time_not_the_list_position(self):
        events = [
            _event("placement_failed", _aws_text("VcpuLimitExceeded"), received_at="2026-10-22T15:25:00+00:00"),
            _event("placement_failed", _aws_text("InsufficientInstanceCapacity"),
                   received_at="2026-10-22T15:20:00+00:00"),
        ]
        assert _failure(events)["errorCode"] == "VcpuLimitExceeded"

    def test_of_two_failures_at_the_same_instant_the_later_listed_is_taken(self):
        events = [_event("placement_failed", _aws_text("InsufficientInstanceCapacity")),
                  _event("placement_failed", _aws_text("VcpuLimitExceeded"))]
        assert _failure(events)["errorCode"] == "VcpuLimitExceeded"

    @pytest.mark.parametrize("events", [
        [],
        [_event("session-ready"), _event("bootstrap-checkpoint", checkpoint="ec2-running")],
        None,
        "placement_failed",
        {"event_type": "placement_failed"},
        ["placement_failed", 7, None],
    ], ids=["no events", "no failure event", "none", "a string", "a mapping", "entries that are not events"])
    def test_no_failure_event_is_no_failure(self, events):
        assert state.edh_failure_from_events(events) == (None, None)

    @pytest.mark.parametrize("event_type", ["Placement_Failed", "placement_failed ", "session_failed", "placed", ""])
    def test_only_the_two_failure_event_types_count(self, event_type):
        assert _failure([_event(event_type, CAPACITY_TEXT)]) is None

    def test_text_that_names_no_aws_error_is_a_failure_without_a_code(self):
        failure, text = state.edh_failure_from_events([_event("placement_failed", "No capacity in any subnet")])
        assert failure == {"event": "placement_failed"}
        assert text == "No capacity in any subnet"

    @pytest.mark.parametrize("sub_status", [None, 12, {"code": "InsufficientInstanceCapacity"}])
    def test_text_that_is_not_a_string_is_a_failure_without_a_code(self, sub_status):
        failure, text = state.edh_failure_from_events([_event("placement_failed", sub_status)])
        assert failure == {"event": "placement_failed"}
        assert text is None

    @pytest.mark.parametrize("code", [
        "Insufficient Capacity", "9Insufficient", "", "Insufficient-Capacity", "A" + "b" * 64,
        "Insufficient<script>",
    ], ids=["space", "leading digit", "empty", "hyphen", "65 characters", "markup"])
    def test_a_malformed_or_oversized_error_code_is_not_kept(self, code):
        failure = _failure([_event("placement_failed", _aws_text(code))])
        assert failure == {"event": "placement_failed"}

    @pytest.mark.parametrize("operation", ["Create Reservation", "Create.Reservation", "C" + "r" * 64, "9Create"],
                             ids=["space", "dot", "65 characters", "leading digit"])
    def test_a_malformed_or_oversized_operation_is_not_kept(self, operation):
        failure = _failure([_event("placement_failed", _aws_text("InsufficientInstanceCapacity", operation))])
        assert "errorCode" not in failure
        assert "operation" not in failure

    def test_the_longest_error_code_and_operation_the_pattern_admits_are_kept(self):
        # Control for the two cases above: 64 characters of each still parse.
        code, operation = "A" + "b" * 63, "C" + "r" * 63
        failure = _failure([_event("placement_failed", _aws_text(code, operation))])
        assert (failure["errorCode"], failure["operation"]) == (code, operation)

    def test_a_dotted_error_code_is_kept(self):
        failure = _failure([_event("placement_failed", _aws_text("Client.InsufficientInstanceCapacity"))])
        assert failure["errorCode"] == "Client.InsufficientInstanceCapacity"

    @pytest.mark.parametrize("checkpoint", ["dcv server", "a/b", "c" * 65, "", None, 3],
                             ids=["space", "slash", "65 characters", "empty", "none", "a number"])
    def test_a_malformed_or_oversized_checkpoint_is_not_kept(self, checkpoint):
        assert _failure([_event("session-failed", "failed", checkpoint)]) == {"event": "session-failed"}

    def test_the_longest_checkpoint_the_pattern_admits_is_kept(self):
        checkpoint = "c" * 64
        assert _failure([_event("session-failed", "failed", checkpoint)])["checkpoint"] == checkpoint

    def test_the_map_carries_only_the_contract_keys_and_never_edh_text(self):
        failure = _failure([_event("session-failed", CAPACITY_TEXT, "dcv_server_start")])
        assert set(failure) <= set(edh_models.EDH_FAILURE_FIELDS)
        assert "Insufficient capacity" not in json.dumps(failure)


@pytest.mark.unit
class TestTheLastErrorAFailureImplies:
    @pytest.mark.parametrize("code", sorted(CONTRACT_CAPACITY_CODES))
    @pytest.mark.parametrize("stored", [None, state.LAST_ERROR_LAUNCH_ERROR])
    def test_a_capacity_code_reads_as_capacity_unavailable(self, code, stored):
        failure = {"event": "placement_failed", "errorCode": code}
        assert state.edh_failure_last_error(failure, stored) == state.LAST_ERROR_CAPACITY_UNAVAILABLE

    @pytest.mark.parametrize("code", sorted(CONTRACT_QUOTA_CODES))
    @pytest.mark.parametrize("stored", [None, state.LAST_ERROR_LAUNCH_ERROR])
    def test_a_quota_code_reads_as_quota_exceeded(self, code, stored):
        failure = {"event": "placement_failed", "errorCode": code}
        assert state.edh_failure_last_error(failure, stored) == state.LAST_ERROR_QUOTA_EXCEEDED

    @pytest.mark.parametrize("failure", [
        {"event": "placement_failed", "errorCode": "UnauthorizedOperation"},
        {"event": "placement_failed"},
        {"event": "session-failed", "checkpoint": "dcv_server_start"},
    ], ids=["an unknown code", "no code", "a bootstrap failure"])
    @pytest.mark.parametrize("stored", [None, state.LAST_ERROR_LAUNCH_ERROR])
    def test_any_other_failure_reads_as_a_launch_error(self, failure, stored):
        assert state.edh_failure_last_error(failure, stored) == state.LAST_ERROR_LAUNCH_ERROR

    @pytest.mark.parametrize("stored", sorted(state.LAST_ERROR_CODES - {state.LAST_ERROR_LAUNCH_ERROR}))
    def test_a_specific_code_the_row_already_holds_is_kept(self, stored):
        failure = {"event": "placement_failed", "errorCode": "VcpuLimitExceeded"}
        assert state.edh_failure_last_error(failure, stored) == stored

    def test_the_code_sets_are_the_contracts_and_do_not_overlap(self):
        assert state.CAPACITY_ERROR_CODES == CONTRACT_CAPACITY_CODES
        assert state.QUOTA_ERROR_CODES == CONTRACT_QUOTA_CODES
        assert not state.CAPACITY_ERROR_CODES & state.QUOTA_ERROR_CODES

    def test_the_codes_it_returns_are_lasterror_codes(self):
        returned = {state.edh_failure_last_error({"event": "placement_failed", "errorCode": code}, None)
                    for code in CONTRACT_CAPACITY_CODES | CONTRACT_QUOTA_CODES | {"Other"}}
        assert returned <= state.LAST_ERROR_CODES
        assert len(returned) == 3


@pytest.mark.unit
class TestWhenARowIsDueARead:
    def test_an_orphaned_row_with_a_session_and_no_reason_is_due(self):
        assert state.edh_failure_read_due(_orphaned(), NOW) is True

    @pytest.mark.parametrize("seconds_ago", [0, 60, 299])
    def test_a_read_stamped_inside_the_interval_holds_the_next_one_off(self, seconds_ago):
        stamp = (NOW - timedelta(seconds=seconds_ago)).isoformat()
        assert state.edh_failure_read_due(_orphaned(edhFailureReadAt=stamp), NOW) is False

    @pytest.mark.parametrize("seconds_ago", [300, 301, 3600])
    def test_a_read_stamped_at_or_past_the_interval_does_not(self, seconds_ago):
        stamp = (NOW - timedelta(seconds=seconds_ago)).isoformat()
        assert state.edh_failure_read_due(_orphaned(edhFailureReadAt=stamp), NOW) is True

    def test_the_interval_is_five_minutes(self):
        assert state.EDH_FAILURE_READ_INTERVAL_SECONDS == 300

    def test_a_stamp_without_an_offset_is_read_as_utc(self):
        stamp = (NOW - timedelta(seconds=60)).replace(tzinfo=None).isoformat()
        assert state.edh_failure_read_due(_orphaned(edhFailureReadAt=stamp), NOW) is False

    @pytest.mark.parametrize("stamp", ["", "yesterday", 12, (NOW + timedelta(hours=1)).isoformat()],
                             ids=["blank", "unreadable", "a number", "far ahead"])
    def test_a_stamp_that_cannot_hold_a_read_off_does_not(self, stamp):
        assert state.edh_failure_read_due(_orphaned(edhFailureReadAt=stamp), NOW) is True

    def test_a_row_that_holds_a_reason_is_never_due(self):
        row = _orphaned(edhFailure={"event": "placement_failed"},
                        edhFailureReadAt=(NOW - timedelta(days=1)).isoformat())
        assert state.edh_failure_read_due(row, NOW) is False

    @pytest.mark.parametrize("overrides", [
        {"state": state.STATE_ACTIVE}, {"state": state.STATE_CREATING}, {"state": state.STATE_STOPPED},
        {"state": state.STATE_DELETED}, {"sessionId": None}, {"sessionId": ""},
    ], ids=["active", "creating", "stopped", "deleted", "no session", "blank session"])
    def test_a_row_that_is_not_an_orphaned_desktop_is_never_due(self, overrides):
        assert state.edh_failure_read_due(_orphaned(**overrides), NOW) is False


@pytest.mark.unit
class TestWhatOneReadWrites:
    def test_a_captured_reason_is_stored_with_its_time_and_reclassifies_a_generic_code(self):
        failure = {"event": "placement_failed", "errorCode": "InsufficientInstanceCapacity",
                   "operation": "CreateCapacityReservation"}
        assert state.edh_failure_attributes(_orphaned(), failure, NOW) == {
            "edhFailureReadAt": NOW.isoformat(),
            "edhFailure": {**failure, "observedAt": NOW.isoformat()},
            "lastError": state.LAST_ERROR_CAPACITY_UNAVAILABLE,
        }

    def test_a_row_without_a_code_is_given_one(self):
        written = state.edh_failure_attributes(_orphaned(lastError=None), {"event": "placement_failed"}, NOW)
        assert written["lastError"] == state.LAST_ERROR_LAUNCH_ERROR

    def test_a_specific_code_is_not_rewritten(self):
        failure = {"event": "placement_failed", "errorCode": "VcpuLimitExceeded"}
        written = state.edh_failure_attributes(_orphaned(lastError="EDH_REFUSED"), failure, NOW)
        assert "lastError" not in written
        assert written["edhFailure"]["errorCode"] == "VcpuLimitExceeded"

    def test_a_generic_code_that_stays_generic_is_not_rewritten(self):
        written = state.edh_failure_attributes(_orphaned(), {"event": "placement_failed"}, NOW)
        assert "lastError" not in written
        assert written["edhFailure"] == {"event": "placement_failed", "observedAt": NOW.isoformat()}

    def test_a_read_that_found_no_reason_writes_only_its_stamp(self):
        assert state.edh_failure_attributes(_orphaned(), None, NOW) == {"edhFailureReadAt": NOW.isoformat()}

    def test_the_stored_reason_carries_only_the_contract_keys(self):
        failure = state.edh_failure_from_events([_event("session-failed", CAPACITY_TEXT, "dcv_server_start")])[0]
        stored = state.edh_failure_attributes(_orphaned(), failure, NOW)["edhFailure"]
        assert set(stored) <= set(edh_models.EDH_FAILURE_FIELDS)
        assert {"event", "observedAt"} <= set(stored)
