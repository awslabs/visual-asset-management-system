# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Scheduled sweep that reconciles EDH workstation session rows against EDH and revokes the session
credentials of desktops that are no longer running.

EDH stops a desktop on its idle timer, and an EDH or VAMS administrator can stop or remove one without
the owner's stop/delete call reaching this credential. This sweep is therefore the soft bound on a
session key's life; the key's own ``keyExpiresAt`` is the hard bound. It lists every active desktop of
the cluster under the EDH root key and derives each row's state from EDH's ``session_state`` through the
shared session-state mapping, matching a row that never received its session id by the session name EDH
stores for it. A desktop EDH lists running is recorded connectable. A row whose secret is not bound to
the EC2 instance EDH places its desktop on -- never bound, or bound to an instance the desktop no longer
runs on -- has the binding applied. A desktop EDH no longer lists was deactivated outside VAMS and is
recorded DELETED, save that a launch still CREATING whose desktop EDH no longer has is recorded ORPHANED
with its launch-error code: EDH deactivates a desktop whose placement failed, and a desktop that never ran
is no Save and Resume. A launch still CREATING past the worker's grace window -- or, with the worker's
attempts spent, past its last attempt's timeout -- is recorded ORPHANED. A launch its owner deleted
before EDH listed its desktop re-adopts that desktop when EDH lists it within the reclaim window: the
row's state follows EDH again, its session id is completed, and it carries no credential until its owner
stops and starts it. A credential is revoked only on a definite signal -- a row that ends STOPPED,
ORPHANED or DELETED, or a key past its expiry; a live key on a desktop EDH lists stopped waits out a
start that still claims the row -- and its secret container goes with it only when the desktop is gone
for good, whether or not a key is still recorded beside it. A row recorded DELETED with no credential
left carries the expiry the table's TTL removes it by. A row EDH lists by session id that has not had
its desktop's EDH schedule set is given the schedule that never starts the desktop automatically, once,
on a pass that leaves the row otherwise unchanged; the sweep makes at most a fixed number of such calls,
none after the first refusal and none once too little of the invocation is left. A row the pass leaves
ORPHANED with a session id and no captured reason is given the reason EDH recorded for its failed launch,
read from EDH's per-user listing as the row's owner -- the listing is owner-scoped -- at most once per row
per read interval and within a per-sweep allowance of such reads; a read that fails writes only its stamp.
Session keys that no
row names at all (a mint whose reply was lost, a row that is gone, a key a re-issue replaced) have their
records deleted once expired or older than one sweep's grace, save that every unexpired key of a desktop
that may still be running -- its row stored ACTIVE or CREATING or under a fresh start or credential
claim, or its desktop listed by EDH as running or starting -- is kept until its own expiry, since the
desktop signs in with whatever its secret holds; an orphan whose expiry has passed is audited as expired
and any other as revoked. The key table has no TTL of its own.

The sweep shares its rows with the requests VAMS serves. A row a request wrote moments ago is left to
that request; every other row is re-read before anything is decided, claimed with a write conditioned
on the row being unchanged since, and only then has its credential revoked and its credential
attributes cleared -- under the same condition -- so a start that races the sweep keeps the key it just
minted. The claim on a row that ends STOPPED also takes the row's start slot, as a start's own claim
does, so no start mints a key for the desktop while the sweep revokes the stored one; the clear gives
the slot up, and so does a revoke that fails or a clear another write overtakes. Every revoking claim
also takes the row's credential claim, the one a renew takes to re-issue the key, so neither runs while
the other holds the row: a row under a fresh credential claim has every revoke and secret removal left
to a later sweep. Credential work runs only while an apiKeyService function is named, and then for a
row that names a key or while session bootstrap is on; a row whose credential this deployment cannot
revoke is counted skipped and its state reconciliation runs regardless. Two sweeps that overlap
converge on the same rows: every revoke tolerates a key or secret that is already gone.
"""

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, NamedTuple, Optional, Set, Tuple

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from botocore.exceptions import ClientError
from aws_lambda_powertools.utilities.typing import LambdaContext

from common.addonResourceNames import AddonResourceKeys
from common.resourceNames import get_table_name
from customLogging.logger import safeLogger
from .common import edhSessionBootstrap as bootstrap
from .common.edhClient import (
    EDH_SERVICE_ACTOR_UID,
    EDH_SESSION_CREATED_ON_FIELD,
    EDH_SESSION_ID_FIELD,
    EDH_SESSION_NAME_FIELD,
    EDH_SESSION_OWNER_FIELD,
    EdhClient, EdhContractError, EdhError, EdhNotFoundError, edh_session_name,
)
from .common.edhIdentity import SYSTEM_USER_ID
from .common.edhLeasePolicy import lease_action, parse_iso, read_lease_policy
from .common.edhSessionState import (
    ACTIVITY_FIELD, CREDENTIAL_CLAIM_SECONDS, EDH_FAILURE_FIELD, EDH_FIELD_INSTANCE_ID, EDH_SCHEDULE_FIELD,
    EDH_SESSION_STATE_FIELD, EDH_SESSION_STATE_STOPPED, EXPIRES_AT_FIELD, LAST_ERROR_CREATE_TIMED_OUT,
    LAST_ERROR_LAUNCH_ERROR, LAST_ERROR_LEASE_EXPIRED, LAST_ERROR_START_FAILED, MAX_LAUNCH_ATTEMPTS,
    START_FAILURE_FIELD, START_IN_PROGRESS_SECONDS,
    STATE_ACTIVE, STATE_CREATING, STATE_DELETED, STATE_ORPHANED, STATE_STOPPED, credential_claim_fresh,
    credential_claim_stamp, deleted_row_expiry, edh_failure_attributes, edh_failure_read_due,
    never_auto_start_schedule, observed_row_attributes, schedule_never_auto_starts, session_row_live,
    start_failure, start_in_progress, start_resolve_due, start_stamp, state_for_edh_session_state,
)

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="EdhSessionReconciler")

try:
    session_table_name = get_table_name(AddonResourceKeys.EDH_SESSION_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

session_table = dynamodb.Table(session_table_name)

# Rows whose desktop may still be running. DELETED rows are terminal and are examined only while they
# still carry a credential, or a start or credential claim inside its window.
SWEPT_STATES = (STATE_CREATING, STATE_ACTIVE, STATE_STOPPED, STATE_ORPHANED)
# States whose row is recorded DELETED -- or, for a launch whose desktop never ran, ORPHANED -- when EDH's
# listing leaves its desktop out; that absence is confirmed with EDH's session-state read before any such
# row is written.
UNLISTED_CONFIRMED_STATES = frozenset({STATE_CREATING, STATE_ACTIVE, STATE_STOPPED})
# Stamped on a row the sweep records DELETED because EDH stopped listing its desktop, and only then: a row
# deleted through VAMS never carries it. EDH's Save and Resume takes a desktop out of the listing while its
# image is taken and brings it back under a new session uuid with the same name and owner, so only a row
# carrying this stamp is re-adopted by a desktop listed under its name.
EDH_UNLISTED_AT_FIELD = "edhUnlistedAt"
# Rows the sweep reads by the session name of a listed desktop no scanned row claims, at most this many per
# sweep: a DELETED row whose expiry is set is no longer scanned, and its resumed desktop names its bootstrap.
MAX_RESUMED_ROW_LOOKUPS_PER_SWEEP = 50
# States in which the desktop is definitely not running, so its credential is revoked ...
REVOKING_STATES = frozenset({STATE_STOPPED, STATE_ORPHANED, STATE_DELETED})
# ... and those in which the secret container goes with it. A STOPPED desktop keeps its container: the
# start that follows writes a fresh key onto the same name.
SECRET_REMOVING_STATES = frozenset({STATE_ORPHANED, STATE_DELETED})
# A CREATING row written more recently than this may still be inside the launch worker's retries.
CREATING_GRACE_MINUTES = 30
# The launch worker's timeout: an attempt it stamped longer ago than this can no longer be running.
WORKER_ATTEMPT_SETTLE_SECONDS = 360
# A DELETED row whose launch never received a session id is still examined for this long after its
# last write, for the desktop EDH may have created for that launch after the row was deleted.
DELETED_RECLAIM_WINDOW_HOURS = 24
# A row written more recently than this is inside a request that manages its own credential (a start,
# a stop, a delete, the launch worker) and is left to that request; the next sweep sees its outcome.
RECENT_WRITE_GRACE_SECONDS = 120
# A session key no row names, whose name encodes no live desktop's bootstrap, is deleted once older than
# this, expired or not: a mint whose reply was lost leaves such a key behind, and a fresh key is named by
# its row well inside one sweep interval.
ORPHAN_KEY_GRACE_SECONDS = 900
# Actor of the cross-calls and audit rows the sweep makes.
SYSTEM_EVENT = {"lambdaCrossCall": {"userName": SYSTEM_USER_ID}}
# EDH schedule updates one sweep makes at most, and the invocation time below which it starts none: a sweep
# may meet many desktops still to schedule, and each update can take about a minute against an EDH that
# answers slowly. Rows left over are taken up by the next sweep.
MAX_SCHEDULE_APPLIES_PER_SWEEP = 50
SCHEDULE_APPLY_TIME_FLOOR_SECONDS = 120
# Reads of a failed launch's reason one sweep makes at most, and the invocation time below which it starts
# none: each is one or two reads of EDH's per-user listing under the batch profile. Rows left over are read
# by the next sweep or by their owner's next list.
MAX_FAILURE_READS_PER_SWEEP = 50
FAILURE_READ_TIME_FLOOR_SECONDS = 120


class DesktopIndex(NamedTuple):
    """EDH's active desktops, by session uuid and by (owner uid, session name)."""
    by_id: Dict[str, Dict[str, Any]]
    by_owner_name: Dict[Tuple[str, str], List[Dict[str, Any]]]


