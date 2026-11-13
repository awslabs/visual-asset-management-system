"""The agent-facing surface of the server (Mandatory Rules 11-14 of tools/VamsMCP/CLAUDE.md).

What an MCP client shows the model before any call: the server `instructions`, each tool's
description, title and annotations, and each typed tool's `outputSchema`. Plus the degraded start:
without a sign-in the server still lists its tools, `vams_status` answers, and every other tool
returns a NotAuthenticated error that asks the agent to have the USER sign in, never to run a CLI.

Every check runs over the server as a client sees it (`mcp.list_tools()` with both gates on and the
EDH add-on's switch present), so a tool registered anywhere, add-on tools included, is covered.
"""

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vamscli import addon_constants
from vamscli.utils.exceptions import AuthenticationError, TokenExpiredError

from vams_mcp import annotations as tool_annotations
from vams_mcp import server
from vams_mcp.addon_tools import edh
from vams_mcp.config import Config

ALL_SWITCHES = ["AUTHPROVIDER_COGNITO", addon_constants.FEATURE_EDH_WORKSTATIONS]

# --- Budgets (Mandatory Rule 11) --------------------------------------------------------------------

MAX_DESCRIPTION_CHARS = 1200
# 30,000 until search_nlp (natural-language search) joined the core tools.
MAX_CORE_TOTAL_CHARS = 31000
# The only tools allowed past MAX_DESCRIPTION_CHARS, each with its own ceiling and the reason. Keep
# this list short: a long description is read on every session by every connected agent.
DESCRIPTION_EXCEPTIONS = {
    # The one listing of a workstation's whole state: its lifecycle, start-failure, EDH-failure,
    # lease, reservation and image fields are what an agent explains a failed launch or start from.
    "list_edh_workstations": 1800,
    # The launch tool: its refusals (architecture, recipe types, reservations) each name the field
    # to change, and it starts billed GPU compute.
    "create_edh_workstation": 1800,
}

# Tools that start, resume, extend, stop or terminate billed compute (Rule 4 / Rule 12).
COMPUTE_TOOLS = {
    "execute_workflow", "rerun_execution", "abort_execution",
    "create_edh_workstation", "renew_edh_workstation", "start_edh_workstation", "stop_edh_workstation",
    "stop_edh_admin_instance", "submit_edh_container_job", "start_edh_ami_recipe_build",
    "deactivate_edh_admin_instance", "kill_edh_container_job",
}

# The typed read tools (Rule 13): each publishes an outputSchema with these fields.
TYPED_TOOLS = {
    "vams_status": "authenticated",
    "list_databases": "Items", "get_database": "databaseId",
    "list_assets": "Items", "get_asset": "assetId",
    "list_asset_files": "Items", "list_asset_versions": "Items", "get_asset_version": "assetVersionId",
    "get_asset_metadata": "Items", "get_database_metadata": "Items",
    "search_assets": "total_is_lower_bound", "search_files": "total_is_lower_bound", "search_nlp": "relation",
    "list_executions": "filterStartDate", "list_workflow_executions": "filterStartDate",
    "get_execution_details": "truncatedCollections", "get_execution_results": "resultContent",
    "generate_download_url": "downloadUrl", "generate_download_urls_bulk": "files",
    "get_file_info": "relativePath", "export_asset": "totalAssetsInTree", "list_features": "fetched_at",
}


