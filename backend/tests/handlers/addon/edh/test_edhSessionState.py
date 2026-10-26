# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The session-row vocabulary shared by the EDH workstation Lambdas.

The EDH ``session_state`` -> VAMS ``state`` mapping, the ``lastError`` codes an ORPHANED row may carry
and the lease-expiry code a STOPPED row keeps, the launch worker's payload keys, the expiry a DELETED
row leaves the table by, ``observed_row_attributes`` -- the one function both the service and the
worker derive a row's state from, so a row never carries a state EDH did not report -- the EDH
schedule VAMS gives every desktop with the predicate that reads a listed one, the credential claim a
key re-issue or a revoke holds a row with, and when a row's desktop may still be running on a key of its
bootstrap. EDH desktop records are built from the module constants, never from string literals.
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from backend.backend.handlers.addon.edh.common.edhClient import EDH_SESSION_ID_FIELD
from backend.backend.models.addon.edh import WORKSTATION_STATES
from backend.tests.handlers.addon.edh.edhTestSupport import count_key, first_value_of, load_fixture
import backend.backend.handlers.addon.edh.common.edhSessionState as state

SESSION_ID = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"
CONNECTION_URL = "https://dcv.example.internal/?authToken=abc"
INSTANCE_ID = "i-0123456789abcdef0"
# The stamp a start whose outcome EDH did not confirm leaves on its row.
START_REQUESTED_AT = "2026-09-08T12:00:00+00:00"

# EDH session_state -> VAMS state.
STATE_PAIRS = [
    ("pending", "CREATING"),
    ("placing", "CREATING"),
    ("running", "ACTIVE"),
    ("stopping", "STOPPED"),
    ("stopped", "STOPPED"),
    ("interrupting", "STOPPED"),
    ("interrupted", "STOPPED"),
    ("error", "ORPHANED"),
]


def _desktop(session_state, connection_string=CONNECTION_URL, session_id=SESSION_ID):
    return {
        EDH_SESSION_ID_FIELD: session_id,
        state.EDH_SESSION_STATE_FIELD: session_state,
        state.EDH_CONNECTION_STRING_FIELD: connection_string,
    }


def _creating_row():
    """The row the API handler writes: CREATING, no session id, no EDH state, not connectable."""
    return {"bootstrapId": "b-1", "state": "CREATING", "edhState": None, "connectionAvailable": False}


def _active_row(**overrides):
    row = {"bootstrapId": "b-1", "sessionId": SESSION_ID, "state": "ACTIVE", "edhState": "running",
           "connectionAvailable": True, "lastError": None}
    row.update(overrides)
    return row


@pytest.mark.unit
class TestStateMapping:
    @pytest.mark.parametrize("edh_state, vams_state", STATE_PAIRS)
    def test_each_edh_state_maps_to_its_vams_state(self, edh_state, vams_state):
        assert state.state_for_edh_session_state(edh_state, "STORED") == vams_state
        # EDH's wording is matched case-insensitively.
        assert state.state_for_edh_session_state(edh_state.upper(), "STORED") == vams_state

    def test_an_unknown_edh_state_keeps_the_stored_state(self):
        assert state.state_for_edh_session_state("hibernating", "STOPPED") == "STOPPED"

    def test_no_edh_state_keeps_the_stored_state(self):
        assert state.state_for_edh_session_state(None, "CREATING") == "CREATING"


@pytest.mark.unit
class TestVocabulary:
    def test_the_failure_codes_are_the_documented_set(self):
        assert state.LAST_ERROR_CODES == {
            "USER_NOT_PROVISIONED",
            "QUOTA_EXCEEDED",
            "IMAGE_UNAVAILABLE",
            "CAPACITY_UNAVAILABLE",
            "EDH_REFUSED",
            "LAUNCH_ERROR",
            "CREATE_TIMED_OUT",
            "START_FAILED",
            "START_STALLED",
            "LEASE_EXPIRED",
            "RESERVATION_FULL",
            "RESERVATION_ENDED",
        }

    def test_the_worker_payload_is_the_row_identity(self):
        assert state.WORKER_PAYLOAD_KEYS == (
            "bootstrapId", "userId", "edhUid", "imageId", "instanceType", "osFamily", "rootSizeGb", "name",
        )

    def test_the_instance_field_is_the_one_the_captured_listing_carries(self):
        listed = load_fixture("desktops_list_after_create")["response"]["json"]["message"]
        desktop = next(iter(listed.values()))
        assert str(desktop[state.EDH_FIELD_INSTANCE_ID]).startswith("i-")

    def test_every_state_a_row_can_be_given_is_a_record_model_state(self):
        # The states EDH's session_state maps onto, plus the one written for a desktop EDH no longer
        # lists, are all states the record model returns to a caller.
        derived = set(state.EDH_SESSION_STATE_TO_STATE.values()) | {state.STATE_DELETED}
        assert derived <= set(WORKSTATION_STATES)


