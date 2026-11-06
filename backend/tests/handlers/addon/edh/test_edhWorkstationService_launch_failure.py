# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The list and get routes capture the reason EDH recorded for a failed launch (in-dev D8).

A row the route records ORPHANED -- EDH reports the desktop in ``error``, or EDH no longer has the desktop
of a launch still CREATING -- or finds ORPHANED with a session id is given the failure EDH's per-user
listing records for that one session: ``edhFailure`` is stored, a generic ``lastError`` becomes the code
the AWS error implies, and a code the launch recorded is kept. Each row is read at most once per five
minutes (``edhFailureReadAt``) and never again once a reason is stored; a read that fails, or finds
nothing, writes only the stamp; a row deleted since it was read is not written. The record carries
``edhFailure`` or null and never the stamp.
"""

from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.edh.common.edhClient import EDH_ROUTE_DESKTOPS_LIST, EdhUnavailableError
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.backend.handlers.addon.edh.common.edhSessionState import (
    LAST_ERROR_CAPACITY_UNAVAILABLE, LAST_ERROR_LAUNCH_ERROR, LAST_ERROR_QUOTA_EXCEEDED,
)
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    BOOTSTRAP_OWN,
    SESSION_OWN,
    USER,
    WORKSTATIONS_PATH,
    FakeEdhClient,
    edh_proxy_records,
    install_service_fakes,
    make_event,
    own_row,
    response_body,
    state_record,
    workstation_path,
)

CAPACITY_FAILURE = {"event": "placement_failed", "errorCode": "InsufficientInstanceCapacity",
                    "operation": "CreateCapacityReservation"}
QUOTA_FAILURE = {"event": "placement_failed", "errorCode": "VcpuLimitExceeded", "operation": "RunInstances"}
FAILURE_OPERATION = "workstationLaunchFailure"


def _writes(fakes):
    """Every row update the route issued, each as {attribute: value}, in call order."""
    writes = []
    for call in fakes.table.update_item.call_args_list:
        names = call.kwargs["ExpressionAttributeNames"]
        values = call.kwargs["ExpressionAttributeValues"]
        writes.append({key: values[placeholder.replace("#a", ":v")]
                       for placeholder, key in names.items() if placeholder.startswith("#a")})
    return writes


def _merged(fakes):
    merged = {}
    for write in _writes(fakes):
        merged.update(write)
    return merged


def _failed_in_edh(**overrides):
    """The caller's row of a desktop EDH last reported in error, already recorded ORPHANED."""
    fields = {"state": "ORPHANED", "edhState": "error", "connectionAvailable": False,
              "lastError": LAST_ERROR_LAUNCH_ERROR, **overrides}
    return own_row(**fields)


def _list(monkeypatch, rows, states, failure=None):
    fakes = install_service_fakes(
        monkeypatch, edh=FakeEdhClient(get_desktop_states=states, get_desktop_failure=failure))
    fakes.table.query.return_value = {"Items": rows}
    return fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {}), fakes


def _get(monkeypatch, row, states, failure=None):
    fakes = install_service_fakes(
        monkeypatch, edh=FakeEdhClient(get_desktop_states=states, get_desktop_failure=failure))
    fakes.table.query.return_value = {"Items": [row]}
    event = make_event("GET", workstation_path(SESSION_OWN), path_params={"sessionId": SESSION_OWN})
    return fakes.module.lambda_handler(event, {}), fakes


def _item(response):
    (item,) = response_body(response)["Items"]
    return item


def _ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