def _tiers_by_name():
    """Each tool's gate tier, read from the source: server.py's blocks and edh.register's."""
    tiers = {}
    source = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    for statement in source.body:
        if isinstance(statement, ast.FunctionDef) and statement.decorator_list:
            tiers[statement.name] = "read"
        elif isinstance(statement, ast.If):
            test = ast.unparse(statement.test)
            tier = {"CONFIG.enable_writes": "write", "CONFIG.enable_destructive": "destructive"}.get(test)
            for inner in statement.body if tier else ():
                if isinstance(inner, ast.FunctionDef) and inner.decorator_list:
                    tiers[inner.name] = tier
    register = next(n for n in ast.parse(Path(edh.__file__).read_text(encoding="utf-8")).body
                    if isinstance(n, ast.FunctionDef) and n.name == "register")
    for statement in register.body:
        if isinstance(statement, ast.FunctionDef):
            tiers[statement.name] = "read"
        elif isinstance(statement, ast.If):
            tier = {"config.enable_writes": "write", "config.enable_destructive": "destructive"}[
                ast.unparse(statement.test)]
            for inner in statement.body:
                if isinstance(inner, ast.FunctionDef):
                    tiers[inner.name] = tier
    return tiers


@pytest.fixture
async def every_tool(server_with_switches):
    """Every tool a client lists with both gates on and the EDH add-on deployed, by name."""
    with server_with_switches(ALL_SWITCHES, enable_writes=True, enable_destructive=True) as reloaded:
        yield {tool.name: tool for tool in await reloaded.mcp.list_tools()}


def _wire(tool):
    """The tool as the client receives it (camelCase on the wire on both mcp majors; mcp 2.x uses
    snake_case attribute names in Python)."""
    return tool.model_dump(by_alias=True, exclude_none=True)


def _core_names():
    return {name for name, tier in _tiers_by_name().items() if name in vars(server)}


# --- Server instructions ----------------------------------------------------------------------------


def test_the_server_sends_its_instructions_within_the_budget():
    text = server.SERVER_INSTRUCTIONS
    assert 500 < len(text) <= server.SERVER_INSTRUCTIONS_MAX_CHARS <= 2000
    assert server.mcp.instructions == text


@pytest.mark.parametrize("fragment", [
    "databaseId + assetId", "search_assets", "search_nlp", "list_databases", "get_asset", "list_asset_files",
    "generate_download_url", "truncated=true", "NextToken", "NotAuthenticated", "vamscli", "source code",
    "say so plainly", "vams_status",
])
def test_the_instructions_cover_the_model_the_agent_needs(fragment):
    assert fragment in " ".join(server.SERVER_INSTRUCTIONS.split())


def test_every_tool_the_instructions_name_exists():
    """A renamed tool would leave the instructions sending the agent to a call that fails."""
    named = {word.strip("(),.:;`") for word in server.SERVER_INSTRUCTIONS.replace("->", " ").split()}
    tools = set(_tiers_by_name())
    prefixes = ("list_", "get_", "search_", "generate_", "execute_", "rerun_", "abort_", "vams_")
    referenced = {word for word in named if word.startswith(prefixes)}
    assert referenced, "no tool name found, so this would be vacuous"
    assert sorted(referenced - tools) == []


# --- Descriptions -----------------------------------------------------------------------------------


async def test_every_description_stays_within_its_budget(every_tool):
    assert len(every_tool) > 130, "the full tool set was not registered, so this would be vacuous"
    over = {
        name: len(tool.description or "")
        for name, tool in every_tool.items()
        if len(tool.description or "") > DESCRIPTION_EXCEPTIONS.get(name, MAX_DESCRIPTION_CHARS)
    }
    assert over == {}


async def test_the_exceptions_are_still_needed_and_still_exist(every_tool):
    """An exception whose tool shrank under the budget, or no longer exists, is dropped from the list."""
    for name in DESCRIPTION_EXCEPTIONS:
        assert len(every_tool[name].description) > MAX_DESCRIPTION_CHARS, name


async def test_the_core_descriptions_stay_within_the_total_budget(every_tool):
    core = _core_names()
    assert len(core) > 90
    total = sum(len(every_tool[name].description or "") for name in core)
    assert total <= MAX_CORE_TOTAL_CHARS, total


async def test_every_core_description_opens_with_when_to_use_the_tool(every_tool):
    """Line 1 is addressed to the agent: when to reach for the tool, not how the handler works."""
    wrong = [name for name in _core_names()
             if not every_tool[name].description.startswith(("Use ", "Check "))]
    assert wrong == []


