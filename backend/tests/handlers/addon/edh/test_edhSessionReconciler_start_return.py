# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The session sweep records why a start EDH accepted ended stopped (#1730).

A pass that moves a CREATING row carrying ``startAcceptedAt`` to STOPPED reads its instance's EC2 state reason and
writes the code it implies, with a ``startFailure``, in the same conditional write that moves the row:
``Server.InsufficientInstanceCapacity`` gives CAPACITY_UNAVAILABLE, another code START_FAILED with it, a read that
fails START_FAILED without one, and a shutdown a user or the instance initiated nothing. The row stays STOPPED, keeps
its secret container and is never deleted or orphaned for it; the key the start minted is revoked as any stopped
desktop's is. No other pass reads EC2."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.edh import edhSessionReconciler as rec
from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap
from backend.backend.handlers.addon.edh.common.edhSessionState import start_stamp
from backend.tests.handlers.addon.edh.test_edhSessionReconciler import (
    NOW, _conditions_for, _desktop, _row, _run, _updates_for, _written_for,
)

INSTANCE_ID = "i-0123456789abcdef0"
ZONE = "us-west-2b"


def _accepted(bootstrap_id="a", **overrides):
    """A CREATING row a start EDH accepted ten minutes ago left, its key kept."""
    stamp = start_stamp(NOW - timedelta(minutes=10))
    return _row(bootstrap_id, **{"state": rec.STATE_CREATING, "edhState": "pending", "connectionAvailable": False,
                                 "instanceId": INSTANCE_ID, "startAcceptedAt": stamp, "updatedAt": stamp,
                                 **overrides})


def _described(code):
    instance = {"InstanceId": INSTANCE_ID, "State": {"Name": "stopped"}, "Placement": {"AvailabilityZone": ZONE}}
    if code is not None:
        instance["StateReason"] = {"Code": code, "Message": f"{code}: details"}
    return {"Reservations": [{"Instances": [instance]}]}


@pytest.mark.unit
class TestTheSweepRecordsWhyTheStartEndedStopped:
    def test_no_capacity_is_recorded_capacity_unavailable_with_the_code_and_zone(self):
        summary, revoke_fn, persist, _pager, client_factory, _delete = _run(
            [_accepted()], [_desktop("sess-a", "stopped")],
            instances=_described("Server.InsufficientInstanceCapacity"))
        written = _written_for(persist, "a")
        assert written["state"] == rec.STATE_STOPPED
        assert written["lastError"] == "CAPACITY_UNAVAILABLE"
        assert written["startFailure"] == {"errorCode": "Server.InsufficientInstanceCapacity",
                                           "availabilityZone": ZONE, "observedAt": NOW.isoformat()}
        assert written["startAcceptedAt"] is None
        client_factory.ec2.describe_instances.assert_called_once_with(InstanceIds=[INSTANCE_ID])

    def test_the_code_goes_in_the_same_write_that_moves_the_row(self):
        row = _accepted()
        _summary_, _revoke, persist, *_ = _run([row], [_desktop("sess-a", "stopped")],
                                               instances=_described("Server.InsufficientInstanceCapacity"))
        first = _updates_for(persist, "a")[0]
        assert first["state"] == rec.STATE_STOPPED and first["lastError"] == "CAPACITY_UNAVAILABLE"
        assert _conditions_for(persist, "a")[0] == row["updatedAt"]

    def test_the_row_stays_stopped_keeps_its_container_and_loses_the_starts_key(self):
        _summary_, revoke_fn, persist, *_ = _run([_accepted()], [_desktop("sess-a", "stopped")],
                                                 instances=_described("Server.InsufficientInstanceCapacity"))
        written = _written_for(persist, "a")
        assert written["state"] == rec.STATE_STOPPED
        assert rec.EXPIRES_AT_FIELD not in written
        revoke_fn.assert_called_once()
        assert revoke_fn.call_args.kwargs["delete_secret"] is False

    def test_a_reservation_bound_row_is_recorded_reservation_full(self):
        _summary_, _revoke, persist, *_ = _run([_accepted(capacityReservationId="cr-0123456789abcdef0")],
                                               [_desktop("sess-a", "stopped")],
                                               instances=_described("Server.InsufficientInstanceCapacity"))
        assert _written_for(persist, "a")["lastError"] == "RESERVATION_FULL"

    def test_another_server_code_is_recorded_start_failed_with_it(self):
        _summary_, _revoke, persist, *_ = _run([_accepted()], [_desktop("sess-a", "stopped")],
                                               instances=_described("Server.InternalError"))
        written = _written_for(persist, "a")
        assert written["lastError"] == "START_FAILED"
        assert written["startFailure"]["errorCode"] == "Server.InternalError"

    @pytest.mark.parametrize("code", ["Client.UserInitiatedShutdown", "Client.InstanceInitiatedShutdown"])
    def test_a_shutdown_a_user_or_the_instance_initiated_records_no_failure(self, code):
        _summary_, _revoke, persist, *_ = _run([_accepted()], [_desktop("sess-a", "stopped")],
                                               instances=_described(code))
        written = _written_for(persist, "a")
        assert written["state"] == rec.STATE_STOPPED
        assert written.get("lastError") is None and written.get("startFailure") is None

    def test_a_read_that_fails_records_the_start_failed_without_a_code(self):
        denied = ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "denied"}}, "DescribeInstances")
        _summary_, _revoke, persist, *_ = _run([_accepted()], [_desktop("sess-a", "stopped")], instances=denied)
        written = _written_for(persist, "a")
        assert written["state"] == rec.STATE_STOPPED
        assert written["lastError"] == "START_FAILED"
        assert written["startFailure"] == {"observedAt": NOW.isoformat()}

    def test_the_instance_edh_lists_is_read_when_the_row_has_none_yet(self):
        with patch.object(bootstrap, "bind_session_secret", MagicMock(return_value={})):
            _summary_, _revoke, _persist, _pager, client_factory, _delete = _run(
                [_accepted(instanceId=None)], [_desktop("sess-a", "stopped", instance_id=INSTANCE_ID)],
                instances=_described("Server.InsufficientInstanceCapacity"))
        client_factory.ec2.describe_instances.assert_called_once_with(InstanceIds=[INSTANCE_ID])


@pytest.mark.unit
class TestNoOtherPassReadsEc2:
    @pytest.mark.parametrize("row, edh_state", [
        (_row("a", state=rec.STATE_ACTIVE), "stopped"),
        (_row("a", state=rec.STATE_CREATING, edhState="pending", connectionAvailable=False), "stopped"),
        (_accepted(), "pending"),
        (_accepted(), "running"),
        (_accepted(stopRequestedAt=(NOW - timedelta(minutes=1)).isoformat()), "stopped"),
    ], ids=["an active row stopped", "a new launch stopped", "a start still booting", "a start that ran",
            "a stop through VAMS"])
    def test_ec2_is_not_read(self, row, edh_state):
        _summary_, _revoke, persist, _pager, client_factory, _delete = _run(
            [row], [_desktop("sess-a", edh_state)], instances=_described("Server.InsufficientInstanceCapacity"))
        client_factory.ec2.describe_instances.assert_not_called()
        assert _written_for(persist, "a").get("lastError") not in ("CAPACITY_UNAVAILABLE", "START_FAILED")
