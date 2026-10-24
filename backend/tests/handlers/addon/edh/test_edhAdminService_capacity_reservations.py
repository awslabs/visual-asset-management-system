# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The admin capacity-reservation routes and the reservation fields of the admin instance views.

``/addon/edh/admin/capacity-reservations`` lists every registered reservation with its live EC2 view and
registers one EC2 holds in the deployment's account and Region; ``.../{capacityReservationId}`` updates
or unregisters one. Unregistering touches the registry only, never EC2, and leaves the rows launched into
the reservation as they are. Every write is audited. The registry is a moto session table and EC2 a fake.
"""

import pytest
from moto import mock_aws

from backend.backend.handlers.addon.edh.common import edhCapacityReservations as reservations
from backend.tests.handlers.addon.edh.edhAdminTestSupport import (
    INSTANCES_PATH, FakeEdhClient, edh_desktop, install_admin_fakes, make_event, response_body, session_row,
)
from backend.tests.handlers.addon.edh.edhReservationTestSupport import (
    CR_FREE, CR_FULL, CR_OTHER, END_DATE, Untouchable, client_error, ec2_reservation, install_reservation_fakes,
)

COLLECTION = "/addon/edh/admin/capacity-reservations"
NOT_FOUND_MESSAGE = "Capacity reservation not found in this account and Region"


def _one(cr_id):
    return f"{COLLECTION}/{cr_id}"


@pytest.fixture
def aws():
    with mock_aws():
        yield


def _install(monkeypatch, *, records=None, failure=None, registered=(), edh=None, rows=None):
    fakes = install_admin_fakes(monkeypatch, edh=edh, rows=rows)
    fakes.reservations = install_reservation_fakes(monkeypatch, records=records, failure=failure,
                                                   registered=registered)
    return fakes


def _call(fakes, method, path, *, body=None, path_params=None):
    return fakes.module.lambda_handler(make_event(method, path, body=body, path_params=path_params), {})


def _audits(fakes, action):
    return [call.args[2] for call in fakes.log_actions.call_args_list if call.args[1] == action]


@pytest.mark.unit
class TestList:
    def test_lists_every_registered_reservation_with_its_live_view(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE)},
                         registered=[(CR_FREE, {"label": "B"}), (CR_OTHER, {"label": "A", "enabled": False})])

        response = _call(fakes, "GET", COLLECTION)

        assert response["statusCode"] == 200
        items = response_body(response)["Items"]
        assert [item["capacityReservationId"] for item in items] == [CR_OTHER, CR_FREE]
        assert items[0]["enabled"] is False and items[0]["state"] == "notFound"
        assert items[1]["state"] == "active" and items[1]["endDate"] == END_DATE.isoformat()
        assert len(fakes.reservations.ec2.calls) >= 1

    def test_a_failed_read_lists_each_reservation_state_unknown(self, monkeypatch, aws):
        fakes = _install(monkeypatch, failure=client_error("UnauthorizedOperation"), registered=[(CR_FREE, {})])

        response = _call(fakes, "GET", COLLECTION)

        assert response["statusCode"] == 200
        [item] = response_body(response)["Items"]
        assert item["state"] == "unknown" and item["availableInstanceCount"] is None

    def test_an_empty_registry_reads_no_ec2(self, monkeypatch, aws):
        fakes = _install(monkeypatch)

        assert response_body(_call(fakes, "GET", COLLECTION)) == {"Items": []}
        assert fakes.reservations.ec2.calls == []


@pytest.mark.unit
class TestRegister:
    def test_registers_a_reservation_ec2_holds(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE)})

        response = _call(fakes, "POST", COLLECTION, body={"capacityReservationId": CR_FREE, "label": " Render farm "})

        assert response["statusCode"] == 201
        body = response_body(response)
        assert body["capacityReservationId"] == CR_FREE and body["label"] == "Render farm"
        assert body["fallbackToOnDemand"] is True and body["enabled"] is True
        assert body["state"] == "active" and body["availableInstanceCount"] == 1
        stored = reservations.get_registration(CR_FREE)
        assert stored["createdBy"] == "carol"
        [audit] = _audits(fakes, "edhCapacityReservationRegister")
        assert audit["capacityReservation"]["capacityReservationId"] == CR_FREE

    def test_keeps_the_given_flags(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE)})

        body = response_body(_call(fakes, "POST", COLLECTION, body={
            "capacityReservationId": CR_FREE, "label": "Strict", "fallbackToOnDemand": False, "enabled": False}))

        assert (body["fallbackToOnDemand"], body["enabled"]) == (False, False)

    def test_a_reservation_ec2_does_not_hold_is_a_400_and_registers_nothing(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={})

        response = _call(fakes, "POST", COLLECTION, body={"capacityReservationId": CR_FREE, "label": "Ghost"})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == NOT_FOUND_MESSAGE
        assert reservations.get_registration(CR_FREE) is None
        assert _audits(fakes, "edhCapacityReservationRegister") == []

    @pytest.mark.parametrize("state", ["expired", "cancelled", "failed"])
    def test_a_reservation_in_a_terminal_state_is_a_400_and_registers_nothing(self, monkeypatch, aws, state):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE, State=state)})

        response = _call(fakes, "POST", COLLECTION, body={"capacityReservationId": CR_FREE, "label": "Over"})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "The capacity reservation is not active"
        assert reservations.get_registration(CR_FREE) is None
        assert _audits(fakes, "edhCapacityReservationRegister") == []

    @pytest.mark.parametrize("state", ["active", "pending", "scheduled"])
    def test_a_reservation_that_is_or_will_be_active_registers(self, monkeypatch, aws, state):
        # A future-dated reservation is legitimate to register before it starts.
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE, State=state)})

        response = _call(fakes, "POST", COLLECTION, body={"capacityReservationId": CR_FREE, "label": "Soon"})

        assert response["statusCode"] == 201
        assert response_body(response)["state"] == state
        assert reservations.get_registration(CR_FREE)["label"] == "Soon"

    def test_a_failed_read_is_a_503_and_registers_nothing(self, monkeypatch, aws):
        fakes = _install(monkeypatch, failure=client_error("RequestLimitExceeded"))

        response = _call(fakes, "POST", COLLECTION, body={"capacityReservationId": CR_FREE, "label": "Later"})

        assert response["statusCode"] == 503
        assert reservations.get_registration(CR_FREE) is None

    def test_a_duplicate_is_a_409(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE)},
                         registered=[(CR_FREE, {"label": "First"})])

        response = _call(fakes, "POST", COLLECTION, body={"capacityReservationId": CR_FREE, "label": "Second"})

        assert response["statusCode"] == 409
        assert reservations.get_registration(CR_FREE)["label"] == "First"

    def test_the_hundred_and_first_is_a_400(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_OTHER: ec2_reservation(CR_OTHER)},
                         registered=[(CR_FREE, {}), (CR_FULL, {})])
        monkeypatch.setattr(reservations, "MAX_REGISTERED_CAPACITY_RESERVATIONS", 2)

        response = _call(fakes, "POST", COLLECTION, body={"capacityReservationId": CR_OTHER, "label": "Third"})

        assert response["statusCode"] == 400
        assert response_body(response)["message"] == "At most 2 capacity reservations can be registered"
        assert reservations.get_registration(CR_OTHER) is None

    @pytest.mark.parametrize("body", [
        {"capacityReservationId": "cr-123", "label": "Short id"},
        {"capacityReservationId": CR_FREE},
        {"capacityReservationId": CR_FREE, "label": ""},
        {"capacityReservationId": CR_FREE, "label": "x" * 65},
        {"capacityReservationId": CR_FREE, "label": "semi;colon"},
        {"capacityReservationId": CR_FREE, "label": "Ok", "fallbackToOnDemand": "yes"},
        {"label": "No id"},
    ])
    def test_a_malformed_body_is_a_400_before_any_read(self, monkeypatch, aws, body):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE)})

        response = _call(fakes, "POST", COLLECTION, body=body)

        assert response["statusCode"] == 400
        assert fakes.reservations.ec2.calls == []

    def test_a_label_of_sixty_four_characters_is_accepted(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE)})

        response = _call(fakes, "POST", COLLECTION, body={"capacityReservationId": CR_FREE, "label": "x" * 64})

        assert response["statusCode"] == 201


@pytest.mark.unit
class TestUpdate:
    def test_updates_the_given_fields(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE)},
                         registered=[(CR_FREE, {"label": "Old"})])

        response = _call(fakes, "PUT", _one(CR_FREE), body={"label": "New", "fallbackToOnDemand": False},
                         path_params={"capacityReservationId": CR_FREE})

        assert response["statusCode"] == 200
        body = response_body(response)
        assert body["label"] == "New" and body["fallbackToOnDemand"] is False and body["enabled"] is True
        assert body["state"] == "active"
        stored = reservations.get_registration(CR_FREE)
        assert stored["updatedBy"] == "carol" and stored["label"] == "New"
        [audit] = _audits(fakes, "edhCapacityReservationUpdate")
        assert audit["capacityReservation"]["label"] == "New"

    def test_an_unregistered_reservation_is_a_404(self, monkeypatch, aws):
        fakes = _install(monkeypatch, records={CR_FREE: ec2_reservation(CR_FREE)})

        response = _call(fakes, "PUT", _one(CR_FREE), body={"label": "New"},
                         path_params={"capacityReservationId": CR_FREE})

        assert response["statusCode"] == 404
        assert _audits(fakes, "edhCapacityReservationUpdate") == []

    @pytest.mark.parametrize("body", [{}, {"label": ""}, {"enabled": "no"}])
    def test_a_malformed_body_is_a_400(self, monkeypatch, aws, body):
        fakes = _install(monkeypatch, registered=[(CR_FREE, {"label": "Old"})])

        response = _call(fakes, "PUT", _one(CR_FREE), body=body, path_params={"capacityReservationId": CR_FREE})

        assert response["statusCode"] == 400
        assert reservations.get_registration(CR_FREE)["label"] == "Old"

    def test_a_malformed_path_id_is_a_400(self, monkeypatch, aws):
        fakes = _install(monkeypatch)

        response = _call(fakes, "PUT", _one("cr-xyz"), body={"label": "New"},
                         path_params={"capacityReservationId": "cr-xyz"})

        assert response["statusCode"] == 400

    def test_a_failed_read_after_the_update_answers_state_unknown(self, monkeypatch, aws):
        fakes = _install(monkeypatch, failure=client_error("RequestLimitExceeded"), registered=[(CR_FREE, {})])

        response = _call(fakes, "PUT", _one(CR_FREE), body={"enabled": False},
                         path_params={"capacityReservationId": CR_FREE})

        assert response["statusCode"] == 200
        assert response_body(response)["state"] == "unknown"
        assert reservations.get_registration(CR_FREE)["enabled"] is False


@pytest.mark.unit
class TestUnregister:
    def test_unregisters_without_touching_ec2(self, monkeypatch, aws):
        fakes = _install(monkeypatch, registered=[(CR_FREE, {})])
        monkeypatch.setattr(reservations, "ec2", Untouchable())

        response = _call(fakes, "DELETE", _one(CR_FREE), path_params={"capacityReservationId": CR_FREE})

        assert response["statusCode"] == 200
        assert response_body(response) == {"capacityReservationId": CR_FREE, "deleted": True}
        assert reservations.get_registration(CR_FREE) is None
        [audit] = _audits(fakes, "edhCapacityReservationDelete")
        assert audit["capacityReservationId"] == CR_FREE

    def test_leaves_the_rows_launched_into_it_alone(self, monkeypatch, aws):
        row = session_row(capacityReservationId=CR_FREE)
        fakes = _install(monkeypatch, registered=[(CR_FREE, {})], rows=[row])

        _call(fakes, "DELETE", _one(CR_FREE), path_params={"capacityReservationId": CR_FREE})

        fakes.table.update_item.assert_not_called()
        fakes.table.delete_item.assert_not_called()

    def test_an_unregistered_reservation_is_a_404(self, monkeypatch, aws):
        fakes = _install(monkeypatch)

        response = _call(fakes, "DELETE", _one(CR_FREE), path_params={"capacityReservationId": CR_FREE})

        assert response["statusCode"] == 404
        assert _audits(fakes, "edhCapacityReservationDelete") == []


@pytest.mark.unit
class TestInstanceItems:
    def test_an_instance_carries_its_reservation_and_label(self, monkeypatch, aws):
        rows = [
            session_row(bootstrapId="b-1", sessionId="s-1", capacityReservationId=CR_FREE),
            session_row(bootstrapId="b-2", sessionId="s-2", capacityReservationFallback=True,
                        capacityReservationRequested=CR_FREE),
            session_row(bootstrapId="b-3", sessionId="s-3", capacityReservationId=CR_OTHER),
            session_row(bootstrapId="b-4", sessionId="s-4"),
        ]
        desktops = [edh_desktop(f"s-{n}", rows[0]["edhUid"], "running") for n in range(1, 5)]
        fakes = _install(monkeypatch, registered=[(CR_FREE, {"label": "Render farm"})], rows=rows,
                         edh=FakeEdhClient(list_all_desktops=desktops))

        items = {item["sessionId"]: item for item in response_body(_call(fakes, "GET", INSTANCES_PATH))["Items"]}

        assert (items["s-1"]["capacityReservationId"], items["s-1"]["capacityReservationLabel"],
                items["s-1"]["capacityReservationFallback"]) == (CR_FREE, "Render farm", False)
        assert (items["s-2"]["capacityReservationId"], items["s-2"]["capacityReservationFallback"]) == (None, True)
        assert (items["s-3"]["capacityReservationId"], items["s-3"]["capacityReservationLabel"]) == (CR_OTHER, None)
        assert (items["s-4"]["capacityReservationId"], items["s-4"]["capacityReservationLabel"],
                items["s-4"]["capacityReservationFallback"]) == (None, None, False)

    def test_instances_without_a_reservation_read_no_registry(self, monkeypatch):
        rows = [session_row(bootstrapId="b-1", sessionId="s-1")]
        fakes = install_admin_fakes(monkeypatch, rows=rows,
                                    edh=FakeEdhClient(list_all_desktops=[edh_desktop("s-1", rows[0]["edhUid"],
                                                                                     "running")]))
        monkeypatch.setattr(reservations, "_session_table", Untouchable())

        response = _call(fakes, "GET", INSTANCES_PATH)

        assert response["statusCode"] == 200
        assert response_body(response)["Items"][0]["capacityReservationLabel"] is None

    def test_the_admin_session_scan_leaves_out_the_settings_rows(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch)

        _call(fakes, "GET", INSTANCES_PATH)

        expression = fakes.table.scan.call_args.kwargs["FilterExpression"].get_expression()
        assert expression["operator"] == "NOT"
        [inner] = expression["values"]
        assert inner.get_expression()["operator"] == "begins_with"
        assert inner.get_expression()["values"][1] == reservations.SETTINGS_KEY_PREFIX
