# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The capacity-reservation display in the CLI (feature #536, D17/D18/D19): the `Capacity:` line of
`edh workstation get`, the STATE and LAUNCH columns of `edh catalog capacity-reservations`, the
no-subnet note of `edh admin capacity-reservation list`, and the typed error of a launch into a
reservation the role may view but not launch into (403 RESERVATION_NOT_PERMITTED).

The CLI half of the CLI/web consistency check reads the same fixture as the web's
capacityDisplayCases.test.tsx, web/src/addons/edh/capacityDisplayCases.json.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from vamscli.main import cli
from vamscli.utils.addon.edh_exceptions import EdhError, EdhReservationNotPermittedError
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import PermissionDeniedError

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURE = json.loads((REPO_ROOT / "web/src/addons/edh/capacityDisplayCases.json").read_text(encoding="utf-8"))
RECORDS = FIXTURE["records"]
OPTIONS = FIXTURE["catalogOptions"]

CR_ID = "cr-0123456789abcdef0"
NOT_PERMITTED_MESSAGE = "Your role may view this capacity reservation but not launch into it"
NOT_PERMITTED_BODY = {"message": NOT_PERMITTED_MESSAGE, "code": "RESERVATION_NOT_PERMITTED"}

WORKSTATION = {
    "bootstrapId": "b-1",
    "sessionId": "s-1",
    "userId": "user@example.com",
    "edhUid": "vabcdefghij23456",
    "state": "STOPPED",
    "edhState": "stopped",
    "activity": None,
    "name": "Render desk",
    "imageId": "12",
    "osFamily": "linux",
    "instanceType": "g5.16xlarge",
    "createdAt": "2026-10-23T10:00:00Z",
    "updatedAt": "2026-10-23T10:00:00Z",
    "connectionAvailable": False,
    "lastError": None,
    "launchAttempts": 1,
}

CREATE_ARGS = ['edh', 'workstation', 'create', '--image-id', '12', '--instance-type', 'g5.16xlarge',
               '--name', 'Render desk', '--root-size-gb', '200', '--os-family', 'linux',
               '--capacity-reservation-id', CR_ID]


CATALOG_COLUMNS = ['LABEL', 'RESERVATION', 'INSTANCE TYPE', 'AZ', 'PLATFORM', 'STATE', 'FREE/TOTAL', 'END DATE',
                   'FALLBACK', 'ENABLED', 'LAUNCH']
ADMIN_COLUMNS = CATALOG_COLUMNS[:-1]


def _ids(cases):
    return [case["name"] for case in cases]


def _columns(output: str, names):
    """The text table of a capacity reservation listing as {label: {column: cell}}: each row is cut at
    the header's column starts, so a cell with spaces in it stays whole."""
    lines = output.splitlines()
    header_index = next(i for i, line in enumerate(lines) if line.startswith("LABEL"))
    header = lines[header_index]
    starts, position = [], 0
    for name in names:
        position = header.index(name, position)
        starts.append((name, position))
        position += len(name)
    assert header[position:].strip() == "", header
    rows = {}
    for line in lines[header_index + 1:]:
        if not line.strip():
            break
        cells = {}
        for i, (name, start) in enumerate(starts):
            end = starts[i + 1][1] if i + 1 < len(starts) else None
            cells[name] = line[start:end].strip()
        rows[cells["LABEL"]] = cells
    return rows


def test_the_shared_fixture_covers_the_contract_s_cases():
    """Control: the file the web test reads too, with every capacity detail and catalog option."""
    reasons = {case["record"].get("capacityReservationFallbackReason") for case in RECORDS}
    assert reasons == {"full", "ended", None}
    assert {case["cliLaunch"] for case in OPTIONS} == {"yes", "view only"}
    assert any(case["view"]["edhSubnetAvailable"] is False for case in OPTIONS)
    assert any("No EDH subnet in" in case["cliState"] for case in OPTIONS)


@pytest.mark.parametrize("case", RECORDS, ids=_ids(RECORDS))
def test_get_prints_the_capacity_line_the_web_shows(cli_runner, generic_command_mocks, case):
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].get_edh_workstation.return_value = {**WORKSTATION, **case["record"]}
        result = cli_runner.invoke(cli, ['edh', 'workstation', 'get', 's-1'])
        assert result.exit_code == 0, result.output
        lines = [line for line in result.output.splitlines() if line.startswith("  Capacity:")]
        if case["capacity"] is None:
            assert lines == []
        else:
            assert lines == [f"  Capacity:        {case['capacity']}"]


