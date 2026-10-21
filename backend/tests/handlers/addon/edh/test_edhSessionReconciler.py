# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The reconciler derives every row from EDH's listing through the shared session-state mapping: a
desktop EDH reports stopped or in error loses its credential (a stopped one whose row a start still
claims, on a later sweep), a desktop EDH no longer lists is recorded
deleted and loses key and secret container -- the container alone when a stop already took the key -- a
launch EDH never listed is orphaned once the worker's grace window has passed (or, its launch attempts
spent, once the last attempt has timed out), a launch EDH lists by session name is completed, a
session-less launch deleted before EDH listed its desktop re-adopts that desktop, an expired key is
revoked wherever it is, a DELETED row that still carries a key or a container loses both, a DELETED row
left without a credential is stamped with the expiry the table's TTL removes it by, and a pending or
unknown EDH state never revokes. A listed desktop whose row a pass leaves otherwise unchanged is given
the EDH schedule that never starts it, once, within the sweep's allowance of such calls. A row under a
fresh credential claim -- a renew re-issuing its key -- has every revoke held, and the sweep's own revoke
takes that claim too. After the rows it deletes the session keys no row names once they are expired or
older than the orphan grace, save that a desktop that may still be running keeps every unexpired key of
its bootstrap until the key's own expiry, auditing each deletion as an expiry or, for a key not yet
expired, as a revoke. A desktop the cluster listing shows running is recorded connectable. The sweep
pages the table to exhaustion, acts as SYSTEM_USER
under the service actor's EDH uid, re-reads every row before deciding, leaves a row a request wrote
moments ago to that request, claims a row with a conditional write before its credential is touched,
and survives a failing row or key."""

import importlib.util
import io
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from boto3.dynamodb.conditions import ConditionExpressionBuilder
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap
from backend.backend.handlers.addon.edh import edhSessionReconciler as rec
from backend.backend.handlers.addon.edh.common.edhClient import (
    EDH_SERVICE_ACTOR_UID, EdhApiError, EdhClient, EdhContractError, EdhNotFoundError, EdhOutcomeUnknownError,
    EdhUnavailableError, edh_session_name,
)
from backend.backend.handlers.addon.edh.common.edhSessionState import (
    CREDENTIAL_CLAIM_SECONDS, EDH_FIELD_INSTANCE_ID, EDH_SCHEDULE_FIELD,
    EDH_SESSION_STATE_FIELD, EXPIRES_AT_FIELD, LAST_ERROR_CREATE_TIMED_OUT, LAST_ERROR_LAUNCH_ERROR,
    START_IN_PROGRESS_SECONDS, credential_claim_stamp, deleted_row_expiry, never_auto_start_schedule,
    observed_row_attributes, start_stamp,
)
from backend.tests.handlers.addon.edh.edhRowWriteTestSupport import ConditionalRowTable
from backend.tests.pagingStub import Pager
import backend.backend.handlers.addon.edh.edhWorkstationService as service

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
EDH_UID = "vabcdefghij23456"
BOOTSTRAP = "3f2a9c1e-4b7d-4e0a-9d2b-6c1f8a7e5b30"
CLEARED_KEY = dict(bootstrap.CLEARED_KEY_ATTRIBUTES)
CLEARED_ALL = {**CLEARED_KEY, **bootstrap.CLEARED_SECRET_ATTRIBUTES}
STAMP = "%Y-%m-%dT%H:%M:%SZ"
INSTANCE_ID = "i-0123456789abcdef0"
# The expiry a DELETED row without a credential is given during a sweep at NOW.
EXPIRY = deleted_row_expiry(NOW)
# The updatedAt the scripted conditional write reports it wrote.
STAMP_WRITTEN = NOW.isoformat()
# The updatedAt every scripted row carries, and so what the claim on it is conditioned on.
SEEN = (NOW - timedelta(hours=1)).isoformat()
# The stamp a start whose outcome EDH did not confirm leaves on its row, written with the row: old enough
# for the sweep to take the row up, young enough to still hold a second start off.
START_REQUESTED_AT = (NOW - timedelta(seconds=150)).isoformat()
# The stamp the sweep takes a stopped row's start slot with at NOW: the one fixed width a start's claim is
# written in, to the microsecond.
START_SLOT = NOW.isoformat(timespec="microseconds")
# The stamp every scripted row carries unless a test removes it: its desktop's EDH schedule already set.
SCHEDULE_APPLIED_AT = (NOW - timedelta(hours=2)).isoformat()
# The stamp the sweep writes at NOW onto a row whose desktop it has given the schedule.
SCHEDULE_STAMP = NOW.isoformat()
# The listing's schedule string for a desktop already on the never-auto-start schedule.
NEVER_AUTO_START = json.dumps(never_auto_start_schedule())
# The credential claim the sweep's revoke takes a row with at NOW.
CREDENTIAL_CLAIM = credential_claim_stamp(NOW)
# The context the sweep is run with unless a test gives one: a MagicMock, which reports no numeric
# remaining time.
_MOCK_CONTEXT = object()

BOOTSTRAP_ENV = (
    "EDH_SESSION_BOOTSTRAP_ENABLED", "EDH_SESSION_SECRET_PREFIX", "EDH_SESSION_KEY_TTL_MINUTES",
    "EDH_WORKSTATION_INSTANCE_ROLE_ARNS", "EDH_SESSION_SECRET_KMS_KEY_ARN",
    "API_KEY_SERVICE_LAMBDA_FUNCTION_NAME", "VAMS_BASE_URL",
)
_PACKAGE = "backend.backend.handlers.addon.edh"

# The bootstrap variables as the stack sets them on a reconciler deployed with session bootstrap off: all
# seven are present -- the flag "false", an empty KMS key ARN and an empty role list beside the apiKeyService
# function name, the secret prefix, the key TTL and the base URL as configured -- so nothing can be bound or
# minted while the revoke path through apiKeyService stays open, and a revoke removes a secret without the
# overwrite the key would encrypt.
BOOTSTRAP_OFF_ENV = {
    "EDH_SESSION_BOOTSTRAP_ENABLED": "false",
    "EDH_SESSION_SECRET_PREFIX": "vams/edh-session/",
    "EDH_SESSION_KEY_TTL_MINUTES": "480",
    "EDH_WORKSTATION_INSTANCE_ROLE_ARNS": "[]",
    "EDH_SESSION_SECRET_KMS_KEY_ARN": "",
    "API_KEY_SERVICE_LAMBDA_FUNCTION_NAME": "vams-apiKeyService",
    "VAMS_BASE_URL": "ssm:/vams/web/deployedUrl",
}

SCHEDULE_FAILURES = [
    EdhApiError("Your administrator has disabled virtual desktops schedule update"),
    EdhUnavailableError("read timeout"),
    EdhOutcomeUnknownError("no answer"),
    EdhContractError("unreadable body", status=200),
]

EXPIRED_KEY = (NOW - timedelta(minutes=1)).strftime(STAMP)

REVOKING_IDS = ["expired key", "stopped", "in error", "gone", "deleted with a key"]


def _row(bootstrap_id, **overrides):
    row = {"bootstrapId": bootstrap_id, "sessionId": f"sess-{bootstrap_id}", "userId": "user@example.com",
           "edhUid": EDH_UID, "state": rec.STATE_ACTIVE, "edhState": "running", "connectionAvailable": True,
           "createdAt": (NOW - timedelta(hours=2)).isoformat(), "updatedAt": (NOW - timedelta(hours=1)).isoformat(),
           "apiKeyId": f"key-{bootstrap_id}", "secretName": f"vams/edh-session/{bootstrap_id}",
           "keyExpiresAt": (NOW + timedelta(hours=6)).strftime(STAMP), "scheduleAppliedAt": SCHEDULE_APPLIED_AT}
    row.update(overrides)
    return row


def _creating_row(bootstrap_id, minutes_since_touch, **overrides):
    """A launch the worker has not completed: no session id, last written ``minutes_since_touch`` ago."""
    return _row(bootstrap_id, state=rec.STATE_CREATING, sessionId="", edhState=None, connectionAvailable=False,
                updatedAt=(NOW - timedelta(minutes=minutes_since_touch)).isoformat(), **overrides)


def _desktop(session_id, state="running", **fields):
    """A desktop as EDH's cluster listing records it: without the connection string, which only the owner's
    own listing carries."""
    desktop = {rec.EDH_SESSION_ID_FIELD: session_id, EDH_SESSION_STATE_FIELD: state}
    desktop.update(fields)
    return desktop


# Every way a pass revokes a credential: an expired key on a running desktop, a desktop EDH lists stopped
# or in error, a desktop EDH no longer lists, and a DELETED row that still names a key.
REVOKING_CASES = [
    ({"keyExpiresAt": EXPIRED_KEY}, [_desktop("sess-h")]),
    ({}, [_desktop("sess-h", "stopped")]),
    ({}, [_desktop("sess-h", "error")]),
    ({}, []),
    ({"state": rec.STATE_DELETED}, []),
]


def _named(session_id, owner=EDH_UID, bootstrap_id=BOOTSTRAP, **fields):
    """A desktop EDH lists under the session name a launch's bootstrap id becomes."""
    return _desktop(session_id, **{rec.EDH_SESSION_OWNER_FIELD: owner,
                                   rec.EDH_SESSION_NAME_FIELD: edh_session_name(bootstrap_id), **fields})


def _placed(session_id, instance_id=INSTANCE_ID, **fields):
    """A running desktop with the EC2 instance EDH placed it on."""
    return _desktop(session_id, **{EDH_FIELD_INSTANCE_ID: instance_id, **fields})


def _start_stamped_row(bootstrap_id):
    """A stopped row a start whose outcome EDH did not confirm stamped, credential kept."""
    return _row(bootstrap_id, state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False,
                startRequestedAt=START_REQUESTED_AT, updatedAt=START_REQUESTED_AT)


def _deleted_launch(bootstrap_id, **overrides):
    """A session-less launch its owner deleted: DELETED, no session id, credential already revoked."""
    row = _row(bootstrap_id, state=rec.STATE_DELETED, edhState=None, connectionAvailable=False,
               apiKeyId="", secretName="", keyExpiresAt="")
    row.pop("sessionId")
    row.update(overrides)
    return row


def _index(*desktops):
    return rec._index_desktops(list(desktops))


def _closes_at_end(text):
    depth = 0
    for index, char in enumerate(text):
        depth += (char == "(") - (char == ")")
        if depth == 0:
            return index == len(text) - 1
    return False


def _disjuncts(expression):
    """The OR-joined terms of a built condition expression, nested OR groups flattened."""
    text = expression.strip()
    if text.startswith("(") and _closes_at_end(text):
        text = text[1:-1].strip()
    terms, depth, start = [], 0, 0
    for index, char in enumerate(text):
        depth += (char == "(") - (char == ")")
        if depth == 0 and text.startswith(" OR ", index):
            terms.append(text[start:index])
            start = index + len(" OR ")
    terms.append(text[start:])
    if len(terms) == 1:
        return terms
    return [term for group in terms for term in _disjuncts(group)]


def _default_revoke(event, row, *, delete_secret, actor):
    return dict(CLEARED_ALL if delete_secret else CLEARED_KEY)


