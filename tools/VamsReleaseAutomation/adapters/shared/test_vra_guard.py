# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for vra-guard.py. Run: python3 -m pytest adapters/shared/ (or python3 this file). Python 3.9+,
like the guard."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

GUARD = pathlib.Path(__file__).with_name("vra-guard.py")

CASES = [
    ("vra deploy --env dev --build b1 --json", 0),
    ("vra --json --ledger ./ledger promote --from dev --to test --build b1 --approval a", 0),
    ("vra resume promote-20261103T044338Z-73b6", 0),
    ("git status", 0),
    ("git push origin sync/release-v2.7.0", 0),
    ("git push origin sync/fix-file-switch", 0),
    ("git push -u origin sync/release-v2.7.0", 0),
    ("git push --force origin main", 2),
    ("git push -f origin main", 2),
    ("git push -uf origin main", 2),
    ("git push origin +main", 2),
    ("git push origin --delete old", 2),
    ("git push origin :old", 2),
    ("git -c core.x=1 push --force origin main", 2),
    ("git -C ../fork push --force-with-lease", 2),
    ("FOO=1 npx cdk deploy --all", 2),
    ("npx --yes aws-cdk@2.1135.0 deploy --all", 2),
    ("vra status && aws sts get-caller-identity", 2),
    ("/usr/local/bin/aws s3 ls", 2),
    ("env AWS_PROFILE=x aws s3 ls", 2),
    ("timeout 60 aws s3 ls", 2),
    ("bash -c 'aws s3 rm s3://b/k'", 2),
    ("python3 -c 'import boto3; boto3.client(\"s3\").delete_bucket(Bucket=\"b\")'", 2),
    ("python -m awscli s3 ls", 2),
    ("git rebase vendor/vams-main", 2),
    ("git tag -d cust-v2.7.0-1", 2),
    ("git merge -X theirs vendor/vams-main", 2),
    # vra is deny-by-default; gate waivers stay with the human
    ("vra resume r1 --confirm", 2),
    ("vra deploy --env test --build b1 --allow-replacements", 2),
    ("vra promote --from test --to prod --build b1 --approval a --emergency", 2),
    ("vra deploy --env prod --build b1 --accept-drift", 2),
    ("vra build --lane release --ref main --allow-missing-scanners", 2),
    ("vra restore --env dev --checkpoint c1 --writes-stopped", 2),
    ("vra data-copy --from prod --to test --approval a --abort-workflow-executions", 2),
    ("vra restore --env dev --checkpoint c1 --write-freeze manual", 0),
    ("vra unlock --env prod --reason x", 2),
    ("vra ledger import --env prod --from c.json", 2),
    ("vra ledger upgrade-config --env dev --to-ref v2.7.0 --write", 2),
    ("vra ledger upgrade-config --env dev --to-ref v2.7.0", 0),
    ("vra credential-process --env prod", 2),
    ("vra resume r1 --takeover", 2),
    ("vra restore --env dev --checkpoint c1 --include-auth-tables", 2),
    ("vra --home /h home migrate --from old", 2),
    ("vra --home /h home sync", 0),
    # IT role package: plan and verify are read-only, apply is human-only
    ("vra iam plan --env prod --mode precreated", 0),
    ("vra --json iam verify --env prod", 0),
    ("vra iam apply --env prod --from it-response.yaml", 2),
    ("vra --json iam apply --env prod --from it-response.yaml", 2),
    ("vra history verify --env prod", 0),
    ("vra status --all", 0),
    ("vra frobnicate", 2),
    ("echo 'unterminated", 2),
    # setup is agent-runnable (it writes a new home and ledger); --force is human-only
    ("vra setup --answers answers.yaml --non-interactive --json", 0),
    ("vra --json setup --join /srv/vra-home", 0),
    ("vra setup --answers answers.yaml --force", 2),
    ("vra --human-approval rec.yaml unlock --env dev --reason x", 2),
    # the engine in other spellings is still the engine
    ("python3 -m vra resume r1 --confirm", 2),
    ("python -m vra unlock --env prod --reason x", 2),
    ("uv run vra ledger import --env prod --from c.json", 2),
    ("python3 engine/vra/__main__.py resume r1 --takeover", 2),
    ("python3 -m vra --json status", 0),
    ("uv run vra status --all", 0),
    # commands whose target cannot be read, and bypass spellings
    ("A=aws; $A s3 ls", 2),
    ("eval 'aws s3 ls'", 2),
    ("source ./x.sh", 2),
    ("bash ./do.sh", 2),
    ("bash -lc 'aws s3 ls'", 2),
    ("uvx awscli s3 ls", 2),
    ("pipx run aws-cdk deploy", 2),
    ("watch aws s3 ls", 2),
    ("git -c alias.p='push --force' p origin main", 2),
    ("git branch -D main", 2),
    ("git update-ref -d refs/heads/main", 2),
    ("git push origin --prune", 2),
    ("git push --mirror origin", 2),
    ("git merge --strategy-option=theirs vendor/vams-main", 2),
    ("gh api -X DELETE repos/o/r/git/refs/heads/main", 2),
    ("gh api repos/o/r/pulls/3", 0),
    ("git branch --list", 0),
    ("echo 'aws s3 rm s3://b/k' | sh", 2),
    ("cat x.py | python3 -", 2),
    ("python3", 2),
    ("git log --oneline | head -5", 0),
    # wrappers with option values, command strings and opaque runners
    ("env -u VRA_AGENT aws s3 ls", 2),
    ("env -uVRA_AGENT aws s3 ls", 2),
    ("env --unset=VRA_AGENT aws s3 ls", 2),
    ("env -C /tmp aws s3 ls", 2),
    ("env -S 'aws s3 ls'", 2),
    ("env -i PATH=/usr/bin aws s3 ls", 2),
    ("nice -n 5 aws s3 ls", 2),
    ("nice -n5 aws s3 ls", 2),
    ("timeout -s KILL 5 aws s3 ls", 2),
    ("timeout -k 1 --signal=TERM 5 aws s3 ls", 2),
    ("setsid aws s3 ls", 2),
    ("setsid -f aws s3 ls", 2),
    ("flock /tmp/l aws s3 ls", 2),
    ("flock -w 5 /tmp/l aws s3 ls", 2),
    ("flock /tmp/l -c 'aws s3 ls'", 2),
    ("stdbuf -o L aws s3 ls", 2),
    ("ionice -c 3 aws s3 ls", 2),
    ("chrt -r 10 aws s3 ls", 2),
    ("taskset -c 1 aws s3 ls", 2),
    ("sudo -u root aws s3 ls", 2),
    ("xargs -I{} aws s3 cp {} .", 2),
    ("busybox sh -c 'aws s3 ls'", 2),
    ("script -qc 'aws s3 ls' /dev/null", 2),
    ("printf 'prod\\n' | script -qec 'env -u VRA_AGENT vra unlock prod' /dev/null", 2),
    ("unbuffer aws s3 ls", 2),
    ("su -c 'aws s3 ls'", 2),
    ("runuser -u x -- aws s3 ls", 2),
    ("expect -c 'spawn aws s3 ls'", 2),
    ("nice -n 5 env -u VRA_AGENT vra unlock --env prod --reason x", 2),
    # an unknown runner that names aws, cdk or git push: what it runs is not visible
    ("find . -name x -exec aws s3 ls ;", 2),
    ("mytool aws s3 ls", 2),
    ("npm exec -- cdk deploy", 2),
    ("foo git push --force origin main", 2),
    # the same wrappers around allowed commands still pass
    ("nice -n 5 vra status", 0),
    ("timeout -s KILL 60 vra status", 0),
    ("env -u FOO vra --json status", 0),
    ("echo aws", 0),
    ("grep -rn aws docs", 0),
    # vra pipeline tiers
    ("vra pipeline plan", 0),
    ("vra --json pipeline status", 0),
    ("vra pipeline sync-ledger", 0),
    ("vra pipeline frobnicate", 2),
    ("vra pipeline", 2),
]
# allowed by the guard for exit-code hosts (their ask lists prompt), "ask" for Cursor
ASK_CASES = ["vra pipeline deploy --plan pl-1", "vra pipeline push-source --lane release",
             "vra pipeline sync-ledger --deploy", "vra --json pipeline deploy"]