# --- Titles and annotations (Rule 12) ---------------------------------------------------------------


async def test_every_tool_has_a_title(every_tool):
    assert [name for name, tool in every_tool.items() if not (_wire(tool).get("title") or "").strip()] == []


async def test_every_annotation_matches_the_gate_block_it_is_registered_in(every_tool):
    tiers = _tiers_by_name()
    assert set(every_tool) <= set(tiers), sorted(set(every_tool) - set(tiers))
    wrong = []
    for name, tool in every_tool.items():
        hints = _wire(tool).get("annotations") or {}
        tier = tiers[name]
        expected = {
            "read": dict(readOnlyHint=True, destructiveHint=False),
            "write": dict(readOnlyHint=False, destructiveHint=False),
            "destructive": dict(readOnlyHint=False, destructiveHint=True),
        }[tier]
        expected["openWorldHint"] = name in COMPUTE_TOOLS
        actual = {key: hints.get(key) for key in expected}
        if actual != expected:
            wrong.append((name, tier, actual))
    assert wrong == []


def test_the_compute_tools_are_the_ones_the_steering_names():
    """Every compute tool exists, and the annotation constants agree with the tier table."""
    assert COMPUTE_TOOLS <= set(_tiers_by_name())
    def open_world(constant):
        return constant.model_dump(by_alias=True)["openWorldHint"]

    assert open_world(tool_annotations.COMPUTE) and open_world(tool_annotations.DESTRUCTIVE_COMPUTE)
    for name in ("READ", "WRITE", "DESTRUCTIVE"):
        assert open_world(getattr(tool_annotations, name)) is False, name


# --- Typed results (Rule 13) ------------------------------------------------------------------------


async def test_the_typed_tools_publish_an_output_schema(every_tool):
    for name, field in TYPED_TOOLS.items():
        schema = _wire(every_tool[name]).get("outputSchema") or {}
        assert field in schema.get("properties", {}), name
        # Error results go through the same model (Rule 5).
        assert "error" in schema["properties"], name


async def _call(name, arguments):
    """(text JSON, structured content) of a call, on either mcp major."""
    result = await server.mcp.call_tool(name, arguments)
    if isinstance(result, tuple):
        content, structured = result
    else:
        content, structured = result.content, result.structured_content
    return json.loads(content[0].text), structured


@pytest.mark.parametrize("returned", [
    {"Items": [{"databaseId": "d", "unknownField": [1, 2]}], "count": 1, "pages": 1, "truncated": True,
     "NextToken": "tok", "note": "n", "fieldTheApiAddsLater": {"x": None}},
    {"Items": [], "count": 0, "pages": 1},
], ids=["full", "empty"])
async def test_a_typed_result_is_the_returned_dict_in_both_channels(monkeypatch, returned):
    client = MagicMock()
    client.paginate.return_value = dict(returned)
    monkeypatch.setattr(server, "CLIENT", client)
    text, structured = await _call("list_databases", {})
    assert text == returned
    assert structured == returned


async def test_an_error_result_passes_through_a_typed_tool(monkeypatch):
    client = MagicMock()
    client.api.get_asset.side_effect = RuntimeError("boom")
    monkeypatch.setattr(server, "CLIENT", client)
    text, structured = await _call("get_asset", {"database_id": "d", "asset_id": "a"})
    assert text == structured == {"error": "boom", "error_type": "RuntimeError"}


# --- Degraded start and NotAuthenticated (Rule 14) --------------------------------------------------


@pytest.fixture
def signed_out(monkeypatch):
    """No client, and a profile that cannot be built, so the retry in _current_client fails too."""
    monkeypatch.setattr(server, "CLIENT", None)
    monkeypatch.setattr(server, "CONFIG", Config(profile="nobody-signed-in"))
    monkeypatch.setattr(server, "STARTUP_REASON", "nobody is signed in on the VAMS profile 'nobody-signed-in'")
    monkeypatch.setattr(server, "STARTUP_USER_STEP", "`vamscli --profile nobody-signed-in auth login -u <user>`")

    def refuse(config):
        raise server.ConfigError("x", reason=server.STARTUP_REASON, user_step=server.STARTUP_USER_STEP)

    monkeypatch.setattr(server, "VamsClient", refuse)
    return server