def _run(rows, desktops, *, revoke=None, keys=None, delete=None, bootstrap_enabled=True, current=None,
         persist=None, api_key_service="vams-apiKeyService", schedule_answer=None, context=_MOCK_CONTEXT,
         states=None):
    """One sweep with every collaborator replaced. ``keys`` is the session-key listing (a list, or the
    exception the listing raises); ``delete`` scripts the orphan-key deletion; ``current`` maps a bootstrap
    id to the row the consistent re-read returns when it differs from the scanned one (None for a row that
    is gone); ``persist`` scripts the conditional write, answering the updatedAt it wrote or None for a
    lost condition; ``api_key_service`` is the apiKeyService function name the environment carries;
    ``schedule_answer`` is what EDH's schedule update raises; ``context`` is the invocation context;
    ``states`` is what EDH's session-state read answers for unlisted rows (a list, or the exception it raises).
    The persist double carries ``order``, the sequence of persist and revoke calls."""
    client = create_autospec(EdhClient, instance=True)
    client.list_all_desktops.return_value = desktops
    if isinstance(states, Exception):
        client.get_desktop_states.side_effect = states
    elif states is not None:
        client.get_desktop_states.return_value = states
    if schedule_answer is not None:
        client.update_desktop_schedule.side_effect = schedule_answer
    client_factory = MagicMock(return_value=client)
    table = MagicMock()
    half = max(1, len(rows) // 2)
    pager = Pager({"Items": rows[:half], "LastEvaluatedKey": {"bootstrapId": "cursor"}},
                  {"Items": rows[half:]}, name="sessions")
    table.scan.side_effect = pager
    by_id = {row["bootstrapId"]: row for row in rows}
    by_id.update(current or {})

    def get_item(**kwargs):
        found = by_id.get(kwargs["Key"]["bootstrapId"])
        return {"Item": dict(found)} if found is not None else {}

    table.get_item.side_effect = get_item
    order = []

    def recording(name, inner):
        def call(*args, **kwargs):
            order.append(name)
            return inner(*args, **kwargs)
        return call

    revoke_fn = MagicMock(side_effect=recording("revoke", revoke if revoke is not None else _default_revoke))
    list_fn = MagicMock(side_effect=keys) if isinstance(keys, Exception) else MagicMock(return_value=list(keys or []))
    delete_fn = MagicMock(side_effect=delete) if delete is not None else MagicMock(return_value=None)
    persist_fn = MagicMock(side_effect=recording(
        "persist", persist if persist is not None else (lambda *args, **kwargs: STAMP_WRITTEN)))
    persist_fn.order = order
    # Default lease policy for tests
    default_policy = {"enabled": False, "leaseHours": 8, "forced": False, "graceHours": 24,
                     "updatedAt": None, "updatedBy": None}
    with patch.object(rec, "EdhClient", client_factory), \
            patch.object(rec, "session_table", table), \
            patch.object(rec, "read_lease_policy", MagicMock(return_value=default_policy)), \
            patch.object(bootstrap, "session_bootstrap_enabled", bootstrap_enabled), \
            patch.object(bootstrap, "api_key_service_function_name", api_key_service), \
            patch.object(bootstrap, "revoke_session", revoke_fn), \
            patch.object(bootstrap, "list_session_keys", list_fn), \
            patch.object(bootstrap, "delete_orphan_key", delete_fn), \
            patch.object(rec, "_persist", persist_fn), \
            patch.object(rec, "_now", MagicMock(return_value=NOW)):
        summary = rec.lambda_handler({}, MagicMock() if context is _MOCK_CONTEXT else context)
    return summary, revoke_fn, persist_fn, pager, client_factory, delete_fn


def _updates_for(persist, bootstrap_id):
    """The attribute dict of every write onto the row, in call order."""
    return [c.args[1] for c in persist.call_args_list if c.args[0] == bootstrap_id]


def _written_for(persist, bootstrap_id):
    """Every attribute the sweep wrote onto the row, merged in call order."""
    merged = {}
    for updates in _updates_for(persist, bootstrap_id):
        merged.update(updates)
    return merged


def _conditions_for(persist, bootstrap_id):
    """The updatedAt each write onto the row was conditioned on, in call order."""
    return [c.args[2] for c in persist.call_args_list if c.args[0] == bootstrap_id]


def _summary(**counts):
    """The sweep's summary with every counter zero unless given."""
    return {"examined": 0, "updated": 0, "revoked": 0, "scheduled": 0, "deferred": 0, "skipped": 0,
            "failed": 0, "orphanKeysDeleted": 0, **counts}


@pytest.mark.unit
class TestReconcileRow:
    def test_a_running_desktop_with_a_live_key_is_left_alone(self):
        assert rec.reconcile_row(_row("a"), _index(_desktop("sess-a")), NOW) == ({}, False, False)

    def test_a_running_desktop_is_recorded_connectable(self):
        summary, revoke_fn, persist, _pager, _client_factory, _delete = _run(
            [_row("x", connectionAvailable=False)], [_desktop("sess-x")])

        assert _written_for(persist, "x") == {"connectionAvailable": True}
        revoke_fn.assert_not_called()
        assert summary == _summary(examined=1, updated=1)

    def test_a_stopped_desktop_marks_the_row_stopped_and_revokes_keeping_the_secret(self):
        updates, revoke, delete_secret = rec.reconcile_row(_row("a"), _index(_desktop("sess-a", "stopped")), NOW)
        assert updates == {"state": rec.STATE_STOPPED, "edhState": "stopped", "connectionAvailable": False}
        assert (revoke, delete_secret) == (True, False)

    def test_a_pending_desktop_keeps_its_key(self):
        updates, revoke, delete_secret = rec.reconcile_row(_row("a"), _index(_desktop("sess-a", "placing")), NOW)
        assert updates == {"state": rec.STATE_CREATING, "edhState": "placing", "connectionAvailable": False}
        assert (revoke, delete_secret) == (False, False)

    def test_an_unknown_edh_state_keeps_the_stored_state_and_the_key(self):
        updates, revoke, delete_secret = rec.reconcile_row(_row("a"), _index(_desktop("sess-a", "hibernating")), NOW)
        assert updates == {"edhState": "hibernating", "connectionAvailable": False}
        assert (revoke, delete_secret) == (False, False)

    def test_a_desktop_in_error_orphans_the_row_and_removes_its_credential(self):
        updates, revoke, delete_secret = rec.reconcile_row(_row("a"), _index(_desktop("sess-a", "error")), NOW)
        assert updates == {"state": rec.STATE_ORPHANED, "edhState": "error",
                           "lastError": LAST_ERROR_LAUNCH_ERROR, "connectionAvailable": False}
        assert (revoke, delete_secret) == (True, True)

    def test_a_desktop_edh_no_longer_lists_is_recorded_deleted_and_its_secret_removed(self):
        assert rec.reconcile_row(_row("a"), _index(), NOW) == (
            {"state": rec.STATE_DELETED, "connectionAvailable": False}, True, True)

    def test_a_stopped_row_edh_no_longer_lists_is_recorded_deleted_and_its_container_removed(self):
        # A stop took the key and kept the container; the desktop is gone, so the container goes now.
        row = _row("a", apiKeyId="", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False)
        assert rec.reconcile_row(row, _index(), NOW) == ({"state": rec.STATE_DELETED}, True, True)

    def test_a_stopped_row_without_any_credential_edh_no_longer_lists_is_recorded_deleted_with_its_expiry(self):
        row = _row("a", apiKeyId="", secretName="", keyExpiresAt="", state=rec.STATE_STOPPED, edhState="stopped",
                   connectionAvailable=False)
        assert rec.reconcile_row(row, _index(), NOW) == (
            {"state": rec.STATE_DELETED, EXPIRES_AT_FIELD: EXPIRY}, False, False)

    @pytest.mark.parametrize("state, edh_state", [(rec.STATE_ACTIVE, "running"), (rec.STATE_STOPPED, "stopped")])
    def test_only_a_row_recorded_deleted_is_given_the_expiry(self, state, edh_state):
        row = _row("a", state=state, edhState=edh_state, connectionAvailable=edh_state == "running",
                   apiKeyId="", secretName="", keyExpiresAt="")
        listed, _, _ = rec.reconcile_row(row, _index(_desktop("sess-a", edh_state)), NOW)
        assert EXPIRES_AT_FIELD not in listed
        unlisted, _, _ = rec.reconcile_row(row, _index(), NOW)
        assert (unlisted["state"], unlisted[EXPIRES_AT_FIELD]) == (rec.STATE_DELETED, EXPIRY)

    def test_a_row_recorded_deleted_with_a_credential_is_given_no_expiry_yet(self):
        # The expiry follows the credential out: it is written with the cleared attributes once the revoke
        # has succeeded, never beside a key or container that is still there.
        updates, revoke, delete_secret = rec.reconcile_row(_row("a"), _index(), NOW)
        assert EXPIRES_AT_FIELD not in updates
        assert (updates["state"], revoke, delete_secret) == (rec.STATE_DELETED, True, True)

    def test_a_creating_row_inside_the_grace_window_is_left_alone(self):
        assert rec.reconcile_row(_creating_row("a", 5), _index(), NOW) == ({}, False, False)

    def test_a_creating_row_past_the_grace_window_is_orphaned_with_the_timeout_code(self):
        row = _creating_row("a", rec.CREATING_GRACE_MINUTES + 1)
        assert rec.reconcile_row(row, _index(), NOW) == (
            {"state": rec.STATE_ORPHANED, "lastError": LAST_ERROR_CREATE_TIMED_OUT}, True, True)

    def test_a_creating_row_whose_last_attempt_has_timed_out_is_orphaned_inside_the_grace_window(self):
        # The table hands the counter back as a Decimal; the last attempt stamped it as it started and
        # has since run out exactly its timeout without settling the row, so nothing but this sweep will.
        row = _creating_row(
            "a", rec.WORKER_ATTEMPT_SETTLE_SECONDS / 60, launchAttempts=Decimal(rec.MAX_LAUNCH_ATTEMPTS))
        assert rec.reconcile_row(row, _index(), NOW) == (
            {"state": rec.STATE_ORPHANED, "lastError": LAST_ERROR_CREATE_TIMED_OUT}, True, True)

    def test_a_listed_desktop_completes_an_attempts_spent_row_instead_of_orphaning_it(self):
        row = _creating_row(BOOTSTRAP, 10, launchAttempts=Decimal(rec.MAX_LAUNCH_ATTEMPTS))
        desktop = _named("9e8d")
        updates, revoke, delete_secret = rec.reconcile_row(row, _index(desktop), NOW)
        assert updates == observed_row_attributes(row, desktop)
        assert (updates["sessionId"], updates["state"]) == ("9e8d", rec.STATE_ACTIVE)
        assert (revoke, delete_secret) == (False, False)

    def test_a_creating_row_on_its_last_attempt_is_left_alone_while_that_attempt_may_still_run(self):
        # For the worker's timeout after the counter was stamped the launch is still the worker's to
        # complete or orphan; the sweep taking its credential now would leave a running desktop keyless.
        row = _creating_row("a", 2, launchAttempts=Decimal(rec.MAX_LAUNCH_ATTEMPTS))
        assert rec.reconcile_row(row, _index(), NOW) == ({}, False, False)
        # EDH_LAUNCH_WORKER_TIMEOUT in
        # infra/lib/nestedStacks/addon/edhWorkstations/lambdaBuilder/edhWorkstationsFunctions.ts
        assert rec.WORKER_ATTEMPT_SETTLE_SECONDS == 300

    def test_a_creating_row_with_an_attempt_left_waits_out_the_grace_window(self):
        row = _creating_row("a", 5, launchAttempts=rec.MAX_LAUNCH_ATTEMPTS - 1)
        assert rec.reconcile_row(row, _index(), NOW) == ({}, False, False)

    def test_an_unreadable_attempt_counter_reads_as_past_the_window(self):
        # A counter nothing can read is settled rather than failed on every sweep.
        row = _creating_row("a", 5, launchAttempts="garbage")
        assert rec.reconcile_row(row, _index(), NOW) == (
            {"state": rec.STATE_ORPHANED, "lastError": LAST_ERROR_CREATE_TIMED_OUT}, True, True)

    def test_a_creating_row_edh_lists_by_session_name_is_completed(self):
        updates, revoke, delete_secret = rec.reconcile_row(_creating_row(BOOTSTRAP, 5), _index(_named("9e8d")), NOW)
        assert updates == {"sessionId": "9e8d", "state": rec.STATE_ACTIVE, "edhState": "running",
                           "connectionAvailable": True}
        assert (revoke, delete_secret) == (False, False)

    def test_the_newest_same_named_desktop_of_the_owner_completes_the_row(self):
        older = _named("old", **{rec.EDH_SESSION_CREATED_ON_FIELD: "2026-09-08T10:00:00Z"})
        newer = _named("new", **{rec.EDH_SESSION_CREATED_ON_FIELD: "2026-09-08T11:00:00Z"})
        updates, _revoke, _delete = rec.reconcile_row(_creating_row(BOOTSTRAP, 5), _index(older, newer), NOW)
        assert updates["sessionId"] == "new"

    def test_a_same_named_desktop_of_another_owner_does_not_complete_the_row(self):
        row = _creating_row(BOOTSTRAP, rec.CREATING_GRACE_MINUTES + 1)
        updates, revoke, delete_secret = rec.reconcile_row(row, _index(_named("9e8d", owner="vsomeoneelse1234")), NOW)
        assert updates == {"state": rec.STATE_ORPHANED, "lastError": LAST_ERROR_CREATE_TIMED_OUT}
        assert (revoke, delete_secret) == (True, True)

    def test_an_expired_key_on_a_running_desktop_is_revoked_without_a_state_change(self):
        row = _row("a", keyExpiresAt=(NOW - timedelta(minutes=1)).strftime(STAMP))
        assert rec.reconcile_row(row, _index(_desktop("sess-a")), NOW) == ({}, True, False)

    def test_an_unreadable_expiry_counts_as_expired(self):
        assert rec.reconcile_row(_row("a", keyExpiresAt="not-a-date"), _index(_desktop("sess-a")), NOW)[1] is True

    def test_a_listed_stopped_row_with_a_leftover_key_revokes_it_and_keeps_the_secret(self):
        row = _row("a", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False)
        assert rec.reconcile_row(row, _index(_desktop("sess-a", "stopped")), NOW) == ({}, True, False)

    def test_an_unlisted_orphaned_row_with_a_key_is_revoked_and_its_secret_removed(self):
        row = _row("a", state=rec.STATE_ORPHANED, edhState=None, connectionAvailable=False, lastError="QUOTA_EXCEEDED")
        assert rec.reconcile_row(row, _index(), NOW) == ({}, True, True)

    def test_a_deleted_row_with_a_leftover_key_loses_key_and_secret_without_a_state_change(self):
        row = _row("a", state=rec.STATE_DELETED)
        # Whether or not EDH still lists the desktop: VAMS already recorded the deletion.
        assert rec.reconcile_row(row, _index(), NOW) == ({}, True, True)
        assert rec.reconcile_row(row, _index(_desktop("sess-a")), NOW) == ({}, True, True)

    def test_a_deleted_row_with_only_its_container_left_removes_the_container(self):
        assert rec.reconcile_row(_row("a", state=rec.STATE_DELETED, apiKeyId=""), _index(), NOW) == ({}, True, True)

    def test_a_deleted_row_without_key_or_container_takes_the_ttl(self):
        row = _row("a", state=rec.STATE_DELETED, apiKeyId="", secretName="")
        assert rec.reconcile_row(row, _index(), NOW) == ({EXPIRES_AT_FIELD: EXPIRY}, False, False)

    def test_a_deleted_row_that_already_carries_its_expiry_has_nothing_to_do(self):
        row = _row("a", state=rec.STATE_DELETED, apiKeyId="", secretName="", **{EXPIRES_AT_FIELD: EXPIRY})
        assert rec.reconcile_row(row, _index(), NOW) == ({}, False, False)

    def test_a_deleted_session_less_row_whose_desktop_edh_lists_is_re_adopted(self):
        updates, revoke, delete_secret = rec.reconcile_row(_deleted_launch(BOOTSTRAP), _index(_named("9e8d")), NOW)
        assert updates == {"sessionId": "9e8d", "state": rec.STATE_ACTIVE, "edhState": "running",
                           "connectionAvailable": True}
        assert (revoke, delete_secret) == (False, False)

    def test_a_re_adopted_desktop_edh_reports_stopped_reads_stopped(self):
        updates, revoke, delete_secret = rec.reconcile_row(
            _deleted_launch(BOOTSTRAP), _index(_named("9e8d", state="stopped")), NOW)
        assert updates == {"sessionId": "9e8d", "state": rec.STATE_STOPPED, "edhState": "stopped"}
        assert (revoke, delete_secret) == (False, False)

    def test_a_re_adopted_row_that_still_carries_a_key_loses_key_and_secret(self):
        # Re-adoption carries no credential: the owner mints a fresh one by stopping and starting.
        row = _deleted_launch(BOOTSTRAP, apiKeyId="key-left", secretName=f"vams/edh-session/{BOOTSTRAP}")
        updates, revoke, delete_secret = rec.reconcile_row(row, _index(_named("9e8d")), NOW)
        assert updates == {"sessionId": "9e8d", "state": rec.STATE_ACTIVE, "edhState": "running",
                           "connectionAvailable": True}
        assert (revoke, delete_secret) == (True, True)

    def test_a_re_adopted_row_binds_nothing_to_the_desktops_instance(self):
        bind = MagicMock()
        row = _deleted_launch(BOOTSTRAP, apiKeyId="key-left", secretName=f"vams/edh-session/{BOOTSTRAP}")
        placed = _named("9e8d", **{EDH_FIELD_INSTANCE_ID: INSTANCE_ID})
        with patch.object(bootstrap, "bind_session_secret", bind):
            updates, revoke, delete_secret = rec.reconcile_row(row, _index(placed), NOW)
        bind.assert_not_called()
        assert updates["instanceId"] == INSTANCE_ID and "secretBoundInstanceId" not in updates
        assert (revoke, delete_secret) == (True, True)

    def test_a_deleted_row_with_a_session_id_is_not_re_adopted(self):
        row = _row("a", state=rec.STATE_DELETED, apiKeyId="", secretName="", connectionAvailable=False)
        updates, revoke, delete_secret = rec.reconcile_row(row, _index(_desktop("sess-a")), NOW)
        assert updates == {EXPIRES_AT_FIELD: EXPIRY}
        assert (revoke, delete_secret) == (False, False)

    def test_a_re_adopted_row_loses_the_expiry_its_deletion_gave_it(self):
        # Left in place, the table's TTL would remove the row of a desktop that is running again.
        row = _deleted_launch(BOOTSTRAP, **{EXPIRES_AT_FIELD: EXPIRY})
        updates, revoke, delete_secret = rec.reconcile_row(row, _index(_named("9e8d")), NOW)
        assert updates[EXPIRES_AT_FIELD] is None
        assert updates["state"] == rec.STATE_ACTIVE
        assert (revoke, delete_secret) == (False, False)

    def test_a_deleted_session_less_row_edh_does_not_list_keeps_only_its_credential_to_revoke(self):
        row = _deleted_launch(BOOTSTRAP, apiKeyId="key-left")
        assert rec.reconcile_row(row, _index(), NOW) == ({}, True, True)

    def test_a_same_named_desktop_of_another_owner_does_not_re_adopt_a_deleted_row(self):
        row = _deleted_launch(BOOTSTRAP)
        assert rec.reconcile_row(row, _index(_named("9e8d", owner="vsomeoneelse1234")), NOW) == (
            {EXPIRES_AT_FIELD: EXPIRY}, False, False)

    def test_a_start_edh_lists_under_way_clears_the_rows_start_stamp(self):
        row = _start_stamped_row("a")
        updates, revoke, delete_secret = rec.reconcile_row(row, _index(_desktop("sess-a", "pending")), NOW)
        assert updates == {"state": rec.STATE_CREATING, "edhState": "pending", "startRequestedAt": None}
        assert (revoke, delete_secret) == (False, False)

    def test_a_desktop_edh_still_lists_stopped_keeps_the_rows_start_stamp(self):
        row = _start_stamped_row("a")
        updates, _revoke, _delete = rec.reconcile_row(row, _index(_desktop("sess-a", "stopped")), NOW)
        assert "startRequestedAt" not in updates

    @staticmethod
    def _claimed(seconds_ago, **overrides):
        """A stopped row holding a live key under a start's claim ``seconds_ago`` old."""
        return _row("a", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False,
                    startRequestedAt=(NOW - timedelta(seconds=seconds_ago)).isoformat(), **overrides)

    @pytest.mark.parametrize("seconds_ago", [1, 150, 179])
    def test_a_stopped_row_under_a_fresh_start_claim_keeps_its_key_for_the_next_sweep(self, seconds_ago):
        # The start that claimed the row minted this key for the desktop it is starting.
        summary = _summary()
        result = rec.reconcile_row(self._claimed(seconds_ago), _index(_desktop("sess-a", "stopped")), NOW, summary)
        assert result == ({}, False, False)
        assert summary == _summary(deferred=1)

    @pytest.mark.parametrize("seconds_ago", [180, 600])
    def test_a_stopped_row_under_a_claim_past_the_window_is_revoked(self, seconds_ago):
        summary = _summary()
        result = rec.reconcile_row(self._claimed(seconds_ago), _index(_desktop("sess-a", "stopped")), NOW, summary)
        assert result == ({}, True, False)
        assert summary == _summary()

    def test_an_expired_key_under_a_fresh_start_claim_is_still_revoked(self):
        summary = _summary()
        row = self._claimed(30, keyExpiresAt=(NOW - timedelta(minutes=1)).strftime(STAMP))
        assert rec.reconcile_row(row, _index(_desktop("sess-a", "stopped")), NOW, summary) == ({}, True, False)
        assert summary == _summary()

    @pytest.mark.parametrize("desktops, final_state", [([_desktop("sess-a", "error")], rec.STATE_ORPHANED),
                                                       ([], rec.STATE_DELETED)], ids=["in error", "gone"])
    def test_a_desktop_in_error_or_gone_under_a_fresh_start_claim_still_loses_its_credential(
            self, desktops, final_state):
        updates, revoke, delete_secret = rec.reconcile_row(self._claimed(30), _index(*desktops), NOW)
        assert updates["state"] == final_state
        assert (revoke, delete_secret) == (True, True)


@pytest.mark.unit
class TestSecretBinding:
    def _bind(self, error=None):
        if error is not None:
            return MagicMock(side_effect=error)
        return MagicMock(side_effect=lambda row, instance_id: {"secretBoundInstanceId": instance_id})

    def test_a_row_with_a_secret_whose_desktop_is_placed_is_bound_to_its_instance(self):
        bind = self._bind()
        row = _row("a")
        with patch.object(bootstrap, "bind_session_secret", bind):
            result = rec.reconcile_row(row, _index(_placed("sess-a")), NOW)
        assert bind.call_args.args == (row, INSTANCE_ID)
        assert result == ({"instanceId": INSTANCE_ID, "secretBoundInstanceId": INSTANCE_ID}, False, False)

    def test_a_changed_instance_is_bound_again(self):
        bind = self._bind()
        row = _row("a", instanceId="i-0old", secretBoundInstanceId="i-0old")
        with patch.object(bootstrap, "bind_session_secret", bind):
            updates, _revoke, _delete = rec.reconcile_row(row, _index(_placed("sess-a")), NOW)
        assert bind.call_args.args == (row, INSTANCE_ID)
        assert updates == {"instanceId": INSTANCE_ID, "secretBoundInstanceId": INSTANCE_ID}

    def test_a_row_whose_binding_was_cleared_is_bound_again(self):
        bind = self._bind()
        row = _row("a", instanceId=INSTANCE_ID, secretBoundInstanceId="")
        with patch.object(bootstrap, "bind_session_secret", bind):
            updates, _revoke, _delete = rec.reconcile_row(row, _index(_placed("sess-a")), NOW)
        assert bind.call_args.args == (row, INSTANCE_ID)
        assert updates == {"secretBoundInstanceId": INSTANCE_ID}

    def test_a_bound_row_is_not_bound_again(self):
        bind = self._bind()
        row = _row("a", instanceId=INSTANCE_ID, secretBoundInstanceId=INSTANCE_ID)
        with patch.object(bootstrap, "bind_session_secret", bind):
            assert rec.reconcile_row(row, _index(_placed("sess-a")), NOW) == ({}, False, False)
        bind.assert_not_called()

    def test_a_row_without_a_secret_records_the_instance_and_binds_nothing(self):
        bind = self._bind()
        row = _row("a", secretName="")
        with patch.object(bootstrap, "bind_session_secret", bind):
            updates, _revoke, _delete = rec.reconcile_row(row, _index(_placed("sess-a")), NOW)
        bind.assert_not_called()
        assert updates == {"instanceId": INSTANCE_ID}

    def test_a_desktop_without_an_instance_binds_nothing(self):
        bind = self._bind()
        with patch.object(bootstrap, "bind_session_secret", bind):
            assert rec.reconcile_row(_row("a"), _index(_desktop("sess-a")), NOW) == ({}, False, False)
        bind.assert_not_called()

    def test_a_failed_binding_records_the_instance_and_waits_for_the_next_sweep(self):
        bind = self._bind(error=bootstrap.SessionBootstrapError("denied"))
        with patch.object(bootstrap, "bind_session_secret", bind):
            result = rec.reconcile_row(_row("a"), _index(_placed("sess-a")), NOW)
        assert result == ({"instanceId": INSTANCE_ID}, False, False)

    def test_a_failed_binding_is_counted_as_failed_by_the_sweep(self):
        # A sweep whose every binding fails must not report a clean run: the desktop starts without
        # its session credential until a binding succeeds.
        bind = self._bind(error=bootstrap.SessionBootstrapError("denied"))
        with patch.object(bootstrap, "bind_session_secret", bind):
            summary, _revoke, persist, *_ = _run([_row("a")], [_placed("sess-a")])
        assert summary == _summary(examined=1, updated=1, failed=1)
        assert _written_for(persist, "a")["instanceId"] == INSTANCE_ID


@pytest.mark.unit
class TestTheSweep:
    def test_pages_the_table_to_exhaustion_and_reconciles_every_row(self):
        rows = [_row("a"), _row("b"), _row("c"), _row("d"), _row("e", state=rec.STATE_DELETED)]
        desktops = [_desktop("sess-a"), _desktop("sess-b", "stopped"), _desktop("sess-d")]
        summary, revoke_fn, persist, pager, client_factory, delete_fn = _run(rows, desktops)
        pager.assert_paged_to_exhaustion()
        assert client_factory.call_args.kwargs == {"profile": "batch"}
        assert client_factory.return_value.list_all_desktops.call_args.kwargs == {"edh_uid": EDH_SERVICE_ACTOR_UID}
        delete_fn.assert_not_called()
        assert summary == _summary(examined=5, updated=3, revoked=3)
        revoked = {c.args[1]["bootstrapId"]: c.kwargs["delete_secret"] for c in revoke_fn.call_args_list}
        assert revoked == {"b": False, "c": True, "e": True}
        assert _written_for(persist, "b") == {"state": rec.STATE_STOPPED, "edhState": "stopped",
                                              "connectionAvailable": False, **CLEARED_KEY,
                                              "startRequestedAt": None, "credentialClaimedAt": None}
        assert _written_for(persist, "c") == {"state": rec.STATE_DELETED, "connectionAvailable": False,
                                              **CLEARED_ALL, EXPIRES_AT_FIELD: EXPIRY, "credentialClaimedAt": None}
        assert _written_for(persist, "e") == {**CLEARED_ALL, EXPIRES_AT_FIELD: EXPIRY, "credentialClaimedAt": None}
        assert _updates_for(persist, "a") == []
        assert _updates_for(persist, "d") == []

    def test_the_row_is_claimed_before_its_credential_is_revoked_and_cleared_under_the_claims_stamp(self):
        summary, revoke_fn, persist, *_ = _run([_row("b")], [_desktop("sess-b", "stopped")])
        assert persist.order == ["persist", "revoke", "persist"]
        claim, clear = _updates_for(persist, "b")
        # The claim takes the row's start slot and its credential claim as well, so neither a start nor a
        # renew mints a key while the revoke takes the stored one back; the clear gives both up.
        assert claim == {"state": rec.STATE_STOPPED, "edhState": "stopped", "connectionAvailable": False,
                         "startRequestedAt": START_SLOT, "credentialClaimedAt": CREDENTIAL_CLAIM}
        assert clear == {**CLEARED_KEY, "startRequestedAt": None, "credentialClaimedAt": None}
        # The claim is conditioned on the updatedAt the re-read returned; the clear on the claim's own stamp,
        # so a start that wrote a fresh key in between keeps it.
        assert _conditions_for(persist, "b") == [SEEN, STAMP_WRITTEN]
        assert summary == _summary(examined=1, updated=1, revoked=1)

    def test_a_row_whose_only_change_is_its_credential_is_still_claimed_first(self):
        # An expired key on a running desktop: no state to write, but the claim (the credential claim and an
        # updatedAt bump) still precedes the revoke so a concurrent writer is detected.
        row = _row("a", keyExpiresAt=(NOW - timedelta(minutes=1)).strftime(STAMP))
        summary, revoke_fn, persist, *_ = _run([row], [_desktop("sess-a")])
        assert persist.order == ["persist", "revoke", "persist"]
        assert _updates_for(persist, "a") == [{"credentialClaimedAt": CREDENTIAL_CLAIM},
                                              {**CLEARED_KEY, "credentialClaimedAt": None}]
        assert summary == _summary(examined=1, updated=1, revoked=1)

    def test_a_row_the_desktop_left_gets_its_expiry_with_the_cleared_credential(self):
        _summary_, _revoke_fn, persist, *_ = _run([_row("c")], [])
        claim, clear = _updates_for(persist, "c")
        assert EXPIRES_AT_FIELD not in claim
        assert clear == {**CLEARED_ALL, EXPIRES_AT_FIELD: EXPIRY, "credentialClaimedAt": None}
        assert isinstance(clear[EXPIRES_AT_FIELD], int)

    def test_a_stopped_desktop_keeps_its_container_and_gets_no_expiry(self):
        _summary_, _revoke_fn, persist, *_ = _run([_row("b")], [_desktop("sess-b", "stopped")])
        assert EXPIRES_AT_FIELD not in _written_for(persist, "b")

    def test_a_start_edh_took_has_its_stamp_cleared_and_its_credential_kept(self):
        # The start's outcome went unconfirmed and the desktop is running now: the stamp would still hold
        # its owner's next start off, and the key it kept is the one the desktop signs in with.
        summary, revoke_fn, persist, *_ = _run([_start_stamped_row("a")], [_desktop("sess-a", "running")])
        assert _written_for(persist, "a") == {"state": rec.STATE_ACTIVE, "edhState": "running",
                                              "connectionAvailable": True, "startRequestedAt": None}
        assert _conditions_for(persist, "a") == [START_REQUESTED_AT]
        revoke_fn.assert_not_called()
        assert summary == _summary(examined=1, updated=1)

    def test_a_stopped_rows_key_under_a_fresh_start_claim_is_left_to_the_next_sweep(self):
        # A start whose outcome EDH did not confirm, taken up past the write grace but inside the start's
        # window: the desktop may yet start, and it signs in with the key that start minted.
        summary, revoke_fn, persist, *_ = _run([_start_stamped_row("a")], [_desktop("sess-a", "stopped")])
        revoke_fn.assert_not_called()
        persist.assert_not_called()
        assert summary == _summary(examined=1, deferred=1)

    def test_a_stopped_rows_key_under_a_claim_past_the_window_is_revoked(self):
        stale = (NOW - timedelta(seconds=START_IN_PROGRESS_SECONDS + 20)).isoformat()
        row = _row("a", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False,
                   startRequestedAt=stale, updatedAt=stale)
        summary, revoke_fn, _persist, *_ = _run([row], [_desktop("sess-a", "stopped")])
        assert {c.args[1]["bootstrapId"]: c.kwargs["delete_secret"] for c in revoke_fn.call_args_list} == {"a": False}
        assert summary == _summary(examined=1, updated=1, revoked=1)

    def test_a_start_racing_the_revoke_of_a_stopped_rows_key_is_refused_until_the_key_is_cleared(
            self, monkeypatch):
        # EDH stopped the desktop and left its key. While the sweep takes that key back, a start that read
        # the row before the sweep's claim loses its own claim, and a start that reads the row now finds a
        # start in progress: neither mints a key for the revoke's overwrite of the secret to disown. The sweep
        # began minutes before it reached the row, so the stamp is the instant of the claim.
        now = datetime.now(timezone.utc)
        sweep_began = now - timedelta(minutes=5)
        row = _row("a", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False,
                   updatedAt=(now - timedelta(hours=1)).isoformat())
        table = ConditionalRowTable(**row)
        sweep_table, start_table = MagicMock(), MagicMock()
        sweep_table.get_item.side_effect = lambda **kwargs: {"Item": dict(table.item)}
        sweep_table.update_item.side_effect = table
        start_table.update_item.side_effect = table
        start_table.get_item.side_effect = table.read
        during = {}

        def revoke(event, revoked_row, *, delete_secret, actor):
            during["a start that read the row first claims it"] = service._claim_start(dict(row))
            during["a start that reads it now finds one in progress"] = service.start_in_progress(dict(table.item))
            return dict(CLEARED_KEY)

        monkeypatch.setattr(rec, "session_table", sweep_table)
        monkeypatch.setattr(rec, "_now", lambda: now)
        monkeypatch.setattr(service, "edh_session_table", start_table)
        monkeypatch.setattr(bootstrap, "revoke_session", revoke)
        monkeypatch.setattr(bootstrap, "api_key_service_function_name", "vams-apiKeyService")
        summary = _summary()

        rec._reconcile({"bootstrapId": "a"}, _index(_desktop("sess-a", "stopped")), sweep_began, summary)

        assert during == {"a start that read the row first claims it": False,
                          "a start that reads it now finds one in progress": True}
        # The clear gave the slot up with the key; the row is counted once, as the credential write.
        assert "startRequestedAt" not in table.item
        assert table.item["apiKeyId"] == CLEARED_KEY["apiKeyId"]
        assert summary == _summary(updated=1, revoked=1)
        # Once the key is cleared, a start claims the row as any start does.
        assert service._claim_start(dict(table.item)) is True

    def test_a_revoke_of_an_expired_key_under_a_fresh_start_claim_leaves_that_claim_in_place(self):
        # The start that claimed the row keeps its claim: the sweep neither takes the slot over nor gives
        # it up with the key.
        row = _start_stamped_row("a")
        row["keyExpiresAt"] = (NOW - timedelta(minutes=1)).strftime(STAMP)
        summary, revoke_fn, persist, *_ = _run([row], [_desktop("sess-a", "stopped")])
        assert {c.args[1]["bootstrapId"] for c in revoke_fn.call_args_list} == {"a"}
        assert _written_for(persist, "a") == {**CLEARED_KEY, "credentialClaimedAt": None}
        assert summary == _summary(examined=1, updated=1, revoked=1)

    def test_a_re_adopted_row_is_persisted_with_its_credential_cleared(self):
        row = _deleted_launch(BOOTSTRAP, apiKeyId="key-left", secretName=f"vams/edh-session/{BOOTSTRAP}")
        summary, revoke_fn, persist, *_ = _run([row], [_named("9e8d")])
        assert revoke_fn.call_args.kwargs["delete_secret"] is True
        assert _written_for(persist, BOOTSTRAP) == {"sessionId": "9e8d", "state": rec.STATE_ACTIVE, "edhState": "running",
                                                    "connectionAvailable": True, **CLEARED_ALL,
                                                    "credentialClaimedAt": None}
        assert summary["revoked"] == 1

    def test_a_row_written_inside_the_grace_is_left_to_the_request_that_wrote_it(self):
        # A start writes its fresh key onto the row before EDH is asked to start the desktop; a sweep that
        # read the row in between would revoke that key. The row is that request's for the grace.
        row = _row("a", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False,
                   updatedAt=(NOW - timedelta(seconds=30)).isoformat())
        summary, revoke_fn, persist, *_ = _run([row], [_desktop("sess-a", "stopped")])
        revoke_fn.assert_not_called()
        persist.assert_not_called()
        assert summary == _summary(examined=1, deferred=1)
        assert rec.RECENT_WRITE_GRACE_SECONDS == 120

    def test_a_row_written_after_the_sweep_began_is_deferred(self):
        row = _row("a", updatedAt=(NOW + timedelta(seconds=5)).isoformat())
        summary, revoke_fn, persist, *_ = _run([row], [_desktop("sess-a", "stopped")])
        revoke_fn.assert_not_called()
        persist.assert_not_called()
        assert summary == _summary(examined=1, deferred=1)

    def test_a_deferred_rows_key_is_still_kept_from_the_orphan_sweep(self):
        row = _row("a", updatedAt=(NOW - timedelta(seconds=30)).isoformat())
        aged = (NOW - timedelta(hours=2)).isoformat()
        keys = [{"apiKeyId": "key-a", "apiKeyName": "temporary-session-edh-a", "userId": "user@example.com",
                 "expiresAt": (NOW - timedelta(hours=1)).strftime(STAMP), "createdAt": aged}]
        _summary_, _revoke_fn, _persist, _pager, _client_factory, delete_fn = _run(
            [row], [_desktop("sess-a", "stopped")], keys=keys)
        delete_fn.assert_not_called()

    def test_the_decision_is_made_on_the_re_read_row_not_the_scanned_copy(self):
        # The scan handed back a STOPPED row still naming its old key; by the time the row is examined a
        # start has written a fresh key and ACTIVE, and EDH lists the desktop running.
        scanned = _row("a", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False)
        current = _row("a", apiKeyId="key-fresh")
        summary, revoke_fn, persist, *_ = _run([scanned], [_desktop("sess-a")], current={"a": current})
        revoke_fn.assert_not_called()
        persist.assert_not_called()
        assert summary == _summary(examined=1)

    def test_a_row_gone_since_the_scan_is_skipped(self):
        # Nothing will find the row next sweep either, so it is not counted among the deferred.
        summary, revoke_fn, persist, *_ = _run([_row("a")], [], current={"a": None})
        revoke_fn.assert_not_called()
        persist.assert_not_called()
        assert summary == _summary(examined=1, skipped=1)

    def test_the_re_read_is_a_consistent_read(self):
        table = MagicMock()
        table.get_item.return_value = {"Item": {"bootstrapId": "a"}}
        with patch.object(rec, "session_table", table):
            assert rec._read_row("a") == {"bootstrapId": "a"}
        assert table.get_item.call_args.kwargs == {"Key": {"bootstrapId": "a"}, "ConsistentRead": True}

    def test_a_lost_claim_defers_the_row_without_touching_its_credential(self):
        summary, revoke_fn, persist, *_ = _run(
            [_row("a")], [_desktop("sess-a", "stopped")], persist=lambda *args, **kwargs: None)
        revoke_fn.assert_not_called()
        assert persist.order == ["persist"]
        assert summary == _summary(examined=1, deferred=1)

    def test_a_lost_clear_after_the_revoke_leaves_the_credential_attributes_for_the_next_sweep(self):
        answers = iter([STAMP_WRITTEN, None])
        summary, revoke_fn, persist, *_ = _run(
            [_row("a")], [_desktop("sess-a", "stopped")], persist=lambda *args, **kwargs: next(answers))
        assert {c.args[1]["bootstrapId"] for c in revoke_fn.call_args_list} == {"a"}
        assert persist.order == ["persist", "revoke", "persist"]
        assert summary == _summary(examined=1, updated=1, revoked=1, deferred=1)

    def test_the_scan_selects_the_swept_states_keyed_expiry_less_reclaimable_and_held_deleted_rows(self):
        built = ConditionExpressionBuilder().build_expression(rec._swept_rows_condition(NOW))
        names, values = built.attribute_name_placeholders, built.attribute_value_placeholders
        cutoff = (NOW - timedelta(hours=rec.DELETED_RECLAIM_WINDOW_HOURS)).isoformat()
        claim_cutoff = credential_claim_stamp(NOW - timedelta(seconds=CREDENTIAL_CLAIM_SECONDS))
        start_cutoff = start_stamp(NOW - timedelta(seconds=START_IN_PROGRESS_SECONDS))
        assert set(names.values()) == {"state", "apiKeyId", "secretName", "sessionId", "updatedAt",
                                       EXPIRES_AT_FIELD, "credentialClaimedAt", "startRequestedAt"}
        assert set(values.values()) == {*rec.SWEPT_STATES, rec.STATE_DELETED, "", cutoff, claim_cutoff, start_cutoff}
        assert set(rec.SWEPT_STATES) == {"CREATING", "ACTIVE", "STOPPED", "ORPHANED"}
        assert rec.DELETED_RECLAIM_WINDOW_HOURS == 24

        # The boolean structure: seven OR-joined terms, the DELETED value conjoined with the key, with the
        # secret container, with the absent expiry, with the reclaim window, and with a fresh credential claim
        # and a fresh start respectively, never selected on its own.
        terms = _disjuncts(built.condition_expression)
        assert len(terms) == 7
        deleted = {placeholder for placeholder, value in values.items() if value == rec.STATE_DELETED}
        (api_key,) = [placeholder for placeholder, name in names.items() if name == "apiKeyId"]
        (secret_name,) = [placeholder for placeholder, name in names.items() if name == "secretName"]
        (session_id,) = [placeholder for placeholder, name in names.items() if name == "sessionId"]
        (updated_at,) = [placeholder for placeholder, name in names.items() if name == "updatedAt"]
        (expires_at,) = [placeholder for placeholder, name in names.items() if name == EXPIRES_AT_FIELD]
        (claimed_at,) = [placeholder for placeholder, name in names.items() if name == "credentialClaimedAt"]
        (started_at,) = [placeholder for placeholder, name in names.items() if name == "startRequestedAt"]
        (swept_term,) = [term for term in terms if " IN " in term]
        (keyed_term,) = [term for term in terms if api_key in term]
        (container_term,) = [term for term in terms if secret_name in term]
        (expiry_term,) = [term for term in terms if expires_at in term]
        (reclaim_term,) = [term for term in terms if updated_at in term]
        (claim_term,) = [term for term in terms if claimed_at in term]
        (start_term,) = [term for term in terms if started_at in term]
        assert deleted.isdisjoint(re.findall(r":v\d+", swept_term))
        assert deleted & set(re.findall(r":v\d+", keyed_term)) and " AND " in keyed_term and "<>" in keyed_term
        assert deleted & set(re.findall(r":v\d+", container_term)) and " AND " in container_term and "<>" in container_term
        assert deleted & set(re.findall(r":v\d+", expiry_term)) and " AND " in expiry_term
        assert f"attribute_not_exists({expires_at})" in expiry_term
        assert deleted & set(re.findall(r":v\d+", reclaim_term)) and " AND " in reclaim_term
        assert f"attribute_not_exists({session_id})" in reclaim_term and f"{updated_at} > " in reclaim_term
        # A DELETED row a renew or a start still holds may have a desktop running on a key of its bootstrap.
        assert deleted & set(re.findall(r":v\d+", claim_term)) and " AND " in claim_term
        assert f"{claimed_at} > " in claim_term
        assert deleted & set(re.findall(r":v\d+", start_term)) and " AND " in start_term
        assert f"{started_at} > " in start_term

    def test_the_reclaim_cutoff_is_written_in_the_shape_the_rows_updated_at_is(self):
        # DynamoDB compares the strings, so the cutoff must sort against what _persist writes.
        built = ConditionExpressionBuilder().build_expression(rec._swept_rows_condition(NOW))
        (updated_at,) = [placeholder for placeholder, name in built.attribute_name_placeholders.items()
                         if name == "updatedAt"]
        (placeholder,) = re.findall(rf"{updated_at} > (:v\d+)", built.condition_expression)
        cutoff = built.attribute_value_placeholders[placeholder]
        assert cutoff == (NOW - timedelta(hours=24)).isoformat()
        assert cutoff < NOW.isoformat()

    def test_the_claim_and_start_cutoffs_are_written_in_the_shape_their_stamps_are(self):
        # A credential claim and a start stamp are both written to the microsecond with an offset, and
        # DynamoDB compares the strings.
        built = ConditionExpressionBuilder().build_expression(rec._swept_rows_condition(NOW))
        cutoffs = {}
        for attribute in ("credentialClaimedAt", "startRequestedAt"):
            (name,) = [placeholder for placeholder, named in built.attribute_name_placeholders.items()
                       if named == attribute]
            (placeholder,) = re.findall(rf"{name} > (:v\d+)", built.condition_expression)
            cutoffs[attribute] = built.attribute_value_placeholders[placeholder]
        assert cutoffs == {
            "credentialClaimedAt": credential_claim_stamp(NOW - timedelta(seconds=CREDENTIAL_CLAIM_SECONDS)),
            "startRequestedAt": start_stamp(NOW - timedelta(seconds=START_IN_PROGRESS_SECONDS)),
        }
        assert cutoffs["credentialClaimedAt"] < credential_claim_stamp(NOW - timedelta(seconds=30))
        assert cutoffs["startRequestedAt"] < START_REQUESTED_AT

    def test_the_sweep_acts_as_the_system_user(self):
        _summary, revoke_fn, *_ = _run([_row("a")], [_desktop("sess-a", "stopped")])
        assert revoke_fn.call_args.args[0] == {"lambdaCrossCall": {"userName": rec.SYSTEM_USER_ID}}
        assert revoke_fn.call_args.kwargs["actor"] == rec.SYSTEM_USER_ID
        assert rec.SYSTEM_USER_ID == "SYSTEM_USER"

    def test_a_failing_row_does_not_stop_the_sweep(self):
        def revoke(_event, row, *, delete_secret, actor):
            if row["bootstrapId"] == "a":
                raise bootstrap.SessionBootstrapError("down")
            return dict(CLEARED_KEY)

        rows = [_row("a"), _row("b")]
        desktops = [_desktop("sess-a", "stopped"), _desktop("sess-b", "stopped")]
        summary, _revoke_fn, persist, *_ = _run(rows, desktops, revoke=revoke)
        assert summary == _summary(examined=2, updated=2, revoked=1, failed=1)
        # Row a's state was claimed before its revoke failed; its credential attributes stay for the next
        # sweep, which finds the row through the keyed DELETED/STOPPED filter again, and the start slot and
        # credential claim its claim took are given up by writes of their own, each conditioned on the stamp
        # the sweep wrote.
        assert _written_for(persist, "a") == {"state": rec.STATE_STOPPED, "edhState": "stopped",
                                              "connectionAvailable": False, "startRequestedAt": START_SLOT,
                                              "credentialClaimedAt": CREDENTIAL_CLAIM}
        assert _written_for(persist, "b") == {"state": rec.STATE_STOPPED, "edhState": "stopped",
                                              "connectionAvailable": False, **CLEARED_KEY,
                                              "startRequestedAt": None, "credentialClaimedAt": None}

    def test_an_edh_listing_failure_skips_the_sweep_and_touches_no_row(self):
        client = create_autospec(EdhClient, instance=True)
        client.list_all_desktops.side_effect = EdhUnavailableError("connect timeout")
        table = MagicMock()
        default_policy = {"enabled": False, "leaseHours": 8, "forced": False, "graceHours": 24,
                         "updatedAt": None, "updatedBy": None}
        with patch.object(rec, "EdhClient", MagicMock(return_value=client)), \
                patch.object(rec, "session_table", table), \
                patch.object(rec, "read_lease_policy", MagicMock(return_value=default_policy)):
            with pytest.raises(EdhUnavailableError):
                rec.lambda_handler({}, MagicMock())
        table.scan.assert_not_called()
        table.update_item.assert_not_called()


@pytest.mark.unit
class TestCredentialWorkWithoutSessionBootstrap:
    """A deployment without session bootstrap still runs the sweep for its state, and keeps the grants to
    take back a credential left from a period with the feature on: a row that names a key or holds a
    secret container has it revoked or removed while the environment names the apiKeyService function
    the revoke runs through. Credential work the sweep cannot do is counted skipped."""

    def test_a_row_with_only_a_container_has_its_container_removed_while_an_apikeyservice_is_named(self):
        row = _row("a", apiKeyId="", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False)
        summary, revoke_fn, persist, *_ = _run([row], [], bootstrap_enabled=False)
        assert revoke_fn.call_args.kwargs["delete_secret"] is True
        assert _written_for(persist, "a") == {"state": rec.STATE_DELETED, **CLEARED_ALL, EXPIRES_AT_FIELD: EXPIRY,
                                              "credentialClaimedAt": None}
        assert summary["skipped"] == 0 and summary["updated"] == 1

    def test_a_row_with_only_a_container_is_recorded_deleted_and_its_container_left_when_no_apikeyservice_is_named(self):
        row = _row("a", apiKeyId="", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False)
        summary, revoke_fn, persist, *_ = _run([row], [], bootstrap_enabled=False, api_key_service="")
        revoke_fn.assert_not_called()
        assert _written_for(persist, "a") == {"state": rec.STATE_DELETED}
        assert summary == _summary(examined=1, updated=1, skipped=1)

    def test_a_row_that_names_a_key_is_still_revoked_while_an_apikeyservice_is_named(self):
        _summary_, revoke_fn, persist, *_ = _run([_row("a")], [_desktop("sess-a", "stopped")],
                                                bootstrap_enabled=False, api_key_service="vams-apiKeyService")
        assert {c.args[1]["bootstrapId"] for c in revoke_fn.call_args_list} == {"a"}
        assert _written_for(persist, "a") == {"state": rec.STATE_STOPPED, "edhState": "stopped",
                                              "connectionAvailable": False, **CLEARED_KEY,
                                              "startRequestedAt": None, "credentialClaimedAt": None}

    @pytest.mark.parametrize("bootstrap_enabled", [False, True], ids=["bootstrap off", "bootstrap on"])
    def test_a_keyed_row_is_left_unclaimed_and_skipped_when_no_apikeyservice_is_named(self, bootstrap_enabled):
        # A stopped desktop still listed stopped, with its key still recorded: the revoke is the row's
        # only work. Without a function to send it to, the row is not claimed either -- a claim followed
        # by a revoke that cannot be sent would bump updatedAt every sweep and fail every sweep.
        row = _row("a", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False)
        summary, revoke_fn, persist, *_ = _run([row], [_desktop("sess-a", "stopped")],
                                               bootstrap_enabled=bootstrap_enabled, api_key_service="")
        revoke_fn.assert_not_called()
        persist.assert_not_called()
        assert summary == _summary(examined=1, skipped=1)

    def test_state_is_still_reconciled_for_a_keyed_row_whose_credential_cannot_be_revoked(self):
        summary, revoke_fn, persist, *_ = _run([_row("a")], [], bootstrap_enabled=False, api_key_service="")
        revoke_fn.assert_not_called()
        assert _written_for(persist, "a") == {"state": rec.STATE_DELETED, "connectionAvailable": False}
        assert summary == _summary(examined=1, updated=1, skipped=1)

    def test_with_the_feature_on_the_container_alone_is_removed(self):
        """Positive control for the first case: the same row under a deployment with the feature."""
        row = _row("a", apiKeyId="", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False)
        _summary_, revoke_fn, persist, *_ = _run([row], [], bootstrap_enabled=True)
        assert revoke_fn.call_args.kwargs["delete_secret"] is True
        assert _written_for(persist, "a") == {"state": rec.STATE_DELETED, **CLEARED_ALL, EXPIRES_AT_FIELD: EXPIRY,
                                              "credentialClaimedAt": None}

    def test_the_orphan_key_sweep_waits_for_the_feature(self):
        keys = [{"apiKeyId": "lost-1", "apiKeyName": "temporary-session-edh-lost-1", "userId": "user@example.com",
                 "expiresAt": (NOW - timedelta(hours=1)).strftime(STAMP), "createdAt": (NOW - timedelta(hours=2)).isoformat()}]
        summary, _revoke_fn, _persist, _pager, _client_factory, delete_fn = _run([], [], keys=keys, bootstrap_enabled=False)
        delete_fn.assert_not_called()
        assert summary == _summary()


def _load_fresh(name, source, unique_suffix):
    """An independent copy of an EDH module loaded by file path, named inside the real package that holds
    the file (the add-on package, or its common subpackage for a library module) so its relative sibling
    imports resolve; the boto3 factories are doubled."""
    package = f"{_PACKAGE}.common" if Path(source).parent.name == "common" else _PACKAGE
    spec = importlib.util.spec_from_file_location(f"{package}.{name}_under_test_{unique_suffix}", source)
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", return_value=MagicMock()), patch("boto3.resource", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module


def _fresh_reconciler(unique_suffix):
    """Independent copies of the bootstrap module and of a reconciler bound to it, loaded in the current
    environment."""
    fresh_bootstrap = _load_fresh("edhSessionBootstrap", bootstrap.__file__, unique_suffix)
    with patch.dict(sys.modules, {f"{_PACKAGE}.common.edhSessionBootstrap": fresh_bootstrap}), \
            patch("backend.backend.handlers.addon.edh.common.edhSessionBootstrap", fresh_bootstrap):
        fresh = _load_fresh("edhSessionReconciler", rec.__file__, unique_suffix)
    assert fresh.bootstrap is fresh_bootstrap
    return fresh, fresh_bootstrap


def _sweep_a_stopped_row_edh_no_longer_lists(fresh, fresh_bootstrap):
    """One sweep of the fresh reconciler over a credential-less STOPPED row EDH does not list; returns the
    summary, the revoke and key-listing doubles and the table."""
    row = _row("a", apiKeyId="", secretName="", keyExpiresAt="", state=rec.STATE_STOPPED, edhState="stopped",
               connectionAvailable=False)
    client = create_autospec(EdhClient, instance=True)
    client.list_all_desktops.return_value = []
    table = MagicMock()
    table.scan.return_value = {"Items": [row]}
    table.get_item.return_value = {"Item": dict(row)}
    revoke, listing = MagicMock(), MagicMock(return_value=[])
    default_policy = {"enabled": False, "leaseHours": 8, "forced": False, "graceHours": 24,
                     "updatedAt": None, "updatedBy": None}
    with patch.object(fresh, "EdhClient", MagicMock(return_value=client)), \
            patch.object(fresh, "session_table", table), \
            patch.object(fresh, "read_lease_policy", MagicMock(return_value=default_policy)), \
            patch.object(fresh_bootstrap, "revoke_session", revoke), \
            patch.object(fresh_bootstrap, "list_session_keys", listing), \
            patch.object(fresh, "_now", MagicMock(return_value=NOW)):
        summary = fresh.lambda_handler({}, MagicMock())
    return summary, revoke, listing, table


def _assert_recorded_deleted_without_credential_work(summary, revoke, listing, table):
    assert summary == _summary(examined=1, updated=1)
    revoke.assert_not_called()
    listing.assert_not_called()
    written = table.update_item.call_args.kwargs
    assert written["Key"] == {"bootstrapId": "a"}
    (state_placeholder,) = [p for p, name in written["ExpressionAttributeNames"].items() if name == "state"]
    assert written["ExpressionAttributeValues"][state_placeholder.replace("#a", ":v")] == rec.STATE_DELETED
    assert "ConditionExpression" in written


@pytest.mark.unit
class TestModuleLoadWithoutTheBootstrapEnvironment:
    def test_the_reconciler_starts_and_sweeps_state_with_the_bootstrap_environment_absent(self, monkeypatch):
        # The reconciler is deployed whenever the add-on is; the session-bootstrap variables come with the
        # feature. Without them the module must still load and record a desktop EDH no longer lists.
        for name in BOOTSTRAP_ENV:
            monkeypatch.delenv(name, raising=False)
        fresh, fresh_bootstrap = _fresh_reconciler("no_env")
        assert fresh_bootstrap.is_enabled() is False

        _assert_recorded_deleted_without_credential_work(*_sweep_a_stopped_row_edh_no_longer_lists(fresh, fresh_bootstrap))

    def test_the_reconciler_sweeps_state_with_the_variables_a_bootstrap_off_deployment_sets(self, monkeypatch):
        # The stack sets all seven variables with bootstrap off: the flag "false", an empty KMS key ARN and an
        # empty role list, and the apiKeyService name, secret prefix, key TTL and base URL as configured. The
        # module reads that as the feature off with nothing to bind or mint, and the name keeps the revoke
        # path open for a row that still carries a key.
        for name in BOOTSTRAP_ENV:
            monkeypatch.delenv(name, raising=False)
        for name, value in BOOTSTRAP_OFF_ENV.items():
            monkeypatch.setenv(name, value)
        fresh, fresh_bootstrap = _fresh_reconciler("off_env")
        assert fresh_bootstrap.is_enabled() is False
        assert fresh_bootstrap.workstation_instance_role_arns == []
        assert fresh_bootstrap.session_secret_kms_key_arn == ""
        assert fresh_bootstrap.api_key_service_function_name == BOOTSTRAP_OFF_ENV["API_KEY_SERVICE_LAMBDA_FUNCTION_NAME"]

        _assert_recorded_deleted_without_credential_work(*_sweep_a_stopped_row_edh_no_longer_lists(fresh, fresh_bootstrap))

    def test_a_keyed_row_edh_no_longer_lists_is_revoked_and_its_secret_removed_without_an_overwrite_with_bootstrap_off(
            self, monkeypatch):
        # Through the real revoke path with the variables a bootstrap-off deployment sets: the key is deleted
        # through apiKeyService, the secret is force-deleted without the overwrite (the session-secrets key
        # left the stack with the feature), and the row is recorded DELETED, its credential cleared and its
        # expiry stamped.
        for name in BOOTSTRAP_ENV:
            monkeypatch.delenv(name, raising=False)
        for name, value in BOOTSTRAP_OFF_ENV.items():
            monkeypatch.setenv(name, value)
        fresh, fresh_bootstrap = _fresh_reconciler("off_env_keyed")
        row = _row("a", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False)
        client = create_autospec(EdhClient, instance=True)
        client.list_all_desktops.return_value = []
        table = MagicMock()
        table.scan.return_value = {"Items": [row]}
        table.get_item.return_value = {"Item": dict(row)}
        fresh_bootstrap.lambda_client.invoke.return_value = {"Payload": io.BytesIO(
            json.dumps({"statusCode": 200, "body": json.dumps({"message": "deleted"})}).encode("utf-8"))}
        default_policy = {"enabled": False, "leaseHours": 8, "forced": False, "graceHours": 24,
                         "updatedAt": None, "updatedBy": None}
        with patch.object(fresh, "EdhClient", MagicMock(return_value=client)), \
                patch.object(fresh, "session_table", table), \
                patch.object(fresh, "read_lease_policy", MagicMock(return_value=default_policy)), \
                patch.object(fresh_bootstrap, "list_session_keys", MagicMock(return_value=[])), \
                patch.object(fresh_bootstrap, "log_auth_changes", MagicMock()), \
                patch.object(fresh, "_now", MagicMock(return_value=NOW)):
            summary = fresh.lambda_handler({}, MagicMock())
        assert summary == _summary(examined=1, updated=1, revoked=1)
        assert fresh_bootstrap.lambda_client.invoke.called
        assert fresh_bootstrap.lambda_client.invoke.call_count <= 1
        assert fresh_bootstrap.lambda_client.invoke.call_args.kwargs["FunctionName"] == \
            BOOTSTRAP_OFF_ENV["API_KEY_SERVICE_LAMBDA_FUNCTION_NAME"]
        fresh_bootstrap.secretsmanager_client.put_secret_value.assert_not_called()
        assert fresh_bootstrap.secretsmanager_client.delete_secret.call_args.kwargs == {
            "SecretId": row["secretName"], "ForceDeleteWithoutRecovery": True}
        writes = [c.kwargs for c in table.update_item.call_args_list]
        assert writes and len(writes) <= 2
        state_write, credential_write = writes[0], writes[-1]
        assert set(state_write["ExpressionAttributeNames"].values()) == {"state", "credentialClaimedAt", "updatedAt"}
        assert set(credential_write["ExpressionAttributeNames"].values()) == {
            *CLEARED_ALL, EXPIRES_AT_FIELD, "credentialClaimedAt", "updatedAt"}

    def test_a_keyed_row_is_skipped_and_apikeyservice_is_never_invoked_with_the_environment_absent(self, monkeypatch):
        # Through the real revoke path: with no function name the sweep never reaches the Lambda client,
        # so no invoke is attempted against an empty FunctionName; the state is still recorded.
        for name in BOOTSTRAP_ENV:
            monkeypatch.delenv(name, raising=False)
        fresh, fresh_bootstrap = _fresh_reconciler("no_env_keyed")
        assert fresh_bootstrap.api_key_service_function_name == ""
        row = _row("a", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False)
        client = create_autospec(EdhClient, instance=True)
        client.list_all_desktops.return_value = []
        table = MagicMock()
        table.scan.return_value = {"Items": [row]}
        table.get_item.return_value = {"Item": dict(row)}
        default_policy = {"enabled": False, "leaseHours": 8, "forced": False, "graceHours": 24,
                         "updatedAt": None, "updatedBy": None}
        with patch.object(fresh, "EdhClient", MagicMock(return_value=client)), \
                patch.object(fresh, "session_table", table), \
                patch.object(fresh, "read_lease_policy", MagicMock(return_value=default_policy)), \
                patch.object(fresh, "_now", MagicMock(return_value=NOW)):
            summary = fresh.lambda_handler({}, MagicMock())
        assert summary == _summary(examined=1, updated=1, skipped=1)
        fresh_bootstrap.lambda_client.invoke.assert_not_called()
        fresh_bootstrap.secretsmanager_client.delete_secret.assert_not_called()
        assert table.update_item.called
        assert table.update_item.call_count <= 1
        written = table.update_item.call_args.kwargs
        assert set(written["ExpressionAttributeNames"].values()) == {"state", "updatedAt"}


@pytest.mark.unit
class TestOrphanKeySweep:
    EXPIRED = (NOW - timedelta(hours=1)).strftime(STAMP)
    LIVE = (NOW + timedelta(hours=1)).strftime(STAMP)
    AGED = (NOW - timedelta(hours=2)).isoformat()
    YOUNG = (NOW - timedelta(seconds=60)).isoformat()

    @staticmethod
    def _key(api_key_id, expires_at, name=None, created_at=AGED):
        return {"apiKeyId": api_key_id, "apiKeyName": name or f"temporary-session-edh-{api_key_id}",
                "userId": "user@example.com", "expiresAt": expires_at, "isActive": "true", "createdAt": created_at}

    def test_deletes_expired_or_aged_keys_no_row_names_and_leaves_the_rest(self):
        keys = [self._key("key-a", self.EXPIRED), self._key("lost-1", self.EXPIRED),
                self._key("lost-2", self.LIVE), self._key("lost-3", ""), self._key("lost-4", "not-a-date"),
                self._key("young-1", self.LIVE, created_at=self.YOUNG), self._key("young-2", "", created_at=self.YOUNG)]
        summary, revoke_fn, _persist, _pager, _client_factory, delete_fn = _run(
            [_row("a")], [_desktop("sess-a")], keys=keys)
        # key-a is named by row a, whose own expiry is live: it is that row's business, not an orphan. The
        # aged orphans go whether or not they have expired -- a mint whose reply was lost left them -- and the
        # young ones may belong to a mint whose row write has not landed yet.
        revoke_fn.assert_not_called()
        assert delete_fn.call_args_list, "no orphan key was deleted"
        assert {c.args[1]["apiKeyId"] for c in delete_fn.call_args_list} == {"lost-1", "lost-2", "lost-3", "lost-4"}
        assert all(c.args[0] == rec.SYSTEM_EVENT and c.args[2] == rec.SYSTEM_USER_ID for c in delete_fn.call_args_list)
        assert summary == _summary(examined=1, orphanKeysDeleted=4)
        assert rec.ORPHAN_KEY_GRACE_SECONDS == 900

    def test_an_orphan_key_that_cannot_be_dated_is_not_kept(self):
        keys = [self._key("undated", self.LIVE, created_at=None), self._key("garbled", self.LIVE, created_at="yesterday")]
        _summary_, _revoke_fn, _persist, _pager, _client_factory, delete_fn = _run([], [], keys=keys)
        assert {c.args[1]["apiKeyId"] for c in delete_fn.call_args_list} == {"undated", "garbled"}

    def test_a_key_the_row_loop_revoked_is_not_deleted_a_second_time(self):
        summary, revoke_fn, _persist, _pager, _client_factory, delete_fn = _run(
            [_row("e", state=rec.STATE_DELETED)], [], keys=[self._key("key-e", self.EXPIRED)])
        assert {c.args[1]["bootstrapId"] for c in revoke_fn.call_args_list} == {"e"}
        delete_fn.assert_not_called()
        assert summary == _summary(examined=1, updated=1, revoked=1)

    @pytest.mark.parametrize("error", [bootstrap.SessionBootstrapError("down"), RuntimeError("boom")],
                             ids=["bootstrap", "unexpected"])
    def test_a_listing_failure_counts_one_failure_and_deletes_nothing(self, error):
        summary, _revoke_fn, _persist, _pager, _client_factory, delete_fn = _run(
            [_row("a")], [_desktop("sess-a")], keys=error)
        delete_fn.assert_not_called()
        assert summary == _summary(examined=1, failed=1)

    def test_a_failing_delete_does_not_stop_the_orphan_sweep(self):
        def delete(_event, key, _actor, *, operation):
            if key["apiKeyId"] == "lost-1":
                raise bootstrap.SessionBootstrapError("down")

        keys = [self._key("lost-1", self.EXPIRED), self._key("lost-2", self.EXPIRED)]
        summary, _revoke_fn, _persist, _pager, _client_factory, delete_fn = _run([], [], keys=keys, delete=delete)
        assert {c.args[1]["apiKeyId"] for c in delete_fn.call_args_list} == {"lost-1", "lost-2"}
        assert summary == _summary(failed=1, orphanKeysDeleted=1)

    def test_an_expired_orphan_is_audited_as_expired_and_an_unexpired_one_as_revoked(self):
        # A desktop that is no longer live may have been given the unexpired key; deleting it takes the key
        # back before its expiry, which is what the audit row says.
        keys = [self._key("lost-1", self.EXPIRED, name=f"temporary-session-edh-{BOOTSTRAP}"),
                self._key("lost-2", self.LIVE, name=f"temporary-session-edh-{BOOTSTRAP}-r20260908100000")]
        audit = MagicMock()
        with patch.object(bootstrap, "_cross_call", MagicMock(return_value=(200, {"message": "API key deleted"}))), \
                patch.object(bootstrap, "log_auth_changes", audit):
            summary, *_ = _run([], [], keys=keys, delete=bootstrap.delete_orphan_key)
        assert {c.args[2]["apiKeyId"]: c.args[2] for c in audit.call_args_list} == {
            "lost-1": {"bootstrapId": BOOTSTRAP, "apiKeyId": "lost-1", "userId": "user@example.com",
                       "operation": bootstrap.AUDIT_OPERATION_EXPIRE},
            "lost-2": {"bootstrapId": BOOTSTRAP, "apiKeyId": "lost-2", "userId": "user@example.com",
                       "operation": bootstrap.AUDIT_OPERATION_REVOKE},
        }
        assert summary == _summary(orphanKeysDeleted=2)


def _unscheduled(bootstrap_id, **overrides):
    """A row whose desktop's EDH schedule has not been set."""
    row = _row(bootstrap_id, **overrides)
    row.pop("scheduleAppliedAt")
    return row


def _uncredentialed(bootstrap_id, **overrides):
    """An unscheduled row holding no credential, so no revoke is due whatever EDH lists."""
    return _unscheduled(bootstrap_id, apiKeyId="", secretName="", keyExpiresAt="", **overrides)


def _stopped_unscheduled(bootstrap_id):
    return _uncredentialed(bootstrap_id, state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False)


def _orphaned_unscheduled(bootstrap_id):
    return _uncredentialed(bootstrap_id, state=rec.STATE_ORPHANED, edhState="error", connectionAvailable=False,
                           lastError=LAST_ERROR_LAUNCH_ERROR)


def _schedule_updates(client_factory):
    """The schedule updates the sweep asked EDH for, on whichever client."""
    return client_factory.return_value.update_desktop_schedule.call_args_list


def _info_lines(logger):
    """The messages the sweep logged at info level."""
    return [c.args[0] for c in logger.info.call_args_list if c.args]


def _context(seconds_left):
    context = MagicMock()
    context.get_remaining_time_in_millis.return_value = int(seconds_left * 1000)
    return context


@pytest.mark.unit
class TestScheduleBackfill:
    """A listed desktop whose row carries no ``scheduleAppliedAt`` is given the EDH schedule that never
    starts it, as the row's owner, and the row is stamped; a desktop EDH already lists on that schedule is
    stamped without a call. The step runs only where a pass leaves the row otherwise unchanged, only from
    the sweep, and within a per-sweep allowance: a number of calls, a stop after the first refusal, and a
    floor of invocation time."""

    def test_every_listed_row_without_the_stamp_is_scheduled_as_its_owner_and_stamped(self):
        rows = [_unscheduled("a"), _stopped_unscheduled("b"), _orphaned_unscheduled("c")]
        desktops = [_desktop("sess-a"), _desktop("sess-b", "stopped"), _desktop("sess-c", "error")]

        summary, revoke_fn, persist, _pager, client_factory, _delete = _run(rows, desktops)

        asked = _schedule_updates(client_factory)
        assert asked, "no schedule was set"
        assert {c.args[1] for c in asked} == {"sess-a", "sess-b", "sess-c"}
        assert {c.args[0] for c in asked} == {EDH_UID}
        assert EDH_SERVICE_ACTOR_UID not in {c.args[0] for c in asked}
        assert all(c.args[2] == never_auto_start_schedule() for c in asked)
        for bootstrap_id in ("a", "b", "c"):
            assert _written_for(persist, bootstrap_id) == {"scheduleAppliedAt": SCHEDULE_STAMP}
            assert set(_conditions_for(persist, bootstrap_id)) == {SEEN}
        revoke_fn.assert_not_called()
        assert summary == _summary(examined=3, scheduled=3)

    def test_a_running_desktop_its_owner_lists_between_sweeps_is_scheduled_on_the_next_one(self):
        # The row is already connectable; the pass has nothing else to write.
        summary, _revoke, persist, _pager, client_factory, _delete = _run([_unscheduled("a")], [_desktop("sess-a")])

        assert {c.args[1] for c in _schedule_updates(client_factory)} == {"sess-a"}
        assert _written_for(persist, "a") == {"scheduleAppliedAt": SCHEDULE_STAMP}
        assert summary == _summary(examined=1, scheduled=1)

    def test_a_desktop_already_listed_on_the_schedule_is_stamped_without_a_call(self):
        summary, _revoke, persist, _pager, client_factory, _delete = _run(
            [_unscheduled("a")], [_desktop("sess-a", **{EDH_SCHEDULE_FIELD: NEVER_AUTO_START})])

        client_factory.return_value.update_desktop_schedule.assert_not_called()
        assert _written_for(persist, "a") == {"scheduleAppliedAt": SCHEDULE_STAMP}
        assert summary == _summary(examined=1, scheduled=1)

    def test_a_desktop_listed_on_edhs_default_schedule_is_scheduled(self):
        default = json.dumps({**never_auto_start_schedule(), "monday": {"start": 480, "stop": 1140}})
        _summary_, _revoke, _persist, _pager, client_factory, _delete = _run(
            [_unscheduled("a")], [_desktop("sess-a", **{EDH_SCHEDULE_FIELD: default})])

        assert {c.args[1] for c in _schedule_updates(client_factory)} == {"sess-a"}

    @pytest.mark.parametrize("row, desktops", [
        (_row("a"), [_desktop("sess-a")]),
        (_uncredentialed("a", state=rec.STATE_DELETED, edhState=None, connectionAvailable=False,
                         **{EXPIRES_AT_FIELD: EXPIRY}), [_desktop("sess-a")]),
        (_orphaned_unscheduled("a"), []),
        ({key: value for key, value in _creating_row("a", 5).items() if key != "scheduleAppliedAt"}, []),
    ], ids=["already stamped", "deleted", "not listed", "no session id"])
    def test_a_row_that_is_stamped_deleted_unlisted_or_session_less_is_not_scheduled(self, row, desktops):
        summary, _revoke, persist, _pager, client_factory, _delete = _run([row], desktops)

        client_factory.return_value.update_desktop_schedule.assert_not_called()
        persist.assert_not_called()
        assert summary == _summary(examined=1)

    def test_a_stamp_another_writer_overtook_is_deferred(self):
        summary, _revoke, _persist, _pager, client_factory, _delete = _run(
            [_unscheduled("a")], [_desktop("sess-a")], persist=lambda *args, **kwargs: None)

        assert {c.args[1] for c in _schedule_updates(client_factory)} == {"sess-a"}
        assert summary == _summary(examined=1, deferred=1)

    def test_the_sweep_sets_no_more_schedules_than_its_allowance(self):
        over = rec.MAX_SCHEDULE_APPLIES_PER_SWEEP + 5
        rows = [_unscheduled(f"r{i}") for i in range(over)] + [_unscheduled("t")]
        desktops = [_desktop(f"sess-r{i}") for i in range(over)] + [
            _desktop("sess-t", **{EDH_SCHEDULE_FIELD: NEVER_AUTO_START})]

        summary, _revoke, persist, _pager, client_factory, _delete = _run(rows, desktops)

        asked = _schedule_updates(client_factory)
        assert asked, "no schedule was set"
        assert len(asked) <= rec.MAX_SCHEDULE_APPLIES_PER_SWEEP
        # A desktop already on the schedule is stamped whatever the allowance has left.
        assert _written_for(persist, "t") == {"scheduleAppliedAt": SCHEDULE_STAMP}
        assert summary == _summary(examined=over + 1, scheduled=rec.MAX_SCHEDULE_APPLIES_PER_SWEEP + 1)

    def test_a_desktop_already_on_the_schedule_uses_none_of_the_allowance(self):
        held = rec.MAX_SCHEDULE_APPLIES_PER_SWEEP
        rows = [_unscheduled(f"t{i}") for i in range(held)] + [_unscheduled("last")]
        desktops = [_desktop(f"sess-t{i}", **{EDH_SCHEDULE_FIELD: NEVER_AUTO_START}) for i in range(held)] + [
            _desktop("sess-last")]

        summary, _revoke, persist, _pager, client_factory, _delete = _run(rows, desktops)

        assert {c.args[1] for c in _schedule_updates(client_factory)} == {"sess-last"}
        assert _written_for(persist, "last") == {"scheduleAppliedAt": SCHEDULE_STAMP}
        assert summary == _summary(examined=held + 1, scheduled=held + 1)

    @pytest.mark.parametrize("error", SCHEDULE_FAILURES, ids=[type(error).__name__ for error in SCHEDULE_FAILURES])
    def test_a_refusal_stops_schedule_calls_for_the_rest_of_the_sweep(self, error):
        rows = [_unscheduled("a"), _unscheduled("b"), _unscheduled("t")]
        desktops = [_desktop("sess-a"), _desktop("sess-b"),
                    _desktop("sess-t", **{EDH_SCHEDULE_FIELD: NEVER_AUTO_START})]

        summary, _revoke, persist, _pager, client_factory, _delete = _run(rows, desktops, schedule_answer=error)

        assert client_factory.return_value.update_desktop_schedule.call_count <= 1
        assert _updates_for(persist, "a") == [] and _updates_for(persist, "b") == []
        assert _written_for(persist, "t") == {"scheduleAppliedAt": SCHEDULE_STAMP}
        assert summary == _summary(examined=3, failed=1, scheduled=1)

    def test_a_desktop_edh_cannot_find_under_its_owner_neither_counts_nor_stops_the_others(self):
        rows = [_unscheduled("a"), _unscheduled("b")]
        summary, _revoke, persist, _pager, client_factory, _delete = _run(
            rows, [_desktop("sess-a"), _desktop("sess-b")],
            schedule_answer=EdhNotFoundError("Unable to find this session"))

        assert {c.args[1] for c in _schedule_updates(client_factory)} == {"sess-a", "sess-b"}
        persist.assert_not_called()
        assert summary == _summary(examined=2)

    def test_no_schedule_call_starts_inside_the_invocations_time_floor(self):
        rows = [_unscheduled("a"), _unscheduled("t")]
        desktops = [_desktop("sess-a"), _desktop("sess-t", **{EDH_SCHEDULE_FIELD: NEVER_AUTO_START})]

        summary, _revoke, persist, _pager, client_factory, _delete = _run(
            rows, desktops, context=_context(rec.SCHEDULE_APPLY_TIME_FLOOR_SECONDS - 1))

        client_factory.return_value.update_desktop_schedule.assert_not_called()
        assert _updates_for(persist, "a") == []
        assert _written_for(persist, "t") == {"scheduleAppliedAt": SCHEDULE_STAMP}
        assert summary == _summary(examined=2, scheduled=1)

    @pytest.mark.parametrize("context", [None, MagicMock(), _context(900)],
                             ids=["no context", "no numeric remaining time", "time left"])
    def test_a_context_without_a_deadline_or_with_time_left_lets_the_schedule_be_set(self, context):
        summary, _revoke, _persist, _pager, client_factory, _delete = _run(
            [_unscheduled("a")], [_desktop("sess-a")], context=context)

        assert {c.args[1] for c in _schedule_updates(client_factory)} == {"sess-a"}
        assert summary == _summary(examined=1, scheduled=1)

    def test_each_row_given_the_schedule_is_logged_and_a_sweep_that_leaves_none_names_no_next_sweep(self):
        rows = [_unscheduled("a"), _unscheduled("t")]
        desktops = [_desktop("sess-a"), _desktop("sess-t", **{EDH_SCHEDULE_FIELD: NEVER_AUTO_START})]

        with patch.object(rec, "logger") as logger:
            _run(rows, desktops)

        lines = _info_lines(logger)
        assert {f"EDH schedule set to never start automatically for bootstrap {bootstrap_id}"
                for bootstrap_id in ("a", "t")} <= set(lines)
        assert not any("left for the next sweep" in line for line in lines)

    @pytest.mark.parametrize("reason, count, answer, seconds_left, left", [
        ("allowance", rec.MAX_SCHEDULE_APPLIES_PER_SWEEP + 5, None, None,
         "5 EDH schedules left for the next sweep: 5 past the per-sweep allowance, 0 after a refusal, "
         "0 inside the invocation's time floor"),
        ("refusal", 3, EdhApiError("Your administrator has disabled virtual desktops schedule update"), None,
         "2 EDH schedules left for the next sweep: 0 past the per-sweep allowance, 2 after a refusal, "
         "0 inside the invocation's time floor"),
        ("time", 2, None, rec.SCHEDULE_APPLY_TIME_FLOOR_SECONDS - 1,
         "2 EDH schedules left for the next sweep: 0 past the per-sweep allowance, 0 after a refusal, "
         "2 inside the invocation's time floor"),
    ], ids=["allowance", "refusal", "time"])
    def test_the_rows_a_sweep_leaves_are_logged_with_their_reasons(self, reason, count, answer, seconds_left,
                                                                   left):
        rows = [_unscheduled(f"r{i}") for i in range(count)]
        desktops = [_desktop(f"sess-r{i}") for i in range(count)]
        context = _MOCK_CONTEXT if seconds_left is None else _context(seconds_left)

        with patch.object(rec, "logger") as logger:
            _run(rows, desktops, schedule_answer=answer, context=context)

        lines = _info_lines(logger)
        assert left in lines

    def test_a_pass_that_revokes_or_writes_the_row_leaves_the_schedule_to_a_later_sweep(self):
        rows = [_unscheduled("a", keyExpiresAt=(NOW - timedelta(minutes=1)).strftime(STAMP)),
                _unscheduled("b"),
                _uncredentialed("c")]
        desktops = [_desktop("sess-a"), _desktop("sess-b", "stopped"), _placed("sess-c")]

        summary, _revoke, persist, _pager, client_factory, _delete = _run(rows, desktops)

        client_factory.return_value.update_desktop_schedule.assert_not_called()
        for bootstrap_id in ("a", "b", "c"):
            assert "scheduleAppliedAt" not in _written_for(persist, bootstrap_id)
        assert _written_for(persist, "c") == {"instanceId": INSTANCE_ID}
        assert summary["scheduled"] == 0

    def test_the_stamp_lands_on_the_stored_row_beside_its_state_and_credential(self, edh_session_table):
        row = _unscheduled("b40")
        edh_session_table.put_item(Item=row)
        client = create_autospec(EdhClient, instance=True)
        summary = _summary()

        with patch.object(rec, "session_table", edh_session_table):
            rec._reconcile({"bootstrapId": "b40"}, _index(_desktop("sess-b40")), NOW, summary,
                           client_factory=lambda: client, schedule=rec.ScheduleSweep())

        stored = edh_session_table.get_item(Key={"bootstrapId": "b40"})["Item"]
        assert stored["scheduleAppliedAt"] == SCHEDULE_STAMP
        assert (stored["state"], stored["apiKeyId"], stored["sessionId"]) == (rec.STATE_ACTIVE, "key-b40", "sess-b40")
        asked = client.update_desktop_schedule.call_args_list
        assert asked, "no schedule was set"
        assert {c.args[:2] for c in asked} == {(EDH_UID, "sess-b40")}
        assert all(c.args[2] == never_auto_start_schedule() for c in asked)
        assert summary == _summary(scheduled=1)

    def test_the_stamp_leaves_the_rows_last_change_as_it_was(self, edh_session_table):
        # The stale-session view dates a row's last change by its updatedAt; the stamp records none.
        row = _stopped_unscheduled("b45")
        edh_session_table.put_item(Item=row)
        listed = _desktop("sess-b45", "stopped", **{EDH_SCHEDULE_FIELD: NEVER_AUTO_START})
        summary = _summary()

        with patch.object(rec, "session_table", edh_session_table):
            rec._reconcile({"bootstrapId": "b45"}, _index(listed), NOW, summary,
                           client_factory=lambda: create_autospec(EdhClient, instance=True),
                           schedule=rec.ScheduleSweep())

        stored = edh_session_table.get_item(Key={"bootstrapId": "b45"})["Item"]
        assert (stored["scheduleAppliedAt"], stored["updatedAt"]) == (SCHEDULE_STAMP, row["updatedAt"])
        assert summary == _summary(scheduled=1)

    def test_a_lease_pass_leaves_the_schedule_to_a_later_sweep(self, edh_session_table):
        row = _uncredentialed("b41", leaseExpiresAt=(NOW - timedelta(minutes=5)).isoformat())
        edh_session_table.put_item(Item=row)
        client = create_autospec(EdhClient, instance=True)
        summary = _summary()

        with patch.object(rec, "session_table", edh_session_table):
            rec._reconcile({"bootstrapId": "b41"}, _index(_desktop("sess-b41")), NOW, summary,
                           client_factory=lambda: client, policy=TestLeaseSweep.POLICY, schedule=rec.ScheduleSweep())

        assert {c.args for c in client.stop_desktop.call_args_list} == {(EDH_UID, "sess-b41")}
        client.update_desktop_schedule.assert_not_called()
        assert "scheduleAppliedAt" not in edh_session_table.get_item(Key={"bootstrapId": "b41"})["Item"]

    def test_a_lease_stop_edh_refuses_leaves_the_schedule_to_a_later_pass(self, edh_session_table):
        row = _uncredentialed("b43", leaseExpiresAt=(NOW - timedelta(minutes=5)).isoformat())
        edh_session_table.put_item(Item=row)
        client = create_autospec(EdhClient, instance=True)
        client.stop_desktop.side_effect = EdhUnavailableError("read timeout")
        summary = _summary()
        schedule = rec.ScheduleSweep()

        with patch.object(rec, "session_table", edh_session_table):
            rec._reconcile({"bootstrapId": "b43"}, _index(_desktop("sess-b43")), NOW, summary,
                           client_factory=lambda: client, policy=TestLeaseSweep.POLICY, schedule=schedule)

        assert {c.args for c in client.stop_desktop.call_args_list} == {(EDH_UID, "sess-b43")}
        client.update_desktop_schedule.assert_not_called()
        assert edh_session_table.get_item(Key={"bootstrapId": "b43"})["Item"] == row
        assert summary == _summary(failed=1)
        assert (schedule.remaining, schedule.refused) == (rec.MAX_SCHEDULE_APPLIES_PER_SWEEP, False)

    def test_a_lease_step_that_asks_edh_nothing_lets_the_schedule_be_set(self, edh_session_table):
        # A desktop EDH lists in a state no lease branch acts on is neither stopped nor deleted, so the
        # pass is the ordinary one and the schedule is set on it.
        row = _uncredentialed("b44", edhState="hibernated", connectionAvailable=False,
                              leaseExpiresAt=(NOW - timedelta(minutes=5)).isoformat())
        edh_session_table.put_item(Item=row)
        client = create_autospec(EdhClient, instance=True)
        summary = _summary()

        with patch.object(rec, "session_table", edh_session_table):
            rec._reconcile({"bootstrapId": "b44"}, _index(_desktop("sess-b44", "hibernated")), NOW, summary,
                           client_factory=lambda: client, policy=TestLeaseSweep.POLICY, schedule=rec.ScheduleSweep())

        client.stop_desktop.assert_not_called()
        assert {c.args[:2] for c in client.update_desktop_schedule.call_args_list} == {(EDH_UID, "sess-b44")}
        assert edh_session_table.get_item(Key={"bootstrapId": "b44"})["Item"]["scheduleAppliedAt"] == SCHEDULE_STAMP
        assert summary == _summary(scheduled=1)

    def test_a_reconcile_without_the_sweeps_schedule_state_sets_no_schedule(self, edh_session_table):
        row = _unscheduled("b42")
        edh_session_table.put_item(Item=row)
        factory = MagicMock()
        summary = _summary()

        with patch.object(rec, "session_table", edh_session_table):
            rec._reconcile({"bootstrapId": "b42"}, _index(_desktop("sess-b42")), NOW, summary,
                           client_factory=factory)

        factory.assert_not_called()
        assert edh_session_table.get_item(Key={"bootstrapId": "b42"})["Item"] == row
        assert summary == _summary()

    def test_the_allowance_is_fifty_calls_and_a_two_minute_floor(self):
        assert rec.MAX_SCHEDULE_APPLIES_PER_SWEEP == 50
        assert rec.SCHEDULE_APPLY_TIME_FLOOR_SECONDS == 120


@pytest.mark.unit
class TestPersist:
    def _write(self, updates, seen, error=None):
        table = MagicMock()
        if error is not None:
            table.update_item.side_effect = error
        with patch.object(rec, "session_table", table), \
                patch.object(rec, "_now", MagicMock(return_value=NOW)):
            result = rec._persist("a", updates, seen)
        return result, table.update_item.call_args.kwargs

    def test_writes_the_attributes_stamps_updated_at_and_conditions_on_the_updated_at_seen(self):
        stamp, kwargs = self._write({"state": rec.STATE_ORPHANED, "apiKeyId": ""}, SEEN)
        assert stamp == NOW.isoformat()
        assert kwargs["Key"] == {"bootstrapId": "a"}
        names, values = kwargs["ExpressionAttributeNames"], kwargs["ExpressionAttributeValues"]
        written = {name: values[placeholder.replace("#a", ":v")] for placeholder, name in names.items()}
        assert written == {"state": rec.STATE_ORPHANED, "apiKeyId": "", "updatedAt": NOW.isoformat()}
        (updated_at,) = [placeholder for placeholder, name in names.items() if name == "updatedAt"]
        assert kwargs["ConditionExpression"] == f"{updated_at} = :seen"
        assert values[":seen"] == SEEN
        assert kwargs["UpdateExpression"].startswith("SET ") and "REMOVE" not in kwargs["UpdateExpression"]

    def test_a_row_that_carried_no_updated_at_is_conditioned_on_its_absence(self):
        _stamp, kwargs = self._write({"state": rec.STATE_DELETED}, None)
        (updated_at,) = [p for p, name in kwargs["ExpressionAttributeNames"].items() if name == "updatedAt"]
        assert kwargs["ConditionExpression"] == f"attribute_not_exists({updated_at})"
        assert ":seen" not in kwargs["ExpressionAttributeValues"]

    def test_a_none_value_removes_its_attribute(self):
        _stamp, kwargs = self._write({"state": rec.STATE_ACTIVE, EXPIRES_AT_FIELD: None}, SEEN)
        names, values = kwargs["ExpressionAttributeNames"], kwargs["ExpressionAttributeValues"]
        (expires,) = [placeholder for placeholder, name in names.items() if name == EXPIRES_AT_FIELD]
        assert kwargs["UpdateExpression"].endswith(f" REMOVE {expires}")
        assert expires.replace("#a", ":v") not in values
        assert set(values) == {p.replace("#a", ":v") for p, name in names.items() if name != EXPIRES_AT_FIELD} | {":seen"}

    def test_a_write_that_leaves_updated_at_alone_is_still_conditioned_on_it(self, edh_session_table):
        edh_session_table.put_item(Item={"bootstrapId": "a", "updatedAt": SEEN})

        with patch.object(rec, "session_table", edh_session_table):
            written = rec._persist("a", {"scheduleAppliedAt": SCHEDULE_STAMP}, SEEN, touch=False)
            overtaken = rec._persist("a", {"scheduleAppliedAt": NOW.isoformat()}, STAMP_WRITTEN, touch=False)

        assert (written, overtaken) == (SEEN, None)
        assert edh_session_table.get_item(Key={"bootstrapId": "a"})["Item"] == {
            "bootstrapId": "a", "updatedAt": SEEN, "scheduleAppliedAt": SCHEDULE_STAMP}

    def test_a_claim_release_removes_only_the_stamps_that_still_hold_the_sweeps_own(self, edh_session_table):
        # Row a's start slot was cleared by a stop since, and row b's credential claim is another writer's;
        # the stamp the sweep still owns goes on each.
        edh_session_table.put_item(Item={"bootstrapId": "a", "updatedAt": STAMP_WRITTEN,
                                         "credentialClaimedAt": CREDENTIAL_CLAIM})
        edh_session_table.put_item(Item={"bootstrapId": "b", "updatedAt": STAMP_WRITTEN,
                                         "credentialClaimedAt": "another writer's", "startRequestedAt": START_SLOT})
        claim = {"credentialClaimedAt": CREDENTIAL_CLAIM, "startRequestedAt": START_SLOT}
        released_at = (NOW + timedelta(seconds=5)).isoformat()

        with patch.object(rec, "session_table", edh_session_table), \
                patch.object(rec, "_now", MagicMock(return_value=NOW + timedelta(seconds=5))):
            rec._release_claim("a", claim)
            rec._release_claim("b", claim)

        assert {key: edh_session_table.get_item(Key={"bootstrapId": key})["Item"] for key in ("a", "b")} == {
            "a": {"bootstrapId": "a", "updatedAt": released_at},
            "b": {"bootstrapId": "b", "updatedAt": released_at, "credentialClaimedAt": "another writer's"},
        }

    def test_a_claim_release_that_fails_leaves_its_stamps_to_age_out(self):
        table = MagicMock()
        table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow"}}, "UpdateItem")

        with patch.object(rec, "session_table", table), patch.object(rec, "_now", MagicMock(return_value=NOW)):
            rec._release_claim("a", {"credentialClaimedAt": CREDENTIAL_CLAIM, "startRequestedAt": START_SLOT})

        assert {c.kwargs["ExpressionAttributeNames"]["#stamp"] for c in table.update_item.call_args_list} == {
            "credentialClaimedAt", "startRequestedAt"}

    def test_a_lost_condition_answers_none(self):
        error = ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "changed"}}, "UpdateItem")
        stamp, _kwargs = self._write({"state": rec.STATE_DELETED}, SEEN, error=error)
        assert stamp is None

    def test_any_other_write_failure_raises(self):
        error = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow"}}, "UpdateItem")
        with pytest.raises(ClientError):
            self._write({"state": rec.STATE_DELETED}, SEEN, error=error)