@pytest.mark.unit
class TestObservedRowAttributes:
    def test_a_running_desktop_completes_and_activates_a_creating_row(self):
        changed = state.observed_row_attributes(_creating_row(), _desktop("running"))

        assert changed == {
            "sessionId": SESSION_ID,
            "edhState": "running",
            "state": "ACTIVE",
            "connectionAvailable": True,
        }

    def test_a_pending_desktop_completes_the_session_id_and_keeps_the_row_creating(self):
        row = _creating_row()

        changed = state.observed_row_attributes(row, _desktop("pending", connection_string=""))

        assert changed["sessionId"] == SESSION_ID
        assert changed["edhState"] == "pending"
        assert {**row, **changed}["state"] == "CREATING"
        # The row already holds False, so nothing about connectability is rewritten.
        assert "connectionAvailable" not in changed

    def test_a_running_record_without_a_connection_string_is_connectable(self):
        # The session-state read and the cluster listing carry no connection string at all.
        record = {EDH_SESSION_ID_FIELD: SESSION_ID, state.EDH_SESSION_STATE_FIELD: "running"}

        changed = state.observed_row_attributes(_active_row(connectionAvailable=False), record)

        assert changed["connectionAvailable"] is True

    def test_a_running_record_with_an_empty_connection_string_is_not_connectable(self):
        changed = state.observed_row_attributes(_active_row(), _desktop("running", connection_string=""))

        assert changed["connectionAvailable"] is False

    def test_a_desktop_in_error_orphans_the_row_and_a_recovery_clears_the_code(self):
        orphaned = state.observed_row_attributes(_active_row(), _desktop("error", connection_string=""))

        assert orphaned["state"] == state.STATE_ORPHANED
        assert orphaned["edhState"] == "error"
        assert orphaned["lastError"] == state.LAST_ERROR_LAUNCH_ERROR
        assert orphaned["connectionAvailable"] is False

        recovered = state.observed_row_attributes(
            _active_row(state="ORPHANED", edhState="error", connectionAvailable=False,
                        lastError=state.LAST_ERROR_LAUNCH_ERROR),
            _desktop("running"))

        assert recovered["state"] == "ACTIVE"
        assert recovered["lastError"] is None
        assert recovered["connectionAvailable"] is True

    def test_a_row_that_matches_its_desktop_changes_nothing(self):
        assert state.observed_row_attributes(_active_row(), _desktop("running")) == {}

    def test_an_orphaned_row_keeps_the_specific_code_it_was_orphaned_with(self):
        row = _active_row(state="ORPHANED", edhState="error", connectionAvailable=False,
                          lastError=state.LAST_ERROR_QUOTA_EXCEEDED)

        changed = state.observed_row_attributes(row, _desktop("error", connection_string=""))

        # The generic code never overwrites the specific one the launch recorded.
        assert "lastError" not in changed
        assert {**row, **changed}["lastError"] == state.LAST_ERROR_QUOTA_EXCEEDED

    def test_an_orphaned_row_without_a_code_receives_the_generic_one(self):
        row = _active_row(state="ORPHANED", edhState="error", connectionAvailable=False, lastError=None)

        changed = state.observed_row_attributes(row, _desktop("error", connection_string=""))

        assert changed == {"lastError": state.LAST_ERROR_LAUNCH_ERROR}

    def test_the_desktops_instance_is_recorded_on_the_row(self):
        desktop = {**_desktop("running"), state.EDH_FIELD_INSTANCE_ID: INSTANCE_ID}

        assert state.observed_row_attributes(_active_row(), desktop) == {"instanceId": INSTANCE_ID}

    def test_a_desktop_without_an_instance_leaves_the_stored_one(self):
        row = _active_row(instanceId=INSTANCE_ID)

        assert state.observed_row_attributes(row, _desktop("running")) == {}
        assert row["instanceId"] == INSTANCE_ID

    def test_a_lease_stopped_row_keeps_its_code_while_the_desktop_stays_stopped(self):
        row = _active_row(state="STOPPED", edhState="stopped", connectionAvailable=False,
                          lastError=state.LAST_ERROR_LEASE_EXPIRED, leaseStoppedAt="2026-09-08T12:00:00+00:00")

        assert state.observed_row_attributes(row, _desktop("stopped", connection_string="")) == {}

    def test_a_desktop_seen_running_again_clears_the_lease_stop(self):
        row = _active_row(state="STOPPED", edhState="stopped", connectionAvailable=False,
                          lastError=state.LAST_ERROR_LEASE_EXPIRED, leaseStoppedAt="2026-09-08T12:00:00+00:00")

        assert state.observed_row_attributes(row, _desktop("running")) == {
            "state": "ACTIVE", "edhState": "running", "lastError": None, "connectionAvailable": True,
            "leaseStoppedAt": None,
        }

    def test_a_desktop_seen_starting_again_clears_the_lease_stop(self):
        row = _active_row(state="STOPPED", edhState="stopped", connectionAvailable=False,
                          lastError=state.LAST_ERROR_LEASE_EXPIRED, leaseStoppedAt="2026-09-08T12:00:00+00:00")

        changed = state.observed_row_attributes(row, _desktop("pending", connection_string=""))

        assert changed["state"] == "CREATING"
        assert changed["lastError"] is None
        assert changed["leaseStoppedAt"] is None

    @pytest.mark.parametrize("edh_state", ["pending", "placing", "running", "error"])
    def test_a_desktop_seen_past_stopped_clears_the_start_stamp(self, edh_state):
        # The stamp keeps a second start from following one EDH may still be carrying out; EDH listing
        # the desktop past stopped shows that start was taken.
        row = _active_row(state="STOPPED", edhState="stopped", connectionAvailable=False,
                          startRequestedAt=START_REQUESTED_AT)

        changed = state.observed_row_attributes(row, _desktop(edh_state, connection_string=""))

        assert "startRequestedAt" in changed and changed["startRequestedAt"] is None

    @pytest.mark.parametrize("edh_state", ["stopping", "stopped", "interrupted", "hibernating"])
    def test_a_desktop_still_seen_stopped_or_in_an_unknown_state_keeps_the_start_stamp(self, edh_state):
        row = _active_row(state="STOPPED", edhState="stopped", connectionAvailable=False,
                          startRequestedAt=START_REQUESTED_AT)

        assert "startRequestedAt" not in state.observed_row_attributes(
            row, _desktop(edh_state, connection_string=""))

    def test_a_row_without_a_start_stamp_gains_no_stamp_attribute(self):
        row = _active_row(state="STOPPED", edhState="stopped", connectionAvailable=False)

        assert "startRequestedAt" not in state.observed_row_attributes(row, _desktop("running"))


