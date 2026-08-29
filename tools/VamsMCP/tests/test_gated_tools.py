"""The write and destructive tool BODIES, executed.

S6-TOOLS-006. The mutating half of this server — the half that can damage a deployment — was covered
only by regex/AST assertions over its own source. The tools are defined inside
`if CONFIG.enable_writes:` / `if CONFIG.enable_destructive:` blocks and `CONFIG = Config.from_env()`
runs once at import, so at collection time those 28 functions did not exist: `_docstring_of()` and
`_source_of()` in `test_server_tools.py` parse the file with `ast` because there is nothing to call.
Payload key names, defaults, and the confirmation flags were therefore unverified. A backend rename of
`relativeKey`, or a `metadataValueType` that became a required enum, would keep every test green and
surface as a 400 against a live deployment.

The other half of the gap was directional. `test_write_tools_gated_off_by_default` and
`test_destructive_tools_gated_off_by_default` assert only ABSENCE, so a write tool accidentally placed
under `if CONFIG.enable_destructive:` — or a misspelled env var — left the tool unreachable with
writes on, and nothing failed. `test_every_gated_tool_is_registered_with_both_gates_on` below is the
positive control that catches misplacement in that direction.

The reload is the only way in: the gate decision happens at import, before any fixture runs. The
fixture manages `os.environ` directly rather than through `monkeypatch`, because monkeypatch's undo
runs AFTER the fixture body finishes and the restoring reload has to see the cleared environment.
`tests/conftest.py` clears those variables for the session, and its autouse fixture is the backstop
against a leak from here.
"""

import importlib
import os
from unittest.mock import MagicMock

import pytest

from vams_mcp import server as server_module

# The contract, stated here rather than derived from the source: a test that reads the same file it is
# checking cannot notice a tool that moved between the two blocks.
WRITE_TOOLS = (
    "create_database",
    "create_asset",
    "update_asset",
    "set_asset_metadata",
    "create_folder",
    "create_asset_version",
    "create_pipeline",
    "update_pipeline",
    "create_pipeline_template",
    "update_pipeline_template",
    "set_pipeline_template_tag_schema",
    "create_workflow",
    "update_workflow",
    "set_workflow_trigger",
    "execute_workflow",
    "rerun_execution",
    "abort_execution",
)

DESTRUCTIVE_TOOLS = (
    "archive_asset",
    "unarchive_asset",
    "delete_asset",
    "delete_database",
    "archive_pipeline",
    "unarchive_pipeline",
    "archive_workflow",
    "unarchive_workflow",
    "delete_pipeline_template",
    "delete_workflow_trigger",
    "permanent_delete_execution",
)

GATE_VARS = ("VAMS_ENABLE_WRITES", "VAMS_ENABLE_DESTRUCTIVE")


@pytest.fixture
def enabled_server():
    """Reimport `vams_mcp.server` with both gates on, then restore the default-gate module."""
    for name in GATE_VARS:
        os.environ[name] = "true"
    importlib.reload(server_module)
    try:
        # Control for every test in this file: if the reload did not take, the tools below would be
        # AttributeErrors and the reader would be left guessing why.
        assert server_module.CONFIG.enable_writes is True
        assert server_module.CONFIG.enable_destructive is True
        yield server_module
    finally:
        for name in GATE_VARS:
            os.environ.pop(name, None)
        importlib.reload(server_module)


@pytest.fixture
def gated(enabled_server, monkeypatch):
    """The reloaded server with a mocked client. Yields (module, client)."""
    client = MagicMock()
    # unwrap_message is a staticmethod on the real client and several tools route through it, so use
    # the real implementation rather than a mock that returns another mock.
    client.unwrap_message = enabled_server.VamsClient.unwrap_message
    client.config = enabled_server.CONFIG
    monkeypatch.setattr(enabled_server, "CLIENT", client)
    return enabled_server, client


# --- Registration -----------------------------------------------------------


@pytest.mark.asyncio
async def test_every_gated_tool_is_registered_with_both_gates_on(enabled_server):
    """The direction the gate-off tests cannot see.

    A write tool that drifted one block down into the destructive section, or a `def` that landed
    after the `if __name__` entrypoint, keeps all four registration tests in test_server_tools.py
    green while making the tool unreachable with writes on.
    """
    names = {tool.name for tool in await enabled_server.mcp.list_tools()}
    missing = sorted(set(WRITE_TOOLS + DESTRUCTIVE_TOOLS) - names)
    assert not missing, f"tools are not registered with both gates on: {missing}"