@pytest.mark.unit
class TestLeaseSweep:
    """The lease step ahead of the ordinary reconciliation: an expired lease stops the desktop and stamps
    the row, a desktop already stopped is stamped where it stands, and a stamped desktop is deleted once
    its grace has run out, each checked against EDH's live view before anything is done to the owner's
    desktop."""

    POLICY = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    DISABLED = {**POLICY, "enabled": False}
    LEASE_STOPPED_AT = (NOW - timedelta(hours=30)).isoformat()
    UNTOUCHED = _summary()

    @staticmethod
    def _fake_edh_client(stop_raises=None, delete_raises=None, on_delete=None):
        """A stand-in for the EDH client that records the stops and deletes asked of it; ``on_delete``
        runs when a delete is asked, before it is recorded or refused."""
        calls = SimpleNamespace(stopped=[], deleted=[], client=create_autospec(EdhClient, instance=True))

        def stop_desktop(edh_uid, session_id):
            if stop_raises is not None:
                raise stop_raises
            calls.stopped.append((edh_uid, session_id))
            return {}

        def delete_desktop(edh_uid, session_id):
            if on_delete is not None:
                on_delete()
            if delete_raises is not None:
                raise delete_raises
            calls.deleted.append((edh_uid, session_id))
            return {}

        calls.client.stop_desktop.side_effect = stop_desktop
        calls.client.delete_desktop.side_effect = delete_desktop
        calls.client.list_all_desktops.return_value = []
        return calls

    @staticmethod
    def _expired(bootstrap_id, **overrides):
        """An ACTIVE row holding no credential whose lease ran out five minutes ago."""
        row = _row(bootstrap_id, apiKeyId="", secretName="", keyExpiresAt="",
                   leaseExpiresAt=(NOW - timedelta(minutes=5)).isoformat())
        row.update(overrides)
        return row

    @classmethod
    def _lease_stopped(cls, bootstrap_id, **overrides):
        """A row holding no credential that the lease stopped 30 hours ago, ten hours after its lease ran
        out: past a 24-hour grace from either instant."""
        row = _row(bootstrap_id, state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False,
                   apiKeyId="", secretName="", keyExpiresAt="", lastError=rec.LAST_ERROR_LEASE_EXPIRED,
                   leaseExpiresAt=(NOW - timedelta(hours=40)).isoformat(), leaseStoppedAt=cls.LEASE_STOPPED_AT)
        row.update(overrides)
        return row

    @classmethod
    def _stopped_past_expiry(cls, bootstrap_id, **overrides):
        """A STOPPED row holding no credential and no stamp whose lease ran out five minutes ago: a desktop
        the owner or EDH's idle timer stopped before the lease did."""
        return cls._expired(bootstrap_id, state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False,
                            **overrides)

    def _sweep(self, table, row, index, calls, policy=None, client_factory=None):
        """Seed ``row`` in ``table``, reconcile it against ``index`` and return (summary, stored row)."""
        table.put_item(Item=row)
        summary = dict(self.UNTOUCHED)
        with patch.object(rec, "session_table", table):
            rec._reconcile({"bootstrapId": row["bootstrapId"]}, index, NOW, summary,
                           client_factory=client_factory or (lambda: calls.client),
                           policy=self.POLICY if policy is None else policy)
        return summary, table.get_item(Key={"bootstrapId": row["bootstrapId"]}).get("Item")

    def test_an_expired_lease_stops_the_running_desktop_and_stamps_the_row(self, edh_session_table):
        calls = self._fake_edh_client()

        summary, row = self._sweep(edh_session_table, self._expired("b1"), _index(_desktop("sess-b1")), calls)

        assert set(calls.stopped) == {(EDH_UID, "sess-b1")}
        assert calls.deleted == []
        assert row["state"] == rec.STATE_STOPPED
        assert row["leaseStoppedAt"] == NOW.isoformat()
        assert row["lastError"] == rec.LAST_ERROR_LEASE_EXPIRED
        assert row["connectionAvailable"] is False
        assert summary == {**self.UNTOUCHED, "updated": 1}

    def test_a_disabled_policy_takes_no_lease_action(self, edh_session_table):
        calls = self._fake_edh_client()

        summary, row = self._sweep(edh_session_table, self._expired("b2"), _index(_desktop("sess-b2")), calls,
                                   policy=self.DISABLED)

        assert calls.stopped == [] and calls.deleted == []
        assert row["state"] == rec.STATE_ACTIVE
        assert "leaseStoppedAt" not in row
        assert summary == self.UNTOUCHED

    def test_a_refused_stop_counts_failed_and_the_live_view_is_still_merged(self, edh_session_table):
        calls = self._fake_edh_client(stop_raises=EdhApiError("refused", status=500))

        summary, row = self._sweep(edh_session_table, self._expired("b3"), _index(_placed("sess-b3")), calls)

        assert summary["failed"] == 1
        assert row["state"] == rec.STATE_ACTIVE
        assert row["instanceId"] == INSTANCE_ID
        assert "leaseStoppedAt" not in row

    def test_an_expired_row_without_a_session_takes_the_ordinary_path(self, edh_session_table):
        calls = self._fake_edh_client()
        row = self._expired("b4")
        row.pop("sessionId")

        summary, stored = self._sweep(edh_session_table, row, _index(), calls)

        assert calls.stopped == [] and calls.deleted == []
        assert summary["failed"] == 0
        # An ACTIVE row EDH lists no desktop for is recorded gone, and one holding no credential is left to
        # the table's TTL.
        assert stored["state"] == rec.STATE_DELETED
        assert stored[EXPIRES_AT_FIELD] == EXPIRY

    def test_a_delete_when_edh_lists_no_desktop_records_the_row_gone_without_a_call(self, edh_session_table):
        calls = self._fake_edh_client()

        summary, row = self._sweep(edh_session_table, self._lease_stopped("b5"), _index(), calls)

        assert calls.stopped == [] and calls.deleted == []
        assert row["state"] == rec.STATE_DELETED
        assert row["connectionAvailable"] is False
        assert row[EXPIRES_AT_FIELD] == EXPIRY
        assert summary == {**self.UNTOUCHED, "updated": 1}

    def test_a_deleted_row_still_holding_a_key_is_left_to_the_credential_sweep(self, edh_session_table):
        calls = self._fake_edh_client()

        _, row = self._sweep(edh_session_table, self._lease_stopped("b6", apiKeyId="key-b6"),
                             _index(_desktop("sess-b6", "stopped")), calls)

        assert set(calls.deleted) == {(EDH_UID, "sess-b6")}
        assert row["state"] == rec.STATE_DELETED
        assert row["apiKeyId"] == "key-b6"
        assert EXPIRES_AT_FIELD not in row

    def test_a_delete_is_refused_while_edh_lists_the_desktop_running(self, edh_session_table):
        calls = self._fake_edh_client()

        summary, row = self._sweep(edh_session_table, self._lease_stopped("b7"), _index(_desktop("sess-b7")), calls)

        assert calls.stopped == [] and calls.deleted == []
        assert row["state"] == rec.STATE_ACTIVE
        assert row["connectionAvailable"] is True
        assert "leaseStoppedAt" not in row
        assert "lastError" not in row
        assert summary == {**self.UNTOUCHED, "updated": 1}

    def test_a_stop_of_a_desktop_edh_already_stopped_stamps_the_row_without_a_call(self, edh_session_table):
        calls = self._fake_edh_client()

        summary, row = self._sweep(edh_session_table, self._expired("b8"),
                                   _index(_desktop("sess-b8", "stopped")), calls)

        assert calls.stopped == []
        assert row["state"] == rec.STATE_STOPPED
        assert row["leaseStoppedAt"] == NOW.isoformat()
        assert row["lastError"] == rec.LAST_ERROR_LEASE_EXPIRED
        assert summary == {**self.UNTOUCHED, "updated": 1}

    def test_a_stopped_desktop_past_expiry_is_stamped_without_an_edh_call(self, edh_session_table):
        calls = self._fake_edh_client()

        summary, row = self._sweep(edh_session_table, self._stopped_past_expiry("b23"),
                                   _index(_desktop("sess-b23", "stopped")), calls)

        assert calls.stopped == []
        assert row["state"] == rec.STATE_STOPPED
        assert row["leaseStoppedAt"] == NOW.isoformat()
        assert row["lastError"] == rec.LAST_ERROR_LEASE_EXPIRED
        assert row["connectionAvailable"] is False
        assert summary == {**self.UNTOUCHED, "updated": 1}

    def test_a_stopped_row_whose_desktop_edh_lists_running_is_stopped_then_stamped(self, edh_session_table):
        calls = self._fake_edh_client()

        summary, row = self._sweep(edh_session_table, self._stopped_past_expiry("b24"),
                                   _index(_desktop("sess-b24")), calls)

        assert set(calls.stopped) == {(EDH_UID, "sess-b24")}
        assert row["state"] == rec.STATE_STOPPED
        assert row["leaseStoppedAt"] == NOW.isoformat()
        assert row["lastError"] == rec.LAST_ERROR_LEASE_EXPIRED
        assert row["connectionAvailable"] is False
        assert summary == {**self.UNTOUCHED, "updated": 1}

    def test_the_expiry_stamp_replaces_a_prior_error_code(self, edh_session_table):
        """The stamp is a plain write, so the code a stopped row already carried gives way to it."""
        calls = self._fake_edh_client()
        row_in = self._stopped_past_expiry("b26", lastError=LAST_ERROR_LAUNCH_ERROR)

        summary, row = self._sweep(edh_session_table, row_in, _index(_desktop("sess-b26", "stopped")), calls)

        assert calls.stopped == []
        assert row["lastError"] == rec.LAST_ERROR_LEASE_EXPIRED
        assert row["leaseStoppedAt"] == NOW.isoformat()
        assert row["state"] == rec.STATE_STOPPED
        assert summary == {**self.UNTOUCHED, "updated": 1}

    def test_a_deleted_row_with_neither_a_credential_nor_an_expiry_is_swept_once_and_given_the_ttl(
            self, edh_session_table):
        """A removal recorded without its expiry: the scan selects the row, the pass stamps the TTL, and
        the next scan leaves it alone."""
        calls = self._fake_edh_client()
        row_in = self._lease_stopped("b27", state=rec.STATE_DELETED)
        assert EXPIRES_AT_FIELD not in row_in
        edh_session_table.put_item(Item=row_in)
        with patch.object(rec, "session_table", edh_session_table):
            selected_before = [item["bootstrapId"] for item in rec._scan_swept_rows(NOW)]

        summary, row = self._sweep(edh_session_table, row_in, _index(), calls)

        with patch.object(rec, "session_table", edh_session_table):
            selected_after = [item["bootstrapId"] for item in rec._scan_swept_rows(NOW)]
        assert (selected_before, selected_after) == (["b27"], [])
        assert row[EXPIRES_AT_FIELD] == EXPIRY
        assert row["state"] == rec.STATE_DELETED
        assert calls.stopped == [] and calls.deleted == []
        assert summary == {**self.UNTOUCHED, "updated": 1}

    def test_a_stopped_row_whose_desktop_is_gone_takes_the_ordinary_path(self, edh_session_table):
        calls = self._fake_edh_client()

        summary, row = self._sweep(edh_session_table, self._stopped_past_expiry("b25"), _index(), calls)

        assert calls.stopped == [] and calls.deleted == []
        # A STOPPED row EDH lists no desktop for is recorded gone, and one holding no credential is left to
        # the table's TTL.
        assert row["state"] == rec.STATE_DELETED
        assert row["connectionAvailable"] is False
        assert row[EXPIRES_AT_FIELD] == EXPIRY
        assert "leaseStoppedAt" not in row
        assert summary == {**self.UNTOUCHED, "updated": 1}

    def test_a_stop_when_edh_lists_no_desktop_takes_the_ordinary_path(self, edh_session_table):
        calls = self._fake_edh_client()

        summary, row = self._sweep(edh_session_table, self._expired("b9"), _index(), calls)

        assert calls.stopped == [] and calls.deleted == []
        assert row["state"] == rec.STATE_DELETED
        assert "leaseStoppedAt" not in row
        assert summary["failed"] == 0

    def test_a_stop_of_a_desktop_in_error_takes_the_ordinary_path(self, edh_session_table):
        calls = self._fake_edh_client()

        _, row = self._sweep(edh_session_table, self._expired("b10"), _index(_desktop("sess-b10", "error")), calls)

        assert calls.stopped == []
        assert row["state"] == rec.STATE_ORPHANED
        assert row["lastError"] == LAST_ERROR_LAUNCH_ERROR
        assert "leaseStoppedAt" not in row

    def test_a_delete_claims_the_row_before_edh_is_called(self, edh_session_table):
        states_at_call = []
        calls = self._fake_edh_client(on_delete=lambda: states_at_call.append(
            edh_session_table.get_item(Key={"bootstrapId": "b11"})["Item"]["state"]))

        summary, row = self._sweep(edh_session_table, self._lease_stopped("b11"),
                                   _index(_desktop("sess-b11", "stopped")), calls)

        assert states_at_call == [rec.STATE_DELETED]
        assert set(calls.deleted) == {(EDH_UID, "sess-b11")}
        assert row["state"] == rec.STATE_DELETED
        assert row[EXPIRES_AT_FIELD] == EXPIRY
        assert summary == {**self.UNTOUCHED, "updated": 1}

    def test_a_delete_whose_claim_is_lost_deletes_nothing(self, edh_session_table):
        calls = self._fake_edh_client()

        with patch.object(rec, "_persist", return_value=None):
            summary, row = self._sweep(edh_session_table, self._lease_stopped("b12"),
                                       _index(_desktop("sess-b12", "stopped")), calls)

        assert calls.deleted == []
        assert row["state"] == rec.STATE_STOPPED
        assert summary == {**self.UNTOUCHED, "deferred": 1}

    def test_a_refused_delete_leaves_the_row_stopped_and_stamped(self, edh_session_table):
        calls = self._fake_edh_client(delete_raises=EdhApiError("refused", status=500))

        summary, row = self._sweep(edh_session_table, self._lease_stopped("b13"),
                                   _index(_desktop("sess-b13", "stopped")), calls)

        assert row["state"] == rec.STATE_STOPPED
        assert row["leaseStoppedAt"] == self.LEASE_STOPPED_AT
        assert row["lastError"] == rec.LAST_ERROR_LEASE_EXPIRED
        assert EXPIRES_AT_FIELD not in row
        assert summary == {**self.UNTOUCHED, "failed": 1}

    @pytest.mark.parametrize("answer", [EdhNotFoundError("gone", status=404), EdhContractError("unreadable")],
                             ids=["already gone", "unconfirmed"])
    def test_a_desktop_gone_or_unconfirmed_at_delete_keeps_the_claim(self, edh_session_table, answer):
        calls = self._fake_edh_client(delete_raises=answer)

        summary, row = self._sweep(edh_session_table, self._lease_stopped("b14"),
                                   _index(_desktop("sess-b14", "stopped")), calls)

        assert row["state"] == rec.STATE_DELETED
        assert row[EXPIRES_AT_FIELD] == EXPIRY
        assert summary == {**self.UNTOUCHED, "updated": 1}

    def test_a_stop_whose_write_is_overtaken_counts_deferred_and_is_stamped_by_the_next_sweep(self, edh_session_table):
        calls = self._fake_edh_client()

        with patch.object(rec, "_persist", return_value=None):
            summary, _ = self._sweep(edh_session_table, self._expired("b15"), _index(_desktop("sess-b15")), calls)

        assert set(calls.stopped) == {(EDH_UID, "sess-b15")}
        assert summary == {**self.UNTOUCHED, "deferred": 1}

        # The write that overtook the stamp was a read route merging EDH's `stopping`. The next sweep finds
        # the desktop stopped and stamps it without another call, and the grace runs from that sweep.
        merged = self._expired("b15", state=rec.STATE_STOPPED, edhState="stopping", connectionAvailable=False,
                               updatedAt=(NOW - timedelta(minutes=3)).isoformat())
        calls = self._fake_edh_client()

        summary, stored = self._sweep(edh_session_table, merged, _index(_desktop("sess-b15", "stopped")), calls)

        assert calls.stopped == []
        assert stored["leaseStoppedAt"] == NOW.isoformat()
        assert stored["lastError"] == rec.LAST_ERROR_LEASE_EXPIRED
        assert summary == {**self.UNTOUCHED, "updated": 1}
        assert rec.lease_action(stored, self.POLICY, NOW + timedelta(hours=25)) == "delete"
        assert rec.lease_action(stored, self.POLICY, NOW + timedelta(hours=23)) is None

    def test_each_lease_action_opens_its_own_edh_client(self, edh_session_table):
        calls = self._fake_edh_client()
        stop_factory, delete_factory, ordinary_factory = (
            MagicMock(side_effect=lambda: calls.client) for _ in range(3))

        self._sweep(edh_session_table, self._expired("b16"), _index(_desktop("sess-b16")), calls,
                    client_factory=stop_factory)
        self._sweep(edh_session_table, self._lease_stopped("b17"), _index(_desktop("sess-b17", "stopped")), calls,
                    client_factory=delete_factory)
        self._sweep(edh_session_table, _row("b18"), _index(_desktop("sess-b18")), calls,
                    client_factory=ordinary_factory)

        assert stop_factory.called and delete_factory.called
        ordinary_factory.assert_not_called()
        assert set(calls.stopped) == {(EDH_UID, "sess-b16")}
        assert set(calls.deleted) == {(EDH_UID, "sess-b17")}

    def test_the_sweep_lists_with_its_own_client_and_acts_with_another(self, edh_session_table):
        lister, actor = create_autospec(EdhClient, instance=True), create_autospec(EdhClient, instance=True)
        lister.list_all_desktops.return_value = [_desktop("sess-b19")]
        clients = iter([lister, actor])
        client_class = MagicMock(side_effect=lambda **kwargs: next(clients))
        edh_session_table.put_item(Item=self._expired("b19"))

        with patch.object(rec, "session_table", edh_session_table), patch.object(rec, "_now", return_value=NOW), \
                patch.object(rec, "read_lease_policy", return_value=self.POLICY), \
                patch.object(rec, "EdhClient", client_class):
            summary = rec.lambda_handler({}, MagicMock())

        assert client_class.call_count <= 2
        lister.stop_desktop.assert_not_called()
        actor.list_all_desktops.assert_not_called()
        assert {call.args for call in actor.stop_desktop.call_args_list} == {(EDH_UID, "sess-b19")}
        assert summary["updated"] == 1 and summary["failed"] == 0

    def test_a_row_without_a_readable_stamp_is_never_acted_on(self, edh_session_table):
        calls = self._fake_edh_client()
        row = _row("b20", updatedAt="not-a-date", leaseExpiresAt=(NOW - timedelta(minutes=5)).isoformat())

        summary, stored = self._sweep(edh_session_table, row, _index(_desktop("sess-b20")), calls)

        assert calls.stopped == [] and calls.deleted == []
        assert stored == row
        assert summary == self.UNTOUCHED

    def test_a_settled_lease_action_ends_the_pass_for_the_row(self, edh_session_table):
        calls = self._fake_edh_client()

        with patch.object(rec, "reconcile_row", wraps=rec.reconcile_row) as merge:
            self._sweep(edh_session_table, self._expired("b21"), _index(_desktop("sess-b21")), calls)

        merge.assert_not_called()

    def test_a_row_whose_lease_holds_takes_the_ordinary_path(self, edh_session_table):
        calls = self._fake_edh_client()
        row = self._expired("b22", leaseExpiresAt=(NOW + timedelta(hours=3)).isoformat())

        with patch.object(rec, "reconcile_row", wraps=rec.reconcile_row) as merge:
            _, stored = self._sweep(edh_session_table, row, _index(_placed("sess-b22")), calls)

        assert merge.called
        assert calls.stopped == [] and calls.deleted == []
        assert stored["instanceId"] == INSTANCE_ID


