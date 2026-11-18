"""Keyword search errors reach the person with the server's reason (issue #1265).

`APIClient.search_query` (POST /search) and `APIClient.search_simple` (POST /search/simple) pass
`raise_http_errors=True`, so their own mapping sees the status code and the handler's message: a 400 names
the refused parameter, the feature-off 404 says search is not available, a 503 (OpenSearch enabled but
unable to answer now) is a SearchUnavailableError carrying the server's message, and any other status
keeps the server's message. Before, the request wrapper turned every 4xx/5xx into its own generic
APIError first, so those branches never ran. The client tests run through the real request wrapper with
only the HTTP session (and the two auth helpers that read the profile) replaced; the command tests show
what `search assets`, `search files` and `search simple` print for the 503.
"""

import json
from unittest.mock import Mock, patch

import pytest
import requests

from vamscli.main import cli
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import APIError, SearchUnavailableError

# The backend's answers (backend/backend/handlers/osSemanticSearch/search.py): the 503 an enabled
# OpenSearch that cannot answer gets, and the feature-off 404.
SEARCH_UNAVAILABLE_MESSAGE = "Search is temporarily unavailable. Try again in a moment."
SEARCH_NOT_AVAILABLE_MESSAGE = "Search is not available when OpenSearch feature is not enabled"

METHODS = [("search_query", "/search"), ("search_simple", "/search/simple")]


def _client_answering(monkeypatch, status, body, path):
    client = APIClient("https://example.invalid/api", Mock())
    monkeypatch.setattr(client, "_validate_token_before_request", lambda include_auth: None)
    monkeypatch.setattr(client, "_get_headers", lambda include_auth: {})
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    response.url = f"https://example.invalid/api{path}"
    monkeypatch.setattr(client.session, "request", Mock(return_value=response))
    return client


@pytest.mark.parametrize("method, path", METHODS, ids=[m for m, _ in METHODS])
class TestKeywordSearchClientThroughTheRequestWrapper:
    def test_a_503_is_search_unavailable_with_the_servers_message(self, monkeypatch, method, path):
        client = _client_answering(monkeypatch, 503, {"message": SEARCH_UNAVAILABLE_MESSAGE}, path)
        with pytest.raises(SearchUnavailableError) as caught:
            getattr(client, method)({"query": "pump"})
        assert str(caught.value) == SEARCH_UNAVAILABLE_MESSAGE

    def test_a_400_names_the_refused_parameter(self, monkeypatch, method, path):
        client = _client_answering(monkeypatch, 400, {"message": "size must be at most 2000"}, path)
        with pytest.raises(APIError, match="^Invalid search parameters: size must be at most 2000$"):
            getattr(client, method)({"query": "pump", "size": 5000})

    def test_the_feature_off_404_says_search_is_not_available(self, monkeypatch, method, path):
        client = _client_answering(monkeypatch, 404, {"message": SEARCH_NOT_AVAILABLE_MESSAGE}, path)
        with pytest.raises(APIError, match=f"^Search is not available: {SEARCH_NOT_AVAILABLE_MESSAGE}$"):
            getattr(client, method)({"query": "pump"})

    def test_another_404_says_the_endpoint_was_not_found(self, monkeypatch, method, path):
        client = _client_answering(monkeypatch, 404, {"message": "Not Found"}, path)
        with pytest.raises(APIError, match="^Search endpoint not found: Not Found$"):
            getattr(client, method)({"query": "pump"})

    def test_another_status_keeps_the_servers_message(self, monkeypatch, method, path):
        client = _client_answering(monkeypatch, 500, {"message": "Internal Server Error"}, path)
        with pytest.raises(APIError) as caught:
            getattr(client, method)({"query": "pump"})
        assert "(500): Internal Server Error" in str(caught.value)
        assert not isinstance(caught.value, SearchUnavailableError)

    def test_a_200_returns_the_body(self, monkeypatch, method, path):
        # Control: the flag changes only how an error status is reported.
        body = {"hits": {"total": {"value": 0}, "hits": []}}
        client = _client_answering(monkeypatch, 200, body, path)
        assert getattr(client, method)({"query": "pump"}) == body


COMMANDS = [
    (["search", "assets", "-q", "pump"], "search_query"),
    (["search", "files", "-q", "pump"], "search_query"),
    (["search", "simple", "-q", "pump"], "search_simple"),
]


@pytest.fixture
def search_command_mocks(generic_command_mocks):
    return generic_command_mocks('search')


@pytest.mark.parametrize("args, method", COMMANDS, ids=[" ".join(a[:2]) for a, _ in COMMANDS])
class TestKeywordSearchUnavailableCommand:
    def test_the_servers_reason_and_a_retry_hint_are_printed(self, cli_runner, search_command_mocks,
                                                            args, method):
        with search_command_mocks as mocks, \
                patch('vamscli.commands.search.is_feature_enabled', return_value=False):
            getattr(mocks['api_client'], method).side_effect = SearchUnavailableError(SEARCH_UNAVAILABLE_MESSAGE)
            result = cli_runner.invoke(cli, args)
        assert result.exit_code == 1
        assert "Search Unavailable" in result.output
        assert SEARCH_UNAVAILABLE_MESSAGE in result.output
        assert "Retry in a moment" in result.output
        assert "Unexpected error" not in result.output

    def test_json_output_names_the_error_type(self, cli_runner, search_command_mocks, args, method):
        with search_command_mocks as mocks, \
                patch('vamscli.commands.search.is_feature_enabled', return_value=False):
            getattr(mocks['api_client'], method).side_effect = SearchUnavailableError(SEARCH_UNAVAILABLE_MESSAGE)
            result = cli_runner.invoke(cli, args + ['--json-output'])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["error_type"] == "SearchUnavailableError"
        assert payload["error"] == SEARCH_UNAVAILABLE_MESSAGE