# Codex shell tools pass the command as an argv list
ARGV_CASES = [
    (["bash", "-lc", "aws s3 ls"], 2),
    (["bash", "-lc", "vra --json status"], 0),
    (["vra", "resume", "r1", "--confirm"], 2),
    (["git", "status"], 0),
]


def run(command, key: str = "tool_input") -> int:
    event = json.dumps({key: {"command": command}})
    return subprocess.run(
        [sys.executable, str(GUARD)], input=event, text=True, capture_output=True
    ).returncode


def test_cases():
    for command, expected in CASES:
        assert run(command) == expected, command


def test_argv_event_shape():
    for argv, expected in ARGV_CASES:
        assert run(argv) == expected, argv


def test_unreadable_events_are_blocked_and_non_shell_events_pass():
    proc = subprocess.run([sys.executable, str(GUARD)], input="not json", text=True, capture_output=True)
    assert proc.returncode == 2
    proc = subprocess.run([sys.executable, str(GUARD)], input="[1]", text=True, capture_output=True)
    assert proc.returncode == 2
    proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps({"tool_input": {"path": "x"}}),
                          text=True, capture_output=True)
    assert proc.returncode == 0


# ------------------------------------------------------------------ Cursor (hooks.json: beforeShellExecution,
# beforeMCPExecution; https://cursor.com/docs/hooks). The decision is JSON on stdout, always with exit 0.
def cursor(event: dict, *args: str, env: dict | None = None) -> tuple[int, dict]:
    import os
    proc = subprocess.run([sys.executable, str(GUARD), *args], input=json.dumps(event), text=True,
                          capture_output=True, env={**os.environ, **(env or {})})
    return proc.returncode, json.loads(proc.stdout)


