# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Session-row vocabulary shared by the EDH workstation Lambdas.

The VAMS states a session row carries, the mapping from EDH's ``session_state`` onto them, the
codes an ORPHANED row keeps in ``lastError`` and the lease-expiry code a STOPPED row keeps, the
expiry a DELETED row leaves the table by, the form a start's stamp and a credential claim are written
in and the windows they hold for, when a row's desktop may still be running on a key of its bootstrap,
the launch worker's payload keys and attempt ceiling, and the EDH schedule VAMS gives every desktop. It
also reads the reason EDH recorded for a failed launch out of the desktop's recent events, says which
``lastError`` that reason implies, and when a row is due a read for one; it says when a start EDH accepted has
stalled, and it names the image a workstation runs: what a launch stores on its row, and the record's image fields
resolved from the recipe builds. A pure module: no AWS client, no environment read.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
EDH_SESSION_STATE_PLACING = "placing"
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
    EDH_SESSION_STATE_PLACING: STATE_CREATING,
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
# A launch into a capacity reservation the user named that failed because the reservation had no free slot.
LAST_ERROR_RESERVATION_FULL = "RESERVATION_FULL"
# A start of a stopped desktop that did not happen for a reason other than capacity: EDH refused it, it could
# not be sent, or EDH still reported the desktop stopped once the start's window had passed.
LAST_ERROR_START_FAILED = "START_FAILED"
# A start of a stopped desktop EDH accepted that has not finished: the row is still CREATING and EDH still lists the
# desktop pending or placing ``START_STALL_SECONDS`` after the start. Kept while the row stays CREATING; the desktop
# is neither stopped, deleted nor orphaned for it, and its key is kept, since the desktop may still finish starting.
LAST_ERROR_START_STALLED = "START_STALLED"
LAST_ERROR_CODES = frozenset({
    LAST_ERROR_USER_NOT_PROVISIONED, LAST_ERROR_QUOTA_EXCEEDED, LAST_ERROR_IMAGE_UNAVAILABLE,
    LAST_ERROR_CAPACITY_UNAVAILABLE, LAST_ERROR_EDH_REFUSED, LAST_ERROR_LAUNCH_ERROR,
    LAST_ERROR_CREATE_TIMED_OUT, LAST_ERROR_LEASE_EXPIRED, LAST_ERROR_RESERVATION_FULL, LAST_ERROR_START_FAILED,
    LAST_ERROR_START_STALLED,
})
# The codes a STOPPED row keeps for as long as it stays stopped: the lease expiry, and why the last start of
# the desktop did not happen. The next start EDH accepts, or EDH reporting the desktop past stopped, clears them.
STOPPED_ROW_LAST_ERRORS = frozenset({
    LAST_ERROR_LEASE_EXPIRED, LAST_ERROR_CAPACITY_UNAVAILABLE, LAST_ERROR_RESERVATION_FULL, LAST_ERROR_START_FAILED,
})
# What a start that did not happen, or stalled, recorded beside its code (``startFailure``): the AWS error code
# EDH's answer names, the Availability Zone of the desktop's instance, and when VAMS recorded it. Kept and cleared
# with the code.
START_FAILURE_FIELD = "startFailure"

