# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The launch worker's start of a stopped desktop (payload ``action`` ``start``): EDH's start is sent once per
start request, under the start profile; a start EDH accepts moves the row to CREATING and finishes the
credential; a start EDH refuses -- for capacity, for a full capacity reservation, or for any other reason --
leaves the row STOPPED with its code and ``startFailure``, takes back the key the start minted and releases the
claim, and never deletes, terminates or orphans the desktop; a start whose answer is lost is followed by reads of
the desktop's state until EDH reports it past stopped, and one EDH still reports stopped at the end is left
STOPPED, marked and claimed, for the sweep. The row is the faked table's one stored item, written as DynamoDB
writes it."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

import backend.backend.handlers.addon.edh.edhLaunchWorker as worker
from backend.backend.handlers.addon.edh.common import edhCapacityReservations as reservations
from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap
from backend.backend.handlers.addon.edh.common.edhClient import (
    EDH_START_BUDGET_SECONDS, EdhApiError, EdhAuthError, EdhCapacityError, EdhContractError, EdhOutcomeUnknownError,
    EdhQuotaError, EdhUnavailableError,
)
from backend.backend.handlers.addon.edh.common.edhSessionState import (
    START_IN_PROGRESS_SECONDS, start_stamp,
)
from backend.tests.handlers.addon.edh.edhRowWriteTestSupport import ConditionalRowTable
from backend.tests.handlers.addon.edh.edhServiceTestSupport import SESSION_OWN, FakeEdhClient, Sequence, own_row
from backend.tests.handlers.addon.edh.edhWorkerTestSupport import edh_proxy_records, install_worker_fakes

INSTANCE_ID = "i-0123456789abcdef0"
RESERVATION = "cr-0123456789abcdef0"
# EDH's refusal of a start EC2 found no capacity for, as start_virtual_desktop.py words it.
ICE_TEXT = ("Unable to start instance due to An error occurred (InsufficientInstanceCapacity) when calling the "
            "StartInstances operation (reached max retries: 4): We currently do not have sufficient g5.16xlarge "
            "capacity in the Availability Zone you requested (us-west-2b).")
RESERVATION_TEXT = ("Unable to start instance due to An error occurred (ReservationCapacityExceeded) when calling "
                    "the StartInstances operation: The requested reservation does not have sufficient capacity.")
FULL_RESERVATION = {"state": "active", "availableInstanceCount": 0, "totalInstanceCount": 1}
# The states of a reservation that has ended, and the reservation specification VAMS gives a stopped desktop's
# instance launched into one before its start: on-demand capacity.
ENDED_STATES = ("expired", "cancelled", "failed", "notFound")
ON_DEMAND_SPECIFICATION = {"CapacityReservationPreference": "open"}
# EC2's refusal of the move, as botocore raises it; its text must never reach the row.
MODIFY_REFUSAL = ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "You are not authorized to perform "
                                        "this operation. Encoded authorization failure message: secret-detail"}},
                             "ModifyInstanceCapacityReservationAttributes")


def _stamp(seconds_ago=5):
    return start_stamp(datetime.now(timezone.utc) - timedelta(seconds=seconds_ago))


def _start_row(stamp, **overrides):
    """A stopped desktop the workstation service claimed for a start and minted a key for."""
    row = own_row(state="STOPPED", edhState="stopped", connectionAvailable=False, instanceId=INSTANCE_ID,
                  startRequestedAt=stamp, activity="starting", apiKeyId="key-2",
                  secretName="vams/edh-session/x", secretBoundInstanceId="", keyExpiresAt="2026-09-09T04:00:00Z")
    row.update(overrides)
    return row


def _context(seconds=360):
    return SimpleNamespace(get_remaining_time_in_millis=lambda: seconds * 1000)


