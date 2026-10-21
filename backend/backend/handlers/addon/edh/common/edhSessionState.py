# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Session-row vocabulary shared by the EDH workstation Lambdas.

The VAMS states a session row carries, the mapping from EDH's ``session_state`` onto them, the
codes an ORPHANED row keeps in ``lastError`` and the lease-expiry code a STOPPED row keeps, the
expiry a DELETED row leaves the table by, the form a start's stamp and a credential claim are written
in and the windows they hold for, when a row's desktop may still be running on a key of its bootstrap,
the launch worker's payload keys and attempt ceiling, and the EDH schedule VAMS gives every desktop. A
pure module: no AWS client, no environment read.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Dict

# Session row states.
STATE_CREATING = "CREATING"
STATE_ACTIVE = "ACTIVE"
STATE_STOPPED = "STOPPED"
STATE_DELETED = "DELETED"
STATE_ORPHANED = "ORPHANED"

# EDH desktop rows: the id, state, connection URL and EC2 instance fields.
EDH_SESSION_ID_FIELD = "session_uuid"
EDH_SESSION_STATE_FIELD = "session_state"
EDH_CONNECTION_STRING_FIELD = "connection_string"
EDH_FIELD_INSTANCE_ID = "instance_id"
EDH_SESSION_STATE_PENDING = "pending"
EDH_SESSION_STATE_RUNNING = "running"
EDH_SESSION_STATE_STOPPED = "stopped"
EDH_SESSION_STATE_ERROR = "error"
# The desktop's schedule: the JSON string EDH lists and its schedule update takes, one
# {"start": minute, "stop": minute} window (UTC minutes of the day) per day.
EDH_SCHEDULE_FIELD = "schedule"
EDH_SCHEDULE_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# EDH session_state -> VAMS state. A value outside this map leaves the stored state alone.
EDH_SESSION_STATE_TO_STATE = {
    EDH_SESSION_STATE_PENDING: STATE_CREATING,
    "placing": STATE_CREATING,
    EDH_SESSION_STATE_RUNNING: STATE_ACTIVE,
    "stopping": STATE_STOPPED,
    EDH_SESSION_STATE_STOPPED: STATE_STOPPED,
    "interrupting": STATE_STOPPED,
    "interrupted": STATE_STOPPED,
    EDH_SESSION_STATE_ERROR: STATE_ORPHANED,
}

# The table's TTL attribute (epoch seconds), written onto a DELETED row once no credential is left on
# it, and how long after that the row stays readable for support before DynamoDB removes it.
EXPIRES_AT_FIELD = "expiresAt"
DELETED_ROW_RETENTION_DAYS = 7

# Codes kept on an ORPHANED row, plus the lease-expiry code a STOPPED row keeps while it stays stopped
# (a renew or start clears it). EDH's own wording is logged and never stored or returned.
LAST_ERROR_USER_NOT_PROVISIONED = "USER_NOT_PROVISIONED"
LAST_ERROR_QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
LAST_ERROR_IMAGE_UNAVAILABLE = "IMAGE_UNAVAILABLE"
LAST_ERROR_CAPACITY_UNAVAILABLE = "CAPACITY_UNAVAILABLE"
LAST_ERROR_EDH_REFUSED = "EDH_REFUSED"
LAST_ERROR_LAUNCH_ERROR = "LAUNCH_ERROR"
LAST_ERROR_CREATE_TIMED_OUT = "CREATE_TIMED_OUT"
LAST_ERROR_LEASE_EXPIRED = "LEASE_EXPIRED"
LAST_ERROR_CODES = frozenset({
    LAST_ERROR_USER_NOT_PROVISIONED, LAST_ERROR_QUOTA_EXCEEDED, LAST_ERROR_IMAGE_UNAVAILABLE,
    LAST_ERROR_CAPACITY_UNAVAILABLE, LAST_ERROR_EDH_REFUSED, LAST_ERROR_LAUNCH_ERROR,
    LAST_ERROR_CREATE_TIMED_OUT, LAST_ERROR_LEASE_EXPIRED,
})

