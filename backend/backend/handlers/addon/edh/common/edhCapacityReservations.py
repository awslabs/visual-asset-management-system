# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The EC2 capacity reservations VAMS may launch EDH desktops into: the registry and the live view.

The registry holds one row per reservation an administrator registered, in the session table under the
reserved key ``SETTINGS#capacityReservation#<cr-id>``. Like the lease settings row, the key cannot collide
with a session row's partition key, a 36-character UUID, and the row carries no ``state``, ``userId`` or
``sessionId``: the state-filtered sweep scan and the user and session-id index queries never yield it, and
the admin instance scan leaves out every ``SETTINGS#`` key. The registry is listed by a scan filtered on the
key prefix. A row holds the reservation id, its label, whether a launch into it falls back to on-demand
when it is full, whether it is offered, and who created and last changed it.

EC2's facts about a reservation are never stored: they are read live with ``DescribeCapacityReservations``
in the Lambda's own Region, which is EDH's (the add-on deploys beside the EDH cluster it fronts), in one call
for every id asked. EC2 refuses a whole call naming an id it does not hold, so on that refusal each id is read
on its own and the missing one is marked ``notFound``. Any other failure is a ``CapacityReservationReadError``
for the caller to decide on: a launch refuses, a listing lists none or reports ``unknown``.

A reservation that is ``expired``, ``cancelled``, ``failed`` or ``notFound`` has ended (``has_ended``). EDH picks
the subnet of a launch into a reservation among its private subnets in the reservation's Availability Zone, so
the live view also says whether EDH has one there (``edhSubnetAvailable``): the subnets are read from EDH's
``/edh/<cluster id>/configuration/PrivateSubnets`` parameter and mapped to their zones with ``DescribeSubnets``,
at most once every five minutes per container; a read that fails leaves it unknown (None) for that window.
"""

import os
import re
import time
from datetime import datetime, timezone
from typing import Dict, FrozenSet, Iterable, List, Optional

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from common.addonResourceNames import AddonResourceKeys
from common.resourceNames import get_table_name
from customLogging.logger import safeLogger

from .edhSessionState import EC2_RESERVATION_STATE_ACTIVE, reservation_has_free_slot, reservation_is_full

logger = safeLogger(service_name="EdhCapacityReservations")

# The reserved partition keys of the session table: every settings row starts with the first, and a registry
# row with the second followed by its reservation id.
SETTINGS_KEY_PREFIX = "SETTINGS#"
REGISTRY_KEY_PREFIX = "SETTINGS#capacityReservation#"
# A reservation id as EC2 issues it.
CAPACITY_RESERVATION_ID_PATTERN = re.compile(r"cr-[0-9a-f]{17}")
MAX_REGISTERED_CAPACITY_RESERVATIONS = 100
DEFAULT_FALLBACK_TO_ON_DEMAND = True
DEFAULT_ENABLED = True
REGISTRY_FIELDS = (
    "capacityReservationId", "label", "fallbackToOnDemand", "enabled", "createdAt", "createdBy", "updatedAt",
    "updatedBy",
)
# The fields an update may change.
REGISTRY_UPDATE_FIELDS = ("label", "fallbackToOnDemand", "enabled")

# The reservation state a launch needs, as EC2 spells it, and the two states VAMS adds: EC2 holds no such
# reservation, or the read failed.
EC2_STATE_ACTIVE = EC2_RESERVATION_STATE_ACTIVE
STATE_NOT_FOUND = "notFound"
STATE_UNKNOWN = "unknown"
# The EC2 states a reservation never leaves: registering one is refused. A reservation that is active, or not yet
# (pending, scheduled: a future-dated one), may be registered.
EC2_TERMINAL_STATES = frozenset({"expired", "cancelled", "failed"})
EC2_NOT_FOUND_CODES = frozenset({"InvalidCapacityReservationId.NotFound", "InvalidCapacityReservationId.Malformed"})
# The states of a reservation that has ended: one EC2 will never make active again, or one EC2 no longer holds. A
# stopped desktop launched into such a reservation can start only once it is moved to on-demand capacity.
RESERVATION_ENDED_STATES = EC2_TERMINAL_STATES | {STATE_NOT_FOUND}
# EC2's reservation type when it reports none.
DEFAULT_RESERVATION_TYPE = "default"
# Each live-view field and the EC2 field it is read from; endDate is written ISO-8601, or None.
EC2_VIEW_FIELDS = {
    "instanceType": "InstanceType",
    "availabilityZone": "AvailabilityZone",
    "instancePlatform": "InstancePlatform",
    "state": "State",
    "totalInstanceCount": "TotalInstanceCount",
    "availableInstanceCount": "AvailableInstanceCount",
    "instanceMatchCriteria": "InstanceMatchCriteria",
    "endDate": "EndDate",
    "reservationType": "ReservationType",
}
VIEW_FIELDS = ("capacityReservationId", "label", "fallbackToOnDemand", "enabled", *EC2_VIEW_FIELDS)
# The live-view field saying whether EDH has a private subnet in the reservation's Availability Zone: True, False,
# or None when that is not known (EDH's subnets or the reservation's zone were not read).
SUBNET_AVAILABLE_FIELD = "edhSubnetAvailable"
# EDH's private subnets: the String parameter under EDH's own ``/edh/<cluster id>`` prefix that EDH picks a
# reservation launch's subnet from, its value a Python list repr of subnet ids, which is read with a pattern and
# never evaluated; and how long a container keeps the Availability Zones they map to, or the failure to read them.
EDH_PRIVATE_SUBNETS_PARAMETER = "/edh/{cluster_id}/configuration/PrivateSubnets"
SUBNET_ID_PATTERN = re.compile(r"subnet-[0-9a-f]+")
SUBNET_ZONES_CACHE_SECONDS = 300
# The reservation platform that suits an image of each OS family. EDH requires the reservation's platform to
# equal the AMI's PlatformDetails, which is ``Linux/UNIX`` for the Linux images and starts ``Windows`` for the
# Windows ones; EC2's other Linux platforms (Red Hat, SUSE, SQL Server) belong to licensed images an EDH Linux
# image is not.
PLATFORM_PREFIX_BY_OS_FAMILY = {"linux": "Linux/UNIX", "windows": "Windows"}


class CapacityReservationReadError(Exception):
    """EC2 could not be read: the reservation's state is unknown, not absent."""


