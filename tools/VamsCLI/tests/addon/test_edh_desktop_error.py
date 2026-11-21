# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""A workstation that has run which EDH reports in error, in the CLI (#1731).

The service keeps such a workstation in its state and records `connectionUnavailableReason` DESKTOP_ERROR on it;
connect, the desktop sign-in and a start of it are refused with 409 and that code. `edh workstation get` prints the
sentence the web shows under its Connect (read from web/src/addons/edh/workstationStatusCases.json, which the web
test reads too), and `connect` and `start` exit non-zero with it, carrying the code into --json-output.
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from vamscli.main import cli
from vamscli.utils.addon.edh_exceptions import (
    EdhDesktopErrorError, EdhDisplaySessionEndedError, EdhOperationError,
)
from vamscli.utils.api_client import APIClient

REPO_ROOT = Path(__file__).resolve().parents[4]
CASES = json.loads((REPO_ROOT / "web/src/addons/edh/workstationStatusCases.json").read_text(encoding="utf-8"))[
    "cases"]
SENTENCE = ("The workstation's remote display is not responding; its disk and data are kept. Wait a few minutes, "
            "or stop it, then start it again, to reconnect.")
ERROR_BODY = {"code": "DESKTOP_ERROR", "message": SENTENCE}


def _http_error(status: int, body: dict) -> requests.exceptions.HTTPError:
    response = Mock()
    response.status_code = status
    encoded = json.dumps(body)
    response.content = encoded.encode("utf-8")
    response.text = encoded
    response.json.return_value = body
    return requests.exceptions.HTTPError(f"{status} Client Error", response=response)


def _refusing_client(monkeypatch, body):
    client = APIClient("https://api.example.com", profile_manager=Mock())

    def _fake(endpoint, *args, **kwargs):
        raise _http_error(409, body)

    monkeypatch.setattr(client, "get", _fake)
    monkeypatch.setattr(client, "post", _fake)
    return client


IN_ERROR = [c for c in CASES if c["record"].get("connectionUnavailableReason") == "DESKTOP_ERROR"
            and c["record"].get("lastError") not in ("LAUNCH_STALLED", "START_STALLED")]
START_STALLED_IN_ERROR = [c for c in CASES if c["record"].get("connectionUnavailableReason") == "DESKTOP_ERROR"
                          and c["record"].get("lastError") == "START_STALLED"]
BOTH = [c for c in CASES if c["record"].get("connectionUnavailableReason") == "DESKTOP_ERROR"
        and c["record"].get("lastError") == "LAUNCH_STALLED"]


def test_the_shared_fixture_has_the_cases():
    """Control: the file the web test reads names such a workstation in each state it keeps, with the sentence,
    and one record carrying a launch stall as well, which reads as the launch stall."""
    assert sorted(case["record"]["state"] for case in IN_ERROR) == ["ACTIVE", "CREATING", "STOPPED"]
    assert {case["connectionUnavailableMessage"] for case in IN_ERROR} == {SENTENCE}
    assert [(case["badge"], case["state"]) for case in BOTH] == [("Launch stalled", "STALLED")]
    assert "connectionUnavailableMessage" not in BOTH[0]
    assert [(case["badge"], case["state"], case["connectionUnavailableMessage"])
            for case in START_STALLED_IN_ERROR] == [("Start stalled", "STALLED", SENTENCE)]


def test_a_stalled_start_edh_then_reports_in_error_shows_both_sentences(cli_runner, generic_command_mocks):
    """A started desktop has run, so EDH's error keeps it CREATING with DESKTOP_ERROR and its START_STALLED: `get`
    reads STALLED with the stall's Start Failure sentence, and prints the desktop error's sentence as Connection."""
    case = START_STALLED_IN_ERROR[0]
    record = {"bootstrapId": "b-1", "userId": "user@example.com", "edhUid": "vabcdefghij23456",
              "name": "Render-box", "imageId": "12", "osFamily": "linux", "createdAt": "2026-11-19T10:00:00Z",
              "updatedAt": "2026-11-19T15:19:13Z", "launchAttempts": 1, **case["record"]}
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].get_edh_workstation.return_value = record
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'get', 's-1'])
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        assert "  State:           STALLED" in lines
        assert f"  Start Failure:   {case['startFailureMessage']}" in lines
        assert lines[lines.index("  Connectable:     False") + 1] == f"  Connection:      {SENTENCE}"
        assert not any("vamscli edh workstation delete" in line for line in lines)


