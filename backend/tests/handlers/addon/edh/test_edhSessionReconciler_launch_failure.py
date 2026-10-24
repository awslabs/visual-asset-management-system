# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The session sweep captures the reason EDH recorded for a failed launch (in-dev D8).

A row the sweep records ORPHANED -- EDH lists its desktop in ``error``, or EDH no longer has the desktop of a
launch still CREATING -- or finds ORPHANED with a session id is given the failure EDH's per-user listing
records for that session, read as the row's own EDH uid since that listing is owner-scoped: ``edhFailure``
is stored and a generic ``lastError`` reclassified, a code the launch recorded kept. The read is made at
most once per row per five minutes and never once a reason is stored; one that fails writes only its
stamp and counts as failed. The write is conditioned on the row as this pass left it, so a row another
writer reached first is not written.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest

from backend.backend.handlers.addon.edh import edhSessionReconciler as rec
from backend.backend.handlers.addon.edh.common.edhClient import EDH_SERVICE_ACTOR_UID, EdhUnavailableError
from backend.backend.handlers.addon.edh.common.edhSessionState import (
    LAST_ERROR_CAPACITY_UNAVAILABLE, LAST_ERROR_LAUNCH_ERROR, LAST_ERROR_QUOTA_EXCEEDED,
)
from backend.tests.handlers.addon.edh.test_edhSessionReconciler import (
    NOW, SEEN, STAMP_WRITTEN, _conditions_for, _context, _desktop, _row, _run, _summary, _updates_for,
    _written_for,
)

OWNER_UID = "vownerabcdef2345"
CAPACITY_FAILURE = {"event": "placement_failed", "errorCode": "InsufficientInstanceCapacity",
                    "operation": "CreateCapacityReservation"}
QUOTA_FAILURE = {"event": "placement_failed", "errorCode": "VcpuLimitExceeded", "operation": "RunInstances"}


def _failure_reads(client_factory):
    return [call.args for call in client_factory.return_value.get_desktop_failure.call_args_list]


def _orphaned(bootstrap_id, **overrides):
    """A row a pass already recorded ORPHANED for EDH's error, its credential already taken back."""
    return _row(bootstrap_id, **{"state": rec.STATE_ORPHANED, "edhState": "error", "connectionAvailable": False,
                                 "lastError": LAST_ERROR_LAUNCH_ERROR, "apiKeyId": "", "secretName": "",
                                 "keyExpiresAt": "", "edhUid": OWNER_UID, **overrides})


def _capture_writes(persist, bootstrap_id):
    return [updates for updates in _updates_for(persist, bootstrap_id) if "edhFailureReadAt" in updates]