@pytest.mark.asyncio
async def test_the_read_tools_survive_enabling_the_gates(enabled_server):
    """Control: the reload must produce a complete server, not just the gated half."""
    names = {tool.name for tool in await enabled_server.mcp.list_tools()}
    for expected in ("list_databases", "get_asset", "search_assets", "list_executions"):
        assert expected in names


def test_the_readme_documents_every_gated_tool(enabled_server):
    """The README tool list is the only place the parameter set is documented outside the docstring,
    so a tool absent from it is one an operator deciding what to enable never sees."""
    from pathlib import Path

    import vams_mcp

    readme = (Path(vams_mcp.__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert readme, "README.md was not read, so this assertion would be vacuous"
    missing = [name for name in WRITE_TOOLS + DESTRUCTIVE_TOOLS if f"`{name}`" not in readme]
    assert not missing, f"gated tools missing from the README tool list: {missing}"


# --- Write tool payloads ----------------------------------------------------


def test_create_asset_payload_keys(gated):
    server, client = gated
    server.create_asset("db1", "My Asset", "A description", is_distributable=True, tags=["t1"])
    payload = client.api.create_asset.call_args.args[0]
    assert payload == {
        "databaseId": "db1",
        "assetName": "My Asset",
        "description": "A description",
        "isDistributable": True,
        "tags": ["t1"],
    }


def test_create_asset_defaults_to_non_distributable_and_no_tags(gated):
    server, client = gated
    server.create_asset("db1", "My Asset", "A description")
    payload = client.api.create_asset.call_args.args[0]
    assert payload["isDistributable"] is False
    assert payload["tags"] == []


def test_create_folder_normalizes_a_missing_trailing_slash(gated):
    """The backend requires `relativeKey` to end in a slash; a folder path without one is a 400."""
    server, client = gated
    server.create_folder("db1", "a1", "sub/dir")
    assert client.api.create_folder.call_args.args[2] == {"relativeKey": "sub/dir/"}


def test_create_folder_leaves_an_existing_trailing_slash_alone(gated):
    server, client = gated
    server.create_folder("db1", "a1", "sub/dir/")
    assert client.api.create_folder.call_args.args[2] == {"relativeKey": "sub/dir/"}


def test_set_asset_metadata_builds_the_item_list(gated):
    server, client = gated
    server.set_asset_metadata("db1", "a1", {"k": 1, "s": "v"})
    args = client.api.update_asset_metadata_v2.call_args
    assert args.args[0] == "db1"
    assert args.args[1] == "a1"
    assert args.args[2] == [
        {"metadataKey": "k", "metadataValue": "1", "metadataValueType": "string"},
        {"metadataKey": "s", "metadataValue": "v", "metadataValueType": "string"},
    ]
    # An upsert, not a replace: `replace` would delete every key the caller did not list.
    assert args.kwargs["update_type"] == "update"


def test_create_asset_version_uses_the_latest_files(gated):
    server, client = gated
    server.create_asset_version("db1", "a1", "a comment")
    assert client.api.create_asset_version.call_args.args[2] == {
        "useLatestFiles": True,
        "comment": "a comment",
    }


def test_create_asset_version_omits_an_absent_alias(gated):
    """An empty `versionAlias` is a meaningful value (it clears the alias), so it must not be sent
    just because the parameter defaulted."""
    server, client = gated
    server.create_asset_version("db1", "a1", "a comment")
    assert "versionAlias" not in client.api.create_asset_version.call_args.args[2]

    server.create_asset_version("db1", "a1", "a comment", version_alias="v1.0")
    assert client.api.create_asset_version.call_args.args[2]["versionAlias"] == "v1.0"


def test_update_asset_forwards_the_updates_unchanged(gated):
    server, client = gated
    server.update_asset("db1", "a1", {"description": "new"})
    assert client.api.update_asset.call_args.args == ("db1", "a1", {"description": "new"})


def test_create_database_payload_keys(gated):
    server, client = gated
    server.create_database("db1", "desc", "bucket-1")
    assert client.api.create_database.call_args.args[0] == {
        "databaseId": "db1",
        "description": "desc",
        "defaultBucketId": "bucket-1",
    }


# --- abort_execution: write-tier, but an irreversible group fan-out ---------


def test_abort_execution_aborts_a_single_run(gated):
    server, client = gated
    client.api.abort_execution.return_value = {"message": "Execution aborted"}
    result = server.abort_execution("e1")
    assert result == {"message": "Execution aborted"}
    client.api.abort_execution.assert_called_once_with("e1", group_id=None)


def test_abort_execution_forwards_the_group_id_by_keyword(gated):
    """The group form fans out across every active run in the group. `APIClient.abort_execution`
    takes `group_id` as a keyword; passing it positionally would land in the wrong parameter."""
    server, client = gated
    client.api.abort_execution.return_value = {"message": {"groupId": "grp-7", "results": []}}
    server.abort_execution("e1", group_id="grp-7")
    client.api.abort_execution.assert_called_once_with("e1", group_id="grp-7")


def test_rerun_execution_forwards_the_group_id_by_keyword(gated):
    server, client = gated
    client.api.rerun_execution.return_value = {"message": {"executionId": "e2"}}
    server.rerun_execution("e1", execution_group_id="grp-1")
    assert client.api.rerun_execution.call_args.kwargs["execution_group_id"] == "grp-1"


# --- Destructive tool payloads ---------------------------------------------


def test_delete_asset_sends_the_required_confirmation_field(gated):
    """`confirmPermanentDelete` is a REQUIRED-TRUE field of the delete request, not a bypassable
    guard: `DeleteAssetRequestModel` declares an `always=True` validator that rejects any other
    value, and `assetService` rejects a false one, so `confirm=True` is the only way to perform the
    operation at all. The controls on this tool are the destructive gate, its name, and its docstring.
    """
    server, client = gated
    server.delete_asset("db1", "a1", reason="cleanup")
    args = client.api.delete_asset_permanent.call_args
    assert args.args == ("db1", "a1")
    assert args.kwargs == {"reason": "cleanup", "confirm": True}


def test_archive_asset_passes_none_for_an_empty_reason(gated):
    """An empty string is not "no reason": the APIClient omits the field only for None."""
    server, client = gated
    server.archive_asset("db1", "a1")
    assert client.api.archive_asset.call_args.kwargs["reason"] is None


def test_unarchive_asset_forwards_unarchive_files(gated):
    server, client = gated
    server.unarchive_asset("db1", "a1", unarchive_files=True)
    kwargs = client.api.unarchive_asset.call_args.kwargs
    assert kwargs["unarchive_files"] is True
    assert kwargs["reason"] is None


def test_unarchive_asset_leaves_files_archived_by_default(gated):
    server, client = gated
    server.unarchive_asset("db1", "a1")
    assert client.api.unarchive_asset.call_args.kwargs["unarchive_files"] is False


@pytest.mark.parametrize(
    "tool,api_method",
    [("unarchive_pipeline", "update_pipeline"), ("unarchive_workflow", "update_workflow")],
)
def test_unarchive_re_enables_the_row(gated, tool, api_method):
    """Archiving also DISABLES, so clearing only `archived` restores a row that lists normally and
    cannot be executed."""
    server, client = gated
    getattr(client.api, api_method).return_value = {"message": {}}
    getattr(server, tool)("db1", "x1")
    assert getattr(client.api, api_method).call_args.args[2] == {"archived": False, "enabled": True}


@pytest.mark.parametrize(
    "tool,api_method",
    [("unarchive_pipeline", "update_pipeline"), ("unarchive_workflow", "update_workflow")],
)
def test_unarchive_keep_disabled_restores_without_enabling(gated, tool, api_method):
    server, client = gated
    getattr(client.api, api_method).return_value = {"message": {}}
    getattr(server, tool)("db1", "x1", keep_disabled=True)
    assert getattr(client.api, api_method).call_args.args[2] == {"archived": False}


def test_permanent_delete_execution_unwraps_the_envelope(gated):
    server, client = gated
    client.api.permanent_delete_execution.return_value = {"message": {"deleted": True}}
    assert server.permanent_delete_execution("e1") == {"deleted": True}


def test_delete_database_forwards_the_id(gated):
    server, client = gated
    server.delete_database("db1")
    client.api.delete_database.assert_called_once_with("db1")


# --- Error contract ---------------------------------------------------------


def test_a_gated_tool_returns_errors_as_data(gated):
    """`@tool_result` must wrap the gated tools too: a raised exception derails an agent session."""
    server, client = gated
    client.api.create_asset.side_effect = RuntimeError("boom")
    result = server.create_asset("db1", "name", "description")
    assert result == {"error": "boom", "error_type": "RuntimeError"}


# --- Restoration ------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_module_is_restored_to_the_default_gates_afterwards():
    """Ordered pair with the fixture above: without the restoring reload, every later test in the
    session would see a server with both gates on — including the gate-off assertions that are the
    security core of test_server_tools.py.

    This test takes no `enabled_server`, so it observes whatever state the previous test left.
    """
    assert server_module.CONFIG.enable_writes is False
    assert server_module.CONFIG.enable_destructive is False
    names = {tool.name for tool in await server_module.mcp.list_tools()}
    for gated_name in ("create_asset", "delete_asset", "abort_execution"):
        assert gated_name not in names