@pytest.mark.unit
class TestStartInProgress:
    """The one freshness check the start route and the reconciler share: a start's stamp holds for the
    window from the instant it was written, measured against the caller's reference instant."""

    NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)

    def _stamped(self, seconds_ago):
        return {"startRequestedAt": (self.NOW - timedelta(seconds=seconds_ago)).isoformat()}

    @pytest.mark.parametrize("seconds_ago", [0, 1, 179])
    def test_a_stamp_younger_than_the_window_is_in_progress(self, seconds_ago):
        assert state.start_in_progress(self._stamped(seconds_ago), self.NOW) is True

    @pytest.mark.parametrize("seconds_ago", [420, 421, 3600])
    def test_a_stamp_the_window_has_passed_holds_nothing(self, seconds_ago):
        assert state.start_in_progress(self._stamped(seconds_ago), self.NOW) is False

    def test_a_stamp_written_after_the_reference_instant_is_in_progress(self):
        # A sweep measures against the instant it began; a start that claimed the row since is under way.
        assert state.start_in_progress(self._stamped(-5), self.NOW) is True

    @pytest.mark.parametrize("row", [{}, {"startRequestedAt": None}, {"startRequestedAt": "not-a-timestamp"}],
                             ids=["absent", "cleared", "unreadable"])
    def test_an_absent_cleared_or_unreadable_stamp_holds_nothing(self, row):
        assert state.start_in_progress(row, self.NOW) is False

    def test_a_stamp_without_an_offset_is_read_as_utc(self):
        naive = (self.NOW - timedelta(seconds=10)).replace(tzinfo=None).isoformat()
        assert state.start_in_progress({"startRequestedAt": naive}, self.NOW) is True

    def test_without_a_reference_instant_the_window_is_measured_from_now(self):
        assert state.start_in_progress({"startRequestedAt": datetime.now(timezone.utc).isoformat()}) is True

    def test_the_window_is_seven_minutes(self):
        # The launch worker's six-minute run and a minute for the hand-over (in-dev D10).
        assert state.START_IN_PROGRESS_SECONDS == 420


