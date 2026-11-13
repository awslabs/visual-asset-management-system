"""The remote server end to end, over real HTTP on the loopback interface.

The server is the real remote application (``vams_mcp.server`` reloaded in remote mode, wrapped by
``http_app.build_app``) served by uvicorn on 127.0.0.1. A fake deployment on another loopback port
plays the issuer and the VAMS API (``tests/remote_support.py``). The tests prove what the security
policy of remote mode requires:

* a request without a token, or with a token that does not verify (bad signature, wrong issuer,
  wrong audience, expired, a machine-to-machine token), gets ``401`` with the RFC 9728 challenge and no
  tool runs;
* the Protected Resource Metadata document is the only route answered without a token;
* a valid token runs the tool, and that same token, and no other, reaches the VAMS API;
* two users calling at the same time each get their own results;
* keys that cannot be read refuse every request (fail closed);
* no token appears in the server's logs, in a tool result, or on disk.
"""

import json
import logging
import os
import threading
from pathlib import Path

import pytest

from .remote_support import (
    OTHER_CLIENT_ID,
    REMOTE_CLIENT_ID,
    WEB_CLIENT_ID,
    FakeDeployment,
    RemoteServer,
    SigningKey,
    free_port,
    remote_server_module,
)

LIST_DATABASES = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_databases", "arguments": {}}}


@pytest.fixture
def deployment():
    with FakeDeployment() as fake:
        yield fake


@pytest.fixture
def remote(deployment, tmp_path, caplog):
    """(server module, running RemoteServer, deployment) with every log record captured at DEBUG."""
    caplog.set_level(logging.DEBUG)
    port = free_port()
    settings = deployment.settings_dict(f"http://127.0.0.1:{port}/mcp")
    with remote_server_module(settings, tmp_path) as server:
        from vams_mcp import http_app

        app = http_app.build_app(server.mcp, server.REMOTE_SETTINGS, server.CONFIG)
        with RemoteServer(app, port) as running:
            yield server, running, deployment


def _challenge(response):
    return response.headers.get("WWW-Authenticate", "")


# --- Discovery ---------------------------------------------------------------------------------------


def test_the_protected_resource_metadata_is_served_without_a_token(remote):
    _, running, deployment = remote
    for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
        response = __import__("requests").get(running.url + path, timeout=10)
        assert response.status_code == 200, path
        document = response.json()
        assert document["resource"] == f"{running.url}/mcp"
        assert document["authorization_servers"] == [deployment.issuer]
        assert document["scopes_supported"] == ["openid", "email", "profile"]
        assert document["bearer_methods_supported"] == ["header"]
        # Public discovery data only: no audience, key or API URL.
        assert REMOTE_CLIENT_ID not in response.text and deployment.api_url not in response.text


def test_a_request_without_a_token_gets_the_challenge(remote):
    _, running, deployment = remote
    response = running.post(LIST_DATABASES)
    assert response.status_code == 401
    assert _challenge(response) == (
        f'Bearer resource_metadata="{running.url}/.well-known/oauth-protected-resource/mcp"'
    )
    assert deployment.api_authorizations == []


def test_any_other_path_needs_a_token_too(remote):
    _, running, _ = remote
    import requests

    for path in ("/", "/healthz", "/mcp/extra", "/.well-known/openid-configuration"):
        assert requests.get(running.url + path, timeout=10).status_code == 401, path


def test_a_malformed_authorization_header_is_refused(remote):
    _, running, deployment = remote
    for value in ("Basic abc", "Bearer", "Bearer a b", "bearer"):
        response = running.post(LIST_DATABASES, headers={"Authorization": value})
        assert response.status_code == 401, value
    assert deployment.api_authorizations == []


# --- Tokens that do not verify -----------------------------------------------------------------------


