"""The setup and login hints `VamsClient` prints are commands vamscli accepts.

`--profile` is a vamscli root-group option, so it goes before the subcommand. A hint that puts it
after (`vamscli setup <url> --profile x`) fails with "No such option" when pasted, and the agent's
user cannot tell the hint from a product error. Each of the three hints is checked with a configured
profile (prefix carries `--profile <name>`) and without one (plain `vamscli `).
"""

from unittest.mock import MagicMock, patch

import pytest

from vams_mcp import client as client_module
from vams_mcp.client import VamsClient
from vams_mcp.config import Config, ConfigError


def _profile_manager(load_config=None, load_auth_profile=None, load_config_raises=None):
    manager = MagicMock()
    manager.get_active_profile.return_value = "active-profile"
    if load_config_raises is not None:
        manager.load_config.side_effect = load_config_raises
    else:
        manager.load_config.return_value = load_config
    manager.load_auth_profile.return_value = load_auth_profile
    return manager


def _build(config, manager):
    with patch.object(client_module, "ProfileManager", return_value=manager), \
            patch.object(client_module, "APIClient"):
        return VamsClient(config)


def _hint(config, manager):
    with pytest.raises(ConfigError) as raised:
        _build(config, manager)
    return str(raised.value)


NOT_SET_UP = _profile_manager(load_config_raises=Exception("profile not found"))
NO_URL = _profile_manager(load_config={})
NOT_LOGGED_IN = _profile_manager(load_config={"api_gateway_url": "https://x/api"}, load_auth_profile=None)


class TestWithAConfiguredProfile:
    CONFIG = Config(profile="staging")

    def test_setup_hint(self):
        hint = _hint(self.CONFIG, NOT_SET_UP)
        assert "`vamscli --profile staging setup <api-gateway-url>`" in hint, hint

    def test_missing_url_hint(self):
        hint = _hint(self.CONFIG, NO_URL)
        assert "`vamscli --profile staging setup <api-gateway-url>`" in hint, hint

    def test_login_hint(self):
        hint = _hint(self.CONFIG, NOT_LOGGED_IN)
        assert "`vamscli --profile staging auth login -u <user>`" in hint, hint

    def test_no_hint_puts_profile_after_the_command(self):
        for manager in (NOT_SET_UP, NO_URL, NOT_LOGGED_IN):
            hint = _hint(self.CONFIG, manager)
            assert "setup <api-gateway-url> --profile" not in hint, hint
            assert "-u <user> --profile" not in hint, hint


class TestWithoutAConfiguredProfile:
    CONFIG = Config()

    def test_setup_hint(self):
        hint = _hint(self.CONFIG, NOT_SET_UP)
        assert "`vamscli setup <api-gateway-url>`" in hint, hint
        assert "--profile" not in hint, hint

    def test_missing_url_hint(self):
        hint = _hint(self.CONFIG, NO_URL)
        assert "`vamscli setup <api-gateway-url>`" in hint, hint
        assert "--profile" not in hint, hint

    def test_login_hint(self):
        hint = _hint(self.CONFIG, NOT_LOGGED_IN)
        assert "`vamscli auth login -u <user>`" in hint, hint
        assert "--profile" not in hint, hint


def test_a_set_up_and_logged_in_profile_builds_a_client():
    """POSITIVE CONTROL: the hints above are raised by the checks, not by a constructor that
    always fails under these mocks."""
    manager = _profile_manager(load_config={"api_gateway_url": "https://x/api"},
                               load_auth_profile={"access_token": "t"})
    client = _build(Config(profile="staging"), manager)
    assert client.profile_name == "staging"
    assert client.api_url == "https://x/api"
