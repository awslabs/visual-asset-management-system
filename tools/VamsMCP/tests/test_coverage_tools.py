"""The file, export, feature-switch, asset-link and tag tools: what each sends to the APIClient.

Each test asserts the arguments that reach the CLI method (Adding a New Tool, step 8): a dropped or
misspelled key is a silent server default or a 400 against a live deployment, which an assertion on
the return value would not catch. Payload keys are the ones the backend request models declare
(backend/backend/models/assetsV3.py, assetExport.py, assetLinks.py, tag.py).
"""

import importlib
import os
from unittest.mock import MagicMock

import pytest

from vams_mcp import server as server_module

GATE_VARS = ("VAMS_ENABLE_WRITES", "VAMS_ENABLE_DESTRUCTIVE")


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(server_module, "CLIENT", client)
    return client


@pytest.fixture
def gated(monkeypatch):
    """The server reloaded with both gates on and a mocked client. Yields (module, client)."""
    for name in GATE_VARS:
        os.environ[name] = "true"
    importlib.reload(server_module)
    try:
        assert server_module.CONFIG.enable_destructive is True
        client = MagicMock()
        client.unwrap_message = server_module.VamsClient.unwrap_message
        monkeypatch.setattr(server_module, "CLIENT", client)
        yield server_module, client
    finally:
        for name in GATE_VARS:
            os.environ.pop(name, None)
        importlib.reload(server_module)


# --- Reads ------------------------------------------------------------------


def test_get_file_info_sends_the_path_and_flags(mock_client):
    mock_client.api.get_file_info.return_value = {"fileName": "a.glb", "relativePath": "/a.glb"}
    result = server_module.get_file_info("db1", "a1", "/a.glb")
    assert result["fileName"] == "a.glb"
    mock_client.api.get_file_info.assert_called_once_with(
        "db1", "a1", {"filePath": "/a.glb", "includeVersions": "false"}
    )


def test_get_file_info_forwards_versions_and_archived(mock_client):
    server_module.get_file_info("db1", "a1", "/a.glb", include_versions=True, include_archived=True)
    params = mock_client.api.get_file_info.call_args.args[2]
    assert params == {"filePath": "/a.glb", "includeVersions": "true", "showArchived": "true"}


def test_export_asset_sends_every_filter_with_the_bounded_defaults(mock_client):
    mock_client.api.export_asset.return_value = {"assets": [], "totalAssetsInTree": 0, "assetsInThisPage": 0}
    result = server_module.export_asset("db1", "root")
    assert "truncated" not in result
    database_id, asset_id, params = mock_client.api.export_asset.call_args.args
    assert (database_id, asset_id) == ("db1", "root")
    assert params == {
        "generatePresignedUrls": False,
        "includeFolderFiles": False,
        "includeOnlyPrimaryTypeFiles": False,
        "includeFileMetadata": True,
        "includeAssetLinkMetadata": True,
        "includeAssetMetadata": True,
        "fetchAssetRelationships": True,
        "fetchEntireChildrenSubtrees": False,
        "includeParentRelationships": False,
        "includeArchivedFiles": False,
        "maxAssets": server_module.EXPORT_DEFAULT_MAX_ASSETS,
        "maxFiles": server_module.EXPORT_DEFAULT_MAX_FILES,
    }


def test_export_asset_forwards_the_narrowing_parameters(mock_client):
    mock_client.api.export_asset.return_value = {"assets": []}
    server_module.export_asset(
        "db1", "root", max_assets=5, max_files=50, starting_token="tok", fetch_relationships=False,
        fetch_entire_subtrees=True, include_parent_relationships=True, include_archived_files=True,
        include_folder_files=True, include_only_primary_type_files=True, file_extensions=["GLB", ".bin "],
        include_asset_metadata=False, include_file_metadata=False, include_asset_link_metadata=False,
    )
    params = mock_client.api.export_asset.call_args.args[2]
    assert params["maxAssets"] == 5 and params["maxFiles"] == 50
    assert params["startingToken"] == "tok"
    assert params["fileExtensions"] == [".glb", ".bin"]
    assert params["fetchAssetRelationships"] is False
    assert params["fetchEntireChildrenSubtrees"] is True
    assert params["includeParentRelationships"] is True
    assert params["includeArchivedFiles"] is True
    assert params["includeFolderFiles"] is True
    assert params["includeOnlyPrimaryTypeFiles"] is True
    assert params["includeAssetMetadata"] is False
    assert params["includeFileMetadata"] is False
    assert params["includeAssetLinkMetadata"] is False


