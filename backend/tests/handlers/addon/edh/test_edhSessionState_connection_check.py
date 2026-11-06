# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""When the list route asks EDH whether a running desktop's display session can still be connected to (D21).

The token-free state read the list and get routes make carries no connection link, so it cannot see a DCV session
that ended while the desktop kept running. Those routes therefore also make the read Connect makes, EDH's per-user
listing narrowed to the one session, for an ACTIVE row with a session id whose desktop EDH reports running. The get
route makes it every time; the list route only for a row whose last such check (``connectionCheckedAt``) is absent
or ``CONNECTION_CHECK_INTERVAL_SECONDS`` old, oldest first, at most ``CONNECTION_CHECKS_PER_LIST`` per request.
"""

from datetime import datetime, timedelta, timezone

import pytest

import backend.backend.handlers.addon.edh.common.edhSessionState as state

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)


def _running(**overrides):
    row = {"bootstrapId": "b-1", "sessionId": "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b", "state": "ACTIVE",
           "edhState": "running", "connectionAvailable": True}
    row.update(overrides)
    return row


def _checked(seconds_ago, **overrides):
    return _running(connectionCheckedAt=(NOW - timedelta(seconds=seconds_ago)).isoformat(), **overrides)


@pytest.mark.unit
class TestTheCheckSettings:
    def test_the_field_interval_and_cap(self):
        assert state.CONNECTION_CHECKED_AT_FIELD == "connectionCheckedAt"
        assert state.CONNECTION_CHECK_INTERVAL_SECONDS == 300
        assert state.CONNECTION_CHECKS_PER_LIST == 2


@pytest.mark.unit
class TestWhichRowsTheCheckAppliesTo:
    @pytest.mark.parametrize("overrides", [{}, {"edhState": "RUNNING"}, {"connectionAvailable": False},
                                           {"connectionUnavailableReason": "DISPLAY_SESSION_ENDED"}],
                             ids=["connectable", "upper case", "not connectable", "recorded ended"])
    def test_an_active_running_row_with_a_session(self, overrides):
        assert state.connection_check_applies(_running(**overrides)) is True

    @pytest.mark.parametrize("overrides", [
        {"state": "STOPPED", "edhState": "stopped"}, {"state": "CREATING", "edhState": "pending"},
        {"state": "ORPHANED", "edhState": "error"}, {"state": "DELETED"}, {"edhState": "stopping"},
        {"edhState": None}, {"sessionId": None}, {"sessionId": ""},
    ], ids=["stopped", "creating", "orphaned", "deleted", "stopping", "no edh state", "no session", "blank session"])
    def test_no_other_row(self, overrides):
        assert state.connection_check_applies(_running(**overrides)) is False


@pytest.mark.unit
class TestWhenARowIsDueACheck:
    def test_a_row_never_checked_is_due(self):
        assert state.connection_check_due(_running(), NOW) is True

    @pytest.mark.parametrize("seconds_ago", [0, 60, 299])
    def test_a_check_stamped_inside_the_interval_holds_the_next_one_off(self, seconds_ago):
        assert state.connection_check_due(_checked(seconds_ago), NOW) is False

    @pytest.mark.parametrize("seconds_ago", [300, 301, 3600])
    def test_a_check_stamped_at_or_past_the_interval_does_not(self, seconds_ago):
        assert state.connection_check_due(_checked(seconds_ago), NOW) is True

    def test_a_stamp_without_an_offset_is_read_as_utc(self):
        stamp = (NOW - timedelta(seconds=60)).replace(tzinfo=None).isoformat()
        assert state.connection_check_due(_running(connectionCheckedAt=stamp), NOW) is False

    @pytest.mark.parametrize("stamp", [None, "", "yesterday", 12, (NOW + timedelta(hours=1)).isoformat()],
                             ids=["none", "blank", "malformed", "a number", "far ahead"])
    def test_a_stamp_that_cannot_hold_a_check_off_does_not(self, stamp):
        assert state.connection_check_due(_running(connectionCheckedAt=stamp), NOW) is True

    def test_a_row_the_check_does_not_apply_to_is_never_due(self):
        assert state.connection_check_due(_running(state="STOPPED", edhState="stopped"), NOW) is False

    def test_now_defaults_to_the_current_instant(self):
        fresh = _running(connectionCheckedAt=datetime.now(timezone.utc).isoformat())
        assert state.connection_check_due(fresh) is False


@pytest.mark.unit
class TestWhichRowsOneListChecks:
    def test_at_most_two_oldest_first_never_checked_ahead(self):
        rows = [_checked(400, bootstrapId="b-400"), _checked(900, bootstrapId="b-900"),
                _running(bootstrapId="b-never"), _checked(301, bootstrapId="b-301")]

        due = state.connection_checks_due(rows, NOW)

        assert [row["bootstrapId"] for row in due] == ["b-never", "b-900"]

    def test_a_malformed_stamp_counts_as_never_checked(self):
        rows = [_checked(900, bootstrapId="b-900"), _running(bootstrapId="b-bad", connectionCheckedAt="yesterday")]

        assert [row["bootstrapId"] for row in state.connection_checks_due(rows, NOW)] == ["b-bad", "b-900"]

    def test_rows_not_due_are_left_out(self):
        rows = [_checked(10, bootstrapId="b-fresh"), _running(bootstrapId="b-stopped", state="STOPPED",
                                                              edhState="stopped"),
                _running(bootstrapId="b-no-session", sessionId=None), _checked(600, bootstrapId="b-stale")]

        assert [row["bootstrapId"] for row in state.connection_checks_due(rows, NOW)] == ["b-stale"]

    def test_none_due_is_empty(self):
        assert state.connection_checks_due([_checked(10)], NOW) == []
