"""Tests for api-key user (self-service) commands."""

import json
import pytest
from click.testing import CliRunner
from unittest.mock import Mock

from vamscli.main import cli
from vamscli.utils.exceptions import (
    ApiKeyNotFoundError,
    ApiKeyCreationError,
    ApiKeyDeletionError,
    ApiKeyUpdateError,
)


class TestUserApiKeyList:
    """Tests for api-key user list command."""

    def test_list_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].list_user_api_keys.return_value = {
                'Items': [
                    {'apiKeyId': 'key-1', 'apiKeyName': 'My Key', 'userId': 'me@test.com',
                     'expiresAt': '2026-12-31T23:59:59Z', 'isActive': 'true'}
                ]
            }
            result = cli_runner.invoke(cli, ['api-key', 'user', 'list'])
            assert result.exit_code == 0
            assert 'My Key' in result.output
            mocks['api_client'].list_user_api_keys.assert_called_once()

    def test_list_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            expected = {'Items': [{'apiKeyId': 'key-1', 'apiKeyName': 'My Key'}]}
            mocks['api_client'].list_user_api_keys.return_value = expected
            result = cli_runner.invoke(cli, ['api-key', 'user', 'list', '--json-output'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['Items'][0]['apiKeyId'] == 'key-1'

    def test_list_empty(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].list_user_api_keys.return_value = {'Items': []}
            result = cli_runner.invoke(cli, ['api-key', 'user', 'list'])
            assert result.exit_code == 0

    def test_list_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('apiKey') as mocks:
            result = cli_runner.invoke(cli, ['api-key', 'user', 'list'])
            assert result.exit_code != 0


class TestUserApiKeyCreate:
    """Tests for api-key user create command."""

    def test_create_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].create_user_api_key.return_value = {
                'apiKeyId': 'new-key-id',
                'apiKeyName': 'My Key',
                'userId': 'me@test.com',
                'apiKey': 'vams_selfkey123',
                'expiresAt': '2026-12-31T23:59:59Z',
            }
            result = cli_runner.invoke(cli, [
                'api-key', 'user', 'create',
                '--name', 'My Key',
                '--description', 'Self-service key',
                '--expires-at', '2026-12-31T23:59:59Z',
            ])
            assert result.exit_code == 0
            assert 'vams_selfkey123' in result.output
            mocks['api_client'].create_user_api_key.assert_called_once_with({
                'apiKeyName': 'My Key',
                'description': 'Self-service key',
                'expiresAt': '2026-12-31T23:59:59Z',
            })

    def test_create_requires_expires_at(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            result = cli_runner.invoke(cli, [
                'api-key', 'user', 'create',
                '--name', 'My Key',
                '--description', 'No expiration',
            ])
            assert result.exit_code != 0
            assert 'expires-at' in result.output.lower() or 'Missing option' in result.output

    def test_create_does_not_accept_user_id(self, cli_runner, generic_command_mocks):
        """The user route never accepts a userId — the key is always self-owned."""
        with generic_command_mocks('apiKey') as mocks:
            result = cli_runner.invoke(cli, [
                'api-key', 'user', 'create',
                '--name', 'My Key',
                '--user-id', 'other@test.com',
                '--description', 'd',
                '--expires-at', '2026-12-31',
            ])
            assert result.exit_code != 0  # unknown option

    def test_create_backend_rejects_long_expiration(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].create_user_api_key.side_effect = ApiKeyCreationError(
                "API key creation failed: Expiration date cannot be more than 365 days after the key's creation date")
            result = cli_runner.invoke(cli, [
                'api-key', 'user', 'create',
                '--name', 'My Key',
                '--description', 'd',
                '--expires-at', '2030-01-01',
            ])
            assert result.exit_code != 0
            assert '365' in result.output

    def test_create_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            expected = {'apiKeyId': 'k', 'apiKey': 'vams_x', 'expiresAt': '2026-12-31'}
            mocks['api_client'].create_user_api_key.return_value = expected
            result = cli_runner.invoke(cli, [
                'api-key', 'user', 'create',
                '--name', 'K', '--description', 'd', '--expires-at', '2026-12-31',
                '--json-output',
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['apiKey'] == 'vams_x'


class TestUserApiKeyUpdate:
    """Tests for api-key user update command."""

    def test_update_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].update_user_api_key.return_value = {
                'apiKeyId': 'key-1', 'description': 'Updated', 'expiresAt': '2026-11-30',
            }
            result = cli_runner.invoke(cli, [
                'api-key', 'user', 'update',
                '--api-key-id', 'key-1',
                '--description', 'Updated',
            ])
            assert result.exit_code == 0
            mocks['api_client'].update_user_api_key.assert_called_once_with(
                'key-1', {'description': 'Updated'})

    def test_update_requires_at_least_one_field(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            result = cli_runner.invoke(cli, [
                'api-key', 'user', 'update', '--api-key-id', 'key-1',
            ])
            assert result.exit_code != 0

    def test_update_not_owned_key(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].update_user_api_key.side_effect = ApiKeyNotFoundError("not found")
            result = cli_runner.invoke(cli, [
                'api-key', 'user', 'update',
                '--api-key-id', 'key-x', '--description', 'd',
            ])
            assert result.exit_code != 0

    def test_update_expiration_window_rejection(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].update_user_api_key.side_effect = ApiKeyUpdateError(
                "API key update failed: Expiration date cannot be more than 365 days after the key's creation date")
            result = cli_runner.invoke(cli, [
                'api-key', 'user', 'update',
                '--api-key-id', 'key-1', '--expires-at', '2030-01-01',
            ])
            assert result.exit_code != 0
            assert '365' in result.output


class TestUserApiKeyDelete:
    """Tests for api-key user delete command."""

    def test_delete_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].delete_user_api_key.return_value = {'message': 'deleted'}
            result = cli_runner.invoke(cli, [
                'api-key', 'user', 'delete', '--api-key-id', 'key-1',
            ])
            assert result.exit_code == 0
            mocks['api_client'].delete_user_api_key.assert_called_once_with('key-1')

    def test_delete_not_owned_key(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].delete_user_api_key.side_effect = ApiKeyNotFoundError("not found")
            result = cli_runner.invoke(cli, [
                'api-key', 'user', 'delete', '--api-key-id', 'key-x',
            ])
            assert result.exit_code != 0


class TestAdminApiKeyBackwardsCompatibility:
    """The admin api-key commands must be unaffected by the user sub-group."""

    def test_admin_list_still_works(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].list_api_keys.return_value = {'Items': []}
            result = cli_runner.invoke(cli, ['api-key', 'list'])
            assert result.exit_code == 0
            mocks['api_client'].list_api_keys.assert_called_once()

    def test_admin_create_without_expiration_still_works(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].create_api_key.return_value = {
                'apiKeyId': 'k', 'apiKey': 'vams_admin', 'expiresAt': '',
            }
            result = cli_runner.invoke(cli, [
                'api-key', 'create',
                '--name', 'Admin Key', '--user-id', 'any@test.com', '--description', 'd',
            ])
            assert result.exit_code == 0
            called = mocks['api_client'].create_api_key.call_args[0][0]
            assert 'expiresAt' not in called  # still optional on the admin path
