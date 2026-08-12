# VAMS Agent

Operate a VAMS deployment at runtime through the `vamscli` command-line tool
(search, inspect, research, bulk-update, cross-link, process). Self-discovers
available commands via `vamscli --help`; read-only by default.

## Instructions

The canonical skill definition lives at `tools/VamsAgentSkill/SKILL.md` (a
standalone, portable skill so it can also be deployed to AgentCore or other
hosts). This command is the Claude Code entry point for it.

1. Read `tools/VamsAgentSkill/SKILL.md` and adopt it as your operating
   instructions for this task.
2. Follow it exactly, in particular:
    - **Authenticate per session** and set the vamscli profile to the current
      user's token before doing VAMS work (Step 1 of the skill).
    - **Self-discover** commands via `vamscli --help` — never assume or hardcode
      commands.
    - **Default to read-only**; only use mutating commands (create, delete,
      edit/modify, execute, upload) if the user has explicitly authorized changes,
      and confirm destructive/bulk actions first.
    - **Only workflows execute** — pipelines, templates, workflows, and executions
      are four entities across three command groups, and the execute request takes
      input-file references rather than an asset.
    - **Authorization has two tiers**; a `403` on a route the user is allowed to
      call is an entity-level refusal to report, not to retry.
3. Treat the user's request in `$ARGUMENTS` as the task to accomplish.

## Workflow

1. Load `tools/VamsAgentSkill/SKILL.md`.
2. Verify `vamscli` is installed and authenticate for the session.
3. Discover the relevant commands via `--help`.
4. Confirm read-only vs. mutating; execute the mapped workflow.
5. Report results with IDs and a summary of anything changed.

## User Request

$ARGUMENTS