# What a workstation is doing between two states, derived from the row whenever a record is read
# (``workstation_activity``), so every client shows the same thing. The row itself stores ``activity`` only as
# the mark of a start the owner asked for that is not yet resolved.
ACTIVITY_FIELD = "activity"
ACTIVITY_STARTING = "starting"
ACTIVITY_STOPPING = "stopping"
WORKSTATION_ACTIVITIES = (ACTIVITY_STARTING, ACTIVITY_STOPPING)
EDH_SESSION_STATE_STOPPING = "stopping"
# The stamp a stop through VAMS writes, and how long after it a desktop EDH still reports running reads as
# stopping: EDH moves a stopped desktop to `stopping` at once, so a read inside this window that still sees
# `running` predates EDH's own update.
STOP_REQUESTED_FIELD = "stopRequestedAt"
STOP_SETTLE_SECONDS = 300
# The AWS error codes, parsed from the reason EDH recorded for a failed launch, that turn a generic
# LAUNCH_ERROR on an ORPHANED row into CAPACITY_UNAVAILABLE or QUOTA_EXCEEDED; any other code leaves it.
CAPACITY_ERROR_CODES = frozenset({
    "InsufficientInstanceCapacity", "InsufficientCapacity", "InsufficientHostCapacity",
    "InsufficientReservedInstanceCapacity", "Unsupported",
})
QUOTA_ERROR_CODES = frozenset({
    "VcpuLimitExceeded", "InstanceLimitExceeded", "ReservationCapacityExceeded", "MaxSpotInstanceCountExceeded",
})
# On a row launched into a capacity reservation (one carrying ``capacityReservationId``), these AWS error codes
# mean the reservation had no free slot, and turn a generic LAUNCH_ERROR into RESERVATION_FULL instead.
RESERVATION_FULL_ERROR_CODES = CAPACITY_ERROR_CODES | frozenset({"ReservationCapacityExceeded"})
# The EC2 state of a capacity reservation a desktop can be launched or started into.
EC2_RESERVATION_STATE_ACTIVE = "active"

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
# The payload of a start the workstation service hands the worker: the action, the row, and the stamp of the
# start, which is that start's identity -- the worker sends EDH the start of one stamp at most once.
WORKER_ACTION_START = "start"
START_PAYLOAD_KEYS = ("action", "bootstrapId", "startRequestedAt")
# The row attribute naming the stamp of the start the worker has sent to EDH.
START_SENT_FIELD = "startSentFor"

# How many times the launch worker starts a launch for one row. The asynchronous invocation is retried
# twice, so the third start is the last: a transient failure there orphans the row instead of leaving
# it CREATING for a retry that will not come.
MAX_LAUNCH_ATTEMPTS = 3

# How long a start's stamp on its row is taken to mean the start is under way: the launch worker's whole run
# (its six-minute timeout, inside which it sends EDH's start and reads the desktop's state after a lost
# answer) and a minute for the asynchronous hand-over, and the worker acts on EDH only inside it. EDH starts
# a desktop its record still shows stopped a second time, so a start of such a desktop is refused meanwhile,
# and the key the start minted is not revoked while EDH still lists the desktop stopped.
START_IN_PROGRESS_SECONDS = 420
# How old the stamp of an unresolved start (a STOPPED row still marked ``activity`` starting) is when the
# session sweep records it START_FAILED, provided EDH still reports the desktop stopped.
START_RESOLVE_SECONDS = 600
# The stamp the launch worker writes (``start_stamp`` form) when it records a start of a stopped desktop EDH
# accepted, and only then -- a new launch never carries it; every write that moves the row out of CREATING clears
# it. An internal row attribute: the record does not return it.
START_ACCEPTED_FIELD = "startAcceptedAt"
# How old that stamp is when the session sweep records the start START_STALLED, provided the row is still CREATING
# and EDH still lists the desktop in one of ``START_STALL_EDH_STATES``. A resumed desktop normally reports running
# within minutes, so half an hour leaves a wide margin for a slow boot while staying shorter than the time a user
# watches a workstation read "starting" before giving up on it. It covers a start only: a first boot, which can
# install a GPU driver, legitimately takes well over an hour, and a new launch never carries the stamp.
START_STALL_SECONDS = 1800
START_STALL_EDH_STATES = frozenset({EDH_SESSION_STATE_PENDING, EDH_SESSION_STATE_PLACING})

# How long a credential claim on a row holds: a renew's key re-issue -- the mint, the row settle and the
# secret write, each bounded by its client's timeouts -- several times over. While a claim is fresh the
# sweep revokes nothing on the row, and a renew or a start finds the row taken; a claim a crashed request
# left ages out.
CREDENTIAL_CLAIM_SECONDS = 300

