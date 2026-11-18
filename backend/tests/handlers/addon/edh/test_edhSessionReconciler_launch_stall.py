# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The session sweep records a new launch EDH never places: a CREATING row with a session id, no accepted start
and no start mark, made three hours ago, whose desktop EDH still lists pending or placing, records
``LAUNCH_STALLED`` with a ``startFailure`` that names nothing EDH said -- and nothing else happens to it: the key is
kept and the desktop is neither stopped, deleted nor orphaned. A younger launch, a start, and a desktop EDH lists
running are never recorded a stalled launch."""

from datetime import timedelta

import pytest

from backend.backend.handlers.addon.edh import edhSessionReconciler as rec
from backend.tests.handlers.addon.edh.test_edhSessionReconciler import (
    NOW, _desktop, _row, _run, _summary, _written_for,
)

# Three hours, the threshold the contract fixes.
LAUNCH_STALL_SECONDS = 3 * 3600


def _launch_row(bootstrap_id, seconds_ago, **overrides):
    """A CREATING row of a new launch EDH lists, made ``seconds_ago`` and last written a minute later."""
    made = NOW - timedelta(seconds=seconds_ago)
    return _row(bootstrap_id, state=rec.STATE_CREATING, edhState="pending", connectionAvailable=False,
                createdAt=made.isoformat(), updatedAt=(made + timedelta(minutes=1)).isoformat(), **overrides)


@pytest.mark.unit
class TestTheSweepRecordsAStalledLaunch:
    @pytest.mark.parametrize("edh_state", ["pending", "placing"])
    def test_a_launch_edh_still_lists_booting_after_three_hours_is_recorded_stalled(self, edh_state):
        summary, _revoke, persist, *_ = _run([_launch_row("a", LAUNCH_STALL_SECONDS + 60)],
                                             [_desktop("sess-a", edh_state)])
        written = _written_for(persist, "a")
        assert written["lastError"] == "LAUNCH_STALLED"
        assert written["startFailure"] == {"observedAt": NOW.isoformat()}
        assert written.get("state", rec.STATE_CREATING) == rec.STATE_CREATING
        assert summary == _summary(examined=1, updated=1)

    def test_the_stall_keeps_the_key_and_touches_nothing_in_edh(self):
        _summary_, revoke_fn, persist, _pager, client_factory, delete_fn = _run(
            [_launch_row("a", LAUNCH_STALL_SECONDS + 60)], [_desktop("sess-a", "pending")])
        assert _written_for(persist, "a")["lastError"] == "LAUNCH_STALLED"
        revoke_fn.assert_not_called()
        delete_fn.assert_not_called()
        client = client_factory.return_value
        client.stop_desktop.assert_not_called()
        client.delete_desktop.assert_not_called()
        written = _written_for(persist, "a")
        assert "apiKeyId" not in written and "secretName" not in written and "expiresAt" not in written

    def test_a_row_recorded_stalled_is_not_written_again(self):
        row = _launch_row("a", LAUNCH_STALL_SECONDS + 600, lastError="LAUNCH_STALLED",
                          startFailure={"observedAt": (NOW - timedelta(minutes=10)).isoformat()})
        summary, _revoke, persist, *_ = _run([row], [_desktop("sess-a", "pending")])
        written = _written_for(persist, "a")
        assert "lastError" not in written and "startFailure" not in written
        assert summary["updated"] == 0


@pytest.mark.unit
class TestTheSweepRecordsNoLaunchStallOtherwise:
    def test_a_first_boot_two_hours_in_is_left_booting(self):
        _summary_, _revoke, persist, *_ = _run([_launch_row("a", 2 * 3600)], [_desktop("sess-a", "pending")])
        assert "lastError" not in _written_for(persist, "a")

    def test_a_start_whose_stamp_was_not_written_is_not_a_stalled_launch(self):
        # A start carries the start mark until the desktop runs, whatever the row's age.
        row = _launch_row("a", 10 * LAUNCH_STALL_SECONDS, activity="starting")
        _summary_, _revoke, persist, *_ = _run([row], [_desktop("sess-a", "pending")])
        assert "lastError" not in _written_for(persist, "a")

    def test_a_stalled_launch_edh_places_is_cleared(self):
        row = _launch_row("a", LAUNCH_STALL_SECONDS + 600, lastError="LAUNCH_STALLED",
                          startFailure={"observedAt": (NOW - timedelta(minutes=10)).isoformat()})
        _summary_, revoke_fn, persist, *_ = _run([row], [_desktop("sess-a", "running")])
        written = _written_for(persist, "a")
        assert written["state"] == rec.STATE_ACTIVE
        assert written["lastError"] is None and written["startFailure"] is None
        revoke_fn.assert_not_called()

    def test_a_stalled_launch_whose_desktop_edh_no_longer_has_is_a_failed_launch(self):
        row = _launch_row("a", LAUNCH_STALL_SECONDS + 600, lastError="LAUNCH_STALLED",
                          startFailure={"observedAt": (NOW - timedelta(minutes=10)).isoformat()})
        _summary_, _revoke, persist, *_ = _run([row], [], states=[])
        written = _written_for(persist, "a")
        assert written["state"] == rec.STATE_ORPHANED
        assert written["lastError"] == "LAUNCH_ERROR"
        assert written["startFailure"] is None