@pytest.mark.unit
class TestCredentialClaim:
    """The claim a renew's key re-issue and the sweep's revoke take on a row: fresh for the window from the
    instant it was written, measured against the caller's reference instant."""

    NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)

    def _claimed(self, seconds_ago):
        return {"credentialClaimedAt": state.credential_claim_stamp(self.NOW - timedelta(seconds=seconds_ago))}

    @pytest.mark.parametrize("seconds_ago", [0, 1, 150, 299])
    def test_a_claim_younger_than_the_window_is_fresh(self, seconds_ago):
        assert state.credential_claim_fresh(self._claimed(seconds_ago), self.NOW) is True

    @pytest.mark.parametrize("seconds_ago", [300, 301, 3600])
    def test_a_claim_the_window_has_passed_is_not_fresh(self, seconds_ago):
        assert state.credential_claim_fresh(self._claimed(seconds_ago), self.NOW) is False

    @pytest.mark.parametrize("seconds_ago", [-1, -150, -299])
    def test_a_claim_written_after_the_reference_instant_inside_one_window_is_fresh(self, seconds_ago):
        # A sweep measures against the instant it began; a renew that claimed the row since holds it.
        assert state.credential_claim_fresh(self._claimed(seconds_ago), self.NOW) is True

    @pytest.mark.parametrize("seconds_ago", [-301, -3600])
    def test_a_claim_dated_more_than_one_window_ahead_is_not_fresh(self, seconds_ago):
        # Only a hand edit or a corrupt value dates a claim that far ahead; it holds nothing off.
        assert state.credential_claim_fresh(self._claimed(seconds_ago), self.NOW) is False

    @pytest.mark.parametrize("row", [{}, {"credentialClaimedAt": None}, {"credentialClaimedAt": ""},
                                     {"credentialClaimedAt": "not-a-timestamp"}, {"credentialClaimedAt": 17}],
                             ids=["absent", "cleared", "blank", "unreadable", "not a string"])
    def test_an_absent_cleared_or_unreadable_claim_is_not_fresh(self, row):
        assert state.credential_claim_fresh(row, self.NOW) is False

    def test_a_claim_without_an_offset_is_read_as_utc(self):
        naive = (self.NOW - timedelta(seconds=10)).replace(tzinfo=None).isoformat()
        assert state.credential_claim_fresh({"credentialClaimedAt": naive}, self.NOW) is True

    def test_without_a_reference_instant_the_window_is_measured_from_now(self):
        claimed = {"credentialClaimedAt": state.credential_claim_stamp(datetime.now(timezone.utc))}
        assert state.credential_claim_fresh(claimed) is True

    def test_the_stamp_is_utc_to_the_microsecond_in_one_fixed_width(self):
        # A claim is taken with a condition that compares stamps as strings.
        early = state.credential_claim_stamp(datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc))
        late = state.credential_claim_stamp(datetime(2026, 9, 8, 12, 0, 0, 1, tzinfo=timezone.utc))
        assert early == "2026-09-08T12:00:00.000000+00:00"
        assert len(early) == len(late) and early < late

    def test_the_window_is_five_minutes(self):
        assert state.CREDENTIAL_CLAIM_SECONDS == 300