# The image a workstation runs, as its record names it: the EDH image name and, for an image an AMI recipe build
# produced, the recipe, its display name and the build version. The launch stores these on the row once
# (``launch_image_snapshot``) and they are never refreshed; a row stored without them, as every row launched
# before they were is, takes them when read from the recipe build that registered its image. The recipe's
# newest AVAILABLE build version, and whether the workstation runs it, are derived on every read and never stored.
IMAGE_NAME_FIELD = "imageName"
RECIPE_ID_FIELD = "recipeId"
RECIPE_DISPLAY_NAME_FIELD = "recipeDisplayName"
RECIPE_VERSION_FIELD = "recipeVersion"
LATEST_RECIPE_VERSION_FIELD = "latestRecipeVersion"
IMAGE_LATEST_FIELD = "imageLatest"
# The recipe and build attributes the image is read from: the recipe's display name, the build's version and
# the name of the EDH stack the build registered.
RECIPE_DISPLAY_NAME_ATTRIBUTE = "displayName"
BUILD_VERSION_ATTRIBUTE = "version"
BUILD_STACK_NAME_ATTRIBUTE = "edhStackName"


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


def start_accepted_cleared(row):
    """The write that clears the stamp of the start EDH accepted (``startAcceptedAt``) from a row moving out of
    CREATING; nothing when the row carries none."""
    return {START_ACCEPTED_FIELD: None} if row.get(START_ACCEPTED_FIELD) is not None else {}


def start_stall_cleared(row):
    """The write a stop makes to clear what an accepted start left on the row: the start's stamp and, on a row that
    records the start stalled, its ``lastError`` and ``startFailure``; nothing the row does not carry. Any other
    code is left as it is."""
    cleared = start_accepted_cleared(row)
    if row.get('lastError') == LAST_ERROR_START_STALLED:
        cleared['lastError'] = None
        if row.get(START_FAILURE_FIELD) is not None:
            cleared[START_FAILURE_FIELD] = None
    return cleared


def orphaned_last_error(stored_last_error) -> str:
    """The ``lastError`` a row moving to ORPHANED holds: the code it holds already, save a stalled start's, which
    says nothing about why the desktop failed, and ``LAUNCH_ERROR`` in its place or when it holds none."""
    if stored_last_error and stored_last_error != LAST_ERROR_START_STALLED:
        return stored_last_error
    return LAST_ERROR_LAUNCH_ERROR


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
    code the launch recorded is kept, a stalled start's is not; as is, while the state stays stopped, a code of
    ``STOPPED_ROW_LAST_ERRORS`` with the ``startFailure`` beside it, and, while the state stays CREATING, a stalled
    start's ``START_STALLED`` with its ``startFailure``; either is cleared again when the state moves on), the
    clearing of the lease-stop stamp from a desktop seen running or starting, since a desktop EDH reports in either
    state is not lease-stopped and the stamp is what ages a stopped one toward deletion, the clearing of the
    start-in-progress stamp from a desktop seen past stopped, which has taken the start that stamp holds a second
    one off for, the clearing of an accepted start's stamp (``startAcceptedAt``) from a row moving out of CREATING,
    and the clearing of the start mark (``activity``) from a desktop seen running or failed, whose start is over."""
    edh_state = desktop.get(EDH_SESSION_STATE_FIELD)
    attributes = {}
    if not row.get('sessionId') and desktop.get(EDH_SESSION_ID_FIELD) is not None:
        attributes['sessionId'] = str(desktop[EDH_SESSION_ID_FIELD])
    if desktop.get(EDH_FIELD_INSTANCE_ID):
        attributes['instanceId'] = str(desktop[EDH_FIELD_INSTANCE_ID])
    if edh_state is not None:
        attributes['edhState'] = str(edh_state)
        attributes['state'] = state_for_edh_session_state(edh_state, row.get('state'))
        stalled = row.get('lastError') == LAST_ERROR_START_STALLED
        if attributes['state'] == STATE_ORPHANED:
            attributes['lastError'] = orphaned_last_error(row.get('lastError'))
        elif row.get('lastError') is not None and not (
                (attributes['state'] == STATE_STOPPED and row.get('lastError') in STOPPED_ROW_LAST_ERRORS)
                or (attributes['state'] == STATE_CREATING and stalled)):
            attributes['lastError'] = None
        # The failure stays beside the code it was recorded with: a stalled start's while the row stays CREATING,
        # any other while it stays STOPPED.
        keeps_failure = attributes['state'] == (STATE_CREATING if stalled else STATE_STOPPED)
        if not keeps_failure and row.get(START_FAILURE_FIELD) is not None:
            attributes[START_FAILURE_FIELD] = None
        if attributes['state'] != STATE_CREATING and row.get(START_ACCEPTED_FIELD) is not None:
            attributes[START_ACCEPTED_FIELD] = None
        if attributes['state'] in (STATE_ACTIVE, STATE_CREATING) and row.get('leaseStoppedAt'):
            attributes['leaseStoppedAt'] = None
        if state_for_edh_session_state(edh_state, None) not in (None, STATE_STOPPED):
            attributes['startRequestedAt'] = None
        if attributes['state'] in (STATE_ACTIVE, STATE_ORPHANED) and row.get(ACTIVITY_FIELD) is not None:
            attributes[ACTIVITY_FIELD] = None
    connection_string = desktop.get(EDH_CONNECTION_STRING_FIELD)
    attributes['connectionAvailable'] = (
        str(edh_state).lower() == EDH_SESSION_STATE_RUNNING
        and (EDH_CONNECTION_STRING_FIELD not in desktop or bool(connection_string)))
    return {key: value for key, value in attributes.items() if row.get(key) != value}


def _stamp_age(value, now):
    """How long before ``now`` an ISO stamp was written, or None when it is absent or unreadable; a stamp
    without an offset is read as UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return now - instant