class ScheduleSweep:
    """One sweep's allowance of EDH schedule updates: the calls left, whether a refusal has closed it for
    the rest of the sweep, the monotonic instant the invocation ends at (None when unknown), and how many
    rows each limit left for the next sweep."""

    def __init__(self, deadline: Optional[float] = None):
        self.remaining = MAX_SCHEDULE_APPLIES_PER_SWEEP
        self.refused = False
        self.deadline = deadline
        self.left = {"allowance": 0, "refusal": 0, "time": 0}

    def holding_reason(self) -> Optional[str]:
        """Why no schedule update may start now, or None when one may."""
        if self.refused:
            return "refusal"
        if self.remaining <= 0:
            return "allowance"
        if self.deadline is not None and self.deadline - time.monotonic() < SCHEDULE_APPLY_TIME_FLOOR_SECONDS:
            return "time"
        return None


class FailureReadSweep:
    """One sweep's allowance of reads of a failed launch's reason: the reads left, and the monotonic instant
    the invocation ends at (None when unknown)."""

    def __init__(self, deadline: Optional[float] = None):
        self.remaining = MAX_FAILURE_READS_PER_SWEEP
        self.deadline = deadline

    def holding(self) -> bool:
        """Whether no read may start now: the allowance is spent or too little of the invocation is left."""
        if self.remaining <= 0:
            return True
        return self.deadline is not None and self.deadline - time.monotonic() < FAILURE_READ_TIME_FLOOR_SECONDS


def _invocation_deadline(context) -> Optional[float]:
    """The monotonic instant the invocation ends at, from the context's remaining time; None when the
    context reports no numeric remaining time."""
    try:
        remaining_ms = context.get_remaining_time_in_millis()
    except (AttributeError, TypeError):
        return None
    if isinstance(remaining_ms, bool) or not isinstance(remaining_ms, (int, float)):
        return None
    return time.monotonic() + remaining_ms / 1000.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _swept_rows_condition(now: datetime):
    """The scan filter: every row in a swept state, DELETED rows that still carry a key or a secret
    container, DELETED rows that carry no expiry, session-less DELETED rows written inside the reclaim
    window, and DELETED rows under a credential claim or a start stamp younger than its window, whose
    desktop may still be running on a key of its bootstrap. Each cutoff is written in the ISO shape of the
    attribute it is compared with, which is what DynamoDB compares."""
    cutoff = (now - timedelta(hours=DELETED_RECLAIM_WINDOW_HOURS)).isoformat()
    claim_cutoff = credential_claim_stamp(now - timedelta(seconds=CREDENTIAL_CLAIM_SECONDS))
    start_cutoff = start_stamp(now - timedelta(seconds=START_IN_PROGRESS_SECONDS))
    return (
        Attr("state").is_in(list(SWEPT_STATES))
        | (Attr("state").eq(STATE_DELETED) & Attr("apiKeyId").ne(""))
        | (Attr("state").eq(STATE_DELETED) & Attr("secretName").ne(""))
        | (Attr("state").eq(STATE_DELETED) & Attr(EXPIRES_AT_FIELD).not_exists())
        | (Attr("state").eq(STATE_DELETED) & Attr("sessionId").not_exists() & Attr("updatedAt").gt(cutoff))
        | (Attr("state").eq(STATE_DELETED) & Attr("credentialClaimedAt").gt(claim_cutoff))
        | (Attr("state").eq(STATE_DELETED) & Attr("startRequestedAt").gt(start_cutoff))
    )


