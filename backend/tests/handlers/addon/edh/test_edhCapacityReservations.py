# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The capacity-reservation registry and its live EC2 view.

The registry rows live in the session table under ``SETTINGS#capacityReservation#<id>`` and carry no
``state``: the tests below write them beside session rows and the lease row in a moto table and run every
session-row read the add-on makes -- the reconciler's swept-rows scan, the admin instance scan, the user and
session-id index queries -- to show none of them yields a registry row, while the registry's own prefix scan
yields only registry rows. EC2's facts are read live, one batched call for every id, falling back to one read
per id when EC2 refuses the batch for an id it does not hold.
"""

from datetime import datetime, timezone

import pytest
from boto3.dynamodb.conditions import Key
from moto import mock_aws

import backend.backend.handlers.addon.edh.edhAdminService as admin_service
import backend.backend.handlers.addon.edh.edhSessionReconciler as reconciler
from backend.backend.handlers.addon.edh.common import edhCapacityReservations as reservations
from backend.backend.handlers.addon.edh.common.edhLeasePolicy import LEASE_SETTINGS_KEY
from backend.tests.handlers.addon.edh.edhReservationTestSupport import (
    CR_FREE, CR_FULL, CR_OTHER, END_DATE, NOT_FOUND_CODE, FakeEc2, client_error, ec2_reservation,
    install_reservation_fakes, registry_item,
)

SESSION_ROW = {
    "bootstrapId": "3f2a9c1e-4b7d-4e0a-9d2b-6c1f8a7e5b30", "sessionId": "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b",
    "userId": "alice", "state": "ACTIVE", "createdAt": "2026-10-23T10:00:00+00:00",
    "updatedAt": "2026-10-23T10:00:00+00:00",
}
DELETED_ROW = {
    "bootstrapId": "7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f", "userId": "alice", "state": "DELETED",
    "createdAt": "2026-10-23T09:00:00+00:00", "updatedAt": "2026-10-23T09:00:00+00:00",
}


@pytest.fixture
def registry(monkeypatch):
    with mock_aws():
        yield install_reservation_fakes(monkeypatch, indexes=True)


@pytest.mark.unit
class TestRegistryKey:
    def test_the_key_cannot_collide_with_a_uuid_bootstrap_id(self):
        key = reservations.registry_key(CR_FREE)
        assert key == "SETTINGS#capacityReservation#" + CR_FREE
        assert "#" in key and len(key) != 36
        assert key.startswith(reservations.SETTINGS_KEY_PREFIX)
        assert LEASE_SETTINGS_KEY.startswith(reservations.SETTINGS_KEY_PREFIX)

    @pytest.mark.parametrize("value,expected", [
        (CR_FREE, True), ("cr-0123456789abcdef", False), ("cr-0123456789ABCDEF0", False),
        ("cr-0123456789abcdef00", False), ("ami-0123456789abcdef0", False), (None, False), (42, False),
    ])
    def test_the_id_shape(self, value, expected):
        assert reservations.is_capacity_reservation_id(value) is expected


@pytest.mark.unit
class TestRegistryCrud:
    def test_register_writes_one_row_with_defaults_and_audit_fields(self, registry):
        saved = reservations.register(CR_FREE, "Render farm", None, None, "carol")

        stored = registry.table.get_item(Key={"bootstrapId": reservations.registry_key(CR_FREE)})["Item"]
        assert stored["capacityReservationId"] == CR_FREE
        assert stored["label"] == "Render farm"
        assert stored["fallbackToOnDemand"] is True and stored["enabled"] is True
        assert stored["createdBy"] == stored["updatedBy"] == "carol"
        assert stored["createdAt"] == stored["updatedAt"]
        assert "state" not in stored
        assert saved == reservations.get_registration(CR_FREE)

    def test_register_keeps_the_given_flags(self, registry):
        saved = reservations.register(CR_FREE, "Strict", False, False, "carol")
        assert (saved["fallbackToOnDemand"], saved["enabled"]) == (False, False)

    def test_a_second_register_of_one_id_is_a_conflict_and_leaves_the_first(self, registry):
        reservations.register(CR_FREE, "First", True, True, "carol")

        with pytest.raises(reservations.RegistrationExists):
            reservations.register(CR_FREE, "Second", False, False, "dave")

        assert reservations.get_registration(CR_FREE)["label"] == "First"

    def test_register_refuses_the_hundred_and_first(self, registry, monkeypatch):
        monkeypatch.setattr(reservations, "MAX_REGISTERED_CAPACITY_RESERVATIONS", 2)
        reservations.register(CR_FREE, "One", True, True, "carol")
        reservations.register(CR_FULL, "Two", True, True, "carol")

        with pytest.raises(reservations.RegistryFull):
            reservations.register(CR_OTHER, "Three", True, True, "carol")

        assert reservations.get_registration(CR_OTHER) is None

    def test_the_ceiling_is_one_hundred(self):
        assert reservations.MAX_REGISTERED_CAPACITY_RESERVATIONS == 100

    def test_update_merges_the_given_fields_and_keeps_the_creator(self, registry):
        reservations.register(CR_FREE, "Old", True, True, "carol")

        updated = reservations.update_registration(CR_FREE, {"label": "New", "enabled": False,
                                                             "fallbackToOnDemand": None}, "dave")

        assert updated["label"] == "New" and updated["enabled"] is False
        assert updated["fallbackToOnDemand"] is True
        assert updated["createdBy"] == "carol" and updated["updatedBy"] == "dave"
        assert reservations.get_registration(CR_FREE) == updated

    def test_update_of_an_unregistered_id_is_not_found_and_writes_nothing(self, registry):
        with pytest.raises(reservations.RegistrationNotFound):
            reservations.update_registration(CR_FREE, {"label": "New"}, "dave")
        assert registry.table.scan()["Items"] == []

    def test_unregister_deletes_the_row(self, registry):
        reservations.register(CR_FREE, "Gone", True, True, "carol")

        reservations.unregister(CR_FREE)

        assert reservations.get_registration(CR_FREE) is None

    def test_unregister_of_an_unregistered_id_is_not_found(self, registry):
        with pytest.raises(reservations.RegistrationNotFound):
            reservations.unregister(CR_FREE)

    def test_get_of_an_unregistered_id_is_none(self, registry):
        assert reservations.get_registration(CR_FREE) is None

    def test_labels_for_joins_registered_ids_and_answers_none_for_the_rest(self, registry):
        reservations.register(CR_FREE, "Render farm", True, True, "carol")

        assert reservations.labels_for([CR_FREE, CR_OTHER, CR_FREE]) == {CR_FREE: "Render farm", CR_OTHER: None}

    def test_labels_for_no_id_reads_nothing(self, monkeypatch):
        table = object()
        monkeypatch.setattr(reservations, "_session_table", table)
        assert reservations.labels_for([]) == {}
        assert reservations.labels_for([None, ""]) == {}


@pytest.mark.unit
class TestPrefixIsolation:
    """Registry rows never reach a session-row read, and the registry listing reads nothing else."""

    def _seed(self, registry):
        for item in (SESSION_ROW, DELETED_ROW, {"bootstrapId": LEASE_SETTINGS_KEY, "enabled": True},
                     registry_item(CR_FREE), registry_item(CR_FULL)):
            registry.table.put_item(Item=item)

    def test_the_registry_listing_yields_only_registry_rows(self, registry):
        self._seed(registry)

        listed = reservations.list_registrations()

        assert sorted(item["capacityReservationId"] for item in listed) == sorted([CR_FREE, CR_FULL])

    def test_the_registry_listing_is_a_prefix_scan_paged_to_exhaustion(self, registry, monkeypatch):
        self._seed(registry)
        scans = []
        real_scan = registry.table.scan

        def scan(**kwargs):
            scans.append(kwargs)
            return real_scan(**{**kwargs, "Limit": 1})

        monkeypatch.setattr(registry.table, "scan", scan)

        listed = reservations.list_registrations()

        assert len(listed) == 2
        assert len(scans) >= 5, "a one-item page per row must be threaded to the end"
        assert all("FilterExpression" in kwargs for kwargs in scans)

    def test_the_reconciler_swept_rows_scan_never_yields_a_registry_row(self, registry, monkeypatch):
        self._seed(registry)
        monkeypatch.setattr(reconciler, "session_table", registry.table)

        swept = list(reconciler._scan_swept_rows(datetime.now(timezone.utc)))

        keys = {row["bootstrapId"] for row in swept}
        assert SESSION_ROW["bootstrapId"] in keys
        assert not any(key.startswith(reservations.SETTINGS_KEY_PREFIX) for key in keys)

    def test_the_admin_instance_scan_never_yields_a_settings_row(self, registry, monkeypatch):
        self._seed(registry)
        monkeypatch.setattr(admin_service, "edh_session_table", registry.table)

        scanned = admin_service._scan_to_exhaustion()

        keys = {row["bootstrapId"] for row in scanned}
        assert keys == {SESSION_ROW["bootstrapId"], DELETED_ROW["bootstrapId"]}

    def test_the_session_indexes_never_hold_a_registry_row(self, registry):
        self._seed(registry)
        for index in ("sessionUuidIndex", "userIdIndex"):
            items = registry.table.scan(IndexName=index)["Items"]
            assert items, f"control: {index} holds the session rows"
            assert not any(item["bootstrapId"].startswith(reservations.SETTINGS_KEY_PREFIX) for item in items)
        by_user = registry.table.query(IndexName="userIdIndex", KeyConditionExpression=Key("userId").eq("alice"))
        assert {item["bootstrapId"] for item in by_user["Items"]} == {SESSION_ROW["bootstrapId"],
                                                                      DELETED_ROW["bootstrapId"]}

    def test_a_registry_row_carries_no_state_user_or_session(self, registry):
        reservations.register(CR_FREE, "Render farm", True, True, "carol")
        stored = registry.table.get_item(Key={"bootstrapId": reservations.registry_key(CR_FREE)})["Item"]
        assert not {"state", "userId", "sessionId"} & set(stored)


@pytest.mark.unit
class TestLiveRead:
    def test_one_batched_call_reads_every_id(self, monkeypatch):
        ec2 = FakeEc2({CR_FREE: ec2_reservation(CR_FREE), CR_FULL: ec2_reservation(CR_FULL, AvailableInstanceCount=0)})
        monkeypatch.setattr(reservations, "ec2", ec2)

        live = reservations.read_live([CR_FREE, CR_FULL, CR_FREE])

        assert ec2.ids_asked() == [[CR_FREE, CR_FULL]]
        assert live[CR_FREE]["availableInstanceCount"] == 1
        assert live[CR_FULL]["availableInstanceCount"] == 0

    def test_no_id_makes_no_call(self, monkeypatch):
        ec2 = FakeEc2()
        monkeypatch.setattr(reservations, "ec2", ec2)
        assert reservations.read_live([]) == {}
        assert ec2.calls == []

    def test_a_missing_id_falls_back_to_one_read_per_id_and_is_marked_not_found(self, monkeypatch):
        ec2 = FakeEc2({CR_FREE: ec2_reservation(CR_FREE)})
        monkeypatch.setattr(reservations, "ec2", ec2)

        live = reservations.read_live([CR_FREE, CR_OTHER])

        assert ec2.ids_asked() == [[CR_FREE, CR_OTHER], [CR_FREE], [CR_OTHER]]
        assert live[CR_FREE]["state"] == "active"
        assert live[CR_OTHER] == {"state": reservations.STATE_NOT_FOUND}

    @pytest.mark.parametrize("failure", [client_error("UnauthorizedOperation"), client_error("RequestLimitExceeded")])
    def test_any_other_failure_is_a_read_error(self, monkeypatch, failure):
        monkeypatch.setattr(reservations, "ec2", FakeEc2(failure=failure))
        with pytest.raises(reservations.CapacityReservationReadError):
            reservations.read_live([CR_FREE])

    def test_a_failure_during_the_per_id_fallback_is_a_read_error(self, monkeypatch):
        class Flaky(FakeEc2):
            def describe_capacity_reservations(self, **kwargs):
                if len(self.calls) >= 1:
                    self.calls.append(kwargs)
                    raise client_error("RequestLimitExceeded")
                return super().describe_capacity_reservations(**kwargs)

        monkeypatch.setattr(reservations, "ec2", Flaky({CR_FREE: ec2_reservation(CR_FREE)}))
        with pytest.raises(reservations.CapacityReservationReadError):
            reservations.read_live([CR_FREE, CR_OTHER])

    def test_the_next_token_is_followed(self, monkeypatch):
        class Paged(FakeEc2):
            def describe_capacity_reservations(self, **kwargs):
                self.calls.append(kwargs)
                if "NextToken" not in kwargs:
                    return {"CapacityReservations": [ec2_reservation(CR_FREE)], "NextToken": "t1"}
                return {"CapacityReservations": [ec2_reservation(CR_FULL)]}

        ec2 = Paged()
        monkeypatch.setattr(reservations, "ec2", ec2)

        live = reservations.read_live([CR_FREE, CR_FULL])

        assert set(live) == {CR_FREE, CR_FULL}
        assert ec2.calls[1]["NextToken"] == "t1"

    def test_the_facts_map_onto_the_view_fields(self, monkeypatch):
        monkeypatch.setattr(reservations, "ec2", FakeEc2({CR_FREE: ec2_reservation(CR_FREE)}))

        facts = reservations.read_live([CR_FREE])[CR_FREE]

        assert facts == {
            "instanceType": "g5.xlarge", "availabilityZone": "us-east-1a", "instancePlatform": "Linux/UNIX",
            "state": "active", "totalInstanceCount": 2, "availableInstanceCount": 1,
            "instanceMatchCriteria": "targeted", "endDate": END_DATE.isoformat(), "reservationType": "default",
        }

    def test_an_open_ended_capacity_block_maps_its_type_and_a_null_end(self, monkeypatch):
        record = ec2_reservation(CR_FREE, ReservationType="capacity-block", InstanceMatchCriteria="open")
        record.pop("EndDate")
        monkeypatch.setattr(reservations, "ec2", FakeEc2({CR_FREE: record}))

        facts = reservations.read_live([CR_FREE])[CR_FREE]

        assert facts["endDate"] is None
        assert facts["reservationType"] == "capacity-block"
        assert facts["instanceMatchCriteria"] == "open"

    def test_a_reservation_type_ec2_omits_reads_as_default(self, monkeypatch):
        record = ec2_reservation(CR_FREE)
        record.pop("ReservationType")
        monkeypatch.setattr(reservations, "ec2", FakeEc2({CR_FREE: record}))
        assert reservations.read_live([CR_FREE])[CR_FREE]["reservationType"] == "default"

    def test_the_not_found_code_is_ec2s(self):
        assert NOT_FOUND_CODE in reservations.EC2_NOT_FOUND_CODES


@pytest.mark.unit
class TestView:
    def test_the_view_joins_the_registration_and_the_facts(self):
        registration = reservations.normalise_registration(registry_item(CR_FREE, label="Render farm"))
        facts = {"instanceType": "g5.xlarge", "availabilityZone": "us-east-1a", "instancePlatform": "Linux/UNIX",
                 "state": "active", "totalInstanceCount": 2, "availableInstanceCount": 1,
                 "instanceMatchCriteria": "targeted", "endDate": None, "reservationType": "default"}

        view = reservations.view(registration, facts)

        assert view == {"capacityReservationId": CR_FREE, "label": "Render farm", "fallbackToOnDemand": True,
                        "enabled": True, **facts}

    def test_a_not_found_reservation_keeps_its_state_and_nulls_the_rest(self):
        registration = reservations.normalise_registration(registry_item(CR_OTHER))
        view = reservations.view(registration, {"state": reservations.STATE_NOT_FOUND})
        assert view["state"] == "notFound"
        assert view["instanceType"] is None and view["availableInstanceCount"] is None
        assert view["reservationType"] is None

    def test_a_failed_read_is_state_unknown(self):
        registration = reservations.normalise_registration(registry_item(CR_FREE))
        view = reservations.view(registration, None)
        assert view["state"] == reservations.STATE_UNKNOWN == "unknown"
        assert view["totalInstanceCount"] is None

    @pytest.mark.parametrize("platform,os_family,expected", [
        ("Linux/UNIX", "linux", True),
        ("Linux with SQL Server Standard", "linux", False),
        ("Red Hat Enterprise Linux", "linux", False),
        ("Windows", "windows", True),
        ("Windows with SQL Server", "windows", True),
        ("Linux/UNIX", "windows", False),
        ("Windows", "linux", False),
        (None, "linux", False),
        ("Linux/UNIX", None, False),
    ])
    def test_the_platform_suits_the_os_family(self, platform, os_family, expected):
        assert reservations.platform_suits(platform, os_family) is expected

    @pytest.mark.parametrize("facts,free,full", [
        ({"state": "active", "availableInstanceCount": 1}, True, False),
        ({"state": "active", "availableInstanceCount": 0}, False, True),
        ({"state": "expired", "availableInstanceCount": 0}, False, False),
        ({"state": "notFound"}, False, False),
        ({"state": "active", "availableInstanceCount": None}, False, True),
    ])
    def test_free_and_full(self, facts, free, full):
        assert reservations.has_free_slot(facts) is free
        assert reservations.is_full(facts) is full
