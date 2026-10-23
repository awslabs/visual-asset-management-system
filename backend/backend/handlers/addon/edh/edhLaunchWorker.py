# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asynchronous launch worker for EDH workstations.

The API handler records a launch as a CREATING session row and hands it here, because provisioning
a directory user and creating a desktop take longer than the API Gateway integration allows. The
worker makes EDH know the user, creates the desktop under the bootstrap id's session name and reads
the session id back, sets the desktop's EDH schedule to one that never starts it automatically, then
completes the row and, when the launch minted a session credential, binds its secret to the EC2 instance
EDH placed the desktop on. A schedule EDH does not take is left to the session sweep and never fails the
launch. A refusal EDH would repeat marks the row ORPHANED with a generic code; anything transient is
left for a retried invocation, which finds the row unchanged -- except on the last attempt, which
orphans the row rather than leave a launch nobody settles. Each invocation counts its attempt on the row
with a write conditioned on the row being as it read it, so of two deliveries of one launch only one
asks EDH for a desktop. A row launched into a capacity reservation is created in it; when EDH refuses that
create and the reservation, read again, is full, the worker creates once more on-demand if the reservation's
registration allows a fallback, and orphans the row RESERVATION_FULL if it does not.
"""

import os
import time
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from aws_lambda_powertools.utilities.typing import LambdaContext
from common.addonResourceNames import AddonResourceKeys
from common.resourceNames import get_table_name
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_actions
from .common import edhCapacityReservations as reservations
from .common import edhSessionBootstrap as bootstrap
from .common.edhClient import (
    EDH_ROUTE_DESKTOPS_CREATE,
    EDH_ROUTE_DESKTOPS_LIST_ALL,
    EDH_ROUTE_DESKTOPS_SCHEDULE,
    EDH_ROUTE_LDAP_USER,
    EdhApiError, EdhCapacityError, EdhClient, EdhError, EdhNotFoundError, EdhQuotaError, edh_session_name,
)
from .common.edhCatalogFields import OS_FAMILY_WINDOWS
from .common.edhIdentity import display_name_for
from .common.edhSessionState import (
    EDH_FIELD_INSTANCE_ID, EDH_SCHEDULE_FIELD,
    LAST_ERROR_CAPACITY_UNAVAILABLE, LAST_ERROR_EDH_REFUSED, LAST_ERROR_IMAGE_UNAVAILABLE, LAST_ERROR_LAUNCH_ERROR,
    LAST_ERROR_QUOTA_EXCEEDED, LAST_ERROR_RESERVATION_FULL, LAST_ERROR_USER_NOT_PROVISIONED, MAX_LAUNCH_ATTEMPTS,
    STATE_CREATING, STATE_ORPHANED, WORKER_PAYLOAD_KEYS,
    never_auto_start_schedule, observed_row_attributes, reservation_refusal_last_error, schedule_never_auto_starts,
)

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="EdhLaunchWorker")

# EDH desktop create form fields (a subset of the client's EDH_DESKTOP_CREATE_FIELDS).
EDH_CREATE_FIELD_SESSION_NAME = "session_name"
EDH_CREATE_FIELD_INSTANCE_TYPE = "instance_type"
EDH_CREATE_FIELD_DISK_SIZE = "disk_size"
EDH_CREATE_FIELD_SOFTWARE_STACK_ID = "software_stack_id"
EDH_CREATE_FIELD_PROJECT = "project"
EDH_CREATE_FIELD_SESSION_TYPE = "session_type"
# A launch into a capacity reservation names it and leaves the subnet to EDH, which picks one of its private
# subnets in the reservation's Availability Zone; a launch without one sends neither field.
EDH_CREATE_FIELD_CAPACITY_RESERVATION_ID = "capacity_reservation_id"
EDH_CREATE_FIELD_SUBNET_ID = "subnet_id"
EDH_SUBNET_AUTO = "auto"

# The EDH errors that are a definite refusal of a create: EDH answered, and created nothing.
EDH_REFUSALS = (EdhApiError, EdhCapacityError, EdhNotFoundError, EdhQuotaError)

# The DCV session type of a desktop: EDH accepts only a console session for Windows.
EDH_SESSION_TYPE_CONSOLE = "console"
EDH_SESSION_TYPE_VIRTUAL = "virtual"

# The create answers with prose; the session id is read back from the cluster listing, filtered to the
# row's owner and session name, once EDH has written its row. Six reads five seconds apart stay well
# inside the batch profile's budget.
FIND_DESKTOP_ATTEMPTS = 6
FIND_DESKTOP_INTERVAL_SECONDS = 5.0

# The HTTP verb and EDH route behind each client wrapper, recorded on the edhProxy audit trail.
# ensure_user is labelled with the probe it sends on every call; the create it may add for an
# unlisted user is a second request inside the client.
WORKER_AUDIT_ROUTES = {
    "ensure_user": ("GET", EDH_ROUTE_LDAP_USER),
    "find_desktop_by_name": ("GET", EDH_ROUTE_DESKTOPS_LIST_ALL),
    "create_desktop": ("POST", EDH_ROUTE_DESKTOPS_CREATE),
    "update_desktop_schedule": ("PUT", EDH_ROUTE_DESKTOPS_SCHEDULE),
}

try:
    edh_session_table_name = get_table_name(AddonResourceKeys.EDH_SESSION_STORAGE_TABLE)
    vdi_default_project = os.environ["EDH_VDI_DEFAULT_PROJECT"]
except Exception as e:
    logger.exception("Failed loading resource names and configuration")
    raise e

edh_session_table = dynamodb.Table(edh_session_table_name)


class ReservationFullError(Exception):
    """EDH refused the create of a reservation-bound row, the reservation is full, and its registration does not
    let the launch fall back to on-demand."""


def _sleep(seconds):
    time.sleep(seconds)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _audit_event(row):
    """The event the audit records are attributed to: the launching user, as a Lambda cross-call."""
    return {"lambdaCrossCall": {"userName": row['userId'], "mfaEnabled": False},
            "edhLaunch": {"bootstrapId": row['bootstrapId']}}


def _call_edh(audit_event, client, wrapper, operation, edh_uid, *args):
    """One EDH call through a client wrapper, audited as edhProxy after it returns or raises."""
    edh_method, edh_route = WORKER_AUDIT_ROUTES[wrapper]
    audit = {"operation": operation, "edhUid": edh_uid, "edhMethod": edh_method, "edhRoute": edh_route}
    try:
        result = getattr(client, wrapper)(*args)
    except Exception as err:
        log_actions(audit_event, "edhProxy", {**audit, "edhStatus": type(err).__name__})
        raise
    log_actions(audit_event, "edhProxy", {**audit, "edhStatus": "ok"})
    return result


def _update_row(row, **attributes):
    """Persist ``attributes`` (plus updatedAt) onto the row and mirror them into ``row``."""
    attributes['updatedAt'] = _utc_now()
    keys = list(attributes)
    edh_session_table.update_item(
        Key={'bootstrapId': row['bootstrapId']},
        UpdateExpression="SET " + ", ".join(f"#a{i} = :v{i}" for i in range(len(keys))),
        ExpressionAttributeNames={f"#a{i}": key for i, key in enumerate(keys)},
        ExpressionAttributeValues={f":v{i}": attributes[key] for i, key in enumerate(keys)},
    )
    row.update(attributes)
    return row


def _claim_attempt(row):
    """Count this invocation's attempt at the launch on the row, provided the row is still as read: CREATING,
    without a session id, and counted as often. False when it is not -- another delivery of the same launch
    counted first, or the row has left the launch since the read."""
    seen = row.get('launchAttempts')
    attempts = int(seen or 0) + 1
    updated_at = _utc_now()
    values = {':v0': attempts, ':v1': updated_at, ':creating': STATE_CREATING}
    condition = "#state = :creating AND attribute_not_exists(#session) AND "
    if seen is None:
        condition += "attribute_not_exists(#a0)"
    else:
        condition += "#a0 = :seen"
        values[':seen'] = seen
    try:
        edh_session_table.update_item(
            Key={'bootstrapId': row['bootstrapId']},
            UpdateExpression="SET #a0 = :v0, #a1 = :v1",
            ConditionExpression=condition,
            ExpressionAttributeNames={'#a0': 'launchAttempts', '#a1': 'updatedAt',
                                      '#state': 'state', '#session': 'sessionId'},
            ExpressionAttributeValues=values,
        )
    except ClientError as err:
        if err.response.get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
            raise
        return False
    row.update(launchAttempts=attempts, updatedAt=updated_at)
    return True


def _session_type(row):
    """The DCV session type of the row's desktop: console for Windows, virtual for every other family. EDH's
    own default is never sent, since it resolves to a console session on some Linux images."""
    return EDH_SESSION_TYPE_CONSOLE if row.get('osFamily') == OS_FAMILY_WINDOWS else EDH_SESSION_TYPE_VIRTUAL


def _create_form(row):
    """The EDH create form for a session row; every value a string, as EDH's parser reads them. A row launched
    into a capacity reservation also names the reservation and an automatic subnet."""
    form = {
        EDH_CREATE_FIELD_INSTANCE_TYPE: str(row['instanceType']),
        EDH_CREATE_FIELD_SOFTWARE_STACK_ID: str(row['imageId']),
        EDH_CREATE_FIELD_DISK_SIZE: str(int(row['rootSizeGb'])),
        EDH_CREATE_FIELD_SESSION_NAME: edh_session_name(row['bootstrapId']),
        EDH_CREATE_FIELD_PROJECT: vdi_default_project,
        EDH_CREATE_FIELD_SESSION_TYPE: _session_type(row),
    }
    if row.get('capacityReservationId'):
        form[EDH_CREATE_FIELD_CAPACITY_RESERVATION_ID] = str(row['capacityReservationId'])
        form[EDH_CREATE_FIELD_SUBNET_ID] = EDH_SUBNET_AUTO
    return form


def _falls_back(capacity_reservation_id):
    """After EDH refused a create into the reservation: True when the reservation, read again, is full and its
    registration, read again, lets the launch fall back to on-demand; False when it is full and does not (or is
    no longer registered); None when it is not full or could not be read, which leaves the refusal as it came."""
    try:
        facts = reservations.read_live([capacity_reservation_id])[capacity_reservation_id]
    except reservations.CapacityReservationReadError as err:
        logger.warning(f"The capacity reservation was not read after EDH refused the create ({err})")
        return None
    if reservation_refusal_last_error(facts) is None:
        return None
    registration = reservations.get_registration(capacity_reservation_id)
    return bool(registration and registration['fallbackToOnDemand'])


def _record_fallback(row, capacity_reservation_id):
    """Move the row off its reservation before the on-demand create: it records the fallback and the reservation
    it asked for, and no longer carries ``capacityReservationId``, so a retried invocation creates on-demand too."""
    updated_at = _utc_now()
    edh_session_table.update_item(
        Key={'bootstrapId': row['bootstrapId']},
        UpdateExpression="SET #a0 = :v0, #a1 = :v1, #a2 = :v2 REMOVE #r0",
        ExpressionAttributeNames={'#a0': 'capacityReservationFallback', '#a1': 'capacityReservationRequested',
                                  '#a2': 'updatedAt', '#r0': 'capacityReservationId'},
        ExpressionAttributeValues={':v0': True, ':v1': capacity_reservation_id, ':v2': updated_at},
    )
    row.pop('capacityReservationId', None)
    row.update(capacityReservationFallback=True, capacityReservationRequested=capacity_reservation_id,
               updatedAt=updated_at)


def _create_desktop(audit_event, client, edh_uid, row):
    """Ask EDH for the row's desktop. When EDH refuses the create of a reservation-bound row because the
    reservation is full, the create is made once more, on-demand, if the reservation's registration allows a
    fallback, and ``ReservationFullError`` is raised if it does not; any other failure is raised as it came. A
    create whose answer was lost may have happened, so it is never made again here."""
    try:
        _call_edh(audit_event, client, "create_desktop", "workstationCreate", edh_uid, edh_uid, _create_form(row))
        return
    except EDH_REFUSALS as err:
        capacity_reservation_id = row.get('capacityReservationId')
        if not capacity_reservation_id:
            raise
        fallback = _falls_back(capacity_reservation_id)
        if fallback is None:
            raise
        if not fallback:
            raise ReservationFullError(capacity_reservation_id) from err
        logger.info(f"The capacity reservation of bootstrap {row['bootstrapId']} is full, so it launches on-demand")
        _record_fallback(row, capacity_reservation_id)
    _call_edh(audit_event, client, "create_desktop", "workstationCreate", edh_uid, edh_uid, _create_form(row))


def _failure_code(wrapper, err):
    """The generic code a definite EDH refusal leaves on the row, or None when the error is transient. A
    not-found answer to a read of the cluster listing is transient: EDH may still list the desktop a
    create asked for."""
    if isinstance(err, ReservationFullError):
        return LAST_ERROR_RESERVATION_FULL
    if isinstance(err, EdhQuotaError):
        return LAST_ERROR_QUOTA_EXCEEDED
    if isinstance(err, EdhCapacityError):
        return LAST_ERROR_CAPACITY_UNAVAILABLE
    if isinstance(err, EdhNotFoundError):
        if wrapper == "find_desktop_by_name":
            return None
        return LAST_ERROR_USER_NOT_PROVISIONED if wrapper == "ensure_user" else LAST_ERROR_IMAGE_UNAVAILABLE
    if isinstance(err, EdhApiError):
        return LAST_ERROR_EDH_REFUSED
    return None


def _bind_session_secret(row, desktop):
    """The row attribute a binding of the session secret to the desktop's instance adds: nothing when
    the launch minted no credential, when EDH has not placed the desktop on an instance yet (the sweep
    binds it once it has), or when the binding failed (logged; the sweep retries it)."""
    if not row.get('secretName'):
        return {}
    instance_id = desktop.get(EDH_FIELD_INSTANCE_ID)
    if not instance_id:
        logger.warning(
            "The desktop is not placed on an instance yet; its session secret stays "
            "unreadable until the sweep binds it"
        )
        return {}
    try:
        return bootstrap.bind_session_secret(row, instance_id)
    except bootstrap.SessionBootstrapError:
        logger.exception("Session secret binding failed; the sweep retries it")
        return {}


def _apply_schedule(audit_event, client, row, session_id, desktop):
    """The row attribute setting the desktop's EDH schedule to one that never starts it adds: the stamp,
    once EDH lists that schedule already or has taken it; nothing when EDH refused it or did not answer
    (logged; the sweep sets it)."""
    if not schedule_never_auto_starts(desktop.get(EDH_SCHEDULE_FIELD)):
        try:
            _call_edh(audit_event, client, "update_desktop_schedule", "workstationCreate", row['edhUid'],
                      row['edhUid'], session_id, never_auto_start_schedule())
        except EdhError as err:
            logger.warning(f"EDH did not take the desktop's schedule ({type(err).__name__}); the sweep sets it",
                           exc_info=True)
            return {}
    return {'scheduleAppliedAt': _utc_now()}


def _find_desktop(audit_event, client, edh_uid, session_name, attempts):
    """The desktop EDH lists under ``session_name``, asked for up to ``attempts`` times a pause apart."""
    for attempt in range(attempts):
        desktop = _call_edh(audit_event, client, "find_desktop_by_name", "workstationCreate", edh_uid,
                            edh_uid, session_name)
        if desktop is not None:
            return desktop
        if attempt + 1 < attempts:
            _sleep(FIND_DESKTOP_INTERVAL_SECONDS)
    return None


def launch(row):
    """Run one launch for a CREATING row whose attempt this invocation has counted, and return the row as
    left."""
    audit_event = _audit_event(row)
    wrapper = "ensure_user"
    try:
        edh_uid = row['edhUid']
        session_name = edh_session_name(row['bootstrapId'])
        client = EdhClient(profile="batch")
        _call_edh(audit_event, client, "ensure_user", "workstationCreate", edh_uid,
                  edh_uid, display_name_for(row['userId']))
        wrapper = "find_desktop_by_name"
        # EDH commits the session row inside the create request and accepts a duplicate session name, so a
        # retry probes for as long as the read-back does: the create the previous attempt timed out on may
        # still be executing, and a second create would leave a twin desktop no row ever adopts.
        retried = row['launchAttempts'] > 1
        desktop = _find_desktop(audit_event, client, edh_uid, session_name,
                                FIND_DESKTOP_ATTEMPTS if retried else 1)
        if desktop is None:
            wrapper = "create_desktop"
            if retried:
                logger.warning(f"Retried launch of bootstrap {row['bootstrapId']} found no desktop; creating one")
            _create_desktop(audit_event, client, edh_uid, row)
            wrapper = "find_desktop_by_name"
            desktop = _find_desktop(audit_event, client, edh_uid, session_name, FIND_DESKTOP_ATTEMPTS)
    except Exception as err:
        code = _failure_code(wrapper, err)
        if code is None and row['launchAttempts'] < MAX_LAUNCH_ATTEMPTS:
            # Outcome unknown: the row stays CREATING and a retried invocation picks it up.
            raise
        if code is None:
            logger.exception(f"The launch failed at {wrapper} on its last attempt; orphaning the row")
            code = LAST_ERROR_LAUNCH_ERROR
        else:
            logger.info(f"EDH refused the launch at {wrapper} ({type(err).__name__}); orphaning the row")
        return _update_row(row, state=STATE_ORPHANED, lastError=code, connectionAvailable=False)
    if desktop is None:
        logger.info("EDH has not listed the desktop yet; the session sweep completes the row")
        return row
    changed = observed_row_attributes(row, desktop)
    session_id = changed.get('sessionId') or row.get('sessionId')
    if session_id and not row.get('scheduleAppliedAt'):
        changed.update(_apply_schedule(audit_event, client, row, session_id, desktop))
    changed.update(_bind_session_secret(row, desktop))
    return _update_row(row, **changed) if changed else row


def lambda_handler(event, context: LambdaContext):
    bootstrap_id = (event or {}).get('bootstrapId')
    if not bootstrap_id:
        raise ValueError("The launch worker payload names no bootstrapId")
    # The payload names the launch in the log, under one key so none of its own (name among them) can
    # collide with a LogRecord attribute; the row is the authority for every value.
    logger.info("Launch worker invoked",
                extra={"launch": {key: event.get(key) for key in WORKER_PAYLOAD_KEYS}})
    # The service wrote the row moments before handing the launch over; the read must see that write.
    row = edh_session_table.get_item(Key={'bootstrapId': bootstrap_id}, ConsistentRead=True).get('Item')
    if row is None:
        logger.warning("Launch worker invoked for a row that no longer exists")
        return {"bootstrapId": bootstrap_id, "outcome": "missing"}
    if row.get('state') != STATE_CREATING or row.get('sessionId') or not _claim_attempt(row):
        # A retried or duplicated invocation: the row already left the launch, or another delivery of it
        # counted this attempt first.
        return {"bootstrapId": bootstrap_id, "outcome": "settled", "state": row.get('state')}
    launched = launch(row)
    return {"bootstrapId": bootstrap_id, "outcome": "launched",
            "state": launched.get('state'), "sessionId": launched.get('sessionId')}
