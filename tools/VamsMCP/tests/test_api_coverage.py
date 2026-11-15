"""The coverage manifest (tests/api_coverage.py) agrees with the CLI's APIClient and with the tools.

Root CLAUDE.md Pattern 7 rule 2 says an APIClient method agents should use gets an MCP tool. Without a
check, a new CLI method that nobody decided on stays CLI-only unnoticed. These tests fail on:

* a public APIClient method (core, add-on or fork mixin) that no table of the manifest names;
* a manifest entry for a method that no longer exists, or a method in two tables;
* a TOOLS entry naming a tool that is not registered with every gate and add-on on;
* a difference between TOOLS and the `client.api.<method>` calls the tools actually make.
"""

import ast
import re
import sys
from pathlib import Path

import pytest

from vamscli import addon_constants
from vamscli.utils.api_client import APIClient
from vamscli.utils.custom_api_client import CustomAPIClientMixin

from vams_mcp import custom_tools, server
from vams_mcp.addon_tools import ADDON_TOOL_MODULES

from . import api_coverage

ALL_SWITCHES = [
    "AUTHPROVIDER_COGNITO",
    addon_constants.FEATURE_EDH_WORKSTATIONS,
    addon_constants.FEATURE_EDH_CONTAINERS,
    addon_constants.FEATURE_EDH_AMI_RECIPES,
]

TABLES = {
    "TOOLS": api_coverage.TOOLS,
    "EXCLUDED": api_coverage.EXCLUDED,
    "NOT_YET_EXPOSED": api_coverage.NOT_YET_EXPOSED,
}


def _public_methods(cls=APIClient):
    """Every public callable on the class and its mixins, by the class that defines it."""
    methods = {}
    for klass in cls.__mro__:
        if klass is object:
            continue
        for name, member in vars(klass).items():
            if name.startswith("_") or name in methods:
                continue
            if callable(member) or isinstance(member, (staticmethod, classmethod)):
                methods[name] = klass
    return methods


def _tool_sources():
    """The source files whose decorated functions are tools: server.py and each add-on module."""
    return [Path(server.__file__)] + [Path(module.__file__) for module in ADDON_TOOL_MODULES]


def _calls_by_tool(paths):
    """{api method: {tool names that call it}} from every `@mcp.tool(...)`-decorated function."""
    calls = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            decorated = any(
                isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "tool" for d in node.decorator_list
            )
            if not decorated:
                continue
            for method in re.findall(r"\.api\.(\w+)\(", ast.unparse(node)):
                calls.setdefault(method, set()).add(node.name)
    return calls


def _custom_tool_calls():
    return set(re.findall(r"\.api\.(\w+)\(", Path(custom_tools.__file__).read_text(encoding="utf-8")))


def test_every_public_apiclient_method_is_in_the_manifest():
    custom = set(vars(CustomAPIClientMixin)) & set(_public_methods())
    named = set().union(*TABLES.values())
    missing = sorted(set(_public_methods()) - named - (custom & _custom_tool_calls()))
    assert missing == [], (
        "APIClient methods with no MCP decision: add each to TOOLS (after adding its tool), EXCLUDED "
        f"(with the reason) or NOT_YET_EXPOSED in tests/api_coverage.py: {missing}"
    )


def test_the_manifest_names_only_methods_that_exist():
    public = set(_public_methods())
    stale = {table: sorted(set(entries) - public) for table, entries in TABLES.items() if set(entries) - public}
    assert stale == {}, "the manifest names APIClient methods that no longer exist"


def test_each_method_is_in_exactly_one_table():
    seen = {}
    for table, entries in TABLES.items():
        for method in entries:
            seen.setdefault(method, []).append(table)
    assert {m: t for m, t in seen.items() if len(t) > 1} == {}


@pytest.mark.parametrize("table", ["EXCLUDED", "NOT_YET_EXPOSED"])
def test_every_entry_without_a_tool_carries_a_one_line_reason(table):
    bad = [m for m, reason in TABLES[table].items() if not reason.strip() or "\n" in reason or len(reason) > 160]
    assert bad == []


def test_the_tools_table_matches_what_the_tools_call():
    """Both directions: a new tool calling a method the table does not list for it, and a table entry
    whose tool no longer makes that call."""
    actual = _calls_by_tool(_tool_sources())
    assert actual, "no tool calls found, so this would be vacuous"
    declared = {method: set(tools) for method, tools in api_coverage.TOOLS.items()}
    differ = {
        method: {"TOOLS says": sorted(declared.get(method, ())), "tools call": sorted(actual.get(method, ()))}
        for method in set(declared) | set(actual)
        if declared.get(method) != actual.get(method)
    }
    assert differ == {}, "update TOOLS in tests/api_coverage.py to match the tools' client.api calls"


async def test_every_tool_the_manifest_names_is_registered(server_with_switches):
    with server_with_switches(ALL_SWITCHES, enable_writes=True, enable_destructive=True) as reloaded:
        registered = {tool.name for tool in await reloaded.mcp.list_tools()}
    named = set().union(*api_coverage.TOOLS.values())
    assert len(registered) > 140, "the full tool set was not registered, so this would be vacuous"
    assert sorted(named - registered) == []


def test_upload_and_administration_stay_cli_only():
    """The decision the server instructions state: no tool uploads files or administers users, roles
    or constraints."""
    for method in ("initialize_upload", "complete_upload", "create_cognito_user", "create_role",
                   "create_constraint", "update_user_roles"):
        assert method in api_coverage.EXCLUDED, method
    text = " ".join(server.SERVER_INSTRUCTIONS.split())
    assert "Uploads, sync and user, role or constraint administration have no tools" in text
    assert "vamscli CLI" in text


def test_the_custom_mixin_rule_counts_a_method_a_custom_tool_calls(monkeypatch):
    """A fork's method is covered by a call in custom_tools.py, without editing the manifest."""
    module = sys.modules[__name__]
    monkeypatch.setattr(CustomAPIClientMixin, "fork_widget_report", lambda self: {}, raising=False)
    monkeypatch.setattr(module, "_custom_tool_calls", lambda: {"fork_widget_report"})
    test_every_public_apiclient_method_is_in_the_manifest()
    monkeypatch.setattr(module, "_custom_tool_calls", lambda: set())
    with pytest.raises(AssertionError, match="fork_widget_report"):
        test_every_public_apiclient_method_is_in_the_manifest()