def _scan_swept_rows(now: datetime) -> Iterator[Dict[str, Any]]:
    """Pages on the presence of LastEvaluatedKey: DynamoDB omits it only when the result set is exhausted."""
    kwargs: Dict[str, Any] = {"FilterExpression": _swept_rows_condition(now)}
    while True:
        response = session_table.scan(**kwargs)
        for item in response.get("Items", []):
            yield item
        if "LastEvaluatedKey" not in response:
            return
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def _index_desktops(desktops) -> DesktopIndex:
    by_id: Dict[str, Dict[str, Any]] = {}
    by_owner_name: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for desktop in desktops or []:
        if not isinstance(desktop, dict):
            continue
        session_id = desktop.get(EDH_SESSION_ID_FIELD)
        if session_id is not None:
            by_id[str(session_id)] = desktop
        owner, name = desktop.get(EDH_SESSION_OWNER_FIELD), desktop.get(EDH_SESSION_NAME_FIELD)
        if owner and name:
            by_owner_name.setdefault((str(owner), str(name)), []).append(desktop)
    return DesktopIndex(by_id, by_owner_name)


def _desktop_for_row(row: Dict[str, Any], index: DesktopIndex) -> Optional[Dict[str, Any]]:
    """EDH's record for a row: by session id, or -- for a launch that never received one -- the
    owner's newest desktop named after the row's bootstrap id."""
    session_id = row.get("sessionId")
    if session_id:
        return index.by_id.get(str(session_id))
    try:
        session_name = edh_session_name(row["bootstrapId"])
    except (KeyError, ValueError):
        return None
    matches = index.by_owner_name.get((str(row.get("edhUid")), session_name), [])
    if not matches:
        return None
    return max(matches, key=lambda desktop: str(desktop.get(EDH_SESSION_CREATED_ON_FIELD) or ""))


def _resumed_desktop(row: Dict[str, Any], index: DesktopIndex) -> Optional[Dict[str, Any]]:
    """The desktop EDH lists under the row's owner and session name with another session id: EDH's Save and
    Resume continues a desktop that way (a new session uuid, the same name and owner), and VAMS gives every
    launch a session name of its own, so it is the row's desktop. The newest wins; None when EDH lists none,
    or when the row has no session id (the launch path matches those by name already)."""
    session_id = row.get("sessionId")
    if not session_id:
        return None
    try:
        session_name = edh_session_name(row["bootstrapId"])
    except (KeyError, ValueError):
        return None
    matches = [desktop for desktop in index.by_owner_name.get((str(row.get("edhUid")), session_name), [])
               if str(desktop.get(EDH_SESSION_ID_FIELD)) != str(session_id)]
    if not matches:
        return None
    return max(matches, key=lambda desktop: str(desktop.get(EDH_SESSION_CREATED_ON_FIELD) or ""))


def _bootstrap_id_of_session_name(session_name) -> Optional[str]:
    """The bootstrap id a VAMS session name was made from (its 32 hex digits, hyphenated back as a uuid);
    None for a name VAMS did not make."""
    text = str(session_name or "")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", text):
        return None
    return f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"


def _resumed_rows(scanned_rows: List[Dict[str, Any]], index: DesktopIndex) -> List[Dict[str, Any]]:
    """Rows the scan no longer returns whose desktop EDH lists again under a new session id: a row the sweep
    recorded DELETED while EDH's Save and Resume had the desktop out of its listing, read by the bootstrap
    id the listed desktop's session name encodes. Only a DELETED row that carries the unlisted stamp, is
    owned by the desktop's owner and names another session id is returned; at most
    ``MAX_RESUMED_ROW_LOOKUPS_PER_SWEEP`` rows are read per sweep."""
    claimed = {str(row["sessionId"]) for row in scanned_rows if row.get("sessionId")}
    scanned = {str(row.get("bootstrapId")) for row in scanned_rows}
    found: List[Dict[str, Any]] = []
    lookups = 0
    for session_id, desktop in index.by_id.items():
        if session_id in claimed:
            continue
        bootstrap_id = _bootstrap_id_of_session_name(desktop.get(EDH_SESSION_NAME_FIELD))
        if bootstrap_id is None or bootstrap_id in scanned:
            continue
        if lookups >= MAX_RESUMED_ROW_LOOKUPS_PER_SWEEP:
            logger.info("Resumed-desktop row lookups reached the per-sweep limit; the rest wait for the next sweep")
            break
        lookups += 1
        scanned.add(bootstrap_id)
        row = _read_row(bootstrap_id)
        if (row is not None and row.get("state") == STATE_DELETED and row.get(EDH_UNLISTED_AT_FIELD)
                and str(row.get("edhUid")) == str(desktop.get(EDH_SESSION_OWNER_FIELD))
                and str(row.get("sessionId")) != session_id):
            found.append(row)
    return found


def _confirm_unlisted(rows: List[Dict[str, Any]], index: DesktopIndex, summary: Dict[str, int]) -> Set[str]:
    """Confirm with EDH's token-free session-state read that the desktops of rows EDH's listing left out are
    gone, before any such row is recorded DELETED, or ORPHANED as a failed launch: a desktop the read still
    reports active joins the index
    under its session id. When the read fails, the session ids it was asked about are returned and their
    rows sit this pass out, so one empty or truncated listing never detaches every workstation."""
    missing = list(dict.fromkeys(
        str(row["sessionId"]) for row in rows
        if row.get("sessionId") and row.get("state") in UNLISTED_CONFIRMED_STATES
        and str(row["sessionId"]) not in index.by_id and _resumed_desktop(row, index) is None))
    if not missing:
        return set()
    try:
        records = EdhClient(profile="batch").get_desktop_states(EDH_SERVICE_ACTOR_UID, missing)
    except EdhError:
        summary["failed"] += 1
        logger.warning(f"EDH session-state read failed; {len(missing)} unlisted workstation(s) are left "
                       "for the next sweep", exc_info=True)
        return set(missing)
    for record in records or []:
        if isinstance(record, dict) and record.get(EDH_SESSION_ID_FIELD):
            index.by_id[str(record[EDH_SESSION_ID_FIELD])] = record
    return set()


