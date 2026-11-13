"""#1476: the asset tools reach the asset their id names when the id ends in '#'.

The tools call vamscli's real APIClient, which puts each id into its own path segment. Assets `x-q`
and `x-q#` in one database are two assets; a request for `x-q#` must never address `x-q`. A tool that
builds its own path uses `route_path` too, and a control character in an id is refused unsent.
"""

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import vams_mcp
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


def test_list_assets_puts_the_database_id_in_its_own_segment(server_and_sent):
    server, sent = server_and_sent
    client = server.CLIENT
    client.get_json = lambda *args, **kwargs: VamsClient.get_json(client, *args, **kwargs)
    server.list_assets(database_id="db#1")
    assert [url.split("?", 1)[0] for _, url in sent] == ["https://api.example.com/database/db%231/assets"]


@pytest.mark.parametrize("asset_id", ["x-q\t", "x-q%09"])
def test_a_control_character_is_refused_before_anything_is_sent(server_and_sent, asset_id):
    server, sent = server_and_sent
    result = server.get_asset("db-1", asset_id)
    assert "may not contain a control character" in result["error"]
    assert sent == []


def test_server_endpoints_are_built_with_route_path():
    """Ratchet: a tool that formats a route constant itself would put a value into the path
    unencoded."""
    package = Path(vams_mcp.__file__).parent
    offenders, sources = [], sorted(package.rglob("*.py"))
    for source in sources:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "format" and isinstance(node.func.value, ast.Name)
                    and node.func.value.id.startswith("API_")):
                offenders.append(f"{source.relative_to(package)}:{node.lineno}")
    assert any(source.name == "server.py" for source in sources), "control: the walk sees the server"
    assert offenders == []