@pytest.mark.unit
class TestTheSweepCapturesAFailedLaunch:
    def test_a_desktop_edh_lists_in_error_is_given_its_reason_read_as_the_rows_owner(self):
        row = _row("b", edhUid=OWNER_UID)

        summary, revoke_fn, persist, _pager, client_factory, _delete = _run(
            [row], [_desktop("sess-b", "error")], failure=CAPACITY_FAILURE)

        assert _failure_reads(client_factory) == [(OWNER_UID, "sess-b")]
        written = _written_for(persist, "b")
        assert written["state"] == rec.STATE_ORPHANED
        assert written["lastError"] == LAST_ERROR_CAPACITY_UNAVAILABLE
        assert written["edhFailure"] == {**CAPACITY_FAILURE, "observedAt": NOW.isoformat()}
        assert written["edhFailureReadAt"] == NOW.isoformat()
        revoke_fn.assert_called()
        assert summary == _summary(examined=1, updated=1, revoked=1, failuresCaptured=1)

    def test_the_read_never_names_the_service_actor(self):
        _summary_out, _revoke, _persist, _pager, client_factory, _delete = _run(
            [_row("b", edhUid=OWNER_UID)], [_desktop("sess-b", "error")], failure=CAPACITY_FAILURE)

        reads = _failure_reads(client_factory)
        assert reads, "the failure was never read"
        assert EDH_SERVICE_ACTOR_UID not in {uid for uid, _session in reads}

    def test_a_launch_edh_no_longer_has_is_given_its_reason(self):
        creating = _row("c", state=rec.STATE_CREATING, edhState="placing", connectionAvailable=False,
                        edhUid=OWNER_UID)

        summary, _revoke, persist, _pager, client_factory, _delete = _run(
            [creating], [], states=[], failure=QUOTA_FAILURE)

        assert _failure_reads(client_factory) == [(OWNER_UID, "sess-c")]
        written = _written_for(persist, "c")
        assert (written["state"], written["lastError"]) == (rec.STATE_ORPHANED, LAST_ERROR_QUOTA_EXCEEDED)
        assert written["edhFailure"]["errorCode"] == "VcpuLimitExceeded"
        assert summary["failuresCaptured"] == 1

    def test_a_code_the_launch_recorded_is_kept_beside_the_reason(self):
        creating = _row("c", state=rec.STATE_CREATING, edhState="placing", connectionAvailable=False,
                        lastError="EDH_REFUSED")

        _summary_out, _revoke, persist, *_rest = _run([creating], [], states=[], failure=CAPACITY_FAILURE)

        (capture,) = _capture_writes(persist, "c")
        assert "lastError" not in capture
        assert capture["edhFailure"]["errorCode"] == "InsufficientInstanceCapacity"
        # The code the launch recorded is never rewritten on this pass.
        assert "lastError" not in _written_for(persist, "c")
        assert _written_for(persist, "c")["state"] == rec.STATE_ORPHANED

    def test_a_row_already_orphaned_is_given_its_reason_under_the_rows_own_condition(self):
        summary, revoke_fn, persist, _pager, client_factory, _delete = _run(
            [_orphaned("o")], [_desktop("sess-o", "error")], failure=CAPACITY_FAILURE)

        assert _failure_reads(client_factory) == [(OWNER_UID, "sess-o")]
        assert _written_for(persist, "o")["lastError"] == LAST_ERROR_CAPACITY_UNAVAILABLE
        # Nothing else was written on this pass, so the capture is conditioned on the row as it was read.
        assert _conditions_for(persist, "o") == [SEEN]
        revoke_fn.assert_not_called()
        assert summary == _summary(examined=1, failuresCaptured=1)

    def test_after_a_revoking_write_the_capture_is_conditioned_on_that_write(self):
        _summary_out, _revoke, persist, *_rest = _run(
            [_row("b")], [_desktop("sess-b", "error")], failure=CAPACITY_FAILURE)

        conditions = _conditions_for(persist, "b")
        assert conditions, "nothing was written"
        assert conditions[-1] == STAMP_WRITTEN
        assert "edhFailureReadAt" in _updates_for(persist, "b")[-1]

    def test_a_read_that_fails_writes_only_its_stamp_and_counts_as_failed(self):
        summary, _revoke, persist, *_rest = _run(
            [_orphaned("o")], [_desktop("sess-o", "error")], failure=EdhUnavailableError("read timeout"))

        assert _updates_for(persist, "o") == [{"edhFailureReadAt": NOW.isoformat()}]
        assert summary == _summary(examined=1, failed=1)

    def test_a_read_that_finds_no_reason_writes_only_its_stamp(self):
        summary, _revoke, persist, *_rest = _run([_orphaned("o")], [_desktop("sess-o", "error")], failure=None)

        assert _updates_for(persist, "o") == [{"edhFailureReadAt": NOW.isoformat()}]
        assert summary == _summary(examined=1)

    def test_a_row_read_inside_the_last_five_minutes_is_not_read_again(self):
        row = _orphaned("o", edhFailureReadAt=(NOW - timedelta(minutes=4)).isoformat())

        _summary_out, _revoke, persist, _pager, client_factory, _delete = _run(
            [row], [_desktop("sess-o", "error")], failure=CAPACITY_FAILURE)

        assert _failure_reads(client_factory) == []
        assert _updates_for(persist, "o") == []

    def test_a_row_read_more_than_five_minutes_ago_is_read_again(self):
        # Control for the case above.
        row = _orphaned("o", edhFailureReadAt=(NOW - timedelta(minutes=6)).isoformat())

        _summary_out, _revoke, _persist, _pager, client_factory, _delete = _run(
            [row], [_desktop("sess-o", "error")], failure=CAPACITY_FAILURE)

        assert _failure_reads(client_factory) == [(OWNER_UID, "sess-o")]

    def test_a_row_that_holds_a_reason_is_never_read_again(self):
        row = _orphaned("o", lastError=LAST_ERROR_CAPACITY_UNAVAILABLE,
                        edhFailure={**CAPACITY_FAILURE, "observedAt": (NOW - timedelta(days=1)).isoformat()})

        _summary_out, _revoke, persist, _pager, client_factory, _delete = _run(
            [row], [_desktop("sess-o", "error")], failure=QUOTA_FAILURE)

        assert _failure_reads(client_factory) == []
        assert _updates_for(persist, "o") == []

    def test_a_running_desktop_is_never_read_for_a_failure(self):
        _summary_out, _revoke, _persist, _pager, client_factory, _delete = _run(
            [_row("a")], [_desktop("sess-a")], failure=CAPACITY_FAILURE)

        assert _failure_reads(client_factory) == []

    def test_a_capture_another_writer_overtook_is_deferred_and_not_counted(self):
        def persist(bootstrap_id, updates, seen, *, touch=True):
            return None if "edhFailureReadAt" in updates else STAMP_WRITTEN

        summary, _revoke, recorded, *_rest = _run(
            [_orphaned("o")], [_desktop("sess-o", "error")], failure=CAPACITY_FAILURE, persist=persist)

        assert _capture_writes(recorded, "o"), "the capture was never attempted"
        assert summary == _summary(examined=1, deferred=1)


