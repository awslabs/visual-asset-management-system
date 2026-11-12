"""The transcript checker of the agent-behaviour scenarios (evals/check_transcript.py)."""

import importlib.util
import json
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parents[1] / "evals"
_spec = importlib.util.spec_from_file_location("check_transcript", EVALS / "check_transcript.py")
check_transcript = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_transcript)


def _stream_json(*names):
    return [json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": n}]}})
            for n in names]


def test_an_mcp_only_session_passes():
    names = check_transcript.tool_calls(_stream_json("mcp__vams__search_assets", "TodoWrite"))
    assert check_transcript.check(names, "S1") == (True, [])


@pytest.mark.parametrize("intruder", ["Bash", "Read", "Grep", "execute_bash"])
def test_a_shell_or_file_read_fails(intruder):
    names = check_transcript.tool_calls(_stream_json("mcp__vams__search_assets", intruder))
    passed, problems = check_transcript.check(names, "S1")
    assert not passed and problems == [f"non-MCP tool called: {intruder}"]


def test_a_session_without_an_expected_tool_fails():
    names = check_transcript.tool_calls(_stream_json("mcp__vams__list_databases"))
    passed, problems = check_transcript.check(names, "S4")
    assert not passed and "expected tools" in problems[0]


def test_kiro_names_and_a_plain_list_are_read():
    assert check_transcript.check(["@vams/get_asset_metadata"], "S2") == (True, [])
    names = check_transcript.tool_calls(["get_execution_details", ""])
    assert check_transcript.check(names, "S4", bare_names_are_mcp=True) == (True, [])


def test_every_scenario_in_the_document_has_expected_tools():
    text = (EVALS / "agent-scenarios.md").read_text(encoding="utf-8")
    for scenario in check_transcript.EXPECTED:
        assert f"| {scenario} " in text, scenario


def test_the_cli_exit_code(tmp_path):
    good = tmp_path / "good.jsonl"
    good.write_text("\n".join(_stream_json("mcp__vams__generate_download_url")), encoding="utf-8")
    bad = tmp_path / "bad.txt"
    bad.write_text("Bash\ngenerate_download_url\n", encoding="utf-8")
    assert check_transcript.main([str(good), "--scenario", "S3"]) == 0
    assert check_transcript.main([str(bad), "--scenario", "S3"]) == 1


@pytest.mark.parametrize("builtin", ["execute_bash", "fs_read", "shell"])
def test_a_client_builtin_in_a_plain_list_fails(builtin):
    names = check_transcript.tool_calls(["search_assets", builtin])
    passed, problems = check_transcript.check(names, "S1", bare_names_are_mcp=True)
    assert not passed and problems == [f"non-MCP tool called: {builtin}"]


def test_claude_codes_tool_loader_is_neutral():
    names = check_transcript.tool_calls(_stream_json("ToolSearch", "mcp__vams__search_assets"))
    assert check_transcript.check(names, "S1") == (True, [])


def test_the_known_tool_names_cover_core_and_add_on_tools():
    names = check_transcript.vams_tool_names()
    assert {"vams_status", "search_assets", "get_execution_details", "list_edh_workstations"} <= names
