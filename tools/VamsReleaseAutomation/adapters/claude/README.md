# Claude Code adapter

Thin front-end for Claude Code. All behaviour lives in `skill/SKILL.md` and
`skill/workflows/`; these files only point Claude at it and wire the `vra` MCP
server.

| File                     | Installed as                     | Purpose                                                                                                               |
| ------------------------ | -------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `mcp.json.example`       | `.mcp.json`                      | The `vra mcp` stdio server: writes on, approval-gated tier off unless `VRA_MCP_ENABLE_GATED=true` in your environment |
| `settings.json.example`  | `.claude/settings.json`          | `VRA_AGENT=1`, the MCP server enabled, allow / ask / deny lists, the `vra-guard` PreToolUse hook                      |
| `CLAUDE.md.example`      | `.claude/CLAUDE.md`              | Project memory: points to the skill, Claude specifics                                                                 |
| `.claude/agents/*.md`    | `.claude/agents/`                | Subagents `vra-release-operator`, `vra-conflict-resolver`, `vra-failure-triager`, `vra-reporter`                      |
| `.claude/commands/*.md`  | `.claude/commands/`              | Slash commands (fallback entry points): `/vra-setup`, `/vra-watch`, `/vra-sync`, `/vra-deploy`, ...                   |
| `mcp.cron.json`          | used in place by the cron script | MCP config for headless runs: approval-gated tier always off                                                          |
| `../shared/vra-guard.py` | stays in `adapters/shared/`      | Hook that blocks direct `aws` / `cdk`, force pushes, ref deletion and human-only `vra` flags                          |

The workspace is the package root: the subagents and commands read
`skill/...` relative to it. The project memory goes to `.claude/CLAUDE.md`
because the package root's own `CLAUDE.md` is the component development guide.
The package `.gitignore` keeps the installed copies out of the repository.

## Install

```bash
cd VamsReleaseAutomation                       # package root
pip install -e '.[mcp]'                        # vra + the MCP server
adapters/install.sh claude
```

Then trust the workspace: start `claude` once in the package root and accept
the trust dialog, and approve the `vra` server from `.mcp.json` when asked.
Until the workspace is trusted, Claude Code ignores the project
`permissions.allow` list (`claude doctor` reports "workspace has not been
trusted"), so headless runs refuse every tool.

Post-install check, in the package root:

```bash
claude doctor                                  # no permission-rule warnings
claude mcp get vra                             # connected, tools listed
echo '{"tool_input":{"command":"aws s3 ls"}}' | python3 adapters/shared/vra-guard.py; echo "exit $?"   # exit 2
```

## Permission model

-   `allow`: the read and write MCP tools (status, report, ledger, preflight,
    history, home, sync, merge, build, test, backup, resume, watch,
    `iam_plan`, `iam_verify`, `pipeline_plan`, `pipeline_status`,
    `pipeline_sync_ledger`), read-only `vra` and git commands, `vra pipeline
plan`, `status` and `sync-ledger`, `git add`, `gh pr view`, edits inside the engine's
    fork clone (`~/.cache/vra/**/repo/**`, needed for conflict resolution), and
    read and search tools.
-   `ask`: every MCP tool that deploys or changes data (deploy, promote,
    rollback, restore, data copy, migrate, `pipeline_deploy`,
    `pipeline_push_source`), the state-changing `vra` CLI fallbacks (also
    `vra pipeline deploy`, `push-source` and `sync-ledger --deploy`), `vra setup`, plain `git push`, `git remote add` and other edits.
-   `deny`: every force-push spelling, ref and branch deletion, `git rebase`,
    `git reset --hard`, tag moves, `cdk` / `npx cdk`, every `aws` call, every
    human-only `vra` flag and command, `python -m vra`, edits to approval
    records, `vra-home.yaml` and engine state (also `state/iam/`,
    `state/pipeline/` and `ledger/envs/*/overlay/`, which only `vra iam apply`
    and the engine write), the engine's owner tokens, and `.env` / `~/.aws`
    reads.
-   Deny wins over ask, ask over allow. The `vra-guard` hook catches compound
    and indirect commands (`a && cdk deploy`, `bash -c`, `python3 -m vra`) that a
    prefix rule can miss. The hook command ends in `|| exit 2`, and the guard
    blocks on an internal error, so a guard that cannot run (no `python3` 3.9
    or later, a moved file) blocks every shell call instead of failing open.
-   The approval-gated MCP tools exist only when `VRA_MCP_ENABLE_GATED=true` is
    set in the environment Claude Code starts in. They still need an approval
    record, which the engine verifies.

The engine refuses every human-only action for an agent on its own
(`VRA_AGENT=1` here, `VRA_MCP=1` in the MCP server), whatever these lists say.
With `VRA_AGENT` cleared it still accepts only an approval record, never a
typed confirmation: Claude Code's markers (`CLAUDECODE`) and the `claude`
process in the parent chain rule the typed path out.

> **Warning:** never run this package with `claude --bare` (skips the hooks)
> or `--dangerously-skip-permissions` (skips settings and hooks). The engine's
> refusals and IAM still apply, but every Claude-side control is gone.

## Headless cron

Use the shared script; it runs `vra watch` with no agent and calls Claude
only for exit codes that need judgment:

```bash
# 17 6 * * *  vra-bot  VRA_PACKAGE=/opt/vra/VamsReleaseAutomation /opt/vra/VamsReleaseAutomation/adapters/shared/vra-cron.sh --agent claude
```

It starts `claude -p --agent vra-release-operator --permission-mode dontAsk`
with `mcp.cron.json` (gated tier off) and `--strict-mcp-config`.
`dontAsk` refuses everything in the `ask` list, so cron cannot deploy, promote
or push. Give the cron user credentials for the auto-deploy environments only.

## Sources

Checked against the installed Claude Code 2.1 (`claude --help`, `claude mcp
--help`, `claude doctor`) and these references:

-   [Claude Code MCP](https://docs.anthropic.com/en/docs/claude-code/mcp)
-   [Claude Code subagents](https://docs.anthropic.com/en/docs/claude-code/sub-agents)
-   [Claude Code settings and permissions](https://docs.anthropic.com/en/docs/claude-code/settings)
-   [Claude Code hooks](https://docs.anthropic.com/en/docs/claude-code/hooks)
-   [Claude Code headless mode](https://docs.anthropic.com/en/docs/claude-code/headless)
