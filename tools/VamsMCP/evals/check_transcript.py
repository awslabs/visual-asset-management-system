"""Check a recorded agent session against the tool criteria of agent-scenarios.md.

Input is either Claude Code's ``--output-format stream-json`` output (one JSON event per line), or a
plain text file with one called tool name per line (for any other client). The check passes when
every tool the agent called is a VAMS MCP tool and at least one of the scenario's expected tools was
called. It does not judge the answer text.

Usage: python check_transcript.py <transcript> --scenario S1 [--server vams]
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Iterable, List, Set

# Tools a scenario expects; one of them must be called.
EXPECTED = {
    "S1": {"search_assets", "list_assets"},
    "S2": {"get_asset_metadata"},
    "S3": {"generate_download_url"},
    "S4": {"get_execution_details"},
    "S5": {"vams_status", "list_databases"},
    "S6": {"move_file"},
    "S7": {"list_features"},
}

# Client tools that touch neither the shell nor the file system, so they break no rule. ToolSearch is
# how Claude Code loads the schemas of deferred MCP tools before it calls them.
NEUTRAL_TOOLS = {"TodoWrite", "TodoRead", "Task", "todo_list", "thinking", "ToolSearch"}

SERVER_SOURCES = [Path(__file__).resolve().parents[1] / "vams_mcp" / "server.py",
                  *sorted((Path(__file__).resolve().parents[1] / "vams_mcp" / "addon_tools").glob("*.py"))]


def vams_tool_names() -> Set[str]:
    """Every tool name the server can register, read from its source (no import, no sign-in).

    A plain list gives bare names, and a client's own tools (Kiro's ``execute_bash``, ``fs_read``)
    look like tool names too, so only a name the server defines counts as a VAMS tool.
    """
    names: Set[str] = set()
    for path in SERVER_SOURCES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                "tool" in ast.unparse(d) for d in node.decorator_list
            ):
                names.add(node.name)
    return names


def tool_calls(lines: Iterable[str]) -> List[str]:
    """The tool names a transcript called, in order."""
    names: List[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            names.append(line)  # the plain one-name-per-line form
            continue
        message = event.get("message") if isinstance(event, dict) else None
        for block in (message or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                names.append(str(block.get("name", "")))
    return names


def mcp_tool_name(name: str, server: str) -> str | None:
    """The VAMS tool behind a client's name for it, or None for any other tool.

    Claude Code names an MCP tool ``mcp__<server>__<tool>``; Kiro uses ``@<server>/<tool>`` or
    ``<server>___<tool>``; a plain list may give the bare tool name.
    """
    for prefix in (f"mcp__{server}__", f"@{server}/", f"{server}___", f"mcp_{server}_"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return None


def check(names: List[str], scenario: str, server: str = "vams", bare_names_are_mcp: bool = False):
    """(passed, problems) for one scenario."""
    problems: List[str] = []
    used = set()
    known = vams_tool_names() if bare_names_are_mcp else set()
    for name in names:
        tool = mcp_tool_name(name, server)
        if tool is None and name in known:
            tool = name
        if tool is not None:
            used.add(tool)
        elif name not in NEUTRAL_TOOLS:
            problems.append(f"non-MCP tool called: {name}")
    expected = EXPECTED[scenario]
    if not used & expected:
        problems.append(f"none of the expected tools was called: {sorted(expected)}")
    return not problems, problems


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--scenario", required=True, choices=sorted(EXPECTED))
    parser.add_argument("--server", default="vams", help="the MCP server's name in the client config")
    args = parser.parse_args(argv)
    text = args.transcript.read_text(encoding="utf-8").splitlines()
    plain = not any(line.lstrip().startswith("{") for line in text)
    passed, problems = check(tool_calls(text), args.scenario, args.server, bare_names_are_mcp=plain)
    for problem in problems:
        print(problem)
    print(f"{args.scenario}: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
