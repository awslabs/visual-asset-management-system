# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""EDH capacity reservations in the CLI: the APIClient methods for the catalog and admin routes, the
launch's `--capacity-reservation-id`, `edh catalog capacity-reservations`, the `edh admin
capacity-reservation` group, and the record's Capacity line.

The client tests replace one HTTP verb and pin the request built and the exception raised; the
command tests mock the APIClient as the other `edh` command tests do.
"""

import json
from unittest.mock import Mock

import pytest
import requests

from vamscli import addon_constants
from vamscli.main import cli
from vamscli.utils.addon.edh_exceptions import (
    EdhCapacityReservationNotFoundError,
    EdhOperationError,
    EdhReservationFullError,
    EdhUnavailableError,
    InvalidEdhRequestError,
)
from vamscli.utils.api_client import APIClient

CR_ID = "cr-0123456789abcdef0"

RENDER_POOL = {
    "capacityReservationId": CR_ID,
    "label": "Render pool",
    "fallbackToOnDemand": True,
    "enabled": True,
    "instanceType": "g5.16xlarge",
    "availabilityZone": "us-east-1a",
    "instancePlatform": "Linux/UNIX",
    "state": "active",
    "totalInstanceCount": 4,
    "availableInstanceCount": 3,
    "instanceMatchCriteria": "targeted",
    "endDate": "2026-10-24T16:00:00Z",
    "reservationType": "default",
}

GONE = {
    **RENDER_POOL,
    "capacityReservationId": "cr-0fedcba9876543210",
    "label": "Old pool",
    "fallbackToOnDemand": False,
    "enabled": False,
    "instanceType": None,
    "availabilityZone": None,
    "instancePlatform": None,
    "state": "notFound",
    "totalInstanceCount": None,
    "availableInstanceCount": None,
    "endDate": None,
}

WORKSTATION = {
    "bootstrapId": "b-1",
    "sessionId": "s-1",
    "state": "CREATING",
    "name": "Render desk",
    "imageId": "12",
    "osFamily": "linux",
    "instanceType": "g5.16xlarge",
    "createdAt": "2026-10-23T10:00:00Z",
    "updatedAt": "2026-10-23T10:00:00Z",
    "connectionAvailable": False,
    "lastError": None,
    "launchAttempts": 0,
}

FULL_BODY = {"message": "The capacity reservation has no free capacity", "code": "RESERVATION_FULL"}


# --- Client --------------------------------------------------------------------


def _client() -> APIClient:
    return APIClient("https://api.example.com", profile_manager=Mock())


class _Response:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _http_error(status: int, body: dict) -> requests.exceptions.HTTPError:
    response = Mock()
    response.status_code = status
    encoded = json.dumps(body)
    response.content = encoded.encode("utf-8")
    response.text = encoded
    response.json.return_value = body
    return requests.exceptions.HTTPError(f"{status} Client Error", response=response)


def _capture(monkeypatch, client, verb, body=None, error=None):
    seen = {}

    def _fake(endpoint, *args, **kwargs):
        seen["endpoint"] = endpoint
        seen["kwargs"] = kwargs
        if error is not None:
            raise error
        return _Response(body)

    monkeypatch.setattr(client, verb, _fake)
    return seen


def test_the_endpoint_constants_name_the_routes():
    assert addon_constants.API_EDH_CATALOG_CAPACITY_RESERVATIONS == "/addon/edh/catalog/capacity-reservations"
    assert addon_constants.API_EDH_ADMIN_CAPACITY_RESERVATIONS == "/addon/edh/admin/capacity-reservations"
    assert (addon_constants.API_EDH_ADMIN_CAPACITY_RESERVATION
            == "/addon/edh/admin/capacity-reservations/{capacityReservationId}")


def test_the_catalog_listing_sends_only_the_filters_given(monkeypatch):
    client = _client()
    seen = _capture(monkeypatch, client, "get", {"Items": [RENDER_POOL]})
    assert client.list_edh_catalog_capacity_reservations() == {"Items": [RENDER_POOL]}
    assert seen["endpoint"] == "/addon/edh/catalog/capacity-reservations"
    assert seen["kwargs"]["params"] == {}
    assert seen["kwargs"]["raise_http_errors"] is True

    client.list_edh_catalog_capacity_reservations(instance_type="g5.16xlarge", os_family="linux")
    assert seen["kwargs"]["params"] == {"instanceType": "g5.16xlarge", "osFamily": "linux"}


def test_the_admin_methods_send_their_verbs_and_bodies(monkeypatch):
    client = _client()
    seen = _capture(monkeypatch, client, "get", {"Items": []})
    assert client.list_edh_admin_capacity_reservations() == {"Items": []}
    assert seen["endpoint"] == "/addon/edh/admin/capacity-reservations"

    seen = _capture(monkeypatch, client, "post", RENDER_POOL)
    body = {"capacityReservationId": CR_ID, "label": "Render pool"}
    assert client.register_edh_capacity_reservation(body) == RENDER_POOL
    assert seen["endpoint"] == "/addon/edh/admin/capacity-reservations"
    assert seen["kwargs"]["data"] == body

    seen = _capture(monkeypatch, client, "put", RENDER_POOL)
    client.update_edh_capacity_reservation(CR_ID, {"enabled": False})
    assert seen["endpoint"] == f"/addon/edh/admin/capacity-reservations/{CR_ID}"
    assert seen["kwargs"]["data"] == {"enabled": False}

    seen = _capture(monkeypatch, client, "delete", {"capacityReservationId": CR_ID})
    client.unregister_edh_capacity_reservation(CR_ID)
    assert seen["endpoint"] == f"/addon/edh/admin/capacity-reservations/{CR_ID}"


@pytest.mark.parametrize("method,args", [
    ("update_edh_capacity_reservation", ("cr-1/../x", {"enabled": False})),
    ("unregister_edh_capacity_reservation", ("../settings",)),
])
def test_a_path_id_that_is_not_a_reservation_id_is_refused_before_any_request(monkeypatch, method, args):
    client = _client()
    verb = "put" if method.startswith("update") else "delete"
    seen = _capture(monkeypatch, client, verb, {})
    with pytest.raises(InvalidEdhRequestError, match="cr- and 17 hex digits"):
        getattr(client, method)(*args)
    assert seen == {}


@pytest.mark.parametrize("method,args,verb", [
    ("update_edh_capacity_reservation", (CR_ID, {"enabled": False}), "put"),
    ("unregister_edh_capacity_reservation", (CR_ID,), "delete"),
])
def test_an_unregistered_reservation_is_reported_as_not_found(monkeypatch, method, args, verb):
    client = _client()
    _capture(monkeypatch, client, verb, error=_http_error(404, {"message": "not registered"}))
    with pytest.raises(EdhCapacityReservationNotFoundError, match=CR_ID):
        getattr(client, method)(*args)


def test_a_duplicate_registration_is_an_operation_error(monkeypatch):
    client = _client()
    _capture(monkeypatch, client, "post", error=_http_error(409, {"message": "already registered"}))
    with pytest.raises(EdhOperationError, match="already registered") as raised:
        client.register_edh_capacity_reservation({"capacityReservationId": CR_ID, "label": "x"})
    assert not isinstance(raised.value, EdhReservationFullError)


def test_a_launch_into_a_full_reservation_raises_the_reservation_full_error(monkeypatch):
    client = _client()
    _capture(monkeypatch, client, "post", error=_http_error(409, FULL_BODY))
    with pytest.raises(EdhReservationFullError, match="no free capacity") as raised:
        client.create_edh_workstation({"capacityReservationId": CR_ID})
    # The code reaches --json-output and the MCP tools as the API's body carries it.
    assert EdhReservationFullError.json_fields == {"code": "RESERVATION_FULL"}
    assert isinstance(raised.value, EdhOperationError)


def test_a_start_is_accepted_with_the_record_and_runs_in_the_background(monkeypatch):
    # The start route answers 202 with the record; a refusal for a full reservation is recorded on the
    # row by the launch worker (lastError RESERVATION_FULL), never answered by the route.
    client = _client()
    accepted = {**WORKSTATION, "state": "STOPPED", "activity": "starting"}
    seen = _capture(monkeypatch, client, "post", accepted)
    assert client.start_edh_workstation("s-1") == accepted
    assert seen["endpoint"] == "/addon/edh/workstations/s-1/start"


def test_a_reservation_read_failure_is_reported_as_unavailable(monkeypatch):
    client = _client()
    _capture(monkeypatch, client, "post", error=_http_error(
        503, {"message": "The capacity reservation could not be read; try again"}))
    with pytest.raises(EdhUnavailableError, match="could not be read") as raised:
        client.create_edh_workstation({"capacityReservationId": CR_ID})
    # EDH was never called, so the message does not blame the cluster.
    assert str(raised.value) == ("Workstation launch failed (503): The capacity reservation could not be "
                                 "read; try again")


# --- Commands --------------------------------------------------------------------


class TestCreateWithReservation:
    ARGS = ['edh', 'workstation', 'create', '--image-id', '12', '--instance-type', 'g5.16xlarge',
            '--name', 'Render desk', '--root-size-gb', '200', '--os-family', 'linux']

    def test_create_sends_the_reservation_when_given(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].create_edh_workstation.return_value = WORKSTATION
            result = cli_runner.invoke(cli, self.ARGS + ['--capacity-reservation-id', CR_ID])
            assert result.exit_code == 0, result.output
            payload = mocks['api_client'].create_edh_workstation.call_args.args[0]
            assert payload['capacityReservationId'] == CR_ID

    def test_create_sends_no_reservation_key_without_the_flag(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].create_edh_workstation.return_value = WORKSTATION
            result = cli_runner.invoke(cli, self.ARGS)
            assert result.exit_code == 0
            assert 'capacityReservationId' not in mocks['api_client'].create_edh_workstation.call_args.args[0]

    def test_create_refuses_an_id_that_is_not_a_reservation_id(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, self.ARGS + ['--capacity-reservation-id', 'cr-123'])
            assert result.exit_code == 2
            assert "Invalid value for '--capacity-reservation-id'" in result.output
            assert not mocks['api_client'].create_edh_workstation.called

    def test_create_reports_a_full_reservation_with_its_remedy(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].create_edh_workstation.side_effect = EdhReservationFullError(
                "Workstation launch refused (409): The capacity reservation has no free capacity")
            result = cli_runner.invoke(cli, self.ARGS + ['--capacity-reservation-id', CR_ID])
            assert result.exit_code != 0
            assert 'no free capacity' in result.output
            assert 'without --capacity-reservation-id' in result.output

    def test_create_json_output_carries_the_reservation_full_code(self, cli_runner, generic_command_mocks):
        message = "Workstation launch refused (409): The capacity reservation has no free capacity"
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].create_edh_workstation.side_effect = EdhReservationFullError(message)
            result = cli_runner.invoke(cli, self.ARGS + ['--capacity-reservation-id', CR_ID, '--json-output'])
            assert result.exit_code == 1
            assert json.loads(result.output) == {
                'error': message,
                'error_type': 'EdhReservationFullError',
                'code': 'RESERVATION_FULL',
            }


class TestRecordCapacityLine:
    def _get(self, cli_runner, generic_command_mocks, record):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].get_edh_workstation.return_value = record
            result = cli_runner.invoke(cli, ['edh', 'workstation', 'get', 's-1'])
            assert result.exit_code == 0, result.output
            return result.output

    def test_the_record_names_its_reservation(self, cli_runner, generic_command_mocks):
        output = self._get(cli_runner, generic_command_mocks, {
            **WORKSTATION, 'capacityReservationId': CR_ID, 'capacityReservationLabel': 'Render pool'})
        assert f"  Capacity:        Render pool ({CR_ID})" in output

    def test_the_record_names_an_unregistered_reservation_by_id(self, cli_runner, generic_command_mocks):
        output = self._get(cli_runner, generic_command_mocks, {
            **WORKSTATION, 'capacityReservationId': CR_ID, 'capacityReservationLabel': None})
        assert f"  Capacity:        {CR_ID}" in output

    def test_the_record_says_a_full_reservation_ran_on_demand(self, cli_runner, generic_command_mocks):
        output = self._get(cli_runner, generic_command_mocks, {
            **WORKSTATION, 'capacityReservationFallback': True, 'capacityReservationRequested': CR_ID})
        assert "  Capacity:        On-demand (reservation full)" in output

    def test_an_on_demand_record_has_no_capacity_line(self, cli_runner, generic_command_mocks):
        output = self._get(cli_runner, generic_command_mocks, WORKSTATION)
        assert "Capacity:" not in output


RESERVATION_COLUMNS = ['LABEL', 'RESERVATION', 'INSTANCE TYPE', 'AZ', 'PLATFORM', 'STATE', 'FREE/TOTAL',
                       'END DATE', 'FALLBACK', 'ENABLED']


class TestCatalogCapacityReservations:
    def test_lists_the_reservations_in_the_web_table_s_columns(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_capacity_reservations.return_value = {
                'Items': [{**RENDER_POOL, 'launchAllowed': True}]}
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'capacity-reservations'])
            assert result.exit_code == 0, result.output
            assert 'Found 1 capacity reservation(s)' in result.output
            lines = result.output.splitlines()
            header = next(line for line in lines if line.startswith('LABEL'))
            # The catalog adds LAUNCH (whether the role may launch into the reservation) as its last column.
            assert [c for c in RESERVATION_COLUMNS + ['LAUNCH'] if c not in header] == []
            assert header.split()[-1] == 'LAUNCH'
            row = next(line for line in lines if line.startswith('Render pool'))
            assert row.split() == ['Render', 'pool', CR_ID, 'g5.16xlarge', 'us-east-1a', 'Linux/UNIX', 'active',
                                   '3/4', '2026-10-24T16:00:00Z', 'on-demand', 'yes', 'yes']
            mocks['api_client'].list_edh_catalog_capacity_reservations.assert_called_once_with(
                instance_type=None, os_family=None)

    def test_passes_the_filters(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_catalog_capacity_reservations.return_value = {'Items': []}
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'capacity-reservations', '--instance-type',
                                             'g5.16xlarge', '--os-family', 'windows'])
            assert result.exit_code == 0
            mocks['api_client'].list_edh_catalog_capacity_reservations.assert_called_once_with(
                instance_type='g5.16xlarge', os_family='windows')
            assert 'No capacity reservations found.' in result.output

    def test_refuses_an_unknown_os_family(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'capacity-reservations', '--os-family', 'mac'])
            assert result.exit_code == 2
            assert not mocks['api_client'].list_edh_catalog_capacity_reservations.called

    def test_json_output_is_the_api_body(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            body = {'Items': [RENDER_POOL]}
            mocks['api_client'].list_edh_catalog_capacity_reservations.return_value = body
            result = cli_runner.invoke(cli, ['edh', 'catalog', 'capacity-reservations', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == body


class TestAdminCapacityReservation:
    def test_list_shows_ec2_s_view_and_the_registry_s_switches(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].list_edh_admin_capacity_reservations.return_value = {'Items': [RENDER_POOL, GONE]}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'capacity-reservation', 'list'])
            assert result.exit_code == 0, result.output
            lines = result.output.splitlines()
            gone = next(line for line in lines if line.startswith('Old pool'))
            assert gone.split() == ['Old', 'pool', 'cr-0fedcba9876543210', '-', '-', '-', 'notFound', '-', '-',
                                    'refuse', 'no']

    def test_list_json_output_is_the_api_body(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            body = {'Items': [RENDER_POOL]}
            mocks['api_client'].list_edh_admin_capacity_reservations.return_value = body
            result = cli_runner.invoke(cli, ['edh', 'admin', 'capacity-reservation', 'list', '--json-output'])
            assert json.loads(result.output) == body

    def test_add_sends_the_id_the_label_and_only_the_switches_given(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].register_edh_capacity_reservation.return_value = RENDER_POOL
            result = cli_runner.invoke(cli, ['edh', 'admin', 'capacity-reservation', 'add',
                                             '--capacity-reservation-id', CR_ID, '--label', 'Render pool'])
            assert result.exit_code == 0, result.output
            sent = mocks['api_client'].register_edh_capacity_reservation.call_args.args[0]
            assert sent == {'capacityReservationId': CR_ID, 'label': 'Render pool'}
            assert 'Capacity reservation registered' in result.output

            cli_runner.invoke(cli, ['edh', 'admin', 'capacity-reservation', 'add', '--capacity-reservation-id',
                                    CR_ID, '--label', 'Render pool', '--no-fallback', '--disabled'])
            sent = mocks['api_client'].register_edh_capacity_reservation.call_args.args[0]
            assert sent == {'capacityReservationId': CR_ID, 'label': 'Render pool',
                            'fallbackToOnDemand': False, 'enabled': False}

    def test_add_refuses_a_bad_id_and_a_long_label(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, ['edh', 'admin', 'capacity-reservation', 'add',
                                             '--capacity-reservation-id', 'i-123', '--label', 'x'])
            assert result.exit_code == 2
            result = cli_runner.invoke(cli, ['edh', 'admin', 'capacity-reservation', 'add',
                                             '--capacity-reservation-id', CR_ID, '--label', 'x' * 65])
            assert result.exit_code == 2
            assert not mocks['api_client'].register_edh_capacity_reservation.called

    def test_update_sends_exactly_the_flags_given(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].update_edh_capacity_reservation.return_value = RENDER_POOL
            result = cli_runner.invoke(cli, ['edh', 'admin', 'capacity-reservation', 'update', CR_ID,
                                             '--label', 'Render farm', '--fallback', '--enabled'])
            assert result.exit_code == 0, result.output
            mocks['api_client'].update_edh_capacity_reservation.assert_called_once_with(
                CR_ID, {'label': 'Render farm', 'fallbackToOnDemand': True, 'enabled': True})
            assert 'Capacity reservation updated' in result.output

    def test_update_refuses_an_empty_patch(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, ['edh', 'admin', 'capacity-reservation', 'update', CR_ID])
            assert result.exit_code == 2
            assert 'Pass at least one of' in result.output
            assert not mocks['api_client'].update_edh_capacity_reservation.called

    def test_update_reports_an_unregistered_reservation(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].update_edh_capacity_reservation.side_effect = EdhCapacityReservationNotFoundError(
                f"Capacity reservation '{CR_ID}' is not registered")
            result = cli_runner.invoke(cli, ['edh', 'admin', 'capacity-reservation', 'update', CR_ID,
                                             '--disabled'])
            assert result.exit_code != 0
            assert 'is not registered' in result.output
            assert 'vamscli edh admin capacity-reservation list' in result.output

    def test_remove_requires_confirmation(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            result = cli_runner.invoke(cli, ['edh', 'admin', 'capacity-reservation', 'remove', CR_ID])
            assert result.exit_code != 0
            assert 'requires the --confirm flag' in result.output or 'Use --confirm' in result.output
            assert not mocks['api_client'].unregister_edh_capacity_reservation.called

    def test_remove_unregisters_with_confirmation(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('addon.edh') as mocks:
            mocks['api_client'].unregister_edh_capacity_reservation.return_value = {'capacityReservationId': CR_ID}
            result = cli_runner.invoke(cli, ['edh', 'admin', 'capacity-reservation', 'remove', CR_ID, '--confirm'])
            assert result.exit_code == 0, result.output
            mocks['api_client'].unregister_edh_capacity_reservation.assert_called_once_with(CR_ID)
            assert 'Capacity reservation unregistered' in result.output
            assert 'EC2 reservation itself is not changed' in result.output

    def test_the_group_lists_its_commands(self, cli_runner):
        result = cli_runner.invoke(cli, ['edh', 'admin', 'capacity-reservation', '--help'])
        assert result.exit_code == 0
        for command in ('list', 'add', 'update', 'remove'):
            assert f"  {command} " in result.output
