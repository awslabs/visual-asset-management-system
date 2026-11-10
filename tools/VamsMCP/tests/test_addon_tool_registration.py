"""Which add-ons' MCP tools a server lists follows the deployment the profile signed in to.

`register_addon_tools` registers an add-on module's tools only when the feature-switch snapshot of the
vamscli profile the server starts with carries the module's `FEATURE`. A deployment without the add-on
therefore lists none of its tools, at any gate setting, and one with it lists them as before. Each
shipped add-on module names a switch a deployment publishes. Without a profile (no client) no add-on
tool is registered.
"""

import ast
from pathlib import Path

import pytest
from vamscli import addon_constants

import vams_mcp.addon_tools as addon_tools
from vams_mcp.addon_tools import addon_deployed, edh

EDH_TOOL_NAMES = {
    node.name
    for node in ast.walk(ast.parse(Path(edh.__file__).read_text(encoding="utf-8")))
    if isinstance(node, ast.FunctionDef)
    and [ast.unparse(d) for d in node.decorator_list] == ["mcp.tool()", "tool_result"]
}
# A core switch every deployment publishes, so "off" is a profile with switches rather than none.
CORE_ONLY = ["AUTHPROVIDER_COGNITO", "LOCATIONSERVICES"]
ALL_GATES = {"enable_writes": True, "enable_destructive": True}


def test_every_add_on_module_names_a_published_add_on_switch():
    published = {value for name, value in vars(addon_constants).items() if name.startswith("FEATURE_")}
    assert addon_tools.ADDON_TOOL_MODULES, "no add-on module, so this would be vacuous"
    for module in addon_tools.ADDON_TOOL_MODULES:
        assert getattr(module, "FEATURE", None) in published, module.__name__
    assert edh.FEATURE == addon_constants.FEATURE_EDH_WORKSTATIONS


def test_no_client_registers_no_add_on():
    assert addon_deployed(None, edh) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("gates", [{}, {"enable_writes": True}, ALL_GATES], ids=["default", "writes", "all"])
async def test_with_the_add_on_off_the_server_lists_no_edh_tool(server_with_switches, gates):
    assert len(EDH_TOOL_NAMES) > 40, "the EDH tool names were not read, so this would be vacuous"
    with server_with_switches(CORE_ONLY, **gates) as server:
        names = {tool.name for tool in await server.mcp.list_tools()}
    assert "list_databases" in names
    assert sorted(names & EDH_TOOL_NAMES) == []


@pytest.mark.asyncio
async def test_with_the_add_on_on_the_server_lists_its_tools(server_with_switches):
    with server_with_switches(CORE_ONLY + [addon_constants.FEATURE_EDH_WORKSTATIONS], **ALL_GATES) as server:
        names = {tool.name for tool in await server.mcp.list_tools()}
    assert sorted(EDH_TOOL_NAMES - names) == []


@pytest.mark.asyncio
async def test_an_optional_feature_off_still_answers_not_enabled_before_any_request(server_with_switches):
    """Container-job and AMI recipe tools are registered with the add-on and refuse when their own
    switch is absent, as the CLI does."""
    with server_with_switches([addon_constants.FEATURE_EDH_WORKSTATIONS]) as server:
        names = {tool.name for tool in await server.mcp.list_tools()}
        assert "list_edh_container_jobs" in names
        answer = str(await server.mcp.call_tool("list_edh_container_jobs", {}))
        assert "FeatureDisabledError" in answer
        assert addon_constants.EDH_CONTAINERS_DISABLED_MESSAGE.split(".")[0] in answer
        assert not server.CLIENT.api.list_edh_container_jobs.called