def _expiry_passed(value, now: datetime) -> bool:
    """True when an ISO expiry has passed. Blank is "never expires" and reads as not passed; an
    unreadable value reads as passed: expiry is a key's only hard bound, so it must not read as
    "no expiry"."""
    if not value:
        return False
    try:
        return parse_iso(value) <= now
    except (ValueError, TypeError, AttributeError):
        return True


def _key_expired(row: Dict[str, Any], now: datetime) -> bool:
    return _expiry_passed(row.get("keyExpiresAt"), now)


def _inside_creating_grace(row: Dict[str, Any], now: datetime) -> bool:
    """True while the launch worker may still be working on a CREATING row: its last write is younger
    than the grace window or, with its attempts spent, younger than the worker's timeout. The counter is
    stamped as an attempt starts, so a row on its last attempt is the worker's to settle only while that
    attempt can still be running; a timeout there settles nothing, and the row is written no further. An
    unreadable timestamp or attempt counter reads as past the window."""
    try:
        attempts_spent = int(row.get("launchAttempts") or 0) >= MAX_LAUNCH_ATTEMPTS
        touched = parse_iso(row.get("updatedAt") or row.get("createdAt") or "")
    except (ValueError, TypeError, AttributeError):
        return False
    window = (timedelta(seconds=WORKER_ATTEMPT_SETTLE_SECONDS) if attempts_spent
              else timedelta(minutes=CREATING_GRACE_MINUTES))
    return now - touched < window


def _written_at(row: Dict[str, Any]) -> Optional[datetime]:
    """The row's last write as an instant, or None when the stamp is missing or unreadable."""
    try:
        return parse_iso(row.get("updatedAt") or "")
    except (ValueError, TypeError, AttributeError):
        return None


def _recently_written(row: Dict[str, Any], now: datetime) -> bool:
    """True while the row's last write is younger than the grace, or later than the sweep began: the row
    is inside a request that manages its own credential. An unreadable timestamp reads as not recent."""
    touched = _written_at(row)
    return touched is not None and now - touched < timedelta(seconds=RECENT_WRITE_GRACE_SECONDS)


def _claimable(row: Dict[str, Any]) -> bool:
    """Whether the row carries a stamp a conditional write can be keyed on. The lease step stops or deletes
    the owner's desktop and then records that it did, so a row it could not claim afterwards is left alone."""
    return _written_at(row) is not None


def _read_row(bootstrap_id: str) -> Optional[Dict[str, Any]]:
    """The row as it is now, read consistently: the scan's copy may predate a write made during the sweep."""
    return session_table.get_item(Key={"bootstrapId": bootstrap_id}, ConsistentRead=True).get("Item")


def _credential_work_allowed(row: Dict[str, Any]) -> bool:
    """Whether this sweep may revoke or remove the row's credential: only while an apiKeyService function
    is named, since the revoke runs through it, and then for a row that names a key or holds a secret
    container, or for any row while session bootstrap is on. The reconciler keeps its revoke grants when
    the feature is off, so a credential left from a period with it on is still taken back."""
    if not bootstrap.api_key_service_function_name:
        return False
    return bool(row.get("apiKeyId")) or bool(row.get("secretName")) or bootstrap.is_enabled()


def _bind_session_secret(row: Dict[str, Any], desktop: Dict[str, Any],
                         summary: Optional[Dict[str, int]] = None) -> Dict[str, str]:
    """The row attribute a binding of the session secret to the desktop's instance adds, when the row
    holds a secret and EDH places the desktop on an instance the secret is not bound to; nothing
    otherwise, or when the binding failed (logged and counted as failed; the next sweep retries it)."""
    instance_id = desktop.get(EDH_FIELD_INSTANCE_ID)
    if not row.get("secretName") or not instance_id or str(instance_id) == row.get("secretBoundInstanceId"):
        return {}
    try:
        return bootstrap.bind_session_secret(row, instance_id)
    except bootstrap.SessionBootstrapError:
        if summary is not None:
            summary["failed"] += 1
        logger.exception(
            f"Session secret binding failed for bootstrap {row.get('bootstrapId')}; "
            "the next sweep retries it"
        )
        return {}


def reconcile_row(row: Dict[str, Any], index: DesktopIndex, now: datetime,
                  summary: Optional[Dict[str, int]] = None) -> Tuple[Dict[str, Any], bool, bool]:
    """(row attribute updates, revoke the credential, remove the secret container) for one row. A row
    recorded DELETED here with no credential to take back is given the expiry the table's TTL removes it
    by; a re-adopted row loses the expiry its deletion gave it. The live key of a desktop EDH lists
    stopped while a start still claims its row is left to a later sweep, as is every revoke and secret
    removal on a row under a fresh credential claim; the state updates still apply. A binding that fails,
    and a revoke left to a later sweep, are counted in ``summary`` when one is given."""
    updates, revoke, delete_secret = _reconcile_decision(row, index, now, summary)
    if revoke and credential_claim_fresh(row, now):
        # A renew is re-issuing the key: a revoke now would disown the key it is settling on the row, or
        # overwrite the secret it is about to fill.
        if summary is not None:
            summary["deferred"] += 1
        revoke = delete_secret = False
    return updates, revoke, delete_secret