def _bad_tokens(deployment):
    stranger = SigningKey("key-1")  # same kid, different key: a forged signature
    return {
        "bad signature": deployment.token(key=stranger),
        "wrong issuer": deployment.token(issuer="http://127.0.0.1:1/other-pool"),
        "wrong audience": deployment.token(client_id=OTHER_CLIENT_ID),
        "web app client": deployment.token(client_id=WEB_CLIENT_ID),
        "expired": deployment.token(expires_in=-60),
        "machine to machine": deployment.token(user=None),
        "wrong token_use": deployment.token(extra={"token_use": "refresh"}),
        "no expiry": deployment.token(drop=("exp",)),
        "unknown kid": deployment.token(key=SigningKey("key-unknown")),
        "not a JWT": "not-a-token",
        "alg none": "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJ1c2VybmFtZSI6ImFsaWNlIn0.",
    }


def test_tokens_that_do_not_verify_get_401_and_no_tool_runs(remote):
    _, running, deployment = remote
    for label, token in _bad_tokens(deployment).items():
        response = running.post(LIST_DATABASES, token)
        assert response.status_code == 401, label
        assert response.json()["error"] == "invalid_token", label
        assert 'error="invalid_token"' in _challenge(response), label
        assert "resource_metadata=" in _challenge(response), label
    assert deployment.api_authorizations == []


def test_an_id_token_of_the_remote_client_is_accepted(remote):
    _, running, deployment = remote
    token = deployment.token("carol", token_use="id")
    assert running.tool_result(running.call_tool("list_databases", token=token))["Items"][0]["databaseId"] == "db-of-carol"


# --- Valid tokens ------------------------------------------------------------------------------------


def test_a_valid_token_runs_the_tool_with_that_token(remote):
    _, running, deployment = remote
    token = deployment.token("alice")
    result = running.tool_result(running.call_tool("list_databases", token=token))
    assert result["Items"] == [{"databaseId": "db-of-alice", "page": 0}]
    assert deployment.api_authorizations == [f"Bearer {token}"]


def test_vams_status_reports_the_requests_user_without_an_api_call(remote):
    _, running, deployment = remote
    status = running.tool_result(running.call_tool("vams_status", token=deployment.token("alice")))
    assert status["authenticated"] is True and status["mode"] == "remote"
    assert status["userId"] == "alice" and status["clientId"] == REMOTE_CLIENT_ID
    assert status["apiUrl"] == deployment.api_url
    assert status["gates"]["toolsets"] == ["consumer"]
    assert "profile" not in status
    assert deployment.api_authorizations == []


def test_the_default_remote_tool_set_is_read_only(remote):
    _, running, deployment = remote
    response = running.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, deployment.token())
    tools = response.json()["result"]["tools"]
    assert 0 < len(tools) <= 100
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools)


def test_two_users_at_the_same_time_get_their_own_results(remote):
    _, running, deployment = remote
    deployment.api_delay = 0.4  # both calls are in flight together
    results = {}

    def call(user):
        token = deployment.token(user)
        results[user] = (token, running.tool_result(running.call_tool("list_databases", token=token)))

    threads = [threading.Thread(target=call, args=(user,)) for user in ("alice", "bob", "alice2", "bob2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert {user: result["Items"][0]["databaseId"] for user, (_, result) in results.items()} == {
        "alice": "db-of-alice", "bob": "db-of-bob", "alice2": "db-of-alice2", "bob2": "db-of-bob2",
    }
    # Each user's token reached the API, and only in that user's own request.
    assert sorted(deployment.api_authorizations) == sorted(f"Bearer {token}" for token, _ in results.values())


def test_a_token_the_api_refuses_is_reported_as_a_sign_in_to_renew(remote):
    _, running, deployment = remote
    deployment.refused_users.add("mallory")
    token = deployment.token("mallory")
    result = running.tool_result(running.call_tool("list_databases", token=token))
    assert result["error_type"] == "NotAuthenticated"
    assert "vamscli" not in json.dumps(result)
    assert token not in json.dumps(result)


# --- Fail closed -------------------------------------------------------------------------------------


def test_keys_that_cannot_be_read_refuse_every_request(deployment, tmp_path):
    deployment.jwks_status = 500
    port = free_port()
    with remote_server_module(deployment.settings_dict(f"http://127.0.0.1:{port}/mcp"), tmp_path) as server:
        from vams_mcp import http_app

        app = http_app.build_app(server.mcp, server.REMOTE_SETTINGS, server.CONFIG)
        with RemoteServer(app, port) as running:
            response = running.post(LIST_DATABASES, deployment.token())
            assert response.status_code == 503
            assert response.headers.get("Retry-After") == "30"
            assert deployment.api_authorizations == []
            # The metadata document still answers, so a client can find the issuer.
            assert __import__("requests").get(running.url + "/.well-known/oauth-protected-resource/mcp").status_code == 200


def test_an_oversized_body_is_refused(remote):
    _, running, deployment = remote
    big = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "search_assets", "arguments": {"query": "x" * (2 * 1024 * 1024)}}}
    assert running.post(big, deployment.token()).status_code == 413
    assert deployment.api_authorizations == []