def test_a_launch_stall_outranks_a_desktop_error_on_one_record(cli_runner, generic_command_mocks):
    """The service never writes both (EDH's error on a launch that never ran orphans it); a record that carries
    both reads as the launch stall: STALLED, its sentence and the delete line, and no desktop-error Connection."""
    record = {"bootstrapId": "b-1", "userId": "user@example.com", "edhUid": "vabcdefghij23456",
              "name": "Render-box", "imageId": "12", "osFamily": "linux", "createdAt": "2026-11-19T10:00:00Z",
              "updatedAt": "2026-11-19T15:19:13Z", "launchAttempts": 1, **BOTH[0]["record"]}
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].get_edh_workstation.return_value = record
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'get', 's-1'])
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        assert "  State:           STALLED" in lines
        assert f"  Start Failure:   {BOTH[0]['startFailureMessage']}" in lines
        assert any(line.strip() == "To delete it: vamscli edh workstation delete s-1 --confirm" for line in lines)
        assert not any(line.startswith("  Connection:") for line in lines)
        assert SENTENCE not in result.output


@pytest.mark.parametrize("case", IN_ERROR, ids=lambda c: c["name"])
def test_get_prints_the_sentence_and_list_the_state_it_keeps(cli_runner, generic_command_mocks, case):
    record = {"bootstrapId": "b-1", "userId": "user@example.com", "edhUid": "vabcdefghij23456",
              "name": "Render-box", "imageId": "12", "osFamily": "linux", "createdAt": "2026-11-19T10:00:00Z",
              "updatedAt": "2026-11-19T15:19:13Z", "launchAttempts": 1, **case["record"]}
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].get_edh_workstation.return_value = record
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'get', 's-1'])
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        assert lines[lines.index("  Connectable:     False") + 1] == f"  Connection:      {SENTENCE}"


@pytest.mark.parametrize("call", ["get_edh_workstation_connection", "get_edh_workstation_os_sign_in"])
def test_a_connect_or_sign_in_refused_for_a_desktop_error_raises_its_own_error(monkeypatch, call):
    client = _refusing_client(monkeypatch, ERROR_BODY)
    with pytest.raises(EdhDesktopErrorError) as raised:
        getattr(client, call)("s-1")
    assert SENTENCE in str(raised.value)
    assert isinstance(raised.value, EdhOperationError)
    assert not isinstance(raised.value, EdhDisplaySessionEndedError)
    assert EdhDesktopErrorError.json_fields == {"code": "DESKTOP_ERROR"}


def test_a_start_refused_for_a_desktop_error_raises_its_own_error(monkeypatch):
    client = _refusing_client(monkeypatch, ERROR_BODY)
    with pytest.raises(EdhDesktopErrorError):
        client.start_edh_workstation("s-1")


def test_connect_exits_non_zero_with_the_sentence_and_the_code(cli_runner, generic_command_mocks):
    message = f"Workstation connection refused (409): {SENTENCE}"
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].get_edh_workstation_connection.side_effect = EdhDesktopErrorError(message)
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'connect', 's-1'])
        assert result.exit_code != 0
        assert SENTENCE in " ".join(result.output.split())
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'connect', 's-1', '--json-output'])
        assert result.exit_code == 1
        assert json.loads(result.output) == {
            'error': message, 'error_type': 'EdhDesktopErrorError', 'code': 'DESKTOP_ERROR'}
