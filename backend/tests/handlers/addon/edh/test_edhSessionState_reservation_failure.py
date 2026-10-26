# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The launch-failure classifier on a row launched into a capacity reservation.

A desktop that EDH launched into a reservation the user named, and that failed because the reservation had
no free slot, reports ``RESERVATION_FULL`` rather than the quota or capacity code an on-demand launch would
report for the same AWS error: the cure is another reservation or on-demand, not a quota request. A row
without a reservation keeps today's mapping, and a specific code a row holds is kept either way.
"""

from datetime import datetime, timezone

import pytest

from backend.backend.handlers.addon.edh.common import edhSessionState as state

CR_ID = "cr-0123456789abcdef0"
NOW = datetime(2026, 10, 23, 15, 0, 0, tzinfo=timezone.utc)
RESERVATION_CODES = sorted(state.CAPACITY_ERROR_CODES | {"ReservationCapacityExceeded"})


@pytest.mark.unit
class TestReservationFullCode:
    def test_the_code_is_stored_vocabulary(self):
        assert state.LAST_ERROR_RESERVATION_FULL == "RESERVATION_FULL"
        assert state.LAST_ERROR_RESERVATION_FULL in state.LAST_ERROR_CODES

    @pytest.mark.parametrize("code", RESERVATION_CODES)
    def test_a_reservation_bound_row_reports_reservation_full(self, code):
        failure = {"event": "placement_failed", "errorCode": code}
        assert state.edh_failure_last_error(failure, None, reservation_bound=True) == "RESERVATION_FULL"
        assert state.edh_failure_last_error(failure, "LAUNCH_ERROR", reservation_bound=True) == "RESERVATION_FULL"

    @pytest.mark.parametrize("code,expected", [
        ("ReservationCapacityExceeded", "QUOTA_EXCEEDED"),
        ("InsufficientInstanceCapacity", "CAPACITY_UNAVAILABLE"),
        ("VcpuLimitExceeded", "QUOTA_EXCEEDED"),
    ])
    def test_a_row_without_a_reservation_keeps_todays_mapping(self, code, expected):
        failure = {"event": "placement_failed", "errorCode": code}
        assert state.edh_failure_last_error(failure, None) == expected
        assert state.edh_failure_last_error(failure, None, reservation_bound=False) == expected

    def test_a_reservation_bound_row_keeps_todays_mapping_for_other_codes(self):
        assert state.edh_failure_last_error({"errorCode": "VcpuLimitExceeded"}, None,
                                            reservation_bound=True) == "QUOTA_EXCEEDED"
        assert state.edh_failure_last_error({"errorCode": "InvalidAMIID.NotFound"}, None,
                                            reservation_bound=True) == "LAUNCH_ERROR"
        assert state.edh_failure_last_error(None, None, reservation_bound=True) == "LAUNCH_ERROR"

    def test_a_specific_stored_code_is_kept(self):
        failure = {"errorCode": "ReservationCapacityExceeded"}
        assert state.edh_failure_last_error(failure, "QUOTA_EXCEEDED", reservation_bound=True) == "QUOTA_EXCEEDED"
        assert state.edh_failure_last_error(failure, "RESERVATION_FULL") == "RESERVATION_FULL"

    def test_the_failure_attributes_read_the_reservation_off_the_row(self):
        failure = {"event": "placement_failed", "errorCode": "ReservationCapacityExceeded"}
        bound = {"state": "ORPHANED", "lastError": "LAUNCH_ERROR", "capacityReservationId": CR_ID}
        unbound = {"state": "ORPHANED", "lastError": "LAUNCH_ERROR"}
        fallback = {"state": "ORPHANED", "lastError": "LAUNCH_ERROR", "capacityReservationFallback": True,
                    "capacityReservationRequested": CR_ID}

        assert state.edh_failure_attributes(bound, failure, NOW)["lastError"] == "RESERVATION_FULL"
        assert state.edh_failure_attributes(unbound, failure, NOW)["lastError"] == "QUOTA_EXCEEDED"
        # A fallback row was launched on-demand: the reservation it asked for is not the one that failed.
        assert state.edh_failure_attributes(fallback, failure, NOW)["lastError"] == "QUOTA_EXCEEDED"


@pytest.mark.unit
class TestRefusalOfAReservationBoundDesktop:
    """The rule a refused create (launch worker) or start (workstation service) of a reservation-bound desktop
    applies once it has read the reservation again; kept here so every caller classifies alike."""

    @pytest.mark.parametrize("facts,expected", [
        ({"state": "active", "availableInstanceCount": 0}, "RESERVATION_FULL"),
        ({"state": "active", "availableInstanceCount": None}, "RESERVATION_FULL"),
        ({"state": "active", "availableInstanceCount": 1}, None),
        ({"state": "expired", "availableInstanceCount": 0}, None),
        ({"state": "notFound"}, None),
        (None, None),
    ])
    def test_a_full_active_reservation_is_reservation_full(self, facts, expected):
        assert state.reservation_refusal_last_error(facts) == expected

    @pytest.mark.parametrize("facts,free,full", [
        ({"state": "active", "availableInstanceCount": 2}, True, False),
        ({"state": "active", "availableInstanceCount": True}, False, True),
        ({"state": "cancelled", "availableInstanceCount": 2}, False, False),
        (None, False, False),
    ])
    def test_free_and_full(self, facts, free, full):
        assert state.reservation_has_free_slot(facts) is free
        assert state.reservation_is_full(facts) is full


@pytest.mark.unit
class TestReservationEndedCode:
    def test_the_code_is_stored_vocabulary_a_stopped_row_keeps(self):
        assert state.LAST_ERROR_RESERVATION_ENDED == "RESERVATION_ENDED"
        assert state.LAST_ERROR_RESERVATION_ENDED in state.LAST_ERROR_CODES
        assert state.LAST_ERROR_RESERVATION_ENDED in state.STOPPED_ROW_LAST_ERRORS

    def test_the_failure_takes_the_code_of_vams_own_aws_call(self):
        failure = state.start_failure(None, "us-west-2b", NOW, error_code="UnauthorizedOperation")
        assert failure == {"observedAt": NOW.isoformat(), "errorCode": "UnauthorizedOperation",
                           "availabilityZone": "us-west-2b"}

    @pytest.mark.parametrize("code", ["", "Unauthorized Operation", "1Bad", "x" * 65, None, 42])
    def test_a_code_that_is_not_one_is_left_out(self, code):
        failure = state.start_failure(None, None, NOW, error_code=code)
        assert failure == {"observedAt": NOW.isoformat()}

    def test_without_a_code_the_failure_still_reads_edhs_text(self):
        text = "An error occurred (ReservationCapacityExceeded) when calling the StartInstances operation: full"
        assert state.start_failure(text, None, NOW)["errorCode"] == "ReservationCapacityExceeded"


@pytest.mark.unit
class TestWhyARowRunsOnDemand:
    @pytest.mark.parametrize("row,expected", [
        ({"capacityReservationFallback": True, "capacityReservationFallbackReason": "ended"}, "ended"),
        ({"capacityReservationFallback": True, "capacityReservationFallbackReason": "full"}, "full"),
        ({"capacityReservationFallback": True}, "full"),
        ({"capacityReservationFallback": True, "capacityReservationFallbackReason": "other"}, "full"),
        ({"capacityReservationFallback": False, "capacityReservationFallbackReason": "ended"}, None),
        ({"capacityReservationId": CR_ID}, None),
        ({}, None),
    ])
    def test_the_reason(self, row, expected):
        assert state.capacity_reservation_fallback_reason(row) == expected

    @pytest.mark.parametrize("row,expected", [
        ({"capacityReservationId": CR_ID}, CR_ID),
        ({"capacityReservationFallback": True, "capacityReservationRequested": CR_ID}, CR_ID),
        ({"capacityReservationId": CR_ID, "capacityReservationRequested": "cr-00000000000000aaa"}, CR_ID),
        ({"capacityReservationId": None, "capacityReservationRequested": ""}, None),
        ({}, None),
    ])
    def test_the_reservation_a_record_names(self, row, expected):
        assert state.capacity_reservation_named(row) == expected