def shell_event(command) -> dict:
    return {"hook_event_name": "beforeShellExecution", "command": command, "cwd": "/w", "sandbox": False,
            "conversation_id": "c1", "cursor_version": "2.4.0"}


def mcp_event(tool, tool_input="{}", server="vra") -> dict:
    ev = {"hook_event_name": "beforeMCPExecution", "tool_name": tool, "tool_input": tool_input,
          "command": "vra mcp", "conversation_id": "c1"}
    if server is not None:
        ev["mcp_server_name"] = server
    return ev


def test_cursor_shell_events_use_the_same_command_policy():
    for command, expected in CASES:
        rc, out = cursor(shell_event(command))
        assert rc == 0, command
        assert out["permission"] == ("allow" if expected == 0 else "deny"), command
        if expected:
            assert out["user_message"].startswith("Blocked by vra-guard:") and "vra MCP tools" in out["agent_message"]


def test_cursor_shell_event_without_a_command_string_is_denied():
    rc, out = cursor({"hook_event_name": "beforeShellExecution", "cwd": "/w"})
    assert (rc, out["permission"]) == (0, "deny")
    rc, out = cursor({"hook_event_name": "beforeShellExecution", "command": ["aws", "s3", "ls"]})
    assert out["permission"] == "deny"


def test_cursor_pre_tool_use_shell_shape():
    rc, out = cursor({"hook_event_name": "preToolUse", "tool_name": "Shell",
                      "tool_input": {"command": "npx cdk deploy --all", "working_directory": "/w"}})
    assert (rc, out["permission"]) == (0, "deny")
    rc, out = cursor({"hook_event_name": "preToolUse", "tool_name": "Shell", "tool_input": {"command": "vra status"}})
    assert out == {"permission": "allow"}


def test_cursor_mcp_events_follow_the_vra_tiers():
    assert cursor(mcp_event("status"))[1] == {"permission": "allow"}
    assert cursor(mcp_event("build", json.dumps({"lane": "release", "ref": "v2.7.0"})))[1]["permission"] == "allow"
    for tool in ("deploy", "promote", "rollback", "restore", "data_copy", "migrate"):
        assert cursor(mcp_event(tool))[1]["permission"] == "ask", tool
    assert cursor(mcp_event("some_new_tool"))[1]["permission"] == "ask"
    assert cursor(mcp_event("MCP:status"))[1]["permission"] == "allow"


def test_cursor_mcp_human_only_calls_and_unreadable_inputs_are_denied():
    for tool in ("unlock", "takeover", "setup", "ledger_import", "home_migrate", "credential_process"):
        assert cursor(mcp_event(tool))[1]["permission"] == "deny", tool
    assert cursor(mcp_event("resume", json.dumps({"run_id": "r1", "confirm": True})))[1]["permission"] == "deny"
    assert cursor(mcp_event("deploy", json.dumps({"env": "prod", "build": "b", "emergency": True})))[1][
        "permission"] == "deny"
    assert cursor(mcp_event("status", "{not json"))[1]["permission"] == "deny"
    assert cursor(mcp_event("status", "[1]"))[1]["permission"] == "deny"
    assert cursor(mcp_event("", "{}"))[1]["permission"] == "deny"


