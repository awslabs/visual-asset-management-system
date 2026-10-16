"""The fork extension point for MCP tools (`vams_mcp/custom_tools.py`).

`server.py` calls `register_custom_tools(mcp, CLIENT, CONFIG, tool_result)` once. The shipped function
is a no-op that a fork fills in, so it is replaced here with a probe rather than edited: the probe
records what it was handed and registers one read tool and one write-gated tool, which shows both that
a custom tool reaches the server and that the gate a fork's tool reads is the server's own CONFIG.
A second probe registers a tool under a core tool's name, which pins the SDK behavior a fork relies
on: the first registration (the core tool) is kept and the duplicate is dropped with a log line.
"""

import importlib
import logging
import re
from pathlib import Path

import pytest

import vams_mcp.custom_tools as custom_tools
from vams_mcp import server as server_module

SOURCE_LINES = Path(server_module.__file__).read_text(encoding="utf-8").splitlines()
CALL = "register_custom_tools(mcp, CLIENT, CONFIG, tool_result)"


def _line_of(prefix):
    for i, line in enumerate(SOURCE_LINES, start=1):
        if line.startswith(prefix):
            return i
    raise AssertionError(f"{prefix!r} not found in server.py")


def _last_tool_definition_line():
    last = 0
    for i, line in enumerate(SOURCE_LINES, start=1):
        if re.match(r"^\s*def \w+\(", line) and i >= 2 and "tool_result" in SOURCE_LINES[i - 2]:
            last = i
    return last


def test_the_hook_is_called_once_at_module_level():
    calls = [i for i, line in enumerate(SOURCE_LINES, start=1) if CALL in line]
    assert len(calls) == 1, f"expected one call to the custom-tools hook, found lines {calls}"
    # Module level, not inside a gate block: the fork's own code decides which gate each tool needs.
    assert SOURCE_LINES[calls[0] - 1].startswith(CALL)


def test_the_hook_runs_after_every_core_tool_and_before_the_entrypoint():
    call = _line_of(CALL)
    assert _line_of("if CONFIG.enable_destructive:") < call
    assert _last_tool_definition_line() < call
    assert call < _line_of("def main()")


@pytest.fixture
def probed_server(monkeypatch):
    """Reload the server with a probe in place of the shipped hook; restore it afterwards."""
    seen = {}

    def probe(mcp, client, config, tool_result):
        seen.update(mcp=mcp, client=client, config=config, tool_result=tool_result)

        @mcp.tool()
        @tool_result
        def custom_probe_read() -> dict:
            """Probe read tool."""
            return {"ok": True}

        if config.enable_writes:

            @mcp.tool()
            @tool_result
            def custom_probe_write() -> dict:
                """Probe write tool."""
                return {"ok": True}

    monkeypatch.setattr(custom_tools, "register_custom_tools", probe)
    importlib.reload(server_module)
    try:
        yield server_module, seen
    finally:
        monkeypatch.undo()
        importlib.reload(server_module)


@pytest.mark.asyncio
async def test_a_custom_tool_is_registered_with_the_servers_objects(probed_server):
    server, seen = probed_server
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert "custom_probe_read" in names
    assert seen["mcp"] is server.mcp
    assert seen["client"] is server.CLIENT
    assert seen["config"] is server.CONFIG
    assert seen["tool_result"] is server.tool_result


@pytest.mark.asyncio
async def test_a_custom_write_tool_follows_the_servers_write_gate(probed_server):
    server, _ = probed_server
    assert server.CONFIG.enable_writes is False
    names = {tool.name for tool in await server.mcp.list_tools()}
    assert "custom_probe_write" not in names


@pytest.mark.asyncio
async def test_the_reload_restores_the_shipped_hook():
    # Control for the fixture's teardown: the default server carries no probe tool.
    names = {tool.name for tool in await server_module.mcp.list_tools()}
    assert "custom_probe_read" not in names
    assert server_module.register_custom_tools is custom_tools.register_custom_tools


class _Records(logging.Handler):
    """Collects log records. Attached to the `mcp` logger, which the server's root-logger reset
    (`logging.basicConfig(force=True)` at import) does not touch."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture
def shadowing_server(monkeypatch):
    """Reload the server with a hook that registers a tool under a CORE tool's name."""
    records = _Records()
    mcp_logger = logging.getLogger("mcp")
    mcp_logger.addHandler(records)

    def shadow(mcp, client, config, tool_result):
        @mcp.tool()
        @tool_result
        def list_databases() -> dict:
            """Probe shadow tool."""
            return {"shadow": True}

        @mcp.tool()
        @tool_result
        def custom_probe_distinct() -> dict:
            """Probe distinct tool."""
            return {"ok": True}

    monkeypatch.setattr(custom_tools, "register_custom_tools", shadow)
    importlib.reload(server_module)
    try:
        yield server_module, records
    finally:
        mcp_logger.removeHandler(records)
        monkeypatch.undo()
        importlib.reload(server_module)


@pytest.mark.asyncio
async def test_a_custom_tool_named_like_a_core_tool_keeps_the_core_tool(shadowing_server):
    server, records = shadowing_server
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    assert tools["list_databases"].description.startswith("List VAMS databases")
    assert "Probe shadow tool." not in tools["list_databases"].description
    assert "Tool already exists: list_databases" in records.messages
    # Control: the hook ran and its tool with a distinct name was registered.
    assert "custom_probe_distinct" in tools
    assert "Tool already exists: custom_probe_distinct" not in records.messages
