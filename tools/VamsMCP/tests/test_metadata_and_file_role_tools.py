"""The file metadata, asset-link metadata, primary-file, file-revert and asset-metadata delete tools.

Each test asserts what reaches the CLI method (Adding a New Tool, step 8). Payload keys are the ones
the backend request models declare: ``UpdateFileMetadataRequestModel`` and
``GetFileMetadataRequestModel`` (backend/backend/models/metadata.py), ``SetPrimaryFileRequestModel``
and ``RevertFileVersionRequestModel`` (backend/backend/models/assetsV3.py).
"""

import importlib
import os
from unittest.mock import MagicMock

import pytest

from vams_mcp import server as server_module

GATE_VARS = ("VAMS_ENABLE_WRITES", "VAMS_ENABLE_DESTRUCTIVE")


def _paging(client):
    client.config = server_module.CONFIG
    client.unwrap_message = server_module.VamsClient.unwrap_message
    client.paginate = lambda *a, **k: server_module.VamsClient.paginate(client, *a, **k)
    return client


@pytest.fixture
def mock_client(monkeypatch):
    client = _paging(MagicMock())
    monkeypatch.setattr(server_module, "CLIENT", client)
    return client


@pytest.fixture
def gated(monkeypatch):
    for name in GATE_VARS:
        os.environ[name] = "true"
    importlib.reload(server_module)
    try:
        client = _paging(MagicMock())
        monkeypatch.setattr(server_module, "CLIENT", client)
        yield server_module, client
    finally:
        for name in GATE_VARS:
            os.environ.pop(name, None)
        importlib.reload(server_module)


# --- Reads ------------------------------------------------------------------


def test_get_file_metadata_reads_the_metadata_rows_and_resumes(mock_client):
    mock_client.api.get_file_metadata_v2.return_value = {
        "metadata": [{"metadataKey": "k", "metadataValue": "v"}], "NextToken": "t2",
    }
    result = server_module.get_file_metadata("db1", "a1", "/a.glb", max_items=1, starting_token="t1")
    assert result["Items"] == [{"metadataKey": "k", "metadataValue": "v"}]
    assert result["NextToken"] == "t2" and result["truncated"] is True
    mock_client.api.get_file_metadata_v2.assert_called_once_with(
        "db1", "a1", "/a.glb", metadata_type="metadata", page_size=1, starting_token="t1", show_archived=False,
    )


def test_get_file_metadata_reads_attributes_and_archived_assets(mock_client):
    mock_client.api.get_file_metadata_v2.return_value = {"metadata": []}
    server_module.get_file_metadata("db1", "a1", "/a.glb", metadata_type="attribute", include_archived=True)
    kwargs = mock_client.api.get_file_metadata_v2.call_args.kwargs
    assert kwargs["metadata_type"] == "attribute" and kwargs["show_archived"] is True


def test_get_file_metadata_refuses_an_unknown_type_without_a_request(mock_client):
    result = server_module.get_file_metadata("db1", "a1", "/a.glb", metadata_type="tags")
    assert result["error_type"] == "ValueError"
    mock_client.api.get_file_metadata_v2.assert_not_called()


def test_get_asset_link_metadata_reads_the_metadata_rows(mock_client):
    mock_client.api.get_asset_link_metadata_v2.return_value = {"metadata": [{"metadataKey": "k"}]}
    result = server_module.get_asset_link_metadata("link-1", include_archived=True)
    assert result["Items"] == [{"metadataKey": "k"}] and "truncated" not in result
    mock_client.api.get_asset_link_metadata_v2.assert_called_once_with(
        "link-1", page_size=server_module.CONFIG.page_size, starting_token=None, show_archived=True,
    )


async def test_the_new_reads_are_registered_without_the_gates():
    names = {tool.name for tool in await server_module.mcp.list_tools()}
    assert {"get_file_metadata", "get_asset_link_metadata"} <= names
    assert not names & {"set_file_metadata", "set_primary_file", "revert_file_version", "delete_asset_metadata"}


# --- Writes -----------------------------------------------------------------


def test_set_file_metadata_upserts_string_items(gated):
    server, client = gated
    server.set_file_metadata("db1", "a1", "/a.glb", {"material": "steel", "parts": 3})
    client.api.update_file_metadata_v2.assert_called_once_with(
        "db1", "a1", "/a.glb", "metadata",
        [
            {"metadataKey": "material", "metadataValue": "steel", "metadataValueType": "string"},
            {"metadataKey": "parts", "metadataValue": "3", "metadataValueType": "string"},
        ],
        update_type="update",
    )


def test_set_file_metadata_writes_attributes(gated):
    server, client = gated
    server.set_file_metadata("db1", "a1", "/a.glb", {"k": "v"}, metadata_type="attribute")
    assert client.api.update_file_metadata_v2.call_args.args[3] == "attribute"
    assert server.set_file_metadata("db1", "a1", "/a.glb", {"k": "v"}, metadata_type="x")["error_type"] == "ValueError"


@pytest.mark.parametrize("given, sent", [("PRIMARY", "primary"), ("lod3", "lod3"), ("", "")])
def test_set_primary_file_sends_the_lowercase_type(gated, given, sent):
    server, client = gated
    server.set_primary_file("db1", "a1", "/a.glb", given)
    client.api.set_primary_file.assert_called_once_with("db1", "a1", {"filePath": "/a.glb", "primaryType": sent})


def test_set_primary_file_names_an_other_type(gated):
    server, client = gated
    server.set_primary_file("db1", "a1", "/a.glb", "other", primary_type_other="preview")
    client.api.set_primary_file.assert_called_once_with(
        "db1", "a1", {"filePath": "/a.glb", "primaryType": "other", "primaryTypeOther": "preview"}
    )


def test_set_primary_file_refuses_an_unknown_type_without_a_request(gated):
    server, client = gated
    assert server.set_primary_file("db1", "a1", "/a.glb", "lod9")["error_type"] == "ValueError"
    client.api.set_primary_file.assert_not_called()


def test_revert_file_version_sends_the_version_and_path(gated):
    server, client = gated
    server.revert_file_version("db1", "a1", "/a.glb", "v-123")
    client.api.revert_file_version.assert_called_once_with("db1", "a1", "v-123", {"filePath": "/a.glb"})


# --- Destructive ------------------------------------------------------------


def test_delete_asset_metadata_sends_the_keys(gated):
    server, client = gated
    server.delete_asset_metadata("db1", "a1", ["obsolete", "draft"])
    client.api.delete_asset_metadata_v2.assert_called_once_with("db1", "a1", ["obsolete", "draft"])
