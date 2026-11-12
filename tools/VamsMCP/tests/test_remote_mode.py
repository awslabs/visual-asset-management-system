"""Remote mode's start-up rules, its per-request client, the CLI request log, and the time budget.

* A remote configuration that names a vamscli profile, or no auth configuration, stops the server.
* The listener binds to 127.0.0.1 unless VAMS_MCP_HOST says otherwise, and a wider bind needs an https
  resource URL (the HTTPS front end).
* In remote mode no profile is read: CLIENT resolves to the request's own client, built from its token.
* The CLI's shared request log is silenced (``client.silence_cli_request_log``), relying on
  ``vamscli.utils.logging.get_logger`` returning its module logger once one is set.
* A tool call stops paging when its time budget runs low, and one that overruns is answered with a
  ToolTimeout instead of hanging the client.
"""

import asyncio
import logging
import time
from unittest.mock import MagicMock

import pytest

from vams_mcp import request_context
from vams_mcp.config import Config, ConfigError
from vams_mcp.remote_auth import RemoteAuthSettings, VerifiedIdentity

from .remote_support import FakeDeployment, remote_server_module


@pytest.fixture
def deployment():
    with FakeDeployment() as fake:
        yield fake


# --- Start-up ----------------------------------------------------------------------------------------


def test_remote_mode_refuses_a_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("VAMS_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("VAMS_MCP_AUTH_CONFIG", str(tmp_path / "a.json"))
    monkeypatch.setenv("VAMS_PROFILE", "operator")
    with pytest.raises(ConfigError, match="VAMS_PROFILE"):
        Config.from_env()


def test_remote_mode_needs_its_auth_configuration(monkeypatch):
    monkeypatch.setenv("VAMS_MCP_TRANSPORT", "streamable-http")
    with pytest.raises(ConfigError, match="VAMS_MCP_AUTH_CONFIG"):
        Config.from_env()


@pytest.mark.parametrize("name, value", [
    ("VAMS_MCP_TRANSPORT", "sse"), ("VAMS_MCP_PORT", "0"), ("VAMS_MCP_PORT", "http"),
    ("VAMS_MCP_TOOL_TIMEOUT", "5"), ("VAMS_MCP_TOOL_TIMEOUT", "100000"),
])
def test_invalid_remote_settings_stop_the_server(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        Config.from_env()


def test_the_listener_defaults_to_loopback(monkeypatch, tmp_path):
    monkeypatch.setenv("VAMS_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("VAMS_MCP_AUTH_CONFIG", str(tmp_path / "a.json"))
    config = Config.from_env()
    assert (config.host, config.port, config.tool_timeout) == ("127.0.0.1", 8000, 240)
    assert config.remote is True
    assert Config().remote is False and Config().host == "127.0.0.1"


def _settings(resource_url):
    return RemoteAuthSettings.from_dict({
        "version": 1, "apiUrl": "https://api.example.com/api", "resourceUrl": resource_url, "provider": "cognito",
        "issuer": "https://issuer.example.com/pool", "jwksUri": "https://issuer.example.com/pool/jwks.json",
        "audiences": ["client"],
    })


def test_a_wider_bind_needs_an_https_resource_url():
    from vams_mcp import http_app

    http_app.check_exposure(_settings("https://mcp.example.com/mcp"), Config(transport="streamable-http", host="0.0.0.0"))
    http_app.check_exposure(_settings("http://127.0.0.1:8000/mcp"), Config(transport="streamable-http"))
    with pytest.raises(ConfigError, match="https"):
        http_app.check_exposure(_settings("http://127.0.0.1:8000/mcp"), Config(transport="streamable-http", host="0.0.0.0"))


def test_dns_rebinding_protection_allows_the_resource_host_and_loopback_only():
    from vams_mcp import http_app

    security = http_app.transport_security_settings(_settings("https://mcp.example.com/mcp"), Config())
    assert security.enable_dns_rebinding_protection is True
    assert "mcp.example.com" in security.allowed_hosts and "127.0.0.1:*" in security.allowed_hosts
    assert "https://mcp.example.com" in security.allowed_origins
    assert not any("*" == host or host.startswith("0.0.0.0") for host in security.allowed_hosts)


def test_the_cli_entry_point_reports_a_configuration_error(tmp_path):
    """Run as its own process: the server module reads its configuration once, at import."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "VAMS_MCP_TRANSPORT": "streamable-http", "VAMS_MCP_AUTH_CONFIG": str(tmp_path / "absent.json")}
    completed = subprocess.run(
        [sys.executable, "-c", "from vams_mcp.cli import main; main([])"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 2
    assert "setup-remote" in completed.stderr and completed.stdout == ""


# --- Per-request client ------------------------------------------------------------------------------


def test_remote_mode_reads_no_profile_and_resolves_the_requests_client(deployment, tmp_path, monkeypatch):
    import vamscli.utils.profile as profile_module

    def no_profile(*args, **kwargs):
        raise AssertionError("a remote server must never build a ProfileManager")

    with remote_server_module(deployment.settings_dict("http://127.0.0.1:9/mcp"), tmp_path) as server, \
            monkeypatch.context() as patch:
        # Undone before the stdio server is reloaded on exit, which does read a profile.
        patch.setattr(profile_module.ProfileManager, "__init__", no_profile)
        assert server.CLIENT.current() is None
        alice = VerifiedIdentity(deployment.token("alice"), "alice", "c", None)
        bob = VerifiedIdentity(deployment.token("bob"), "bob", "c", None)
        with request_context.acting_for(alice):
            first = server.CLIENT.current()
            assert first is server.CLIENT.current()  # one client per request
            assert first.api._bearer_token == alice.token and first.api.profile_manager is None
            assert first.api.request_timeout[1] == server.CONFIG.tool_timeout
            assert server.list_databases.__wrapped__()["Items"][0]["databaseId"] == "db-of-alice"
        with request_context.acting_for(bob):
            assert server.CLIENT.current() is not first
            assert server.list_databases.__wrapped__()["Items"][0]["databaseId"] == "db-of-bob"
        assert server.CLIENT.current() is None


async def test_a_remote_tool_outside_a_verified_request_answers_not_authenticated(deployment, tmp_path):
    with remote_server_module(deployment.settings_dict("http://127.0.0.1:9/mcp"), tmp_path) as server:
        result = await server.list_databases()
    assert result["error_type"] == "NotAuthenticated"
    assert "vamscli" not in result["error"] + result["user_action"]


def test_the_cli_request_log_is_silenced_in_remote_mode(deployment, tmp_path):
    import vamscli.utils.logging as cli_logging

    with remote_server_module(deployment.settings_dict("http://127.0.0.1:9/mcp"), tmp_path):
        logger = cli_logging.get_logger()
        assert logger is cli_logging._logger
        assert not any(isinstance(h, logging.FileHandler) for h in logger.handlers)
        assert logger.propagate is False and not logger.isEnabledFor(logging.CRITICAL)
        # The contract silence_cli_request_log relies on: initialize_logging keeps a logger already set.
        assert cli_logging.initialize_logging() is logger
        cli_logging.log_api_request("GET", "https://api.example.com/database?secret=1", {}, {"a": 1})


# --- Time budget -------------------------------------------------------------------------------------


def test_paginate_stops_with_a_token_when_the_budget_runs_low():
    from vams_mcp.client import VamsClient

    client = MagicMock()
    client.config = Config(max_pages=50, page_size=1)
    client.unwrap_message = VamsClient.unwrap_message
    calls = []

    def fetch(params):
        calls.append(params)
        time.sleep(0.05)
        return {"Items": [len(calls)], "NextToken": f"t{len(calls)}"}

    with request_context.time_budget(0.2):
        result = VamsClient.paginate(client, fetch, max_items=50)
    assert result["truncated"] is True and result["NextToken"] == f"t{len(calls)}"
    assert "time budget" in result["note"]
    assert 1 <= len(calls) < 50


def test_paginate_has_no_time_bound_without_a_budget():
    from vams_mcp.client import VamsClient

    client = MagicMock()
    client.config = Config(max_pages=5, page_size=1)
    client.unwrap_message = VamsClient.unwrap_message
    pages = iter(range(10))
    result = VamsClient.paginate(client, lambda p: {"Items": [next(pages)], "NextToken": "t"}, max_items=50)
    assert result["pages"] == 5 and "time budget" not in result["note"]


async def test_a_call_that_overruns_its_budget_is_answered_with_a_timeout(deployment, tmp_path, monkeypatch):
    with remote_server_module(
        deployment.settings_dict("http://127.0.0.1:9/mcp"), tmp_path, env={"VAMS_MCP_TOOL_TIMEOUT": "10"}
    ) as server:
        monkeypatch.setattr(server, "_REMOTE_TIMEOUT_GRACE_SECONDS", -9.8)  # answer after 0.2 s
        identity = VerifiedIdentity(deployment.token("alice"), "alice", "c", None)
        deployment.api_delay = 2
        started = time.monotonic()
        with request_context.acting_for(identity):
            result = await server.list_databases()
        assert result["error_type"] == "ToolTimeout"
        assert time.monotonic() - started < 1.5
        deployment.api_delay = 0
        await asyncio.sleep(2.1)  # let the abandoned worker finish before the fake API stops
