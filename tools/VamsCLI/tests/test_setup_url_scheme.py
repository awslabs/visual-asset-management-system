"""`vamscli setup` stores a deployment URL only when it is https, or http to this machine.

Every later command sends the bearer token to the API URL that setup reads from the deployment, so the
base URL setup is given and the API URL it stores must both be https; plain http is accepted for
localhost and loopback addresses only, as `vams-mcp setup-remote` and the remote MCP server's own auth
file check also require.
"""

from unittest.mock import patch

import pytest

from vamscli.commands.setup import is_loopback_host, validate_base_url
from vamscli.main import cli


@pytest.fixture
def setup_mocks(generic_command_mocks):
    return generic_command_mocks("setup")


AMPLIFY = {
    "region": "us-west-2",
    "api": "https://2jf1k4c5lj.execute-api.us-west-2.amazonaws.com/api",
    "cognitoUserPoolId": "us-west-2_test",
    "cognitoAppClientId": "test-client-id",
}


@pytest.mark.parametrize(
    "url",
    [
        "https://vams.example.com",
        "HTTPS://vams.example.com/",
        "https://10.0.0.5:8443",
        "http://localhost:3001",
        "http://127.0.0.1",
        "http://127.8.9.10:80/api",
        "http://[::1]:8080",
        "HTTP://LOCALHOST",
    ],
)
def test_https_and_loopback_http_are_accepted(url):
    assert validate_base_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://vams.example.com",
        "http://10.0.0.5",
        "http://192.168.1.10:3001",
        "http://[2001:db8::1]",
        "http://localhost.example.com",
        "ftp://vams.example.com",
        "vams.example.com",
        "https://",
        "",
    ],
)
def test_plain_http_to_another_host_is_refused(url):
    assert validate_base_url(url) is False


def test_loopback_hosts():
    assert (
        is_loopback_host("localhost")
        and is_loopback_host("[::1]")
        and is_loopback_host("127.0.0.1")
    )
    assert (
        not is_loopback_host("10.0.0.1")
        and not is_loopback_host("")
        and not is_loopback_host("example")
    )


def test_setup_refuses_a_plain_http_base_url_before_any_request(cli_runner, setup_mocks):
    with setup_mocks as mocks:
        mocks["profile_manager"].has_config.return_value = False
        result = cli_runner.invoke(cli, ["setup", "http://vams.example.com"])
        assert result.exit_code != 0
        assert "https" in result.output and "loopback" in result.output
        mocks["api_client"].check_version.assert_not_called()
        mocks["api_client"].get_amplify_config.assert_not_called()
        mocks["profile_manager"].save_config.assert_not_called()


def test_setup_refuses_a_plain_http_api_url_from_the_deployment(cli_runner, setup_mocks):
    with setup_mocks as mocks:
        mocks["profile_manager"].has_config.return_value = False
        mocks["api_client"].check_version.return_value = {
            "match": True,
            "cli_version": "1",
            "api_version": "1",
        }
        mocks["api_client"].get_amplify_config.return_value = {
            **AMPLIFY,
            "api": "http://api.example.com/api",
        }
        with patch("click.DateTime"):
            result = cli_runner.invoke(cli, ["setup", "https://vams.example.com"])
        assert result.exit_code != 0
        assert "http://api.example.com/api" in result.output and "https" in result.output
        mocks["profile_manager"].save_config.assert_not_called()


def test_setup_accepts_a_loopback_http_base_url(cli_runner, setup_mocks):
    """Control: a local development deployment keeps working."""
    with setup_mocks as mocks:
        mocks["profile_manager"].has_config.return_value = False
        mocks["api_client"].check_version.return_value = {
            "match": True,
            "cli_version": "1",
            "api_version": "1",
        }
        mocks["api_client"].get_amplify_config.return_value = {
            **AMPLIFY,
            "api": "http://127.0.0.1:8002/api",
        }
        with patch("click.DateTime"):
            result = cli_runner.invoke(cli, ["setup", "http://localhost:3001"])
        assert result.exit_code == 0, result.output
        mocks["profile_manager"].save_config.assert_called_once()