@pytest.mark.unit
def test_an_unreadable_lease_policy_does_not_stop_the_sweep():
    """A policy read that fails leaves the sweep running rather than aborting it: it still reaches the
    table, and taking no lease action is the safe direction because a lease action stops or deletes a
    desktop."""
    client = create_autospec(EdhClient, instance=True)
    client.list_all_desktops.return_value = []
    table = MagicMock()
    table.scan.return_value = {"Items": []}
    with patch.object(rec, "EdhClient", MagicMock(return_value=client)), \
            patch.object(rec, "session_table", table), \
            patch.object(rec, "read_lease_policy",
                         MagicMock(side_effect=RuntimeError("dynamodb unavailable"))):
        summary = rec.lambda_handler({}, MagicMock())
    # The sweep completed rather than raising, and it still went to the table.
    assert isinstance(summary, dict)
    table.scan.assert_called()


def _claimed_row(bootstrap_id, seconds_ago, **overrides):
    """A row a renew claimed ``seconds_ago`` before NOW to re-issue its key."""
    return _row(bootstrap_id, credentialClaimedAt=credential_claim_stamp(NOW - timedelta(seconds=seconds_ago)),
                **overrides)


@pytest.mark.unit
class TestTheCredentialClaimHoldsEveryRevoke:
    """A renew claims its row before it mints a replacement key, settles the row, writes the secret and
    releases the claim. While that claim is fresh the sweep revokes nothing on the row -- whatever the
    key's expiry -- since a revoke would overwrite the secret the renew is about to fill or delete a key
    the row is about to name."""

    @pytest.mark.parametrize("overrides, desktops", REVOKING_CASES, ids=REVOKING_IDS)
    @pytest.mark.parametrize("seconds_ago", [0, 60, CREDENTIAL_CLAIM_SECONDS - 1])
    def test_a_fresh_claim_holds_the_revoke_and_the_secret_removal_for_a_later_sweep(
            self, overrides, desktops, seconds_ago):
        summary = _summary()
        _updates, revoke, delete_secret = rec.reconcile_row(
            _claimed_row("h", seconds_ago, **overrides), _index(*desktops), NOW, summary)
        assert (revoke, delete_secret) == (False, False)
        assert summary == _summary(deferred=1)

    @pytest.mark.parametrize("overrides, desktops", REVOKING_CASES, ids=REVOKING_IDS)
    def test_the_same_rows_without_a_claim_are_revoked(self, overrides, desktops):
        # Control: each case above does revoke once the claim is gone.
        _updates, revoke, _delete = rec.reconcile_row(_row("h", **overrides), _index(*desktops), NOW, _summary())
        assert revoke is True

    @pytest.mark.parametrize("claim", [credential_claim_stamp(NOW - timedelta(seconds=CREDENTIAL_CLAIM_SECONDS)),
                                       credential_claim_stamp(NOW - timedelta(hours=1)), "not-a-timestamp", None],
                             ids=["at the window", "long past", "unreadable", "cleared"])
    def test_a_claim_that_is_not_fresh_holds_nothing(self, claim):
        summary = _summary()
        row = _row("h", credentialClaimedAt=claim, keyExpiresAt=EXPIRED_KEY)
        assert rec.reconcile_row(row, _index(_desktop("sess-h")), NOW, summary) == ({}, True, False)
        assert summary == _summary()

    def test_a_held_row_keeps_the_state_edh_lists(self):
        updates, _revoke, _delete = rec.reconcile_row(
            _claimed_row("h", 30), _index(_desktop("sess-h", "stopped")), NOW, _summary())
        assert updates == {"state": rec.STATE_STOPPED, "edhState": "stopped", "connectionAvailable": False}

    def test_a_claimed_row_with_nothing_to_revoke_is_not_counted_deferred(self):
        summary = _summary()
        assert rec.reconcile_row(_claimed_row("h", 30), _index(_desktop("sess-h")), NOW, summary) == ({}, False, False)
        assert summary == _summary()

    def test_a_sweep_revokes_nothing_on_a_claimed_row_and_writes_what_edh_lists(self):
        row = _claimed_row("h", 30, state=rec.STATE_ACTIVE, keyExpiresAt=EXPIRED_KEY)
        summary, revoke_fn, persist, *_ = _run([row], [_desktop("sess-h", "stopped")])
        revoke_fn.assert_not_called()
        assert _written_for(persist, "h") == {"state": rec.STATE_STOPPED, "edhState": "stopped",
                                              "connectionAvailable": False}
        assert summary == _summary(examined=1, updated=1, deferred=1)

    def test_the_window_is_five_minutes(self):
        assert CREDENTIAL_CLAIM_SECONDS == 300