def test_export_asset_never_asks_for_presigned_urls(mock_client):
    """The URLs are bearer credentials; the download tools carry that warning, the export does not."""
    mock_client.api.export_asset.return_value = {"assets": []}
    server_module.export_asset("db1", "root", file_extensions=[".glb"])
    assert mock_client.api.export_asset.call_args.args[2]["generatePresignedUrls"] is False


def test_export_asset_marks_a_page_with_a_token_as_truncated(mock_client):
    mock_client.api.export_asset.return_value = {
        "assets": [{"assetid": "root"}], "NextToken": "next", "totalAssetsInTree": 40, "assetsInThisPage": 25,
    }
    result = server_module.export_asset("db1", "root")
    assert result["truncated"] is True
    assert result["NextToken"] == "next"
    assert "25 of 40" in result["note"] and "starting_token" in result["note"]
    assert "files_truncated" not in result["note"]


def test_export_asset_note_names_assets_whose_files_continue(mock_client):
    mock_client.api.export_asset.return_value = {
        "assets": [{"assetid": "root", "files_truncated": True}],
        "NextToken": "next",
        "totalAssetsInTree": 1,
        "assetsInThisPage": 1,
    }
    result = server_module.export_asset("db1", "root")
    assert result["truncated"] is True
    assert "files_truncated have more files on the next page" in result["note"]


def test_list_features_reports_the_profile_snapshot(mock_client):
    mock_client.feature_switches.return_value = {
        "has_feature_switches": True, "enabled": ["A", "B"], "total": 2, "fetched_at": "2026-11-12T00:00:00Z",
    }
    assert server_module.list_features() == {"enabled": ["A", "B"], "total": 2, "fetched_at": "2026-11-12T00:00:00Z"}
    mock_client.api.assert_not_called()


def test_list_features_without_a_snapshot_says_none_are_known(mock_client):
    mock_client.feature_switches.return_value = {
        "has_feature_switches": False, "enabled": [], "total": 0, "fetched_at": None,
    }
    result = server_module.list_features()
    assert result["enabled"] == []
    assert "none are known" in result["note"]
    assert "vamscli" not in result["note"]  # Mandatory Rule 14: the agent is not told to run a CLI


def test_the_client_snapshot_matches_the_cli_features_list():
    """`VamsClient.feature_switches` reports what `vamscli features list` does: sorted, with count."""
    from vams_mcp.client import VamsClient

    fake = MagicMock()
    fake._profile.get_feature_switches_info.return_value = {
        "has_feature_switches": True, "enabled": ["Z", "A"], "count": 2, "fetched_at": "t",
    }
    assert VamsClient.feature_switches(fake) == {
        "has_feature_switches": True, "enabled": ["A", "Z"], "total": 2, "fetched_at": "t",
    }


# --- Writes -----------------------------------------------------------------


def test_move_file_payload(gated):
    server, client = gated
    server.move_file("db1", "a1", "/old.glb", "/new.glb")
    client.api.move_file.assert_called_once_with(
        "db1", "a1", {"sourcePath": "/old.glb", "destinationPath": "/new.glb"}
    )


def test_copy_file_within_the_asset_sends_no_destination_ids(gated):
    server, client = gated
    server.copy_file("db1", "a1", "/a.glb", "/b.glb")
    assert client.api.copy_file.call_args.args[2] == {"sourcePath": "/a.glb", "destinationPath": "/b.glb"}


def test_copy_file_to_another_asset_and_database(gated):
    server, client = gated
    server.copy_file("db1", "a1", "/a.glb", "/a.glb", destination_asset_id="a2", destination_database_id="db2")
    assert client.api.copy_file.call_args.args[2] == {
        "sourcePath": "/a.glb", "destinationPath": "/a.glb", "destinationAssetId": "a2",
        "destinationDatabaseId": "db2",
    }


