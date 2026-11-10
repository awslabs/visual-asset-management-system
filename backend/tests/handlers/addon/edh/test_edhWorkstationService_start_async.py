# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The start route is asynchronous, as launch is: ``POST /addon/edh/workstations/{sessionId}/start`` keeps every
check and the claim, mints the desktop's key, marks the row ``activity`` starting, hands the start to the launch
worker and answers 202 with the record -- it never calls EDH's start itself, and never answers 409
``RESERVATION_FULL`` (the worker records that). A hand-over that fails takes the key back, releases the claim and
raises. Every record the workstation routes answer carries ``activity`` and ``startFailure``, and a stop through
VAMS stamps the row so a read that still finds the desktop running reports it stopping."""

import json
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
    BOOTSTRAP_OWN, SESSION_OWN, WORKSTATIONS_PATH, FakeEdhClient, install_service_fakes, make_event, own_row,
    response_body, state_record, workstation_path,
)

CREDENTIAL = {"apiKeyId": "key-2", "secretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:x",
              "secretName": "vams/edh-session/x", "keyExpiresAt": "2026-09-09T04:00:00Z",
              "secretBoundInstanceId": ""}
RESERVATION = "cr-0123456789abcdef0"


def _run(monkeypatch, row, *, action="start", method="POST", desktop_states=None, invoke_error=None,
         credential=CREDENTIAL):
    edh = FakeEdhClient(get_desktop_states=desktop_states if desktop_states is not None
                        else [state_record(SESSION_OWN, "stopped")])
    fakes = install_service_fakes(monkeypatch, edh=edh)
    table = ConditionalRowTable(**{k: v for k, v in row.items() if k != "bootstrapId"})
    fakes.table.query.return_value = {"Items": [row]}
    fakes.table.update_item.side_effect = table
    fakes.table.get_item.side_effect = table.read
    fakes.stored = table
    if invoke_error is not None:
        fakes.lambda_client.invoke.side_effect = invoke_error
    revoked = []

    def _revoke(event, row_arg, **kwargs):
        revoked.append(dict(row_arg))
        return dict(bootstrap.CLEARED_KEY_ATTRIBUTES)

    revoke = MagicMock(side_effect=_revoke)
    session = MagicMock(return_value=("provisioned" if credential else "skipped", dict(credential)))
    monkeypatch.setattr(bootstrap, "revoke_session", revoke)
    monkeypatch.setattr(bootstrap, "bootstrap_session", session)
    monkeypatch.setattr(bootstrap, "bind_session_secret", MagicMock(return_value={}))
    monkeypatch.setattr(bootstrap, "delete_other_session_keys", MagicMock())
    read_live = MagicMock(return_value={RESERVATION: {"state": "active", "availableInstanceCount": 0}})
    monkeypatch.setattr(reservations, "read_live", read_live)
    monkeypatch.setattr(reservations, "labels_for", MagicMock(return_value={}))
    fakes.revoke, fakes.session, fakes.read_live, fakes.revoked = revoke, session, read_live, revoked
    if action == "list":
        event = make_event("GET", WORKSTATIONS_PATH)
    else:
        event = make_event(method, workstation_path(SESSION_OWN, action), path_params={"sessionId": SESSION_OWN})
    return service.lambda_handler(event, MagicMock()), fakes


def _stopped(**fields):
    return own_row(state="STOPPED", edhState="stopped", connectionAvailable=False, **fields)


def _invoked_payloads(fakes):
    return [json.loads(call.kwargs["Payload"]) for call in fakes.lambda_client.invoke.call_args_list]


@pytest.mark.unit
class TestTheStartRouteHandsTheStartToTheWorker:
    def test_it_answers_202_with_the_record_and_makes_no_edh_start(self, monkeypatch):
        response, fakes = _run(monkeypatch, _stopped())
        assert response["statusCode"] == 202
        assert fakes.edh.calls_to("start_desktop") == []
        body = response_body(response)
        assert body["state"] == "STOPPED"
        assert body["activity"] == "starting"
        assert body["sessionBootstrap"] == "provisioned"

    def test_it_invokes_the_worker_asynchronously_with_the_start_payload(self, monkeypatch):
        response, fakes = _run(monkeypatch, _stopped())
        call = fakes.lambda_client.invoke.call_args
        assert call.kwargs["InvocationType"] == "Event"
        assert call.kwargs["FunctionName"] == service.launch_worker_function_name
        stamp = fakes.stored.item["startRequestedAt"]
        assert _invoked_payloads(fakes) == [{"action": "start", "bootstrapId": BOOTSTRAP_OWN,
                                             "startRequestedAt": stamp}]

    def test_the_row_holds_the_claim_the_mark_and_the_key_before_the_worker_is_invoked(self, monkeypatch):
        seen = {}
        response, fakes = None, None

        def invoked(**kwargs):
            seen.update(fakes.stored.item)
            return {"StatusCode": 202}

        edh = FakeEdhClient(get_desktop_states=[state_record(SESSION_OWN, "stopped")])
        fakes = install_service_fakes(monkeypatch, edh=edh)
        row = _stopped()
        table = ConditionalRowTable(**{k: v for k, v in row.items() if k != "bootstrapId"})
        fakes.table.query.return_value = {"Items": [row]}
        fakes.table.update_item.side_effect = table
        fakes.table.get_item.side_effect = table.read
        fakes.stored = table
        fakes.lambda_client.invoke.side_effect = invoked
        monkeypatch.setattr(bootstrap, "bootstrap_session", MagicMock(return_value=("provisioned", dict(CREDENTIAL))))
        monkeypatch.setattr(reservations, "labels_for", MagicMock(return_value={}))
        response = service.lambda_handler(
            make_event("POST", workstation_path(SESSION_OWN, "start"), path_params={"sessionId": SESSION_OWN}),
            MagicMock())
        assert response["statusCode"] == 202
        assert seen["activity"] == "starting"
        assert seen["apiKeyId"] == "key-2"
        assert seen["startRequestedAt"]
        assert seen["state"] == "STOPPED"

    def test_a_start_that_minted_no_key_is_handed_over_all_the_same(self, monkeypatch):
        response, fakes = _run(monkeypatch, _stopped(), credential={})
        assert response["statusCode"] == 202
        assert response_body(response)["sessionBootstrap"] == "skipped"
        assert len(_invoked_payloads(fakes)) == 1

    def test_a_stop_stamp_goes_with_the_start(self, monkeypatch):
        stopped_at = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        _, fakes = _run(monkeypatch, _stopped(stopRequestedAt=stopped_at))
        assert fakes.stored.item["stopRequestedAt"] is None

    def test_an_earlier_start_failure_stays_until_the_worker_records_an_accepted_start(self, monkeypatch):
        failure = {"errorCode": "InsufficientInstanceCapacity", "observedAt": "2026-10-23T15:19:30+00:00"}
        response, fakes = _run(monkeypatch, _stopped(lastError="CAPACITY_UNAVAILABLE", startFailure=failure))
        assert fakes.stored.item["lastError"] == "CAPACITY_UNAVAILABLE"
        body = response_body(response)
        assert body["lastError"] == "CAPACITY_UNAVAILABLE" and body["startFailure"] == failure

    def test_a_reservation_bound_start_reads_no_reservation_and_is_handed_over(self, monkeypatch):
        response, fakes = _run(monkeypatch, _stopped(capacityReservationId=RESERVATION))
        assert response["statusCode"] == 202
        assert "code" not in response_body(response)
        fakes.read_live.assert_not_called()


@pytest.mark.unit
class TestAHandOverThatFails:
    def test_the_key_is_taken_back_the_claim_released_and_the_request_fails(self, monkeypatch):
        error = ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "slow"}}, "Invoke")
        response, fakes = _run(monkeypatch, _stopped(), invoke_error=error)
        assert response["statusCode"] == 500
        fakes.revoke.assert_called_once()
        assert fakes.revoke.call_args.kwargs["every_key"] is False
        assert fakes.revoked[0]["apiKeyId"] == "key-2"
        stored = fakes.stored.item
        assert stored["startRequestedAt"] is None and stored["activity"] is None
        assert stored["state"] == "STOPPED"
        assert fakes.edh.calls_to("start_desktop") == []


@pytest.mark.unit
class TestTheChecksStay:
    def test_a_start_inside_another_starts_window_is_refused_and_nothing_is_handed_over(self, monkeypatch):
        fresh = start_stamp(datetime.now(timezone.utc) - timedelta(seconds=200))
        response, fakes = _run(monkeypatch, _stopped(startRequestedAt=fresh, activity="starting"))
        assert response["statusCode"] == 409
        assert response_body(response)["startInProgress"] is True
        fakes.lambda_client.invoke.assert_not_called()
        fakes.session.assert_not_called()

    def test_a_running_desktop_is_refused(self, monkeypatch):
        response, fakes = _run(monkeypatch, _stopped(), desktop_states=[state_record(SESSION_OWN, "running")])
        assert response["statusCode"] == 409
        fakes.lambda_client.invoke.assert_not_called()


@pytest.mark.unit
class TestRecordsCarryActivity:
    def test_the_list_derives_starting_for_a_start_in_progress(self, monkeypatch):
        fresh = start_stamp(datetime.now(timezone.utc) - timedelta(seconds=20))
        response, _ = _run(monkeypatch, _stopped(startRequestedAt=fresh, activity="starting"), action="list")
        [item] = response_body(response)["Items"]
        assert item["state"] == "STOPPED" and item["activity"] == "starting"

    def test_the_list_derives_stopping_from_edhs_state(self, monkeypatch):
        response, _ = _run(monkeypatch, own_row(), action="list",
                           desktop_states=[state_record(SESSION_OWN, "stopping")])
        [item] = response_body(response)["Items"]
        assert item["state"] == "STOPPED" and item["activity"] == "stopping"

    def test_a_record_never_echoes_the_stored_mark(self, monkeypatch):
        stale = start_stamp(datetime.now(timezone.utc) - timedelta(seconds=service.START_IN_PROGRESS_SECONDS + 5))
        response, _ = _run(monkeypatch, _stopped(startRequestedAt=stale, activity="starting"), action="list")
        [item] = response_body(response)["Items"]
        assert item["activity"] is None
        assert item["startFailure"] is None

    def test_the_stop_stamps_the_row_and_answers_stopping(self, monkeypatch):
        response, fakes = _run(monkeypatch, own_row(activity="starting"), action="stop")
        assert response["statusCode"] == 200
        stored = fakes.stored.item
        assert datetime.fromisoformat(stored["stopRequestedAt"]).tzinfo is not None
        assert stored["activity"] is None
        # EDH still reported the desktop running when the row was last read.
        assert response_body(response)["activity"] == "stopping"
