# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from datetime import datetime, timedelta, timezone

import pytest

from backend.backend.handlers.addon.edh.common import edhLeasePolicy as lp
from backend.backend.handlers.addon.edh.common.edhSessionState import STATE_ACTIVE, STATE_STOPPED, STATE_DELETED


def _now():
    return datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)


def test_settings_key_cannot_collide_with_a_uuid_bootstrap_id():
    # A bootstrapId is a 36-char UUID; the reserved key contains a '#' and is 14 chars.
    assert "#" in lp.LEASE_SETTINGS_KEY
    assert len(lp.LEASE_SETTINGS_KEY) != 36


def test_read_returns_seeded_default_when_absent(edh_session_table):
    policy = lp.read_lease_policy()
    assert policy["enabled"] is False
    assert policy["leaseHours"] == 8
    assert policy["forced"] is False
    assert policy["graceHours"] == 24


def test_write_then_read_round_trips(edh_session_table):
    saved = lp.write_lease_policy({"enabled": True, "leaseHours": 12}, "user-1")
    assert saved["enabled"] is True and saved["leaseHours"] == 12
    assert saved["updatedBy"] == "user-1"
    again = lp.read_lease_policy()
    assert again["enabled"] is True and again["leaseHours"] == 12
    assert again["forced"] is False  # untouched field keeps its default


@pytest.mark.parametrize("patch, message", [
    ({"leaseHours": 0}, "leaseHours must be 1-720"),
    ({"leaseHours": 721}, "leaseHours must be 1-720"),
    ({"graceHours": -1}, "graceHours must be 0-168"),
    ({"graceHours": 169}, "graceHours must be 0-168"),
])
def test_write_rejects_out_of_range_values_and_leaves_the_row_alone(edh_session_table, patch, message):
    lp.write_lease_policy({"enabled": True}, "user-1")

    with pytest.raises(lp.LeaseValidationError, match=message):
        lp.write_lease_policy(patch, "user-2")

    # The ranges are checked before the put, so the stored policy and its author are untouched.
    stored = lp.read_lease_policy()
    assert stored["enabled"] is True
    assert stored["leaseHours"] == 8
    assert stored["forced"] is False
    assert stored["graceHours"] == 24
    assert stored["updatedBy"] == "user-1"


@pytest.mark.parametrize("patch", [{"leaseHours": 720, "graceHours": 0}, {"leaseHours": 1, "graceHours": 168}])
def test_write_accepts_the_range_boundaries(edh_session_table, patch):
    saved = lp.write_lease_policy(patch, "user-1")
    assert (saved["leaseHours"], saved["graceHours"]) == (patch["leaseHours"], patch["graceHours"])
    again = lp.read_lease_policy()
    assert (again["leaseHours"], again["graceHours"]) == (patch["leaseHours"], patch["graceHours"])


def test_write_applies_false_and_zero(edh_session_table):
    """A merge on the stored policy carries the falsy values through, so leases can be turned off."""
    lp.write_lease_policy({"enabled": True, "forced": True, "graceHours": 24}, "user-1")

    saved = lp.write_lease_policy({"enabled": False, "forced": False, "graceHours": 0}, "user-2")

    for policy in (saved, lp.read_lease_policy()):
        assert policy["enabled"] is False
        assert policy["forced"] is False
        assert policy["graceHours"] == 0


def test_seed_inserts_once_only(edh_session_table):
    assert lp.seed_lease_policy({"enabled": True, "leaseHours": 5, "forced": True, "graceHours": 2}) is True
    # A second seed must not overwrite an admin-set policy.
    assert lp.seed_lease_policy({"enabled": False, "leaseHours": 99, "forced": False, "graceHours": 0}) is False
    assert lp.read_lease_policy()["leaseHours"] == 5


def test_resolve_launch_lease_forced_ignores_request(edh_session_table):
    policy = {"enabled": True, "leaseHours": 8, "forced": True, "graceHours": 24}
    expires, hours = lp.resolve_launch_lease(policy, 3, _now())
    assert hours == 8
    assert expires == (_now() + timedelta(hours=8)).isoformat()


def test_resolve_launch_lease_optional_honours_value(edh_session_table):
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    expires, hours = lp.resolve_launch_lease(policy, 3, _now())
    assert hours == 3
    assert expires == (_now() + timedelta(hours=3)).isoformat()


def test_resolve_launch_lease_optional_none_means_no_lease(edh_session_table):
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    assert lp.resolve_launch_lease(policy, None, _now()) == (None, None)


def test_resolve_launch_lease_rejects_over_cap(edh_session_table):
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    with pytest.raises(lp.LeaseValidationError):
        lp.resolve_launch_lease(policy, 9, _now())


def test_resolve_launch_lease_rejects_when_disabled(edh_session_table):
    policy = {"enabled": False, "leaseHours": 8, "forced": False, "graceHours": 24}
    with pytest.raises(lp.LeaseValidationError):
        lp.resolve_launch_lease(policy, 3, _now())
    # No request while disabled is fine — no lease.
    assert lp.resolve_launch_lease(policy, None, _now()) == (None, None)


def test_lease_action_stops_active_at_expiry():
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    row = {"state": STATE_ACTIVE, "leaseExpiresAt": (_now() - timedelta(minutes=1)).isoformat()}
    assert lp.lease_action(row, policy, _now()) == "stop"