def test_create_asset_link_payload(gated):
    server, client = gated
    server.create_asset_link("db1", "parent", "db2", "child", "parentChild", alias_id="second", tags=["t"])
    client.api.create_asset_link.assert_called_once_with({
        "fromAssetId": "parent", "fromAssetDatabaseId": "db1", "toAssetId": "child",
        "toAssetDatabaseId": "db2", "relationshipType": "parentChild", "tags": ["t"],
        "assetLinkAliasId": "second",
    })


def test_create_asset_link_without_alias_sends_none(gated):
    server, client = gated
    server.create_asset_link("db1", "a", "db1", "b", "related")
    payload = client.api.create_asset_link.call_args.args[0]
    assert "assetLinkAliasId" not in payload and payload["tags"] == []


@pytest.mark.parametrize("tool, method", [("create_tag", "create_tags"), ("update_tag", "update_tags")])
def test_tag_writes_send_one_flat_object(gated, tool, method):
    server, client = gated
    getattr(server, tool)("urgent", "Needs action", "priority")
    getattr(client.api, method).assert_called_once_with(
        {"tagName": "urgent", "description": "Needs action", "tagTypeName": "priority"}
    )
    getattr(server, tool)("urgent", "Needs action", "priority", database_id="db1")
    assert getattr(client.api, method).call_args.args[0]["databaseId"] == "db1"


@pytest.mark.parametrize(
    "tool, method", [("create_tag_type", "create_tag_types"), ("update_tag_type", "update_tag_types")]
)
def test_tag_type_writes_send_required_as_the_string_the_model_takes(gated, tool, method):
    server, client = gated
    getattr(server, tool)("priority", "How urgent", required=True)
    getattr(client.api, method).assert_called_once_with(
        {"tagTypeName": "priority", "description": "How urgent", "required": "True"}
    )
    getattr(server, tool)("priority", "How urgent", required=False, database_id="db1")
    assert getattr(client.api, method).call_args.args[0] == {
        "tagTypeName": "priority", "description": "How urgent", "required": "False", "databaseId": "db1",
    }


def test_create_tag_type_defaults_to_not_required(gated):
    server, client = gated
    server.create_tag_type("priority", "How urgent")
    assert client.api.create_tag_types.call_args.args[0]["required"] == "False"


# --- Destructive ------------------------------------------------------------


def test_archive_file_payload(gated):
    server, client = gated
    server.archive_file("db1", "a1", "/folder/", is_prefix=True)
    client.api.archive_file.assert_called_once_with("db1", "a1", {"filePath": "/folder/", "isPrefix": True})


def test_unarchive_file_payload(gated):
    server, client = gated
    server.unarchive_file("db1", "a1", "/a.glb")
    client.api.unarchive_file.assert_called_once_with("db1", "a1", {"filePath": "/a.glb"})


def test_delete_file_sends_the_required_confirmation(gated):
    """DeleteFileRequestModel defaults confirmPermanentDelete to False and the route refuses that."""
    server, client = gated
    server.delete_file("db1", "a1", "/a.glb")
    client.api.delete_file.assert_called_once_with(
        "db1", "a1", {"filePath": "/a.glb", "isPrefix": False, "confirmPermanentDelete": True}
    )


def test_delete_asset_link(gated):
    server, client = gated
    server.delete_asset_link("link-1")
    client.api.delete_asset_link.assert_called_once_with("link-1")


@pytest.mark.parametrize("tool, method", [("delete_tag", "delete_tag"), ("delete_tag_type", "delete_tag_type")])
def test_tag_deletes_forward_the_database_scope(gated, tool, method):
    server, client = gated
    getattr(server, tool)("name")
    assert getattr(client.api, method).call_args.kwargs == {"database_id": None}
    getattr(server, tool)("name", database_id="db1")
    assert getattr(client.api, method).call_args.kwargs == {"database_id": "db1"}