def test_cursor_other_mcp_servers_are_denied_unless_allowlisted():
    assert cursor(mcp_event("s3_list", server="aws-api"))[1]["permission"] == "deny"
    assert cursor(mcp_event("status", server=None))[1]["permission"] == "deny"
    env = {"VRA_GUARD_MCP_ALLOW": "docs, linear-*"}
    assert cursor(mcp_event("search", server="docs"), env=env)[1] == {"permission": "allow"}
    assert cursor(mcp_event("issue", server="linear-eu"), env=env)[1] == {"permission": "allow"}
    assert cursor(mcp_event("s3_list", server="aws-api"), env=env)[1]["permission"] == "deny"


def test_cursor_format_flag_and_unreadable_events():
    proc = subprocess.run([sys.executable, str(GUARD), "--format", "cursor"], input="not json", text=True,
                          capture_output=True)
    assert proc.returncode == 0 and json.loads(proc.stdout)["permission"] == "deny"
    rc, out = cursor({"command": "aws s3 ls"}, "--format", "cursor")  # no event name: still a shell command
    assert out["permission"] == "deny"
    rc, out = cursor({"tool_input": {"command": "vra status"}}, "--format=cursor")
    assert out == {"permission": "allow"}


def test_other_agents_keep_exit_codes_and_print_nothing():
    proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps({"tool_input": {"command": "aws s3 ls"}}),
                          text=True, capture_output=True)
    assert proc.returncode == 2 and proc.stdout == "" and "Blocked by vra-guard" in proc.stderr
    proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps({"hook_event_name": "PreToolUse",
                                                                          "tool_input": {"command": "vra status"}}),
                          text=True, capture_output=True)
    assert proc.returncode == 0 and proc.stdout == ""


def test_guard_mcp_tables_match_the_engine_and_the_claude_settings():
    import importlib.util
    spec = importlib.util.spec_from_file_location("vra_guard", GUARD)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)  # type: ignore[union-attr]
    try:
        from vra import mcp_server
    except ImportError:  # the guard runs without the engine; the package check installs it
        return
    cfg = mcp_server.Config(enable_writes=True, enable_gated=True)
    tools = {t.name: t.tier for t in mcp_server.tools(cfg, runner=lambda a, c: (0, "{}", ""),
                                                       gated_check=lambda e: True)}
    for name, tier in tools.items():
        if name not in guard.MCP_ALLOW | guard.MCP_ASK:
            assert guard.check_mcp("vra", name, "{}") == "ask", name  # a tool added later asks the person
            continue
        # write-tier tools that change a deployed env's data or stacks ask too
        want = "ask" if tier == mcp_server.GATED or name in ("deploy", "reindex") else "allow"
        assert guard.check_mcp("vra", name, "{}") == want, name
    assert guard.MCP_ALLOW | guard.MCP_ASK <= set(tools), "the guard lists a tool the server does not have"
    pkg = GUARD.parents[2]
    claude = json.loads((pkg / "adapters/claude/settings.json.example").read_text())["permissions"]
    mcp = lambda rules: {r.removeprefix("mcp__vra__") for r in rules if r.startswith("mcp__vra__")}  # noqa: E731
    assert mcp(claude["allow"]) == guard.MCP_ALLOW and mcp(claude["ask"]) == guard.MCP_ASK
    policy_spec = importlib.util.spec_from_file_location("check_agent_policy", pkg / "scripts/check_agent_policy.py")
    policy = importlib.util.module_from_spec(policy_spec)
    policy_spec.loader.exec_module(policy)  # type: ignore[union-attr]
    assert guard.MCP_HUMAN_TOOLS == policy.HUMAN_TOOLS and guard.MCP_HUMAN_PARAMS == policy.HUMAN_PARAMS


def test_pipeline_deploy_and_push_source_ask_a_person():
    for command in ASK_CASES:
        assert run(command) == 0, command                      # the host's ask list prompts (Claude, Codex, Kiro)
        rc, out = cursor(shell_event(command))
        assert out["permission"] == "ask", command
    for tool in ("pipeline_deploy", "pipeline_push_source"):
        assert cursor(mcp_event(tool))[1]["permission"] == "ask", tool
    for tool in ("pipeline_status", "pipeline_plan", "pipeline_sync_ledger"):
        assert cursor(mcp_event(tool))[1] == {"permission": "allow"}, tool


