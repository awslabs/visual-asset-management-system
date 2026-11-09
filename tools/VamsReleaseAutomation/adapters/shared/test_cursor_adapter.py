# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline tests of the Cursor adapter (adapters/cursor): it wires the same MCP tiers, guard and permission lists
as the other agents, stays a thin pointer to skill/, and installs with adapters/install.sh cursor."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import yaml

PKG = Path(__file__).resolve().parents[2]
CURSOR = PKG / "adapters/cursor/.cursor"
CLAUDE = PKG / "adapters/claude"


# ------------------------------------------------------------------ Claude settings -> Cursor CLI permissions
def _gen():
    import importlib.util
    spec = importlib.util.spec_from_file_location("gen_cli_json", PKG / "adapters/cursor/gen_cli_json.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


gen = _gen()
cursor_rules = gen.cursor_rules


def test_cursor_rule_conversion():
    assert gen.cursor_rule("mcp__vra__status") == "Mcp(vra:status)"
    assert gen.cursor_rule("Bash(vra status:*)") == "Shell(vra:status*)"
    assert gen.cursor_rule("Bash(aws:*)") == "Shell(aws)"
    assert gen.cursor_rule("Bash(vra * --confirm*)") == "Shell(vra:* --confirm*)"
    assert gen.cursor_rule("Edit(**/vra-home.yaml)") == "Write(**/vra-home.yaml)"
    assert gen.cursor_rule("Read(~/.aws/**)") == "Read(~/.aws/**)"
    assert gen.cursor_rule("Grep") is None
    assert gen.main(["--check"]) == 0, "cli.json is stale: run adapters/cursor/gen_cli_json.py"


def shell_matches(token: str, command: str) -> bool:
    """Our reading of Cursor's Shell(commandBase) / Shell(command:args-glob) (cursor.com/docs/cli/reference/permissions)."""
    m = re.fullmatch(r"Shell\(([^:]*)(?::(.*))?\)", token)
    if not m:
        return False
    words = shlex.split(command)
    if not words or not fnmatch.fnmatchcase(words[0], m.group(1)):
        return False
    return m.group(2) is None or fnmatch.fnmatchcase(" ".join(words[1:]), m.group(2))


def test_cli_permissions_are_the_claude_lists_in_cursor_tokens():
    claude = json.loads((CLAUDE / "settings.json.example").read_text())["permissions"]
    cli = json.loads((CURSOR / "cli.json").read_text())["permissions"]
    assert cli["allow"] == cursor_rules(claude["allow"])
    assert cli["deny"] == cursor_rules(claude["deny"])
    # Cursor has no `ask` list: the ask rules are neither allowed nor denied (Cursor prompts for them)
    assert not set(cursor_rules(claude["ask"])) & set(cli["allow"])


def test_cli_permissions_deny_every_human_only_sample():
    import importlib.util

    import pytest
    pytest.importorskip("vra")   # the policy tables come from the engine (Python 3.12+); the guard runs on 3.9
    spec = importlib.util.spec_from_file_location("check_agent_policy", PKG / "scripts/check_agent_policy.py")
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)  # type: ignore[union-attr]
    deny = json.loads((CURSOR / "cli.json").read_text())["permissions"]["deny"]
    for action, (_, sample) in {**policy.SAMPLES, **policy.WAIVERS}.items():
        assert any(shell_matches(t, sample) for t in deny), (action, sample)
    for sample in ("aws s3 ls", "npx cdk deploy --all", "git push --force origin main", "git rebase main"):
        assert any(shell_matches(t, sample) for t in deny), sample
    assert not any(shell_matches(t, "vra status --all") for t in deny)


# ------------------------------------------------------------------ MCP, hooks, rules, commands
def test_mcp_json_wires_vra_mcp_with_the_gated_tier_off_by_default():
    cfg = json.loads((CURSOR / "mcp.json").read_text())["mcpServers"]
    assert list(cfg) == ["vra"], "the guard recognizes the server by this key"
    srv = cfg["vra"]
    assert (srv["type"], srv["command"], srv["args"]) == ("stdio", "vra", ["mcp"])
    assert srv["env"]["VRA_AGENT"] == "1" and srv["env"]["VRA_MCP_ENABLE_WRITES"] == "true"
    # empty / unresolved when the variable is unset: the server treats anything but true as off
    assert srv["env"]["VRA_MCP_ENABLE_GATED"] == "${env:VRA_MCP_ENABLE_GATED}"


def test_hook_commands_run_the_shared_guard_from_the_package():
    hooks = json.loads((CURSOR / "hooks.json").read_text())["hooks"]
    assert set(hooks) == {"beforeShellExecution", "beforeMCPExecution", "preToolUse"}
    for (entry,) in hooks.values():
        # `|| exit 2`: a guard that cannot run blocks (R50-8)
        assert entry["command"] == 'python3 "${VRA_PACKAGE:-.}/adapters/shared/vra-guard.py" --format cursor || exit 2'
        assert entry["failClosed"] is True and entry["timeout"] <= 15
    # cloud agents skip beforeMCPExecution but run preToolUse for MCP tools (R50-21)
    assert hooks["preToolUse"][0]["matcher"] == "MCP:.*"


def _frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text()
    m = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", text, re.S)
    assert m, path
    return yaml.safe_load(m.group(1)), m.group(2)


def test_rules_are_thin_pointers_to_the_skill():
    rules = sorted((CURSOR / "rules").glob("*.mdc"))
    assert rules and not list((CURSOR / "rules").glob("*.md")), "Cursor ignores .md files in .cursor/rules"
    for r in rules:
        meta, body = _frontmatter(r)
        assert meta["alwaysApply"] is True and meta["description"]
        assert "skill/SKILL.md" in body and len(body.splitlines()) <= 30, r.name
        assert "W4-deploy" not in body, "no workflow steps in a rule: they live in skill/workflows"