class _Clock:
    """A monotonic clock the worker's pauses advance."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _run(monkeypatch, row, *, edh=None, context=None, zone="us-west-2b", describe_error=None, facts=None,
         revoke_error=None, deliveries=1, event_stamp=None, modify=None, read_error=None):
    edh = edh if edh is not None else FakeEdhClient()
    fakes = install_worker_fakes(monkeypatch, edh=edh)
    table = ConditionalRowTable(**{k: v for k, v in row.items() if k != "bootstrapId"})
    fakes.table.update_item.side_effect = table
    fakes.table.get_item.side_effect = table.read
    fakes.stored = table
    clock = _Clock()
    monkeypatch.setattr(worker, "_monotonic", clock.monotonic)
    fakes.sleep = MagicMock(side_effect=clock.sleep)
    monkeypatch.setattr(worker, "_sleep", fakes.sleep)
    ec2 = MagicMock()
    if describe_error is not None:
        ec2.describe_instances.side_effect = describe_error
    else:
        ec2.describe_instances.return_value = {
            "Reservations": [{"Instances": [{"InstanceId": INSTANCE_ID, "Placement": {"AvailabilityZone": zone}}]}]}
    if modify is not None:
        # EC2's answer to the move off an ended reservation: an error it raises, or a callable answering it.
        ec2.modify_instance_capacity_reservation_attributes.side_effect = modify
    monkeypatch.setattr(worker, "ec2", ec2)
    fakes.ec2 = ec2
    read_live = MagicMock(return_value={RESERVATION: facts or FULL_RESERVATION})
    if read_error is not None:
        read_live.side_effect = read_error
    monkeypatch.setattr(reservations, "read_live", read_live)
    fakes.read_live = read_live
    revoke = MagicMock(side_effect=revoke_error, return_value=dict(bootstrap.CLEARED_KEY_ATTRIBUTES))
    if revoke_error is None:
        revoke.side_effect = None
    others = MagicMock()
    bind = MagicMock(return_value={"secretBoundInstanceId": INSTANCE_ID})
    monkeypatch.setattr(bootstrap, "revoke_session", revoke)
    monkeypatch.setattr(bootstrap, "delete_other_session_keys", others)
    monkeypatch.setattr(bootstrap, "bind_session_secret", bind)
    monkeypatch.setattr(bootstrap, "session_bootstrap_enabled", True)
    fakes.revoke, fakes.others, fakes.bind = revoke, others, bind
    event = {"action": "start", "bootstrapId": row["bootstrapId"],
             "startRequestedAt": event_stamp or row["startRequestedAt"]}
    results = [worker.lambda_handler(event, context if context is not None else _context())
               for _ in range(deliveries)]
    fakes.results = results
    return results[0], fakes


def _stored(fakes):
    return fakes.stored.item


@pytest.mark.unit
class TestAStartEdhAccepts:
    def test_the_row_moves_to_creating_and_the_credential_is_finished(self, monkeypatch):
        stamp = _stamp()
        result, fakes = _run(monkeypatch, _start_row(stamp, lastError="CAPACITY_UNAVAILABLE",
                                                     startFailure={"observedAt": "2026-10-23T15:00:00+00:00"},
                                                     leaseStoppedAt="2026-10-23T14:00:00+00:00"))
        assert result["outcome"] == "started"
        assert fakes.edh.calls_to("start_desktop") == [(own_row()["edhUid"], SESSION_OWN)]
        stored = _stored(fakes)
        assert stored["state"] == "CREATING" and stored["edhState"] == "pending"
        assert stored["activity"] == "starting"
        assert stored["startRequestedAt"] is None and stored["startSentFor"] == stamp
        # An accepted start clears the code and the failure of the earlier start, and the lease-stop stamp.
        assert stored["lastError"] is None and stored["startFailure"] is None and stored["leaseStoppedAt"] is None
        fakes.bind.assert_called_once()
        assert fakes.bind.call_args.args[1] == INSTANCE_ID
        assert stored["secretBoundInstanceId"] == INSTANCE_ID
        fakes.others.assert_called_once()
        assert fakes.others.call_args.kwargs["keep"] == "key-2"
        fakes.revoke.assert_not_called()

    def test_the_start_is_sent_under_the_start_profile_with_its_whole_budget(self, monkeypatch):
        _, fakes = _run(monkeypatch, _start_row(_stamp()))
        profiles = [call.kwargs.get("profile") for call in fakes.client_factory.call_args_list]
        assert profiles == ["start"]
        deadline = fakes.client_factory.call_args.kwargs["deadline"]
        assert deadline == pytest.approx(1000.0 + EDH_START_BUDGET_SECONDS)
        assert EDH_START_BUDGET_SECONDS >= 240

    def test_the_start_is_audited_as_an_edh_proxy_call(self, monkeypatch):
        _, fakes = _run(monkeypatch, _start_row(_stamp()))
        assert edh_proxy_records(fakes) == [{"operation": "workstationStart", "edhUid": own_row()["edhUid"],
                                             "edhMethod": "PUT", "edhRoute": "/api/dcv/virtual_desktops/start",
                                             "edhStatus": "ok"}]


@pytest.mark.unit
class TestAStartEdhRefusesForCapacity:
    def _refused(self, monkeypatch, **kwargs):
        edh = FakeEdhClient(start_desktop=EdhCapacityError("no capacity", status=500, body_text=ICE_TEXT))
        return _run(monkeypatch, _start_row(_stamp(), **kwargs.pop("row", {})), edh=edh, **kwargs)

    def test_the_row_stays_stopped_with_the_capacity_code_and_the_failure(self, monkeypatch):
        result, fakes = self._refused(monkeypatch)
        stored = _stored(fakes)
        assert result["outcome"] == "refused"
        assert stored["state"] == "STOPPED"
        assert stored["lastError"] == "CAPACITY_UNAVAILABLE"
        assert stored["startFailure"]["errorCode"] == "InsufficientInstanceCapacity"
        assert stored["startFailure"]["availabilityZone"] == "us-west-2b"
        assert datetime.fromisoformat(stored["startFailure"]["observedAt"]).tzinfo is not None
        fakes.ec2.describe_instances.assert_called_once_with(InstanceIds=[INSTANCE_ID])

    def test_the_key_is_taken_back_and_the_claim_released(self, monkeypatch):
        _, fakes = self._refused(monkeypatch)
        stored = _stored(fakes)
        fakes.revoke.assert_called_once()
        assert fakes.revoke.call_args.kwargs == {"delete_secret": False, "actor": own_row()["userId"],
                                                 "every_key": False}
        assert not stored["apiKeyId"]
        assert stored["startRequestedAt"] is None and stored["activity"] is None
        fakes.others.assert_not_called()
        fakes.bind.assert_not_called()

    def test_the_desktop_is_never_deleted_stopped_or_orphaned(self, monkeypatch):
        _, fakes = self._refused(monkeypatch)
        names = [name for name, _ in fakes.edh.calls]
        assert names == ["start_desktop"]
        assert _stored(fakes)["state"] == "STOPPED"
        assert "expiresAt" not in _stored(fakes)
        # The secret container stays for the next start.
        assert fakes.revoke.call_args.kwargs["delete_secret"] is False

    def test_an_instance_ec2_does_not_read_names_no_zone(self, monkeypatch):
        _, fakes = self._refused(monkeypatch, describe_error=RuntimeError("throttled"))
        failure = _stored(fakes)["startFailure"]
        assert "availabilityZone" not in failure
        assert failure["errorCode"] == "InsufficientInstanceCapacity"
        assert _stored(fakes)["lastError"] == "CAPACITY_UNAVAILABLE"

    def test_a_key_that_cannot_be_taken_back_still_records_the_failure(self, monkeypatch):
        _, fakes = self._refused(monkeypatch, revoke_error=bootstrap.SessionBootstrapError("down"))
        stored = _stored(fakes)
        assert stored["lastError"] == "CAPACITY_UNAVAILABLE"
        assert stored["startRequestedAt"] is None
        # The key stays named, for the sweep to revoke.
        assert stored["apiKeyId"] == "key-2"


@pytest.mark.unit
class TestAStartOfAReservationBoundDesktop:
    def test_a_refusal_while_the_reservation_is_full_records_reservation_full(self, monkeypatch):
        edh = FakeEdhClient(start_desktop=EdhCapacityError("no capacity", status=500, body_text=RESERVATION_TEXT))
        _, fakes = _run(monkeypatch, _start_row(_stamp(), capacityReservationId=RESERVATION), edh=edh)
        stored = _stored(fakes)
        assert stored["state"] == "STOPPED"
        assert stored["lastError"] == "RESERVATION_FULL"
        assert stored["startFailure"]["errorCode"] == "ReservationCapacityExceeded"
        assert stored["capacityReservationId"] == RESERVATION
        # The refusal is classified on a read of the reservation made after EDH refused (the start's own read,
        # before EDH's start, looks only for a reservation that has ended).
        fakes.read_live.assert_called_with([RESERVATION])
        fakes.revoke.assert_called_once()

    def test_a_refusal_while_the_reservation_has_a_slot_keeps_edhs_classification(self, monkeypatch):
        edh = FakeEdhClient(start_desktop=EdhCapacityError("no capacity", status=500, body_text=ICE_TEXT))
        _, fakes = _run(monkeypatch, _start_row(_stamp(), capacityReservationId=RESERVATION), edh=edh,
                        facts={"state": "active", "availableInstanceCount": 1})
        assert _stored(fakes)["lastError"] == "CAPACITY_UNAVAILABLE"

    def test_a_row_without_a_reservation_reads_none(self, monkeypatch):
        edh = FakeEdhClient(start_desktop=EdhCapacityError("no capacity", status=500, body_text=ICE_TEXT))
        _, fakes = _run(monkeypatch, _start_row(_stamp()), edh=edh)
        fakes.read_live.assert_not_called()


@pytest.mark.unit
class TestAStartIntoAReservationThatHasEnded:
    """A stopped desktop launched into a capacity reservation keeps that reservation as its instance's only
    capacity for life, and EDH leaves it untouched, so once the reservation has ended every start would fail. The
    worker moves the instance to on-demand capacity first, records the fallback, and sends EDH's start; when EC2
    refuses the move, EDH's start is not sent and the row records RESERVATION_ENDED."""

    @pytest.mark.parametrize("ended", ENDED_STATES)
    def test_the_desktop_is_moved_to_on_demand_and_started(self, monkeypatch, ended):
        edh = FakeEdhClient()
        seen_at_move = []

        def move(**kwargs):
            # What EDH had been asked when EC2 was: nothing yet, so the start finds the instance on-demand.
            seen_at_move.append(list(edh.calls))
            return {"Return": True}

        result, fakes = _run(monkeypatch, _start_row(_stamp(), capacityReservationId=RESERVATION), edh=edh,
                             facts={"state": ended}, modify=move)

        assert result["outcome"] == "started"
        fakes.ec2.modify_instance_capacity_reservation_attributes.assert_called_once_with(
            InstanceId=INSTANCE_ID, CapacityReservationSpecification=ON_DEMAND_SPECIFICATION)
        assert seen_at_move == [[]]
        assert fakes.edh.calls_to("start_desktop") == [(own_row()["edhUid"], SESSION_OWN)]
        stored = _stored(fakes)
        assert "capacityReservationId" not in stored
        assert stored["capacityReservationRequested"] == RESERVATION
        assert stored["capacityReservationFallback"] is True
        assert stored["capacityReservationFallbackReason"] == "ended"
        assert stored["state"] == "CREATING" and stored["lastError"] is None
        fakes.read_live.assert_any_call([RESERVATION])

    @pytest.mark.parametrize("facts", [
        {"state": "active", "availableInstanceCount": 1, "totalInstanceCount": 2},
        {"state": "pending"},
        {"state": "scheduled"},
        {"state": "unknown"},
    ])
    def test_a_reservation_that_has_not_ended_changes_nothing(self, monkeypatch, facts):
        result, fakes = _run(monkeypatch, _start_row(_stamp(), capacityReservationId=RESERVATION), facts=facts)
        assert result["outcome"] == "started"
        fakes.ec2.modify_instance_capacity_reservation_attributes.assert_not_called()
        stored = _stored(fakes)
        assert stored["capacityReservationId"] == RESERVATION
        assert not stored.get("capacityReservationFallback")
        assert len(fakes.edh.calls_to("start_desktop")) == 1

    def test_a_reservation_that_could_not_be_read_changes_nothing(self, monkeypatch):
        result, fakes = _run(monkeypatch, _start_row(_stamp(), capacityReservationId=RESERVATION),
                             read_error=reservations.CapacityReservationReadError("ThrottlingException"))
        assert result["outcome"] == "started"
        fakes.ec2.modify_instance_capacity_reservation_attributes.assert_not_called()
        assert _stored(fakes)["capacityReservationId"] == RESERVATION
        assert len(fakes.edh.calls_to("start_desktop")) == 1

    def test_a_full_active_reservation_still_records_reservation_full(self, monkeypatch):
        edh = FakeEdhClient(start_desktop=EdhCapacityError("no capacity", status=500, body_text=RESERVATION_TEXT))
        _, fakes = _run(monkeypatch, _start_row(_stamp(), capacityReservationId=RESERVATION), edh=edh)
        fakes.ec2.modify_instance_capacity_reservation_attributes.assert_not_called()
        assert _stored(fakes)["lastError"] == "RESERVATION_FULL"
        assert _stored(fakes)["capacityReservationId"] == RESERVATION

    def test_a_move_ec2_refuses_records_reservation_ended_and_sends_no_start(self, monkeypatch):
        result, fakes = _run(monkeypatch, _start_row(_stamp(), capacityReservationId=RESERVATION),
                             facts={"state": "expired"}, modify=MODIFY_REFUSAL)
        stored = _stored(fakes)
        assert result["outcome"] == "refused" and result["lastError"] == "RESERVATION_ENDED"
        assert fakes.edh.calls_to("start_desktop") == []
        assert fakes.client_factory.call_args_list == []
        assert stored["state"] == "STOPPED" and stored["lastError"] == "RESERVATION_ENDED"
        assert stored["startFailure"]["errorCode"] == "UnauthorizedOperation"
        assert stored["startFailure"]["availabilityZone"] == "us-west-2b"
        assert "secret-detail" not in str(stored["startFailure"])
        # The row keeps its reservation: nothing moved.
        assert stored["capacityReservationId"] == RESERVATION
        assert not stored.get("capacityReservationFallback")
        # The key is taken back and the claim released, as for any start that did not happen.
        fakes.revoke.assert_called_once()
        assert stored["activity"] is None and stored["startRequestedAt"] is None

    def test_a_move_that_fails_without_an_aws_answer_names_no_error_code(self, monkeypatch):
        _, fakes = _run(monkeypatch, _start_row(_stamp(), capacityReservationId=RESERVATION),
                        facts={"state": "cancelled"},
                        modify=EndpointConnectionError(endpoint_url="https://ec2.us-west-2.amazonaws.com"))
        stored = _stored(fakes)
        assert stored["lastError"] == "RESERVATION_ENDED"
        assert "errorCode" not in stored["startFailure"]
        assert fakes.edh.calls_to("start_desktop") == []

    def test_a_row_without_an_instance_reads_no_reservation_before_the_start(self, monkeypatch):
        row = _start_row(_stamp(), capacityReservationId=RESERVATION)
        row.pop("instanceId")
        result, fakes = _run(monkeypatch, row, facts={"state": "expired"})
        assert result["outcome"] == "started"
        fakes.ec2.modify_instance_capacity_reservation_attributes.assert_not_called()
        fakes.read_live.assert_not_called()

    def test_a_second_delivery_of_the_same_start_moves_nothing(self, monkeypatch):
        _, fakes = _run(monkeypatch, _start_row(_stamp(), capacityReservationId=RESERVATION),
                        facts={"state": "expired"}, deliveries=2)
        assert fakes.ec2.modify_instance_capacity_reservation_attributes.call_count == 1
        assert fakes.results[1]["outcome"] == "settled"
        assert len(fakes.edh.calls_to("start_desktop")) == 1


