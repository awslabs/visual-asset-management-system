# AGENTS.md — VAMS release automation (Codex)

This workspace is operated with the `vams-release-automation` skill. Codex
MUST load and follow it before doing any release work here.

-   Canonical skill: `skill/SKILL.md` (relative to the package root; if the
    package lives elsewhere, `$VRA_PACKAGE/skill/SKILL.md`). It is also
    installed as the project skill `.codex/skills/vams-release-automation`.
-   Workflow SOPs: `skill/workflows/*.md`. Pick the one the skill's workflow
    index names for the task and follow it step by step.

The skill is the only source of behaviour rules. This file adds Codex-specific
notes and does not override it.

## Codex specifics

-   Use the `vra` MCP server tools for every engine action. The `vra` CLI is
    the fallback for commands without a tool (`vra setup`, `vra merge --abort`)
    and when the server is not connected; always pass `--json`.
-   When Codex asks for approval to run a command, an approval-gated step in
    the skill still needs the user's explicit answer in chat; a sandbox
    approval is not a release approval.
-   In `codex exec` (headless) there is no human: stop at every **[HUMAN]**
    step and put the question in your final output.
