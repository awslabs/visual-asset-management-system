"""The compliance tools form the ``compliance`` tool set, keep their own description budget, and over
the remote server act for the signed-in user under the same gates (D201).

Tool sets decide which tools a connection lists (``VAMS_MCP_TOOLSETS``), and a tool in no named set can
never be listed by a remote client. The 25 compliance tools are in one set of their own, ``compliance``,
with the reads that orient a session. Their descriptions keep a total of their own
(``tests/test_agent_surface.py`` ``MAX_COMPLIANCE_TOTAL_CHARS``) inside the per-set budget, so they take
no room from another set. The write and destructive gates apply inside the set as everywhere, and every
compliance tool calls the VAMS API with the request's own credential: over the remote server it is the
signed-in user's token, so the API's authorizer and constraints decide each call.
"""

import json
import os

import pytest

from vams_mcp import toolsets

from .remote_support import FakeDeployment, RemoteServer, free_port, remote_server_module
from .test_agent_surface import COMPLIANCE_TOOLS as AGENT_SURFACE_COMPLIANCE_TOOLS
from .test_agent_surface import MAX_COMPLIANCE_TOTAL_CHARS
from .test_toolsets import ALL_SWITCHES

READS = {name for name in toolsets.COMPLIANCE_TOOLS if name.startswith(("list_", "get_", "query_"))}
DESTRUCTIVE = {"delete_compliance_schema"}
WRITES = set(toolsets.COMPLIANCE_TOOLS) - READS - DESTRUCTIVE
ORIENTATION = {"vams_status", "list_features", "list_allowed_api_routes", "list_databases", "get_database"}


async def _listed(server_with_switches, selection, **gates):
    os.environ["VAMS_MCP_TOOLSETS"] = selection
    try:
        with server_with_switches(ALL_SWITCHES, **gates) as server:
            os.environ.pop("VAMS_MCP_TOOLSETS", None)
            tools = {tool.name: tool for tool in await server.mcp.list_tools()}
            return tools, server.vams_status()
    finally:
        os.environ.pop("VAMS_MCP_TOOLSETS", None)


def test_the_compliance_set_is_the_compliance_tools_and_the_orientation_reads():
    assert toolsets.COMPLIANCE == toolsets.COMPLIANCE_TOOLS | ORIENTATION
    assert toolsets.TOOLSETS["compliance"] is toolsets.COMPLIANCE
    # One list of the compliance tools: the set's, and the budget test's.
    assert toolsets.COMPLIANCE_TOOLS == AGENT_SURFACE_COMPLIANCE_TOOLS
    assert len(toolsets.COMPLIANCE_TOOLS) == 25
    assert (len(READS), len(WRITES), len(DESTRUCTIVE)) == (11, 13, 1)


def test_the_compliance_tools_are_in_the_compliance_set_only():
    holding = {name: sorted(set_name for set_name, members in toolsets.TOOLSETS.items() if name in members)
               for name in toolsets.COMPLIANCE_TOOLS}
    assert {name: sets for name, sets in holding.items() if sets != ["compliance"]} == {}


async def test_the_compliance_set_keeps_its_own_budget_within_the_per_set_budget(server_with_switches):
    tools, _ = await _listed(server_with_switches, "all", enable_writes=True, enable_destructive=True)
    compliance = sum(len(tools[name].description or "") for name in toolsets.COMPLIANCE_TOOLS)
    whole_set = sum(len(tools[name].description or "") for name in toolsets.COMPLIANCE)
    assert 0 < compliance <= MAX_COMPLIANCE_TOTAL_CHARS < toolsets.MAX_DESCRIPTION_CHARS_PER_SET
    assert whole_set <= toolsets.MAX_DESCRIPTION_CHARS_PER_SET
    assert len(toolsets.COMPLIANCE) <= toolsets.MAX_TOOLS_PER_SET


