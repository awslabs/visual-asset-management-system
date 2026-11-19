"""`search_nlp` in the tool sets and in remote mode (Mandatory Rules 16 and 17).

Natural-language search is a read, so it is a `consumer` tool, and with it an `operator` one; the
administration and add-on sets do not carry it. In remote mode it runs through the request's own
client: each user's verified token, and no other, reaches `POST /search/nlp`, and an error answer never
shows the token. The remote server is the real one, over HTTP on the loopback interface, with a fake
deployment playing the issuer and the API (`tests/remote_support.py`).
"""

import logging

import pytest

from vams_mcp import request_context, toolsets
from vams_mcp.remote_auth import VerifiedIdentity

from .remote_support import FakeDeployment, RemoteServer, free_port, remote_server_module


# --- Tool sets ---------------------------------------------------------------------------------------


def test_search_nlp_is_in_the_sets_that_carry_search():
    holding = sorted(name for name, members in toolsets.TOOLSETS.items() if "search_nlp" in members)
    assert holding == ["consumer", "operator"]
    # The same sets as the keyword search tools, so a connection that can search can do both.
    for keyword_tool in ("search_assets", "search_files"):
        assert sorted(name for name, members in toolsets.TOOLSETS.items() if keyword_tool in members) == holding


@pytest.mark.parametrize("selection, listed", [
    ("consumer", True), ("operator", True), ("admin", False), ("edh", False), ("edh-admin", False),
    ("custom", False), ("all", True),
])
async def test_a_selection_lists_search_nlp_only_through_its_sets(server_with_switches, monkeypatch, selection,
                                                                  listed):
    monkeypatch.setenv("VAMS_MCP_TOOLSETS", selection)
    with server_with_switches([], enable_writes=True, enable_destructive=True) as server:
        monkeypatch.delenv("VAMS_MCP_TOOLSETS")  # cleared before the fixture's restoring reload
        names = {tool.name for tool in await server.mcp.list_tools()}
    assert ("search_nlp" in names) is listed


# --- Remote mode -------------------------------------------------------------------------------------


@pytest.fixture
def deployment():
    with FakeDeployment() as fake:
        yield fake


@pytest.fixture
def remote(deployment, tmp_path, caplog):
    """(server module, running RemoteServer, deployment), the remote default tool set, logs captured."""
    caplog.set_level(logging.DEBUG)
    port = free_port()
    settings = deployment.settings_dict(f"http://127.0.0.1:{port}/mcp")
    with remote_server_module(settings, tmp_path) as server:
        from vams_mcp import http_app

        app = http_app.build_app(server.mcp, server.REMOTE_SETTINGS, server.CONFIG)
        with RemoteServer(app, port) as running:
            yield server, running, deployment


def _nlp_bodies(deployment):
    return [body for route, body in deployment.api_writes if route == "/search/nlp"]


def test_the_remote_default_set_serves_search_nlp_with_each_users_token(remote):
    server, running, deployment = remote
    assert server.CONFIG.toolsets == ("consumer",)
    for user in ("alice", "bob"):
        token = deployment.token(user)
        before = len(deployment.api_authorizations)
        result = running.tool_result(running.call_tool("search_nlp", {"query": "pump housing", "size": 5}, token))
        assert deployment.api_authorizations[before:] == [f"Bearer {token}"], "only this request's token"
        assert result["results"][0]["source"]["str_databaseid"] == f"db-of-{user}"
        assert result["relation"] == "gte" and result["total_is_lower_bound"] is True
        assert result["results"][0]["source"]["_vector"]["fileClass"] == "mesh"
    assert _nlp_bodies(deployment)[-1] == {"query": "pump housing", "entityTypes": ["file"], "size": 5,
                                           "includeArchived": False}


def test_a_remote_search_error_never_shows_the_token(remote, caplog):
    _, running, deployment = remote
    deployment.search_status = 503
    token = deployment.token("alice")
    result = running.tool_result(running.call_tool("search_nlp", {"query": "pump housing"}, token))
    assert result["error_type"] == "SearchUnavailableError"
    assert "Search is busy" in result["error"]
    assert token not in result["error"] and token.split(".")[2] not in str(result)
    assert all(token not in record.getMessage() for record in caplog.records)


def test_a_token_the_api_refuses_asks_for_a_new_sign_in(remote):
    _, running, deployment = remote
    deployment.refused_users.add("mallory")
    result = running.tool_result(running.call_tool("search_nlp", {"query": "pump"}, deployment.token("mallory")))
    assert result["error_type"] == "NotAuthenticated"
    assert "vamscli" not in result["error"] + result["user_action"]


def test_search_nlp_resolves_the_requests_own_client(deployment, tmp_path):
    with remote_server_module(deployment.settings_dict("http://127.0.0.1:9/mcp"), tmp_path) as server:
        alice = VerifiedIdentity(deployment.token("alice"), "alice", "c", None)
        bob = VerifiedIdentity(deployment.token("bob"), "bob", "c", None)
        with request_context.acting_for(alice):
            first = server.search_nlp.__wrapped__(query="valve")
        with request_context.acting_for(bob):
            second = server.search_nlp.__wrapped__(query="valve")
    assert first["results"][0]["source"]["str_databaseid"] == "db-of-alice"
    assert second["results"][0]["source"]["str_databaseid"] == "db-of-bob"
    assert deployment.api_authorizations[-2:] == [f"Bearer {alice.token}", f"Bearer {bob.token}"]