@pytest.mark.unit
class TestTheSweepsRevokeTakesTheCredentialClaim:
    def test_the_claim_stamps_the_credential_claim_and_the_clear_removes_it(self):
        row = _row("a", keyExpiresAt=EXPIRED_KEY)
        summary, _revoke_fn, persist, *_ = _run([row], [_desktop("sess-a")])
        claim, clear = _updates_for(persist, "a")
        assert claim == {"credentialClaimedAt": CREDENTIAL_CLAIM}
        assert clear == {**CLEARED_KEY, "credentialClaimedAt": None}
        assert summary == _summary(examined=1, updated=1, revoked=1)

    def test_a_stopped_row_is_claimed_with_both_its_start_slot_and_the_credential_claim(self):
        summary, _revoke_fn, persist, *_ = _run([_row("b")], [_desktop("sess-b", "stopped")])
        claim, clear = _updates_for(persist, "b")
        assert claim["credentialClaimedAt"] == CREDENTIAL_CLAIM and claim["startRequestedAt"] == START_SLOT
        assert clear["credentialClaimedAt"] is None and clear["startRequestedAt"] is None

    def test_a_claim_a_crashed_renew_left_past_the_window_is_taken_over(self):
        row = _claimed_row("a", CREDENTIAL_CLAIM_SECONDS + 60, keyExpiresAt=EXPIRED_KEY)
        _summary_, revoke_fn, persist, *_ = _run([row], [_desktop("sess-a")])
        assert {c.args[1]["bootstrapId"] for c in revoke_fn.call_args_list} == {"a"}
        assert _written_for(persist, "a")["credentialClaimedAt"] is None

    def test_a_pass_that_revokes_nothing_takes_no_claim(self):
        _summary_, _revoke_fn, persist, *_ = _run([_row("a")], [_desktop("sess-a", "placing")])
        assert "credentialClaimedAt" not in _written_for(persist, "a")

    def test_a_renew_racing_the_revoke_cannot_claim_the_row_until_the_claim_is_cleared(self, monkeypatch):
        # The sweep takes an expired key back from a running desktop. A renew that claims the row while the
        # revoke runs is refused, so it neither mints a key the revoke's overwrite would disown nor settles a
        # key onto a row whose clear is about to remove it.
        now = datetime.now(timezone.utc)
        row = _row("a", updatedAt=(now - timedelta(hours=1)).isoformat(),
                   keyExpiresAt=(now - timedelta(minutes=1)).strftime(STAMP))
        table = ConditionalRowTable(**row)
        sweep_table, renew_table = MagicMock(), MagicMock()
        sweep_table.get_item.side_effect = lambda **kwargs: {"Item": dict(table.item)}
        sweep_table.update_item.side_effect = table
        renew_table.update_item.side_effect = table
        during = {}

        renew_table.get_item.side_effect = table.read

        def revoke(event, revoked_row, *, delete_secret, actor):
            during["a renew claims the row"] = service._claim_credential(
                dict(table.item), credential_claim_stamp(datetime.now(timezone.utc)))
            return dict(CLEARED_KEY)

        monkeypatch.setattr(rec, "session_table", sweep_table)
        monkeypatch.setattr(rec, "_now", lambda: now)
        monkeypatch.setattr(service, "edh_session_table", renew_table)
        monkeypatch.setattr(bootstrap, "revoke_session", revoke)
        monkeypatch.setattr(bootstrap, "api_key_service_function_name", "vams-apiKeyService")
        summary = _summary()

        rec._reconcile({"bootstrapId": "a"}, _index(_desktop("sess-a")), now, summary)

        assert during == {"a renew claims the row": False}
        assert "credentialClaimedAt" not in table.item
        assert summary == _summary(updated=1, revoked=1)
        # Once the clear has landed, a renew claims the row as any renew does.
        assert service._claim_credential(dict(table.item), credential_claim_stamp(datetime.now(timezone.utc))) is True


