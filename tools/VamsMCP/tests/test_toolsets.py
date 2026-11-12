"""Tool sets (VAMS_MCP_TOOLSETS): every named set fits a remote client's limits, and selects exactly
its tools.

A remote client lists at most 100 tools per connection (Amazon Quick drops the rest silently), and
every connected agent reads every description at the start of a session. So each named set holds at
most ``toolsets.MAX_TOOLS_PER_SET`` tools and ``toolsets.MAX_DESCRIPTION_CHARS_PER_SET`` characters of
descriptions, counted with every gate and every add-on on.
"""

import os

import pytest
from vamscli import addon_constants

from vams_mcp import toolsets
from vams_mcp.config import Config, ConfigError

ALL_SWITCHES = ["AUTHPROVIDER_COGNITO", addon_constants.FEATURE_EDH_WORKSTATIONS,
                addon_constants.FEATURE_EDH_CONTAINERS, addon_constants.FEATURE_EDH_AMI_RECIPES]


@pytest.fixture
async def every_tool(server_with_switches):
    """Every tool a server registers with both gates and every add-on on, by name."""
    with server_with_switches(ALL_SWITCHES, enable_writes=True, enable_destructive=True) as server:
        return {tool.name: tool for tool in await server.mcp.list_tools()}


async def _tools_with(server_with_switches, selection):
    os.environ["VAMS_MCP_TOOLSETS"] = selection
    try:
        with server_with_switches(ALL_SWITCHES, enable_writes=True, enable_destructive=True) as server:
            # Cleared before the fixture's restoring reload, so the next test sees the full server.
            os.environ.pop("VAMS_MCP_TOOLSETS", None)
            names = {tool.name for tool in await server.mcp.list_tools()}
            return {"toolsets": server.CONFIG.toolsets, "status": server.vams_status()}, names
    finally:
        os.environ.pop("VAMS_MCP_TOOLSETS", None)


@pytest.mark.parametrize("name", sorted(toolsets.TOOLSETS))
async def test_every_named_set_fits_the_tool_limit(every_tool, name):
    members = toolsets.TOOLSETS[name]
    assert len(members) <= toolsets.MAX_TOOLS_PER_SET, len(members)


@pytest.mark.parametrize("name", sorted(toolsets.TOOLSETS))
async def test_every_named_set_fits_the_description_budget(every_tool, name):
    total = sum(len(every_tool[tool].description or "") for tool in toolsets.TOOLSETS[name])
    assert total <= toolsets.MAX_DESCRIPTION_CHARS_PER_SET, total


async def test_every_set_member_is_a_real_tool(every_tool):
    unknown = {name: sorted(members - set(every_tool)) for name, members in toolsets.TOOLSETS.items()}
    assert {name: tools for name, tools in unknown.items() if tools} == {}


async def test_every_tool_is_in_a_named_set(every_tool):
    """A new core or add-on tool must be placed in a set, or no remote client can ever list it."""
    placed = frozenset().union(*toolsets.TOOLSETS.values())
    assert sorted(set(every_tool) - placed) == []


async def test_the_consumer_set_is_read_only(every_tool):
    writable = [name for name in toolsets.CONSUMER
                if not every_tool[name].model_dump(by_alias=True)["annotations"]["readOnlyHint"]]
    assert writable == []


async def test_a_selected_set_registers_exactly_its_tools(server_with_switches):
    seen, names = await _tools_with(server_with_switches, "consumer")
    assert names == set(toolsets.CONSUMER)
    assert seen["toolsets"] == ("consumer",)


async def test_sets_combine_and_the_gates_still_apply(server_with_switches):
    os.environ["VAMS_MCP_TOOLSETS"] = "operator,edh"
    try:
        with server_with_switches(ALL_SWITCHES) as server:  # both gates off
            os.environ.pop("VAMS_MCP_TOOLSETS", None)
            names = {tool.name for tool in await server.mcp.list_tools()}
            status = server.vams_status()
    finally:
        os.environ.pop("VAMS_MCP_TOOLSETS", None)
    assert "create_asset" not in names and "create_edh_workstation" not in names
    assert {"get_asset", "list_edh_workstations"} <= names
    assert names <= set(toolsets.OPERATOR | toolsets.EDH)
    assert status["gates"]["toolsets"] == ["operator", "edh"]
    assert status["gates"]["addons"] == ["edh"]


async def test_an_add_on_outside_the_selection_is_not_reported_registered(server_with_switches):
    seen, names = await _tools_with(server_with_switches, "consumer")
    assert not any("edh" in name for name in names)
    assert seen["status"]["gates"]["addons"] == []


async def test_all_registers_every_tool(server_with_switches, every_tool):
    _, names = await _tools_with(server_with_switches, "all")
    assert names == set(every_tool)


def test_custom_selects_only_the_tools_of_no_named_set():
    selection = toolsets.Selection(["custom"])
    assert selection.includes("custom_report") and not selection.includes("get_asset")
    assert toolsets.Selection(["consumer", "custom"]).includes("get_asset")


@pytest.mark.parametrize("raw, transport, expected", [
    (None, "stdio", ("all",)),
    ("", "stdio", ("all",)),
    (None, "streamable-http", ("consumer",)),
    (" Operator , edh ,operator", "stdio", ("operator", "edh")),
])
def test_the_selection_is_parsed_with_the_transport_default(monkeypatch, tmp_path, raw, transport, expected):
    if raw is not None:
        monkeypatch.setenv("VAMS_MCP_TOOLSETS", raw)
    monkeypatch.setenv("VAMS_MCP_TRANSPORT", transport)
    monkeypatch.setenv("VAMS_MCP_AUTH_CONFIG", str(tmp_path / "auth.json"))
    assert Config.from_env().toolsets == expected


def test_an_unknown_set_stops_the_server(monkeypatch):
    monkeypatch.setenv("VAMS_MCP_TOOLSETS", "consumer,everything")
    with pytest.raises(ConfigError, match="everything"):
        Config.from_env()


@pytest.mark.parametrize("name", sorted(toolsets.TOOLSETS))
async def test_every_outcome_check_is_a_read_tool_of_the_same_set(every_tool, name):
    """A non-read call past its time budget names a read tool to check with; the connection must list it."""
    from vams_mcp import server

    wrong = {}
    for tool in sorted(toolsets.TOOLSETS[name]):
        if every_tool[tool].model_dump(by_alias=True)["annotations"]["readOnlyHint"]:
            continue
        check = server.outcome_check_tool(tool)
        annotations = every_tool[check].model_dump(by_alias=True)["annotations"] if check in every_tool else {}
        if check not in toolsets.TOOLSETS[name] or not annotations.get("readOnlyHint"):
            wrong[tool] = check
    assert wrong == {}


def test_the_subscription_writes_are_checked_with_a_tool_of_the_operator_set():
    from vams_mcp import server

    for tool in ("create_subscription", "update_subscription", "delete_subscription", "unsubscribe"):
        assert server.outcome_check_tool(tool) == "check_subscription"
    # A tool in no named set (a fork's) takes the first match.
    assert server.outcome_check_tool("custom_subscription_writer") == "list_subscriptions"
