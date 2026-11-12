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


# --- What a call past its budget tells the agent, by tier ----------------------------------------------


@pytest.mark.parametrize("tool, arguments, retry_invited", [
    ("list_databases", {}, True),                                                    # read
    ("create_database", {"database_id": "d", "description": "dddd", "default_bucket_id": "b"}, False),  # write
    ("execute_workflow", None, False),                                               # compute
    ("delete_asset_metadata", {"database_id": "d", "asset_id": "a", "metadata_keys": ["k"]}, False),  # destructive
])
async def test_a_timed_out_call_invites_a_retry_only_for_a_read(deployment, tmp_path, monkeypatch, tool, arguments,
                                                                  retry_invited):
    env = {"VAMS_MCP_TOOL_TIMEOUT": "10", "VAMS_MCP_TOOLSETS": "all", "VAMS_ENABLE_WRITES": "true",
           "VAMS_ENABLE_DESTRUCTIVE": "true"}
    with remote_server_module(deployment.settings_dict("http://127.0.0.1:9/mcp"), tmp_path, env=env) as server:
        monkeypatch.setattr(server, "_REMOTE_TIMEOUT_GRACE_SECONDS", -9.8)  # answer after 0.2 s
        fn = getattr(server, tool)
        if arguments is None:  # every required parameter of the tool, filled with a placeholder
            import inspect as _inspect
            arguments = {name: "x" for name, param in _inspect.signature(fn).parameters.items()
                         if param.default is _inspect.Parameter.empty}
        identity = VerifiedIdentity(deployment.token("alice"), "alice", "c", None)
        deployment.api_delay = 2
        with request_context.acting_for(identity):
            result = await fn(**arguments)
        deployment.api_delay = 0
        await asyncio.sleep(2.1)  # let the abandoned worker finish before the fake API stops
    assert result["error_type"] == "ToolTimeout"
    if retry_invited:
        assert "call the tool again" in result["error"] and "outcomeUnknown" not in result
    else:
        assert result["outcomeUnknown"] is True
        assert "call the tool again" not in result["error"] and f"Do not call {tool} again" in result["error"]
        assert result["checkWith"] in result["error"]


async def test_every_non_read_tool_names_a_read_tool_that_checks_its_outcome(server_with_switches):
    from vamscli import addon_constants

    switches = ["AUTHPROVIDER_COGNITO", addon_constants.FEATURE_EDH_WORKSTATIONS]
    with server_with_switches(switches, enable_writes=True, enable_destructive=True) as server:
        tools = {tool.name: tool.model_dump(by_alias=True) for tool in await server.mcp.list_tools()}
        missing = {}
        for name, tool in tools.items():
            if tool["annotations"]["readOnlyHint"]:
                continue
            check = server.outcome_check_tool(name)
            if check not in tools or not tools[check]["annotations"]["readOnlyHint"]:
                missing[name] = check
    assert missing == {}


# --- One user cannot starve the others ------------------------------------------------------------------


def test_one_users_slow_calls_do_not_delay_another_users_calls(deployment, tmp_path):
    """The reviewer's starvation probe: 60 slow calls by one user, then another user's call."""
    import concurrent.futures as cf

    from vams_mcp import http_app

    from .remote_support import RemoteServer, free_port

    port = free_port()
    with remote_server_module(deployment.settings_dict(f"http://127.0.0.1:{port}/mcp"), tmp_path) as server:
        app = http_app.build_app(server.mcp, server.REMOTE_SETTINGS, server.CONFIG)
        with RemoteServer(app, port) as running:
            alice, bob = deployment.token("alice"), deployment.token("bob")
            deployment.api_delay = 3
            with cf.ThreadPoolExecutor(60) as pool:
                calls = [pool.submit(running.call_tool, "list_databases", {}, alice) for _ in range(60)]
                time.sleep(1.0)
                started = time.monotonic()
                status = RemoteServer.tool_result(running.call_tool("vams_status", {}, bob))
                waited = time.monotonic() - started
                results = [RemoteServer.tool_result(call.result()) for call in calls]
            deployment.api_delay = 0
    assert status["userId"] == "bob" and waited < 1.0, waited
    refused = [r for r in results if r.get("error_type") == "TooManyConcurrentCalls"]
    served = [r for r in results if "Items" in r]
    assert len(served) == server.CONFIG.max_calls_per_user and len(refused) == 60 - len(served)
    assert "call the tool again" in refused[0]["error"]


def test_the_per_user_call_limit_is_a_bounded_setting(monkeypatch):
    assert Config.from_env().max_calls_per_user == 8
    monkeypatch.setenv("VAMS_MCP_MAX_CALLS_PER_USER", "3")
    assert Config.from_env().max_calls_per_user == 3
    for value in ("0", "65", "many"):
        monkeypatch.setenv("VAMS_MCP_MAX_CALLS_PER_USER", value)
        with pytest.raises(ConfigError):
            Config.from_env()


def test_a_wide_bind_without_https_prints_the_cannot_start_line(tmp_path):
    """Run as its own process: the check runs at start, after the configuration was read."""
    import json as _json
    import os
    import subprocess
    import sys

    from .remote_support import FakeDeployment as _Fake

    with _Fake() as fake:
        path = tmp_path / "auth.json"
        path.write_text(_json.dumps(fake.settings_dict("http://127.0.0.1:8000/mcp")))
        env = {**os.environ, "VAMS_MCP_TRANSPORT": "streamable-http", "VAMS_MCP_AUTH_CONFIG": str(path),
               "VAMS_MCP_HOST": "0.0.0.0", "VAMS_MCP_PORT": "1"}
        completed = subprocess.run(
            [sys.executable, "-c", "from vams_mcp.cli import main; main([])"],
            env=env, capture_output=True, text=True, timeout=120,
        )
    assert completed.returncode == 2, completed.stderr[-500:]
    assert "[vams-mcp] cannot start:" in completed.stderr and "https" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_an_auth_file_listing_the_web_applications_client_stops_the_server():
    data = _settings("https://mcp.example.com/mcp").to_dict()
    data["webAppClientId"] = "webclient"
    data["audiences"] = ["client", "webclient"]
    with pytest.raises(ConfigError, match="web application"):
        RemoteAuthSettings.from_dict(data)
    data["audiences"] = ["client"]
    assert RemoteAuthSettings.from_dict(data).web_app_client_id == "webclient"