@pytest.mark.unit
class TestAStartEdhRefusesOtherwise:
    @pytest.mark.parametrize("error", [
        EdhApiError("refused", status=500, body_text="Unable to start instance due to An error occurred "
                    "(IncorrectInstanceState) when calling the StartInstances operation: busy"),
        EdhQuotaError("quota", status=500, body_text="budget allocation for this project has been exceeded"),
        EdhAuthError("root key", status=401),
        EdhUnavailableError("connect timeout"),
        EdhUnavailableError("no healthy targets", status=503),
    ])
    def test_the_row_stays_stopped_with_start_failed(self, monkeypatch, error):
        result, fakes = _run(monkeypatch, _start_row(_stamp()), edh=FakeEdhClient(start_desktop=error))
        stored = _stored(fakes)
        assert result["outcome"] == "refused"
        assert stored["state"] == "STOPPED" and stored["lastError"] == "START_FAILED"
        assert stored["activity"] is None and stored["startRequestedAt"] is None
        fakes.revoke.assert_called_once()
        assert [name for name, _ in fakes.edh.calls] == ["start_desktop"]

    def test_the_failure_carries_what_edhs_answer_named(self, monkeypatch):
        error = EdhApiError("refused", status=500, body_text="Unable to start instance due to An error occurred "
                            "(IncorrectInstanceState) when calling the StartInstances operation: busy")
        _, fakes = _run(monkeypatch, _start_row(_stamp()), edh=FakeEdhClient(start_desktop=error))
        failure = _stored(fakes)["startFailure"]
        assert failure["errorCode"] == "IncorrectInstanceState"
        assert failure["availabilityZone"] == "us-west-2b"
        # EDH's text itself is never stored.
        assert "busy" not in str(failure)


