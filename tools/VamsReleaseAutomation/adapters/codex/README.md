# Codex adapter

Thin front-end for the OpenAI Codex CLI. All behaviour lives in
`skill/SKILL.md` and `skill/workflows/`; these files only point Codex at it and
wire the `vra` MCP server. Everything installs into the project (the package
root), not into `~/.codex`, so it applies to this workspace only.

| File                  | Installed as                                   | Purpose                                                                                                |
| --------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `AGENTS.md`           | `AGENTS.md`                                    | Project instructions: load the skill, Codex specifics                                                  |
| `config.toml.example` | `.codex/config.toml`                           | `[mcp_servers.vra]` (writes on, gated tier off), `VRA_AGENT=1`, sandbox, hooks feature, cron profile   |
| `hooks.json`          | `.codex/hooks.json`                            | PreToolUse hook running `adapters/shared/vra-guard.py` before every tool call                          |
| `rules/vra.rules`     | `.codex/rules/vra.rules`                       | Command rules for the shell fallback: allow read-only `vra` and git, forbid `aws`, `cdk`, force pushes |
| `../../skill/`        | `.codex/skills/vams-release-automation` (link) | The skill itself, loaded as a Codex skill                                                              |
| `prompts/*.md`        | not installed by default                       | Fallback custom prompts (`/prompts:vra-deploy ENV=...`) for Codex versions without skills              |

## Install

```bash
cd VamsReleaseAutomation                       # package root
pip install -e '.[mcp]'                        # vra + the MCP server
adapters/install.sh codex
export VRA_PACKAGE="$PWD"
```

Then edit `.codex/config.toml` and set `writable_roots` to this machine's
`~/.cache/vra` path (the engine's fork clone lives there; conflict resolution
edits it). Trust the project: Codex loads project configuration, rules, hooks
and skills only for a trusted project (accept the prompt on first start, or set
`projects."<package root>".trust_level = "trusted"` in `~/.codex/config.toml`).

Custom prompts are deprecated in Codex in favour of skills. If your Codex
version has no skills, copy `prompts/*.md` to `~/.codex/prompts/`; they apply
to every project of that user, so remove them when you upgrade.

Post-install check, in the package root:

```bash
codex mcp list                                                  # vra is listed
codex execpolicy check --help                                   # then check `aws s3 ls` against .codex/rules/vra.rules: forbidden
echo '{"tool_input":{"command":["bash","-lc","aws s3 ls"]}}' | python3 adapters/shared/vra-guard.py; echo "exit $?"   # exit 2
```

## Controls

-   The MCP server is the primary surface. Its read and write tiers are on; the
    approval-gated tier (deploy to, promote into, roll back, restore, copy data
    into and migrate approval-gated envs) is off. Set `VRA_MCP_ENABLE_GATED =
"true"` in `[mcp_servers.vra].env` to let the agent run such an action when
    you ask for it; the engine still requires an approval record.
-   The guard hook runs before every tool call and blocks direct `aws` / `cdk`
    in any spelling (also inside `bash -lc "..."`), force pushes, ref deletion,
    and every human-only `vra` flag and command. Codex shell tools pass the
    command as an argv list; the guard reads both shapes. The hook command
    ends in `|| exit 2`, and the guard blocks on an internal error, so a guard
    that cannot run (no `python3` 3.9 or later, no file) blocks the call.
-   `vra pipeline plan`, `status` and `sync-ledger` are allowed by the rules;
    `vra pipeline deploy`, `push-source` and `sync-ledger --deploy` are
    `prompt` (the strictest matching rule wins; MCP `pipeline_deploy` and
    `pipeline_push_source` are in the gated tier).
-   The sandbox writes only the workspace and the fork clone
    (`writable_roots`); keep the shared home outside the workspace, so the
    agent cannot edit `state/iam/`, `state/pipeline/` or
    `ledger/envs/*/overlay/`. The engine also refuses an IT apply record
    (`applied.json`) that no `vra iam apply` history record matches.
-   The rules cover simple commands only: a script that uses `VAR=...`,
    `$(...)`, redirects or globs is evaluated as one `bash -lc` call, which the
    prefix rules do not split. The guard and the engine cover that case.
-   `VRA_AGENT=1` (shell environment) and `VRA_MCP=1` (set by the server) make
    the engine refuse every human-only action. With them cleared, the engine
    still accepts only an approval record, never a typed confirmation, while
    `codex` runs in the parent process chain or a Codex marker is set.

## Headless cron

Use the shared script; it runs `vra watch` with no agent and calls Codex only
for exit codes that need judgment:

```bash
# 17 6 * * *  vra-bot  VRA_PACKAGE=/opt/vra/VamsReleaseAutomation /opt/vra/VamsReleaseAutomation/adapters/shared/vra-cron.sh --agent codex
```

It runs `codex exec --profile vra-cron` with the gated tier forced off
(`-c mcp_servers.vra.env.VRA_MCP_ENABLE_GATED="false"`). The profile sets
`approval_policy = "never"`, so every rule with decision `prompt` is refused.
Give the cron user credentials for the auto-deploy environments only.

## Sources

Codex is not installed on the machine this adapter was built on, so nothing
here was executed against Codex; run the post-install check above. Written
against:

-   [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) (MCP servers, hooks, profiles, project trust)
-   [Codex rules](https://learn.chatgpt.com/docs/agent-configuration/rules)
-   [Codex custom prompts](https://learn.chatgpt.com/docs/custom-prompts) (deprecated in favour of skills)
-   [AGENTS.md open format](https://agents.md/)