# The launch worker's invocation payload: the row's identity, echoed so the worker's log names it.
WORKER_PAYLOAD_KEYS = ("bootstrapId", "userId", "edhUid", "imageId", "instanceType", "osFamily", "rootSizeGb", "name")

# How many times the launch worker starts a launch for one row. The asynchronous invocation is retried
# twice, so the third start is the last: a transient failure there orphans the row instead of leaving
# it CREATING for a retry that will not come.
MAX_LAUNCH_ATTEMPTS = 3

# How long a start's stamp on its row is taken to mean the start is under way: EDH's wait for a capacity
# reservation to settle (up to 20 s by default) and its own work, several times over. EDH starts a desktop
# its record still shows stopped a second time, so a start of such a desktop is refused meanwhile, and
# the key the start minted is not revoked while EDH still lists the desktop stopped.
START_IN_PROGRESS_SECONDS = 180

# How long a credential claim on a row holds: a renew's key re-issue -- the mint, the row settle and the
# secret write, each bounded by its client's timeouts -- several times over. While a claim is fresh the
# sweep revokes nothing on the row, and a renew or a start finds the row taken; a claim a crashed request
# left ages out.
CREDENTIAL_CLAIM_SECONDS = 300


def deleted_row_expiry(now=None):
    """The ``expiresAt`` value of a DELETED row without a credential: ``now`` plus the retention, as the
    integer epoch seconds DynamoDB's TTL reads."""
    now = now or datetime.now(timezone.utc)
    return int((now + timedelta(days=DELETED_ROW_RETENTION_DAYS)).timestamp())


def state_for_edh_session_state(session_state, stored_state):
    """The VAMS state EDH's ``session_state`` implies, or the stored state for an unknown value."""
    if session_state is None:
        return stored_state
    return EDH_SESSION_STATE_TO_STATE.get(str(session_state).lower(), stored_state)


def start_stamp(instant):
    """A start-in-progress stamp: UTC to the microsecond, one fixed width, so two stamps order as strings."""
    return instant.isoformat(timespec='microseconds')


def start_stamp_cleared(row):
    """The write that clears the row's start-in-progress stamp; nothing when the row carries none."""
    return {'startRequestedAt': None} if row.get('startRequestedAt') else {}


def start_in_progress(row, now=None):
    """True while the row's start-in-progress stamp is younger than the window at ``now`` (the current
    instant when not given); an absent or unreadable stamp holds nothing off, and one without an offset
    is read as UTC."""
    now = now or datetime.now(timezone.utc)
    try:
        requested_at = datetime.fromisoformat(str(row.get('startRequestedAt')))
    except ValueError:
        return False
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)
    return now - requested_at < timedelta(seconds=START_IN_PROGRESS_SECONDS)


def credential_claim_stamp(instant):
    """A credential claim: UTC to the microsecond, one fixed width, so two claims order as strings."""
    return instant.isoformat(timespec='microseconds')


def credential_claim_fresh(row, now=None):
    """True while the row's ``credentialClaimedAt`` is younger than the claim window at ``now`` (the current
    instant when not given), including a claim written up to one window after ``now``; a claim dated further
    ahead, and an absent, blank or unreadable one, holds nothing, and one without an offset is read as UTC."""
    claimed = row.get('credentialClaimedAt')
    if not isinstance(claimed, str) or not claimed:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        claimed_at = datetime.fromisoformat(claimed)
    except ValueError:
        return False
    if claimed_at.tzinfo is None:
        claimed_at = claimed_at.replace(tzinfo=timezone.utc)
    window = timedelta(seconds=CREDENTIAL_CLAIM_SECONDS)
    return -window <= now - claimed_at < window


