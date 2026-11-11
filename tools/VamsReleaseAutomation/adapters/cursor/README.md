# Cursor adapter

Thin front-end for Cursor (the editor's Agent and the Cursor CLI). All behaviour lives in `skill/SKILL.md` and
`skill/workflows/`; these files only point Cursor at it, wire the `vra` MCP server and the shared guard, and set
the CLI permission lists. Everything installs into the project (`.cursor/` in the package root), not into
`~/.cursor`, so it applies to this workspace only.

| File                            | Installed as                                    | Purpose                                                                                                                 |
| ------------------------------- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `.cursor/mcp.json`              | `.cursor/mcp.json`                              | The `vra mcp` stdio server: `VRA_AGENT=1`, writes on, approval-gated tier from `VRA_MCP_ENABLE_GATED` (off when unset)  |
| `.cursor/hooks.json`            | `.cursor/hooks.json`                            | `beforeShellExecution`, `beforeMCPExecution` and `preToolUse` (`MCP:.*`) run the guard (`--format cursor`), fail closed |
| `.cursor/cli.json`              | `.cursor/cli.json`                              | Cursor CLI allow / deny lists, the Claude Code lists in Cursor tokens (`Shell(...)`, `Mcp(vra:...)`)                    |
| `.cursor/rules/*.mdc`           | `.cursor/rules/`                                | One always-applied rule: read the skill, use the MCP tools, the guard's deny is final                                   |
| `.cursor/skills/vra-*/SKILL.md` | `.cursor/skills/`                               | Commands, the same set as the Claude Code adapter (below)                                                               |
| `../../skill/`                  | `.cursor/skills/vams-release-automation` (link) | The skill itself, loaded as an Agent Skill                                                                              |
| `../shared/vra-guard.py`        | stays in `adapters/shared/`                     | Guard: direct `aws` / `cdk`, force pushes, ref deletion, human-only `vra` flags; MCP tiers                              |

Commands: `/vra-setup`, `/vra-status`, `/vra-watch`, `/vra-sync`, `/vra-deploy`, `/vra-promote`, `/vra-rollback`,
`/vra-resume`, `/vra-data-copy`. Cursor 2.4 turned slash commands into Agent Skills; each command is a skill with
`disable-model-invocation: true`, so it runs only when typed. Type the arguments after the command.

## Install

```bash
cd VamsReleaseAutomation                       # package root
pip install -e '.[mcp]'                        # vra + the MCP server
adapters/install.sh cursor
export VRA_PACKAGE="$PWD"
```

Open the package root in Cursor and trust the workspace (project hooks run only in a trusted workspace). In
**Customize**, enable the `vra` MCP server, and check that the Hooks tab lists both hooks and Skills lists
`vams-release-automation` and the `vra-*` commands. Start Cursor from a shell that exports `VRA_AGENT=1`, so
fallback `vra` CLI commands run as an agent too; the MCP server sets it for its own calls.

For the CLI, install it (`curl https://cursor.com/install -fsS | bash`; the binary is `agent`, older installs
named it `cursor-agent`) and authenticate (`agent login`, or `CURSOR_API_KEY` for scripts).

Post-install check, in the package root:

```bash
agent mcp enable vra                                              # approve the project server (CLI)
agent mcp list                                                    # vra is listed and loaded
agent mcp list-tools vra                                          # tool names: status, report, ... (the guard matches them)
echo '{"hook_event_name":"beforeShellExecution","command":"aws s3 ls"}' \
  | python3 adapters/shared/vra-guard.py --format cursor          # {"permission": "deny", ...}
echo '{"hook_event_name":"beforeMCPExecution","mcp_server_name":"vra","tool_name":"promote","tool_input":"{}"}' \
  | python3 adapters/shared/vra-guard.py --format cursor          # {"permission": "ask", ...}
```

Then ask the agent to run `aws sts get-caller-identity`: the hook denies it and the Hooks output channel shows the
call.

## Controls

-   The MCP server is the primary surface. Read and write tools are allowed; `deploy` and the approval-gated tools
    (promote, rollback, restore, data copy, migrate) **ask** in the `beforeMCPExecution` hook. The gated tools exist
    only when Cursor starts with `VRA_MCP_ENABLE_GATED=true` in its environment, and they still need an approval
    record the engine verifies.
-   The guard denies every other MCP server in this workspace unless `VRA_GUARD_MCP_ALLOW` (comma-separated names,
    `*` wildcards, in Cursor's environment) lists it. A call without a server name is denied.
-   `preToolUse` with matcher `MCP:.*` runs the guard as well, because cloud agents skip `beforeMCPExecution`.
    `preToolUse` does not enforce `ask`, so the guard turns an ask into a deny there.
-   `failClosed: true`, and every hook command ends in `|| exit 2`: a guard crash, a timeout, a missing `python3`
    (3.9 or later) or a missing guard file blocks the action. `adapters/install.sh cursor` writes the package's
    absolute path into the installed `hooks.json`, so a workspace opened without `VRA_PACKAGE` still finds the
    guard. Cursor merges all hook sources with deny over
    ask over allow, so enterprise or user hooks cannot loosen this one.
-   `.cursor/cli.json` is the CLI's own layer: the same allow and deny rules as the Claude Code settings. Cursor has
    no `ask` list; what is neither allowed nor denied prompts in an interactive session.
-   `vra pipeline plan`, `status` and `sync-ledger` are allowed; `vra pipeline deploy`, `push-source` and
    `sync-ledger --deploy` ask (MCP `pipeline_deploy`, `pipeline_push_source`).
-   Edits of `state/iam/`, `state/pipeline/` and `ledger/envs/*/overlay/` are denied: only `vra iam apply` (a
    person) and the engine write them.
-   The engine refuses every human-only action for an agent (`VRA_AGENT=1`, `VRA_MCP=1`) whatever Cursor allows.
    Under Cursor it accepts only an approval record, never a typed confirmation, because an agent CLI runs in the
    parent process chain.

> **Warning:** do not run `agent --force` (`--yolo`) in this workspace: it runs every command that is not denied.
> The cron script does not use it.

## Headless cron

Use the shared script; it runs `vra watch` with no agent and calls Cursor only for exit codes that need judgment:

```bash
# 17 6 * * *  vra-bot  VRA_PACKAGE=/opt/vra/VamsReleaseAutomation CURSOR_API_KEY=... /opt/vra/VamsReleaseAutomation/adapters/shared/vra-cron.sh --agent cursor
```

It runs `agent -p --trust --approve-mcps --workspace <package> --output-format json` with
`VRA_MCP_ENABLE_GATED=false` exported (the project `mcp.json` reads it), so nothing in this path can reach an
approval-gated environment. Headless Cursor is allowlist-only: without `--force`, print mode runs only what
`.cursor/cli.json` `permissions.allow` lists, because whether the CLI runs project hooks is not proven. Before
the agent starts, a canary pipes a direct `aws` call through the guard and checks that `cli.json` denies `aws`
and `cdk` and does not allow every shell command; if any check fails, the script sends the report without
triage. Set `VRA_CURSOR_AGENT` to the binary path if it is not on the cron user's `PATH`.
Give the cron user credentials for the auto-deploy environments only, and keep `CURSOR_API_KEY` in the cron
user's environment file, not in the crontab line, on a shared host.

## Cloud agents

Cursor cloud agents run the project's `beforeShellExecution` hook but not `beforeMCPExecution` (cloud agents
start in a read-only environment where MCP hooks do not load), and they do not see `~/.cache/vra` or the shared
home. Do not run release work in cloud agents; use the editor or the CLI on an operator machine.

## Sources

Cursor is not installed on the machine this adapter was built on, so nothing here was executed against Cursor.
The guard's Cursor payload handling is unit tested (`adapters/shared/test_vra_guard.py`,
`adapters/shared/test_cursor_adapter.py`). Written against these pages (read 2026-11-04):

