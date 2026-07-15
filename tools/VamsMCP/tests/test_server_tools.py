"""Tests for the MCP tool functions in vams_mcp.server.

Tools reference the module-global CLIENT, so we patch it with a MagicMock.
No live VAMS deployment or vamscli profile is required.
"""

from unittest.mock import MagicMock

import pytest

from vams_mcp import server


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(server, "CLIENT", client)
    return client


def test_list_databases_uses_pagination(mock_client):
    mock_client.paginate.return_value = {"Items": [{"databaseId": "db1"}], "count": 1}
    result = server.list_databases()
    assert result == {"Items": [{"databaseId": "db1"}], "count": 1}
    assert mock_client.paginate.called


def test_get_asset_calls_api(mock_client):
    mock_client.api.get_asset.return_value = {"assetId": "a1"}
    result = server.get_asset("db1", "a1")
    assert result == {"assetId": "a1"}
    mock_client.api.get_asset.assert_called_once_with("db1", "a1", show_archived=False)


def test_tool_result_wraps_errors(mock_client):
    mock_client.api.get_database.side_effect = RuntimeError("boom")
    result = server.get_database("missing")
    assert result["error"] == "boom"
    assert result["error_type"] == "RuntimeError"


def test_search_assets_builds_request(mock_client):
    mock_client.api.search_query.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
    mock_client.trim_search_results.return_value = {"total": 0, "returned": 0, "results": []}

    server.search_assets(query="bridge", database_id="db1", size=5)

    assert mock_client.api.search_query.called
    request = mock_client.api.search_query.call_args.args[0]
    assert request["entityTypes"] == ["asset"]
    assert request["query"] == "bridge"
    assert request["size"] == 5
    # database_id becomes a filter
    assert any("db1" in str(f) for f in request.get("filters", []))


def test_search_files_entity_type(mock_client):
    mock_client.api.search_query.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
    mock_client.trim_search_results.return_value = {"total": 0, "returned": 0, "results": []}
    server.search_files(query="texture")
    request = mock_client.api.search_query.call_args.args[0]
    assert request["entityTypes"] == ["file"]


@pytest.mark.asyncio
async def test_read_tools_registered():
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    for expected in (
        "list_databases",
        "get_asset",
        "search_assets",
        "find_and_summarize",
    ):
        assert expected in names


def test_generate_download_url_is_non_mutating(mock_client):
    mock_client.api.download_asset_file.return_value = {"url": "https://example/presigned"}
    result = server.generate_download_url("db1", "a1", file_key="model.glb")
    assert result == {"url": "https://example/presigned"}
    mock_client.api.download_asset_file.assert_called_once()
