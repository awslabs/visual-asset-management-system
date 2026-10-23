# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reservations, registry rows and an EC2 fake shared by the capacity-reservation tests.

``FakeEc2`` answers ``describe_capacity_reservations`` from a dict of EC2 records keyed by id, the way
EC2 does: a request naming any id it does not hold fails as a whole with
``InvalidCapacityReservationId.NotFound``. ``failure`` makes every call raise instead. Every request is
recorded. ``install_reservation_fakes`` points the registry module at a moto session table and the fake
EC2 client; the handlers reach the module through its attribute, so one install serves them all.
"""

import os
from datetime import datetime, timezone
from types import SimpleNamespace

import boto3
from botocore.exceptions import ClientError

import backend.backend.handlers.addon.edh.common.edhCapacityReservations as reservations

CR_FREE = "cr-0123456789abcdef0"
CR_FULL = "cr-0fedcba9876543210"
CR_OTHER = "cr-00000000000000aaa"
END_DATE = datetime(2026, 10, 23, 17, 0, 0, tzinfo=timezone.utc)
NOT_FOUND_CODE = "InvalidCapacityReservationId.NotFound"


def ec2_reservation(cr_id=CR_FREE, **overrides):
    """A reservation as DescribeCapacityReservations returns it."""
    record = {
        "CapacityReservationId": cr_id,
        "InstanceType": "g5.xlarge",
        "InstancePlatform": "Linux/UNIX",
        "AvailabilityZone": "us-east-1a",
        "State": "active",
        "TotalInstanceCount": 2,
        "AvailableInstanceCount": 1,
        "InstanceMatchCriteria": "targeted",
        "EndDate": END_DATE,
        "ReservationType": "default",
    }
    record.update(overrides)
    return record


def client_error(code, operation="DescribeCapacityReservations"):
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class Untouchable:
    """A stand-in for a client or table no request may reach: any use fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"{name} was used")


class FakeEc2:
    """``describe_capacity_reservations`` over ``records`` (id -> EC2 record), as EC2 answers it."""

    def __init__(self, records=None, failure=None):
        self.records = dict(records or {})
        self.failure = failure
        self.calls = []

    def describe_capacity_reservations(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        ids = kwargs.get("CapacityReservationIds") or []
        if any(cr_id not in self.records for cr_id in ids):
            raise client_error(NOT_FOUND_CODE)
        return {"CapacityReservations": [dict(self.records[cr_id]) for cr_id in ids]}

    def ids_asked(self):
        return [list(call.get("CapacityReservationIds") or []) for call in self.calls]


def create_session_table(resource, *, indexes=False):
    """The session table as the add-on stack defines it: keyed on bootstrapId, with its two session indexes
    when ``indexes``."""
    table_name = os.environ["EDH_SESSION_STORAGE_TABLE_NAME"]
    spec = {
        "TableName": table_name,
        "KeySchema": [{"AttributeName": "bootstrapId", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "bootstrapId", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    }
    if indexes:
        spec["AttributeDefinitions"] += [
            {"AttributeName": "sessionId", "AttributeType": "S"},
            {"AttributeName": "userId", "AttributeType": "S"},
            {"AttributeName": "createdAt", "AttributeType": "S"},
        ]
        spec["GlobalSecondaryIndexes"] = [
            {"IndexName": "sessionUuidIndex", "KeySchema": [{"AttributeName": "sessionId", "KeyType": "HASH"}],
             "Projection": {"ProjectionType": "ALL"}},
            {"IndexName": "userIdIndex", "KeySchema": [{"AttributeName": "userId", "KeyType": "HASH"},
                                                       {"AttributeName": "createdAt", "KeyType": "RANGE"}],
             "Projection": {"ProjectionType": "ALL"}},
        ]
    resource.create_table(**spec)
    return resource.Table(table_name)


def install_reservation_fakes(monkeypatch, *, records=None, failure=None, registered=(), indexes=False):
    """Inside an active mock_aws: a moto session table and a FakeEc2 behind the registry module, with each
    of ``registered`` -- (id, fields) pairs -- written as a registry row."""
    resource = boto3.resource("dynamodb", region_name="us-east-1")
    table = create_session_table(resource, indexes=indexes)
    ec2 = FakeEc2(records, failure)
    monkeypatch.setattr(reservations, "_session_table", table)
    monkeypatch.setattr(reservations, "ec2", ec2)
    for cr_id, fields in registered:
        table.put_item(Item=registry_item(cr_id, **fields))
    return SimpleNamespace(module=reservations, table=table, ec2=ec2)


def registry_item(cr_id, **overrides):
    """A registry row as the admin register route writes it."""
    item = {
        "bootstrapId": reservations.REGISTRY_KEY_PREFIX + cr_id,
        "capacityReservationId": cr_id,
        "label": f"Reservation {cr_id[-3:]}",
        "fallbackToOnDemand": True,
        "enabled": True,
        "createdAt": "2026-10-23T15:00:00+00:00",
        "createdBy": "carol",
        "updatedAt": "2026-10-23T15:00:00+00:00",
        "updatedBy": "carol",
    }
    item.update(overrides)
    return item