-   [Hooks](https://cursor.com/docs/hooks): `hooks.json` version 1, project hooks run from the project root,
    `beforeShellExecution` input `command` / `cwd`, `beforeMCPExecution` input `tool_name` / `tool_input` (JSON
    string) / `mcp_server_name`, output `permission` allow / deny / ask, `failClosed`, exit code 2 blocks, the
    cloud-agent support table.
-   [Model Context Protocol](https://cursor.com/docs/mcp): `.cursor/mcp.json`, stdio fields, `${env:NAME}`
    interpolation.
-   [Rules](https://cursor.com/docs/rules): `.cursor/rules/*.mdc`, `alwaysApply` / `description` / `globs`.
-   [Agent Skills](https://cursor.com/docs/skills): `.cursor/skills/<name>/SKILL.md`, `name` must match the folder,
    `disable-model-invocation`, slash commands migrated to skills in 2.4.
-   [CLI headless](https://cursor.com/docs/cli/headless), [CLI parameters](https://cursor.com/docs/cli/reference/parameters),
    [CLI permissions](https://cursor.com/docs/cli/reference/permissions),
    [CLI configuration](https://cursor.com/docs/cli/reference/configuration), [CLI installation](https://cursor.com/docs/cli/installation).

Not verified (check on first install):

-   That the `tool_name` of `beforeMCPExecution` is the bare tool name (`status`); the guard also accepts the
    `MCP:status` form of `preToolUse`.
-   That an unset `${env:VRA_MCP_ENABLE_GATED}` resolves to an empty string (either way the server stays off for
    anything but `true`).
-   That the CLI runs project hooks (the docs state it for `workspaceOpen` only); `cli.json` covers the CLI either
    way.
-   The exact matching of `Shell(command:args)` tokens with spaces and wildcards in the arguments
    (`Shell(vra:* --confirm*)`); the test reads them as a glob over the arguments after the command.
-   Whether `--approve-mcps` also approves individual MCP tool calls in print mode, and what an `ask` decision does
    in print mode.
