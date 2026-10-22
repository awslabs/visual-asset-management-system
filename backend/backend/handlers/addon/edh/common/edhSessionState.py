# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Session-row vocabulary shared by the EDH workstation Lambdas.

The VAMS states a session row carries, the mapping from EDH's ``session_state`` onto them, the
codes an ORPHANED row keeps in ``lastError`` and the lease-expiry code a STOPPED row keeps, the
expiry a DELETED row leaves the table by, the form a start's stamp and a credential claim are written
in and the windows they hold for, when a row's desktop may still be running on a key of its bootstrap,
the launch worker's payload keys and attempt ceiling, and the EDH schedule VAMS gives every desktop. It
also reads the reason EDH recorded for a failed launch out of the desktop's recent events, says which
``lastError`` that reason implies, and when a row is due a read for one. A pure module: no AWS client, no
environment read.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

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
# The AWS error codes, parsed from the reason EDH recorded for a failed launch, that turn a generic
# LAUNCH_ERROR on an ORPHANED row into CAPACITY_UNAVAILABLE or QUOTA_EXCEEDED; any other code leaves it.
CAPACITY_ERROR_CODES = frozenset({
    "InsufficientInstanceCapacity", "InsufficientCapacity", "InsufficientHostCapacity",
    "InsufficientReservedInstanceCapacity", "Unsupported",
})
QUOTA_ERROR_CODES = frozenset({
    "VcpuLimitExceeded", "InstanceLimitExceeded", "ReservationCapacityExceeded", "MaxSpotInstanceCountExceeded",
})

# The reason EDH recorded for a failed launch, as VAMS stores it on the row (``edhFailure``), and the stamp of
# the last read for one (``edhFailureReadAt``), which holds the next read off for the interval. EDH's per-user
# listing carries a desktop's last few events under ``recent_events``; a failure is the newest
# ``placement_failed`` (the asynchronous placement gave up) or ``session-failed`` (the desktop's bootstrap
# failed) among them. Its free text (``sub_status``) is logged, cut to the limit, and never stored.
EDH_FAILURE_FIELD = "edhFailure"
EDH_FAILURE_READ_AT_FIELD = "edhFailureReadAt"
EDH_FAILURE_READ_INTERVAL_SECONDS = 300
EDH_RECENT_EVENTS_FIELD = "recent_events"
EDH_EVENT_TYPE_FIELD = "event_type"
EDH_EVENT_CHECKPOINT_FIELD = "checkpoint"
EDH_EVENT_SUB_STATUS_FIELD = "sub_status"
EDH_EVENT_RECEIVED_AT_FIELD = "received_at"
EDH_EVENT_PLACEMENT_FAILED = "placement_failed"
EDH_EVENT_SESSION_FAILED = "session-failed"
EDH_FAILURE_EVENT_TYPES = frozenset({EDH_EVENT_PLACEMENT_FAILED, EDH_EVENT_SESSION_FAILED})
EDH_FAILURE_TEXT_LOG_LIMIT = 300
# The AWS error code and operation in EDH's text, as botocore words a ClientError, and the checkpoint name a
# bootstrap failure keeps; anything else is left out of the stored reason.
EDH_FAILURE_AWS_ERROR_PATTERN = re.compile(
    r"An error occurred \(([A-Za-z][A-Za-z0-9.]{0,63})\) when calling the ([A-Za-z][A-Za-z0-9]{0,63}) operation")
EDH_FAILURE_CHECKPOINT_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,64}")

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


def _event_instant(event):
    """When EDH received an event, as an aware datetime (one without an offset read as UTC); None when the
    event carries no readable time."""
    try:
        instant = datetime.fromisoformat(str(event.get(EDH_EVENT_RECEIVED_AT_FIELD)).replace("Z", "+00:00"))
    except ValueError:
        return None
    return instant if instant.tzinfo is not None else instant.replace(tzinfo=timezone.utc)