def _reconcile_decision(row: Dict[str, Any], index: DesktopIndex, now: datetime,
                        summary: Optional[Dict[str, int]]) -> Tuple[Dict[str, Any], bool, bool]:
    """``reconcile_row``'s decision before the credential claim is considered."""
    state = row.get("state")
    has_key = bool(row.get("apiKeyId"))
    # A STOPPED desktop keeps its container with no key in it; the container goes when the desktop does.
    has_secret = bool(row.get("secretName"))
    has_credential = has_key or has_secret
    re_adopted = False
    resumed = None
    if state == STATE_DELETED:
        # A launch deleted before EDH listed its desktop re-adopts the desktop EDH created for it, and a row
        # the sweep recorded deleted while EDH's Save and Resume had its desktop out of the listing re-adopts
        # the resumed desktop; any other DELETED row has its removal recorded and only a leftover credential
        # left to act on.
        if not row.get("sessionId"):
            desktop = _desktop_for_row(row, index)
        elif row.get(EDH_UNLISTED_AT_FIELD):
            desktop = resumed = _resumed_desktop(row, index)
        else:
            desktop = None
        if desktop is None:
            # A recorded removal that carries neither a credential nor an expiry takes the TTL now.
            if not has_credential and row.get(EXPIRES_AT_FIELD) is None:
                return {EXPIRES_AT_FIELD: deleted_row_expiry(now)}, False, False
            return {}, has_credential, has_credential
        re_adopted = True
    else:
        desktop = _desktop_for_row(row, index)
        if desktop is None:
            desktop = resumed = _resumed_desktop(row, index)
    updates: Dict[str, Any] = {}
    if desktop is not None:
        updates = observed_row_attributes(row, desktop)
        if resumed is not None:
            # The row follows its desktop's new session; the never-auto-start schedule is set on it afresh.
            updates["sessionId"] = str(resumed[EDH_SESSION_ID_FIELD])
            if row.get("scheduleAppliedAt"):
                updates["scheduleAppliedAt"] = None
            if row.get(EDH_UNLISTED_AT_FIELD):
                updates[EDH_UNLISTED_AT_FIELD] = None
            logger.info(f"Bootstrap {row['bootstrapId']} follows its desktop resumed by EDH under a new session")
        if not re_adopted:
            updates.update(_bind_session_secret(row, desktop, summary))
            updates.update(_start_resolution(row, updates, desktop, now))
    elif state == STATE_CREATING and not row.get("sessionId"):
        if _inside_creating_grace(row, now):
            return {}, False, False
        updates = {"state": STATE_ORPHANED, "lastError": LAST_ERROR_CREATE_TIMED_OUT}
    elif state == STATE_CREATING:
        # EDH records a desktop whose placement failed as ``error`` and deactivates it within minutes; a
        # desktop that never ran cannot be held out of the listing by Save and Resume, so a launch EDH no
        # longer has is a failed launch, kept for its owner to see, not a removed desktop.
        updates = {"state": STATE_ORPHANED}
        if not row.get("lastError"):
            updates["lastError"] = LAST_ERROR_LAUNCH_ERROR
    elif state in (STATE_ACTIVE, STATE_STOPPED):
        # EDH lists every desktop that is not deleted; one it no longer lists was removed outside VAMS, or is
        # held out of the listing by EDH's Save and Resume, which the stamp lets a later sweep re-adopt.
        updates = {"state": STATE_DELETED, EDH_UNLISTED_AT_FIELD: now.isoformat()}
    if updates.get("state") in SECRET_REMOVING_STATES and row.get("connectionAvailable") is not False:
        updates["connectionAvailable"] = False
    final_state = updates.get("state", state)
    if re_adopted:
        # A re-adopted row carries no credential: a key its delete left behind goes now, secret
        # container and all, and its owner mints a fresh one by stopping and starting the desktop.
        if row.get(EXPIRES_AT_FIELD) is not None:
            updates[EXPIRES_AT_FIELD] = None
        return updates, has_credential, has_credential
    if final_state == STATE_DELETED and not has_credential:
        updates[EXPIRES_AT_FIELD] = deleted_row_expiry(now)
    remove_secret = has_secret and final_state in SECRET_REMOVING_STATES
    # A start under way minted the key for the desktop it is starting, which EDH lists stopped until its
    # capacity wait settles; an expired key goes regardless.
    held = (has_key and final_state == STATE_STOPPED and start_in_progress(row, now)
            and not _key_expired(row, now))
    if held and summary is not None:
        summary["deferred"] += 1
    revoke = (has_key and not held and (final_state in REVOKING_STATES or _key_expired(row, now))) or remove_secret
    return updates, revoke, revoke and final_state in SECRET_REMOVING_STATES


def _start_resolution(row: Dict[str, Any], updates: Dict[str, Any], desktop: Dict[str, Any],
                      now: datetime) -> Dict[str, Any]:
    """The attributes that resolve a start the launch worker could not: a row this pass leaves STOPPED, still
    marked with a start the owner asked for whose stamp is ``START_RESOLVE_SECONDS`` old, whose desktop EDH lists
    stopped, did not start. Its mark and stamp go and it records START_FAILED with a ``startFailure`` that names
    nothing EDH said; the key the start minted is then revoked as any stopped desktop's is. The desktop is never
    deleted for it: the row stays STOPPED. Nothing for any other row."""
    if (not start_resolve_due({**row, **updates}, now)
            or str(desktop.get(EDH_SESSION_STATE_FIELD) or "").lower() != EDH_SESSION_STATE_STOPPED):
        return {}
    logger.warning(f"The start of bootstrap {row.get('bootstrapId')} was not resolved and EDH lists its desktop "
                   "stopped; recorded as a failed start")
    return {ACTIVITY_FIELD: None, "startRequestedAt": None, "lastError": LAST_ERROR_START_FAILED,
            START_FAILURE_FIELD: start_failure(None, None, now)}


