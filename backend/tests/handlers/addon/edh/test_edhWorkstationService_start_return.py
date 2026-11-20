# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The list and get routes record why a start EDH accepted ended stopped (#1730).

When EDH's state read moves a CREATING row carrying ``startAcceptedAt`` to STOPPED, the route reads the instance's
EC2 state reason (``ec2:DescribeInstances``) and writes the code it implies with a ``startFailure`` in the same write:
the record then reads STOPPED with ``lastError`` CAPACITY_UNAVAILABLE and the AWS code and Availability Zone, exactly
as a start EC2 refuses at once does, and a new start is allowed. A shutdown a user or the instance initiated records
nothing; a read that fails records START_FAILED without a code; no other read of a row touches EC2."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

import backend.backend.handlers.addon.edh.edhWorkstationService as service
from backend.backend.handlers.addon.edh.common import edhCapacityReservations as reservations
from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap
from backend.backend.handlers.addon.edh.common.edhSessionState import start_stamp
from backend.tests.handlers.addon.edh.edhRowWriteTestSupport import ConditionalRowTable
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    SESSION_OWN, WORKSTATIONS_PATH, FakeEdhClient, FakeInstanceTypesEc2, install_service_fakes, make_event, own_row,
    response_body, state_record, workstation_path,
)

INSTANCE_ID = "i-0123456789abcdef0"
ZONE = "us-west-2b"


def _accepted(**fields):
    accepted = start_stamp(datetime.now(timezone.utc) - timedelta(minutes=10))
    return own_row(**{"state": "CREATING", "edhState": "pending", "connectionAvailable": False,
                      "instanceId": INSTANCE_ID, "startAcceptedAt": accepted, **fields})


def _instance(code):
    instance = {"InstanceId": INSTANCE_ID, "State": {"Name": "stopped"}, "Placement": {"AvailabilityZone": ZONE}}
    if code is not None:
        instance["StateReason"] = {"Code": code, "Message": f"{code}: details"}
    return instance


def _run(monkeypatch, row, *, action="", code="Server.InsufficientInstanceCapacity", ec2_failure=None,
         edh_state="stopped"):
    ec2 = FakeInstanceTypesEc2(instances={INSTANCE_ID: _instance(code)}, instances_failure=ec2_failure)
    edh = FakeEdhClient(get_desktop_states=[state_record(SESSION_OWN, edh_state)])
    fakes = install_service_fakes(monkeypatch, edh=edh, ec2=ec2)
    table = ConditionalRowTable(**{k: v for k, v in row.items() if k != "bootstrapId"})
    fakes.table.query.return_value = {"Items": [row]}
    fakes.table.update_item.side_effect = table
    fakes.table.get_item.side_effect = table.read
    fakes.stored = table
    monkeypatch.setattr(bootstrap, "revoke_session", MagicMock(return_value=dict(bootstrap.CLEARED_KEY_ATTRIBUTES)))
    monkeypatch.setattr(bootstrap, "bind_session_secret", MagicMock(return_value={}))
    monkeypatch.setattr(reservations, "labels_for", MagicMock(return_value={}))
    if action == "list":
        event = make_event("GET", WORKSTATIONS_PATH)
    else:
        event = make_event("GET", workstation_path(SESSION_OWN, action), path_params={"sessionId": SESSION_OWN})
    return service.lambda_handler(event, MagicMock()), fakes


def _body(response, action):
    body = response_body(response)
    return body["Items"][0] if action == "list" else body


@pytest.mark.unit
class TestTheRouteRecordsWhyTheStartEndedStopped:
    @pytest.mark.parametrize("action", ["list", ""], ids=["list", "get"])
    def test_no_capacity_reads_stopped_with_capacity_unavailable_the_code_and_the_zone(self, monkeypatch, action):
        response, fakes = _run(monkeypatch, _accepted(), action=action)
        assert response["statusCode"] == 200
        item = _body(response, action)
        assert item["state"] == "STOPPED"
        assert item["lastError"] == "CAPACITY_UNAVAILABLE"
        assert item["startFailure"]["errorCode"] == "Server.InsufficientInstanceCapacity"
        assert item["startFailure"]["availabilityZone"] == ZONE
        assert item["activity"] is None
        assert fakes.ec2.instance_requests == [{"InstanceIds": [INSTANCE_ID]}]
        stored = fakes.stored.item
        assert stored["lastError"] == "CAPACITY_UNAVAILABLE"
        assert stored["startAcceptedAt"] is None

    def test_another_code_reads_start_failed_with_it(self, monkeypatch):
        response, _fakes = _run(monkeypatch, _accepted(), code="Server.InternalError")
        body = response_body(response)
        assert (body["lastError"], body["startFailure"]["errorCode"]) == ("START_FAILED", "Server.InternalError")

    def test_a_user_initiated_shutdown_records_no_failure(self, monkeypatch):
        response, _fakes = _run(monkeypatch, _accepted(), code="Client.UserInitiatedShutdown")
        body = response_body(response)
        assert (body["state"], body["lastError"], body["startFailure"]) == ("STOPPED", None, None)

    def test_a_read_that_fails_records_start_failed_without_a_code(self, monkeypatch):
        denied = ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "denied"}}, "DescribeInstances")
        response, _fakes = _run(monkeypatch, _accepted(), ec2_failure=denied)
        body = response_body(response)
        assert body["lastError"] == "START_FAILED"
        assert "errorCode" not in (body["startFailure"] or {})

    def test_a_new_start_is_allowed_after_it(self, monkeypatch):
        _response, fakes = _run(monkeypatch, _accepted())
        stored = dict(fakes.stored.item)
        assert stored["state"] == "STOPPED" and not stored.get("startRequestedAt")
        assert stored.get("activity") is None


@pytest.mark.unit
class TestNoOtherReadTouchesEc2:
    @pytest.mark.parametrize("row, edh_state", [
        (own_row(state="ACTIVE"), "stopped"),
        (own_row(state="CREATING", edhState="pending", connectionAvailable=False), "stopped"),
        (_accepted(), "pending"),
    ], ids=["an active row stopped", "a new launch stopped", "a start still booting"])
    def test_ec2_is_not_read(self, monkeypatch, row, edh_state):
        _response, fakes = _run(monkeypatch, row, edh_state=edh_state)
        assert fakes.ec2.instance_requests == []
