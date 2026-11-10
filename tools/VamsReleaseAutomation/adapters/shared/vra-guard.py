#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pre-tool-use guard shared by the Claude Code, Codex, Kiro and Cursor adapters.

Reads the hook event JSON on stdin, looks at the shell command the agent is about to run, and blocks
commands that would bypass the `vra` engine or waive one of its gates:

- direct AWS / CDK access in any spelling: `aws`, `/usr/bin/aws`, `env aws`, `bash -c "aws ..."`,
  `python3 -c 'import boto3 ...'`, `python -m awscli`, `uvx awscli`, `pipx run aws-cdk`, `cdk`, `npx cdk`;
- commands whose real target cannot be read from the text: `eval`, `source`, `.`, `watch`, a variable as
  the command name (`$A s3 ls`), a shell given a script file (`bash ./do.sh`);
- force pushes, ref deletion, history rewrites and tag moves, also behind `git -c k=v` / `git -C dir`, git
  aliases defined on the command line, `git update-ref -d`, `git branch -D` and `gh api` writes to refs;
- `vra` itself is deny-by-default, in every spelling (`vra`, `python -m vra`, `uv run vra`,
  `.../vra/__main__.py`): only the subcommands below are allowed, and the human-only flags and commands
  are left to the human.

The event shapes of all four agents are accepted: `{"tool_input": {"command": "<string>"}}` (Claude Code,
Kiro, Cursor preToolUse) and the same with `command` as an argv list (Codex shell tools); Cursor's
`beforeShellExecution` (`{"command": "<string>", "cwd": ...}`) and `beforeMCPExecution`
(`{"tool_name", "tool_input": "<json>", "mcp_server_name", ...}`).

Exit codes follow the hook protocols: 0 = allow, 2 = block (the message on stderr is returned to the
model). Cursor (`--format cursor`, or a Cursor hook event name) gets a JSON decision on stdout instead, always
with exit 0: `{"permission": "allow" | "deny" | "ask", "user_message", "agent_message"}`. MCP calls (Cursor
only) follow the vra server's tiers: read and write tools are allowed, deploy and the approval-gated tools ask
the person, anything else on the vra server is denied, and another MCP server is denied unless
VRA_GUARD_MCP_ALLOW lists it (comma-separated names, `*` wildcards). A command the guard cannot parse is
BLOCKED (deny by default). This guard is a second layer: IAM roles, the engine's gates and its refusal of
human-only actions for agents (VRA_AGENT=1, VRA_MCP=1) are the real controls.

Wrappers are read with their option tables (`env -u X`, `nice -n 5`, `timeout -s KILL 5`, `stdbuf -o L`,
`flock <file>`, `setsid`, `chrt`, `ionice`, `taskset`, `sudo -u`, `xargs -I`), and `env -S '...'` /
`flock -c '...'` are checked as command lines. Programs that hide or re-host the command (`script`, `busybox`,
`su`, `runuser`, `unbuffer`, `expect`, ...) are blocked. Any other program whose arguments name `aws`, `cdk` or
`git push` is blocked too: the guard cannot tell what it runs.