@pytest.mark.unit
class TestTheListCapturesAFailedLaunch:
    def test_a_desktop_edh_reports_in_error_is_given_its_reason_and_reclassified(self, monkeypatch):
        response, fakes = _list(monkeypatch, [own_row()], [state_record(SESSION_OWN, "error")], CAPACITY_FAILURE)

        assert response["statusCode"] == 200
        item = _item(response)
        assert (item["state"], item["lastError"]) == ("ORPHANED", LAST_ERROR_CAPACITY_UNAVAILABLE)
        assert {key: item["edhFailure"][key] for key in CAPACITY_FAILURE} == CAPACITY_FAILURE
        assert datetime.fromisoformat(item["edhFailure"]["observedAt"]).tzinfo is not None
        assert "edhFailureReadAt" not in item
        assert fakes.edh.calls_to("get_desktop_failure") == [(edh_uid_for(USER), SESSION_OWN)]
        stored = _merged(fakes)
        assert stored["edhFailure"] == item["edhFailure"]
        assert stored["lastError"] == LAST_ERROR_CAPACITY_UNAVAILABLE
        assert stored["edhFailureReadAt"]

    def test_the_read_is_audited_under_its_own_operation_and_the_per_user_route(self, monkeypatch):
        _response, fakes = _list(monkeypatch, [own_row()], [state_record(SESSION_OWN, "error")], CAPACITY_FAILURE)

        reads = [record for record in edh_proxy_records(fakes) if record["operation"] == FAILURE_OPERATION]
        assert reads, "the failure read was not audited"
        assert {(r["edhMethod"], r["edhRoute"], r["edhUid"], r["edhStatus"]) for r in reads} == {
            ("GET", EDH_ROUTE_DESKTOPS_LIST, edh_uid_for(USER), "ok")}

    def test_a_launch_edh_no_longer_has_is_given_its_reason(self, monkeypatch):
        creating = own_row(state="CREATING", edhState="placing", connectionAvailable=False)

        response, fakes = _list(monkeypatch, [creating], [], QUOTA_FAILURE)

        item = _item(response)
        assert (item["state"], item["lastError"]) == ("ORPHANED", LAST_ERROR_QUOTA_EXCEEDED)
        assert item["edhFailure"]["errorCode"] == "VcpuLimitExceeded"
        assert fakes.edh.calls_to("get_desktop_failure") == [(edh_uid_for(USER), SESSION_OWN)]

    def test_a_code_the_launch_recorded_is_kept_beside_the_reason(self, monkeypatch):
        creating = own_row(state="CREATING", edhState="placing", connectionAvailable=False, lastError="EDH_REFUSED")

        response, fakes = _list(monkeypatch, [creating], [], CAPACITY_FAILURE)

        item = _item(response)
        assert (item["state"], item["lastError"]) == ("ORPHANED", "EDH_REFUSED")
        assert item["edhFailure"]["errorCode"] == "InsufficientInstanceCapacity"
        assert "EDH_REFUSED" in [write.get("lastError") for write in _writes(fakes)]
        assert LAST_ERROR_CAPACITY_UNAVAILABLE not in [write.get("lastError") for write in _writes(fakes)]

    def test_a_row_already_orphaned_is_given_its_reason_on_a_later_list(self, monkeypatch):
        response, fakes = _list(monkeypatch, [_failed_in_edh()], [state_record(SESSION_OWN, "error")],
                                CAPACITY_FAILURE)

        assert _item(response)["lastError"] == LAST_ERROR_CAPACITY_UNAVAILABLE
        assert fakes.edh.calls_to("get_desktop_failure") == [(edh_uid_for(USER), SESSION_OWN)]

    def test_a_failure_edh_records_without_an_aws_code_keeps_the_generic_code(self, monkeypatch):
        response, _fakes = _list(monkeypatch, [_failed_in_edh()], [state_record(SESSION_OWN, "error")],
                                 {"event": "session-failed", "checkpoint": "dcv_server_start"})

        item = _item(response)
        assert item["lastError"] == LAST_ERROR_LAUNCH_ERROR
        assert item["edhFailure"]["checkpoint"] == "dcv_server_start"
        assert "errorCode" not in item["edhFailure"]

    def test_a_read_that_fails_writes_only_its_stamp(self, monkeypatch):
        response, fakes = _list(monkeypatch, [_failed_in_edh()], [state_record(SESSION_OWN, "error")],
                                EdhUnavailableError("read timeout"))

        assert response["statusCode"] == 200
        item = _item(response)
        assert (item["state"], item["lastError"], item["edhFailure"]) == ("ORPHANED", LAST_ERROR_LAUNCH_ERROR, None)
        writes = _writes(fakes)
        assert writes, "the failed read was not stamped"
        assert {key for write in writes for key in write} <= {"edhFailureReadAt", "updatedAt"}
        assert {(r["operation"], r["edhStatus"]) for r in edh_proxy_records(fakes)} >= {
            (FAILURE_OPERATION, "EdhUnavailableError")}

    def test_a_read_that_finds_no_reason_writes_only_its_stamp(self, monkeypatch):
        response, fakes = _list(monkeypatch, [_failed_in_edh()], [state_record(SESSION_OWN, "error")], None)

        assert _item(response)["edhFailure"] is None
        writes = _writes(fakes)
        assert writes, "the read was not stamped"
        assert {key for write in writes for key in write} <= {"edhFailureReadAt", "updatedAt"}

    def test_a_row_read_inside_the_last_five_minutes_is_not_read_again(self, monkeypatch):
        row = _failed_in_edh(edhFailureReadAt=_ago(minutes=2))

        response, fakes = _list(monkeypatch, [row], [state_record(SESSION_OWN, "error")], CAPACITY_FAILURE)

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("get_desktop_failure") == []
        assert _item(response)["edhFailure"] is None

    def test_a_row_read_more_than_five_minutes_ago_is_read_again(self, monkeypatch):
        # Control for the case above.
        row = _failed_in_edh(edhFailureReadAt=_ago(minutes=6))

        _response, fakes = _list(monkeypatch, [row], [state_record(SESSION_OWN, "error")], CAPACITY_FAILURE)

        assert fakes.edh.calls_to("get_desktop_failure") == [(edh_uid_for(USER), SESSION_OWN)]

    def test_a_row_that_holds_a_reason_is_not_read_again(self, monkeypatch):
        stored = {**CAPACITY_FAILURE, "observedAt": _ago(hours=1)}
        row = _failed_in_edh(lastError=LAST_ERROR_CAPACITY_UNAVAILABLE, edhFailure=stored)

        response, fakes = _list(monkeypatch, [row], [state_record(SESSION_OWN, "error")], QUOTA_FAILURE)

        assert fakes.edh.calls_to("get_desktop_failure") == []
        assert _item(response)["edhFailure"] == stored
        fakes.table.update_item.assert_not_called()

    def test_a_running_desktop_is_never_read_for_a_failure(self, monkeypatch):
        response, fakes = _list(monkeypatch, [own_row()], [state_record(SESSION_OWN)], CAPACITY_FAILURE)

        assert fakes.edh.calls_to("get_desktop_failure") == []
        assert _item(response)["edhFailure"] is None

    def test_an_orphaned_launch_without_a_session_is_never_read(self, monkeypatch):
        row = own_row(state="ORPHANED", edhState=None, connectionAvailable=False, lastError="EDH_REFUSED")
        row.pop("sessionId")

        response, fakes = _list(monkeypatch, [row], [], CAPACITY_FAILURE)

        assert response["statusCode"] == 200
        assert fakes.edh.calls == []

    def test_a_row_deleted_since_it_was_read_is_not_written(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(
            get_desktop_states=[state_record(SESSION_OWN, "error")], get_desktop_failure=CAPACITY_FAILURE))
        fakes.table.query.return_value = {"Items": [_failed_in_edh()]}
        fakes.table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "deleted"}}, "UpdateItem")

        response = fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})

        assert response["statusCode"] == 200
        item = _item(response)
        assert item["edhFailure"] is None
        assert item["lastError"] == LAST_ERROR_LAUNCH_ERROR
        fakes.table.update_item.assert_called()

    def test_each_failed_row_is_read_for_its_own_session(self, monkeypatch):
        second = "2b3c4d5e-6f70-4a8b-9c0d-1e2f3a4b5c6d"
        rows = [_failed_in_edh(), _failed_in_edh(bootstrapId="b-2", sessionId=second)]

        response, fakes = _list(monkeypatch, rows, [state_record(SESSION_OWN, "error"),
                                                    state_record(second, "error")], CAPACITY_FAILURE)

        assert response["statusCode"] == 200
        assert sorted(fakes.edh.calls_to("get_desktop_failure")) == sorted(
            [(edh_uid_for(USER), SESSION_OWN), (edh_uid_for(USER), second)])


