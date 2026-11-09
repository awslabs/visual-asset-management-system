#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check that the agent policy is the same everywhere it is written down.

The engine's human-only actions (``vra.human.HUMAN_ONLY``) plus the approval-scoped gate waivers are the
reference set. For each of them this check asserts that:

- ``skill/SKILL.md`` names it in its human-only list;
- no MCP tool exposes it (no tool is named after it, no tool takes it as a parameter);
- the shared guard blocks a sample command for it;
- every Kiro agent denies the sample command, the Claude Code settings deny it, and the Cursor CLI permissions
  (``adapters/cursor/.cursor/cli.json``) deny it.

It also checks that the Cursor hooks run the guard fail-closed before shell and MCP calls, that the guard's MCP
human-only tables equal this check's, and runs every ``vra ...`` command in ``skill/workflows/*.md`` through the
guard: a workflow step the guard blocks must be on a line that hands the command to the human (it mentions
"human", "[HUMAN]" or "yourself").

Usage: check_agent_policy.py <package-root>
"""
from __future__ import annotations

import fnmatch
import importlib.util
import json
import re
import shlex
import sys
from pathlib import Path

from vra import human, mcp_server

# action -> (flag or command words the skill names, a sample command an agent might run)
SAMPLES: dict[str, tuple[str, str]] = {
    "confirm": ("--confirm", "vra resume r1 --confirm"),
    "allow-missing-scanners": ("--allow-missing-scanners", "vra build --lane release --ref v1 --allow-missing-scanners"),
    "writes-stopped": ("--writes-stopped", "vra restore --env dev --checkpoint c1 --writes-stopped"),
    "unlock": ("vra unlock", "vra unlock --env dev --reason x"),
    "takeover": ("--takeover", "vra resume r1 --takeover"),
    "abort-workflow-executions": ("--abort-workflow-executions",
                                  "vra deploy --env dev --build b1 --abort-workflow-executions"),
    "ledger-import": ("vra ledger import", "vra ledger import --env dev --from c.json"),
    "upgrade-config-write": ("--write", "vra ledger upgrade-config --env dev --to-ref v1 --write"),
    "setup-force": ("--force", "vra setup --answers a.yaml --force"),
    "home-migrate": ("vra home migrate", "vra home migrate --from old --to new"),
    "iam-apply": ("vra iam apply", "vra iam apply --env prod --from it-response.yaml"),
}
# Human-only actions the engine already enforces whose skill / guard / Kiro / Claude / Cursor entries another
# build lane adds (handoffs/v050-lane-notes.md): sample, and who adds it. Their gaps print as "awaiting" and do
# not fail the check. Once the skill, the guard and every adapter name the action, move it to SAMPLES.
# None is open in 0.5.0 (iam-apply moved to SAMPLES).
AWAITING: dict[str, tuple[str, str, str]] = {}
# gate waivers that need an approval record scoped to them; agents never pass them
WAIVERS: dict[str, tuple[str, str]] = {
    "emergency": ("--emergency", "vra deploy --env prod --build b1 --emergency"),
    "allow-replacements": ("--allow-replacements", "vra deploy --env dev --build b1 --allow-replacements"),
    "accept-drift": ("--accept-drift", "vra deploy --env prod --build b1 --accept-drift"),
    "include-auth-tables": ("--include-auth-tables", "vra restore --env dev --checkpoint c1 --include-auth-tables"),
}
HANDOFF = re.compile(r"human|\[HUMAN\]|yourself|themselves", re.I)


def _guard(root: Path):
    spec = importlib.util.spec_from_file_location("vra_guard", root / "adapters/shared/vra-guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def blocked(guard, command: str) -> bool:
    try:
        guard.check_line(command)
    except guard.Blocked:
        return True
    return False


def kiro_denies(cfg: dict, command: str) -> bool:
    shell = (cfg.get("toolsSettings") or {}).get("shell")
    if shell is None:
        return "shell" not in (cfg.get("tools") or [])  # no shell at all
    return any(re.fullmatch(p, command) for p in shell.get("deniedCommands") or [])


def claude_denies(deny: list[str], command: str) -> bool:
    for rule in deny:
        m = re.fullmatch(r"Bash\((.*)\)", rule)
        if not m:
            continue
        pat = m.group(1)
        if pat.endswith(":*"):
            if command == pat[:-2] or command.startswith(pat[:-2] + " "):
                return True
        elif re.fullmatch(re.escape(pat).replace(r"\*", ".*"), command):
            return True
    return False


def cursor_denies(deny: list[str] | None, command: str) -> bool:
    """Cursor CLI permission ``Shell(<command glob>[:<args glob>])`` (cursor.com/docs/cli permissions)."""
    if deny is None:
        return True   # no Cursor adapter in this tree
    words = shlex.split(command)
    for tok in deny:
        m = re.fullmatch(r"Shell\(([^:]*)(?::(.*))?\)", tok)
        if m and words and fnmatch.fnmatchcase(words[0], m.group(1)) and (
                m.group(2) is None or fnmatch.fnmatchcase(" ".join(words[1:]), m.group(2))):
            return True
    return False


def cursor_problems(root: Path, guard) -> list[str]:
    out = []
    hooks_p = root / "adapters/cursor/.cursor/hooks.json"
    if hooks_p.exists():
        hooks = json.loads(hooks_p.read_text(encoding="utf-8")).get("hooks") or {}
        for ev in ("beforeShellExecution", "beforeMCPExecution"):
            if not any("vra-guard.py" in h.get("command", "") and h.get("failClosed") for h in hooks.get(ev, [])):
                out.append(f"Cursor hooks.json: {ev} does not run vra-guard.py fail-closed")
    if getattr(guard, "MCP_HUMAN_TOOLS", HUMAN_TOOLS) != HUMAN_TOOLS or \
            getattr(guard, "MCP_HUMAN_PARAMS", HUMAN_PARAMS) != HUMAN_PARAMS:
        out.append("vra-guard.py MCP human-only tables differ from check_agent_policy")
    return out


def workflow_commands(text: str) -> list[tuple[int, str, str]]:
    """(line number, command, the line with its neighbours: a wrapped sentence spans lines)."""
    lines = text.splitlines()
    out = []
    for n, line in enumerate(lines, 1):
        context = " ".join(lines[max(0, n - 2):n + 1])
        for m in re.finditer(r"`(vra [^`]+)`", line):
            out.append((n, m.group(1), context))
    return out


# MCP tool parameters and tool names that would expose a human-only action
HUMAN_PARAMS = {"confirm", "takeover", "force", "write", "writes_stopped", "allow_missing_scanners",
                "abort_workflow_executions", "emergency", "allow_replacements", "accept_drift", "include_auth_tables",
                "human_approval"}
HUMAN_TOOLS = {"unlock", "takeover", "setup", "ledger_import", "upgrade_config", "home_migrate", "credential_process",
               "mcp"}


def mcp_exposure() -> list[str]:
    """Every registered MCP tool (all tiers on): none is human-only and none takes a human-only parameter."""
    import inspect
    cfg = mcp_server.Config(enable_writes=True, enable_gated=True)
    out = []
    for t in mcp_server.tools(cfg, runner=lambda argv, c: (0, "{}", ""), gated_check=lambda env: True):
        if t.name in HUMAN_TOOLS:
            out.append(f"MCP tool {t.name!r} exposes a human-only action")
        bad = HUMAN_PARAMS & set(inspect.signature(t.fn).parameters)
        if bad:
            out.append(f"MCP tool {t.name!r} takes human-only parameter(s) {sorted(bad)}")
    return out


AWAITED: dict[str, list[str]] = {}   # action -> its open gaps, filled by check()

# State an agent must never write itself (R50-4): IT apply records, pipeline plans, the env overlays `vra iam
# apply` writes. Sample paths every adapter's write denies must match.
PROTECTED_WRITES = ("/srv/vra-home/state/iam/prod/applied.json", "/srv/vra-home/state/iam/prod/1/package.json",
                    "/srv/vra-home/state/pipeline/plans/pl-1.json",
                    "/srv/vra-home/ledger/envs/prod/overlay/infra/config/config.json")
# `vra pipeline` (R50-15): agents run these; deploy and push-source ask a person (never in an allow list)
PIPELINE_AGENT = ("vra pipeline plan", "vra pipeline status", "vra pipeline sync-ledger")
PIPELINE_ASK = ("vra pipeline deploy --plan pl-1", "vra pipeline push-source --lane release",
                "vra pipeline sync-ledger --deploy")


def _glob_hit(patterns: list[str], path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, p.replace("**/", "*").replace("/**", "/*")) for p in patterns)


def protected_state_problems(root: Path, kiro: dict, claude: dict, cursor_cfg: dict | None) -> list[str]:
    out = []
    edits = [m.group(1) for r in claude["permissions"]["deny"] if (m := re.fullmatch(r"Edit\((.*)\)", r))]
    cur = [m.group(1) for r in ((cursor_cfg or {}).get("permissions") or {}).get("deny") or []
           if (m := re.fullmatch(r"Write\((.*)\)", r))]
    for path in PROTECTED_WRITES:
        if not _glob_hit(edits, path):
            out.append(f"Claude settings do not deny agent edits of {path}")
        if cursor_cfg is not None and not _glob_hit(cur, path):
            out.append(f"Cursor cli.json does not deny agent writes of {path}")
        for name, cfg in kiro.items():
            write = ((cfg.get("toolsSettings") or {}).get("write") or {})
            if "write" in (cfg.get("tools") or []) and not _glob_hit(write.get("deniedPaths") or [], path):
                out.append(f"Kiro {name} does not deny writes of {path}")
    return out


def hook_commands(root: Path, kiro: dict, claude: dict) -> list[tuple[str, str]]:
    out = [("Claude", h["command"]) for m in (claude.get("hooks") or {}).get("PreToolUse") or [] for h in m["hooks"]]
    codex_p = root / "adapters/codex/hooks.json"
    if codex_p.exists():
        codex = json.loads(codex_p.read_text(encoding="utf-8"))
        out += [("Codex", h["command"]) for m in codex["hooks"]["PreToolUse"] for h in m["hooks"]]
    for name, cfg in kiro.items():
        hooks = cfg.get("hooks") or {}
        items = hooks if isinstance(hooks, list) else [h for v in hooks.values() for h in v]
        out += [(f"Kiro {name}", (h.get("action") or {}).get("command") or h.get("command") or "") for h in items]
    cur_p = root / "adapters/cursor/.cursor/hooks.json"
    if cur_p.exists():
        cur = json.loads(cur_p.read_text(encoding="utf-8")).get("hooks") or {}
        out += [("Cursor", h.get("command", "")) for v in cur.values() for h in v]
    return out


def fail_closed_problems(root: Path, kiro: dict, claude: dict) -> list[str]:
    """R50-8: every hook runs the guard and ends in `|| exit 2` (a guard that cannot run blocks)."""
    return [f"{who} hook does not fail closed (`... vra-guard.py || exit 2`): {cmd!r}"
            for who, cmd in hook_commands(root, kiro, claude)
            if "vra-guard.py" in cmd and not cmd.rstrip().endswith("|| exit 2")]


def pipeline_problems(guard, kiro: dict, claude: dict) -> list[str]:
    out = []
    allow = claude["permissions"]["allow"]
    ask = claude["permissions"]["ask"]
    for cmd in PIPELINE_AGENT:
        if blocked(guard, cmd):
            out.append(f"vra-guard blocks `{cmd}`, which agents may run")
        if not claude_denies(allow, cmd):      # same matcher: "is the command in this Bash list"
            out.append(f"Claude settings do not allow `{cmd}`")
    for cmd in PIPELINE_ASK:
        if blocked(guard, cmd) or guard.check_line(cmd) != "ask":
            out.append(f"vra-guard does not ask a person before `{cmd}`")
        if not claude_denies(ask, cmd):
            out.append(f"Claude settings do not ask before `{cmd}`")
        for name, cfg in kiro.items():
            shell = (cfg.get("toolsSettings") or {}).get("shell") or {}
            if any(re.fullmatch(p, cmd) for p in shell.get("allowedCommands") or []):
                out.append(f"Kiro {name} runs `{cmd}` without asking")
    return out


def adoption_gaps(action: str, words: str, sample: str, skill_list: str, guard, kiro: dict, deny: list,
                  cursor: list[str] | None = None) -> list[str]:
    out = []
    if not cursor_denies(cursor, sample):
        out.append(f"Cursor cli.json does not deny {sample!r} ({action})")
    if not re.search(re.escape(words) + r"(?![\w-])", skill_list):
        out.append(f"SKILL.md human-only list does not name {words} ({action})")
    if not blocked(guard, sample):
        out.append(f"vra-guard allows {sample!r} ({action})")
    for name, cfg in kiro.items():
        if not kiro_denies(cfg, sample):
            out.append(f"Kiro {name} does not deny {sample!r} ({action})")
    if not claude_denies(deny, sample):
        out.append(f"Claude settings do not deny {sample!r} ({action})")
    return out


def check(root: Path) -> list[str]:
    problems: list[str] = []
    guard = _guard(root)
    skill = (root / "skill/SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"<!-- human-only:start -->(.*?)<!-- human-only:end -->", skill, re.S)
    skill_list = m.group(1) if m else ""
    if not m:
        problems.append("skill/SKILL.md has no <!-- human-only:start --> ... <!-- human-only:end --> list")
    unknown = set(human.HUMAN_ONLY) - set(SAMPLES) - set(AWAITING)
    for a in sorted(unknown):
        problems.append(f"engine human-only action {a!r} has no sample in check_agent_policy.SAMPLES")

    kiro = {p.name: json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((root / "adapters/kiro/.kiro/agents").glob("*.json"))}
    claude = json.loads((root / "adapters/claude/settings.json.example").read_text(encoding="utf-8"))
    deny = claude["permissions"]["deny"]

    cli_p = root / "adapters/cursor/.cursor/cli.json"
    cursor = json.loads(cli_p.read_text(encoding="utf-8"))["permissions"]["deny"] if cli_p.exists() else None

    problems += mcp_exposure()
    problems += cursor_problems(root, guard)
    problems += protected_state_problems(root, kiro, claude,
                                         json.loads(cli_p.read_text(encoding="utf-8")) if cli_p.exists() else None)
    problems += fail_closed_problems(root, kiro, claude)
    problems += pipeline_problems(guard, kiro, claude)
    for action, (words, sample) in {**SAMPLES, **WAIVERS}.items():
        problems += adoption_gaps(action, words, sample, skill_list, guard, kiro, deny, cursor)
    for action, (words, sample, _who) in AWAITING.items():
        AWAITED[action] = adoption_gaps(action, words, sample, skill_list, guard, kiro, deny, cursor)

    for wf in sorted((root / "skill/workflows").glob("*.md")):
        for n, cmd, line in workflow_commands(wf.read_text(encoding="utf-8")):
            if blocked(guard, cmd) and not HANDOFF.search(line):
                problems.append(f"{wf.relative_to(root)}:{n}: the guard blocks `{cmd}`, and the line does not "
                                f"hand it to the human")
    return problems


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    problems = check(root)
    for p in problems:
        print(f"  {p}")
    for action, gaps in AWAITED.items():
        if gaps:
            print(f"  awaiting ({AWAITING[action][2]}): {len(gaps)} place(s) do not name {action} yet; the "
                  f"engine refuses it for agents already")
    print(f"agent policy check: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
