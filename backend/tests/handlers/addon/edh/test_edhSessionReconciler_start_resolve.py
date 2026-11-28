# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The session sweep resolves a start the launch worker could not: a STOPPED row still marked ``activity``
starting whose stamp is ``START_RESOLVE_SECONDS`` old, and whose desktop EDH lists stopped, records START_FAILED,
loses its mark and stamp and has the start's key revoked -- and stays STOPPED: the sweep never deletes a row, or
its desktop, because a start failed. EDH reporting the desktop pending or running settles the row as any read
does."""

from datetime import timedelta

import pytest

from backend.backend.handlers.addon.edh import edhSessionReconciler as rec
from backend.backend.handlers.addon.edh.common.edhSessionState import START_RESOLVE_SECONDS, start_stamp
from backend.tests.handlers.addon.edh.test_edhSessionReconciler import (
    NOW, _desktop, _row, _run, _summary, _written_for,
)


def _unresolved(bootstrap_id, seconds_ago, **overrides):
    """A STOPPED row a start left marked and stamped ``seconds_ago``, its key kept."""
    stamp = start_stamp(NOW - timedelta(seconds=seconds_ago))
    return _row(bootstrap_id, state=rec.STATE_STOPPED, edhState="stopped", connectionAvailable=False,
                activity="starting", startRequestedAt=stamp, updatedAt=stamp, **overrides)


@pytest.mark.unit
class TestTheSweepResolvesAStartTheWorkerCouldNot:
    def test_a_stale_start_edh_lists_stopped_is_recorded_failed_and_its_key_revoked(self):
        summary, revoke_fn, persist, _pager, client_factory, _ = _run(
            [_unresolved("a", START_RESOLVE_SECONDS + 30)], [_desktop("sess-a", "stopped")])
        written = _written_for(persist, "a")
        assert written["lastError"] == "START_FAILED"
        assert written["activity"] is None
        assert written["startRequestedAt"] is None
        assert written["startFailure"] == {"observedAt": NOW.isoformat()}
        assert {c.args[1]["bootstrapId"]: c.kwargs["delete_secret"] for c in revoke_fn.call_args_list} == {"a": False}
        assert summary == _summary(examined=1, updated=1, revoked=1)

    def test_the_row_is_never_deleted_for_a_failed_start(self):
        summary, _revoke, persist, _pager, client_factory, _ = _run(
            [_unresolved("a", START_RESOLVE_SECONDS + 30)], [_desktop("sess-a", "stopped")])
        for updates in [c.args[1] for c in persist.call_args_list]:
            assert updates.get("state", rec.STATE_STOPPED) == rec.STATE_STOPPED
            assert "expiresAt" not in updates
        client = client_factory.return_value
        client.delete_desktop.assert_not_called()
        client.stop_desktop.assert_not_called()
        client.start_desktop.assert_not_called()

    def test_a_start_inside_its_window_is_left_alone(self):
        summary, revoke_fn, persist, *_ = _run([_unresolved("a", 200)], [_desktop("sess-a", "stopped")])
        assert "lastError" not in _written_for(persist, "a")
        revoke_fn.assert_not_called()

    def test_a_start_past_the_window_but_short_of_the_resolve_age_is_not_recorded_failed(self):
        _summary_, _revoke, persist, *_ = _run([_unresolved("a", START_RESOLVE_SECONDS - 60)],
                                               [_desktop("sess-a", "stopped")])
        assert "lastError" not in _written_for(persist, "a")
        assert "activity" not in _written_for(persist, "a")

    @pytest.mark.parametrize("edh_state, mapped", [("pending", rec.STATE_CREATING), ("running", rec.STATE_ACTIVE)])
    def test_a_desktop_edh_lists_started_settles_the_row_as_any_read_does(self, edh_state, mapped):
        _summary_, revoke_fn, persist, *_ = _run([_unresolved("a", START_RESOLVE_SECONDS + 30)],
                                                 [_desktop("sess-a", edh_state)])
        written = _written_for(persist, "a")
        assert written["state"] == mapped
        assert written.get("lastError") != "START_FAILED"
        revoke_fn.assert_not_called()

    def test_a_row_without_the_mark_is_not_a_failed_start(self):
        row = _unresolved("a", START_RESOLVE_SECONDS + 30)
        row.pop("activity")
        _summary_, _revoke, persist, *_ = _run([row], [_desktop("sess-a", "stopped")])
        assert "lastError" not in _written_for(persist, "a")

    def test_the_launch_workers_timeout_is_the_attempt_settle(self):
        assert rec.WORKER_ATTEMPT_SETTLE_SECONDS == 360