def test_commands_are_manual_skills_matching_the_claude_commands():
    claude = {p.stem for p in (CLAUDE / ".claude/commands").glob("*.md")}
    skills = {p.parent.name: p for p in (CURSOR / "skills").glob("*/SKILL.md")}
    assert set(skills) == claude
    for name, path in skills.items():
        meta, body = _frontmatter(path)
        assert meta["name"] == name and meta["description"]
        assert meta["disable-model-invocation"] is True, "a command runs only when typed as /" + name
        assert "skill/SKILL.md" in body
        for wf in re.findall(r"skill/workflows/[\w.-]+\.md", body):
            assert (PKG / wf).is_file(), (name, wf)


# ------------------------------------------------------------------ install.sh cursor
def test_install_cursor_copies_the_files_and_links_the_skill(tmp_path):
    r = subprocess.run(["bash", str(PKG / "adapters/install.sh"), "cursor", "--dest", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for rel in ("mcp.json", "cli.json", "rules/vra-release-automation.mdc", "skills/vra-deploy/SKILL.md"):
        assert (tmp_path / ".cursor" / rel).read_text() == (CURSOR / rel).read_text(), rel
    # the hooks name the package by its absolute path (Cursor's app environment has no VRA_PACKAGE, R50-21)
    hooks = (tmp_path / ".cursor/hooks.json").read_text()
    assert "${VRA_PACKAGE" not in hooks and f'{PKG}/adapters/shared/vra-guard.py' in hooks
    json.loads(hooks)
    link = tmp_path / ".cursor/skills/vams-release-automation"
    assert link.is_symlink() and link.resolve() == (PKG / "skill").resolve()
    (tmp_path / ".cursor/mcp.json").write_text("{}")
    r = subprocess.run(["bash", str(PKG / "adapters/install.sh"), "cursor", "--dest", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "kept" in r.stdout and (tmp_path / ".cursor/mcp.json").read_text() == "{}"
    r = subprocess.run(["bash", str(PKG / "adapters/install.sh"), "cursor", "--dest", str(tmp_path), "--overwrite"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and (tmp_path / ".cursor/mcp.json").read_text() == (CURSOR / "mcp.json").read_text()
    assert "VRA_PACKAGE" in r.stdout


# ------------------------------------------------------------------ vra-cron.sh --agent cursor
def _fake_bin(tmp_path: Path, vra_exit: int) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "vra").write_text(f"#!/usr/bin/env bash\necho '{{\"exitCode\": {vra_exit}}}'\nexit {vra_exit}\n")
    (bin_dir / "agent").write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"${HOME}/agent-argv.txt\"\n"
        "env | grep '^VRA_' | sort > \"${HOME}/agent-env.txt\"\necho '{}'\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("VRA_")}
    env.update(PATH=f"{bin_dir}:{os.environ['PATH']}", HOME=str(tmp_path), VRA_PACKAGE=str(PKG),
               VRA_CRON_OUT=str(tmp_path / "out"))
    return env


def test_cron_calls_cursor_headless_with_the_gated_tier_off(tmp_path):
    env = _fake_bin(tmp_path, 20)
    r = subprocess.run(["bash", str(PKG / "adapters/shared/vra-cron.sh"), "--agent", "cursor"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 20, r.stderr
    argv = (tmp_path / "agent-argv.txt").read_text().split("\n")
    assert argv[:1] == ["-p"] and "--trust" in argv and "--approve-mcps" in argv
    assert "--force" not in argv and "-f" not in argv          # cli.json allow is the allowlist (R50-10)
    assert argv[argv.index("--workspace") + 1] == str(PKG)
    assert argv[argv.index("--output-format") + 1] == "json"
    agent_env = (tmp_path / "agent-env.txt").read_text()
    assert "VRA_MCP_ENABLE_GATED=false" in agent_env and "VRA_AGENT=1" in agent_env
    assert "VRA_AGENT_SESSION=cron-cursor" in agent_env


def test_cron_canary_stops_triage_when_the_guard_does_not_block(tmp_path):
    """A guard that allows the aws probe, or cannot run, stops the headless agent (R50-10)."""
    env = _fake_bin(tmp_path, 20)
    fake_pkg = tmp_path / "pkg"
    (fake_pkg / "adapters/shared").mkdir(parents=True)
    (fake_pkg / "adapters/shared/vra-guard.py").write_text("import sys\nsys.exit(0)\n")   # allows everything
    env["VRA_PACKAGE"] = str(fake_pkg)
    r = subprocess.run(["bash", str(PKG / "adapters/shared/vra-cron.sh"), "--agent", "cursor"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 20 and "canary failed" in r.stderr
    assert not (tmp_path / "agent-argv.txt").exists()
    (fake_pkg / "adapters/shared/vra-guard.py").write_text("raise SystemExit(1)\n")       # crashes
    r = subprocess.run(["bash", str(PKG / "adapters/shared/vra-cron.sh"), "--agent", "cursor"], env=env,
                       capture_output=True, text=True)
    assert "the guard did not run" in r.stderr and not (tmp_path / "agent-argv.txt").exists()


def test_cron_never_calls_cursor_when_nothing_needs_judgment(tmp_path):
    env = _fake_bin(tmp_path, 0)
    r = subprocess.run(["bash", str(PKG / "adapters/shared/vra-cron.sh"), "--agent", "cursor"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0 and not (tmp_path / "agent-argv.txt").exists()
