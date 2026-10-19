"""The Location Service style URL's API key is masked in human-readable output only.

`auth status` and the `profile` commands print the stored `location_service_api_url`, whose
`?key=` query value is an Amazon Location Service API key. The human-readable view shows the first
characters of the key followed by an ellipsis -- enough to tell two keys apart, not enough to reuse
one from a pasted terminal. `--json-output` is a machine contract and keeps the stored value.
"""

import json
from unittest.mock import Mock, patch

import pytest

from vamscli.main import cli
from vamscli.utils.json_output import mask_url_key

# A hand-built, key-shaped value: the `v1.public.` prefix plus 40 hex characters. Not a credential and
# deliberately not JWT-shaped, so secret scanners do not read it as one.
FULL_KEY = "v1.public.0123456789abcdef0123456789abcdef01234567"
STYLE_URL = f"https://maps.geo.us-east-1.amazonaws.com/maps/v0/maps/m/style-descriptor?key={FULL_KEY}"
MASKED_URL = "https://maps.geo.us-east-1.amazonaws.com/maps/v0/maps/m/style-descriptor?key=v1.publi…"


class TestMaskUrlKey:

    def test_masks_the_key_value_to_a_prefix(self):
        assert mask_url_key(STYLE_URL) == MASKED_URL

    def test_masks_a_key_that_is_not_the_first_parameter(self):
        url = f"https://example.com/style?lang=en&key={FULL_KEY}&v=2"
        assert mask_url_key(url) == "https://example.com/style?lang=en&key=v1.publi…&v=2"

    def test_leaves_a_url_without_a_key_alone(self):
        url = "https://example.com/style?lang=en"
        assert mask_url_key(url) == url

    def test_leaves_a_short_key_alone(self):
        """A value no longer than the visible prefix has nothing to hide behind an ellipsis."""
        assert mask_url_key("https://example.com/s?key=abc") == "https://example.com/s?key=abc"

    def test_does_not_touch_other_parameters_ending_in_key(self):
        url = f"https://example.com/s?apikey={FULL_KEY}"
        assert mask_url_key(url) == url

    @pytest.mark.parametrize("value", [None, ""])
    def test_passes_through_empty_values(self, value):
        assert mask_url_key(value) == value


def _auth_profile():
    return {
        'user_id': 'test@example.com',
        'web_deployed_url': 'https://web.example.com',
        'location_service_api_url': STYLE_URL,
    }


@pytest.fixture
def auth_status_mocks(generic_command_mocks):
    return generic_command_mocks('auth')


class TestAuthStatus:

    @staticmethod
    def _arrange(mocks):
        pm = mocks['profile_manager']
        pm.has_auth_profile.return_value = True
        pm.get_token_expiration_info.return_value = {
            'has_token': True, 'token_type': 'cognito', 'has_expiration': True,
            'is_expired': False, 'expires_in_human': '2 hours', 'expires_at': 1234567890,
        }
        pm.load_auth_profile.return_value = _auth_profile()
        pm.has_credentials.return_value = False
        pm.get_feature_switches_info.return_value = {'has_feature_switches': False}

    def test_human_readable_output_masks_the_key(self, cli_runner, auth_status_mocks):
        with auth_status_mocks as mocks:
            self._arrange(mocks)
            result = cli_runner.invoke(cli, ['auth', 'status'])
        assert result.exit_code == 0, result.output
        assert f"Location Service URL: {MASKED_URL}" in result.output
        assert FULL_KEY not in result.output

    def test_json_output_keeps_the_full_url(self, cli_runner, auth_status_mocks):
        with auth_status_mocks as mocks:
            self._arrange(mocks)
            result = cli_runner.invoke(cli, ['auth', 'status', '--json-output'])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)['location_service_api_url'] == STYLE_URL


class TestProfileList:

    @staticmethod
    def _profiles_info():
        return [{
            'profile_name': 'default', 'is_active': True,
            'has_config': True, 'api_gateway_url': 'https://api.example.com', 'cli_version': '1.0.0',
            'has_auth': True, 'user_id': 'test@example.com', 'token_type': 'cognito',
            'token_expired': False, 'web_deployed_url': 'https://web.example.com',
            'location_service_api_url': STYLE_URL,
            'has_credentials': False,
        }]

    def test_human_readable_output_masks_the_key(self, cli_runner):
        with patch('vamscli.commands.profile.ProfileManager.get_all_profiles_info',
                   return_value=self._profiles_info()):
            result = cli_runner.invoke(cli, ['profile', 'list'])
        assert result.exit_code == 0, result.output
        assert f"Location Service URL: {MASKED_URL}" in result.output
        assert FULL_KEY not in result.output

    def test_json_output_keeps_the_full_url(self, cli_runner):
        with patch('vamscli.commands.profile.ProfileManager.get_all_profiles_info',
                   return_value=self._profiles_info()):
            result = cli_runner.invoke(cli, ['profile', 'list', '--json-output'])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert FULL_KEY in json.dumps(payload)


class TestProfileInfoAndCurrent:

    @staticmethod
    def _manager():
        pm = Mock()
        pm.profile_name = 'default'
        pm.get_active_profile.return_value = 'default'
        pm.has_config.return_value = True
        pm.has_auth_profile.return_value = True
        pm.has_credentials.return_value = False
        pm.load_config.return_value = {'api_gateway_url': 'https://api.example.com', 'cli_version': '1.0.0'}
        pm.load_auth_profile.return_value = _auth_profile()
        pm.get_profile_info.return_value = {
            'profile_name': 'default', 'is_active': True, 'profile_dir': '/tmp/vamscli/profiles/default',
            'has_config': True, 'api_gateway_url': 'https://api.example.com', 'cli_version': '1.0.0',
            'has_auth': True, 'user_id': 'test@example.com', 'token_type': 'cognito',
            'token_expired': False, 'web_deployed_url': 'https://web.example.com',
            'location_service_api_url': STYLE_URL,
            'has_credentials': False,
        }
        return pm

    @pytest.mark.parametrize("args", [['profile', 'info', 'default'], ['profile', 'current']],
                             ids=lambda a: " ".join(a))
    def test_human_readable_output_masks_the_key(self, cli_runner, args):
        with patch('vamscli.commands.profile.ProfileManager', return_value=self._manager()):
            result = cli_runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert "Location Service URL:" in result.output
        assert "key=v1.publi…" in result.output
        assert FULL_KEY not in result.output

    @pytest.mark.parametrize("args", [['profile', 'info', 'default', '--json-output'],
                                      ['profile', 'current', '--json-output']],
                             ids=lambda a: " ".join(a))
    def test_json_output_keeps_the_full_url(self, cli_runner, args):
        with patch('vamscli.commands.profile.ProfileManager', return_value=self._manager()):
            result = cli_runner.invoke(cli, args)
        assert result.exit_code == 0, result.output
        assert FULL_KEY in json.dumps(json.loads(result.output))
