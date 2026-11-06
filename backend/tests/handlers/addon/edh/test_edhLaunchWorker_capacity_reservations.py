# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The launch worker on a row launched into a capacity reservation.

A row carrying ``capacityReservationId`` is created with ``capacity_reservation_id`` and ``subnet_id``
``auto``, so EDH places the desktop in one of its private subnets in the reservation's Availability Zone; a
row without one sends today's form, key for key. When EDH refuses the create of a reservation-bound row, the
worker reads the reservation again: full, with its registration still allowing a fallback, the create is
retried once on-demand and the row records the fallback; full without one, the row is ORPHANED with
``RESERVATION_FULL``; anything else is the existing refusal path. A create whose answer was lost may have
happened, so it is never retried and the reservation is never read for it.
"""

import pytest
from moto import mock_aws

from backend.backend.handlers.addon.edh.common.edhClient import (
    EDH_DESKTOP_CREATE_FIELDS, EdhApiError, EdhCapacityError, EdhOutcomeUnknownError, EdhUnavailableError,
)
from backend.backend.handlers.addon.edh.common.edhSessionState import (
    LAST_ERROR_CAPACITY_UNAVAILABLE, LAST_ERROR_EDH_REFUSED, LAST_ERROR_LAUNCH_ERROR, LAST_ERROR_RESERVATION_FULL,
    MAX_LAUNCH_ATTEMPTS,
)
from backend.tests.handlers.addon.edh.edhReservationTestSupport import (
    CR_FULL, Untouchable, client_error, ec2_reservation, install_reservation_fakes,
)
from backend.tests.handlers.addon.edh.edhServiceTestSupport import FakeEdhClient, Sequence
from backend.tests.handlers.addon.edh.edhWorkerTestSupport import (
    creating_row, desktop, install_worker_fakes, updates, worker_event,
)

# The form a launch without a reservation sends today.
ON_DEMAND_FORM_KEYS = {"instance_type", "software_stack_id", "disk_size", "session_name", "project", "session_type"}
FULL = ec2_reservation(CR_FULL, AvailableInstanceCount=0)


@pytest.fixture
def aws():
    with mock_aws():
        yield


def _run(monkeypatch, row, create, *, records=None, failure=None, registered=((CR_FULL, {}),)):
    edh = FakeEdhClient(create_desktop=create, find_desktop_by_name=Sequence(None, desktop()))
    fakes = install_worker_fakes(monkeypatch, row=row, edh=edh)
    fakes.reservations = install_reservation_fakes(monkeypatch, records=records, failure=failure,
                                                   registered=registered)
    result = fakes.module.lambda_handler(worker_event(row), None)
    return result, fakes


def _forms(fakes):
    return [args[1] for args in fakes.edh.calls_to("create_desktop")]


def _fallback_write(fakes):
    """The update_item call that moved the row off its reservation, or None."""
    for call in fakes.table.update_item.call_args_list:
        if "REMOVE" in call.kwargs.get("UpdateExpression", ""):
            return call.kwargs
    return None


@pytest.mark.unit
class TestCreateForm:
    def test_a_row_without_a_reservation_sends_todays_form_and_reads_no_reservation(self, monkeypatch):
        edh = FakeEdhClient(find_desktop_by_name=Sequence(None, desktop()))
        fakes = install_worker_fakes(monkeypatch, row=creating_row(), edh=edh)
        monkeypatch.setattr(fakes.module.reservations, "ec2", Untouchable())
        monkeypatch.setattr(fakes.module.reservations, "_session_table", Untouchable())

        fakes.module.lambda_handler(worker_event(creating_row()), None)

        [form] = _forms(fakes)
        assert set(form) == ON_DEMAND_FORM_KEYS
        assert "capacity_reservation_id" not in form and "subnet_id" not in form

    def test_a_reservation_bound_row_names_the_reservation_and_an_automatic_subnet(self, monkeypatch, aws):
        result, fakes = _run(monkeypatch, creating_row(capacityReservationId=CR_FULL), {})

        [form] = _forms(fakes)
        assert form["capacity_reservation_id"] == CR_FULL
        assert form["subnet_id"] == "auto"
        assert set(form) == ON_DEMAND_FORM_KEYS | {"capacity_reservation_id", "subnet_id"}
        assert set(form) <= EDH_DESKTOP_CREATE_FIELDS
        # An accepted create reads no reservation.
        assert fakes.reservations.ec2.calls == []
        assert result["outcome"] == "launched"

    def test_a_fallback_row_sends_the_on_demand_form(self, monkeypatch, aws):
        row = creating_row(capacityReservationFallback=True, capacityReservationRequested=CR_FULL)

        _, fakes = _run(monkeypatch, row, {})

        [form] = _forms(fakes)
        assert set(form) == ON_DEMAND_FORM_KEYS


@pytest.mark.unit
class TestRefusedCreateOfAReservationBoundRow:
    def test_a_full_reservation_that_falls_back_is_retried_once_on_demand(self, monkeypatch, aws):
        row = creating_row(capacityReservationId=CR_FULL)

        result, fakes = _run(monkeypatch, row, Sequence(EdhApiError("GENERIC_ERROR"), {}),
                             records={CR_FULL: FULL}, registered=[(CR_FULL, {"fallbackToOnDemand": True})])

        first, second = _forms(fakes)
        assert first["capacity_reservation_id"] == CR_FULL
        assert set(second) == ON_DEMAND_FORM_KEYS
        write = _fallback_write(fakes)
        assert write is not None
        removed = [write["ExpressionAttributeNames"][name] for name in write["ExpressionAttributeNames"]
                   if name.startswith("#r")]
        assert removed == ["capacityReservationId"]
        written = updates(fakes)
        assert {"capacityReservationFallback": True, "capacityReservationRequested": CR_FULL,
                "capacityReservationFallbackReason": "full"}.items() <= \
            next(w for w in written if "capacityReservationFallback" in w).items()
        assert result["outcome"] == "launched" and result["state"] != "ORPHANED"
        assert fakes.reservations.ec2.ids_asked() == [[CR_FULL]]

    def test_the_on_demand_retry_is_made_at_most_once(self, monkeypatch, aws):
        row = creating_row(capacityReservationId=CR_FULL)

        result, fakes = _run(monkeypatch, row, EdhCapacityError("unable to find available capacity"),
                             records={CR_FULL: FULL}, registered=[(CR_FULL, {"fallbackToOnDemand": True})])

        assert len(_forms(fakes)) == 2
        assert result["state"] == "ORPHANED"
        assert updates(fakes)[-1]["lastError"] == LAST_ERROR_CAPACITY_UNAVAILABLE
        # The reservation is read after the first refusal only: the on-demand create no longer names it.
        assert fakes.reservations.ec2.ids_asked() == [[CR_FULL]]

    def test_a_full_reservation_that_does_not_fall_back_orphans_the_row_reservation_full(self, monkeypatch, aws):
        row = creating_row(capacityReservationId=CR_FULL)

        result, fakes = _run(monkeypatch, row, EdhApiError("GENERIC_ERROR"),
                             records={CR_FULL: FULL}, registered=[(CR_FULL, {"fallbackToOnDemand": False})])

        assert len(_forms(fakes)) == 1
        assert result["state"] == "ORPHANED"
        assert updates(fakes)[-1] == {"state": "ORPHANED", "lastError": LAST_ERROR_RESERVATION_FULL,
                                      "connectionAvailable": False}
        assert _fallback_write(fakes) is None

    def test_a_full_reservation_unregistered_since_the_launch_orphans_the_row_reservation_full(self, monkeypatch, aws):
        row = creating_row(capacityReservationId=CR_FULL)

        result, fakes = _run(monkeypatch, row, EdhApiError("GENERIC_ERROR"), records={CR_FULL: FULL}, registered=())

        assert len(_forms(fakes)) == 1
        assert updates(fakes)[-1]["lastError"] == LAST_ERROR_RESERVATION_FULL

    def test_a_reservation_with_a_free_slot_takes_the_existing_refusal_path(self, monkeypatch, aws):
        row = creating_row(capacityReservationId=CR_FULL)

        result, fakes = _run(monkeypatch, row, EdhApiError("GENERIC_ERROR"),
                             records={CR_FULL: ec2_reservation(CR_FULL)})

        assert len(_forms(fakes)) == 1
        assert updates(fakes)[-1]["lastError"] == LAST_ERROR_EDH_REFUSED

    def test_a_reservation_that_cannot_be_read_takes_the_existing_refusal_path(self, monkeypatch, aws):
        row = creating_row(capacityReservationId=CR_FULL)

        result, fakes = _run(monkeypatch, row, EdhCapacityError("no reserved capacity available"),
                             failure=client_error("RequestLimitExceeded"))

        assert len(_forms(fakes)) == 1
        assert updates(fakes)[-1]["lastError"] == LAST_ERROR_CAPACITY_UNAVAILABLE

    def test_a_reservation_ec2_no_longer_holds_takes_the_existing_refusal_path(self, monkeypatch, aws):
        row = creating_row(capacityReservationId=CR_FULL)

        result, fakes = _run(monkeypatch, row, EdhApiError("GENERIC_ERROR"), records={})

        assert len(_forms(fakes)) == 1
        assert updates(fakes)[-1]["lastError"] == LAST_ERROR_EDH_REFUSED


@pytest.mark.unit
class TestNoRetryWhenTheOutcomeIsUnknown:
    def test_a_lost_create_answer_is_never_retried_and_reads_no_reservation(self, monkeypatch, aws):
        row = creating_row(capacityReservationId=CR_FULL)

        with pytest.raises(EdhOutcomeUnknownError):
            _run(monkeypatch, row, EdhOutcomeUnknownError("read timeout"), records={CR_FULL: FULL})

    def test_a_lost_create_answer_leaves_the_row_and_makes_one_create(self, monkeypatch, aws):
        row = creating_row(capacityReservationId=CR_FULL)
        edh = FakeEdhClient(create_desktop=EdhOutcomeUnknownError("read timeout"),
                            find_desktop_by_name=Sequence(None))
        fakes = install_worker_fakes(monkeypatch, row=row, edh=edh)
        fakes.reservations = install_reservation_fakes(monkeypatch, records={CR_FULL: FULL},
                                                       registered=[(CR_FULL, {})])

        with pytest.raises(EdhOutcomeUnknownError):
            fakes.module.lambda_handler(worker_event(row), None)

        assert len(_forms(fakes)) == 1
        assert fakes.reservations.ec2.calls == []
        assert _fallback_write(fakes) is None

    def test_a_lost_create_answer_on_the_last_attempt_orphans_without_a_reservation_read(self, monkeypatch, aws):
        row = creating_row(capacityReservationId=CR_FULL, launchAttempts=MAX_LAUNCH_ATTEMPTS - 1)
        # A retried launch probes for the desktop before it creates one; EDH lists none.
        edh = FakeEdhClient(create_desktop=EdhOutcomeUnknownError("read timeout"), find_desktop_by_name=None)
        fakes = install_worker_fakes(monkeypatch, row=row, edh=edh)
        fakes.reservations = install_reservation_fakes(monkeypatch, records={CR_FULL: FULL},
                                                       registered=[(CR_FULL, {})])

        result = fakes.module.lambda_handler(worker_event(row), None)

        assert result["state"] == "ORPHANED"
        assert len(_forms(fakes)) == 1
        assert updates(fakes)[-1]["lastError"] == LAST_ERROR_LAUNCH_ERROR
        assert fakes.reservations.ec2.calls == []

    def test_an_unreachable_edh_is_transient_and_reads_no_reservation(self, monkeypatch, aws):
        row = creating_row(capacityReservationId=CR_FULL)

        with pytest.raises(EdhUnavailableError):
            _run(monkeypatch, row, EdhUnavailableError("connect timeout"), records={CR_FULL: FULL})

    def test_a_refusal_of_an_on_demand_row_reads_no_reservation(self, monkeypatch, aws):
        result, fakes = _run(monkeypatch, creating_row(), EdhCapacityError("unable to find available capacity"),
                             records={CR_FULL: FULL})

        assert len(_forms(fakes)) == 1
        assert fakes.reservations.ec2.calls == []
        assert updates(fakes)[-1]["lastError"] == LAST_ERROR_CAPACITY_UNAVAILABLE