@pytest.mark.unit
class TestSessionRowLive:
    """Whether a row's desktop may still be running on a key of its bootstrap: the row is stored ACTIVE or
    CREATING, or holds a start stamp or a credential claim inside its window. The orphan sweep keeps every
    unexpired key of such a row's bootstrap."""

    NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
    FRESH_START = state.start_stamp(NOW - timedelta(seconds=30))
    STALE_START = state.start_stamp(NOW - timedelta(seconds=state.START_IN_PROGRESS_SECONDS + 1))
    FRESH_CLAIM = state.credential_claim_stamp(NOW - timedelta(seconds=30))
    STALE_CLAIM = state.credential_claim_stamp(NOW - timedelta(seconds=state.CREDENTIAL_CLAIM_SECONDS + 1))

    @pytest.mark.parametrize("row", [
        {"state": "ACTIVE"},
        {"state": "CREATING"},
        {"state": "STOPPED", "startRequestedAt": FRESH_START},
        {"state": "STOPPED", "credentialClaimedAt": FRESH_CLAIM},
        {"state": "DELETED", "credentialClaimedAt": FRESH_CLAIM},
    ], ids=["running", "launching", "stopped under a start", "stopped under a claim", "deleted under a claim"])
    def test_a_row_stored_running_or_launching_or_under_a_fresh_stamp_is_live(self, row):
        assert state.session_row_live(row, self.NOW) is True

    @pytest.mark.parametrize("row", [
        {"state": "STOPPED"},
        {"state": "ORPHANED"},
        {"state": "DELETED"},
        {},
        {"state": "STOPPED", "startRequestedAt": STALE_START},
        {"state": "STOPPED", "credentialClaimedAt": STALE_CLAIM},
        {"state": "STOPPED", "startRequestedAt": None, "credentialClaimedAt": None},
    ], ids=["stopped", "orphaned", "deleted", "no state", "stale start", "stale claim", "cleared stamps"])
    def test_any_other_row_is_not_live(self, row):
        assert state.session_row_live(row, self.NOW) is False

    def test_the_callers_own_claim_does_not_count(self):
        row = {"state": "STOPPED", "credentialClaimedAt": self.FRESH_CLAIM}
        assert state.session_row_live(row, self.NOW, own_claim=self.FRESH_CLAIM) is False
        # Control: a claim another writer holds is counted.
        other = state.credential_claim_stamp(self.NOW - timedelta(seconds=31))
        assert state.session_row_live(row, self.NOW, own_claim=other) is True

    def test_the_callers_own_claim_leaves_a_running_row_live(self):
        row = {"state": "ACTIVE", "credentialClaimedAt": self.FRESH_CLAIM}
        assert state.session_row_live(row, self.NOW, own_claim=self.FRESH_CLAIM) is True

    def test_without_a_reference_instant_the_windows_are_measured_from_now(self):
        row = {"state": "STOPPED", "credentialClaimedAt": state.credential_claim_stamp(datetime.now(timezone.utc))}
        assert state.session_row_live(row) is True


@pytest.mark.unit
class TestDeletedRowExpiry:
    def test_the_expiry_is_the_retention_after_now_in_integer_epoch_seconds(self):
        # DynamoDB's TTL reads a Number of epoch seconds; a float or an ISO string would never expire.
        now = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
        expiry = state.deleted_row_expiry(now)
        assert isinstance(expiry, int) and not isinstance(expiry, bool)
        assert expiry == int((now + timedelta(days=state.DELETED_ROW_RETENTION_DAYS)).timestamp())
        assert state.DELETED_ROW_RETENTION_DAYS == 7

    def test_without_a_reference_instant_the_expiry_is_measured_from_now(self):
        before = int((datetime.now(timezone.utc) + timedelta(days=state.DELETED_ROW_RETENTION_DAYS)).timestamp())
        expiry = state.deleted_row_expiry()
        after = int((datetime.now(timezone.utc) + timedelta(days=state.DELETED_ROW_RETENTION_DAYS)).timestamp())
        assert before <= expiry <= after

    def test_the_attribute_is_the_tables_ttl_attribute(self):
        assert state.EXPIRES_AT_FIELD == "expiresAt"


def _schedule_text(**days):
    """The JSON an EDH listing carries for a schedule: every day 0/0 unless given."""
    schedule = state.never_auto_start_schedule()
    schedule.update(days)
    return json.dumps(schedule)


def _without(day):
    schedule = state.never_auto_start_schedule()
    schedule.pop(day)
    return json.dumps(schedule)


