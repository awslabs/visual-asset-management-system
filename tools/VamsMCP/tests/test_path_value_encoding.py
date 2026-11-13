"""#1476: the asset tools reach the asset their id names when the id ends in '#'.

The tools call vamscli's real APIClient, which puts each id into its own path segment. Assets `x-q`
and `x-q#` in one database are two assets; a request for `x-q#` must never address `x-q`.
"""

from unittest.mock import MagicMock

import pytest

from vams_mcp.client import VamsClient

ASSET_TOOLS = [
    # (tool, extra arguments, method, path suffix)
    ("get_asset", (), "GET", ""),
    ("update_asset", ({"description": "d"},), "PUT", ""),
    ("list_asset_files", (), "GET", "/listFiles"),
    ("archive_asset", (), "DELETE", "/archiveAsset"),
    ("delete_asset", (), "DELETE", "/deleteAsset"),
]


class _EmptyAnswer:
    status_code = 200
    content = b"{}"
    text = "{}"
    headers = {}

    def json(self):
        return {}

    def raise_for_status(self):
        return None


@pytest.fixture
def server_and_sent(server_with_switches, monkeypatch):
    """The server with every gate open over a client holding vamscli's real APIClient, whose session
    records each request and answers an empty 200."""
    from vamscli.utils.api_client import APIClient

    with server_with_switches([], enable_writes=True, enable_destructive=True) as server:
        client = server.CLIENT
        client.paginate = lambda *args, **kwargs: VamsClient.paginate(client, *args, **kwargs)
        client.api = APIClient("https://api.example.com", profile_manager=MagicMock())
        sent = []
        # The client's request log is a file under the vamscli config directory; the server tests
        # assert no such file is written, so the log calls are stubbed rather than run.
        monkeypatch.setattr("vamscli.utils.logging.log_api_request", lambda *args, **kwargs: None)
        monkeypatch.setattr("vamscli.utils.logging.log_api_response", lambda *args, **kwargs: None)
        monkeypatch.setattr(client.api, "_validate_token_before_request", lambda include_auth: None)
        monkeypatch.setattr(client.api, "_get_headers", lambda include_auth: {})
        monkeypatch.setattr(
            client.api.session, "request",
            lambda method, url, **kwargs: sent.append((method, url)) or _EmptyAnswer())
        yield server, sent


@pytest.mark.parametrize("tool,extra,method,suffix", ASSET_TOOLS)
def test_each_sibling_reaches_its_own_asset(server_and_sent, tool, extra, method, suffix):
    server, sent = server_and_sent
    getattr(server, tool)("db-1", "x-q#", *extra)
    getattr(server, tool)("db-1", "x-q", *extra)
    assert sent == [
        (method, f"https://api.example.com/database/db-1/assets/x-q%23{suffix}"),
        (method, f"https://api.example.com/database/db-1/assets/x-q{suffix}"),
    ]