def workstation_activity(row, now=None) -> Optional[str]:
    """What the workstation is doing between two states, as every record reports it: ``stopping`` while EDH
    reports the desktop stopping, or while a stop through VAMS is younger than ``STOP_SETTLE_SECONDS`` and EDH
    still reports it running; ``starting`` for a STOPPED row marked with a start the owner asked for whose stamp
    is still inside ``START_IN_PROGRESS_SECONDS``, for a CREATING row with a session id (a stopped desktop EDH
    has taken a start of, or a new one EDH has placed) unless it records the start stalled (``START_STALLED``: the
    start is stuck, no longer under way), and for an ACTIVE row whose desktop is not connectable yet; None
    otherwise, and always for a DELETED or ORPHANED row. Pure: it reads only the row and ``now``."""
    now = now or datetime.now(timezone.utc)
    state = row.get('state')
    if state in (STATE_DELETED, STATE_ORPHANED):
        return None
    edh_state = str(row.get('edhState') or '').lower()
    if edh_state == EDH_SESSION_STATE_STOPPING:
        return ACTIVITY_STOPPING
    stopped_for = _stamp_age(row.get(STOP_REQUESTED_FIELD), now)
    if (edh_state == EDH_SESSION_STATE_RUNNING and stopped_for is not None
            and stopped_for < timedelta(seconds=STOP_SETTLE_SECONDS)):
        return ACTIVITY_STOPPING
    if state == STATE_STOPPED:
        if row.get(ACTIVITY_FIELD) == ACTIVITY_STARTING and start_in_progress(row, now):
            return ACTIVITY_STARTING
        return None
    if state == STATE_CREATING and row.get('sessionId'):
        return None if row.get('lastError') == LAST_ERROR_START_STALLED else ACTIVITY_STARTING
    if state == STATE_ACTIVE and not row.get('connectionAvailable'):
        return ACTIVITY_STARTING
    return None


def _image_text(value) -> Optional[str]:
    """A stored or recorded name as the record returns it: a string with something in it, else None."""
    return value if isinstance(value, str) and value.strip() else None