@pytest.mark.unit
class TestNeverAutoStartSchedule:
    """The schedule VAMS gives every desktop, and the predicate that decides from the string an EDH listing
    carries whether a desktop already has it. EDH's scheduler reads that string with ``json.loads`` and
    compares integer minutes, so only JSON with integer zeros reads as applied."""

    def test_the_schedule_names_every_day_edh_requires_stopped_all_day(self):
        schedule = state.never_auto_start_schedule()
        assert state.EDH_SCHEDULE_DAYS == (
            "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        assert schedule == {day: {"start": 0, "stop": 0} for day in state.EDH_SCHEDULE_DAYS}
        assert all(type(minute) is int for window in schedule.values() for minute in window.values())

    def test_each_call_returns_a_fresh_copy(self):
        first, second = state.never_auto_start_schedule(), state.never_auto_start_schedule()
        assert first is not second and first["monday"] is not second["monday"]
        first["monday"]["start"] = 480
        assert state.never_auto_start_schedule()["monday"] == {"start": 0, "stop": 0}

    def test_the_field_is_the_one_the_captured_listings_carry(self):
        for key in ("desktops_list_active", "desktops_list_all"):
            capture = load_fixture(key)
            assert count_key(capture, state.EDH_SCHEDULE_FIELD) > 0, key
            listed = json.loads(first_value_of(capture, state.EDH_SCHEDULE_FIELD))
            assert set(listed) == set(state.EDH_SCHEDULE_DAYS), key

    @pytest.mark.parametrize("value", [
        json.dumps(state.never_auto_start_schedule()),
        json.dumps(state.never_auto_start_schedule(), separators=(",", ":")),
        json.dumps({**state.never_auto_start_schedule(), "timezone": "UTC"}),
        _schedule_text(monday={"start": 0, "stop": 0, "note": "kept"}),
    ], ids=["target", "compact", "extra top-level key", "extra day key"])
    def test_json_with_every_day_at_integer_zero_never_auto_starts(self, value):
        assert state.schedule_never_auto_starts(value) is True

    def test_the_default_schedule_edh_gives_a_new_desktop_still_needs_applying(self):
        listed = first_value_of(load_fixture("desktops_list_active"), state.EDH_SCHEDULE_FIELD)
        assert json.loads(listed)["monday"] == {"start": 480, "stop": 1140}
        assert state.schedule_never_auto_starts(listed) is False

    @pytest.mark.parametrize("value", [
        _without("sunday"),
        _schedule_text(friday={"start": 0}),
        _schedule_text(monday={"start": "0", "stop": "0"}),
        _schedule_text(monday={"start": False, "stop": False}),
        _schedule_text(monday={"start": True, "stop": True}),
        _schedule_text(monday={"start": 0.0, "stop": 0.0}),
        _schedule_text(saturday={"start": 1440, "stop": 1440}),
        _schedule_text(tuesday={"start": 0, "stop": 1140}),
        _schedule_text(wednesday=None),
        str(state.never_auto_start_schedule()),
        "never",
        None,
        "",
        "[]",
        "null",
        json.dumps([state.never_auto_start_schedule()]),
        state.never_auto_start_schedule(),
    ], ids=["missing day", "missing stop", "string minutes", "false minutes", "true minutes", "float minutes",
            "all-day start", "a window", "null day", "python repr", "not json", "none", "empty", "json list",
            "json null", "wrapped in a list", "a dict not a string"])
    def test_anything_else_still_needs_applying(self, value):
        assert state.schedule_never_auto_starts(value) is False


@pytest.mark.unit
class TestTheVocabularyIsPure:
    PACKAGE = "backend.backend.handlers.addon.edh.common"
    CLIENT_ENVIRONMENT = ("EDH_API_ENDPOINT", "EDH_ADMIN_KEY_SECRET_ARN", "EDH_AUTO_PROVISION_USERS")

    def _load_fresh(self, name, source):
        spec = importlib.util.spec_from_file_location(f"{self.PACKAGE}.{name}_under_test", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_vocabulary_loads_without_the_clients_environment(self, monkeypatch):
        # A utility that only rewrites session rows imports the vocabulary and never contacts EDH, so
        # the module must not pull the client's mandatory environment in behind it.
        for name in self.CLIENT_ENVIRONMENT:
            monkeypatch.delenv(name, raising=False)
        with patch.dict(sys.modules):
            sys.modules.pop(f"{self.PACKAGE}.edhClient", None)
            module = self._load_fresh("edhSessionState", state.__file__)
            assert module.EDH_SESSION_ID_FIELD == "session_uuid"
            assert f"{self.PACKAGE}.edhClient" not in sys.modules
            # Control: the client itself does need that environment.
            from backend.backend.handlers.addon.edh.common import edhClient
            with pytest.raises(KeyError):
                self._load_fresh("edhClient", edhClient.__file__)

    def test_the_client_reads_the_id_field_from_the_vocabulary(self):
        from backend.backend.handlers.addon.edh.common import edhClient
        assert edhClient.EDH_SESSION_ID_FIELD == state.EDH_SESSION_ID_FIELD == "session_uuid"