@pytest.mark.unit
class TestAStartWhoseAnswerIsLost:
    @pytest.mark.parametrize("lost", [
        EdhOutcomeUnknownError("read timeout"),
        EdhUnavailableError("gateway timeout", status=504),
        EdhUnavailableError("bad gateway", status=502),
        EdhContractError("not json", status=200),
    ])
    def test_a_desktop_edh_then_reports_pending_is_an_accepted_start(self, monkeypatch, lost):
        edh = FakeEdhClient(start_desktop=lost, get_desktop_states=Sequence(
            [{"session_uuid": SESSION_OWN, "session_state": "stopped"}],
            [{"session_uuid": SESSION_OWN, "session_state": "pending", "instance_id": INSTANCE_ID}]))
        result, fakes = _run(monkeypatch, _start_row(_stamp()), edh=edh)
        assert result["outcome"] == "started"
        assert _stored(fakes)["state"] == "CREATING" and _stored(fakes)["edhState"] == "pending"
        assert len(fakes.edh.calls_to("start_desktop")) == 1
        assert len(fakes.edh.calls_to("get_desktop_states")) == 2
        fakes.revoke.assert_not_called()
        fakes.others.assert_called_once()

    def test_the_reads_are_fifteen_seconds_apart(self, monkeypatch):
        edh = FakeEdhClient(start_desktop=EdhOutcomeUnknownError("lost"), get_desktop_states=Sequence(
            [{"session_uuid": SESSION_OWN, "session_state": "stopped"}],
            [{"session_uuid": SESSION_OWN, "session_state": "stopped"}],
            [{"session_uuid": SESSION_OWN, "session_state": "running"}]))
        _, fakes = _run(monkeypatch, _start_row(_stamp()), edh=edh)
        assert [call.args for call in fakes.sleep.call_args_list] == [(15.0,), (15.0,)]
        assert _stored(fakes)["state"] == "ACTIVE"
        assert _stored(fakes)["activity"] is None

    def test_a_desktop_edh_still_reports_stopped_at_the_end_is_left_for_the_sweep(self, monkeypatch):
        stamp = _stamp()
        edh = FakeEdhClient(start_desktop=EdhOutcomeUnknownError("lost"),
                            get_desktop_states=[{"session_uuid": SESSION_OWN, "session_state": "stopped"}])
        result, fakes = _run(monkeypatch, _start_row(stamp), edh=edh)
        stored = _stored(fakes)
        assert result["outcome"] == "unresolved"
        assert stored["state"] == "STOPPED"
        # The claim and the mark stay, and so does the key: the start may still happen in EDH.
        assert stored["startRequestedAt"] == stamp and stored["activity"] == "starting"
        assert stored["apiKeyId"] == "key-2"
        assert stored.get("lastError") is None
        fakes.revoke.assert_not_called()
        # EDH's start is never sent a second time.
        assert len(fakes.edh.calls_to("start_desktop")) == 1

    def test_the_reads_stay_inside_the_lambdas_time_and_the_starts_window(self, monkeypatch):
        edh = FakeEdhClient(start_desktop=EdhOutcomeUnknownError("lost"),
                            get_desktop_states=[{"session_uuid": SESSION_OWN, "session_state": "stopped"}])
        _, fakes = _run(monkeypatch, _start_row(_stamp(10)), edh=edh, context=_context(360))
        # The clock starts at 1000 and only the pauses advance it: every read starts before the deadline less
        # the finish reserve, and the last pause ends inside it.
        slept = sum(call.args[0] for call in fakes.sleep.call_args_list)
        assert slept <= 360 - worker.START_FINISH_RESERVE_SECONDS
        assert slept >= 360 - worker.START_FINISH_RESERVE_SECONDS - 2 * worker.START_POLL_INTERVAL_SECONDS

    def test_a_desktop_edh_no_longer_has_ends_the_reads(self, monkeypatch):
        edh = FakeEdhClient(start_desktop=EdhOutcomeUnknownError("lost"), get_desktop_states=[])
        result, fakes = _run(monkeypatch, _start_row(_stamp()), edh=edh)
        assert result["outcome"] == "unresolved"
        assert len(fakes.edh.calls_to("get_desktop_states")) == 1


