"""Tests for `api-key get` and `api-key user get`.

Both single-key GET routes were declared in `constants.py` and served by the backend, but no
`APIClient` method issued a GET against either — so the only way to reach one key from the CLI was
the full listing, which is itself capped. The first class asserts the method and path of each.
"""

import json
from unittest.mock import MagicMock, Mock

import pytest
import requests

from vamscli.main import cli
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import APIError, ApiKeyNotFoundError, SetupRequiredError


API_KEY_ID = "11111111-2222-3333-4444-555555555555"


class _Response:
    """Minimal stand-in for a requests.Response carrying a JSON body."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _recording_client(payload=None):
    """An APIClient whose transport is replaced by a recorder (see test_comment_commands)."""
    client = APIClient("https://api.example.com", profile_manager=Mock())
    calls = []

    def _fake_request(method, endpoint, include_auth=True, **kwargs):
        calls.append({'method': method, 'endpoint': endpoint, 'kwargs': kwargs})
        return _Response({} if payload is None else payload)

    client._make_request = _fake_request
    return client, calls


class TestApiKeyGetRequestPathsAndMethods:
    def test_get_api_key_gets_the_admin_scoped_route(self):
        client, calls = _recording_client({'apiKeyId': API_KEY_ID})
        client.get_api_key(API_KEY_ID)

        assert calls[0]['method'] == 'GET'
        assert calls[0]['endpoint'] == f'/auth/api-keys/{API_KEY_ID}'

    def test_get_user_api_key_gets_the_user_scoped_route(self):
        client, calls = _recording_client({'apiKeyId': API_KEY_ID})
        client.get_user_api_key(API_KEY_ID)

        assert calls[0]['method'] == 'GET'
        # The user scope is a different route, and the handler restricts it to the caller's keys.
        assert calls[0]['endpoint'] == f'/auth/user/api-keys/{API_KEY_ID}'

    def test_the_two_scopes_do_not_share_a_route(self):
        admin, admin_calls = _recording_client({'apiKeyId': API_KEY_ID})
        admin.get_api_key(API_KEY_ID)
        own, own_calls = _recording_client({'apiKeyId': API_KEY_ID})
        own.get_user_api_key(API_KEY_ID)

        assert admin_calls[0]['endpoint'] != own_calls[0]['endpoint']

    def test_a_single_key_read_is_not_a_listing(self):
        """Guards against the method degenerating into the capped list it exists to avoid."""
        client, calls = _recording_client({'apiKeyId': API_KEY_ID})
        client.get_api_key(API_KEY_ID)

        assert calls[0]['endpoint'] != '/auth/api-keys'
        assert 'params' not in calls[0]['kwargs']


class _ErrorResponse:
    """A non-2xx response placed at the TRANSPORT boundary (see test_comment_commands for why)."""

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.content = self.text.encode('utf-8')
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        raise requests.exceptions.HTTPError(str(self.status_code), response=self)


def _client_with_transport(response):
    """An APIClient whose HTTP session returns `response`, with the real request pipeline intact."""
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = False
    profile_manager.is_token_expired.return_value = False
    profile_manager.load_auth_profile.return_value = None

    client = APIClient("https://api.example.com", profile_manager=profile_manager)
    client.session.request = lambda *args, **kwargs: response
    return client


class TestApiKeyGetErrorMapping:
    """A missing key is a 400 from this handler, not a 404, in both scopes."""

    def test_a_400_naming_a_missing_key_is_not_found(self):
        client = _client_with_transport(_ErrorResponse(400, {'message': 'API key not found'}))
        with pytest.raises(ApiKeyNotFoundError):
            client.get_api_key(API_KEY_ID)

    def test_a_400_naming_a_missing_key_is_not_found_in_the_user_scope(self):
        # The user scope answers the same way for a key owned by someone else, so an operator is
        # never told a key exists that they may not read.
        client = _client_with_transport(_ErrorResponse(400, {'message': 'API key not found'}))
        with pytest.raises(ApiKeyNotFoundError):
            client.get_user_api_key(API_KEY_ID)

    def test_a_rejected_id_is_not_reported_as_a_missing_key(self):
        # The discriminating branch must fall the other way for a malformed id, or the operator is
        # told the key does not exist when the request itself was wrong.
        client = _client_with_transport(_ErrorResponse(400, {
            'message': 'apiKeyId must be a valid UUID'}))
        with pytest.raises(APIError) as raised:
            client.get_api_key('not-a-uuid')
        assert not isinstance(raised.value, ApiKeyNotFoundError)


class TestApiKeyGet:
    def test_get_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].get_api_key.return_value = {
                'apiKeyId': API_KEY_ID,
                'apiKeyName': 'CI Pipeline',
                'userId': 'bot@example.com',
                'isActive': 'true',
            }
            result = cli_runner.invoke(cli, ['api-key', 'get', '--api-key-id', API_KEY_ID])

            assert result.exit_code == 0
            assert 'CI Pipeline' in result.output
            mocks['api_client'].get_api_key.assert_called_once_with(API_KEY_ID)

    def test_get_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].get_api_key.return_value = {'apiKeyId': API_KEY_ID}
            result = cli_runner.invoke(
                cli, ['api-key', 'get', '--api-key-id', API_KEY_ID, '--json-output'])

            assert result.exit_code == 0
            assert json.loads(result.output)['apiKeyId'] == API_KEY_ID

    def test_get_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].get_api_key.side_effect = ApiKeyNotFoundError("API key not found")
            result = cli_runner.invoke(cli, ['api-key', 'get', '--api-key-id', API_KEY_ID])
            assert result.exit_code != 0

    def test_get_requires_an_id(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            result = cli_runner.invoke(cli, ['api-key', 'get'])

            assert result.exit_code != 0
            mocks['api_client'].get_api_key.assert_not_called()

    def test_get_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('apiKey'):
            result = cli_runner.invoke(cli, ['api-key', 'get', '--api-key-id', API_KEY_ID])
            # Global exception handling: SetupRequiredError propagates rather than being
            # printed, so `result.output` is empty and the message lives on the exception.
            # This matches the convention in test_tag_commands.py and its siblings.
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, SetupRequiredError)


class TestApiKeyUserGet:
    def test_user_get_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].get_user_api_key.return_value = {
                'apiKeyId': API_KEY_ID,
                'apiKeyName': 'My Script',
                'userId': 'alice@example.com',
            }
            result = cli_runner.invoke(cli, ['api-key', 'user', 'get', '--api-key-id', API_KEY_ID])

            assert result.exit_code == 0
            assert 'My Script' in result.output
            mocks['api_client'].get_user_api_key.assert_called_once_with(API_KEY_ID)
            # The user scope must not reach the admin-scoped method.
            mocks['api_client'].get_api_key.assert_not_called()

    def test_user_get_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('apiKey') as mocks:
            mocks['api_client'].get_user_api_key.side_effect = ApiKeyNotFoundError(
                "API key not found")
            result = cli_runner.invoke(cli, ['api-key', 'user', 'get', '--api-key-id', API_KEY_ID])
            assert result.exit_code != 0
