# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Capacity reservations on the user routes: the launch catalog of reservations, a launch that names one,
the record of a workstation launched into one, and a start EDH refuses because the reservation is full.

A launch that names a reservation runs seven checks after the existing recipe-type and root-size checks and
before any row, credential mint, worker hand-over or further EDH call, in a fixed order, each with its own
answer; a launch that names none is today's launch, with no registry read, no EC2 read and no reservation
field on the row. The registry is a moto session table and EC2 a fake (``edhReservationTestSupport``).
"""

import json
from unittest.mock import MagicMock

import pytest
from moto import mock_aws

import backend.backend.handlers.addon.edh.edhWorkstationService as service
from backend.backend.handlers.addon.edh.common.edhSessionState import (
    EDH_SESSION_ID_FIELD, EDH_SESSION_STATE_FIELD, WORKER_PAYLOAD_KEYS,
)
from backend.tests.handlers.addon.edh.edhReservationTestSupport import (
    CR_FREE, CR_FULL, CR_OTHER, END_DATE, Untouchable, client_error, ec2_reservation, install_reservation_fakes,
)
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    CATALOG_STACK, SESSION_OWN, WORKSTATIONS_PATH, FakeEdhClient, install_service_fakes, make_event, own_row,
    response_body, workstation_path,
)

CATALOG_PATH = "/addon/edh/catalog/capacity-reservations"
BOOTSTRAP_NEW = "5b6c7d8e-9f0a-4b1c-8d2e-3f4a5b6c7d8e"
LAUNCH = {"bootstrapId": BOOTSTRAP_NEW, "imageId": "42", "instanceType": "g5.xlarge", "name": "Desk",
          "rootSizeGb": 100, "osFamily": "linux"}
VIEW_KEYS = {
    "capacityReservationId", "label", "fallbackToOnDemand", "enabled", "instanceType", "availabilityZone",
    "instancePlatform", "state", "totalInstanceCount", "availableInstanceCount", "instanceMatchCriteria",
    "endDate", "reservationType",
}
RESERVATION_OBJECT_TYPE = "edhCapacityReservation"
FULL_BODY = {"message": "The capacity reservation has no free capacity", "code": "RESERVATION_FULL"}


def _grant(*, denied_reservations=(), denied_actions=()):
    """An enforcer that allows everything but the named reservations, or the named actions on any reservation."""
    def enforce(obj, action):
        if obj.get("object__type") == RESERVATION_OBJECT_TYPE:
            return obj["edhCapacityReservationId"] not in denied_reservations and action not in denied_actions
        return True
    return enforce


def _reservation_calls(fakes):
    return [(call.args[0], call.args[1]) for call in fakes.enforcer.enforce.call_args_list
            if call.args[0].get("object__type") == RESERVATION_OBJECT_TYPE]


@pytest.fixture
def aws():
    with mock_aws():
        yield


def _install(monkeypatch, *, records=None, failure=None, registered=(), enforce=None, edh=None):
    fakes = install_service_fakes(monkeypatch, enforce=enforce or _grant(), edh=edh)
    fakes.reservations = install_reservation_fakes(monkeypatch, records=records, failure=failure,
                                                   registered=registered)
    fakes.mint = MagicMock(return_value=("disabled", {}))
    monkeypatch.setattr(service.bootstrap, "bootstrap_session", fakes.mint)
    return fakes


def _launch(fakes, **overrides):
    body = {**LAUNCH, **overrides}
    return service.lambda_handler(make_event("POST", WORKSTATIONS_PATH, body=body), MagicMock())


def _catalog(fakes, query=None):
    event = make_event("GET", CATALOG_PATH)
    if query is not None:
        event["queryStringParameters"] = query
    return service.lambda_handler(event, MagicMock())


def _assert_nothing_launched(fakes):
    fakes.table.put_item.assert_not_called()
    fakes.table.update_item.assert_not_called()
    fakes.mint.assert_not_called()
    fakes.lambda_client.invoke.assert_not_called()
    # The only EDH call a refused launch made is the catalog read that precedes the reservation checks.
    assert fakes.edh.calls, "control: the catalog read ran"
    assert {name for name, _ in fakes.edh.calls} == {"list_software_stacks"}


def _written_row(fakes):
    return fakes.table.put_item.call_args.kwargs["Item"]


@pytest.mark.unit
class TestLaunchWithoutAReservation:
    def test_a_launch_naming_none_reads_no_registry_and_no_ec2_and_writes_no_reservation_field(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        monkeypatch.setattr(service.reservations, "_session_table", Untouchable())
        monkeypatch.setattr(service.reservations, "ec2", Untouchable())

        response = _launch(fakes)

        assert response["statusCode"] == 202
        row = _written_row(fakes)
        assert not [key for key in row if key.startswith("capacityReservation")]
        payload = fakes.lambda_client.invoke.call_args.kwargs["Payload"]
        assert sorted(json.loads(payload)) == sorted(WORKER_PAYLOAD_KEYS)
        body = response_body(response)
        assert body["capacityReservationId"] is None and body["capacityReservationLabel"] is None
        assert body["capacityReservationFallback"] is False

    def test_a_null_reservation_is_none(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        monkeypatch.setattr(service.reservations, "ec2", Untouchable())
        monkeypatch.setattr(service.reservations, "_session_table", Untouchable())

        response = _launch(fakes, capacityReservationId=None)

        assert response["statusCode"] == 202
        assert not [key for key in _written_row(fakes) if key.startswith("capacityReservation")]

    @pytest.mark.parametrize("value", ["cr-123", "cr-0123456789ABCDEF0", "ami-0123456789abcdef0", 7, ""])
    def test_a_malformed_reservation_id_is_a_400_before_anything(self, monkeypatch, value):
        fakes = install_service_fakes(monkeypatch)
        monkeypatch.setattr(service.reservations, "ec2", Untouchable())
        monkeypatch.setattr(service.reservations, "_session_table", Untouchable())

        response = _launch(fakes, capacityReservationId=value)

        assert response["statusCode"] == 400
        fakes.table.put_item.assert_not_called()
        fakes.lambda_client.invoke.assert_not_called()


@pytest.mark.unit
class TestLaunchChecks:
    def test_a_launch_into_a_free_reservation_records_it_on_the_row(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE)},
                         registered=[(CR_FREE, {"label": "Render farm"})])

        response = _launch(fakes, capacityReservationId=CR_FREE)

        assert response["statusCode"] == 202
        row = _written_row(fakes)
        assert row["capacityReservationId"] == CR_FREE
        assert "capacityReservationFallback" not in row and "capacityReservationRequested" not in row
        payload = json.loads(fakes.lambda_client.invoke.call_args.kwargs["Payload"])
        assert sorted(payload) == sorted(WORKER_PAYLOAD_KEYS)
        body = response_body(response)
        assert body["capacityReservationId"] == CR_FREE
        assert body["capacityReservationLabel"] == "Render farm"
        assert body["capacityReservationFallback"] is False
        calls = _reservation_calls(fakes)
        assert ({"object__type": RESERVATION_OBJECT_TYPE, "edhCapacityReservationId": CR_FREE}, "POST") in calls
        assert {action for _, action in calls} == {"POST"}
        assert fakes.reservations.ec2.ids_asked() == [[CR_FREE]]

    def test_1_an_unregistered_reservation_is_a_400(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE)})

        response = _launch(fakes, capacityReservationId=CR_FREE)

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "capacityReservationId is not registered"
        _assert_nothing_launched(fakes)
        assert fakes.reservations.ec2.calls == []
        assert _reservation_calls(fakes) == []

    def test_1_a_disabled_reservation_is_unregistered(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE)},
                         registered=[(CR_FREE, {"enabled": False})])

        response = _launch(fakes, capacityReservationId=CR_FREE)

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "capacityReservationId is not registered"
        _assert_nothing_launched(fakes)

    def test_2_a_reservation_the_role_may_not_launch_into_is_a_403(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE)},
                         registered=[(CR_FREE, {})], enforce=_grant(denied_actions=("POST",)))

        response = _launch(fakes, capacityReservationId=CR_FREE)

        assert response["statusCode"] == 403
        _assert_nothing_launched(fakes)
        assert fakes.reservations.ec2.calls == []

    def test_3_a_failed_read_is_a_503_and_never_a_fallback(self, monkeypatch, aws):
        fakes = _install(monkeypatch, failure=client_error("RequestLimitExceeded"),
                         registered=[(CR_FREE, {"fallbackToOnDemand": True})])

        response = _launch(fakes, capacityReservationId=CR_FREE)

        assert response["statusCode"] == 503
        assert response_body(response)["message"] == "The capacity reservation could not be read; try again"
        _assert_nothing_launched(fakes)

    @pytest.mark.parametrize("records", [
        {CR_FREE: ec2_reservation(CR_FREE, State="expired")},
        {CR_FREE: ec2_reservation(CR_FREE, State="pending")},
        {},
    ], ids=["expired", "pending", "notFound"])
    def test_4_a_reservation_that_is_not_active_is_a_400(self, monkeypatch, aws, records):
        fakes = _install(monkeypatch, records=records, registered=[(CR_FREE, {})])

        response = _launch(fakes, capacityReservationId=CR_FREE)

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "The capacity reservation is not active"
        _assert_nothing_launched(fakes)

    def test_5_another_instance_type_is_a_400(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE, InstanceType="g5.2xlarge")},
                         registered=[(CR_FREE, {})])

        response = _launch(fakes, capacityReservationId=CR_FREE)

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "instanceType does not match the capacity reservation"
        _assert_nothing_launched(fakes)

    @pytest.mark.parametrize("platform", ["Windows", "Red Hat Enterprise Linux", "Linux with SQL Server Web"])
    def test_6_a_platform_that_does_not_suit_the_image_is_a_400(self, monkeypatch, aws, platform):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE, InstancePlatform=platform)},
                         registered=[(CR_FREE, {})])

        response = _launch(fakes, capacityReservationId=CR_FREE)

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "The capacity reservation's platform does not match the image"
        _assert_nothing_launched(fakes)

    def test_7_a_full_reservation_that_falls_back_launches_on_demand(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FULL: ec2_reservation(CR_FULL, AvailableInstanceCount=0)},
                         registered=[(CR_FULL, {"fallbackToOnDemand": True})])

        response = _launch(fakes, capacityReservationId=CR_FULL)

        assert response["statusCode"] == 202
        row = _written_row(fakes)
        assert "capacityReservationId" not in row
        assert row["capacityReservationFallback"] is True
        assert row["capacityReservationRequested"] == CR_FULL
        body = response_body(response)
        assert body["capacityReservationId"] is None and body["capacityReservationFallback"] is True
        assert fakes.lambda_client.invoke.called

    def test_7_a_full_reservation_that_does_not_fall_back_is_a_409(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FULL: ec2_reservation(CR_FULL, AvailableInstanceCount=0)},
                         registered=[(CR_FULL, {"fallbackToOnDemand": False})])

        response = _launch(fakes, capacityReservationId=CR_FULL)

        assert response["statusCode"] == 409
        assert response_body(response) == FULL_BODY
        _assert_nothing_launched(fakes)

    @pytest.mark.parametrize("case,expected_status,expected_message", [
        # Each request fails every check from its own onward; the answer names the first.
        ("unregistered", 400, "capacityReservationId is not registered"),
        ("denied", 403, None),
        ("unreadable", 503, "The capacity reservation could not be read; try again"),
        ("inactive", 400, "The capacity reservation is not active"),
        ("type", 400, "instanceType does not match the capacity reservation"),
        ("platform", 400, "The capacity reservation's platform does not match the image"),
        ("full", 409, "The capacity reservation has no free capacity"),
    ])
    def test_the_checks_run_in_order(self, monkeypatch, aws, case, expected_status, expected_message):
        later = {"State": "expired", "InstanceType": "g5.2xlarge", "InstancePlatform": "Windows",
                 "AvailableInstanceCount": 0}
        order = ["inactive", "type", "platform", "full"]
        fields = {}
        if case in order:
            # Fail this check and every later one, pass every earlier one.
            for name, (key, value) in zip(order, later.items()):
                if order.index(name) >= order.index(case):
                    fields[key] = value
        records = {CR_FULL: ec2_reservation(CR_FULL, **fields)}
        registered = [] if case == "unregistered" else [(CR_FULL, {"fallbackToOnDemand": False})]
        failure = client_error("RequestLimitExceeded") if case in ("unregistered", "denied", "unreadable") else None
        enforce = _grant(denied_actions=("POST",)) if case in ("unregistered", "denied") else None
        fakes = _install(monkeypatch, records=records, failure=failure, registered=registered, enforce=enforce)

        response = _launch(fakes, capacityReservationId=CR_FULL)

        assert response["statusCode"] == expected_status
        if expected_message is not None:
            assert response_body(response)["message"] == expected_message
        _assert_nothing_launched(fakes)

    def test_the_checks_follow_the_root_size_check(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={})

        response = _launch(fakes, capacityReservationId=CR_FREE, rootSizeGb=20)

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "Root disk size must be at least 100 GB for this image"

    def test_the_checks_follow_the_workstation_tier_2_check(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={}, enforce=lambda obj, action: obj["object__type"] != "edhWorkstation")

        response = _launch(fakes, capacityReservationId=CR_FREE)

        assert response["statusCode"] == 403
        assert _reservation_calls(fakes) == []

    def test_a_windows_image_launches_into_a_windows_reservation(self, monkeypatch, aws):
        stack = {**CATALOG_STACK, service.EDH_FIELD_STACK_BASE_OS: "windows2022",
                 service.EDH_FIELD_STACK_OS_FAMILY: "windows"}
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE, InstancePlatform="Windows")},
                         registered=[(CR_FREE, {})], edh=FakeEdhClient(list_software_stacks=[stack]))

        response = _launch(fakes, capacityReservationId=CR_FREE, osFamily="windows")

        assert response["statusCode"] == 202
        assert _written_row(fakes)["capacityReservationId"] == CR_FREE


@pytest.mark.unit
class TestCatalog:
    def _registered(self):
        return [(CR_FREE, {"label": "B farm"}), (CR_FULL, {"label": "A farm", "fallbackToOnDemand": False}),
                (CR_OTHER, {"label": "C windows"})]

    def _records(self):
        return {
            CR_FREE: ec2_reservation(CR_FREE),
            CR_FULL: ec2_reservation(CR_FULL, AvailableInstanceCount=0, InstanceType="g5.2xlarge"),
            CR_OTHER: ec2_reservation(CR_OTHER, InstancePlatform="Windows"),
        }

    def test_lists_the_registered_enabled_permitted_active_reservations_by_label(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records=self._records(), registered=self._registered())

        response = _catalog(fakes)

        assert response["statusCode"] == 200
        items = response_body(response)["Items"]
        assert [item["capacityReservationId"] for item in items] == [CR_FULL, CR_FREE, CR_OTHER]
        assert all(set(item) == VIEW_KEYS for item in items)
        free = items[1]
        assert free == {"capacityReservationId": CR_FREE, "label": "B farm", "fallbackToOnDemand": True,
                        "enabled": True, "instanceType": "g5.xlarge", "availabilityZone": "us-east-1a",
                        "instancePlatform": "Linux/UNIX", "state": "active", "totalInstanceCount": 2,
                        "availableInstanceCount": 1, "instanceMatchCriteria": "targeted",
                        "endDate": END_DATE.isoformat(), "reservationType": "default"}
        asked = fakes.reservations.ec2.ids_asked()
        assert len(asked) == 1 and sorted(asked[0]) == sorted([CR_FREE, CR_FULL, CR_OTHER])
        assert {(obj["edhCapacityReservationId"], action) for obj, action in _reservation_calls(fakes)} == {
            (CR_FREE, "GET"), (CR_FULL, "GET"), (CR_OTHER, "GET")}

    def test_leaves_out_disabled_unpermitted_and_inactive_reservations(self, monkeypatch, aws):
        records = self._records()
        records[CR_OTHER] = ec2_reservation(CR_OTHER, State="cancelled")
        registered = [(CR_FREE, {"enabled": False}), (CR_FULL, {}), (CR_OTHER, {})]
        fakes = _install(monkeypatch, records=records, registered=registered,
                         enforce=_grant(denied_reservations=(CR_FULL,)))

        response = _catalog(fakes)

        assert response_body(response)["Items"] == []
        # Only the permitted, enabled one is read: EC2 is never asked about a reservation the caller cannot see.
        assert fakes.reservations.ec2.ids_asked() == [[CR_OTHER]]

    def test_a_reservation_ec2_no_longer_holds_is_left_out(self, monkeypatch, aws):
        records = self._records()
        del records[CR_OTHER]
        fakes = _install(monkeypatch, records=records, registered=self._registered())

        ids = [item["capacityReservationId"] for item in response_body(_catalog(fakes))["Items"]]

        assert ids == [CR_FULL, CR_FREE]

    @pytest.mark.parametrize("query,expected", [
        ({"instanceType": "g5.xlarge"}, [CR_FREE, CR_OTHER]),
        ({"instanceType": "g5.2xlarge"}, [CR_FULL]),
        ({"osFamily": "linux"}, [CR_FULL, CR_FREE]),
        ({"osFamily": "windows"}, [CR_OTHER]),
        ({"instanceType": "g5.xlarge", "osFamily": "linux"}, [CR_FREE]),
        ({"instanceType": "p4d.24xlarge"}, []),
    ])
    def test_filters_by_instance_type_and_os_family(self, monkeypatch, aws, query, expected):
        fakes = _install(monkeypatch, records=self._records(), registered=self._registered())

        response = _catalog(fakes, query)

        assert [item["capacityReservationId"] for item in response_body(response)["Items"]] == expected

    @pytest.mark.parametrize("query", [{"instanceType": "G5 XL"}, {"osFamily": "macos"}, {"instanceType": ""}])
    def test_a_malformed_filter_is_a_400(self, monkeypatch, aws, query):
        fakes = _install(monkeypatch, records=self._records(), registered=self._registered())

        response = _catalog(fakes, query)

        assert response["statusCode"] == 400
        assert fakes.reservations.ec2.calls == []

    def test_a_failed_ec2_read_lists_none_and_answers_200(self, monkeypatch, aws):
        fakes = _install(monkeypatch, failure=client_error("UnauthorizedOperation"), registered=self._registered())
        warnings = MagicMock()
        monkeypatch.setattr(service.logger, "warning", warnings)

        response = _catalog(fakes)

        assert response["statusCode"] == 200
        assert response_body(response) == {"Items": []}
        warnings.assert_called()

    def test_an_empty_registry_reads_no_ec2(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records=self._records())

        response = _catalog(fakes)

        assert response_body(response) == {"Items": []}
        assert fakes.reservations.ec2.calls == []


def _stopped_row(**overrides):
    return own_row(state="STOPPED", edhState="stopped", connectionAvailable=False, **overrides)


def _start(monkeypatch, row, start_result, *, records=None, failure=None):
    edh = FakeEdhClient(start_desktop=start_result,
                        get_desktop_states=[{EDH_SESSION_ID_FIELD: SESSION_OWN, EDH_SESSION_STATE_FIELD: "stopped"}])
    fakes = _install(monkeypatch, records=records, failure=failure, registered=[(CR_FULL, {})], edh=edh)
    fakes.table.query.return_value = {"Items": [row]}
    fakes.mint.return_value = ("skipped", {})
    event = make_event("POST", workstation_path(SESSION_OWN, "start"), path_params={"sessionId": SESSION_OWN})
    return service.lambda_handler(event, MagicMock()), fakes


@pytest.mark.unit
class TestStartOfAReservationBoundDesktop:
    """The start route hands every start to the launch worker and reads no reservation: a start EDH refuses while
    the desktop's reservation is full is recorded RESERVATION_FULL by the worker (test_edhLaunchWorker_start.py),
    not answered 409 here (in-dev D10, D11)."""

    @pytest.mark.parametrize("overrides", [{"capacityReservationId": CR_FULL},
                                           {"capacityReservationFallback": True,
                                            "capacityReservationRequested": CR_FULL}, {}],
                             ids=["reservation-bound", "fell back", "on-demand"])
    def test_the_start_is_handed_over_and_reads_no_reservation(self, monkeypatch, aws, overrides):
        response, fakes = _start(monkeypatch, _stopped_row(**overrides), {},
                                 records={CR_FULL: ec2_reservation(CR_FULL, AvailableInstanceCount=0)})

        assert response["statusCode"] == 202
        assert "code" not in response_body(response)
        assert fakes.edh.calls_to("start_desktop") == []
        assert fakes.reservations.ec2.ids_asked() == []
        handed_over = {(payload["action"], payload["bootstrapId"])
                       for payload in (json.loads(call.kwargs["Payload"])
                                       for call in fakes.lambda_client.invoke.call_args_list)}
        assert handed_over == {("start", own_row()["bootstrapId"])}


@pytest.mark.unit
class TestRecordLabels:
    def test_get_joins_the_label_of_a_registered_reservation(self, monkeypatch, aws):
        fakes = _install(monkeypatch, registered=[(CR_FREE, {"label": "Render farm"})])
        fakes.table.query.return_value = {"Items": [own_row(capacityReservationId=CR_FREE)]}
        event = make_event("GET", workstation_path(SESSION_OWN), path_params={"sessionId": SESSION_OWN})

        body = response_body(service.lambda_handler(event, MagicMock()))

        assert body["capacityReservationId"] == CR_FREE
        assert body["capacityReservationLabel"] == "Render farm"
        assert body["capacityReservationFallback"] is False
        assert "capacityReservationRequested" not in body

    def test_list_joins_labels_and_leaves_an_unregistered_one_null(self, monkeypatch, aws):
        fakes = _install(monkeypatch, registered=[(CR_FREE, {"label": "Render farm"})])
        rows = [
            own_row(bootstrapId="a" * 8 + "-0000-4000-8000-" + "0" * 12, capacityReservationId=CR_FREE),
            own_row(bootstrapId="b" * 8 + "-0000-4000-8000-" + "0" * 12, capacityReservationId=CR_OTHER),
            own_row(bootstrapId="c" * 8 + "-0000-4000-8000-" + "0" * 12, capacityReservationFallback=True,
                    capacityReservationRequested=CR_FREE),
            own_row(bootstrapId="d" * 8 + "-0000-4000-8000-" + "0" * 12),
        ]
        for row in rows:
            row.pop("sessionId")
        fakes.table.query.return_value = {"Items": rows}

        items = response_body(service.lambda_handler(make_event("GET", WORKSTATIONS_PATH), MagicMock()))["Items"]

        assert [(item["capacityReservationId"], item["capacityReservationLabel"], item["capacityReservationFallback"])
                for item in items] == [(CR_FREE, "Render farm", False), (CR_OTHER, None, False),
                                       (None, None, True), (None, None, False)]

    def test_a_list_without_a_reservation_reads_no_registry(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        monkeypatch.setattr(service.reservations, "_session_table", Untouchable())
        row = own_row()
        row.pop("sessionId")
        fakes.table.query.return_value = {"Items": [row]}

        response = service.lambda_handler(make_event("GET", WORKSTATIONS_PATH), MagicMock())

        assert response["statusCode"] == 200
        assert response_body(response)["Items"][0]["capacityReservationLabel"] is None

    def test_a_label_read_that_fails_leaves_the_label_null(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)

        class Failing:
            def get_item(self, **kwargs):
                raise client_error("ProvisionedThroughputExceededException", "GetItem")

        monkeypatch.setattr(service.reservations, "_session_table", Failing())
        row = own_row(capacityReservationId=CR_FREE)
        row.pop("sessionId")
        fakes.table.query.return_value = {"Items": [row]}

        response = service.lambda_handler(make_event("GET", WORKSTATIONS_PATH), MagicMock())

        assert response["statusCode"] == 200
        item = response_body(response)["Items"][0]
        assert item["capacityReservationId"] == CR_FREE and item["capacityReservationLabel"] is None