def _assert_not_authenticated(result):
    assert result["error_type"] == "NotAuthenticated"
    assert "vamscli" not in result["error"], "the agent-facing error must not tell it to run a CLI"
    assert "Ask the user to sign in" in result["error"]
    assert "the user runs `vamscli" in result["user_action"]


def test_without_a_sign_in_a_tool_returns_not_authenticated(signed_out):
    _assert_not_authenticated(signed_out.get_asset("d", "a"))
    _assert_not_authenticated(signed_out.list_databases())


def test_vams_status_answers_without_a_sign_in(signed_out, monkeypatch):
    monkeypatch.setattr(server, "describe_profile", lambda config: {"profile": config.profile, "apiUrl": None})
    status = signed_out.vams_status()
    assert status["authenticated"] is False
    assert status["profile"] == "nobody-signed-in"
    assert status["gates"]["reads"] is True
    assert status["mcpVersion"] == server.MCP_VERSION and status["cliVersion"]
    assert "the user runs `vamscli" in status["user_action"]
    assert "error" not in status


def test_vams_status_reports_a_signed_in_profile(monkeypatch):
    monkeypatch.setattr(server, "CLIENT", MagicMock())
    monkeypatch.setattr(server, "describe_profile",
                        lambda config: {"profile": "p", "apiUrl": "https://x/api", "userId": "u1",
                                        "featureSwitches": ["AUTHPROVIDER_COGNITO"]})
    status = server.vams_status()
    assert status["authenticated"] is True and status["userId"] == "u1"
    assert "user_action" not in status and "reason" not in status


def test_a_sign_in_made_after_start_is_picked_up_without_a_restart(signed_out, monkeypatch):
    client = MagicMock()
    client.api.get_asset.return_value = {"assetId": "a"}
    monkeypatch.setattr(server, "VamsClient", lambda config: client)
    assert signed_out.get_asset("d", "a") == {"assetId": "a"}
    assert server.CLIENT is client


@pytest.mark.parametrize("error", [
    TokenExpiredError("Authentication token has expired. Please run 'vamscli auth login' to re-authenticate."),
    AuthenticationError("Authentication failed. Please run 'vamscli auth login' to re-authenticate."),
])
def test_an_expired_sign_in_during_a_session_is_not_authenticated(monkeypatch, error):
    client = MagicMock()
    client.api.get_asset.side_effect = error
    monkeypatch.setattr(server, "CLIENT", client)
    _assert_not_authenticated(server.get_asset("d", "a"))


def test_vams_status_reports_a_sign_in_the_api_refused_until_a_call_succeeds(monkeypatch):
    client = MagicMock()
    client.api.get_asset.side_effect = TokenExpiredError("expired. Please run 'vamscli auth login'.")
    monkeypatch.setattr(server, "CLIENT", client)
    monkeypatch.setattr(server, "_SIGN_IN_FAILURE", None)
    marker = {"expires_at": 1}
    monkeypatch.setattr(server, "sign_in_marker", lambda config: marker["expires_at"])
    monkeypatch.setattr(server, "describe_profile",
                        lambda config: {"profile": "p", "userId": "u1", "tokenExpired": True})
    first = server.vams_status()
    assert first["authenticated"] is True and "renews" in first["note"]
    _assert_not_authenticated(server.get_asset("d", "a"))
    status = server.vams_status()
    assert status["authenticated"] is False
    assert status["reason"] == server._SIGN_IN_ERRORS[TokenExpiredError]
    assert "the user runs `vamscli" in status["user_action"] and "note" not in status
    client.api.get_asset.side_effect = None
    client.api.get_asset.return_value = {"assetId": "a"}
    assert server.get_asset("d", "a") == {"assetId": "a"}
    assert server.vams_status()["authenticated"] is True