def _newest_failure_event(events):
    """The newest ``placement_failed`` or ``session-failed`` entry of ``events`` (EDH lists them oldest first):
    by the time EDH received it, then by position, an entry without a readable time ranking below one with a
    time. None when ``events`` is not a list or holds no failure."""
    if not isinstance(events, list):
        return None
    failures = [(index, event) for index, event in enumerate(events)
                if isinstance(event, dict) and event.get(EDH_EVENT_TYPE_FIELD) in EDH_FAILURE_EVENT_TYPES]
    if not failures:
        return None

    def rank(entry):
        index, event = entry
        instant = _event_instant(event)
        return (instant is not None, instant or datetime.min.replace(tzinfo=timezone.utc), index)

    return max(failures, key=rank)[1]


def edh_failure_from_events(events) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """(the stored shape of the newest failure in a desktop's ``recent_events``, EDH's own text of it cut to
    ``EDH_FAILURE_TEXT_LOG_LIMIT`` for the caller to log), or (None, None) when there is no failure. The map
    carries ``event``; ``errorCode`` and ``operation`` when EDH's text names an AWS error in botocore's words;
    and, for ``session-failed``, ``checkpoint`` when EDH's checkpoint name is a plain token. EDH's text is
    never in the map, and is None when EDH's is not a string."""
    event = _newest_failure_event(events)
    if event is None:
        return None, None
    failure = {"event": event[EDH_EVENT_TYPE_FIELD]}
    sub_status = event.get(EDH_EVENT_SUB_STATUS_FIELD)
    if not isinstance(sub_status, str):
        sub_status = None
    match = EDH_FAILURE_AWS_ERROR_PATTERN.search(sub_status) if sub_status else None
    if match:
        failure["errorCode"], failure["operation"] = match.group(1), match.group(2)
    checkpoint = event.get(EDH_EVENT_CHECKPOINT_FIELD)
    if (failure["event"] == EDH_EVENT_SESSION_FAILED and isinstance(checkpoint, str)
            and EDH_FAILURE_CHECKPOINT_PATTERN.fullmatch(checkpoint)):
        failure["checkpoint"] = checkpoint
    return failure, (sub_status[:EDH_FAILURE_TEXT_LOG_LIMIT] if sub_status is not None else None)


def edh_failure_last_error(failure, stored_last_error):
    """The ``lastError`` an ORPHANED row holds once ``failure`` is known: a specific code the row already holds
    is kept; a generic ``LAUNCH_ERROR``, or none, becomes ``CAPACITY_UNAVAILABLE`` or ``QUOTA_EXCEEDED`` for an
    AWS error code of those sets, and ``LAUNCH_ERROR`` otherwise."""
    if stored_last_error and stored_last_error != LAST_ERROR_LAUNCH_ERROR:
        return stored_last_error
    code = (failure or {}).get("errorCode")
    if code in CAPACITY_ERROR_CODES:
        return LAST_ERROR_CAPACITY_UNAVAILABLE
    if code in QUOTA_ERROR_CODES:
        return LAST_ERROR_QUOTA_EXCEEDED
    return LAST_ERROR_LAUNCH_ERROR


def edh_failure_read_due(row, now=None) -> bool:
    """True when the row is an ORPHANED desktop with a session id that holds no captured reason, and its last
    read for one is not inside the interval at ``now`` (the current instant when not given). A stamp written up
    to one interval after ``now`` holds the read off too; an absent, blank, unreadable or further-ahead stamp
    holds nothing, and one without an offset is read as UTC."""
    if row.get('state') != STATE_ORPHANED or not row.get('sessionId') or row.get(EDH_FAILURE_FIELD):
        return False
    stamp = row.get(EDH_FAILURE_READ_AT_FIELD)
    if not isinstance(stamp, str) or not stamp:
        return True
    try:
        read_at = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    if read_at.tzinfo is None:
        read_at = read_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    window = timedelta(seconds=EDH_FAILURE_READ_INTERVAL_SECONDS)
    return not -window <= now - read_at < window


def edh_failure_attributes(row, failure, now):
    """The row attributes one read for a failed launch's reason writes at ``now``: always its stamp; when the
    read found a reason, the reason with ``observedAt``, and the ``lastError`` it implies when that differs from
    the row's."""
    observed = now.isoformat()
    attributes = {EDH_FAILURE_READ_AT_FIELD: observed}
    if failure:
        attributes[EDH_FAILURE_FIELD] = {**failure, "observedAt": observed}
        last_error = edh_failure_last_error(failure, row.get('lastError'))
        if last_error != row.get('lastError'):
            attributes['lastError'] = last_error
    return attributes
