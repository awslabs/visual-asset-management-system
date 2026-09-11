"""Tests for api-key commands."""

import json
import pytest
from click.testing import CliRunner
from unittest.mock import Mock

from vamscli.commands.apiKey import format_list_output
from vamscli.main import cli
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import (
    APIError,
    ApiKeyNotFoundError,
    ApiKeyCreationError,
    ApiKeyDeletionError,
)


class _Response:
    """Minimal stand-in for a requests.Response carrying a JSON body."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _recording_client(payload=None):
    """An APIClient whose transport is replaced by a recorder."""
    client = APIClient("https://api.example.com", profile_manager=Mock())
    calls = []

    def _fake_request(method, endpoint, include_auth=True, **kwargs):
        calls.append({'method': method, 'endpoint': endpoint, 'kwargs': kwargs})
        return _Response({'Items': []} if payload is None else payload)

    client._make_request = _fake_request
    return client, calls


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


class TestApiKeyListPaginationOnTheWire:
    """The pagination options reach the endpoint as query parameters.

    A CLI-side-only `--max-items` would still yield one item when combined with
    `--page-size 1`, so the isolating case (maxItems with no pageSize) is what proves the
    parameter is not swallowed.
    """

    def test_no_options_sends_no_pagination_parameters(self):
        client, calls = _recording_client()
        client.list_api_keys()

        assert calls[0]['method'] == 'GET'
        assert calls[0]['endpoint'] == '/auth/api-keys'
        assert calls[0]['kwargs']['params'] == {}

    def test_only_the_pagination_given_is_forwarded(self):
        client, calls = _recording_client()
        client.list_api_keys(max_items=10, page_size=5, starting_token='tok')

        assert calls[0]['kwargs']['params'] == {
            'maxItems': 10, 'pageSize': 5, 'startingToken': 'tok'}

    def test_max_items_alone_reaches_the_endpoint(self):
        client, calls = _recording_client()
        client.list_api_keys(max_items=1)

        assert calls[0]['kwargs']['params'] == {'maxItems': 1}

    def test_user_scope_forwards_the_same_parameters(self):
        client, calls = _recording_client()
        client.list_user_api_keys(max_items=10, page_size=5, starting_token='tok')

        assert calls[0]['endpoint'] == '/auth/user/api-keys'
        assert calls[0]['kwargs']['params'] == {
            'maxItems': 10, 'pageSize': 5, 'startingToken': 'tok'}

    def test_user_scope_with_no_options_sends_nothing(self):
        client, calls = _recording_client()
        client.list_user_api_keys()

        assert calls[0]['kwargs']['params'] == {}


class TestApiKeyListPagingOptions:
    """`api-key list` paging flags, and the truncation the listing reports."""

    KEY_1 = {'apiKeyId': 'key-1', 'apiKeyName': 'One', 'userId': 'u@test.com',
             'isActive': 'true'}
    KEY_2 = {'apiKeyId': 'key-2', 'apiKeyName': 'Two', 'userId': 'u@test.com',
             'isActive': 'true'}

    def test_flags_are_passed_through_to_the_client(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].list_api_keys.return_value = {'Items': [self.KEY_1]}
            result = cli_runner.invoke(cli, [
                'api-key', 'list', '--page-size', '1', '--max-items', '2',
                '--starting-token', 'tok', '--json-output'])

            assert result.exit_code == 0
            mocks['api_client'].list_api_keys.assert_called_once_with(
                max_items=2, page_size=1, starting_token='tok')

    def test_no_flags_leaves_the_deployment_defaults_in_force(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].list_api_keys.return_value = {'Items': [self.KEY_1]}
            result = cli_runner.invoke(cli, ['api-key', 'list'])

            assert result.exit_code == 0
            assert 'Next token' not in result.output
            mocks['api_client'].list_api_keys.assert_called_once_with(
                max_items=None, page_size=None, starting_token=None)

    def test_auto_paginate_follows_the_token_to_the_end(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].list_api_keys.side_effect = [
                {'Items': [self.KEY_1], 'NextToken': 't1', 'truncated': True},
                {'Items': [self.KEY_2]},
            ]
            result = cli_runner.invoke(cli, ['api-key', 'list', '--auto-paginate',
                                             '--page-size', '1', '--json-output'])

            assert result.exit_code == 0
            data = json.loads(result.output)
            assert [item['apiKeyId'] for item in data['Items']] == ['key-1', 'key-2']
            assert 'NextToken' not in data
            assert mocks['api_client'].list_api_keys.call_args_list[1].kwargs[
                'starting_token'] == 't1'

    def test_auto_paginate_stops_on_an_empty_final_page(
            self, cli_runner, generic_command_mocks):
        # DynamoDB reports LastEvaluatedKey whenever a read stops at its limit, so the page
        # after an exact multiple of the bound is empty and still carries a token.
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].list_api_keys.side_effect = [
                {'Items': [self.KEY_1], 'NextToken': 't1', 'truncated': True},
                {'Items': [], 'NextToken': 't2', 'truncated': True},
            ]
            result = cli_runner.invoke(cli, ['api-key', 'list', '--auto-paginate',
                                             '--json-output'])

            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data['Items']) == 1
            assert mocks['api_client'].list_api_keys.call_count == 2

    def test_auto_paginate_stops_at_max_items_and_reports_the_remainder(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].list_api_keys.return_value = {
                'Items': [self.KEY_1], 'NextToken': 't1', 'truncated': True}
            result = cli_runner.invoke(cli, ['api-key', 'list', '--auto-paginate',
                                             '--max-items', '1', '--json-output'])

            assert result.exit_code == 0
            data = json.loads(result.output)
            assert len(data['Items']) == 1
            assert data['NextToken'] == 't1'
            assert data['truncated'] is True
            assert mocks['api_client'].list_api_keys.call_count == 1

    def test_auto_paginate_with_starting_token_is_rejected(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            result = cli_runner.invoke(cli, ['api-key', 'list', '--auto-paginate',
                                             '--starting-token', 'tok'])

            assert result.exit_code != 0
            assert 'Cannot use --auto-paginate with --starting-token' in result.output
            mocks['api_client'].list_api_keys.assert_not_called()

    def test_a_rejected_starting_token_is_reported_as_an_error(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].list_api_keys.side_effect = APIError(
                'Invalid request (400): Invalid pagination token')
            result = cli_runner.invoke(cli, ['api-key', 'list', '--starting-token', 'garbage',
                                             '--json-output'])

            assert result.exit_code != 0
            assert 'Invalid pagination token' in json.dumps(json.loads(result.output))


class TestApiKeyListTruncationOutput:
    """The human listing surfaces the token, including on a page that holds no items."""

    def test_the_token_is_printed_after_the_rows(self):
        output = format_list_output({
            'Items': [{'apiKeyId': 'key-1', 'apiKeyName': 'One', 'userId': 'u@test.com',
                       'isActive': 'true'}],
            'NextToken': 'tok-abc',
            'truncated': True,
        })

        assert 'key-1' in output
        assert 'tok-abc' in output
        assert '--starting-token' in output

    def test_an_empty_page_still_offers_its_token(self):
        output = format_list_output({'Items': [], 'NextToken': 'tok-abc', 'truncated': True})

        assert 'No API keys found.' in output
        assert 'tok-abc' in output

    def test_a_complete_listing_prints_no_token_line(self):
        output = format_list_output({'Items': []})

        assert output == 'No API keys found.'


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
