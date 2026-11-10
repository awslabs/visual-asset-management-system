# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Human-only operations, enforced in the engine (not only in agent guards).

Each one either records that a person did something (``--confirm``, ``--writes-stopped``), waives a gate
(``--allow-missing-scanners``), takes state away from another run (``unlock``, ``resume --takeover``), stops work
in the env (``--abort-workflow-executions``), or rewrites the ledger's truth (``ledger import``,
``ledger upgrade-config --write``, ``setup --force``, ``home migrate``).

Rules:

- an agent (``VRA_AGENT=1``) or the MCP server (``VRA_MCP=1``) is always refused (exit 20);
- otherwise a person confirms at their own interactive terminal by typing the word shown, but only when no
  agent or CI marker is set (``AGENT_MARKERS``, ``CI_MARKERS``), stdin and stderr are TTYs, the TTY is not a
  pseudo terminal of a wrapper (``script``, ``unbuffer``, ``expect``) and no agent CLI runs in the parent process
  chain (``AGENT_PROGRAMS``); a chain that cannot be read counts as an agent;
- in every other case (a CI job, an agent with VRA_AGENT cleared) an approval record is required whose subject
  is ``human:<action>`` and whose scope names the action (``--approval``), checked like every other approval
  (approvers, SoD, expiry, single use).
"""

from __future__ import annotations

import os
import sys
from typing import Callable

from vra.errors import NeedsHuman

HUMAN_ONLY = {
    "confirm": "`vra resume --confirm` records that a HUMAN did manual work",
    "allow-missing-scanners": "`--allow-missing-scanners` waives the build's security scanners",
    "writes-stopped": "`--writes-stopped` records that a HUMAN stopped writes to the env",
    "unlock": "`vra unlock` releases a lock another run holds",
    "takeover": "`vra resume --takeover` takes a run over from another machine",
    "abort-workflow-executions": "`--abort-workflow-executions` stops running VAMS workflow executions",
    "ledger-import": "`vra ledger import` replaces an env's VAMS config",
    "upgrade-config-write": "`vra ledger upgrade-config --write` rewrites an env's VAMS config",
    "setup-force": "`vra setup --force` overwrites ledger files",
    "home-migrate": "`vra home migrate` creates the shared home from an old ledger",
    "iam-apply": "`vra iam apply` wires the IAM roles IT returned into an env's VAMS config",
}

def is_agent() -> bool:
    return os.environ.get("VRA_AGENT") == "1" or os.environ.get("VRA_MCP") == "1"


def _tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


# Environment variables agent hosts and CI systems set. Any of them present: a typed confirmation is not
# accepted, only an approval record. `env -u VRA_AGENT` clears one, not all of them, and not the
# process chain below.
AGENT_MARKERS = ("VRA_AGENT", "VRA_MCP", "VRA_AGENT_SESSION", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
                 "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED", "CODEX_THREAD_ID", "CURSOR_AGENT",
                 "KIRO_AGENT", "KIRO_SESSION_ID", "Q_SESSION_ID", "AIDER_MODEL", "GEMINI_CLI", "GOOSE_SESSION")
CI_MARKERS = ("CI", "GITHUB_ACTIONS", "CODEBUILD_BUILD_ID", "GITLAB_CI", "JENKINS_URL", "TF_BUILD", "BUILDKITE",
              "CIRCLECI", "TEAMCITY_VERSION", "BITBUCKET_BUILD_NUMBER")
# programs that run an agent (process names or the script a node / python runtime runs)
AGENT_PROGRAMS = {"claude", "codex", "kiro", "kiro-cli", "kiro-cli-chat", "cursor-agent", "q", "qchat", "aider",
                  "gemini", "goose", "opencode", "amp", "copilot"}
# programs that give a command a pseudo terminal of their own: a TTY under them is not a person's login TTY
PTY_WRAPPERS = {"script", "unbuffer", "expect", "tclsh", "socat", "empty", "ttyd", "pexpect", "busybox", "faketty"}


def _proc_info(pid: int) -> tuple[int, list[str]] | None:
    """(parent pid, [process name, argv...]) of ``pid``; None when it cannot be read."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            stat = fh.read()
        comm = stat[stat.index("(") + 1:stat.rindex(")")]
        ppid = int(stat[stat.rindex(")") + 2:].split()[1])
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                argv = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
        except OSError:
            argv = []
        return ppid, [comm, *argv]
    except (OSError, ValueError, IndexError):
        pass
    import subprocess
    try:   # no /proc (macOS): ps
        out = subprocess.run(["ps", "-o", "ppid=", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5, check=False).stdout.strip()
        args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True, timeout=5,
                              check=False).stdout.strip()
        ppid_s, _, comm = out.partition(" ")
        return int(ppid_s), [comm.strip(), *args.split()]
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _names(info: list[str]) -> set[str]:
    """Program names of a process: its name, argv[0] and the script a runtime (node, python, deno, bun) runs."""
    out = {os.path.basename(x).lower() for x in info[:2] if x}
    if len(info) > 2 and os.path.basename(info[1]).lower().rstrip("0123456789.") in ("node", "python", "deno",
                                                                                   "bun"):
        script = next((a for a in info[2:] if not a.startswith("-")), "")
        out.add(os.path.basename(script).lower())
        parts = script.replace("\\", "/").lower().split("/")
        out |= {p for p in parts if p in AGENT_PROGRAMS or p in ("@anthropic-ai", "claude-code", "@openai")}
        if "claude-code" in parts:
            out.add("claude")
    return out