@pytest.mark.unit
class TestOneEdhStartPerStartRequest:
    def test_a_second_delivery_of_the_same_start_sends_nothing(self, monkeypatch):
        edh = FakeEdhClient(start_desktop=EdhOutcomeUnknownError("lost"),
                            get_desktop_states=[{"session_uuid": SESSION_OWN, "session_state": "stopped"}])
        _, fakes = _run(monkeypatch, _start_row(_stamp()), edh=edh, deliveries=2)
        assert len(fakes.edh.calls_to("start_desktop")) == 1
        assert fakes.results[1]["outcome"] == "settled"

    def test_a_retried_delivery_after_a_crash_past_the_claim_sends_nothing(self, monkeypatch):
        stamp = _stamp()
        _, fakes = _run(monkeypatch, _start_row(stamp, startSentFor=stamp))
        assert fakes.edh.calls_to("start_desktop") == []

    def test_a_claim_another_delivery_won_sends_nothing(self, monkeypatch):
        stamp = _stamp()
        row = _start_row(stamp)
        fakes = install_worker_fakes(monkeypatch, row=row)
        table = ConditionalRowTable(**{**row, "startSentFor": stamp})
        fakes.table.update_item.side_effect = table
        assert worker.start(dict(row), stamp, _context())["outcome"] == "settled"
        assert fakes.edh.calls_to("start_desktop") == []

    @pytest.mark.parametrize("change", [
        {"startRequestedAt": None}, {"state": "CREATING"}, {"activity": None}, {"state": "DELETED"},
    ])
    def test_a_row_that_left_the_start_is_not_started(self, monkeypatch, change):
        stamp = _stamp()
        result, fakes = _run(monkeypatch, _start_row(stamp, **change), event_stamp=stamp)
        assert result["outcome"] == "settled"
        assert fakes.edh.calls_to("start_desktop") == []

    def test_a_row_another_start_stamped_since_is_not_started(self, monkeypatch):
        result, fakes = _run(monkeypatch, _start_row(_stamp(1)), event_stamp=_stamp(30))
        assert result["outcome"] == "settled"
        assert fakes.edh.calls_to("start_desktop") == []

    def test_a_start_that_reaches_the_worker_too_late_for_its_window_is_not_sent(self, monkeypatch):
        late = START_IN_PROGRESS_SECONDS - EDH_START_BUDGET_SECONDS
        result, fakes = _run(monkeypatch, _start_row(_stamp(late)))
        stored = _stored(fakes)
        assert fakes.edh.calls_to("start_desktop") == []
        assert stored["state"] == "STOPPED" and stored["lastError"] == "START_FAILED"
        assert stored["startRequestedAt"] is None
        fakes.revoke.assert_called_once()

    def test_the_window_holds_the_workers_whole_run(self):
        # A second start is refused for as long as the first one's worker can still act on EDH.
        assert START_IN_PROGRESS_SECONDS >= 360 + 60