class RegistrationExists(Exception):
    """The reservation is registered already."""


class RegistryFull(Exception):
    """The registry holds the most reservations it may."""


class RegistrationNotFound(Exception):
    """The reservation is not registered."""


retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource("dynamodb", config=retry_config)
ec2 = boto3.client("ec2", config=retry_config)
ssm = boto3.client("ssm", config=retry_config)

try:
    _session_table_name = get_table_name(AddonResourceKeys.EDH_SESSION_STORAGE_TABLE)
    _edh_cluster_id = os.environ["EDH_CLUSTER_ID"]
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

_session_table = dynamodb.Table(_session_table_name)
# The Availability Zones of EDH's private subnets as this container last read them (a frozenset, or None when the
# read failed), and the monotonic instant of that read (None before the first).
_subnet_zones_cache = {"zones": None, "readAt": None}


def is_capacity_reservation_id(value) -> bool:
    return isinstance(value, str) and CAPACITY_RESERVATION_ID_PATTERN.fullmatch(value) is not None


def registry_key(capacity_reservation_id: str) -> str:
    return REGISTRY_KEY_PREFIX + capacity_reservation_id


def session_rows_only():
    """The scan filter that leaves out every settings row of the session table."""
    return ~Attr("bootstrapId").begins_with(SETTINGS_KEY_PREFIX)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _monotonic() -> float:
    return time.monotonic()


def normalise_registration(item: dict) -> dict:
    """A registry row as the handlers read it: the registry fields only, the flags as booleans."""
    registration = {key: item.get(key) for key in REGISTRY_FIELDS}
    registration["fallbackToOnDemand"] = bool(item.get("fallbackToOnDemand", DEFAULT_FALLBACK_TO_ON_DEMAND))
    registration["enabled"] = bool(item.get("enabled", DEFAULT_ENABLED))
    return registration