def test_cursor_pre_tool_use_mcp_calls_are_checked_and_ask_becomes_deny():
    """cloud agents run preToolUse (tool_name MCP:<tool>), not beforeMCPExecution; ask is not enforced
    there, so it is a deny."""
    ev = {"hook_event_name": "preToolUse", "tool_name": "MCP:status", "tool_input": {}}
    assert cursor(ev)[1] == {"permission": "allow"}
    for tool in ("MCP:deploy", "MCP:pipeline_deploy", "MCP:unlock"):
        assert cursor({**ev, "tool_name": tool})[1]["permission"] == "deny", tool
    assert cursor({**ev, "tool_name": "MCP:s3_list"})[1]["permission"] == "deny"   # unknown server
    assert cursor({**ev, "tool_name": "MCP:resume", "tool_input": {"run_id": "r", "takeover": True}})[1][
        "permission"] == "deny"


def test_guard_fails_closed_on_an_internal_error(monkeypatch, capsys):
    """an exception inside the guard blocks (exit 2; Cursor: deny), never allows."""
    import importlib.util
    import io
    spec = importlib.util.spec_from_file_location("vra_guard_fc", GUARD)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)  # type: ignore[union-attr]

    def boom(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(guard, "decide", boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool_input": {"command": "vra status"}})))
    assert guard.main([]) == 2
    assert "internal error" in capsys.readouterr().err
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"command": "vra status"})))
    assert guard.main(["--format", "cursor"]) == 0
    assert json.loads(capsys.readouterr().out)["permission"] == "deny"


def _oldest_python() -> str | None:
    import os
    import shutil
    for name in (os.environ.get("VRA_GUARD_OLDEST_PYTHON"), "python3.9"):
        if name and shutil.which(name):
            return shutil.which(name)
    return None


def test_guard_runs_on_python_3_9():
    """the guard parses and decides on the oldest supported python3 (3.9, the Amazon Linux 2023
    default). Skipped when no 3.9 interpreter is on PATH (scripts/check.sh runs it when one is)."""
    import pytest
    py = _oldest_python()
    if py is None:
        pytest.skip("no python3.9 on PATH (set VRA_GUARD_OLDEST_PYTHON)")
    for command, expected in [("aws s3 ls", 2), ("vra status", 0), ("env -u VRA_AGENT aws s3 ls", 2)]:
        p = subprocess.run([py, str(GUARD)], input=json.dumps({"tool_input": {"command": command}}), text=True,
                           capture_output=True)
        assert p.returncode == expected, (py, command, p.stderr)


def test_every_adapter_hook_fails_closed():
    """each hook command ends in `|| exit 2`, so a missing interpreter or guard file blocks too."""
    pkg = GUARD.parents[2]
    commands = []
    claude = json.loads((pkg / "adapters/claude/settings.json.example").read_text())
    commands += [h["command"] for m in claude["hooks"]["PreToolUse"] for h in m["hooks"]]
    codex = json.loads((pkg / "adapters/codex/hooks.json").read_text())
    commands += [h["command"] for m in codex["hooks"]["PreToolUse"] for h in m["hooks"]]
    for f in sorted((pkg / "adapters/kiro/.kiro/agents").glob("*.json")):
        doc = json.loads(f.read_text())
        hooks = doc.get("hooks") or {}
        items = hooks if isinstance(hooks, list) else [h for v in hooks.values() for h in v]
        commands += [(h.get("action") or {}).get("command") or h.get("command") for h in items]
    cursor_hooks = json.loads((pkg / "adapters/cursor/.cursor/hooks.json").read_text())["hooks"]
    commands += [h["command"] for v in cursor_hooks.values() for h in v]
    assert commands
    for c in commands:
        assert "vra-guard.py" in c and c.rstrip().endswith("|| exit 2"), c
    # a missing guard file blocks through the `|| exit 2`
    p = subprocess.run(["sh", "-c", 'python3 "/nonexistent/vra-guard.py" || exit 2'], input="{}", text=True,
                       capture_output=True)
    assert p.returncode == 2


def test_cursor_hooks_file_wires_the_guard_fail_closed():
    pkg = GUARD.parents[2]
    hooks = json.loads((pkg / "adapters/cursor/.cursor/hooks.json").read_text())
    assert hooks["version"] == 1
    for event in ("beforeShellExecution", "beforeMCPExecution"):
        (entry,) = hooks["hooks"][event]
        assert "adapters/shared/vra-guard.py" in entry["command"] and "--format cursor" in entry["command"]
        assert entry["failClosed"] is True and entry.get("matcher", "*") in ("", "*")


if __name__ == "__main__":
    failed = [(c, e, run(c)) for c, e in CASES if run(c) != e]
    for c, e, got in failed:
        print(f"FAIL expected {e} got {got}: {c}")
    print("all passed" if not failed else f"{len(failed)} failed")
    sys.exit(1 if failed else 0)
