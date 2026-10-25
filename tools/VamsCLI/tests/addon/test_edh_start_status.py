# SPDX-License-Identifier: Apache-2.0
"""The workstation start status in the CLI (feature #536, D10/D11): the State column and the `get`
lines a record's `activity` and `startFailure` produce, the start answered 202 and followed in the
background, and every exit path of `edh workstation start --wait`.

The CLI half of the CLI/web consistency check reads the same fixture as the web's
WorkstationStatusBadge.cases.test.tsx, web/src/addons/edh/workstationStatusCases.json, and asserts
the State column and the `get` lines it agrees with the web's badge and start-failure lines.
"""

import json
from pathlib import Path

import pytest

import vamscli.commands.addon.edh as edh_commands
import vamscli.utils.addon.edh_exceptions as edh_exceptions
from vamscli.main import cli
from vamscli.utils.addon.edh_exceptions import (
    EdhOperationError,
    EdhStartFailedError,
    EdhStartWaitTimeoutError,
    EdhUnavailableError,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
STATUS_CASES = REPO_ROOT / "web/src/addons/edh/workstationStatusCases.json"
CASES = json.loads(STATUS_CASES.read_text(encoding="utf-8"))["cases"]

# The fields a list row and a `get` record carry beside the ones each case sets.
BASE = {
    "bootstrapId": "b-1",
    "userId": "user@example.com",
    "edhUid": "vabcdefghij23456",
    "name": "Render-box",
    "imageId": "12",
    "osFamily": "linux",
    "createdAt": "2026-10-23T10:00:00Z",
    "updatedAt": "2026-10-23T15:19:13Z",
    "launchAttempts": 1,
}

STOPPED = {**BASE, "sessionId": "s-1", "state": "STOPPED", "edhState": "stopped", "activity": None,
           "lastError": None, "startFailure": None, "connectionAvailable": False,
           "instanceType": "g5.16xlarge"}
START_ACCEPTED = {**STOPPED, "activity": "starting", "sessionBootstrap": "provisioned"}
BOOTING = {**STOPPED, "state": "CREATING", "edhState": "pending", "activity": "starting"}
NOT_CONNECTABLE = {**STOPPED, "state": "ACTIVE", "edhState": "pending", "activity": "starting"}
RUNNING = {**STOPPED, "state": "ACTIVE", "edhState": "running", "connectionAvailable": True}
CAPACITY_FAILURE = {"errorCode": "InsufficientInstanceCapacity", "availabilityZone": "us-west-2b",
                    "observedAt": "2026-10-23T15:19:30Z"}
REFUSED = {**STOPPED, "lastError": "CAPACITY_UNAVAILABLE", "startFailure": CAPACITY_FAILURE}
CAPACITY_SENTENCE = (
    "AWS had no capacity for g5.16xlarge in this workstation's Availability Zone (us-west-2b) when it "
    "was started. A stopped workstation keeps its disk and starts only in that zone, so it stays "
    "stopped; start it again later.")
# A start EDH accepted and kept pending for 30 minutes: the session sweep records the stall on the
# row, which stays CREATING (EDH may still finish), and the service derives no activity for it (D16).
STALLED = {**BOOTING, "activity": None, "lastError": "START_STALLED",
           "startFailure": {"availabilityZone": "us-west-2b", "observedAt": "2026-10-24T11:40:00Z"}}
STALLED_SENTENCE = (
    "EDH has not finished starting this workstation after 30 minutes. Stop it, then start it again; if "
    "it stalls again, ask your administrator.")

START_LINE = ("Start requested; the workstation is starting. Follow it with "
              "`vamscli edh workstation get s-1`.")


def _ids(cases):
    return [case["name"] for case in cases]


def _row(case):
    return {**BASE, **case["record"]}


def _state_cell(output: str) -> str:
    """The State column of the single data row of `edh workstation list` text output."""
    lines = output.splitlines()
    header = next(line for line in lines if "Identifier" in line)
    row = next(line for line in lines if line.startswith("  s-1") or line.startswith("  b-1*"))
    start = header.index("State")
    end = header.index("Last Error")
    return row[start:end].strip()


class _FakeClock:
    """Stands in for time.monotonic and time.sleep: sleeping advances the clock, nothing waits."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert 0 <= seconds <= 10, seconds
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = _FakeClock()
    monkeypatch.setattr(edh_commands, "_monotonic", fake.monotonic)
    monkeypatch.setattr(edh_commands, "_sleep", fake.sleep)
    return fake


# --- The fixture shared with the web --------------------------------------------------------------


def test_the_shared_fixture_covers_every_state_activity_and_start_failure_code():
    """Control: the file the web test reads too, with every state, activity and start failure code."""
    assert {case["record"]["state"] for case in CASES} == {
        "CREATING", "ACTIVE", "STOPPED", "DELETED", "ORPHANED"}
    assert {case["record"]["activity"] for case in CASES} == {"starting", "stopping", None}
    stopped_codes = {case["record"]["lastError"] for case in CASES if case["record"]["state"] == "STOPPED"}
    assert {"CAPACITY_UNAVAILABLE", "RESERVATION_FULL", "START_FAILED", "LEASE_EXPIRED", None} <= stopped_codes
    # A start EDH accepted and never finished is a CREATING row the sweep marked stalled.
    stalled = [case for case in CASES if case["record"]["lastError"] == "START_STALLED"]
    assert stalled and all(case["record"]["state"] == "CREATING" for case in stalled)
    assert {case["startFailureMessage"] for case in stalled} == {STALLED_SENTENCE}


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_the_state_column_reads_as_the_web_badge_does(cli_runner, generic_command_mocks, case):
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].list_edh_workstations.return_value = {'Items': [_row(case)]}
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'list'])
        assert result.exit_code == 0, result.output
        assert _state_cell(result.output) == case["state"]
        # The web's label, its last word upper-cased: the two clients name the same phase, and the
        # column keeps to one word (`Start stalled` prints STALLED).
        assert case["state"] == case["badge"].split()[-1].upper()


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_get_prints_the_activity_and_the_start_failure_the_web_shows(cli_runner, generic_command_mocks, case):
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].get_edh_workstation.return_value = _row(case)
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'get', 's-1'])
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        assert f"  Activity:        {case['record']['activity'] or 'none'}" in lines
        failure_lines = [line for line in lines if line.startswith("  Start Failure:")]
        if case["startFailureMessage"] is None:
            assert failure_lines == []
            assert not any(line.strip().startswith("AWS:") for line in lines)
            return
        # The sentence beneath Last Error, then the web's detail line in the same value column.
        last_error = lines.index(f"  Last Error:      {case['record']['lastError']}")
        assert lines[last_error + 1] == f"  Start Failure:   {case['startFailureMessage']}"
        if case["startFailureDetail"] is None:
            assert not any(line.strip().startswith("AWS:") for line in lines)
        else:
            assert lines[last_error + 2] == f"                   {case['startFailureDetail']}"


def test_list_json_output_carries_activity_and_start_failure_unchanged(cli_runner, generic_command_mocks):
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].list_edh_workstations.return_value = {'Items': [REFUSED, START_ACCEPTED]}
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'list', '--json-output'])
        assert result.exit_code == 0
        items = json.loads(result.output)['Items']
        assert items[0]['startFailure'] == CAPACITY_FAILURE
        assert items[1]['activity'] == 'starting'


# --- start: accepted (202) and followed in the background ----------------------------------------


class TestStartAccepted:
    def test_start_prints_the_record_and_the_started_in_the_background_line(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = START_ACCEPTED
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'start', 's-1'])
            assert result.exit_code == 0, result.output
            assert "Workstation start requested" in result.output
            assert "  Activity:        starting" in result.output
            assert START_LINE in result.output
            # Without --wait nothing is polled.
            assert not mocks['api_client'].get_edh_workstation.called

    def test_start_json_output_is_the_accepted_record(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = START_ACCEPTED
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'start', 's-1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == START_ACCEPTED

    def test_a_refusal_with_a_reservation_full_code_is_an_ordinary_refusal(
            self, cli_runner, generic_command_mocks):
        # The start route no longer answers RESERVATION_FULL; the row records it. Whatever a 409 says,
        # the command reports it as the service's refusal.
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.side_effect = EdhOperationError(
                "Workstation start refused (409): Workstation is not stopped")
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'start', 's-1'])
            assert result.exit_code == 1
            assert 'EDH Error' in result.output
            assert 'Capacity Reservation Full' not in result.output

    def test_the_help_names_the_wait_option_and_the_asynchronous_start(self, cli_runner):
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'start', '--help'])
        assert result.exit_code == 0
        text = ' '.join(result.output.split())
        assert '--wait' in text
        assert 'HTTP 202' in text
        assert 'START_STALLED' in text
        assert edh_commands.START_WAIT_POLL_SECONDS == 10
        assert edh_commands.START_WAIT_TIMEOUT_SECONDS == 600

    @pytest.mark.parametrize("command", ['list', 'get', 'stop'])
    def test_the_list_get_and_stop_help_name_the_stalled_start(self, cli_runner, command):
        result = cli_runner.invoke(cli, ['edh', 'workstation', command, '--help'])
        assert result.exit_code == 0
        text = ' '.join(result.output.split())
        assert 'STALLED' in text, command


# --- start --wait: every exit path ----------------------------------------------------------------


class TestStartWait:
    ARGS = ['edh', 'workstation', 'start', 's-1', '--wait']

    def test_exits_0_once_the_workstation_is_active(self, cli_runner, generic_command_mocks, clock):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = START_ACCEPTED
            mocks['api_client'].get_edh_workstation.side_effect = [BOOTING, NOT_CONNECTABLE, RUNNING]
            result = cli_runner.invoke(cli, self.ARGS)
            assert result.exit_code == 0, result.output
            assert mocks['api_client'].get_edh_workstation.call_count == 3
            mocks['api_client'].get_edh_workstation.assert_called_with('s-1')
            assert clock.sleeps == [10, 10, 10]
            assert "Workstation started (ACTIVE)" in result.output
            assert "  State:           ACTIVE" in result.output
            # An ACTIVE row EDH does not report connectable is still starting: the wait goes on.
            assert "STARTING" in result.output

    def test_json_output_on_success_is_the_final_record(self, cli_runner, generic_command_mocks, clock):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = START_ACCEPTED
            mocks['api_client'].get_edh_workstation.side_effect = [BOOTING, RUNNING]
            result = cli_runner.invoke(cli, self.ARGS + ['--json-output'])
            assert result.exit_code == 0, result.output
            assert json.loads(result.output) == RUNNING

    @pytest.mark.parametrize("refused,sentence,detail", [
        (REFUSED, CAPACITY_SENTENCE, "AWS: InsufficientInstanceCapacity in us-west-2b"),
        ({**STOPPED, "lastError": "RESERVATION_FULL",
          "startFailure": {"errorCode": "ReservationCapacityExceeded", "availabilityZone": "us-west-2b",
                           "observedAt": "2026-10-23T15:19:30Z"}},
         "The capacity reservation has no free capacity; the workstation stays stopped. Start it again "
         "once a slot is free, or ask an administrator.",
         "AWS: ReservationCapacityExceeded in us-west-2b"),
        ({**STOPPED, "lastError": "START_FAILED", "startFailure": None},
         "The workstation did not start; it is still stopped with its disk. Start it again, or ask an "
         "administrator.", None),
    ], ids=["capacity", "reservation-full", "start-failed"])
    def test_exits_non_zero_with_the_failure_once_the_start_left_it_stopped(
            self, cli_runner, generic_command_mocks, clock, refused, sentence, detail):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = START_ACCEPTED
            mocks['api_client'].get_edh_workstation.side_effect = [START_ACCEPTED, refused]
            result = cli_runner.invoke(cli, self.ARGS)
            assert result.exit_code == 1, result.output
            assert mocks['api_client'].get_edh_workstation.call_count == 2
            assert "Workstation Start Failed" in result.output
            assert f"({refused['lastError']})" in result.output
            assert f"  Start Failure:   {sentence}" in result.output
            if detail:
                assert detail in result.output
            assert "Workstation started" not in result.output

    def test_json_output_on_failure_carries_the_outcome_and_the_failure(
            self, cli_runner, generic_command_mocks, clock):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = START_ACCEPTED
            mocks['api_client'].get_edh_workstation.side_effect = [REFUSED]
            result = cli_runner.invoke(cli, self.ARGS + ['--json-output'])
            assert result.exit_code == 1
            body = json.loads(result.output)
            assert body['error_type'] == 'EdhStartFailedError'
            assert body['waitOutcome'] == 'failed'
            assert body['state'] == 'STOPPED'
            assert body['lastError'] == 'CAPACITY_UNAVAILABLE'
            assert body['startFailure'] == CAPACITY_FAILURE
            assert CAPACITY_SENTENCE in body['error']

    @pytest.mark.parametrize("ended", [
        {**STOPPED},
        {**STOPPED, "state": "DELETED", "edhState": None},
    ], ids=["stopped-without-a-reason", "deleted"])
    def test_exits_non_zero_when_the_start_ended_in_another_settled_state(
            self, cli_runner, generic_command_mocks, clock, ended):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = START_ACCEPTED
            mocks['api_client'].get_edh_workstation.side_effect = [ended]
            result = cli_runner.invoke(cli, self.ARGS)
            assert result.exit_code == 1, result.output
            assert f"the workstation is {ended['state']}" in result.output

    def test_exits_non_zero_with_the_sentence_once_the_start_stalled(
            self, cli_runner, generic_command_mocks, clock):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = START_ACCEPTED
            mocks['api_client'].get_edh_workstation.side_effect = [BOOTING, STALLED]
            result = cli_runner.invoke(cli, self.ARGS)
            assert result.exit_code == 1, result.output
            # The wait stops at the first read that shows the stall, well inside its 10 minutes.
            assert mocks['api_client'].get_edh_workstation.call_count == 2
            assert clock.sleeps == [10, 10]
            lines = result.output.splitlines()
            assert "  STALLED" in lines
            assert "Workstation Start Stalled" in result.output
            assert f"Workstation start stalled (START_STALLED): {STALLED_SENTENCE}" in result.output
            assert f"  Start Failure:   {STALLED_SENTENCE}" in lines
            assert "vamscli edh workstation stop s-1" in result.output
            # EDH named no AWS error for a stall, so there is no detail line.
            assert not any(line.strip().startswith("AWS:") for line in lines)
            assert "Workstation started" not in result.output
            assert "Workstation Start Failed" not in result.output

    def test_json_output_on_a_stall_names_the_outcome(self, cli_runner, generic_command_mocks, clock):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = START_ACCEPTED
            mocks['api_client'].get_edh_workstation.side_effect = [STALLED]
            result = cli_runner.invoke(cli, self.ARGS + ['--json-output'])
            assert result.exit_code == 1
            body = json.loads(result.output)
            assert body['error_type'] == 'EdhStartStalledError'
            assert body['waitOutcome'] == 'stalled'
            assert body['state'] == 'CREATING'
            assert body['activity'] is None
            assert body['lastError'] == 'START_STALLED'
            assert body['startFailure'] == STALLED['startFailure']
            assert STALLED_SENTENCE in body['error']

    def test_a_start_still_under_way_on_a_creating_row_keeps_the_wait_going(
            self, cli_runner, generic_command_mocks, clock):
        # Control: a CREATING row with `activity` starting and no stall is not a stall.
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = START_ACCEPTED
            mocks['api_client'].get_edh_workstation.side_effect = [BOOTING, BOOTING, RUNNING]
            result = cli_runner.invoke(cli, self.ARGS)
            assert result.exit_code == 0, result.output
            assert "STALLED" not in result.output

    def test_exits_non_zero_after_10_minutes_of_polling_every_10_seconds(
            self, cli_runner, generic_command_mocks, clock):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = START_ACCEPTED
            mocks['api_client'].get_edh_workstation.return_value = START_ACCEPTED
            result = cli_runner.invoke(cli, self.ARGS)
            assert result.exit_code == 1, result.output
            assert sum(clock.sleeps) == 600
            assert set(clock.sleeps) == {10}
            assert mocks['api_client'].get_edh_workstation.call_count == 60
            assert "still STARTING after 10 minutes" in result.output
            assert "vamscli edh workstation get s-1" in result.output

    def test_json_output_on_timeout_names_the_outcome(self, cli_runner, generic_command_mocks, clock):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = START_ACCEPTED
            mocks['api_client'].get_edh_workstation.return_value = BOOTING
            result = cli_runner.invoke(cli, self.ARGS + ['--json-output'])
            assert result.exit_code == 1
            body = json.loads(result.output)
            assert body['error_type'] == 'EdhStartWaitTimeoutError'
            assert body['waitOutcome'] == 'timedOut'
            assert body['state'] == 'CREATING'
            assert body['activity'] == 'starting'

    def test_a_poll_the_cluster_did_not_answer_is_retried_at_the_next_poll(
            self, cli_runner, generic_command_mocks, clock):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.return_value = START_ACCEPTED
            mocks['api_client'].get_edh_workstation.side_effect = [
                EdhUnavailableError("Workstation lookup failed (502): the EDH cluster did not answer."),
                RUNNING]
            result = cli_runner.invoke(cli, self.ARGS)
            assert result.exit_code == 0, result.output
            assert mocks['api_client'].get_edh_workstation.call_count == 2

    def test_a_refused_start_is_reported_without_polling(self, cli_runner, generic_command_mocks, clock):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].start_edh_workstation.side_effect = EdhOperationError(
                "Workstation start refused (409): Workstation is already running")
            result = cli_runner.invoke(cli, self.ARGS)
            assert result.exit_code == 1
            assert not mocks['api_client'].get_edh_workstation.called
            assert clock.sleeps == []

    def test_the_wait_errors_are_edh_errors_with_their_json_fields(self):
        assert EdhStartFailedError.json_fields == {"waitOutcome": "failed"}
        assert EdhStartWaitTimeoutError.json_fields == {"waitOutcome": "timedOut"}
        stalled = edh_exceptions.EdhStartStalledError
        assert stalled.json_fields == {"waitOutcome": "stalled"}
        assert issubclass(stalled, edh_exceptions.EdhError)
        # Its own class, so a caller that retries a failed start does not retry a stalled one.
        assert not issubclass(stalled, EdhStartFailedError)
        assert stalled("x", STALLED).json_fields == {
            "waitOutcome": "stalled", "state": "CREATING", "activity": None,
            "lastError": "START_STALLED", "startFailure": STALLED["startFailure"]}