@pytest.mark.unit
class TestTheGetCapturesAFailedLaunch:
    def test_a_desktop_edh_reports_in_error_is_given_its_reason(self, monkeypatch):
        response, fakes = _get(monkeypatch, own_row(), [state_record(SESSION_OWN, "error")], CAPACITY_FAILURE)

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["state"], body["lastError"]) == ("ORPHANED", LAST_ERROR_CAPACITY_UNAVAILABLE)
        assert body["edhFailure"]["operation"] == "CreateCapacityReservation"
        assert "edhFailureReadAt" not in body
        assert fakes.edh.calls_to("get_desktop_failure") == [(edh_uid_for(USER), SESSION_OWN)]
        assert _merged(fakes)["edhFailure"] == body["edhFailure"]

    def test_a_launch_edh_no_longer_has_is_given_its_reason(self, monkeypatch):
        creating = own_row(state="CREATING", edhState="placing", connectionAvailable=False)

        response, _fakes = _get(monkeypatch, creating, [], QUOTA_FAILURE)

        body = response_body(response)
        assert (body["state"], body["lastError"]) == ("ORPHANED", LAST_ERROR_QUOTA_EXCEEDED)

    def test_a_read_that_fails_answers_the_record_and_stamps_it(self, monkeypatch):
        response, fakes = _get(monkeypatch, _failed_in_edh(), [state_record(SESSION_OWN, "error")],
                               EdhUnavailableError("read timeout"))

        assert response["statusCode"] == 200
        assert response_body(response)["edhFailure"] is None
        assert "edhFailureReadAt" in _merged(fakes)
        assert "edhFailure" not in _merged(fakes)

    def test_a_row_read_inside_the_last_five_minutes_is_not_read_again(self, monkeypatch):
        response, fakes = _get(monkeypatch, _failed_in_edh(edhFailureReadAt=_ago(seconds=30)),
                               [state_record(SESSION_OWN, "error")], CAPACITY_FAILURE)

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("get_desktop_failure") == []


@pytest.mark.unit
class TestTheRecordCarriesTheReason:
    def test_a_row_without_a_reason_carries_null(self, monkeypatch):
        response, _fakes = _list(monkeypatch, [own_row()], [state_record(SESSION_OWN)])

        item = _item(response)
        assert "edhFailure" in item
        assert item["edhFailure"] is None

    def test_a_stored_reason_is_returned_and_its_read_stamp_never(self, monkeypatch):
        stored = {"event": "session-failed", "checkpoint": "dcv_server_start", "observedAt": _ago(hours=1)}
        row = _failed_in_edh(edhFailure=stored, edhFailureReadAt=_ago(hours=1))

        response, _fakes = _list(monkeypatch, [row], [state_record(SESSION_OWN, "error")])

        item = _item(response)
        assert item["edhFailure"] == stored
        assert "edhFailureReadAt" not in item

    def test_the_bootstrap_id_of_the_row_is_the_one_returned(self, monkeypatch):
        # Control: the record projected is the caller's row.
        response, _fakes = _list(monkeypatch, [_failed_in_edh()], [state_record(SESSION_OWN, "error")])

        assert _item(response)["bootstrapId"] == BOOTSTRAP_OWN