def test_the_catalog_prints_the_state_and_launch_columns_the_fixture_agrees(cli_runner, generic_command_mocks):
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].list_edh_catalog_capacity_reservations.return_value = {
            'Items': [case["view"] for case in OPTIONS]}
        result = cli_runner.invoke(cli, ['edh', 'catalog', 'capacity-reservations'])
        assert result.exit_code == 0, result.output
        rows = _columns(result.output, CATALOG_COLUMNS)
        assert list(rows) == [case["view"]["label"] for case in OPTIONS]
        for case in OPTIONS:
            row = rows[case["view"]["label"]]
            assert row["STATE"] == case["cliState"], case["name"]
            assert row["LAUNCH"] == case["cliLaunch"], case["name"]


def test_the_catalog_s_launch_column_reads_a_dash_for_a_service_without_the_field(
        cli_runner, generic_command_mocks):
    view = {key: value for key, value in OPTIONS[-1]["view"].items() if key != "launchAllowed"}
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].list_edh_catalog_capacity_reservations.return_value = {'Items': [view]}
        result = cli_runner.invoke(cli, ['edh', 'catalog', 'capacity-reservations'])
        assert _columns(result.output, CATALOG_COLUMNS)[view["label"]]["LAUNCH"] == "-"


def test_the_admin_list_notes_a_missing_edh_subnet_and_has_no_launch_column(cli_runner, generic_command_mocks):
    # The admin list carries no launchAllowed: it lists every registered reservation.
    views = [{key: value for key, value in case["view"].items() if key != "launchAllowed"} for case in OPTIONS]
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].list_edh_admin_capacity_reservations.return_value = {'Items': views}
        result = cli_runner.invoke(cli, ['edh', 'admin', 'capacity-reservation', 'list'])
        assert result.exit_code == 0, result.output
        rows = _columns(result.output, ADMIN_COLUMNS)
        assert "LAUNCH" not in next(line for line in result.output.splitlines() if line.startswith("LABEL"))
        for case in OPTIONS:
            assert rows[case["view"]["label"]]["STATE"] == case["cliState"], case["name"]


def test_json_output_passes_the_new_fields_unchanged(cli_runner, generic_command_mocks):
    body = {'Items': [case["view"] for case in OPTIONS]}
    with generic_command_mocks('addon.edh') as mocks:
        mocks['api_client'].list_edh_catalog_capacity_reservations.return_value = body
        result = cli_runner.invoke(cli, ['edh', 'catalog', 'capacity-reservations', '--json-output'])
        assert json.loads(result.output) == body


# --- A reservation the role may view but not launch into ---------------------------------------


def _wire_client(monkeypatch, status, body):
    """An APIClient whose session answers every request with `status` and `body`, through
    _make_request's own reading of the response (a handler's 403 included)."""
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = False
    profile_manager.is_token_expired.return_value = False
    profile_manager.load_auth_profile.return_value = {"access_token": "token"}
    client = APIClient("https://api.example.com", profile_manager=profile_manager)
    response = requests.Response()
    response.status_code = status
    response.reason = "Forbidden"
    response.url = "https://api.example.com/addon/edh/workstations"
    response._content = json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    monkeypatch.setattr(client.session, "request", lambda method, url, **kwargs: response)
    monkeypatch.setattr(client, "_try_refresh_token", lambda token_before=None: True)
    return client


def test_a_handler_403_carries_the_body_s_code_on_the_permission_denial(monkeypatch):
    client = _wire_client(monkeypatch, 403, NOT_PERMITTED_BODY)
    with pytest.raises(PermissionDeniedError) as raised:
        client.get("/addon/edh/catalog/images", include_auth=True, raise_http_errors=True)
    assert raised.value.code == "RESERVATION_NOT_PERMITTED"
    assert type(raised.value) is EdhReservationNotPermittedError
    assert str(raised.value) == f"Access forbidden. {NOT_PERMITTED_MESSAGE}"
    # A 403 without a code, or with one no error stands for, is the plain denial.
    for body, code in (({"message": "Not Authorized"}, None),
                       ({"message": "Not Authorized", "code": "SOMETHING_ELSE"}, "SOMETHING_ELSE")):
        client = _wire_client(monkeypatch, 403, body)
        with pytest.raises(PermissionDeniedError) as raised:
            client.get("/addon/edh/catalog/images", include_auth=True, raise_http_errors=True)
        assert type(raised.value) is PermissionDeniedError
        assert raised.value.code == code