def get_registration(capacity_reservation_id: str) -> Optional[dict]:
    """The registration of the reservation, read consistently; None when it is not registered."""
    item = _session_table.get_item(Key={"bootstrapId": registry_key(capacity_reservation_id)},
                                   ConsistentRead=True).get("Item")
    return normalise_registration(item) if item else None


def list_registrations() -> List[dict]:
    """Every registration: a scan filtered on the registry key prefix, paged on the presence of
    LastEvaluatedKey, which DynamoDB omits only once the table is exhausted."""
    kwargs = {"FilterExpression": Attr("bootstrapId").begins_with(REGISTRY_KEY_PREFIX), "ConsistentRead": True}
    registrations = []
    while True:
        response = _session_table.scan(**kwargs)
        registrations.extend(normalise_registration(item) for item in response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return registrations
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def register(capacity_reservation_id: str, label: str, fallback_to_on_demand: Optional[bool],
             enabled: Optional[bool], user_id: str) -> dict:
    """Write the registration, the flags defaulting when None. Raises ``RegistryFull`` when the registry holds
    ``MAX_REGISTERED_CAPACITY_RESERVATIONS`` already and ``RegistrationExists`` for an id it holds; the put is
    conditional on the key, so of two registers of one id only one lands."""
    if len(list_registrations()) >= MAX_REGISTERED_CAPACITY_RESERVATIONS:
        raise RegistryFull(f"At most {MAX_REGISTERED_CAPACITY_RESERVATIONS} capacity reservations can be registered")
    now = _utc_now()
    item = {
        "bootstrapId": registry_key(capacity_reservation_id),
        "capacityReservationId": capacity_reservation_id,
        "label": label,
        "fallbackToOnDemand": DEFAULT_FALLBACK_TO_ON_DEMAND if fallback_to_on_demand is None
        else bool(fallback_to_on_demand),
        "enabled": DEFAULT_ENABLED if enabled is None else bool(enabled),
        "createdAt": now,
        "createdBy": user_id,
        "updatedAt": now,
        "updatedBy": user_id,
    }
    try:
        _session_table.put_item(Item=item, ConditionExpression="attribute_not_exists(bootstrapId)")
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        raise RegistrationExists(capacity_reservation_id) from err
    return normalise_registration(item)


def update_registration(capacity_reservation_id: str, patch: dict, user_id: str) -> dict:
    """Set the non-None ``REGISTRY_UPDATE_FIELDS`` of ``patch`` on the registration and return it as the write
    left it. Raises ``RegistrationNotFound`` when the reservation is not registered."""
    attributes = {key: patch[key] for key in REGISTRY_UPDATE_FIELDS if patch.get(key) is not None}
    attributes.update(updatedAt=_utc_now(), updatedBy=user_id)
    keys = list(attributes)
    names = {"#a" + str(i): key for i, key in enumerate(keys)}
    values = {":v" + str(i): attributes[key] for i, key in enumerate(keys)}
    try:
        response = _session_table.update_item(
            Key={"bootstrapId": registry_key(capacity_reservation_id)},
            UpdateExpression="SET " + ", ".join(name + " = :v" + name[2:] for name in names),
            ConditionExpression="attribute_exists(bootstrapId)",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        raise RegistrationNotFound(capacity_reservation_id) from err
    return normalise_registration(response.get("Attributes") or {})


def unregister(capacity_reservation_id: str) -> None:
    """Delete the registration; EC2 is not touched. Raises ``RegistrationNotFound`` when it is not registered."""
    try:
        _session_table.delete_item(Key={"bootstrapId": registry_key(capacity_reservation_id)},
                                   ConditionExpression="attribute_exists(bootstrapId)")
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        raise RegistrationNotFound(capacity_reservation_id) from err


def labels_for(capacity_reservation_ids: Iterable) -> Dict[str, Optional[str]]:
    """The label of each distinct id, None for one not registered; no read for no id. A read that fails is
    logged and answers None for that id, so a listing is never refused for a label."""
    labels: Dict[str, Optional[str]] = {}
    for capacity_reservation_id in capacity_reservation_ids:
        if not capacity_reservation_id or capacity_reservation_id in labels:
            continue
        try:
            item = _session_table.get_item(Key={"bootstrapId": registry_key(str(capacity_reservation_id))}).get("Item")
        except (ClientError, BotoCoreError) as err:
            logger.warning(f"Capacity reservation label not read ({type(err).__name__})")
            item = None
        labels[capacity_reservation_id] = item.get("label") if item else None
    return labels


def _facts(record: dict) -> dict:
    """The live-view fields of one EC2 reservation record."""
    facts = {field: record.get(ec2_field) for field, ec2_field in EC2_VIEW_FIELDS.items()}
    end_date = facts["endDate"]
    facts["endDate"] = end_date.isoformat() if isinstance(end_date, datetime) else (end_date or None)
    facts["reservationType"] = facts["reservationType"] or DEFAULT_RESERVATION_TYPE
    return facts


def _describe(capacity_reservation_ids: List[str]) -> Dict[str, dict]:
    """EC2's records of the ids, by id, following NextToken; raises EC2's ClientError as is."""
    found: Dict[str, dict] = {}
    kwargs = {"CapacityReservationIds": list(capacity_reservation_ids)}
    while True:
        response = ec2.describe_capacity_reservations(**kwargs)
        for record in response.get("CapacityReservations") or []:
            if isinstance(record, dict) and record.get("CapacityReservationId"):
                found[record["CapacityReservationId"]] = _facts(record)
        token = response.get("NextToken")
        if not token:
            return found
        kwargs["NextToken"] = token


def _not_found(err: Exception) -> bool:
    return isinstance(err, ClientError) and err.response.get("Error", {}).get("Code") in EC2_NOT_FOUND_CODES


def read_live(capacity_reservation_ids: Iterable[str]) -> Dict[str, dict]:
    """EC2's facts about each distinct id, by id: the live-view fields, or ``{"state": "notFound"}`` for an id
    EC2 does not hold. One call reads every id; when EC2 refuses it for an id it does not hold, each id is read
    on its own. Raises ``CapacityReservationReadError`` on any other failure."""
    ids = list(dict.fromkeys(capacity_reservation_ids))
    if not ids:
        return {}
    try:
        return _with_missing(ids, _describe(ids))
    except (ClientError, BotoCoreError) as err:
        if not _not_found(err):
            raise CapacityReservationReadError(type(err).__name__) from err
    live: Dict[str, dict] = {}
    for capacity_reservation_id in ids:
        try:
            live.update(_with_missing([capacity_reservation_id], _describe([capacity_reservation_id])))
        except (ClientError, BotoCoreError) as err:
            if not _not_found(err):
                raise CapacityReservationReadError(type(err).__name__) from err
            live[capacity_reservation_id] = {"state": STATE_NOT_FOUND}
    return live


def _with_missing(ids: List[str], found: Dict[str, dict]) -> Dict[str, dict]:
    """``found`` with every id it lacks marked notFound."""
    return {capacity_reservation_id: found.get(capacity_reservation_id, {"state": STATE_NOT_FOUND})
            for capacity_reservation_id in ids}


def view(registration: dict, facts: Optional[dict], subnet_zones: Optional[FrozenSet[str]] = None) -> dict:
    """The reservation as the API returns it (``CapacityReservationView``): the registration, EC2's facts, and
    whether EDH has a private subnet in its Availability Zone, given ``subnet_zones`` (``subnet_zones()``; None,
    not known, leaves that None). Facts of None, a read that failed, are state ``unknown``; a field EC2 did not
    answer is None."""
    facts = facts if facts is not None else {"state": STATE_UNKNOWN}
    fields = {field: facts.get(field) if field in EC2_VIEW_FIELDS else registration.get(field) for field in VIEW_FIELDS}
    fields[SUBNET_AVAILABLE_FIELD] = subnet_available(facts, subnet_zones)
    return fields


def has_ended(facts: Optional[dict]) -> bool:
    """Whether the reservation, as read live, has ended (``RESERVATION_ENDED_STATES``): False for one active, yet
    to become active, or not read (None, or state ``unknown``)."""
    return (facts or {}).get("state") in RESERVATION_ENDED_STATES


def parse_subnet_ids(value) -> List[str]:
    """The distinct subnet ids EDH's ``PrivateSubnets`` value names, in its order: every ``subnet-<hex>`` in the
    text, which is a Python list repr; nothing for a value that is not a string or names none."""
    return list(dict.fromkeys(SUBNET_ID_PATTERN.findall(value))) if isinstance(value, str) else []


def _subnet_zone_set(subnet_ids: List[str]) -> FrozenSet[str]:
    """The Availability Zones of the subnets, as EC2 describes them, following NextToken; raises EC2's error."""
    zones = set()
    kwargs = {"SubnetIds": subnet_ids}
    while True:
        response = ec2.describe_subnets(**kwargs)
        for subnet in response.get("Subnets") or []:
            zone = subnet.get("AvailabilityZone") if isinstance(subnet, dict) else None
            if isinstance(zone, str) and zone:
                zones.add(zone)
        token = response.get("NextToken")
        if not token:
            return frozenset(zones)
        kwargs["NextToken"] = token


def _read_subnet_zones() -> Optional[FrozenSet[str]]:
    """The Availability Zones of EDH's private subnets, read now; None (logged) when the parameter or the subnets
    could not be read, or the parameter names no subnet, which is not taken to mean EDH has none."""
    name = EDH_PRIVATE_SUBNETS_PARAMETER.format(cluster_id=_edh_cluster_id)
    try:
        value = ((ssm.get_parameter(Name=name).get("Parameter") or {}).get("Value"))
        subnet_ids = parse_subnet_ids(value)
        if not subnet_ids:
            logger.warning("EDH's private subnets parameter names no subnet; the Availability Zone check is skipped")
            return None
        return _subnet_zone_set(subnet_ids)
    except (ClientError, BotoCoreError) as err:
        logger.warning(f"EDH's private subnets not read ({type(err).__name__}); the Availability Zone check is "
                       f"skipped")
        return None


def subnet_zones() -> Optional[FrozenSet[str]]:
    """The Availability Zones EDH has a private subnet in, or None when they are not known; read at most once per
    ``SUBNET_ZONES_CACHE_SECONDS`` in a container, a failed read included, so its warning is logged once per
    window."""
    now = _monotonic()
    read_at = _subnet_zones_cache["readAt"]
    if read_at is None or now - read_at >= SUBNET_ZONES_CACHE_SECONDS:
        _subnet_zones_cache["zones"] = _read_subnet_zones()
        _subnet_zones_cache["readAt"] = now
    return _subnet_zones_cache["zones"]


def subnet_available(facts: Optional[dict], zones: Optional[FrozenSet[str]]) -> Optional[bool]:
    """Whether EDH has a private subnet in the reservation's Availability Zone: None when EDH's zones or the
    reservation's zone are not known."""
    zone = (facts or {}).get("availabilityZone")
    if zones is None or not isinstance(zone, str) or not zone:
        return None
    return zone in zones


def platform_suits(platform, os_family) -> bool:
    """Whether a reservation of ``platform`` can hold a desktop of an image of ``os_family``: a platform starting
    ``Linux/UNIX`` for a Linux image, one starting ``Windows`` for a Windows image."""
    if not isinstance(platform, str):
        return False
    prefix = PLATFORM_PREFIX_BY_OS_FAMILY.get(os_family) if isinstance(os_family, str) else None
    return prefix is not None and platform.startswith(prefix)


def has_free_slot(facts: dict) -> bool:
    """Whether the reservation is active with at least one instance slot free (``edhSessionState``'s rule)."""
    return reservation_has_free_slot(facts)


def is_full(facts: dict) -> bool:
    """Whether the reservation is active with no instance slot free (``edhSessionState``'s rule)."""
    return reservation_is_full(facts)