def _typed_confirmation_blocker() -> str | None:
    """Why a typed confirmation is not accepted here (None: a person at their own terminal). Fails closed: a
    process chain that cannot be read blocks it."""
    for var in AGENT_MARKERS:
        if os.environ.get(var):
            return f"agent marker {var} is set"
    for var in CI_MARKERS:
        if os.environ.get(var) not in (None, "", "false", "0"):
            return f"CI marker {var} is set"
    if not _tty():
        return "stdin / stderr is not a terminal"
    pid, seen = os.getpid(), 0
    while pid > 1 and seen < 64:
        info = _proc_info(pid)
        if info is None:
            return "the parent process chain cannot be read"
        ppid, names = info[0], _names(info[1])
        hit = names & AGENT_PROGRAMS
        if hit:
            return f"the parent process chain runs {sorted(hit)[0]}"
        if pid != os.getpid() and names & PTY_WRAPPERS:
            return f"the terminal is a pseudo terminal of {sorted(names & PTY_WRAPPERS)[0]}, not a login TTY"
        pid, seen = ppid, seen + 1
    return None


def _default_ask(question: str) -> str:
    sys.stderr.write(question)
    sys.stderr.flush()
    return sys.stdin.readline().strip()


def require(ctx, action: str, env: str | None = None, approval: str | None = None,
            ask: Callable[[str], str] | None = None, tty: bool | None = None) -> dict:
    """Allow a human-only ``action`` or raise NeedsHuman. Returns how it was authorized (recorded by callers)."""
    why = HUMAN_ONLY.get(action, action)
    if is_agent():
        raise NeedsHuman(f"{why}; it is human-only and an agent cannot run it (VRA_AGENT / MCP). Ask the human "
                         f"to run the command", {"humanOnly": action})
    if tty is None:
        tty = _tty() and not getattr(ctx, "non_interactive", False)
    blocker = _typed_confirmation_blocker() if tty else None
    if blocker:
        # an agent may hold a pseudo terminal (`script -qc`, `unbuffer`) or run with VRA_AGENT cleared: only an
        # approval record (approvers, separation of duties, single use) authorizes the action then
        tty = False
        if not approval:
            raise NeedsHuman(f"{why}; a typed confirmation is not accepted here ({blocker}). Pass --approval naming a "
                             f"record with subject human:{action} and scope [{action}]", {"humanOnly": action,
                                                                                         "why": blocker})
    if tty:
        word = env or action
        answer = (ask or _default_ask)(f"{why}. Type '{word}' to confirm: ")
        if answer.strip() != word:
            raise NeedsHuman(f"{action} not confirmed", {"humanOnly": action})
        return {"action": action, "by": getattr(ctx, "initiator", None), "how": "tty"}
    if not approval or not env:
        raise NeedsHuman(f"{why}; without an interactive terminal it needs --approval naming a record with subject "
                         f"human:{action} and scope [{action}]" + ("" if env else " (and an env)"),
                         {"humanOnly": action})
    from vra import approvals, util
    rec = approvals.check(ctx, env, f"human:{action}", action, approval, force_required=True)
    use = f"human:{action}:{util.now_iso()}"
    got = ctx.backend.consumed.claim(env, rec["sha256"], {"runId": use, "env": env, "action": action,
                                                          "approver": rec.get("approver"), "at": util.now_iso()})
    if got.get("runId") != use:
        raise NeedsHuman(f"approval {rec.get('path')} was already used ({got.get('action')}, {got.get('runId')})")
    return {"action": action, "by": getattr(ctx, "initiator", None), "how": "approval",
            "approval": rec.get("path"), "approvalSha256": rec.get("sha256"), "approver": rec.get("approver")}


__all__ = ["HUMAN_ONLY", "require", "is_agent"]