def _persist(bootstrap_id: str, updates: Dict[str, Any], seen_updated_at, *, touch: bool = True) -> Optional[str]:
    """Write ``updates`` onto the session row, with a fresh updatedAt unless ``touch`` is off, provided its
    updatedAt is still ``seen_updated_at`` (absent when the row carried none); a None value removes its
    attribute. Returns the updatedAt the row carries after the write (the empty string for a row that
    carries none), or None when another writer reached the row first."""
    stamp = _now().isoformat() if touch else (seen_updated_at or "")
    attributes = {**updates, "updatedAt": stamp} if touch else dict(updates)
    names = {f"#a{i}": key for i, key in enumerate(attributes)}
    placeholder = {key: name for name, key in names.items()}
    values = {f":v{i}": value for i, value in enumerate(attributes.values()) if value is not None}
    sets = [f"{placeholder[key]} = :v{i}" for i, (key, value) in enumerate(attributes.items()) if value is not None]
    removes = [placeholder[key] for key, value in attributes.items() if value is None]
    expression = "SET " + ", ".join(sets) + (" REMOVE " + ", ".join(removes) if removes else "")
    if "updatedAt" not in placeholder:
        names["#seen"] = "updatedAt"
        placeholder["updatedAt"] = "#seen"
    if seen_updated_at:
        condition = f"{placeholder['updatedAt']} = :seen"
        values[":seen"] = seen_updated_at
    else:
        condition = f"attribute_not_exists({placeholder['updatedAt']})"
    try:
        session_table.update_item(
            Key={'bootstrapId': bootstrap_id},
            UpdateExpression=expression,
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        return None
    return stamp


def _release_claim(bootstrap_id: str, claim: Dict[str, Any]) -> None:
    """Give up the credential claim and the start slot ``claim`` took on the row, each only while the row
    still holds the stamp this sweep wrote, so another writer's is never removed; the credential attributes
    stay for the next sweep. A release another write overtook needs nothing more, and one that fails is
    logged and its stamp left to age out of its window."""
    for attribute in ("credentialClaimedAt", "startRequestedAt"):
        if attribute not in claim:
            continue
        try:
            session_table.update_item(
                Key={'bootstrapId': bootstrap_id},
                UpdateExpression="SET #updated = :updated REMOVE #stamp",
                ConditionExpression="#stamp = :stamp",
                ExpressionAttributeNames={"#stamp": attribute, "#updated": "updatedAt"},
                ExpressionAttributeValues={":stamp": claim[attribute], ":updated": _now().isoformat()},
            )
        except Exception as e:
            code = e.response.get("Error", {}).get("Code") if isinstance(e, ClientError) else None
            if code != "ConditionalCheckFailedException":
                logger.exception(f"{attribute} release failed for bootstrap {bootstrap_id}; it ages out")


def _orphan_key_settled(key: Dict[str, Any], now: datetime) -> bool:
    """True once a key record is older than the orphan grace: a row naming it would have been written by
    now. A missing or unreadable creation time reads as settled; a key nothing can date is not kept."""
    try:
        return now - parse_iso(key.get("createdAt") or "") >= timedelta(seconds=ORPHAN_KEY_GRACE_SECONDS)
    except (ValueError, TypeError, AttributeError):
        return True


def _sweep_orphan_keys(referenced_key_ids: Set[str], live_bootstrap_ids: Set[str], now: datetime) -> Tuple[int, int]:
    """Delete every ``temporary-session-edh-*`` key record that no session row names once it has expired or aged
    past the orphan grace, save an unexpired key whose name encodes one of ``live_bootstrap_ids``, the
    bootstraps of desktops that may still be running: such a desktop may be signing in with it. A key past
    its expiry is audited as expired and any other as revoked, since its desktop may have held it. Returns
    (deleted, failed). A key a row names is that row's reconciliation to handle; a younger orphan may be a
    mint whose row write has not landed yet."""
    try:
        keys = bootstrap.list_session_keys(SYSTEM_USER_ID)
    except Exception:
        logger.exception("Session key listing failed; orphan keys wait for the next sweep")
        return 0, 1
    deleted = failed = 0
    for key in keys:
        api_key_id = str(key.get("apiKeyId", ""))
        if not api_key_id or api_key_id in referenced_key_ids:
            continue
        expired = _expiry_passed(key.get("expiresAt"), now)
        if not expired and bootstrap.bootstrap_id_from_key_name(key.get("apiKeyName")) in live_bootstrap_ids:
            continue
        if not expired and not _orphan_key_settled(key, now):
            continue
        try:
            bootstrap.delete_orphan_key(
                SYSTEM_EVENT, key, SYSTEM_USER_ID,
                operation=bootstrap.AUDIT_OPERATION_EXPIRE if expired else bootstrap.AUDIT_OPERATION_REVOKE)
            deleted += 1
        except Exception:
            failed += 1
            logger.exception(f"Orphan session key delete failed for {api_key_id}")
    return deleted, failed


def _live_state(row: Dict[str, Any], index: DesktopIndex) -> Optional[str]:
    """The state EDH's listing implies for the row's desktop: None when EDH lists no desktop for the row.
    A listed desktop in a state outside the mapping yields the empty string, which no lease branch acts on."""
    desktop = _desktop_for_row(row, index) or _resumed_desktop(row, index)
    if desktop is None:
        return None
    return state_for_edh_session_state(desktop.get(EDH_SESSION_STATE_FIELD), "")


def _apply_lease_action(action: str, row: Dict[str, Any], index: DesktopIndex, now: datetime,
                        client_factory: Callable[[], EdhClient], summary: Dict[str, int]) -> bool:
    """Carry out the lease action on the row's desktop, checked against EDH's live view first: the stored
    state can be a sweep stale against a desktop the owner restarted from the EDH console, so a desktop
    EDH lists as running or starting is stopped but never deleted, and one EDH no longer lists is recorded
    as gone without a call. `leaseStoppedAt` is the delete guard: the first sweep to find a desktop past
    its expiry stamps it, stopping it first when it still runs, the grace runs from that stamp, and the
    state merge clears it whenever the desktop is seen running again. A delete claims the row before EDH
    is called, so a start cannot slip in between, and the claim is reverted when EDH refuses; a refused
    stop is simply left for the next sweep. Inaction is the safe direction on every failure, since acting
    means stopping or deleting the owner's desktop. Returns True when the row is settled for this pass
    and False when the ordinary reconciliation should run for it."""
    bootstrap_id, edh_uid, session_id = row["bootstrapId"], row["edhUid"], row["sessionId"]
    live = _live_state(row, index)
    if action == "stop":
        if live in (STATE_ACTIVE, STATE_CREATING):
            try:
                client_factory().stop_desktop(edh_uid, session_id)
            except EdhError:
                summary["failed"] += 1
                logger.warning(f"Lease stop failed for bootstrap {bootstrap_id}; retried next sweep", exc_info=True)
                return False
        elif live != STATE_STOPPED:
            return False
        stamped = _persist(bootstrap_id, {"state": STATE_STOPPED, "leaseStoppedAt": now.isoformat(),
                                          "lastError": LAST_ERROR_LEASE_EXPIRED, "connectionAvailable": False},
                           row["updatedAt"])
        summary["deferred" if stamped is None else "updated"] += 1
        return True
    # A row holding no credential is left to the table's TTL once its desktop is gone.
    expiry = {} if row.get("apiKeyId") or row.get("secretName") else {EXPIRES_AT_FIELD: deleted_row_expiry(now)}
    if live is None:
        stamped = _persist(bootstrap_id, {"state": STATE_DELETED, "connectionAvailable": False, **expiry},
                           row["updatedAt"])
        summary["deferred" if stamped is None else "updated"] += 1
        return True
    if live != STATE_STOPPED:
        return False
    stamp = _persist(bootstrap_id, {"state": STATE_DELETED, "connectionAvailable": False}, row["updatedAt"])
    if stamp is None:
        summary["deferred"] += 1
        return True
    try:
        client_factory().delete_desktop(edh_uid, session_id)
    except (EdhNotFoundError, EdhContractError):
        # The desktop is already gone, or EDH answered success with a body it could not read: the request
        # may have taken effect, and a desktop is never deleted twice.
        logger.info(f"Lease delete for bootstrap {bootstrap_id} found the desktop gone or unconfirmed; claim stands")
    except EdhError:
        summary["failed"] += 1
        logger.warning(f"Lease delete failed for bootstrap {bootstrap_id}; retried next sweep", exc_info=True)
        if _persist(bootstrap_id, {"state": STATE_STOPPED}, stamp) is None:
            logger.warning(f"Lease delete claim on bootstrap {bootstrap_id} was overtaken before it could be reverted")
        return True
    if expiry and _persist(bootstrap_id, expiry, stamp) is None:
        summary["deferred"] += 1
    summary["updated"] += 1
    return True


def _apply_schedule(row: Dict[str, Any], index: DesktopIndex, now: datetime,
                    client_factory: Callable[[], EdhClient], summary: Dict[str, int],
                    schedule: ScheduleSweep) -> None:
    """Give the row's desktop the EDH schedule that never starts it automatically, and stamp the row, when
    the row has a session id EDH lists, is not DELETED and carries no stamp. A desktop EDH already lists on
    that schedule is stamped without a call; otherwise the call is made under the row's owner, on its own
    client, while the sweep's allowance holds. A desktop EDH cannot find under that owner is left to the
    listing; any other failure is counted and closes the allowance for the rest of the sweep. The stamp
    leaves updatedAt as it is, since it records no change of the desktop; a stamp another writer overtook
    is deferred: EDH already holds the schedule, so the next sweep stamps it."""
    session_id = row.get("sessionId")
    if not session_id or row.get("scheduleAppliedAt") or row.get("state") == STATE_DELETED:
        return
    desktop = index.by_id.get(str(session_id))
    if desktop is None:
        return
    bootstrap_id = row["bootstrapId"]
    if not schedule_never_auto_starts(desktop.get(EDH_SCHEDULE_FIELD)):
        reason = schedule.holding_reason()
        if reason is not None:
            schedule.left[reason] += 1
            return
        schedule.remaining -= 1
        try:
            client_factory().update_desktop_schedule(row["edhUid"], session_id, never_auto_start_schedule())
        except EdhNotFoundError:
            logger.info(f"EDH found no desktop to schedule for bootstrap {bootstrap_id}; the listing reconciles it")
            return
        except EdhError:
            summary["failed"] += 1
            schedule.refused = True
            logger.warning(f"EDH schedule update failed for bootstrap {bootstrap_id}; no further schedule is "
                           "set this sweep", exc_info=True)
            return
    if _persist(bootstrap_id, {"scheduleAppliedAt": now.isoformat()}, row.get("updatedAt"), touch=False) is None:
        summary["deferred"] += 1
        return
    summary["scheduled"] += 1
    logger.info(f"EDH schedule set to never start automatically for bootstrap {bootstrap_id}")


def _capture_launch_failure(row: Dict[str, Any], seen_updated_at, now: datetime,
                            client_factory: Callable[[], EdhClient], summary: Dict[str, int],
                            failure_reads: FailureReadSweep) -> None:
    """Give a row this pass leaves ORPHANED with a session id, no captured reason and no read within the
    interval the reason EDH recorded for its failed launch, with the ``lastError`` it implies and the read's
    stamp, under a write conditioned on ``seen_updated_at``, the updatedAt the row carries after this pass.
    The read names the row's own EDH uid, since EDH's per-user listing is owner-scoped, and runs on its own
    client within the sweep's allowance; a row the allowance holds is left unstamped for a later read. A read
    that fails is counted and writes the stamp alone; a write another writer overtook is deferred."""
    if not edh_failure_read_due(row, now):
        return
    if failure_reads.holding():
        return
    failure_reads.remaining -= 1
    try:
        failure = client_factory().get_desktop_failure(row.get("edhUid"), row["sessionId"])
    except (EdhError, ValueError):
        summary["failed"] += 1
        logger.warning(f"EDH's reason for the failed launch of bootstrap {row['bootstrapId']} was not read, and is "
                       "read again later", exc_info=True)
        failure = None
    attributes = edh_failure_attributes(row, failure, now)
    # A read that found nothing records no change of the row, so it leaves updatedAt as it is.
    if _persist(row["bootstrapId"], attributes, seen_updated_at, touch=bool(failure)) is None:
        summary["deferred"] += 1
        return
    if EDH_FAILURE_FIELD in attributes:
        summary["failuresCaptured"] += 1
        logger.info(f"Recorded EDH's reason for the failed launch of bootstrap {row['bootstrapId']}")


def _reconcile(scanned: Dict[str, Any], index: DesktopIndex, now: datetime, summary: Dict[str, int],
               client_factory: Optional[Callable[[], EdhClient]] = None,
               policy: Optional[Dict[str, Any]] = None, schedule: Optional[ScheduleSweep] = None,
               failure_reads: Optional[FailureReadSweep] = None) -> None:
    """Reconcile one row the scan returned. The row is re-read before anything is decided and left alone
    while a request of its own is inside it; the state is claimed with a conditional write before the
    credential is touched, and the credential attributes are cleared, under the same condition, only
    once the revoke has succeeded. The claim on a row that ends STOPPED stamps the row's start slot unless
    a start already holds it, and every revoking claim stamps the row's credential claim too; the clear
    removes both. A revoke that fails, or a clear another write overtakes, gives each up while the row
    still holds this sweep's stamp and leaves the credential for the next sweep; a sweep that ends
    part-way through a revoke leaves both to age out of their windows. A lost condition defers the row
    to the next sweep; a row gone since the scan, or a credential this deployment cannot revoke, is
    skipped. A lease the policy says has run out is acted on ahead of all this whenever EDH's live view
    bears the action out: a stop for a desktop it lists as running, starting or stopped, a delete for one
    it lists as stopped or no longer lists; in any other case the row takes the ordinary path. A row the
    ordinary path leaves unchanged has its desktop's EDH schedule set when the sweep's schedule allowance
    is given, unless a lease stop EDH refused on this pass: its schedule is left to a later one. A row the
    ordinary path leaves ORPHANED is then given the reason EDH recorded for its failed launch when the sweep's
    allowance of such reads is given."""
    bootstrap_id = scanned["bootstrapId"]
    row = _read_row(bootstrap_id)
    if row is None:
        summary["skipped"] += 1
        return
    if _recently_written(row, now):
        summary["deferred"] += 1
        return
    lease_failed = False
    if (client_factory is not None and policy is not None
            and row.get("sessionId") and _claimable(row)):
        action = lease_action(row, policy, now)
        if action is not None:
            failed = summary["failed"]
            if _apply_lease_action(action, row, index, now, client_factory, summary):
                return
            lease_failed = summary["failed"] > failed
    updates, revoke, delete_secret = reconcile_row(row, index, now, summary)
    if revoke and not _credential_work_allowed(row):
        # The credential stays recorded and unclaimed; the state reconciliation still runs.
        summary["skipped"] += 1
        revoke = delete_secret = False
    capture = failure_reads is not None and client_factory is not None
    if not updates and not revoke:
        if schedule is not None and client_factory is not None and not lease_failed:
            _apply_schedule(row, index, now, client_factory, summary, schedule)
        if capture:
            _capture_launch_failure(row, row.get("updatedAt"), now, client_factory, summary, failure_reads)
        return
    claim = updates
    if revoke:
        # A renew reads the claim as the row taken, so none re-issues a key while this revoke takes the
        # stored ones back and overwrites the secret.
        claim = {**claim, "credentialClaimedAt": credential_claim_stamp(_now())}
    if revoke and updates.get("state", row.get("state")) == STATE_STOPPED and not start_in_progress(row, now):
        # A start reads the stamp as a start already under way, so none mints a key while this revoke takes
        # the stored one back and overwrites the secret.
        claim = {**claim, "startRequestedAt": start_stamp(_now())}
    stamp = _persist(bootstrap_id, claim, row.get("updatedAt"))
    if stamp is None:
        summary["deferred"] += 1
        return
    if updates:
        summary["updated"] += 1
    if not revoke:
        if capture:
            _capture_launch_failure({**row, **updates}, stamp, now, client_factory, summary, failure_reads)
        return
    try:
        cleared = bootstrap.revoke_session(SYSTEM_EVENT, row, delete_secret=delete_secret, actor=SYSTEM_USER_ID)
    except Exception:
        # The credential stays recorded for the next sweep; the claim goes now, so no start or renew waits
        # out a revoke that is over.
        _release_claim(bootstrap_id, claim)
        raise
    summary["revoked"] += 1
    if delete_secret and updates.get("state", row.get("state")) == STATE_DELETED:
        # The desktop is gone and so, now, is its credential: the row is left to the table's TTL.
        cleared = {**cleared, EXPIRES_AT_FIELD: deleted_row_expiry(now)}
    cleared = {**cleared, "credentialClaimedAt": None}
    if "startRequestedAt" in claim:
        cleared = {**cleared, "startRequestedAt": None}
    settled = _persist(bootstrap_id, cleared, stamp)
    if settled is None:
        # Another write reached the row after the revoke: the credential attributes wait for the next sweep,
        # and the claim goes all the same.
        _release_claim(bootstrap_id, claim)
        summary["deferred"] += 1
        return
    if not updates:
        summary["updated"] += 1
    if capture:
        _capture_launch_failure({**row, **updates}, settled, now, client_factory, summary, failure_reads)


def lambda_handler(event, context: LambdaContext) -> Dict[str, int]:
    now = _now()
    client = EdhClient(profile="batch")
    summary = {"examined": 0, "updated": 0, "revoked": 0, "scheduled": 0, "deferred": 0, "skipped": 0,
               "failed": 0, "orphanKeysDeleted": 0, "failuresCaptured": 0}
    deadline = _invocation_deadline(context)
    schedule = ScheduleSweep(deadline)
    failure_reads = FailureReadSweep(deadline)
    try:
        policy = read_lease_policy()
    except Exception:
        # A policy the sweep cannot read disables only the lease step: the rest of the sweep -- state
        # reconciliation and credential revocation -- does not depend on it and still runs. Leaving the
        # policy None is also the safe direction, since a lease action stops or deletes a desktop. The
        # skipped step is counted as failed.
        summary["failed"] += 1
        logger.exception("Lease policy unreadable; this sweep takes no lease action")
        policy = None
    try:
        desktops = client.list_all_desktops(edh_uid=EDH_SERVICE_ACTOR_UID)
    except EdhError:
        logger.exception("EDH list_all failed; the sweep is skipped")
        raise
    index = _index_desktops(desktops)
    scanned_rows = list(_scan_swept_rows(now))
    scanned_rows.extend(_resumed_rows(scanned_rows, index))
    unconfirmed = _confirm_unlisted(scanned_rows, index, summary)

    # Every key some row names, and the bootstrap of every desktop that may still be running -- by its row as
    # the scan returned it, or by the running or starting state EDH lists it in, which this pass records --
    # collected before any revoke, so the orphan pass touches neither.
    referenced: Set[str] = set()
    live: Set[str] = set()
    for scanned in scanned_rows:
        summary["examined"] += 1
        bootstrap_id = scanned.get("bootstrapId")
        if scanned.get("apiKeyId"):
            referenced.add(str(scanned["apiKeyId"]))
        held_back = bool(scanned.get("sessionId")) and str(scanned["sessionId"]) in unconfirmed
        if bootstrap_id and (held_back or session_row_live(scanned, now)
                             or _live_state(scanned, index) in (STATE_ACTIVE, STATE_CREATING)):
            live.add(str(bootstrap_id))
        if held_back:
            summary["deferred"] += 1
            continue
        try:
            # The batch profile's time budget is per client instance, so each lease action and schedule
            # update opens its own.
            _reconcile(scanned, index, now, summary, client_factory=lambda: EdhClient(profile="batch"),
                       policy=policy, schedule=schedule, failure_reads=failure_reads)
        except Exception:
            summary["failed"] += 1
            logger.exception(f"Reconcile failed for bootstrap {bootstrap_id}")
    if any(schedule.left.values()):
        logger.info(f"{sum(schedule.left.values())} EDH schedules left for the next sweep: "
                    f"{schedule.left['allowance']} past the per-sweep allowance, {schedule.left['refusal']} "
                    f"after a refusal, {schedule.left['time']} inside the invocation's time floor")

    if bootstrap.is_enabled():
        # The orphan pass reads the key listing through apiKeyService, a grant that comes with the feature.
        deleted, failed = _sweep_orphan_keys(referenced, live, now)
        summary["orphanKeysDeleted"] += deleted
        summary["failed"] += failed

    logger.info("EDH session sweep complete", extra=summary)
    return summary
