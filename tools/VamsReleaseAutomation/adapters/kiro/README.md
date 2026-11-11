# Kiro adapter

Thin front-end for Kiro (`kiro-cli` and the IDE). All behaviour lives in
`skill/SKILL.md` and `skill/workflows/`; these files only point Kiro at it and
wire the `vra` MCP server into each agent.

| File                                          | Installed as         | Purpose                                                                                                                                  |
| --------------------------------------------- | -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `.kiro/agents/*.json`                         | `.kiro/agents/`      | Agents `vra-release-operator`, `vra-gated-operator`, `vra-cron-operator`, `vra-conflict-resolver`, `vra-failure-triager`, `vra-reporter` |
| `.kiro/steering/{product,workflow,safety}.md` | `.kiro/steering/`    | Always-included steering; points at `skill/SKILL.md` with `#[[file:...]]`                                                                |
| `../shared/vra-guard.py`                      | stays in `adapters/` | `preToolUse` hook command for every shell call                                                                                           |

The agent files are in Kiro's universal format (the V2 keys plus the V3
`permissions` and `hooks` that `kiro-cli agent upgrade` writes), so both agent
engines load the same rules. `make check` runs `kiro-cli agent validate` and
`kiro-cli agent upgrade --dry-run` on them when `kiro-cli` is installed; the
only upgrade note is that V3 does not yet apply read restrictions to skill
loading.

## Install

```bash
cd VamsReleaseAutomation                       # package root
pip install -e '.[mcp]'                        # vra + the MCP server
adapters/install.sh kiro
export VRA_PACKAGE="$PWD" VRA_AGENT=1
kiro-cli chat --agent vra-release-operator
```

Start Kiro from the package root: agent `resources` are relative to it, and
the guard hook runs `${VRA_PACKAGE:-.}/adapters/shared/vra-guard.py`. Started
anywhere else without `VRA_PACKAGE`, the hook cannot find the guard and blocks
every shell call (fail closed). Kiro has no per-agent environment key, so
export `VRA_AGENT=1` yourself for the CLI fallback; the MCP server sets it for
every tool call on its own.

Trust the workspace when Kiro asks. Kiro V3 loads agents, hooks and MCP servers
from an untrusted folder only with `--trust-workspace`.

Post-install check:

```bash
kiro-cli agent list                            # lists the six agents (vra-release-operator, ...) as workspace agents
kiro-cli chat --agent vra-release-operator     # then ask it to run `aws --version`: "Blocked by vra-guard"
```

## What each agent may do

| Agent                   | MCP tools without a prompt                              | Shell without a prompt                           | Writes                               |
| ----------------------- | ------------------------------------------------------- | ------------------------------------------------ | ------------------------------------ |
| `vra-release-operator`  | read and write tiers (status ... watch); deploy prompts | read-only `vra` and git, `git add`, `gh pr view` | fork clone files (prompts elsewhere) |
| `vra-gated-operator`    | same; the gated tier exists and always prompts          | same                                             | fork clone files                     |
| `vra-cron-operator`     | same; gated tier off                                    | same                                             | fork clone files                     |
| `vra-conflict-resolver` | `status`, `merge_continue`, `build`                     | read-only git, `git add`                         | fork clone files                     |
| `vra-failure-triager`   | `status`, `report`, `history_show`, `test`              | none (no shell)                                  | none                                 |
| `vra-reporter`          | `status`, `report`                                      | none (no shell)                                  | none                                 |

Every agent sets `includeMcpJson: false`, so only its own `vra` server loads
(`kiro-cli mcp list` shows one `vra` server per agent). The approval-gated MCP
tier is off in every agent except `vra-gated-operator`, which is
`vra-release-operator` with `VRA_MCP_ENABLE_GATED=true`: start it only when you
want the agent to run an approval-gated deploy, promotion, rollback, restore,
data copy or migration that you asked for. The engine still requires an
approval record. The gated tools always prompt.

`vra pipeline plan`, `status` and `sync-ledger` (without `--deploy`) run
without a prompt (MCP `pipeline_plan`, `pipeline_status`,
`pipeline_sync_ledger`); `vra pipeline deploy`, `push-source` and
`sync-ledger --deploy` (MCP `pipeline_deploy`, `pipeline_push_source`) prompt.

Denied commands cover force pushes, ref and branch deletion, history rewrites,
`cdk` and `aws` in any spelling, `python -m vra`, `$(...)` and backticks, and
every human-only `vra` flag and command. Read and write deny `~/.aws/**`,
`.env*` and the engine's owner tokens; writes also deny approval records,
`vra-home.yaml`, engine state, `state/iam/**`, `state/pipeline/**` and
`ledger/envs/*/overlay/**` (only `vra iam apply` and the engine write them).

The hook command ends in `|| exit 2`, and the guard blocks on an internal
error, so a guard that cannot run (no `python3` 3.9 or later, no file) blocks
the shell call. Under Kiro the engine accepts only an approval record for a
human-only action, never a typed confirmation (`kiro-cli` runs in the parent
process chain).

## Headless cron

Use the shared script; it runs `vra watch` with no agent and calls Kiro only
for exit codes that need judgment:

```bash
# 17 6 * * *  vra-bot  VRA_PACKAGE=/opt/vra/VamsReleaseAutomation /opt/vra/VamsReleaseAutomation/adapters/shared/vra-cron.sh --agent kiro
```

It runs `kiro-cli chat --no-interactive --trust-workspace --agent
vra-cron-operator`. Tools outside `allowedTools` prompt, and with nobody to
answer they are refused. Never use `--trust-all-tools` / `-a` for this
package. Give the cron user credentials for the auto-deploy environments only.

## Sources

Checked against the installed `kiro-cli 3.0.0`: `agent validate`, `agent
upgrade --dry-run -v` (universal format, permission rules, hooks),
`agent list`, `chat --help` (`--agent`, `--no-interactive`,
`--trust-workspace`, `--trust-all-tools`). References:

-   [Kiro CLI custom agents: configuration reference](https://kiro.dev/docs/cli/custom-agents/configuration-reference/)
-   [Kiro CLI MCP servers](https://kiro.dev/docs/cli/mcp/)
-   [Kiro CLI hooks](https://kiro.dev/docs/cli/hooks/)
-   [Kiro steering](https://kiro.dev/docs/steering/)
