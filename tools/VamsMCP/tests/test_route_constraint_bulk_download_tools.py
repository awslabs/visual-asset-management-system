"""Tests for the full-route listing, constraint-vocabulary, and bulk presigned-URL read tools.

All three wrap an APIClient method that is a single unpaginated call returning one dict, so each
test asserts the exact arguments that reach the client (a dropped optional pin is a silent
server-default) and that the response is handed back unaltered. The source-layout and README checks
mirror those in test_server_tools.py / test_gated_tools.py for the read section.
"""

import ast
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vams_mcp import server

SOURCE_PATH = Path(server.__file__)
SOURCE_TEXT = SOURCE_PATH.read_text(encoding="utf-8")
README_TEXT = (SOURCE_PATH.resolve().parents[1] / "README.md").read_text(encoding="utf-8")

NEW_READ_TOOLS = ("list_api_routes", "list_constraint_permission_objects", "generate_download_urls_bulk")


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(server, "CLIENT", client)
    return client


def _docstring_of(name):
    for node in ast.walk(ast.parse(SOURCE_TEXT)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return " ".join((ast.get_docstring(node) or "").split())
    raise AssertionError(f"no function named {name!r} in {SOURCE_PATH.name}")


def _def_line_of(name):
    for i, line in enumerate(SOURCE_TEXT.splitlines(), start=1):
        if re.match(rf"^def {re.escape(name)}\(", line):
            return i
    raise AssertionError(f"no top-level def {name!r} in {SOURCE_PATH.name}")


def _line_of(prefix):
    for i, line in enumerate(SOURCE_TEXT.splitlines(), start=1):
        if line.startswith(prefix):
            return i
    raise AssertionError(f"{prefix!r} not found in {SOURCE_PATH.name}")


# --- Route listing ------------------------------------------------------------


def test_list_api_routes_returns_the_full_route_list_unaltered(mock_client):
    routes = {"routes": [{"path": "/auth/constraints", "methods": ["GET", "POST"],
                          "category": "auth", "unauthenticated": False}]}
    mock_client.api.list_api_routes.return_value = routes
    assert server.list_api_routes() == routes
    mock_client.api.list_api_routes.assert_called_once_with()


def test_list_api_routes_is_distinct_from_the_allowed_listing(mock_client):
    """Two APIClient methods share a prefix; the full listing must not call the allowed one."""
    mock_client.api.list_api_routes.return_value = {"routes": []}
    server.list_api_routes()
    assert not mock_client.api.list_allowed_api_routes.called
    mock_client.api.list_allowed_api_routes.return_value = {"routes": [], "userId": "u1"}
    server.list_allowed_api_routes()
    mock_client.api.list_api_routes.assert_called_once()
    mock_client.api.list_allowed_api_routes.assert_called_once()


# --- Constraint vocabulary ----------------------------------------------------


def test_list_constraint_permission_objects_returns_the_vocabulary_unaltered(mock_client):
    vocabulary = {
        "objectTypes": [{"label": "Asset", "value": "asset",
                         "fields": [{"label": "Asset ID", "value": "assetId"}]}],
        "operators": [{"label": "Equals", "value": "equals"}],
        "permissions": [{"label": "GET", "value": "GET"}],
        "permissionTypes": [{"label": "Allow", "value": "allow"}],
    }
    mock_client.api.list_constraint_permission_objects.return_value = vocabulary
    assert server.list_constraint_permission_objects() == vocabulary
    mock_client.api.list_constraint_permission_objects.assert_called_once_with()


@pytest.mark.parametrize("tool", ["list_api_routes", "list_constraint_permission_objects"])
def test_the_vocabulary_reads_report_a_failure_as_data(mock_client, tool):
    getattr(mock_client.api, tool).side_effect = RuntimeError("boom")
    result = getattr(server, tool)()
    assert result == {"error": "boom", "error_type": "RuntimeError"}


# --- Bulk presigned URLs --------------------------------------------------------


def test_generate_download_urls_bulk_forwards_keys_and_the_asset_version_pin(mock_client):
    mock_client.api.download_asset_files_bulk.return_value = {"files": []}
    keys = ["/model.glb", {"key": "/textures/a.png", "versionId": "v2"}]

    server.generate_download_urls_bulk("db1", "a1", keys, asset_version_id="av1")

    mock_client.api.download_asset_files_bulk.assert_called_once_with(
        "db1", "a1", keys, asset_version_id="av1", asset_version_alias=None
    )


def test_generate_download_urls_bulk_forwards_the_alias_pin(mock_client):
    mock_client.api.download_asset_files_bulk.return_value = {"files": []}
    server.generate_download_urls_bulk("db1", "a1", ["/model.glb"], asset_version_alias="latest")
    kwargs = mock_client.api.download_asset_files_bulk.call_args.kwargs
    assert kwargs == {"asset_version_id": None, "asset_version_alias": "latest"}


def test_generate_download_urls_bulk_sends_no_pin_the_caller_did_not_set(mock_client):
    mock_client.api.download_asset_files_bulk.return_value = {"files": []}
    server.generate_download_urls_bulk("db1", "a1", ["/model.glb"])
    args, kwargs = mock_client.api.download_asset_files_bulk.call_args
    assert args == ("db1", "a1", ["/model.glb"])
    assert kwargs == {"asset_version_id": None, "asset_version_alias": None}


def test_generate_download_urls_bulk_returns_the_per_file_entries_unaltered(mock_client):
    """Skipped files arrive as success=false entries beside the signed ones; nothing is filtered or
    re-shaped, so the agent sees exactly what the handler reported."""
    response = {
        "downloadUrl": "https://example/first",
        "expiresIn": 86400,
        "downloadType": "assetFile",
        "message": "Generated 1 of 2 download URLs. Warning: 1 file path(s) do not exist or are not "
                   "downloadable and were skipped.",
        "files": [
            {"key": "/model.glb", "downloadUrl": "https://example/first", "versionId": "v1",
             "success": True, "error": None},
            {"key": "/missing.bin", "downloadUrl": None, "versionId": None,
             "success": False, "error": "File not found"},
        ],
    }
    mock_client.api.download_asset_files_bulk.return_value = response
    assert server.generate_download_urls_bulk("db1", "a1", ["/model.glb", "/missing.bin"]) == response


def test_generate_download_urls_bulk_is_the_bulk_method_not_the_single_one(mock_client):
    mock_client.api.download_asset_files_bulk.return_value = {"files": []}
    server.generate_download_urls_bulk("db1", "a1", ["/model.glb"])
    assert not mock_client.api.download_asset_file.called


# --- Docstring contracts --------------------------------------------------------


@pytest.mark.parametrize(
    "fragment",
    # The bulk tool sits in the unconditional read gate beside generate_download_url and hands out
    # N bearer credentials per call, so its docstring must carry the same disclosure, and must tell
    # the agent that a successful call can still have skipped files.
    ["bearer credential", "presignedUrlTimeoutSeconds", "24 hours", "transcript",
     "generate_download_url", "success", "SKIPPED"],
)
def test_generate_download_urls_bulk_docstring_names_the_exposure_and_partial_success(fragment):
    assert fragment in _docstring_of("generate_download_urls_bulk")


def test_generate_download_urls_bulk_docstring_states_the_backend_key_cap():
    """The number in the docstring is the backend's cap, mirrored in the CLI constant; a drift in
    either makes the agent split too early or hit a 400 it was told it would not."""
    from vamscli.constants import MAX_DOWNLOAD_KEYS_PER_REQUEST

    assert f"up to {MAX_DOWNLOAD_KEYS_PER_REQUEST} entries" in _docstring_of("generate_download_urls_bulk")


def test_list_api_routes_docstring_points_at_the_allowed_listing_for_scope():
    docstring = _docstring_of("list_api_routes")
    assert "list_allowed_api_routes" in docstring
    assert "regardless of what the current user may call" in docstring


def test_list_constraint_permission_objects_docstring_says_which_half_of_the_pair_to_send():
    docstring = _docstring_of("list_constraint_permission_objects")
    for fragment in ("objectTypes", "fields", "operators", "permissions", "permissionTypes",
                     "Send the `value`"):
        assert fragment in docstring


# --- Registration, placement, and README ----------------------------------------


@pytest.mark.asyncio
async def test_the_three_reads_are_registered_with_both_gates_off():
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for name in NEW_READ_TOOLS:
        assert name in tools, f"{name} is not registered at the default gate settings"
    # mcp 1.x names the field inputSchema; mcp 2.x renamed it input_schema.
    bulk = tools["generate_download_urls_bulk"]
    schema = getattr(bulk, "input_schema", None) or getattr(bulk, "inputSchema")
    assert set(schema["required"]) == {"database_id", "asset_id", "file_keys"}
    # The key list accepts both a path string and a {key, versionId} object.
    file_keys = schema["properties"]["file_keys"]
    assert file_keys["type"] == "array"
    assert "anyOf" in file_keys["items"], json.dumps(file_keys)
    kinds = {alt.get("type") for alt in file_keys["items"]["anyOf"]}
    assert kinds == {"string", "object"}


@pytest.mark.parametrize("name", NEW_READ_TOOLS)
def test_the_three_reads_sit_in_the_read_section(name):
    assert _def_line_of(name) < _line_of("if CONFIG.enable_writes:"), (
        f"{name} is a read tool but is defined inside a gated block"
    )


def test_the_bulk_tool_sits_beside_the_single_url_tool():
    single = _def_line_of("generate_download_url")
    bulk = _def_line_of("generate_download_urls_bulk")
    between = SOURCE_TEXT.splitlines()[single:bulk - 1]
    other_defs = [line for line in between if re.match(r"^def (?!generate_download_urls_bulk)\w+\(", line)]
    assert not other_defs, f"tools defined between the two download tools: {other_defs}"


@pytest.mark.parametrize("name", NEW_READ_TOOLS)
def test_the_readme_tool_list_names_each_read(name):
    assert README_TEXT, "README.md was not read, so this assertion would be vacuous"
    assert f"`{name}`" in README_TEXT, f"{name} is missing from the README tool list"


def _readme_autoapprove_names():
    match = re.search(r'"autoApprove":\s*\[(.*?)\]', README_TEXT, re.DOTALL)
    assert match, "the README sample MCP host config has no autoApprove array"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


@pytest.mark.parametrize("name", ["list_api_routes", "list_constraint_permission_objects"])
def test_the_static_vocabulary_reads_are_auto_approvable(name):
    assert name in _readme_autoapprove_names()


@pytest.mark.parametrize("name", ["generate_download_urls_bulk", "generate_download_url"])
def test_the_presigned_url_tools_are_not_auto_approved(name):
    """Both hand out bearer credentials; the single-URL tool is the control that the array was
    actually parsed rather than matched vacuously."""
    names = _readme_autoapprove_names()
    assert names, "no autoApprove names were parsed"
    assert name not in names


def test_the_readme_security_note_names_the_bulk_tool_with_the_single_one():
    bullets = [p for p in README_TEXT.split("\n-   ") if "bearer" in p and "presigned Amazon S3 URL" in p]
    assert len(bullets) == 1, f"expected exactly one presigned-URL security bullet, found {len(bullets)}"
    assert "`generate_download_urls_bulk`" in bullets[0]
    assert "`generate_download_url`" in bullets[0]