@pytest.mark.unit
class TestTheKeysOfALiveDesktop:
    """A desktop that may still be running -- its row stored ACTIVE or CREATING or under a fresh start or
    credential claim, or the desktop listed by EDH as running or starting -- keeps every unexpired key of
    its bootstrap until the key's own expiry: a re-issue
    leaves the key it replaced with the desktop, which signs in with whatever its secret holds. The keys of
    a row that is not live are unreferenced save the one it names, and go once expired or past the orphan
    grace."""

    AGED = (NOW - timedelta(hours=3)).isoformat()
    YOUNG = (NOW - timedelta(seconds=60)).isoformat()
    LIVE = (NOW + timedelta(hours=2)).strftime(STAMP)
    EXPIRED = (NOW - timedelta(minutes=5)).strftime(STAMP)

    @classmethod
    def _key(cls, api_key_id, bootstrap_id, expires_at, suffix="-r20260908100000", created_at=None):
        return {"apiKeyId": api_key_id, "apiKeyName": f"temporary-session-edh-{bootstrap_id}{suffix}",
                "userId": "user@example.com", "expiresAt": expires_at, "isActive": "true",
                "createdAt": created_at or cls.AGED}

    def test_a_running_desktop_keeps_every_unexpired_key_of_its_bootstrap(self):
        keys = [self._key("key-l", "l", self.LIVE), self._key("key-l-old", "l", self.LIVE, suffix=""),
                self._key("key-l-older", "l", self.LIVE, suffix="-r20260908090000"),
                self._key("key-l-expired", "l", self.EXPIRED, suffix="-r20260908080000")]
        summary, revoke_fn, _persist, _pager, _client_factory, delete_fn = _run(
            [_row("l")], [_desktop("sess-l")], keys=keys)
        # The key the row names is its own; the ones it replaced stay with the desktop until they expire.
        assert {c.args[1]["apiKeyId"] for c in delete_fn.call_args_list} == {"key-l-expired"}
        revoke_fn.assert_not_called()
        assert summary == _summary(examined=1, orphanKeysDeleted=1)

    @pytest.mark.parametrize("row, desktops", [
        (_creating_row("v", 5), []),
        (_start_stamped_row("v"), [_desktop("sess-v", "stopped")]),
        (_claimed_row("v", 30, state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False),
         [_desktop("sess-v", "stopped")]),
        (_claimed_row("v", 30, state=rec.STATE_DELETED, apiKeyId="", secretName="", keyExpiresAt="",
                      **{EXPIRES_AT_FIELD: EXPIRY}), []),
    ], ids=["launching", "starting", "stopped under a claim", "deleted under a claim"])
    def test_a_desktop_that_may_still_be_running_keeps_its_keys_and_a_stopped_one_does_not(self, row, desktops):
        stopped = _row("s", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False, apiKeyId="")
        keys = [self._key("key-v-old", "v", self.LIVE), self._key("key-s-old", "s", self.LIVE)]
        _summary_, _revoke_fn, _persist, _pager, _client_factory, delete_fn = _run(
            [row, stopped], [*desktops, _desktop("sess-s", "stopped")], keys=keys)
        assert {c.args[1]["apiKeyId"] for c in delete_fn.call_args_list} == {"key-s-old"}

    def test_a_stopped_desktop_loses_its_superseded_keys_and_leaves_the_named_one_to_its_revoke(self):
        row = _row("s", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False)
        keys = [self._key("key-s", "s", self.LIVE), self._key("key-s-old", "s", self.LIVE, suffix=""),
                self._key("key-s-young", "s", self.LIVE, suffix="-r20260908115900", created_at=self.YOUNG)]
        summary, revoke_fn, _persist, _pager, _client_factory, delete_fn = _run(
            [row], [_desktop("sess-s", "stopped")], keys=keys)
        # A key minted within the grace may still be one a request is about to name on its row.
        assert {c.args[1]["apiKeyId"] for c in delete_fn.call_args_list} == {"key-s-old"}
        assert {c.args[1]["apiKeyId"] for c in revoke_fn.call_args_list} == {"key-s"}
        assert summary == _summary(examined=1, updated=1, revoked=1, orphanKeysDeleted=1)

    def test_a_claim_past_its_window_keeps_nothing(self):
        row = _claimed_row("v", CREDENTIAL_CLAIM_SECONDS + 60, state=rec.STATE_STOPPED, edhState="stopped",
                           connectionAvailable=False, apiKeyId="")
        _summary_, _revoke_fn, _persist, _pager, _client_factory, delete_fn = _run(
            [row], [_desktop("sess-v", "stopped")], keys=[self._key("key-v-old", "v", self.LIVE)])
        assert {c.args[1]["apiKeyId"] for c in delete_fn.call_args_list} == {"key-v-old"}

    @pytest.mark.parametrize("state, stored_edh_state, edh_state", [
        (rec.STATE_STOPPED, "stopped", "running"), (rec.STATE_ORPHANED, "error", "running"),
        (rec.STATE_STOPPED, "stopped", "pending"),
    ], ids=["stopped, listed running", "orphaned, listed running", "stopped, listed starting"])
    def test_a_row_stored_down_whose_desktop_edh_lists_up_keeps_its_keys(self, state, stored_edh_state, edh_state):
        # The pass records the row running or starting again; its keys are judged by that, not by the state
        # the scan read.
        row = _row("r", state=state, edhState=stored_edh_state, connectionAvailable=False, apiKeyId="")
        stopped = _row("s", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False, apiKeyId="")
        keys = [self._key("key-r-old", "r", self.LIVE, suffix=""), self._key("key-s-old", "s", self.LIVE)]
        _summary_, _revoke_fn, _persist, _pager, _client_factory, delete_fn = _run(
            [row, stopped], [_desktop("sess-r", edh_state), _desktop("sess-s", "stopped")], keys=keys)
        assert {c.args[1]["apiKeyId"] for c in delete_fn.call_args_list} == {"key-s-old"}

    def test_a_row_the_scan_found_live_keeps_its_keys_though_the_sweep_later_stops_it(self):
        # The keys are judged against the rows as the scan returned them: the orphan pass never deletes a
        # key of a desktop the scan saw running, and the next sweep takes the stopped row's keys up.
        keys = [self._key("key-a-old", "a", self.LIVE, suffix="")]
        _summary_, revoke_fn, _persist, _pager, _client_factory, delete_fn = _run(
            [_row("a")], [_desktop("sess-a", "stopped")], keys=keys)
        assert {c.args[1]["bootstrapId"] for c in revoke_fn.call_args_list} == {"a"}
        delete_fn.assert_not_called()