def test_an_api_error_comes_back_as_data(gated):
    server, client = gated
    client.api.delete_tag_type.side_effect = RuntimeError("Tag type is in use")
    result = server.delete_tag_type("priority")
    assert result == {"error": "Tag type is in use", "error_type": "RuntimeError"}


def test_the_readme_documents_the_new_tools():
    from pathlib import Path

    readme = (Path(server_module.__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for name in ("get_file_info", "export_asset", "list_features", "move_file", "copy_file", "create_asset_link",
                 "create_tag", "update_tag", "create_tag_type", "update_tag_type", "archive_file",
                 "unarchive_file", "delete_file", "delete_asset_link", "delete_tag", "delete_tag_type"):
        assert f"`{name}`" in readme, name


# --- One export answer stays bounded ----------------------------------------


@pytest.mark.parametrize(
    "asked, sent",
    [
        ((1000, 10000), (100, 1000)),  # the route's own maxima are lowered to the tool's caps
        ((101, 1001), (100, 1000)),
        ((100, 1000), (100, 1000)),
        ((0, -5), (1, 1)),  # the route refuses values below 1
    ],
)
def test_export_asset_caps_the_page_it_asks_for(mock_client, asked, sent):
    mock_client.api.export_asset.return_value = {"assets": []}
    server_module.export_asset("db1", "root", max_assets=asked[0], max_files=asked[1])
    params = mock_client.api.export_asset.call_args.args[2]
    assert (params["maxAssets"], params["maxFiles"]) == sent
    assert (server_module.EXPORT_MAX_ASSETS, server_module.EXPORT_MAX_FILES) == (100, 1000)


def test_export_asset_description_states_the_cap_and_the_token():
    doc = " ".join(server_module.export_asset.__doc__.split())
    assert "100 assets and 1000 files" in doc
    assert "starting_token" in doc and "NextToken" in doc


# --- A tag type copied from list_tags ----------------------------------------
#
# list_tags returns the tag type of a REQUIRED type as "<name> [R]". The write routes reject a name with
# brackets (400), so the tools drop the marker, as the CLI's `tag update` does.


@pytest.mark.parametrize("tool, method", [("create_tag", "create_tags"), ("update_tag", "update_tags")])
def test_tag_writes_drop_the_required_marker_list_tags_adds(gated, tool, method):
    server, client = gated
    getattr(server, tool)("urgent", "Needs action", "priority [R]")
    assert getattr(client.api, method).call_args.args[0]["tagTypeName"] == "priority"


@pytest.mark.parametrize(
    "tool, method", [("create_tag_type", "create_tag_types"), ("update_tag_type", "update_tag_types")]
)
def test_tag_type_writes_drop_the_required_marker(gated, tool, method):
    server, client = gated
    getattr(server, tool)("priority [R]", "How urgent", required=True)
    assert getattr(client.api, method).call_args.args[0]["tagTypeName"] == "priority"


def test_delete_tag_type_drops_the_required_marker(gated):
    server, client = gated
    server.delete_tag_type("priority [R]", database_id="db1")
    client.api.delete_tag_type.assert_called_once_with("priority", database_id="db1")


def test_a_tag_type_name_without_the_marker_is_sent_unchanged(gated):
    server, client = gated
    server.update_tag("urgent", "Needs action", "Priority R")
    assert client.api.update_tags.call_args.args[0]["tagTypeName"] == "Priority R"


def test_the_marker_matches_the_one_the_cli_strips():
    """The CLI's tag update removes the same literal from the type it reads back from the tag list."""
    from pathlib import Path

    import vamscli

    cli_tag = (Path(vamscli.__file__).parent / "commands" / "tag.py").read_text(encoding="utf-8")
    assert f".replace('{server_module.REQUIRED_TAG_TYPE_SUFFIX}', '')" in cli_tag


def test_move_and_unarchive_file_descriptions_say_where_the_archived_entry_is(gated):
    server, _ = gated
    assert "the source stays as an archived entry" in " ".join(server.move_file.__doc__.split())
    assert "list_asset_files(include_archived)" in server.unarchive_file.__doc__
