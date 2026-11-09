# VAMS Release Automation

Operate the release lifecycle of a customer fork of VAMS through the `vra` engine, with its MCP server
(`vra mcp`) when it is connected and the `vra` command-line tool otherwise (status, sync and merge of
upstream releases, build, deploy, test, promote, roll back, data copy, reports). Read-only by default.

## Instructions

The canonical skill definition lives at `tools/VamsReleaseAutomation/skill/SKILL.md` (a portable skill
that the Codex, Claude Code, Kiro and Cursor adapters all point at). This command is the Claude Code entry point
for it.

1. Read `tools/VamsReleaseAutomation/skill/SKILL.md` and adopt it as your operating instructions for
   this task, then read the workflow file it names in `tools/VamsReleaseAutomation/skill/workflows/`.
2. Follow it exactly, in particular:
    - **Every state change goes through `vra`** (its MCP tools, or `vra --json ...` with `VRA_AGENT=1`).
      Never run `cdk`, `npx cdk`, `aws`, migration scripts, force pushes or rebases yourself.
    - **Branch on the `vra` exit code**: `0` OK, `10` BLOCKED_ON_IT (hand the IT request, or the IT role
      package of an `iam-package` request, to the user and stop), `20` NEEDS_HUMAN, `30` TESTS_FAILED or
      ROLLED_BACK, `40` ROLLBACK_FAILED, `2` usage error, `1` other error.
    - **Default to read-only** (`vra status`, `vra report`, `vra --dry-run ...`). Deploy, promote,
      restore, roll back or copy data only when the user asked for that exact action in this session.
    - **Never author an approval record**, never edit the shared home, and never use a human-only flag
      or command (for example `vra iam apply`); give the user the command to run instead. You may run
      `vra iam plan` and `vra iam verify`.
3. Treat the user's request in `$ARGUMENTS` as the task to accomplish.

## Workflow

1. Load `tools/VamsReleaseAutomation/skill/SKILL.md`.
2. Verify `vra` is installed (`vra --version`) and the shared home is reachable (`vra home show`,
   `vra ledger validate`).
3. Pick the workflow for the request and run its read-only steps first.
4. Confirm any state-changing step with the user; run it through `vra`.
5. Report the run id, the exit code and what changed.

## User Request

$ARGUMENTS
