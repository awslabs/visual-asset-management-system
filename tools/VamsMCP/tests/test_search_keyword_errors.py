"""The keyword search tools report the server's reason for a refused or unavailable search (issue #1265).

`search_assets`, `search_files` and `find_and_summarize` call `APIClient.search_query`, which maps the
status code and the handler's message itself (`raise_http_errors=True`). Through the real vamscli client,
with only its HTTP session (and the two auth helpers that read the profile) replaced, a 503 reaches the
agent as `{"error": <the server's message>, "error_type": "SearchUnavailableError"}` and a 400 as
`Invalid search parameters: <the server's message>`, rather than the request wrapper's generic
`Server error (503)` / `Invalid request (400)` text.
"""

import json
from unittest.mock import MagicMock, Mock

import pytest
import requests

from vams_mcp import server
from vamscli.utils.api_client import APIClient

SEARCH_UNAVAILABLE_MESSAGE = "Search is temporarily unavailable. Try again in a moment."


def _api_answering(monkeypatch, status, body):
    api = APIClient("https://example.invalid/api", Mock())
    monkeypatch.setattr(api, "_validate_token_before_request", lambda include_auth: None)
    monkeypatch.setattr(api, "_get_headers", lambda include_auth: {})
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    response.url = "https://example.invalid/api/search"
    monkeypatch.setattr(api.session, "request", Mock(return_value=response))
    return api


@pytest.fixture
def client_with(monkeypatch):
    def build(status, body):
        client = MagicMock()
        client.search_enabled.return_value = True
        client.api = _api_answering(monkeypatch, status, body)
        client.trim_search_results = server.VamsClient.trim_search_results
        monkeypatch.setattr(server, "CLIENT", client)
        return client

    return build


TOOLS = [
    ("search_assets", lambda: server.search_assets(query="pump")),
    ("search_files", lambda: server.search_files(query="pump")),
    ("find_and_summarize", lambda: server.find_and_summarize(query="pump")),
]


@pytest.mark.parametrize("name, call", TOOLS, ids=[name for name, _ in TOOLS])
class TestKeywordSearchToolErrors:
    def test_a_503_is_returned_with_the_servers_message(self, client_with, name, call):
        client_with(503, {"message": SEARCH_UNAVAILABLE_MESSAGE})
        result = call()
        assert result == {"error": SEARCH_UNAVAILABLE_MESSAGE, "error_type": "SearchUnavailableError"}

    def test_a_400_names_the_refused_parameter(self, client_with, name, call):
        client_with(400, {"message": "size must be at most 2000"})
        result = call()
        assert result["error_type"] == "APIError"
        assert result["error"] == "Invalid search parameters: size must be at most 2000"

    def test_a_200_is_a_result(self, client_with, name, call):
        # Control: the same path answers a successful search with results, not an error.
        client_with(200, {"hits": {"total": {"value": 0}, "hits": []}})
        result = call()
        assert "error" not in result
        assert result["returned"] == 0