@pytest.mark.parametrize("gates, expected", [
    ({}, READS),
    ({"enable_writes": True}, READS | WRITES),
    ({"enable_writes": True, "enable_destructive": True}, READS | WRITES | DESTRUCTIVE),
], ids=["reads only", "writes", "writes and destructive"])
async def test_the_gates_apply_inside_the_compliance_set(server_with_switches, gates, expected):
    tools, status = await _listed(server_with_switches, "compliance", **gates)
    assert set(tools) - ORIENTATION == expected
    assert status["gates"]["toolsets"] == ["compliance"]
    for name in expected:
        hints = tools[name].model_dump(by_alias=True)["annotations"]
        assert hints["readOnlyHint"] is (name in READS), name
        assert hints["destructiveHint"] is (name in DESTRUCTIVE), name


def test_every_compliance_write_is_checked_with_a_compliance_read():
    from vams_mcp import server

    checks = {name: server.outcome_check_tool(name) for name in WRITES | DESTRUCTIVE}
    assert {name: check for name, check in checks.items() if check not in READS} == {}


# --- Over the remote server -------------------------------------------------------------------------


@pytest.fixture
def deployment():
    with FakeDeployment() as fake:
        yield fake


@pytest.fixture
def compliance_remote(deployment, tmp_path):
    """The real remote application serving the compliance set with writes on and destructive off."""
    port = free_port()
    settings = deployment.settings_dict(f"http://127.0.0.1:{port}/mcp")
    env = {"VAMS_MCP_TOOLSETS": "compliance", "VAMS_ENABLE_WRITES": "true"}
    # remote_server_module clears env and reloads the stdio server on exit.
    with remote_server_module(settings, tmp_path, env=env) as server:
        from vams_mcp import http_app

        app = http_app.build_app(server.mcp, server.REMOTE_SETTINGS, server.CONFIG)
        with RemoteServer(app, port) as running:
            yield running


def _rpc_error_or_result(response):
    assert response.status_code == 200, (response.status_code, response.text[:300])
    message = response.json()
    if "error" in message:
        return {"rpc_error": message["error"]}
    result = message["result"]
    return {"isError": result.get("isError"), "text": result["content"][0]["text"] if result["content"] else ""}


def test_the_remote_server_lists_the_compliance_set_under_its_gates(compliance_remote, deployment):
    response = compliance_remote.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                                      deployment.token("alice"))
    listed = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
    assert set(listed) == (toolsets.COMPLIANCE - DESTRUCTIVE)
    assert all(listed[name]["annotations"]["readOnlyHint"] for name in READS)
    assert not any(listed[name]["annotations"]["readOnlyHint"] for name in WRITES)


@pytest.mark.parametrize("tool, arguments", [
    ("list_compliance_schemas", {}),
    ("release_quarantine", {"database_id": "db1", "asset_id": "a1", "reason": "reviewed"}),
], ids=["read", "write"])
def test_a_compliance_call_reaches_the_api_with_the_users_own_token(compliance_remote, deployment, tool, arguments):
    """The API decides: the call carries the signed-in user's token, and the API's answer comes back."""
    token = deployment.token("alice")
    before = len(deployment.api_authorizations)
    outcome = _rpc_error_or_result(compliance_remote.call_tool(tool, arguments, token=token))
    sent = deployment.api_authorizations[before:]
    assert sent and set(sent) == {f"Bearer {token}"}, sent
    # The fake API knows no compliance route: its refusal is the tool's answer, nothing is decided here.
    assert "rpc_error" not in outcome
    assert "error" in json.loads(outcome["text"])


def test_a_destructive_compliance_tool_is_not_served_with_the_destructive_gate_off(compliance_remote, deployment):
    before = len(deployment.api_authorizations)
    outcome = _rpc_error_or_result(compliance_remote.call_tool(
        "delete_compliance_schema", {"schema_name": "s1"}, token=deployment.token("alice")))
    assert outcome.get("rpc_error") or outcome.get("isError"), outcome
    assert deployment.api_authorizations[before:] == [], "nothing reached the API"
