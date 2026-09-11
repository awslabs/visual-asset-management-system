"""Tests for auth routes commands (API route listing)."""

import json
import pytest

from vamscli.main import cli
from vamscli.utils.exceptions import AuthRoutesError


_FULL_ROUTES_RESPONSE = {
    'routes': [
        {'path': '/database', 'methods': ['GET', 'POST'], 'category': 'databases'},
        {'path': '/assets', 'methods': ['GET', 'POST'], 'category': 'assets'},
        {'path': '/auth/routes/api', 'methods': ['GET'], 'category': 'auth'},
    ]
}

_ALLOWED_ROUTES_RESPONSE = {
    'routes': [
        {'path': '/database', 'methods': ['GET'], 'category': 'databases'},
    ],
    'userId': 'test-user',
}


class TestAuthRoutesList:
    """Tests for auth routes list command."""

    def test_list_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mocks['api_client'].list_api_routes.return_value = _FULL_ROUTES_RESPONSE
            result = cli_runner.invoke(cli, ['auth', 'routes', 'list'])
            assert result.exit_code == 0
            assert '/database' in result.output
            assert 'databases' in result.output

    def test_list_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mocks['api_client'].list_api_routes.return_value = _FULL_ROUTES_RESPONSE
            result = cli_runner.invoke(cli, ['auth', 'routes', 'list', '--json-output'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data['routes']) == 3
            assert data['routes'][0]['path'] == '/database'

    def test_list_empty(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mocks['api_client'].list_api_routes.return_value = {'routes': []}
            result = cli_runner.invoke(cli, ['auth', 'routes', 'list'])
            assert result.exit_code == 0

    def test_list_error(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mocks['api_client'].list_api_routes.side_effect = AuthRoutesError("boom")
            result = cli_runner.invoke(cli, ['auth', 'routes', 'list'])
            assert result.exit_code != 0

    def test_list_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('auth') as mocks:
            result = cli_runner.invoke(cli, ['auth', 'routes', 'list'])
            assert result.exit_code != 0


class TestAuthRoutesAllowed:
    """Tests for auth routes allowed command."""

    def test_allowed_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mocks['api_client'].list_allowed_api_routes.return_value = _ALLOWED_ROUTES_RESPONSE
            result = cli_runner.invoke(cli, ['auth', 'routes', 'allowed'])
            assert result.exit_code == 0
            assert '/database' in result.output

    def test_allowed_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mocks['api_client'].list_allowed_api_routes.return_value = _ALLOWED_ROUTES_RESPONSE
            result = cli_runner.invoke(cli, ['auth', 'routes', 'allowed', '--json-output'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['userId'] == 'test-user'
            assert data['routes'][0]['methods'] == ['GET']

    def test_allowed_error(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mocks['api_client'].list_allowed_api_routes.side_effect = AuthRoutesError("boom")
            result = cli_runner.invoke(cli, ['auth', 'routes', 'allowed'])
            assert result.exit_code != 0