def session_row_live(row, now=None, *, own_claim=None):
    """True while the row's desktop may be running on a key of its bootstrap: the row is stored ACTIVE or
    CREATING, or holds a start stamp or a credential claim still inside its window at ``now`` (the current
    instant when not given). ``own_claim`` is a credential claim the caller holds itself, which does not
    count."""
    now = now or datetime.now(timezone.utc)
    if row.get('state') in (STATE_ACTIVE, STATE_CREATING) or start_in_progress(row, now):
        return True
    return row.get('credentialClaimedAt') != own_claim and credential_claim_fresh(row, now)


def never_auto_start_schedule() -> Dict[str, Dict[str, int]]:
    """A fresh copy of the EDH schedule VAMS gives every desktop: stopped all day, every day."""
    return {day: {"start": 0, "stop": 0} for day in EDH_SCHEDULE_DAYS}


def schedule_never_auto_starts(value) -> bool:
    """True only when ``value`` -- the schedule string an EDH listing carries -- parses as JSON to a dict
    whose seven days each have integer start and stop of 0. Extra keys are ignored; anything else
    (missing, not JSON, a Python repr, string or boolean minutes, a missing day) is False, i.e. the
    schedule still needs applying."""
    if not isinstance(value, str):
        return False
    try:
        schedule = json.loads(value)
    except ValueError:
        return False
    if not isinstance(schedule, dict):
        return False
    for day in EDH_SCHEDULE_DAYS:
        window = schedule.get(day)
        if not isinstance(window, dict):
            return False
        for minute in (window.get("start"), window.get("stop")):
            if type(minute) is not int or minute != 0:
                return False
    return True


def observed_row_attributes(row, desktop):
    """The row attributes EDH's desktop record implies, limited to those that differ from the row:
    the session id a row lacks, the raw and mapped state, connectability (running, unless the record
    carries an empty connection string), the EC2 instance the desktop is placed on (left as stored
    while EDH reports none), the launch-error code an ``error`` state carries when the row holds none (a
    code the launch recorded is kept, as is a lease-expiry code while the state stays stopped; either is
    cleared again when the state moves on), the clearing of the lease-stop stamp from a desktop seen
    running or starting, since a desktop EDH reports in either state is not lease-stopped and the stamp
    is what ages a stopped one toward deletion, and the clearing of the start-in-progress stamp from a
    desktop seen past stopped, which has taken the start that stamp holds a second one off for."""
    edh_state = desktop.get(EDH_SESSION_STATE_FIELD)
    attributes = {}
    if not row.get('sessionId') and desktop.get(EDH_SESSION_ID_FIELD) is not None:
        attributes['sessionId'] = str(desktop[EDH_SESSION_ID_FIELD])
    if desktop.get(EDH_FIELD_INSTANCE_ID):
        attributes['instanceId'] = str(desktop[EDH_FIELD_INSTANCE_ID])
    if edh_state is not None:
        attributes['edhState'] = str(edh_state)
        attributes['state'] = state_for_edh_session_state(edh_state, row.get('state'))
        if attributes['state'] == STATE_ORPHANED:
            attributes['lastError'] = row.get('lastError') or LAST_ERROR_LAUNCH_ERROR
        elif row.get('lastError') is not None and not (
                attributes['state'] == STATE_STOPPED and row.get('lastError') == LAST_ERROR_LEASE_EXPIRED):
            attributes['lastError'] = None
        if attributes['state'] in (STATE_ACTIVE, STATE_CREATING) and row.get('leaseStoppedAt'):
            attributes['leaseStoppedAt'] = None
        if state_for_edh_session_state(edh_state, None) not in (None, STATE_STOPPED):
            attributes['startRequestedAt'] = None
    connection_string = desktop.get(EDH_CONNECTION_STRING_FIELD)
    attributes['connectionAvailable'] = (
        str(edh_state).lower() == EDH_SESSION_STATE_RUNNING
        and (EDH_CONNECTION_STRING_FIELD not in desktop or bool(connection_string)))
    return {key: value for key, value in attributes.items() if row.get(key) != value}
