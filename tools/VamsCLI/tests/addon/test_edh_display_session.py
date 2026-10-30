# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""A running workstation whose remote display session has ended, in the CLI (feature #536, D20).

The service records `connectionUnavailableReason` DISPLAY_SESSION_ENDED on the workstation and refuses
its connect with 409 and that code. `edh workstation get` prints the sentence the web shows under its
inert Connect (read from the fixture the web test reads too,
web/src/addons/edh/workstationStatusCases.json), and `edh workstation connect` exits non-zero with it.
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from vamscli.main import cli
from vamscli.utils.addon.edh_exceptions import EdhDisplaySessionEndedError, EdhOperationError
from vamscli.utils.api_client import APIClient

REPO_ROOT = Path(__file__).resolve().parents[4]
CASES = json.loads((REPO_ROOT / "web/src/addons/edh/workstationStatusCases.json").read_text(encoding="utf-8"))[
    "cases"]
SENTENCE = ("The workstation is running but its remote display session has ended. "
            "Stop it, then start it again, to reconnect.")
ENDED_BODY = {"code": "DISPLAY_SESSION_ENDED", "message": SENTENCE}

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
    return client


def test_the_shared_fixture_has_the_case():
    """Control: the file the web test reads names one such workstation, with the sentence."""
    ended = [case for case in CASES if case["record"].get("connectionUnavailableReason") == "DISPLAY_SESSION_ENDED"]
    assert [case["connectionUnavailableMessage"] for case in ended] == [SENTENCE]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_get_prints_the_connect_sentence_the_web_shows(cli_runner, generic_command_mocks, case):
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].get_edh_workstation.return_value = {**BASE, **case["record"]}
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'get', 's-1'])
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        connection = [line for line in lines if line.startswith("  Connection:")]
        message = case.get("connectionUnavailableMessage")
        if message is None:
            assert connection == []
            return
        # Beneath the Connectable line, in the record's value column.
        assert lines[lines.index("  Connectable:     False") + 1] == f"  Connection:      {message}"


def test_a_connect_refused_for_an_ended_session_raises_its_own_error(monkeypatch):
    client = _refusing_client(monkeypatch, ENDED_BODY)
    with pytest.raises(EdhDisplaySessionEndedError) as raised:
        client.get_edh_workstation_connection("s-1")
    assert SENTENCE in str(raised.value)
    assert isinstance(raised.value, EdhOperationError)
    # The code reaches --json-output and the MCP tools as the API's body carries it.
    assert EdhDisplaySessionEndedError.json_fields == {"code": "DISPLAY_SESSION_ENDED"}


def test_a_desktop_sign_in_refused_for_an_ended_session_raises_its_own_error(monkeypatch):
    client = _refusing_client(monkeypatch, ENDED_BODY)
    with pytest.raises(EdhDisplaySessionEndedError):
        client.get_edh_workstation_os_sign_in("s-1")


def test_any_other_not_ready_refusal_stays_an_operation_error(monkeypatch):
    client = _refusing_client(monkeypatch, {"message": "Workstation is not ready for connection"})
    with pytest.raises(EdhOperationError) as raised:
        client.get_edh_workstation_connection("s-1")
    assert not isinstance(raised.value, EdhDisplaySessionEndedError)


def test_connect_exits_non_zero_with_the_sentence(cli_runner, generic_command_mocks):
    message = f"Workstation connection refused (409): {SENTENCE}"
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].get_edh_workstation_connection.side_effect = EdhDisplaySessionEndedError(message)
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'connect', 's-1'])
        assert result.exit_code != 0
        assert SENTENCE in " ".join(result.output.split())


def test_connect_json_output_carries_the_code(cli_runner, generic_command_mocks):
    message = f"Workstation connection refused (409): {SENTENCE}"
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].get_edh_workstation_connection.side_effect = EdhDisplaySessionEndedError(message)
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'connect', 's-1', '--json-output'])
        assert result.exit_code == 1
        assert json.loads(result.output) == {
            'error': message, 'error_type': 'EdhDisplaySessionEndedError', 'code': 'DISPLAY_SESSION_ENDED'}