def _build_version(value) -> Optional[int]:
    """A build version as a whole number, from the int or the Decimal DynamoDB hands back; None for anything else,
    a bool or a fraction included."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)) and value == int(value):
        return int(value)
    return None


def launch_image_snapshot(stack_name, recipe_build) -> Dict[str, object]:
    """The image attributes a launch stores on the row it creates: the EDH image name, and, when an AVAILABLE AMI
    recipe build registered the image (``recipe_build``, its recipe and build), the recipe, its display name and the
    build version. A value the stack or the recipe does not state is left out rather than guessed."""
    snapshot: Dict[str, object] = {}
    name = _image_text(stack_name)
    if name is not None:
        snapshot[IMAGE_NAME_FIELD] = name
    if recipe_build is not None:
        recipe, build = recipe_build
        snapshot[RECIPE_ID_FIELD] = recipe['recipeId']
        display_name = _image_text(recipe.get(RECIPE_DISPLAY_NAME_ATTRIBUTE))
        if display_name is not None:
            snapshot[RECIPE_DISPLAY_NAME_FIELD] = display_name
        version = _build_version(build.get(BUILD_VERSION_ATTRIBUTE))
        if version is not None:
            snapshot[RECIPE_VERSION_FIELD] = version
    return snapshot


def workstation_image(stored, image_id, builds_by_stack, latest_versions) -> Dict[str, object]:
    """The image fields of a workstation's record. ``stored`` is what the row stored at launch; ``builds_by_stack``
    the recipe builds that registered a desktop image (recipe and build, keyed by EDH stack id) and
    ``latest_versions`` each recipe's newest AVAILABLE build version, both empty when the recipe table was not read.

    The recipe, its display name and the build version are the stored ones when the row names a recipe, else the
    build that registered ``image_id``'s; the image name is the stored one, else the name of the stack that build
    registered. ``imageLatest`` is True when the workstation runs its recipe's newest AVAILABLE build, False only
    when a newer one exists, and None when the recipe, the version or a newest build is unknown. Pure: the row is
    never written and nothing is guessed."""
    recipe_build = builds_by_stack.get(str(image_id)) if image_id is not None else None
    recipe_id = _image_text(stored.get(RECIPE_ID_FIELD))
    if recipe_id is not None:
        display_name = _image_text(stored.get(RECIPE_DISPLAY_NAME_FIELD))
        version = _build_version(stored.get(RECIPE_VERSION_FIELD))
    elif recipe_build is not None:
        recipe, build = recipe_build
        recipe_id = recipe['recipeId']
        display_name = _image_text(recipe.get(RECIPE_DISPLAY_NAME_ATTRIBUTE))
        version = _build_version(build.get(BUILD_VERSION_ATTRIBUTE))
    else:
        display_name = version = None
    name = _image_text(stored.get(IMAGE_NAME_FIELD))
    if name is None and recipe_build is not None:
        name = _image_text(recipe_build[1].get(BUILD_STACK_NAME_ATTRIBUTE))
    latest = latest_versions.get(recipe_id) if recipe_id is not None else None
    if version is None or latest is None:
        image_latest = None
    elif version == latest:
        image_latest = True
    elif latest > version:
        image_latest = False
    else:
        # A newer build than the recipe's AVAILABLE one, which a retried older build retired: not behind.
        image_latest = None
    return {
        IMAGE_NAME_FIELD: name,
        RECIPE_ID_FIELD: recipe_id,
        RECIPE_DISPLAY_NAME_FIELD: display_name,
        RECIPE_VERSION_FIELD: version,
        LATEST_RECIPE_VERSION_FIELD: latest,
        IMAGE_LATEST_FIELD: image_latest,
    }


def start_resolve_due(row, now=None) -> bool:
    """True when the row is a STOPPED desktop still marked with a start the owner asked for whose stamp is at
    least ``START_RESOLVE_SECONDS`` old: the launch worker has long given up on it, and EDH still reporting the
    desktop stopped means the start did not happen. A row without the mark, or whose stamp is gone or
    unreadable (a start some write has already settled), is never due."""
    if row.get('state') != STATE_STOPPED or row.get(ACTIVITY_FIELD) != ACTIVITY_STARTING:
        return False
    age = _stamp_age(row.get('startRequestedAt'), now or datetime.now(timezone.utc))
    return age is not None and age >= timedelta(seconds=START_RESOLVE_SECONDS)


def start_stall_due(row, now=None) -> bool:
    """True when the row is CREATING after a start of a stopped desktop EDH accepted whose stamp
    (``startAcceptedAt``) is at least ``START_STALL_SECONDS`` old at ``now`` (the current instant when not given),
    and the row does not record the stall already: the start has not finished in many times the time a resume
    takes. A row without the stamp (a new launch, or a row some write has moved out of CREATING since), with an
    unreadable one, or in any other state is never due. Whether EDH still lists the desktop pending or placing is
    the caller's to check."""
    if row.get('state') != STATE_CREATING or row.get('lastError') == LAST_ERROR_START_STALLED:
        return False
    age = _stamp_age(row.get(START_ACCEPTED_FIELD), now or datetime.now(timezone.utc))
    return age is not None and age >= timedelta(seconds=START_STALL_SECONDS)


