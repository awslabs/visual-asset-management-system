# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The session sweep orphans only a new launch that never ran (#1731).

A workstation that has run and that EDH lists in ``error`` (its Amazon DCV health check failed) keeps its state, its
key and its secret, is recorded not connectable with ``DESKTOP_ERROR``, and is given no failed-launch read; its key's
own expiry still revokes it. A start EDH accepted (``startAcceptedAt``) whose desktop EDH no longer lists is recorded
DELETED with the unlisted stamp, as an ACTIVE or STOPPED one is, not ORPHANED. A new launch in error, or one EDH no
longer has, is ORPHANED as before."""

from datetime import timedelta

import pytest

from backend.backend.handlers.addon.edh import edhSessionReconciler as rec
from backend.backend.handlers.addon.edh.common.edhSessionState import start_stamp
from backend.tests.handlers.addon.edh.test_edhSessionReconciler import (
    BOOTSTRAP, EXPIRED_KEY, NOW, _desktop, _index, _named, _row, _run, _summary, _written_for,
)


def _accepted(bootstrap_id, **overrides):
    stamp = start_stamp(NOW - timedelta(minutes=10))
    return _row(bootstrap_id, **{"state": rec.STATE_CREATING, "edhState": "pending", "connectionAvailable": False,
                                 "startAcceptedAt": stamp, "updatedAt": stamp, **overrides})


@pytest.mark.unit
class TestADesktopThatHasRunInError:
    def test_an_active_desktop_keeps_its_state_key_and_secret(self):
        summary, revoke_fn, persist, _pager, client_factory, _delete = _run([_row("a")], [_desktop("sess-a", "error")])
        written = _written_for(persist, "a")
        assert written == {"edhState": "error", "connectionAvailable": False,
                           "connectionUnavailableReason": "DESKTOP_ERROR"}
        revoke_fn.assert_not_called()
        client_factory.return_value.get_desktop_failure.assert_not_called()
        assert summary == _summary(examined=1, updated=1)

    def test_a_stopped_desktop_keeps_its_secret_container(self):
        row = _row("s", state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False, apiKeyId="")
        _summary_, revoke_fn, persist, *_ = _run([row], [_desktop("sess-s", "error")])
        assert "state" not in _written_for(persist, "s")
        revoke_fn.assert_not_called()

    def test_an_accepted_start_keeps_creating_and_its_key(self):
        _summary_, revoke_fn, persist, *_ = _run([_accepted("c")], [_desktop("sess-c", "error")])
        written = _written_for(persist, "c")
        assert "state" not in written and "startAcceptedAt" not in written
        assert written["connectionUnavailableReason"] == "DESKTOP_ERROR"
        revoke_fn.assert_not_called()

    def test_its_key_past_its_expiry_is_still_revoked_and_the_secret_kept(self):
        _summary_, revoke_fn, _persist, *_ = _run([_row("a", keyExpiresAt=EXPIRED_KEY)],
                                                  [_desktop("sess-a", "error")])
        revoke_fn.assert_called_once()
        assert revoke_fn.call_args.kwargs["delete_secret"] is False

    def test_edh_recovering_it_makes_it_connectable_again(self):
        row = _row("a", edhState="error", connectionAvailable=False, connectionUnavailableReason="DESKTOP_ERROR")
        _summary_, revoke_fn, persist, *_ = _run([row], [_desktop("sess-a", "running")])
        written = _written_for(persist, "a")
        assert written["connectionAvailable"] is True and written["connectionUnavailableReason"] is None
        revoke_fn.assert_not_called()


@pytest.mark.unit
class TestANewLaunchStillFails:
    def test_a_new_launch_in_error_is_orphaned_its_credential_removed_and_its_reason_read(self):
        row = _row("n", state=rec.STATE_CREATING, edhState="placing", connectionAvailable=False)
        summary, revoke_fn, persist, _pager, client_factory, _delete = _run([row], [_desktop("sess-n", "error")])
        written = _written_for(persist, "n")
        assert written["state"] == rec.STATE_ORPHANED and written["lastError"] == "LAUNCH_ERROR"
        assert revoke_fn.call_args.kwargs["delete_secret"] is True
        client_factory.return_value.get_desktop_failure.assert_called_once()

    def test_a_new_launch_edh_no_longer_has_is_orphaned(self):
        row = _row("n", state=rec.STATE_CREATING, edhState="placing", connectionAvailable=False)
        _summary_, _revoke, persist, *_ = _run([row], [], states=[])
        written = _written_for(persist, "n")
        assert written["state"] == rec.STATE_ORPHANED
        assert rec.EDH_UNLISTED_AT_FIELD not in written


@pytest.mark.unit
class TestAnAcceptedStartEdhNoLongerLists:
    def test_it_is_recorded_deleted_with_the_unlisted_stamp_and_its_credential_removed(self):
        summary, revoke_fn, persist, *_ = _run([_accepted("c")], [], states=[])
        written = _written_for(persist, "c")
        assert written["state"] == rec.STATE_DELETED
        assert written[rec.EDH_UNLISTED_AT_FIELD] == NOW.isoformat()
        assert written["startAcceptedAt"] is None
        assert "lastError" not in written or written["lastError"] is None
        assert revoke_fn.call_args.kwargs["delete_secret"] is True

    def test_it_waits_for_the_state_read_to_confirm_it_gone(self):
        summary, revoke_fn, persist, *_ = _run([_accepted("c")], [], states=[_desktop("sess-c", "pending")])
        written = _written_for(persist, "c")
        assert written.get("state", rec.STATE_CREATING) == rec.STATE_CREATING
        revoke_fn.assert_not_called()

    def test_a_save_and_resume_re_adopts_it(self):
        deleted = _row(BOOTSTRAP, state=rec.STATE_DELETED, edhUnlistedAt=(NOW - timedelta(minutes=5)).isoformat(),
                       apiKeyId="", secretName="", keyExpiresAt="", connectionAvailable=False)
        updates, _revoke, _delete = rec.reconcile_row(deleted, _index(_named("sess-new", state="error")), NOW)
        assert updates["state"] == rec.STATE_ACTIVE
        assert updates["sessionId"] == "sess-new"
        assert updates["connectionUnavailableReason"] == "DESKTOP_ERROR"
