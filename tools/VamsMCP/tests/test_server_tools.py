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


def test_search_assets_passes_geo_search(mock_client):
    mock_client.api.search_query.return_value = {"hits": {"total": {"value": 0}, "hits": []}}
    mock_client.trim_search_results.return_value = {"total": 0, "returned": 0, "results": []}

    geo = {"point": {"lat": 47.6, "lon": -122.3, "radiusMeters": 500}, "relation": "within"}
    server.search_assets(query="bridge", geo_search=geo)

    request = mock_client.api.search_query.call_args.args[0]
    assert request["geoSearch"] == geo


def test_list_assets_without_database_lists_all(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_assets()
    fetch = mock_client.paginate.call_args.args[0]
    mock_client.get_json.return_value = {"Items": []}
    fetch({"pageSize": 10})
    assert mock_client.get_json.call_args.args[0] == "/assets"


def test_list_asset_versions_uses_versions_key(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_asset_versions("db1", "a1")
    assert mock_client.paginate.call_args.kwargs["items_key"] == "versions"


def test_list_asset_files_uses_lowercase_items_key(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_asset_files("db1", "a1")
    assert mock_client.paginate.call_args.kwargs["items_key"] == "items"


def test_list_workflow_executions_caps_page_size(mock_client):
    mock_client.paginate.return_value = {"Items": [], "count": 0}
    server.list_workflow_executions("db1", "a1")
    assert mock_client.paginate.call_args.kwargs["page_size"] <= 50


@pytest.mark.asyncio
async def test_read_tools_registered():
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    for expected in (
        "list_databases",
        "get_asset",
        "search_assets",
        "find_and_summarize",
        "list_allowed_api_routes",
        "get_asset_history",
    ):
        assert expected in names


@pytest.mark.asyncio
async def test_write_tools_gated_off_by_default():
    # The test environment sets no VAMS_ENABLE_* vars, so mutating tools must
    # not be registered at all.
    names = {t.name for t in await server.mcp.list_tools()}
    for gated in ("create_asset", "execute_workflow", "delete_asset", "archive_asset"):
        assert gated not in names


def test_generate_download_url_is_non_mutating(mock_client):
    mock_client.api.download_asset_file.return_value = {"url": "https://example/presigned"}
    result = server.generate_download_url("db1", "a1", file_key="model.glb")
    assert result == {"url": "https://example/presigned"}
    mock_client.api.download_asset_file.assert_called_once()
