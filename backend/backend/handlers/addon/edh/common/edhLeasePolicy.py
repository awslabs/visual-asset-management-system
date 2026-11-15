# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The EDH workstation lease policy: one settings row in the session table, plus the pure decisions
the launch handler, the renew route and the reconciler make from it. The settings row lives under a
reserved key that cannot collide with a session row's 36-char UUID bootstrapId."""
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from common.addonResourceNames import AddonResourceKeys
from common.resourceNames import get_table_name
from customLogging.logger import safeLogger

from .edhIdentity import SYSTEM_USER_ID
from .edhSessionState import STATE_ACTIVE, STATE_STOPPED

logger = safeLogger(service_name="EdhLeasePolicy")

# Carries no `state`, so the state-filtered scans that read session rows never yield it.
LEASE_SETTINGS_KEY = "SETTINGS#lease"
LEASE_POLICY_FIELDS = ("enabled", "leaseHours", "forced", "graceHours")
DEFAULT_LEASE_POLICY = {"enabled": False, "leaseHours": 8, "forced": False, "graceHours": 24}

LEASE_HOURS_MIN, LEASE_HOURS_MAX = 1, 720
GRACE_HOURS_MIN, GRACE_HOURS_MAX = 0, 168


class LeaseValidationError(Exception):
    """A launch or policy request that the lease rules reject; the handler maps it to a 400."""


retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource("dynamodb", config=retry_config)

try:
    _session_table_name = get_table_name(AddonResourceKeys.EDH_SESSION_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

_session_table = dynamodb.Table(_session_table_name)


def _normalise(item: dict) -> dict:
    return {
        "enabled": bool(item.get("enabled", DEFAULT_LEASE_POLICY["enabled"])),
        "leaseHours": int(item.get("leaseHours", DEFAULT_LEASE_POLICY["leaseHours"])),
        "forced": bool(item.get("forced", DEFAULT_LEASE_POLICY["forced"])),
        "graceHours": int(item.get("graceHours", DEFAULT_LEASE_POLICY["graceHours"])),
        "updatedAt": item.get("updatedAt"),
        "updatedBy": item.get("updatedBy"),
    }


def read_lease_policy() -> dict:
    """The stored policy, or the default when no settings row exists yet."""
    item = _session_table.get_item(Key={"bootstrapId": LEASE_SETTINGS_KEY}, ConsistentRead=True).get("Item")
    if not item:
        return {**DEFAULT_LEASE_POLICY, "updatedAt": None, "updatedBy": None}
    return _normalise(item)


def _validate_ranges(policy: dict) -> None:
    if not (LEASE_HOURS_MIN <= policy["leaseHours"] <= LEASE_HOURS_MAX):
        raise LeaseValidationError(f"leaseHours must be {LEASE_HOURS_MIN}-{LEASE_HOURS_MAX}")
    if not (GRACE_HOURS_MIN <= policy["graceHours"] <= GRACE_HOURS_MAX):
        raise LeaseValidationError(f"graceHours must be {GRACE_HOURS_MIN}-{GRACE_HOURS_MAX}")


def write_lease_policy(patch: dict, user_id: str) -> dict:
    """Merge ``patch`` (known, non-None fields only) onto the current policy and persist it."""
    current = read_lease_policy()
    merged = {k: current[k] for k in LEASE_POLICY_FIELDS}
    for key in LEASE_POLICY_FIELDS:
        if patch.get(key) is not None:
            merged[key] = bool(patch[key]) if key in ("enabled", "forced") else int(patch[key])
    _validate_ranges(merged)
    now = datetime.now(timezone.utc).isoformat()
    _session_table.put_item(Item={"bootstrapId": LEASE_SETTINGS_KEY, **merged, "updatedAt": now, "updatedBy": user_id})
    return {**merged, "updatedAt": now, "updatedBy": user_id}


def seed_lease_policy(defaults: dict) -> bool:
    """Write the deploy-time default policy only when no settings row exists. Returns True if it wrote."""
    item = {
        "bootstrapId": LEASE_SETTINGS_KEY,
        "enabled": bool(defaults.get("enabled", DEFAULT_LEASE_POLICY["enabled"])),
        "leaseHours": int(defaults.get("leaseHours", DEFAULT_LEASE_POLICY["leaseHours"])),
        "forced": bool(defaults.get("forced", DEFAULT_LEASE_POLICY["forced"])),
        "graceHours": int(defaults.get("graceHours", DEFAULT_LEASE_POLICY["graceHours"])),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "updatedBy": SYSTEM_USER_ID,
    }
    try:
        _session_table.put_item(Item=item, ConditionExpression="attribute_not_exists(bootstrapId)")
        return True
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        return False


def parse_iso(value) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def compute_expiry(now: datetime, hours: int) -> str:
    return (now + timedelta(hours=hours)).isoformat()


def resolve_launch_lease(policy: dict, requested_hours: Optional[int], now: datetime) -> Tuple[Optional[str], Optional[int]]:
    """The (leaseExpiresAt, leaseHoursAtLaunch) a launch is stamped with, or (None, None) for no lease."""
    if not policy.get("enabled"):
        if requested_hours is not None:
            raise LeaseValidationError("Leases are disabled for this deployment")
        return None, None
    if policy.get("forced"):
        hours = int(policy["leaseHours"])
        return compute_expiry(now, hours), hours
    if requested_hours is None:
        return None, None
    hours = int(requested_hours)
    if hours < LEASE_HOURS_MIN or hours > int(policy["leaseHours"]):
        raise LeaseValidationError(f"leaseHours must be {LEASE_HOURS_MIN}-{policy['leaseHours']}")
    return compute_expiry(now, hours), hours


def lease_action(row: dict, policy: dict, now: datetime) -> Optional[str]:
    """What the reconciler should do to this row's desktop this pass: 'stop', 'delete', or None.

    A 'stop' is asked for any unstamped row past its expiry, running or already stopped: on a desktop
    that is already stopped it is the stamp that starts the grace, so a desktop stopped before its lease
    ran out still ages to deletion."""
    if not policy or not policy.get("enabled"):
        return None
    expires_at = row.get("leaseExpiresAt")
    if not expires_at:
        return None
    try:
        expiry = parse_iso(expires_at)
    except (ValueError, TypeError, AttributeError):
        # Unlike a credential expiry, an unreadable lease must not imply an action: acting means
        # stopping or deleting the owner's desktop, so a corrupt row is reported and left alone.
        logger.warning("Unreadable leaseExpiresAt on %s; no lease action taken", row.get("bootstrapId"))
        return None
    state = row.get("state")
    if state in (STATE_ACTIVE, STATE_STOPPED) and not row.get("leaseStoppedAt") and now >= expiry:
        return "stop"
    if state == STATE_STOPPED and row.get("leaseStoppedAt"):
        try:
            stopped = parse_iso(row["leaseStoppedAt"])
        except (ValueError, TypeError, AttributeError):
            logger.warning("Unreadable leaseStoppedAt on %s; no lease action taken", row.get("bootstrapId"))
            return None
        # The grace runs from whichever came later, the expiry or the stamp the sweep set, so a stamp that
        # landed late never shortens the owner's window.
        grace_hours = int(policy.get("graceHours", DEFAULT_LEASE_POLICY["graceHours"]))
        if now >= max(expiry, stopped) + timedelta(hours=grace_hours):
            return "delete"
    return None
