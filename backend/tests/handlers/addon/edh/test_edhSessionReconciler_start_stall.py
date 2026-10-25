# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The session sweep records a start EDH accepted and never finished (in-dev D16): a CREATING row whose
``startAcceptedAt`` is half an hour old, and whose desktop EDH still lists pending or placing, records
``START_STALLED`` with a ``startFailure`` that names nothing EDH said -- and nothing else happens to it: the key is
kept, the desktop is neither stopped, deleted nor orphaned, and the write is conditioned on the row as read, so a
concurrent change wins. A new launch, a desktop EDH lists running or stopped, and a start younger than the
threshold are never recorded stalled."""

from datetime import timedelta

import pytest

from backend.backend.handlers.addon.edh import edhSessionReconciler as rec
from backend.backend.handlers.addon.edh.common.edhSessionState import start_stamp
from backend.tests.handlers.addon.edh.test_edhSessionReconciler import (
    NOW, _conditions_for, _desktop, _row, _run, _summary, _written_for,
)

# Half an hour, the threshold the contract fixes.
STALL_SECONDS = 1800


def _accepted_row(bootstrap_id, seconds_ago, **overrides):
    """A CREATING row a start EDH accepted ``seconds_ago`` left, its key kept, last written then."""
    stamp = start_stamp(NOW - timedelta(seconds=seconds_ago))
    return _row(bootstrap_id, state=rec.STATE_CREATING, edhState="pending", connectionAvailable=False,
                activity="starting", startAcceptedAt=stamp, updatedAt=stamp, **overrides)


@pytest.mark.unit
class TestTheSweepRecordsAStalledStart:
    @pytest.mark.parametrize("edh_state", ["pending", "placing"])
    def test_a_start_edh_still_lists_booting_after_half_an_hour_is_recorded_stalled(self, edh_state):
        summary, _revoke, persist, *_ = _run([_accepted_row("a", STALL_SECONDS + 60)],
                                             [_desktop("sess-a", edh_state)])
        written = _written_for(persist, "a")
        assert written["lastError"] == "START_STALLED"
        assert written["startFailure"] == {"observedAt": NOW.isoformat()}
        assert written.get("state", rec.STATE_CREATING) == rec.STATE_CREATING
        assert summary == _summary(examined=1, updated=1)

    def test_the_stall_keeps_the_key_and_touches_nothing_in_edh(self):
        _summary_, revoke_fn, persist, _pager, client_factory, delete_fn = _run(
            [_accepted_row("a", STALL_SECONDS + 60)], [_desktop("sess-a", "pending")])
        assert _written_for(persist, "a")["lastError"] == "START_STALLED"
        revoke_fn.assert_not_called()
        delete_fn.assert_not_called()
        client = client_factory.return_value
        client.stop_desktop.assert_not_called()
        client.delete_desktop.assert_not_called()
        client.start_desktop.assert_not_called()
        written = _written_for(persist, "a")
        assert "apiKeyId" not in written and "secretName" not in written and "expiresAt" not in written

    def test_the_stall_is_written_on_the_row_as_the_sweep_read_it(self):
        row = _accepted_row("a", STALL_SECONDS + 60)
        _summary_, _revoke, persist, *_ = _run([row], [_desktop("sess-a", "pending")])
        conditions = _conditions_for(persist, "a")
        assert conditions, "the stall was not written"
        assert all(condition == row["updatedAt"] for condition in conditions)

    def test_a_concurrent_change_wins_and_nothing_else_follows(self):
        summary, revoke_fn, persist, *_ = _run([_accepted_row("a", STALL_SECONDS + 60)],
                                               [_desktop("sess-a", "pending")],
                                               persist=lambda *args, **kwargs: None)
        assert summary == _summary(examined=1, deferred=1)
        revoke_fn.assert_not_called()

    def test_a_row_recorded_stalled_is_not_written_again(self):
        row = _accepted_row("a", STALL_SECONDS + 600, lastError="START_STALLED",
                            startFailure={"observedAt": (NOW - timedelta(minutes=10)).isoformat()})
        summary, _revoke, persist, *_ = _run([row], [_desktop("sess-a", "pending")])
        written = _written_for(persist, "a")
        assert "lastError" not in written and "startFailure" not in written
        assert summary["updated"] == 0


@pytest.mark.unit
class TestTheSweepRecordsNoStallOtherwise:
    def test_a_start_younger_than_half_an_hour_is_left_booting(self):
        _summary_, _revoke, persist, *_ = _run([_accepted_row("a", 29 * 60)], [_desktop("sess-a", "pending")])
        assert "lastError" not in _written_for(persist, "a")

    def test_a_new_launch_is_never_a_stalled_start(self):
        row = _accepted_row("a", 3 * STALL_SECONDS)
        row.pop("startAcceptedAt")
        _summary_, _revoke, persist, *_ = _run([row], [_desktop("sess-a", "pending")])
        assert "lastError" not in _written_for(persist, "a")

    def test_a_desktop_edh_lists_running_settles_the_row_and_clears_the_stamp(self):
        _summary_, revoke_fn, persist, *_ = _run([_accepted_row("a", STALL_SECONDS + 60)],
                                                 [_desktop("sess-a", "running")])
        written = _written_for(persist, "a")
        assert written["state"] == rec.STATE_ACTIVE
        assert written.get("lastError") != "START_STALLED"
        assert written["startAcceptedAt"] is None
        revoke_fn.assert_not_called()

    def test_a_desktop_edh_lists_stopped_is_not_a_stalled_start(self):
        _summary_, _revoke, persist, *_ = _run([_accepted_row("a", STALL_SECONDS + 60)],
                                               [_desktop("sess-a", "stopped")])
        written = _written_for(persist, "a")
        assert written["state"] == rec.STATE_STOPPED
        assert written.get("lastError") != "START_STALLED"
        assert written["startAcceptedAt"] is None

    def test_a_stalled_start_edh_moves_on_is_cleared(self):
        row = _accepted_row("a", STALL_SECONDS + 600, lastError="START_STALLED",
                            startFailure={"observedAt": (NOW - timedelta(minutes=10)).isoformat()})
        _summary_, _revoke, persist, *_ = _run([row], [_desktop("sess-a", "running")])
        written = _written_for(persist, "a")
        assert written["state"] == rec.STATE_ACTIVE
        assert written["lastError"] is None and written["startFailure"] is None
        assert written["startAcceptedAt"] is None

    def test_a_stalled_start_whose_desktop_edh_no_longer_has_is_a_failed_launch_not_a_stall(self):
        row = _accepted_row("a", STALL_SECONDS + 600, lastError="START_STALLED",
                            startFailure={"observedAt": (NOW - timedelta(minutes=10)).isoformat()})
        _summary_, _revoke, persist, *_ = _run([row], [], states=[])
        written = _written_for(persist, "a")
        assert written["state"] == rec.STATE_ORPHANED
        assert written["lastError"] == "LAUNCH_ERROR"
        assert written["startFailure"] is None and written["startAcceptedAt"] is None
