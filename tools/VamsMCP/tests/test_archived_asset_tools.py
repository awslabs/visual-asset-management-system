"""#870: the asset read tools take include_archived to read an archived asset.

The asset GET tool already maps include_archived to the API's showArchived. The tools that read
an asset's files, versions, metadata, links and comments now do the same, so an agent that found an
archived asset with get_asset(include_archived=True) can read the rest of it. Without the argument
each tool sends what it sent before.
"""

from unittest.mock import MagicMock

import pytest

from vams_mcp import server


@pytest.fixture
def client(monkeypatch):
    mock = MagicMock()
    mock.config = server.CONFIG
    mock.unwrap_message = server.VamsClient.unwrap_message
    mock.paginate = lambda *args, **kwargs: server.VamsClient.paginate(mock, *args, **kwargs)
    mock.api.list_asset_files.return_value = {"items": []}
    mock.api.get_asset_versions.return_value = {"versions": []}
    mock.api.get_asset_metadata_v2.return_value = {"metadata": []}
    mock.api.get_asset_version.return_value = {"assetVersionId": "1"}
    mock.api.get_asset_links_for_asset.return_value = {"related": [], "parents": [], "children": []}
    mock.api.list_asset_comments.return_value = {"message": []}
    mock.api.list_asset_version_comments.return_value = {"message": []}
    monkeypatch.setattr(server, "CLIENT", mock)
    return mock


def _params_sent(api_method):
    return [call.kwargs["params"] for call in api_method.call_args_list]


@pytest.mark.parametrize("archived", [True, False])
def test_list_asset_files(client, archived):
    server.list_asset_files("db1", "a1", include_archived=archived)
    for params in _params_sent(client.api.list_asset_files):
        assert (params.get("showArchived") == "true") is archived


@pytest.mark.parametrize("archived", [True, False])
def test_list_asset_versions(client, archived):
    server.list_asset_versions("db1", "a1", include_archived=archived)
    for params in _params_sent(client.api.get_asset_versions):
        assert (params.get("showArchived") == "true") is archived


@pytest.mark.parametrize("archived", [True, False])
def test_get_asset_metadata(client, archived):
    server.get_asset_metadata("db1", "a1", include_archived=archived)
    assert client.api.get_asset_metadata_v2.call_args.kwargs["show_archived"] is archived


@pytest.mark.parametrize("archived", [True, False])
def test_get_asset_version(client, archived):
    server.get_asset_version("db1", "a1", "1", include_archived=archived)
    assert client.api.get_asset_version.call_args.kwargs["show_archived"] is archived


@pytest.mark.parametrize("archived", [True, False])
def test_get_asset_links(client, archived):
    server.get_asset_links("db1", "a1", include_archived=archived)
    assert client.api.get_asset_links_for_asset.call_args.kwargs["show_archived"] is archived


@pytest.mark.parametrize("archived", [True, False])
def test_list_asset_comments(client, archived):
    server.list_asset_comments("a1", include_archived=archived)
    assert client.api.list_asset_comments.call_args.kwargs["show_archived"] is archived


@pytest.mark.parametrize("archived", [True, False])
def test_list_asset_version_comments(client, archived):
    server.list_asset_version_comments("a1", "1", include_archived=archived)
    assert client.api.list_asset_version_comments.call_args.kwargs["show_archived"] is archived


def test_defaults_send_nothing_new(client):
    server.list_asset_files("db1", "a1")
    server.list_asset_versions("db1", "a1")
    server.get_asset_metadata("db1", "a1")
    assert "showArchived" not in _params_sent(client.api.list_asset_files)[0]
    assert "showArchived" not in _params_sent(client.api.get_asset_versions)[0]
    assert client.api.get_asset_metadata_v2.call_args.kwargs["show_archived"] is False


@pytest.mark.parametrize("tool", [
    "list_asset_files", "list_asset_versions", "get_asset_metadata", "get_asset_version",
    "get_asset_links", "list_asset_comments", "list_asset_version_comments",
])
def test_docstring_names_the_argument(tool):
    assert "include_archived" in (getattr(server, tool).__doc__ or "")