def start_failure(error_text, availability_zone, now) -> Dict[str, str]:
    """The ``startFailure`` a start that did not happen records at ``now``: the AWS error code EDH's answer
    names in botocore's words (``EDH_FAILURE_AWS_ERROR_PATTERN``, absent when it names none), the desktop's
    Availability Zone (absent when it was not read) and ``observedAt``. EDH's text itself is never kept."""
    failure = {"observedAt": now.isoformat()}
    match = EDH_FAILURE_AWS_ERROR_PATTERN.search(error_text) if isinstance(error_text, str) else None
    if match:
        failure["errorCode"] = match.group(1)
    if isinstance(availability_zone, str) and availability_zone:
        failure["availabilityZone"] = availability_zone
    return failure


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


def edh_failure_last_error(failure, stored_last_error, reservation_bound=False):
    """The ``lastError`` an ORPHANED row holds once ``failure`` is known: a specific code the row already holds
    is kept; a generic ``LAUNCH_ERROR``, or none, becomes ``RESERVATION_FULL`` for an AWS error code of
    ``RESERVATION_FULL_ERROR_CODES`` on a ``reservation_bound`` row (one launched into a capacity reservation),
    else ``CAPACITY_UNAVAILABLE`` or ``QUOTA_EXCEEDED`` for an AWS error code of those sets, and
    ``LAUNCH_ERROR`` otherwise."""
    if stored_last_error and stored_last_error != LAST_ERROR_LAUNCH_ERROR:
        return stored_last_error
    code = (failure or {}).get("errorCode")
    if reservation_bound and code in RESERVATION_FULL_ERROR_CODES:
        return LAST_ERROR_RESERVATION_FULL
    if code in CAPACITY_ERROR_CODES:
        return LAST_ERROR_CAPACITY_UNAVAILABLE
    if code in QUOTA_ERROR_CODES:
        return LAST_ERROR_QUOTA_EXCEEDED
    return LAST_ERROR_LAUNCH_ERROR


def reservation_has_free_slot(facts) -> bool:
    """Whether a capacity reservation, as its live view reads (``state``, ``availableInstanceCount``), is active
    with at least one instance slot free."""
    available = (facts or {}).get("availableInstanceCount")
    return ((facts or {}).get("state") == EC2_RESERVATION_STATE_ACTIVE and isinstance(available, int)
            and not isinstance(available, bool) and available >= 1)


def reservation_is_full(facts) -> bool:
    """Whether a capacity reservation, as its live view reads, is active with no instance slot free."""
    return (facts or {}).get("state") == EC2_RESERVATION_STATE_ACTIVE and not reservation_has_free_slot(facts)


def reservation_refusal_last_error(facts) -> Optional[str]:
    """The code a definite EDH refusal of a reservation-bound desktop's create or start reports once the
    reservation has been read again: ``RESERVATION_FULL`` when it is full, None when it is not (or was not read),
    which leaves the refusal's own classification standing."""
    return LAST_ERROR_RESERVATION_FULL if facts is not None and reservation_is_full(facts) else None


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
    the row's, read as a reservation-bound row's when the row carries ``capacityReservationId``."""
    observed = now.isoformat()
    attributes = {EDH_FAILURE_READ_AT_FIELD: observed}
    if failure:
        attributes[EDH_FAILURE_FIELD] = {**failure, "observedAt": observed}
        last_error = edh_failure_last_error(failure, row.get('lastError'),
                                            reservation_bound=bool(row.get('capacityReservationId')))
        if last_error != row.get('lastError'):
            attributes['lastError'] = last_error
    return attributes
