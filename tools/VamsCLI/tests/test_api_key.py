"""Tests for api-key commands."""

import json
import pytest
from click.testing import CliRunner
from unittest.mock import Mock

from vamscli.main import cli
from vamscli.utils.exceptions import ApiKeyNotFoundError, ApiKeyCreationError, ApiKeyDeletionError


class TestApiKeyList:
    """Tests for api-key list command."""

    def test_list_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].list_api_keys.return_value = {
                'Items': [
                    {'apiKeyId': 'key-1', 'apiKeyName': 'Test Key', 'userId': 'user@test.com', 'isActive': 'true'}
                ]
            }
            result = cli_runner.invoke(cli, ['api-key', 'list'])
            assert result.exit_code == 0
            assert 'Test Key' in result.output
            mocks['api_client'].list_api_keys.assert_called_once()

    def test_list_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            expected = {'Items': [{'apiKeyId': 'key-1', 'apiKeyName': 'Test Key'}]}
            mocks['api_client'].list_api_keys.return_value = expected
            result = cli_runner.invoke(cli, ['api-key', 'list', '--json-output'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['Items'][0]['apiKeyId'] == 'key-1'

    def test_list_empty(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].list_api_keys.return_value = {'Items': []}
            result = cli_runner.invoke(cli, ['api-key', 'list'])
            assert result.exit_code == 0

    def test_list_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('apiKey') as mocks:
            result = cli_runner.invoke(cli, ['api-key', 'list'])
            assert result.exit_code != 0


class TestApiKeyCreate:
    """Tests for api-key create command."""

    def test_create_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].create_api_key.return_value = {
                'apiKeyId': 'new-key-id',
                'apiKeyName': 'My Key',
                'userId': 'user@test.com',
                'apiKey': 'vams_testkey123',
                'createdBy': 'admin',
                'expiresAt': '',
            }
            result = cli_runner.invoke(cli, [
                'api-key', 'create',
                '--name', 'My Key',
                '--user-id', 'user@test.com',
                '--description', 'Test key'
            ])
            assert result.exit_code == 0
            assert 'vams_testkey123' in result.output
            mocks['api_client'].create_api_key.assert_called_once_with({
                'apiKeyName': 'My Key',
                'userId': 'user@test.com',
                'description': 'Test key',
            })

    def test_create_with_all_options(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].create_api_key.return_value = {
                'apiKeyId': 'new-key-id',
                'apiKeyName': 'Full Key',
                'userId': 'user@test.com',
                'apiKey': 'vams_fullkey456',
                'description': 'Test description',
                'expiresAt': '2027-01-01T00:00:00Z',
            }
            result = cli_runner.invoke(cli, [
                'api-key', 'create',
                '--name', 'Full Key',
                '--user-id', 'user@test.com',
                '--description', 'Test description',
                '--expires-at', '2027-01-01T00:00:00Z'
            ])
            assert result.exit_code == 0

    def test_create_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            expected = {
                'apiKeyId': 'new-key-id',
                'apiKey': 'vams_jsonkey789',
            }
            mocks['api_client'].create_api_key.return_value = expected
            result = cli_runner.invoke(cli, [
                'api-key', 'create',
                '--name', 'JSON Key',
                '--user-id', 'user@test.com',
                '--description', 'JSON test key',
                '--json-output'
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['apiKey'] == 'vams_jsonkey789'

    def test_create_missing_name(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            result = cli_runner.invoke(cli, [
                'api-key', 'create',
                '--user-id', 'user@test.com',
                '--description', 'Test'
            ])
            assert result.exit_code != 0

    def test_create_missing_user_id(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            result = cli_runner.invoke(cli, [
                'api-key', 'create',
                '--name', 'My Key',
                '--description', 'Test'
            ])
            assert result.exit_code != 0

    def test_create_missing_description(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            result = cli_runner.invoke(cli, [
                'api-key', 'create',
                '--name', 'My Key',
                '--user-id', 'user@test.com'
            ])
            assert result.exit_code != 0


class TestApiKeyUpdate:
    """Tests for api-key update command."""

    def test_update_description(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].update_api_key.return_value = {
                'apiKeyId': 'key-1',
                'description': 'Updated',
            }
            result = cli_runner.invoke(cli, [
                'api-key', 'update',
                '--api-key-id', 'key-1',
                '--description', 'Updated'
            ])
            assert result.exit_code == 0
            mocks['api_client'].update_api_key.assert_called_once_with(
                'key-1', {'description': 'Updated'}
            )

    def test_update_expiration(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].update_api_key.return_value = {
                'apiKeyId': 'key-1',
                'expiresAt': '2028-01-01T00:00:00Z',
            }
            result = cli_runner.invoke(cli, [
                'api-key', 'update',
                '--api-key-id', 'key-1',
                '--expires-at', '2028-01-01T00:00:00Z'
            ])
            assert result.exit_code == 0

    def test_update_is_active(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].update_api_key.return_value = {
                'apiKeyId': 'key-1',
                'isActive': 'false',
            }
            result = cli_runner.invoke(cli, [
                'api-key', 'update',
                '--api-key-id', 'key-1',
                '--is-active', 'false'
            ])
            assert result.exit_code == 0
            mocks['api_client'].update_api_key.assert_called_once_with(
                'key-1', {'isActive': 'false'}
            )

    def test_update_clear_expiration(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].update_api_key.return_value = {
                'apiKeyId': 'key-1',
                'expiresAt': '',
            }
            result = cli_runner.invoke(cli, [
                'api-key', 'update',
                '--api-key-id', 'key-1',
                '--expires-at', ''
            ])
            assert result.exit_code == 0
            mocks['api_client'].update_api_key.assert_called_once_with(
                'key-1', {'expiresAt': ''}
            )

    def test_update_multiple_fields(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].update_api_key.return_value = {
                'apiKeyId': 'key-1',
                'description': 'New desc',
                'isActive': 'true',
            }
            result = cli_runner.invoke(cli, [
                'api-key', 'update',
                '--api-key-id', 'key-1',
                '--description', 'New desc',
                '--is-active', 'true',
                '--expires-at', '2028-06-30T23:59:59Z'
            ])
            assert result.exit_code == 0
            mocks['api_client'].update_api_key.assert_called_once_with(
                'key-1', {
                    'description': 'New desc',
                    'expiresAt': '2028-06-30T23:59:59Z',
                    'isActive': 'true',
                }
            )

    def test_update_no_fields(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            result = cli_runner.invoke(cli, [
                'api-key', 'update',
                '--api-key-id', 'key-1'
            ])
            assert result.exit_code != 0

    def test_update_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].update_api_key.side_effect = ApiKeyNotFoundError("Not found")
            result = cli_runner.invoke(cli, [
                'api-key', 'update',
                '--api-key-id', 'bad-id',
                '--description', 'test'
            ])
            assert result.exit_code != 0


class TestApiKeyDelete:
    """Tests for api-key delete command."""

    def test_delete_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].delete_api_key.return_value = {
                'message': "API key 'key-1' deleted successfully"
            }
            result = cli_runner.invoke(cli, [
                'api-key', 'delete',
                '--api-key-id', 'key-1'
            ])
            assert result.exit_code == 0
            mocks['api_client'].delete_api_key.assert_called_once_with('key-1')

    def test_delete_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            expected = {'message': "Deleted"}
            mocks['api_client'].delete_api_key.return_value = expected
            result = cli_runner.invoke(cli, [
                'api-key', 'delete',
                '--api-key-id', 'key-1',
                '--json-output'
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert 'message' in data

    def test_delete_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].delete_api_key.side_effect = ApiKeyNotFoundError("Not found")
            result = cli_runner.invoke(cli, [
                'api-key', 'delete',
                '--api-key-id', 'bad-id'
            ])
            assert result.exit_code != 0

    def test_delete_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('apiKey') as mocks:
            result = cli_runner.invoke(cli, [
                'api-key', 'delete',
                '--api-key-id', 'key-1'
            ])
            assert result.exit_code != 0