def test_lease_action_none_before_expiry():
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    row = {"state": STATE_ACTIVE, "leaseExpiresAt": (_now() + timedelta(hours=1)).isoformat()}
    assert lp.lease_action(row, policy, _now()) is None


def test_lease_action_stamps_a_stopped_row_past_expiry():
    # A desktop stopped before its lease ran out is "stopped" again by the first sweep past the expiry: on a
    # desktop already stopped that is the stamp which starts its grace.
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    row = {"state": STATE_STOPPED, "leaseExpiresAt": (_now() - timedelta(hours=1)).isoformat()}
    assert lp.lease_action(row, policy, _now()) == "stop"


def test_lease_action_none_for_a_stopped_row_before_expiry():
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    row = {"state": STATE_STOPPED, "leaseExpiresAt": (_now() + timedelta(hours=1)).isoformat()}
    assert lp.lease_action(row, policy, _now()) is None


def test_lease_action_deletes_stopped_after_grace():
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 2}
    row = {
        "state": STATE_STOPPED,
        "leaseStoppedAt": (_now() - timedelta(hours=3)).isoformat(),
        "leaseExpiresAt": (_now() - timedelta(hours=3)).isoformat(),
    }
    assert lp.lease_action(row, policy, _now()) == "delete"


def test_lease_action_none_within_grace():
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    row = {
        "state": STATE_STOPPED,
        "leaseStoppedAt": (_now() - timedelta(hours=1)).isoformat(),
        "leaseExpiresAt": (_now() - timedelta(hours=1)).isoformat(),
    }
    assert lp.lease_action(row, policy, _now()) is None


def test_lease_action_skipped_when_disabled():
    policy = {"enabled": False, "leaseHours": 8, "forced": False, "graceHours": 24}
    row = {"state": STATE_ACTIVE, "leaseExpiresAt": (_now() - timedelta(hours=99)).isoformat()}
    assert lp.lease_action(row, policy, _now()) is None


def test_lease_action_none_when_no_lease_on_row():
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    assert lp.lease_action({"state": STATE_ACTIVE}, policy, _now()) is None


def test_lease_action_terminates_after_delete_state():
    # Once DELETED, lease_action never re-fires (provably terminating).
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 0}
    row = {"state": STATE_DELETED, "leaseStoppedAt": _now().isoformat(),
           "leaseExpiresAt": _now().isoformat()}
    assert lp.lease_action(row, policy, _now()) is None


def test_lease_action_stops_active_at_exact_expiry():
    # Pins the >= comparison: expiry equals now is PAST the deadline.
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    row = {"state": STATE_ACTIVE, "leaseExpiresAt": _now().isoformat()}
    assert lp.lease_action(row, policy, _now()) == "stop"


def test_lease_action_deletes_stopped_at_exact_grace_deadline():
    # Pins the >= comparison: grace deadline equals now is PAST the deadline.
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 2}
    expiry = _now() - timedelta(hours=2)
    row = {
        "state": STATE_STOPPED,
        "leaseStoppedAt": expiry.isoformat(),
        "leaseExpiresAt": expiry.isoformat(),
    }
    assert lp.lease_action(row, policy, _now()) == "delete"


def test_grace_runs_from_the_later_of_expiry_and_stop():
    # A stop that landed after the expiry starts the grace, so a late stop never shortens the window.
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 2}
    expiry = (_now() - timedelta(hours=3)).isoformat()

    within_grace = {"state": STATE_STOPPED, "leaseExpiresAt": expiry,
                    "leaseStoppedAt": (_now() - timedelta(hours=1)).isoformat()}
    assert lp.lease_action(within_grace, policy, _now()) is None

    past_grace = {"state": STATE_STOPPED, "leaseExpiresAt": expiry,
                  "leaseStoppedAt": (_now() - timedelta(hours=2, minutes=30)).isoformat()}
    assert lp.lease_action(past_grace, policy, _now()) == "delete"


def test_an_unreadable_stop_time_yields_no_action():
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 2}
    row = {"state": STATE_STOPPED, "leaseExpiresAt": (_now() - timedelta(hours=9)).isoformat(),
           "leaseStoppedAt": "not-a-date", "bootstrapId": "test-id"}
    assert lp.lease_action(row, policy, _now()) is None


def test_lease_action_naive_expiry_reads_as_utc():
    # A naive timestamp (no timezone) is treated as UTC and does not raise.
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    naive_past = "2026-09-16T11:00:00"  # No timezone suffix
    row = {"state": STATE_ACTIVE, "leaseExpiresAt": naive_past}
    assert lp.lease_action(row, policy, _now()) == "stop"


def test_lease_action_unparseable_expiry_yields_none():
    # Corrupt timestamp returns None instead of crashing the sweep.
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    row = {"state": STATE_ACTIVE, "leaseExpiresAt": "not-a-datetime", "bootstrapId": "test-id"}
    assert lp.lease_action(row, policy, _now()) is None


def test_lease_action_z_suffixed_expiry_reads_as_utc():
    # A "Z"-suffixed timestamp is the other spelling of UTC an external writer may use.
    policy = {"enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24}
    row = {"state": STATE_ACTIVE, "leaseExpiresAt": "2026-09-16T11:00:00Z"}
    assert lp.lease_action(row, policy, _now()) == "stop"