# --- No token anywhere -------------------------------------------------------------------------------


def test_no_token_reaches_a_log_or_the_disk(remote, caplog):
    _, running, deployment = remote
    good = deployment.token("alice")
    running.tool_result(running.call_tool("list_databases", token=good))
    deployment.refused_users.add("mallory")
    refused = deployment.token("mallory")
    running.tool_result(running.call_tool("list_databases", token=refused))
    tokens = [good, refused, *_bad_tokens(deployment).values()]
    for token in tokens[2:]:
        running.post(LIST_DATABASES, token)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "user=alice" in logged and "tool=list_databases" in logged, "the per-user log line is missing"
    for token in tokens:
        assert token not in logged
        # Not even its signature part.
        if token.count(".") == 2 and token.rsplit(".", 1)[-1]:
            assert token.rsplit(".", 1)[-1] not in logged

    # The CLI wrote no request log and no profile: the isolated HOME holds nothing of this server.
    home = Path(os.environ["HOME"])
    written = [str(p.relative_to(home)) for p in home.rglob("*") if p.is_file()]
    assert not any("vamscli.log" in name or "auth_profile" in name for name in written), written


def test_a_request_for_another_host_name_is_refused(remote):
    """DNS rebinding protection: a valid token sent to the server under a foreign Host header."""
    _, running, deployment = remote
    response = running.post(LIST_DATABASES, deployment.token(), headers={"Host": "attacker.example.com"})
    assert response.status_code == 421
    assert deployment.api_authorizations == []


def test_both_spellings_of_the_endpoint_are_served_without_a_redirect(remote):
    _, running, deployment = remote
    for path in ("/mcp", "/mcp/"):
        response = running.post(LIST_DATABASES, deployment.token(), path=path)
        assert response.status_code == 200, (path, response.status_code)


def test_a_chunked_body_over_the_limit_is_refused_with_413(remote):
    import requests

    _, running, deployment = remote

    def chunks():
        for _ in range(40):
            yield b"x" * 65536  # 2.5 MiB in all, with no Content-Length

    response = requests.post(
        f"{running.url}/mcp", data=chunks(), timeout=30,
        headers={"Authorization": f"Bearer {deployment.token()}", "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 413
    assert deployment.api_authorizations == []
    # The server stays healthy.
    assert running.tool_result(running.call_tool("vams_status", token=deployment.token()))["authenticated"] is True


def test_a_chunked_body_within_the_limit_is_served(remote):
    import requests

    _, running, deployment = remote
    payload = json.dumps(LIST_DATABASES).encode()
    response = requests.post(
        f"{running.url}/mcp", data=iter([payload[:10], payload[10:]]), timeout=30, allow_redirects=False,
        headers={"Authorization": f"Bearer {deployment.token('alice')}", "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"},
    )
    assert running.tool_result(response)["Items"][0]["databaseId"] == "db-of-alice"