@pytest.mark.unit
def test_an_unreadable_lease_policy_is_counted_as_a_failure():
    """A sweep that could not read the policy took no lease action on any row, so its summary does not
    read as a clean one."""
    client = create_autospec(EdhClient, instance=True)
    client.list_all_desktops.return_value = []
    table = MagicMock()
    table.scan.return_value = {"Items": []}
    with patch.object(rec, "EdhClient", MagicMock(return_value=client)), \
            patch.object(rec, "session_table", table), \
            patch.object(bootstrap, "session_bootstrap_enabled", False), \
            patch.object(rec, "read_lease_policy",
                         MagicMock(side_effect=RuntimeError("dynamodb unavailable"))):
        summary = rec.lambda_handler({}, MagicMock())
    assert summary == _summary(failed=1)


@pytest.mark.unit
class TestAnUnlistedDesktopIsConfirmedGoneBeforeItsRowIsDeleted:
    """EDH's cluster listing leaving a desktop out is confirmed with the token-free session-state read
    before the row is recorded DELETED, so one empty or truncated listing cannot detach every workstation
    and revoke every credential while the desktops keep running."""

    def test_an_empty_listing_of_desktops_edh_still_reports_running_deletes_nothing(self):
        rows = [_row("a"), _row("b", state=rec.STATE_STOPPED, edhState="stopped")]
        states = [_desktop("sess-a", "running"), _desktop("sess-b", "stopped")]
        summary, revoke_fn, persist, _pager, client_factory, _delete = _run(rows, [], states=states)
        client = client_factory.return_value
        assert sorted(client.get_desktop_states.call_args.args[1]) == ["sess-a", "sess-b"]
        assert client.get_desktop_states.call_args.args[0] == EDH_SERVICE_ACTOR_UID
        # The running desktop is left as it is; the stopped one only has its key revoked, as any stopped
        # desktop's is. Neither row is recorded DELETED.
        assert _updates_for(persist, "a") == []
        assert _updates_for(persist, "b")
        assert "state" not in _written_for(persist, "b")
        assert {c.args[1]["bootstrapId"] for c in revoke_fn.call_args_list} == {"b"}
        assert summary["revoked"] == 1

    def test_a_desktop_the_session_state_read_does_not_report_is_recorded_deleted(self):
        summary, revoke_fn, persist, _pager, client_factory, _delete = _run([_row("gone")], [], states=[])
        assert _written_for(persist, "gone").get("state") == rec.STATE_DELETED
        assert {c.args[1]["bootstrapId"] for c in revoke_fn.call_args_list} == {"gone"}

    def test_a_failed_session_state_read_leaves_every_unlisted_row_for_the_next_sweep(self):
        summary, revoke_fn, persist, _pager, client_factory, delete_fn = _run(
            [_row("a"), _row("b")], [], states=EdhUnavailableError("read timeout"),
            keys=[{"apiKeyId": "key-a", "apiKeyName": "temporary-session-edh-a",
                   "createdAt": (NOW - timedelta(days=2)).isoformat()}])
        persist.assert_not_called()
        revoke_fn.assert_not_called()
        delete_fn.assert_not_called()
        assert summary["deferred"] == 2
        assert summary["failed"] >= 1

    def test_listed_desktops_are_not_read_again(self):
        _summary_, _revoke, _persist, _pager, client_factory, _delete = _run(
            [_row("a")], [_desktop("sess-a")], states=[])
        client_factory.return_value.get_desktop_states.assert_not_called()