def test_vams_status_drops_a_refusal_once_the_user_signed_in_again(monkeypatch):
    client = MagicMock()
    client.api.get_asset.side_effect = AuthenticationError("refused. Please run 'vamscli auth login'.")
    monkeypatch.setattr(server, "CLIENT", client)
    monkeypatch.setattr(server, "_SIGN_IN_FAILURE", None)
    marker = {"expires_at": 1}
    monkeypatch.setattr(server, "sign_in_marker", lambda config: marker["expires_at"])
    monkeypatch.setattr(server, "describe_profile", lambda config: {"profile": "p", "tokenExpired": False})
    server.get_asset("d", "a")
    assert server.vams_status()["authenticated"] is False
    marker["expires_at"] = 2  # a new `auth login` rewrote the stored sign-in
    assert server.vams_status()["authenticated"] is True


def test_the_server_starts_without_a_sign_in(signed_out, monkeypatch):
    ran = []
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: ran.append(True))
    server.main()  # no SystemExit
    assert ran == [True]


# --- README autoApprove sample (the README sample) ---------------------------------------------

# The reads that return a credential, a presigned URL or other users' identifiers (README "Security
# notes", Mandatory Rule 4). Every other read tool is in the sample, so the agent is not prompted for
# a safe read and has no reason to prefer the shell.
NOT_AUTO_APPROVED_READS = {
    "generate_download_url", "generate_download_urls_bulk",
    "get_api_key", "get_user_api_key", "list_api_keys", "list_user_api_keys", "list_subscriptions",
    # Bills an Amazon Bedrock embedding on every call (Mandatory Rule 4, the billed-read case).
    "search_nlp",
}


def _readme_auto_approve():
    import re

    readme = (Path(server.__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    match = re.search(r'"autoApprove":\s*\[(.*?)\]', readme, re.DOTALL)
    assert match, "the README sample has no autoApprove array"
    return re.findall(r'"(\w+)"', match.group(1))


def test_the_auto_approve_sample_is_every_safe_core_read():
    reads = {name for name, tier in _tiers_by_name().items() if tier == "read" and hasattr(server, name)}
    sample = _readme_auto_approve()
    assert len(sample) == len(set(sample)), "a tool is listed twice"
    assert set(sample) == reads - NOT_AUTO_APPROVED_READS
    assert NOT_AUTO_APPROVED_READS <= reads


# --- The suite is hermetic ---------------------------------------------------------------------------


def test_the_suite_runs_without_the_hosts_vamscli_profile():
    """conftest points HOME at an empty directory before the server is imported, so this suite gives
    the same answer on a developer machine with a signed-in profile as in CI, which has none."""
    from vamscli.constants import get_config_dir

    from . import conftest

    assert str(Path.home()) == conftest.ISOLATED_HOME
    assert str(get_config_dir()).startswith(conftest.ISOLATED_HOME)
    assert not get_config_dir().exists() or not any(get_config_dir().iterdir())
    with pytest.raises(server.ConfigError):
        server.VamsClient(Config())


def test_a_test_that_does_not_ask_for_the_signed_out_state_gets_a_client():
    assert server.CLIENT is not None


def test_the_signed_out_fixture_keeps_the_server_signed_out(no_client):
    assert no_client.CLIENT is None
    assert no_client.get_asset("d", "a")["error_type"] == "NotAuthenticated"


async def test_descriptions_carry_no_source_indentation(every_tool):
    """Python 3.12 keeps a docstring's indentation and 3.13 strips it; `tool_result` cleans it, so the
    description and its budget are the same on both."""
    indented = [name for name, tool in every_tool.items()
                if any(line.startswith("    ") for line in (tool.description or "").splitlines())]
    assert indented == []