@pytest.mark.unit
class TestTheSweepsAllowanceOfFailureReads:
    def test_reads_past_the_allowance_are_left_unstamped_for_a_later_sweep(self):
        rows = [_orphaned("o1"), _orphaned("o2"), _orphaned("o3")]
        desktops = [_desktop("sess-o1", "error"), _desktop("sess-o2", "error"), _desktop("sess-o3", "error")]

        with patch.object(rec, "MAX_FAILURE_READS_PER_SWEEP", 2):
            summary, _revoke, persist, _pager, client_factory, _delete = _run(rows, desktops, failure=CAPACITY_FAILURE)

        read = {session for _uid, session in _failure_reads(client_factory)}
        assert 0 < len(read) <= 2
        unread = [f"o{n}" for n in (1, 2, 3) if f"sess-o{n}" not in read]
        assert unread, "every row was read past the allowance"
        for bootstrap_id in unread:
            assert _capture_writes(persist, bootstrap_id) == []
        assert summary["failuresCaptured"] == len(read)

    def test_no_read_starts_inside_the_invocations_time_floor(self):
        summary, _revoke, persist, _pager, client_factory, _delete = _run(
            [_orphaned("o")], [_desktop("sess-o", "error")], failure=CAPACITY_FAILURE,
            context=_context(rec.FAILURE_READ_TIME_FLOOR_SECONDS - 1))

        assert _failure_reads(client_factory) == []
        assert _capture_writes(persist, "o") == []
        assert summary["failuresCaptured"] == 0

    def test_a_read_starts_with_time_to_spare(self):
        # Control for the case above.
        _summary_out, _revoke, _persist, _pager, client_factory, _delete = _run(
            [_orphaned("o")], [_desktop("sess-o", "error")], failure=CAPACITY_FAILURE,
            context=_context(rec.FAILURE_READ_TIME_FLOOR_SECONDS + 600))

        assert _failure_reads(client_factory) == [(OWNER_UID, "sess-o")]
