#!/usr/bin/env python3
# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Source of the Kiro agent files in .kiro/agents/.

Writes the agents with their V2 keys only (patterns that convert to V3 globs without loss). Then run
`kiro-cli agent upgrade --no-backup <dir>`, which adds the V3 `permissions` and `hooks` (the universal format
both agent engines load), and copy the result to .kiro/agents/. Usage: generate_agents.py <out-dir>
"""
import json
import pathlib
import sys

out = pathlib.Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

READ_TOOLS = ["status", "report", "ledger_validate", "ledger_show", "ledger_diff", "ledger_graph", "preflight",
              "sync_dry_run", "history_verify", "history_show", "home_show", "iam_verify", "pipeline_status"]
WRITE_TOOLS = ["sync", "merge", "merge_continue", "build", "test", "backup", "resume", "watch", "iam_plan",
               "pipeline_plan", "pipeline_sync_ledger"]
# pipeline_deploy and pipeline_push_source (gated tier) are not trusted: Kiro asks the person before each call

HUMAN_FLAGS = ["--confirm", "--takeover", "--allow-replacements", "--accept-drift", "--emergency",
               "--allow-missing-scanners", "--writes-stopped", "--abort-workflow-executions", "--include-auth-tables",
               "--human-approval", "--force"]
DENY = (
    [".*git push .*--force.*", ".*git push -f.*", ".*git push .* -f.*", ".*git push .*--delete.*",
     ".*git push .*--mirror.*", ".*git push .*--prune.*", ".*git rebase.*", ".*git reset --hard.*",
     ".*git tag .*-d.*", ".*git tag .*-f.*", ".*git branch .*-D.*", ".*git update-ref.*", ".*git filter-.*",
     "cdk", "cdk .*", ".* cdk .*", ".*npx .*aws-cdk.*", "aws", "aws .*", ".* aws .*", ".*/aws .*",
     ".*uvx .*awscli.*", ".*pipx .*aws.*", ".*python .*-c .*boto3.*", ".*python3 .*-c .*boto3.*",
     ".*python -m vra.*", ".*python3 -m vra.*", "eval .*", ".* eval .*", ".*\\$\\(.*", ".*`.*",
     ".*vra unlock.*", ".*vra iam apply.*", ".*vra credential-process.*", ".*vra ledger import.*",
     ".*vra ledger upgrade-config .*--write.*", ".*vra home migrate.*"]
    + [f".*vra .*{f}.*" for f in HUMAN_FLAGS]
)
READ_VRA = ["status", "report", "ledger validate", "ledger show", "ledger diff", "ledger graph", "history verify",
            "history show", "home show", "preflight", "iam verify", "pipeline status", "pipeline plan"]
# `vra pipeline sync-ledger` only without arguments: `--deploy` is not matched, so Kiro asks the person
VRA_EXACT = ["vra pipeline sync-ledger", "vra --json pipeline sync-ledger"]
GIT_READ = ["status", "diff", "log", "show", "branch --list", "rev-parse", "ls-files", "tag -l"]


def vra_read():
    out = []
    for c in READ_VRA:
        for p in ("vra ", "vra --json "):
            out += [p + c, p + c + " .*"]
    return out + VRA_EXACT


def git_read(extra=()):
    out = []
    for c in GIT_READ + list(extra):
        out += ["git " + c, "git " + c + " .*"]
    return out + ["git remote -v"]


# `|| exit 2`: a guard that cannot run (no python3, no file, a crash) blocks instead of failing open
GUARD = 'python3 "${VRA_PACKAGE:-.}/adapters/shared/vra-guard.py" || exit 2'
HOOKS = {"preToolUse": [{"matcher": "execute_bash", "command": GUARD}, {"matcher": "shell", "command": GUARD}]}
READ_DENY = ["~/.aws/**", "**/.env", "**/.env.*", "~/.cache/vra/**/owner-tokens/**"]
WRITE_DENY = READ_DENY + ["**/ledger/approvals/**", "**/vra-home.yaml", "**/state/history/**", "**/state/locks/**",
                          "**/state/approvals-consumed/**", "**/state/runs/*/state.json", "**/state/iam/**",
                          "**/state/pipeline/**", "**/ledger/envs/*/overlay/**", "**/envs/*/overlay/**"]
STEERING = "file://adapters/kiro/.kiro/steering/*.md"


def mcp(gated):
    env = {"VRA_AGENT": "1", "VRA_MCP_ENABLE_WRITES": "true", "VRA_MCP_ENABLE_GATED": "true" if gated else "false"}
    return {"vra": {"command": "vra", "args": ["mcp"], "env": env, "timeout": 21600000}}


def at(names):
    return [f"@vra/{n}" for n in names]


def operator(name, description, gated, extra_prompt=""):
    return {
        "name": name,
        "description": description,
        "prompt": (
            "You are the release operator for a customer fork of VAMS. Read skill/SKILL.md in full, then the "
            "workflow file it names in skill/workflows/, and follow them exactly; they override this prompt. Use "
            "the vra MCP tools for every engine action; use shell only for a vra command that has no tool (vra "
            "setup, vra merge --abort) and for read-only git. Branch on exit codes per SKILL.md section 3 (a tool "
            "error carries exitCode). Delegate conflicts to vra-conflict-resolver, failure classification to "
            "vra-failure-triager and the end-of-run summary to vra-reporter. At a [HUMAN] step ask one clear "
            "question and wait; never assume the answer." + extra_prompt),
        "includeMcpJson": False,
        "mcpServers": mcp(gated),
        "tools": ["read", "write", "shell", "glob", "grep", "use_subagent", "@vra"],
        "allowedTools": ["read", "glob", "grep", "use_subagent"] + at(READ_TOOLS + WRITE_TOOLS),
        "toolsSettings": {
            "shell": {"allowedCommands": vra_read() + git_read(["add"]) + ["gh pr view", "gh pr view .*"],
                      "deniedCommands": DENY},
            "read": {"deniedPaths": READ_DENY},
            "write": {"allowedPaths": ["~/.cache/vra/**/repo/**"], "deniedPaths": WRITE_DENY},
            "subagent": {"availableAgents": ["vra-conflict-resolver", "vra-failure-triager", "vra-reporter"],
                         "trustedAgents": ["vra-failure-triager", "vra-reporter"]},
        },
        "resources": ["file://skill/SKILL.md", "file://skill/workflows/*.md", STEERING],
        "hooks": HOOKS,
    }


agents = {
    "vra-release-operator": operator(
        "vra-release-operator",
        "Drives one VAMS release automation workflow (W0-W10) end to end through the vra MCP tools (CLI as "
        "fallback), branching on exit codes and stopping at every human gate.",
        gated=False),
    "vra-gated-operator": operator(
        "vra-gated-operator",
        "vra-release-operator with the approval-gated MCP tier on: it may run deploy, promote, rollback, restore, "
        "data copy and migrate on approval-gated envs when the human asked for that exact action and an approval "
        "record exists (the engine verifies it).",
        gated=True),
    "vra-cron-operator": operator(
        "vra-cron-operator",
        "Headless triage after a cron vra watch cycle: same as vra-release-operator, with the approval-gated MCP "
        "tier off. Used by adapters/shared/vra-cron.sh.",
        gated=False,
        extra_prompt=" There is no human in this session: at a [HUMAN] step write the question into your final "
                     "output and stop."),
    "vra-conflict-resolver": {
        "name": "vra-conflict-resolver",
        "description": "Resolves merge conflicts left by vra merge (exit 20 with conflicts.json) by path-policy "
                       "category, proposes core patches, and escalates semantic conflicts.",
        "prompt": "Follow skill/workflows/conflict-resolution.md exactly under the rules in skill/SKILL.md. Edit "
                  "only the conflicted files in the engine's fork clone (the repo directory of the work dir that "
                  "the status tool reports). Use shell only for read-only git and git add <path> in that clone, "
                  "and vra merge --abort when the workflow says so; continue the merge with the merge_continue "
                  "tool. Never commit the merge yourself, never rebase, never use tree-wide -X ours/theirs, never "
                  "push. Return per path: category, resolution, core patch proposed or retired; escalate semantic "
                  "conflicts with two candidate resolutions.",
        "includeMcpJson": False,
        "mcpServers": mcp(False),
        "tools": ["read", "write", "shell", "glob", "grep", "@vra"],
        "allowedTools": ["read", "glob", "grep"] + at(["status", "merge_continue", "build"]),
        "toolsSettings": {
            "shell": {"allowedCommands": git_read(["add"]), "deniedCommands": DENY + [".*git commit.*"]},
            "read": {"deniedPaths": READ_DENY},
            "write": {"allowedPaths": ["~/.cache/vra/**/repo/**"], "deniedPaths": WRITE_DENY},
        },
        "resources": ["file://skill/SKILL.md", "file://skill/workflows/conflict-resolution.md", STEERING],
        "hooks": HOOKS,
    },
    "vra-failure-triager": {
        "name": "vra-failure-triager",
        "description": "Read-only classification of VAMS release automation failures from vra run state, logs and "
                       "JUnit into the W5 triage classes.",
        "prompt": "Follow the Triage section of skill/workflows/W5-test.md under the rules in skill/SKILL.md. Read "
                  "the run with the status tool, then the step logs under <runDir>/logs/ and JUnit under "
                  "<runDir>/junit/. You may re-run one failed suite once with the test tool to check for a flake. "
                  "Never edit or skip tests; never quote secrets. Return per failure: class, evidence path, short "
                  "excerpt, next workflow, who decides.",
        "includeMcpJson": False,
        "mcpServers": mcp(False),
        "tools": ["read", "glob", "grep", "@vra"],
        "allowedTools": ["read", "glob", "grep"] + at(["status", "report", "history_show", "test"]),
        "toolsSettings": {"read": {"deniedPaths": READ_DENY}},
        "resources": ["file://skill/SKILL.md", "file://skill/workflows/W5-test.md", STEERING],
    },
    "vra-reporter": {
        "name": "vra-reporter",
        "description": "Writes the end-of-run summary for a VAMS release automation run (W10).",
        "prompt": "Follow skill/workflows/W10-report.md under the rules in skill/SKILL.md. Call the report tool "
                  "for the run. Do not edit files in the shared home; the report is engine-written. Never include "
                  "secrets. Return the short summary for the human, including the triage notes you were given.",
        "includeMcpJson": False,
        "mcpServers": mcp(False),
        "tools": ["read", "@vra"],
        "allowedTools": ["read"] + at(["status", "report"]),
        "toolsSettings": {"read": {"deniedPaths": READ_DENY}},
        "resources": ["file://skill/SKILL.md", "file://skill/workflows/W10-report.md", STEERING],
    },
}

for name, cfg in agents.items():
    (out / f"{name}.json").write_text(json.dumps(cfg, indent=4) + "\n", encoding="utf-8")
print("wrote", len(agents))