Python 3.9+ (`from __future__ import annotations`). The guard FAILS CLOSED: an internal error blocks (exit 2,
Cursor `deny`); the adapters' hook commands end in `|| exit 2` so a missing interpreter or file blocks as well.
"""

from __future__ import annotations

import fnmatch

import json
import os
import re
import shlex
import sys

VRA_ALLOWED = {"status", "report", "ledger", "preflight", "sync", "merge", "build", "test", "deploy", "promote",
               "backup", "restore", "migrate", "rollback", "data-copy", "reindex", "watch", "resume", "history", "home",
               "setup", "iam", "pipeline"}
# `vra pipeline`: agents plan, read status and re-plan (sync-ledger without --deploy); deploying the pipeline
# stack and feeding its source ask a person (the adapters' ask lists; Cursor gets "ask")
VRA_PIPELINE_ALLOWED = {"plan", "status", "sync-ledger"}
VRA_PIPELINE_ASK = {"deploy", "push-source"}
VRA_HUMAN_FLAGS = {"--confirm", "--allow-replacements", "--accept-drift", "--emergency",
                   "--allow-missing-scanners", "--force", "--writes-stopped", "--abort-workflow-executions",
                   "--takeover", "--include-auth-tables", "--human-approval"}
VRA_HUMAN_SUBCOMMANDS = {"unlock", "credential-process"}
VRA_GLOBAL_WITH_VALUE = ("--ledger", "--work-dir", "--run-id", "--home", "--lock-wait", "--human-approval")
# wrapper -> (options that take a value, positional arguments before the command)
WRAPPER_SPEC = {
    "env": ({"-u", "--unset", "-C", "--chdir", "-P", "-S", "--split-string", "-a", "--argv0"}, 0),
    "command": (set(), 0),
    "exec": ({"-a"}, 0),
    "nohup": (set(), 0),
    "time": ({"-f", "--format", "-o", "--output"}, 0),
    "sudo": ({"-u", "--user", "-g", "--group", "-C", "--close-from", "-D", "--chdir", "-h", "--host", "-p",
              "--prompt", "-r", "--role", "-t", "--type", "-T", "--command-timeout", "-U", "--other-user"}, 0),
    "doas": ({"-u", "-C"}, 0),
    "nice": ({"-n", "--adjustment"}, 0),
    "timeout": ({"-s", "--signal", "-k", "--kill-after"}, 1),
    "xargs": ({"-I", "-d", "--delimiter", "-E", "-L", "-n", "--max-args", "-P", "--max-procs", "-s",
               "--max-chars", "-a", "--arg-file", "--process-slot-var"}, 0),
    "stdbuf": ({"-i", "-o", "-e", "--input", "--output", "--error"}, 0),
    "flock": ({"-w", "--wait", "--timeout", "-E", "--conflict-exit-code"}, 1),
    "setsid": (set(), 0),
    "chrt": ({"-T", "-P", "-D", "--sched-runtime", "--sched-period", "--sched-deadline"}, 1),
    "ionice": ({"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "--pgid", "-u", "--uid"}, 0),
    "taskset": (set(), 1),
    "caffeinate": ({"-t", "-w"}, 0),
    "nocache": (set(), 0),
}
WRAPPERS = set(WRAPPER_SPEC)
OPAQUE = {"eval", "source", ".", "watch", "fc", "builtin"}
# programs that run a command the guard cannot see (a script, another user, a pseudo terminal, a sandbox)
OPAQUE_RUNNERS = {"script", "busybox", "toybox", "su", "runuser", "sg", "newgrp", "unbuffer", "expect", "tclsh",
                  "socat", "strace", "ltrace", "gdb", "nsenter", "unshare", "chroot", "firejail", "bwrap",
                  "systemd-run", "at", "batch", "parallel", "faketime", "proot", "pexpect", "empty", "dtach",
                  "tmux", "screen", "byobu"}
# programs whose arguments are data, never a command they run
DATA_HEADS = {"echo", "printf", "grep", "egrep", "fgrep", "rg", "ag", "ack", "cat", "less", "more", "head", "tail",
              "wc", "ls", "man", "which", "whereis", "type", "help", "file", "stat", "jq", "yq", "diff", "sort",
              "uniq", "cut", "tr", "tee", "test", "[", "true", "false", "pwd", "basename", "dirname", "touch",
              "mkdir", "readlink", "realpath", "git", "gh", "vra"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
PYTHONS = re.compile(r"^python(\d+(\.\d+)?)?$")
AWS_TOOLS = {"awscli", "aws", "aws-cdk", "cdk", "aws-shell", "boto3"}

SPLIT = re.compile(r"\s*(?:;|&&|\|\||\||&|\n|\$\(|`|\))\s*")

# vra MCP tools (engine/vra/mcp_server.py tiers; same split as the Claude Code allow / ask lists). A vra tool
# that is in neither set (a tool added later) asks the person.
MCP_ALLOW = {"status", "report", "ledger_validate", "ledger_show", "ledger_diff", "ledger_graph", "preflight",
             "sync_dry_run", "history_verify", "history_show", "home_show", "sync", "merge", "merge_continue",
             "build", "test", "backup", "resume", "watch", "iam_verify", "iam_plan", "pipeline_status",
             "pipeline_plan", "pipeline_sync_ledger"}
MCP_ASK = {"deploy", "promote", "rollback", "restore", "data_copy", "migrate", "reindex", "pipeline_deploy",
           "pipeline_push_source"}
# names and parameters that would expose a human-only action (scripts/check_agent_policy.py HUMAN_TOOLS / PARAMS)
MCP_HUMAN_TOOLS = {"unlock", "takeover", "setup", "ledger_import", "upgrade_config", "home_migrate",
                   "credential_process", "mcp"}
MCP_HUMAN_PARAMS = {"confirm", "takeover", "force", "write", "writes_stopped", "allow_missing_scanners",
                    "abort_workflow_executions", "emergency", "allow_replacements", "accept_drift",
                    "include_auth_tables", "human_approval"}
VRA_MCP_SERVER = "vra"
CURSOR_EVENTS = {"beforeShellExecution", "beforeMCPExecution", "preToolUse"}
MESSAGE_TAIL = ("Use the vra MCP tools or the vra command from the skill workflow instead, or ask the human.")


class Blocked(Exception):
    pass


def extract_command(event: dict) -> str:
    """The shell command of a hook event, as one string ('' when the tool is not a shell)."""
    for key in ("tool_input", "toolInput", "input", "arguments", "params"):
        tool_input = event.get(key)
        if tool_input is None:
            continue
        if isinstance(tool_input, str):
            return tool_input
        if isinstance(tool_input, list):
            return shlex.join(str(x) for x in tool_input)
        if isinstance(tool_input, dict):
            cmd = tool_input.get("command", tool_input.get("cmd"))
            if isinstance(cmd, list):
                return shlex.join(str(x) for x in cmd)
            if cmd:
                return str(cmd)
    return ""


ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


class Nested(Exception):
    """A wrapper runs a command line given as one string (`env -S`, `flock -c`)."""

    def __init__(self, line: str):
        super().__init__(line)
        self.line = line


def _skip_options(head: str, out: list) -> list:
    """Drop the options of wrapper ``head`` (with their values) from ``out``; raise Nested for a command string."""
    vals, _npos = WRAPPER_SPEC[head]
    nested = {"env": ("-S", "--split-string"), "flock": ("-c", "--command")}.get(head, ())
    while out:
        a = out[0]
        if a == "--":
            return out[1:]
        if not a.startswith("-") or a == "-":
            if head == "env" and ASSIGN.match(a):
                out = out[1:]
                continue
            return out
        name, eq, value = a.partition("=") if a.startswith("--") else (a, "", "")
        if name in nested:
            line = value if eq else (out[1] if len(out) > 1 else "")
            rest = out[1:] if eq else out[2:]
            raise Nested(" ".join([line] + [shlex.quote(x) for x in rest]) if head == "env" else line)
        if a.startswith("--"):
            out = out[2:] if (name in vals and not eq) else out[1:]
            continue
        # a short option cluster: -n5, -uVAR, -qc, -s KILL
        letters = a[1:]
        consumed = 1
        for k, ch in enumerate(letters):
            if f"-{ch}" in nested:
                line = letters[k + 1:] or (out[1] if len(out) > 1 else "")
                rest = out[1:] if letters[k + 1:] else out[2:]
                raise Nested(" ".join([line] + [shlex.quote(x) for x in rest]) if head == "env" else line)
            if f"-{ch}" in vals:
                consumed = 1 if k < len(letters) - 1 else 2
                break
        out = out[consumed:]
    return out


def _strip(tokens: list) -> list:
    """Drop leading VAR=value assignments and wrapper commands with their options and positional arguments
    (``env -u X``, ``nice -n 5``, ``timeout -s KILL 5``, ``flock /tmp/l``, ``taskset -c 1 ...``)."""
    out = list(tokens)
    while out:
        head = os.path.basename(out[0])
        if ASSIGN.match(out[0]):
            out = out[1:]
            continue
        if head in OPAQUE_RUNNERS:
            raise Blocked(f"`{head}` runs a command the guard cannot check (another shell, user, pseudo terminal or "
                          f"sandbox); run the command directly.")
        if head not in WRAPPERS:
            break
        if head == "taskset" and any(a in ("-p", "--pid") or re.match(r"^-[a-z]*p[a-z]*$", a) for a in out[1:3]):
            return []      # taskset -p <mask> <pid>: changes a running process, runs nothing
        if head == "ionice" and any(a in ("-p", "--pid", "-P", "--pgid", "-u", "--uid") for a in out[1:]):
            return []
        out = _skip_options(head, out[1:])
        npos = WRAPPER_SPEC[head][1]
        if head == "flock" and out:
            out = out[1:]                                  # the lock file / directory / fd
            if out and (out[0] in ("-c", "--command") or out[0].startswith("--command=")):
                raise Nested(out[0].split("=", 1)[1] if "=" in out[0] else (out[1] if len(out) > 1 else ""))
            out = _skip_options(head, out)
            continue
        out = out[npos:]
    return out


def check_git(args: list) -> None:
    i = 0
    while i < len(args) and args[i].startswith("-"):  # git -c k=v, -C dir, --git-dir=...
        if args[i] == "-c" and i + 1 < len(args) and args[i + 1].lower().startswith("alias."):
            raise Blocked("Git aliases defined on the command line are not allowed.")
        if args[i].startswith("--config-env"):
            raise Blocked("git --config-env is not allowed.")
        i += 2 if args[i] in ("-c", "-C") else 1
    if i >= len(args):
        return
    sub, rest = args[i], args[i + 1:]
    if sub == "push":
        for a in rest:
            if a in ("-f", "--force", "--force-with-lease", "--force-if-includes", "--delete", "-d", "--mirror",
                     "--prune") \
                    or a.startswith("--force") or a.startswith("+") or (a.startswith(":") and len(a) > 1) \
                    or (re.match(r"^-[a-zA-Z]+$", a) and ("f" in a or "d" in a)):
                raise Blocked("Force pushes, mirror pushes and remote ref deletion are never allowed.")
    elif sub == "rebase":
        raise Blocked("Rebasing is not allowed; vra merges with --no-ff.")
    elif sub == "reset" and "--hard" in rest:
        raise Blocked("git reset --hard is not allowed.")
    elif sub == "tag" and any(a in ("-d", "--delete", "-f", "--force") for a in rest):
        raise Blocked("Moving or deleting tags is not allowed.")
    elif sub == "branch" and any(a in ("-D", "-d", "--delete", "-M", "-f", "--force") for a in rest):
        raise Blocked("Deleting or force-moving branches is not allowed.")
    elif sub == "update-ref" and any(a in ("-d", "--stdin") for a in rest):
        raise Blocked("Deleting refs is not allowed.")
    elif sub == "merge" and any(a in ("-Xours", "-Xtheirs", "-X") or a.startswith("--strategy-option")
                                or a.startswith("-X") for a in rest):
        raise Blocked("Tree-wide strategy options (-X ours/theirs) are not allowed; resolve per path.")
    elif sub == "commit" and "--no-verify" in rest:
        raise Blocked("Skipping hooks is not allowed.")
    elif sub in ("filter-branch", "filter-repo", "replace"):
        raise Blocked("Rewriting history is not allowed.")


def check_gh(args: list) -> None:
    if not args or args[0] != "api":
        return
    method = None
    for j, a in enumerate(args):
        if a in ("-X", "--method") and j + 1 < len(args):
            method = args[j + 1].upper()
        elif a.startswith("--method="):
            method = a.split("=", 1)[1].upper()
        elif re.match(r"^-X[A-Za-z]+$", a):
            method = a[2:].upper()
    if method in ("DELETE", "PATCH", "PUT") and any("/git/refs" in a or "/branches/" in a for a in args):
        raise Blocked("Changing or deleting refs through the GitHub API is not allowed.")


def check_vra(args: list) -> str:
    """"allow" or "ask" for one vra invocation; raises Blocked for a deny."""
    if not args or args[0] in ("--version", "-h", "--help"):
        return "allow"
    # global flags may precede the subcommand
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i].split("=")[0] in VRA_HUMAN_FLAGS:
            raise Blocked(f"{args[i].split('=')[0]} is human-only; only the human passes it.")
        i += 2 if args[i] in VRA_GLOBAL_WITH_VALUE else 1
    if i >= len(args):
        return "allow"
    sub, rest = args[i], args[i + 1:]
    if sub in VRA_HUMAN_SUBCOMMANDS:
        raise Blocked(f"`vra {sub}` is run by the human, not the agent.")
    if sub not in VRA_ALLOWED:
        raise Blocked(f"`vra {sub}` is not on the agent allowlist.")
    waived = sorted(VRA_HUMAN_FLAGS & {a.split("=")[0] for a in rest})
    if waived:
        raise Blocked(f"{', '.join(waived)} is human-only; only the human passes it.")
    if sub == "home" and rest and rest[0] == "migrate":
        raise Blocked("`vra home migrate` creates the shared home from an old ledger; the human runs it.")
    if sub == "iam" and "apply" in [a for a in rest if not a.startswith("-")][:1]:
        raise Blocked("`vra iam apply` wires the roles IT returned into the env's config; the human runs it.")
    if sub == "ledger" and rest and (rest[0] == "import" or (rest[0] == "upgrade-config" and "--write" in rest)):
        raise Blocked("Changing the ledger's env config is the human's decision (review the proposal first).")
    if sub == "pipeline":
        words = [a for a in rest if not a.startswith("-")]
        action = words[0] if words else ""
        if action in VRA_PIPELINE_ASK or (action == "sync-ledger" and "--deploy" in rest):
            return "ask"
        if action not in VRA_PIPELINE_ALLOWED:
            raise Blocked(f"`vra pipeline {action or '(none)'}` is not on the agent allowlist.")
    return "allow"


def _vra_args(head: str, args: list) -> list | None:
    """The vra arguments when the command runs the engine in another spelling, else None."""
    if head == "vra":
        return args
    if PYTHONS.match(head):
        if "-m" in args and args[args.index("-m") + 1:args.index("-m") + 2] == ["vra"]:
            return args[args.index("-m") + 2:]
        first = next((a for a in args if not a.startswith("-")), "")
        if first.replace("\\", "/").endswith("vra/__main__.py") or os.path.basename(first) == "vra":
            return args[args.index(first) + 1:]
    if head in ("uv", "uvx", "pipx", "poetry", "pdm", "hatch", "rye", "pixi"):
        rest = [a for a in args if not a.startswith("-")]
        if rest[:2] in (["run", "vra"], ["exec", "vra"]) or rest[:1] == ["vra"]:
            k = args.index("vra")
            return args[k + 1:]
    return None


def _names_aws_cdk_or_push(args: list) -> str | None:
    for k, a in enumerate(args):
        b = os.path.basename(a)
        if b in ("aws", "cdk", "aws-cdk") or b.startswith("aws2") or b.split("@")[0] in ("aws-cdk", "cdk"):
            return b
        if b == "git" and "push" in args[k + 1:]:
            return "git push"
    return None


def check_simple(tokens: list, depth: int = 0) -> str:
    """"allow" or "ask" for one simple command; raises Blocked for a deny."""
    try:
        tokens = _strip(tokens)
    except Nested as n:
        return check_line(n.line, depth + 1)
    if not tokens:
        return "allow"
    if tokens[0].startswith("$") or "${" in tokens[0]:
        raise Blocked("A variable as the command name cannot be checked; run the command directly.")
    head = os.path.basename(tokens[0])
    args = tokens[1:]
    if head in OPAQUE:
        raise Blocked(f"`{head}` hides the command it runs; run the command directly.")
    if head in SHELLS:
        cflag = next((a for a in args if re.match(r"^-[a-zA-Z]*c[a-zA-Z]*$", a)), None)
        if cflag is not None:
            k = args.index(cflag) + 1
            return check_line(args[k] if k < len(args) else "", depth + 1)
        if any(not a.startswith("-") for a in args):
            raise Blocked("Running a shell script file cannot be checked; run its commands directly.")
        raise Blocked("A shell reading commands from its input cannot be checked; run the commands directly.")
    vra_args = _vra_args(head, args)
    if vra_args is not None:
        return check_vra(vra_args)
    if head == "aws" or head.startswith("aws2"):
        raise Blocked("Direct aws CLI calls are not allowed; vra performs every AWS change.")
    if head == "cdk" or (head in ("npx", "pnpx", "yarn", "bunx", "pnpm", "npm") and any(
            os.path.basename(a).split("@")[0] in ("cdk", "aws-cdk") for a in args if not a.startswith("-"))):
        raise Blocked("Direct cdk is not allowed; vra deploys from the build bundle.")
    if head in ("uvx", "pipx", "uv") and any(a.split("==")[0].split("@")[0] in AWS_TOOLS for a in args):
        raise Blocked("Running the AWS CLI or CDK through a package runner is not allowed.")
    if PYTHONS.match(head):
        if not [a for a in args if a not in ("-u", "-B", "-q", "-I", "-E", "-s", "-S")] or "-" in args:
            raise Blocked("Python reading code from its input cannot be checked; run a script file directly.")
        if "-m" in args and args[args.index("-m") + 1:args.index("-m") + 2] in (["awscli"], ["aws"]):
            raise Blocked("Direct aws CLI calls are not allowed; vra performs every AWS change.")
        code = " ".join(args[args.index("-c") + 1:args.index("-c") + 2]) if "-c" in args else ""
        if re.search(r"\b(boto3|botocore|aws_cdk|awscli)\b", code):
            raise Blocked("AWS SDK one-liners are not allowed; vra performs every AWS change.")
    if head == "git":
        check_git(args)
    if head == "gh":
        check_gh(args)
    if head not in DATA_HEADS and not PYTHONS.match(head):
        # an unknown runner (`foo aws s3 ls`, `find -exec aws`, `npm exec -- cdk`): what it runs is not visible
        named = _names_aws_cdk_or_push(args)
        if named:
            raise Blocked(f"`{head}` would run {named}, which the guard cannot check through another program; vra "
                          f"performs every AWS change and push.")
    return "allow"


def check_line(command: str, depth: int = 0) -> str:
    """"allow" or "ask" for a command line (the strictest of its parts); raises Blocked for a deny."""
    if depth > 3:
        raise Blocked("Nested shells are too deep to check; run the command directly.")
    tier = "allow"
    for part in SPLIT.split(command.strip()):
        part = part.strip().lstrip("(").strip()
        if not part:
            continue
        try:
            tokens = shlex.split(part)
        except ValueError:
            raise Blocked("The command could not be parsed; run simpler commands one at a time.")
        if check_simple(tokens, depth) == "ask":
            tier = "ask"
    return tier


def _mcp_allowlist() -> list[str]:
    return [p.strip() for p in os.environ.get("VRA_GUARD_MCP_ALLOW", "").split(",") if p.strip()]


def check_mcp(server, tool, tool_input) -> str:
    """Decision for one MCP call: "allow" or "ask"; raises Blocked for a deny."""
    if not isinstance(server, str) or not server:
        raise Blocked("An MCP call without a server name cannot be checked.")
    if server != VRA_MCP_SERVER:
        if any(fnmatch.fnmatchcase(server, p) for p in _mcp_allowlist()):
            return "allow"
        raise Blocked(f"MCP server {server!r} is not allowed in this workspace (VRA_GUARD_MCP_ALLOW).")
    if not isinstance(tool, str) or not tool:
        raise Blocked("An MCP call without a tool name cannot be checked.")
    tool = tool[4:] if tool.startswith("MCP:") else tool
    if tool in MCP_HUMAN_TOOLS:
        raise Blocked(f"vra tool {tool!r} is a human-only action.")
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input) if tool_input.strip() else {}
        except ValueError:
            raise Blocked("The MCP tool input could not be parsed.")
    if tool_input is None:
        tool_input = {}
    if not isinstance(tool_input, dict):
        raise Blocked("The MCP tool input is not an object.")
    waived = sorted(MCP_HUMAN_PARAMS & set(tool_input))
    if waived:
        raise Blocked(f"{', '.join(waived)} is human-only; only the human passes it.")
    if tool in MCP_ALLOW:
        return "allow"
    return "ask"  # MCP_ASK, and any vra tool this guard does not know yet


VRA_MCP_TOOLS = MCP_ALLOW | MCP_ASK | MCP_HUMAN_TOOLS


def decide(event: dict, cursor: bool = False) -> tuple[str, str]:
    """(permission, message) for one hook event: "allow" / "ask" / "deny"."""
    name = event.get("hook_event_name")
    tool_name = event.get("tool_name")
    try:
        if name == "beforeMCPExecution" or "mcp_server_name" in event:
            return check_mcp(event.get("mcp_server_name"), tool_name, event.get("tool_input")), ""
        if isinstance(tool_name, str) and tool_name.startswith("MCP:"):
            # Cursor preToolUse (also run by cloud agents, which skip beforeMCPExecution): no server name, and
            # "ask" is not enforced there, so ask becomes deny (the person runs it from an editor session)
            tool = tool_name[4:]
            server = event.get("mcp_server_name") or (VRA_MCP_SERVER if tool in VRA_MCP_TOOLS else None)
            tier = check_mcp(server, tool, event.get("tool_input"))
            if tier == "ask" and name == "preToolUse":
                raise Blocked(f"vra tool {tool!r} asks a person, which preToolUse cannot do; run it from an "
                              f"interactive session.")
            return tier, ""
        # Cursor's shell event carries the command at the top level (also when the event name is missing)
        if name == "beforeShellExecution" or (cursor and "command" in event and "tool_input" not in event):
            command = event.get("command")
            if not isinstance(command, str):
                raise Blocked("The shell command could not be read from the hook event.")
        else:
            command = extract_command(event)
        if command:
            tier = check_line(command)
            # exit-code hosts (Claude Code, Codex, Kiro) ask through their own ask lists; Cursor asks here
            return (tier if cursor else "allow"), ""
    except Blocked as exc:
        return "deny", f"Blocked by vra-guard: {exc}"
    return "allow", ""


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cursor = "--format=cursor" in argv or ("--format" in argv and argv[argv.index("--format") + 1:][:1] == ["cursor"])
    try:
        return _main(cursor)
    except Exception as exc:  # noqa: BLE001 - fail closed: an internal error must block, never allow
        message = f"Blocked by vra-guard: internal error ({type(exc).__name__}); every command is blocked until " \
                  f"the guard runs."
        if cursor:
            sys.stdout.write(json.dumps({"permission": "deny", "user_message": message,
                                         "agent_message": message}) + "\n")
            return 0
        sys.stderr.write(message + "\n")
        return 2


def _main(cursor: bool) -> int:
    try:
        event = json.load(sys.stdin)
    except (ValueError, OSError):
        event = None
    if not isinstance(event, dict):
        permission, message = "deny", "Blocked by vra-guard: unreadable hook event."
    else:
        cursor = cursor or event.get("hook_event_name") in CURSOR_EVENTS
        permission, message = decide(event, cursor)
    if cursor:
        out = {"permission": permission}
        if permission == "deny":
            out.update(user_message=message, agent_message=f"{message} {MESSAGE_TAIL}")
        elif permission == "ask":
            out.update(user_message="vra: this action changes an environment that may be approval-gated; "
                                    "approve it only if you asked for it.")
        sys.stdout.write(json.dumps(out) + "\n")
        return 0
    if permission == "deny":
        sys.stderr.write(f"{message} {MESSAGE_TAIL}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