def test_for_code_picks_the_subclass_that_declares_the_code():
    assert PermissionDeniedError.for_code("RESERVATION_NOT_PERMITTED") is EdhReservationNotPermittedError
    assert PermissionDeniedError.for_code(None) is PermissionDeniedError
    assert PermissionDeniedError.for_code("") is PermissionDeniedError
    assert PermissionDeniedError.for_code("UNKNOWN") is PermissionDeniedError


def test_a_launch_the_role_may_only_view_raises_the_typed_error(monkeypatch):
    client = _wire_client(monkeypatch, 403, NOT_PERMITTED_BODY)
    with pytest.raises(EdhReservationNotPermittedError) as raised:
        client.create_edh_workstation({"capacityReservationId": CR_ID})
    assert str(raised.value) == f"Access forbidden. {NOT_PERMITTED_MESSAGE}"
    # An EDH error the `edh` commands report with its remedy, and still the permission denial the
    # client's guards let through to the global handler.
    assert isinstance(raised.value, EdhError)
    assert isinstance(raised.value, PermissionDeniedError)
    assert raised.value.code == "RESERVATION_NOT_PERMITTED"
    assert EdhReservationNotPermittedError.json_fields == {"code": "RESERVATION_NOT_PERMITTED"}


def test_a_launch_refused_without_the_code_stays_a_permission_denial(monkeypatch):
    # Without GET on the reservation the service answers the generic 403: nothing is disclosed.
    client = _wire_client(monkeypatch, 403, {"message": "Not Authorized"})
    with pytest.raises(PermissionDeniedError) as raised:
        client.create_edh_workstation({"capacityReservationId": CR_ID})
    assert not isinstance(raised.value, EdhReservationNotPermittedError)


def test_the_edh_error_mapping_reads_the_code_of_a_403_it_sees(monkeypatch):
    # The path a 403 takes when it reaches the add-on's own mapping as an HTTPError.
    client = APIClient("https://api.example.com", profile_manager=MagicMock())
    response = MagicMock()
    response.status_code = 403
    response.content = json.dumps(NOT_PERMITTED_BODY).encode("utf-8")
    response.json.return_value = NOT_PERMITTED_BODY

    def _raise(*args, **kwargs):
        raise requests.exceptions.HTTPError("403 Client Error", response=response)

    monkeypatch.setattr(client, "post", _raise)
    with pytest.raises(EdhReservationNotPermittedError, match=NOT_PERMITTED_MESSAGE):
        client.create_edh_workstation({"capacityReservationId": CR_ID})


class TestCreateIntoAViewOnlyReservation:
    MESSAGE = f"Access forbidden. {NOT_PERMITTED_MESSAGE}"

    def test_prints_the_reason_and_the_remedy(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].create_edh_workstation.side_effect = EdhReservationNotPermittedError(self.MESSAGE)
            result = cli_runner.invoke(cli, CREATE_ARGS)
            assert result.exit_code != 0
            text = ' '.join(result.output.split())
            assert 'Capacity Reservation Not Permitted' in text
            assert NOT_PERMITTED_MESSAGE in text
            assert 'without --capacity-reservation-id' in text
            assert 'LAUNCH column reads yes' in text

    def test_json_output_carries_the_code(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].create_edh_workstation.side_effect = EdhReservationNotPermittedError(self.MESSAGE)
            result = cli_runner.invoke(cli, CREATE_ARGS + ['--json-output'])
            assert result.exit_code == 1
            assert json.loads(result.output) == {
                'error': self.MESSAGE,
                'error_type': 'EdhReservationNotPermittedError',
                'code': 'RESERVATION_NOT_PERMITTED',
            }


def test_the_catalog_help_names_the_launch_column(cli_runner):
    result = cli_runner.invoke(cli, ['edh', 'catalog', 'capacity-reservations', '--help'])
    text = ' '.join(result.output.split())
    assert 'LAUNCH' in text and 'view only' in text
