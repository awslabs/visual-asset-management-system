---
title: "Installation"
sidebar_position: 3
description: "Install the vra engine and its MCP server, then the Claude Code, Kiro, Codex or Cursor adapter: files, workspace trust, MCP tiers, the guard hook, verification, more machines, cron, CI runners, upgrades, uninstall and install troubleshooting."
---

# Installation

Release automation has two parts to install on each operator machine: the `vra` engine (a Python
package, with the optional MCP server), and, if an AI agent drives it, one agent adapter. The adapter
only points the agent at the skill and wires the MCP server; the engine enforces every gate on its own.
Complete the [Prerequisites](prerequisites.md) first, then install, then run [Setup](setup.md).

## Install the engine

The engine is the Python package `vams-release-automation` in `tools/VamsReleaseAutomation/`. It
needs Python 3.12 or later (3.12 is the runtime the CI templates use, 3.13 is used for development) and
pins every dependency exactly, so install it into its own virtual environment.

| Extra   | Adds                                                                                     |
| ------- | ---------------------------------------------------------------------------------------- |
| none    | The `vra` command: every workflow, the CLI fallback for agents, CI                       |
| `[mcp]` | The `vra mcp` server (`mcp` library) that agent systems call; required for every adapter |
| `[dev]` | Test dependencies, for changing the tool itself (`pytest`, `moto`, `mcp`)                |

### From a VAMS checkout

```bash
cd <vams-checkout>/tools/VamsReleaseAutomation
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[mcp]'
```

`-e` (editable) keeps the engine pointing at this folder, so a `git pull` of the checkout updates it.
The `.venv/` folder is ignored by the package's `.gitignore`.

### From the standalone package zip

The release zip `vams-release-automation-<version>.zip` holds the package under
`tools/VamsReleaseAutomation/` (with its documentation pages and steering files next to it):

```bash
unzip vams-release-automation-<version>.zip -d vra-<version>
cd vra-<version>/tools/VamsReleaseAutomation
python3.12 -m venv .venv
. .venv/bin/activate
pip install '.[mcp]'
```

Keep the unpacked folder: the adapters, the guard hook and the cron script read `skill/` and
`adapters/shared/` from it, whether or not the engine itself is installed editable.

### Check the engine

```bash
vra --version                                  # vra <version>
vra mcp --help                                 # the MCP server's options (needs [mcp] to run)
python -c "import mcp" && echo "mcp extra installed"
```

There is no separate doctor command. Once a home exists, `vra home show` (path, sync, lease, hosts),
`vra ledger validate` and `vra preflight --all` check the installation against the ledger and the AWS
accounts.

## Install an agent adapter

Install the adapter of the agent system you use, from the package root, with the engine's virtual
environment active:

```bash
adapters/install.sh claude|codex|kiro|cursor [--dest <workspace>] [--overwrite]
```

The script copies files only and changes nothing outside the workspace. The default workspace is the
package root, which every adapter expects as the agent's working directory. An existing file is kept
(`kept ... (exists; --overwrite replaces it)`) unless you pass `--overwrite`. With `--dest` elsewhere,
export `VRA_PACKAGE=<package root>` so the adapter finds `skill/` and `adapters/shared/`. The package's
`.gitignore` keeps the installed copies out of the repository.

These things are the same for every agent system:

-   **The MCP server.** The agent starts `vra mcp` over stdio with `VRA_AGENT=1`. Its tiers come from
    environment variables: the read tier is always on, `VRA_MCP_ENABLE_WRITES=true` adds the write tier
    (shipped on), and `VRA_MCP_ENABLE_GATED=true` (with writes) adds the approval-gated tier (shipped
    off). Turn the gated tier on only for a session in which you ask the agent for an approval-gated
    action; the engine still requires an approval record. See
    [Agent systems: the MCP server](agent-systems.md#the-mcp-server).
-   **`vra` on the agent's `PATH`.** The shipped MCP configurations run `vra`, so start the agent from a
    shell with the virtual environment active, or replace `vra` with the full path to `.venv/bin/vra` in
    the MCP configuration.
-   **The guard hook.** `adapters/shared/vra-guard.py` runs before every shell call and blocks direct
    `aws` and `cdk`, force pushes, ref deletion and every human-only `vra` flag. It stays in
    `adapters/shared/`; the hook commands find it relative to the workspace or `VRA_PACKAGE`. It needs
    `python3` 3.9 or later on the agent's `PATH` (no packages). Every hook command ends in `|| exit 2`: if
    the guard cannot run, every command is blocked.
-   **Protected state.** The Claude Code, Kiro and Cursor adapters deny agent edits of `state/iam/`,
    `state/pipeline/` and `ledger/envs/*/overlay/`, besides approvals, locks and history: only
    `vra iam apply` and `vra pipeline` write them. The Codex sandbox writes only the workspace and the
    fork clone, so keep the shared home outside the workspace. The engine also refuses an IT apply
    record that no `vra iam apply` history record matches.

### Claude Code

Verified against Claude Code 2.1.

```bash
adapters/install.sh claude
```

| Installed file              | Purpose                                                                                          |
| --------------------------- | ------------------------------------------------------------------------------------------------ |
| `.mcp.json`                 | The `vra` stdio server; `VRA_MCP_ENABLE_GATED` is read from Claude Code's environment            |
| `.claude/settings.json`     | `VRA_AGENT=1`, the server enabled, allow / ask / deny lists, the guard on `Bash`                 |
| `.claude/CLAUDE.md`         | Project memory pointing at the skill                                                             |
| `.claude/agents/vra-*.md`   | Subagents `vra-release-operator`, `vra-conflict-resolver`, `vra-failure-triager`, `vra-reporter` |
| `.claude/commands/vra-*.md` | Slash commands `/vra-setup`, `/vra-watch`, `/vra-sync`, `/vra-deploy`, ...                       |

1. Start `claude` once in the package root and accept the trust dialog. Until the workspace is
   trusted, Claude Code ignores the project allow list.
2. Approve the `vra` server from `.mcp.json` when Claude Code asks.
3. For an approval-gated session, start Claude Code with `VRA_MCP_ENABLE_GATED=true` in its
   environment.
4. Verify, in the package root:

```bash
claude doctor                                  # no permission-rule or trust warnings
claude mcp get vra                             # connected, tools listed
echo '{"tool_input":{"command":"aws s3 ls"}}' | python3 adapters/shared/vra-guard.py; echo "exit $?"   # exit 2
```

### Kiro

Verified against `kiro-cli` 3.0.0.

```bash
adapters/install.sh kiro
export VRA_PACKAGE="$PWD" VRA_AGENT=1
```

| Installed file                                | Purpose                                                                           |
| --------------------------------------------- | --------------------------------------------------------------------------------- |
| `.kiro/agents/vra-*.json`                     | Six agents, each with its own `vra` MCP server and the guard as `preToolUse` hook |
| `.kiro/steering/{product,workflow,safety}.md` | Always-included steering that loads `skill/SKILL.md`                              |

The agents are `vra-release-operator`, `vra-gated-operator` (the same with `VRA_MCP_ENABLE_GATED=true`),
`vra-cron-operator`, `vra-conflict-resolver`, `vra-failure-triager` and `vra-reporter`. Kiro has no
per-agent environment key, so export `VRA_AGENT=1` yourself for the CLI fallback; the MCP server sets it
for every tool call.

1. Start Kiro from the package root: agent resources and the guard path are relative to it. Started
   elsewhere without `VRA_PACKAGE`, the guard is not found and every shell call is blocked (fail closed).
2. Trust the workspace when Kiro asks. Without it, Kiro loads no agents, hooks or MCP servers from the
   folder.
3. Verify:

```bash
kiro-cli agent validate --path .kiro/agents/vra-release-operator.json
kiro-cli agent list                            # the six vra-* agents, as workspace agents
kiro-cli chat --agent vra-release-operator     # ask it to run `aws --version`: "Blocked by vra-guard"
```

The agent files are generated. To change them, edit `adapters/kiro/generate_agents.py`, run it into a
scratch directory, run `kiro-cli agent upgrade --no-backup <scratch dir>` (adds the V3 permissions and
hooks), copy the result to `adapters/kiro/.kiro/agents/`, and install again with `--overwrite`.

### Codex

Written against the Codex documentation only; not executed against a Codex installation. Use a Codex CLI
with skills, hooks and MCP server support, and run the verification below before relying on it.

```bash
adapters/install.sh codex
export VRA_PACKAGE="$PWD"
```

| Installed file                          | Purpose                                                                                         |
| --------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `AGENTS.md`                             | Project instructions: load the skill                                                            |
| `.codex/config.toml`                    | `[mcp_servers.vra]` (writes on, gated off), `VRA_AGENT=1`, sandbox, hooks feature, cron profile |
| `.codex/hooks.json`                     | `PreToolUse` hook running the guard before every tool call                                      |
| `.codex/rules/vra.rules`                | Command rules for the shell fallback                                                            |
| `.codex/skills/vams-release-automation` | A link to `skill/`                                                                              |

1. Edit `.codex/config.toml`: set `writable_roots` to this machine's `~/.cache/vra` path (the engine's
   fork clone lives there).
2. Trust the project: accept the prompt on first start, or set
   `projects."<package root>".trust_level = "trusted"` in `~/.codex/config.toml`.
3. For an approval-gated session, set `VRA_MCP_ENABLE_GATED = "true"` in `[mcp_servers.vra].env`.
4. A Codex version without skills can use the fallback prompts: copy `adapters/codex/prompts/*.md` to
   `~/.codex/prompts/` (they apply to every project of that user).
5. Verify, in the package root:

```bash
codex mcp list                                 # vra is listed
codex execpolicy check --help                  # then check `aws s3 ls` against .codex/rules/vra.rules: forbidden
echo '{"tool_input":{"command":["bash","-lc","aws s3 ls"]}}' | python3 adapters/shared/vra-guard.py; echo "exit $?"   # exit 2
```

### Cursor

Written against the Cursor documentation (hooks, MCP, rules, skills, CLI); not executed against a Cursor
installation. Run the verification below on first install.

```bash
adapters/install.sh cursor
export VRA_PACKAGE="$PWD"
```

| Installed file                             | Purpose                                                                                                   |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| `.cursor/mcp.json`                         | The `vra` stdio server: `VRA_AGENT=1`, writes on, gated tier from `VRA_MCP_ENABLE_GATED` (off when unset) |
| `.cursor/hooks.json`                       | `beforeShellExecution`, `beforeMCPExecution` and `preToolUse` (`MCP:.*`) run the guard, fail closed       |
| `.cursor/cli.json`                         | Cursor CLI allow and deny lists, the Claude Code lists in Cursor tokens                                   |
| `.cursor/rules/vra-release-automation.mdc` | One always-applied rule that points at the skill                                                          |
| `.cursor/skills/vra-*/SKILL.md`            | Commands `/vra-setup`, `/vra-status`, `/vra-watch`, `/vra-deploy`, ... (run only when typed)              |
| `.cursor/skills/vams-release-automation`   | A link to `skill/`                                                                                        |

1. Open the package root in Cursor and trust the workspace; project hooks run only in a trusted
   workspace.
2. In Customize, enable the `vra` MCP server, and check that Hooks lists both hooks and Skills lists
   `vams-release-automation` and the `vra-*` commands.
3. Start Cursor from a shell that exports `VRA_AGENT=1`, so fallback `vra` commands run as an agent too.
   For an approval-gated session, export `VRA_MCP_ENABLE_GATED=true` as well.
4. For the CLI (`agent`, `cursor-agent` in older installs), authenticate with `agent login`, or set
   `CURSOR_API_KEY` for scripts.
5. Verify, in the package root:

```bash
agent mcp enable vra && agent mcp list         # vra is listed and loaded
echo '{"hook_event_name":"beforeShellExecution","command":"aws s3 ls"}' | python3 adapters/shared/vra-guard.py --format cursor   # "deny"
```

Each adapter's README in
[`adapters/`](https://github.com/awslabs/visual-asset-management-system/tree/main/tools/VamsReleaseAutomation/adapters)
has the full permission model.

## Additional machines

Every machine that runs `vra` installs the engine (and an adapter if an agent runs there), then joins the
existing home instead of running setup again:

```bash
vra setup --join git@git.example.com:<customer>/vra-home.git
vra setup --join /mnt/shared/vams-release-home
vra home show
vra status --all
```

Join clones a git home into `~/vams-release-home` (`--home <dir>` for another folder), validates its
ledger, registers this machine, writes the pointer `~/.config/vra/config.yaml` for this user and returns
the cron line for a watch host. It changes nothing in any environment. See
[Setup and joining](setup.md#join-an-existing-home).

## Headless and cron

`adapters/shared/vra-cron.sh` runs one `vra watch` cycle with no agent and calls the agent only for exit
codes that need judgment (`20`, `30`, `1`); `10` and `40` only notify. It forces the gated tier off and
sets `VRA_AGENT=1`.

1. Create a dedicated user with credentials for the auto-deploy environments only.
2. As that user: install the engine, install the adapter into the package root, and run
   `vra setup --join <home>` so the user has its own pointer.
3. Make `vra` reachable from cron's minimal `PATH` (put the virtual environment's `bin/` first in the
   cron line), and add one line per home:

```bash
# /etc/cron.d/vra
17 6 * * *  vra-bot  PATH=/opt/vra/VamsReleaseAutomation/.venv/bin:/usr/bin:/bin VRA_PACKAGE=/opt/vra/VamsReleaseAutomation /opt/vra/VamsReleaseAutomation/adapters/shared/vra-cron.sh --agent kiro
```

| `--agent` | Triage command the script runs                                                                              |
| --------- | ----------------------------------------------------------------------------------------------------------- |
| `claude`  | `claude -p --agent vra-release-operator --permission-mode dontAsk` with `adapters/claude/mcp.cron.json`     |
| `kiro`    | `kiro-cli chat --no-interactive --trust-workspace --agent vra-cron-operator`                                |
| `codex`   | `codex exec --profile vra-cron` with the gated tier forced off                                              |
| `cursor`  | `agent -p --trust --approve-mcps --workspace <package> --output-format json` (no `--force`), gated tier off |
| `none`    | No agent: the watch cycle only                                                                              |

Before an agent triages, the script runs a canary: it pipes a direct `aws` call through the guard and stops
the triage (the report is still sent) unless the guard blocks it. For Cursor it also checks that
`.cursor/cli.json` denies `aws` and `cdk` and does not allow every shell command: headless Cursor is
allowlist-only, because whether the Cursor CLI runs project hooks is not verified.

Output goes to `~/.cache/vra/cron/` (`VRA_CRON_OUT`). Run the cron line once by hand as that user and
read `last-watch.json` before relying on it. Never use `--trust-all-tools`, `--bare` or
`--dangerously-skip-permissions` for this package.

## CI runners

A CI job needs the engine only, without an adapter or the `[mcp]` extra:

```bash
pip install './tools/VamsReleaseAutomation'                     # the reviewed commit of the package
pip install 'vams-release-automation==<version>'               # or a pinned version from a private mirror
```

The AWS CodeBuild templates install it with `cicd/codebuild/scripts/install-toolchain.sh`, where
`VRA_PACKAGE` is a pip specification (in the adapters and the cron script it is the package root). Bake
the engine into a custom image the same way. See
[CI/CD and AWS CodePipeline: job environment and toolchain](codepipeline.md#job-environment-and-toolchain).

## Upgrade

1. Read the release notes of the new version.
2. Update the package: `git pull` the checkout (editable install), or unpack the new zip and run
   `pip install '.[mcp]'` in it with the same virtual environment. Check `vra --version`.
3. Re-install the adapter with `--overwrite`. It replaces every file the adapter installs, including
   your edits (Codex `writable_roots`, added permissions); re-apply them. The Codex skill link is created
   only when it is missing; when the package folder moved, remove the link and install again.
4. If the ledger pins the engine (`vra.yaml` `engineVersion`), gated actions refuse every other engine
   version: change the pin by a reviewed ledger commit once every machine and CI image runs the new
   version.
5. Upgrade every machine that joined the home, and the cron user's installation.

## Uninstall

The shared home is the record of every run, approval and deploy: keep it. Remove per machine:

| Part                | Remove                                                                                                                                                                              |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Claude Code adapter | `.mcp.json`, `.claude/settings.json`, `.claude/CLAUDE.md`, `.claude/agents/vra-*.md`, `.claude/commands/vra-*.md`                                                                   |
| Kiro adapter        | `.kiro/agents/vra-*.json` (six files), `.kiro/steering/product.md`, `workflow.md`, `safety.md`                                                                                      |
| Codex adapter       | `AGENTS.md`, `.codex/config.toml`, `.codex/hooks.json`, `.codex/rules/vra.rules`, the link `.codex/skills/vams-release-automation`, any copied `~/.codex/prompts/vra-*.md`          |
| Cursor adapter      | `.cursor/mcp.json`, `.cursor/hooks.json`, `.cursor/cli.json`, `.cursor/rules/vra-release-automation.mdc`, `.cursor/skills/vra-*`, the link `.cursor/skills/vams-release-automation` |
| Cron                | The `/etc/cron.d/vra` line                                                                                                                                                          |
| Engine              | `pip uninstall vams-release-automation`, or the virtual environment                                                                                                                 |
| Machine-local state | The pointer `~/.config/vra/config.yaml` and the work dir `~/.cache/vra/<homeId>` (fork clone, owner tokens); only when no run of this machine is parked                             |

The adapter files are relative to the workspace the adapter was installed into (the package root, or
`--dest`). Check `vra status --all` before removing a work dir: a parked run's owner token lives there,
and without it the run needs a takeover.

## Troubleshooting the installation

| Symptom                                                            | Cause                                                        | Fix                                                                                             |
| ------------------------------------------------------------------ | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `pip` refuses the package (`requires-python`)                      | Python older than 3.12                                       | Create the virtual environment with Python 3.12 or later                                        |
| `pip` reports dependency conflicts                                 | Exact pins clash with other packages in the environment      | Use a dedicated virtual environment                                                             |
| `vra mcp` fails with `No module named 'mcp'`                       | Installed without the extra                                  | `pip install '.[mcp]'`                                                                          |
| The agent shows no `vra` tools, or the server fails to start       | `vra` is not on the agent's `PATH`                           | Start the agent with the virtual environment active, or use the full path to `vra`              |
| `install.sh` prints `kept ...`                                     | The file exists                                              | Re-run with `--overwrite` (re-apply your edits)                                                 |
| `install.sh` exits `2`                                             | Unknown agent or argument                                    | `adapters/install.sh claude\|codex\|kiro\|cursor [--dest <workspace>] [--overwrite]`            |
| Cursor runs a shell call the guard should block                    | The workspace is not trusted, or a cloud agent               | Trust the workspace; run release work in the editor or the CLI, not in cloud agents             |
| Kiro blocks every shell call                                       | The guard is not found (started outside the package root)    | Start in the package root, or export `VRA_PACKAGE`                                              |
| Kiro lists no `vra-*` agents                                       | Not in the package root, or the workspace is not trusted     | `cd` to the package root and trust it                                                           |
| Claude Code asks for every tool, `claude doctor` warns of trust    | The workspace is not trusted                                 | Start `claude` in the package root and accept the trust dialog                                  |
| Codex ignores the project configuration                            | The project is not trusted                                   | Trust it (prompt, or `trust_level = "trusted"`)                                                 |
| The guard check prints exit `0`                                    | Another guard file, or the hook points elsewhere             | Re-install with `--overwrite`; check `VRA_PACKAGE`                                              |
| Cron does nothing, or `vra: command not found` in `last-watch.log` | Cron's `PATH`, or no pointer for the cron user               | Put the virtual environment's `bin/` on the cron line's `PATH`; `vra setup --join` as that user |
| Gated tools are missing in an approval-gated session               | `VRA_MCP_ENABLE_GATED` is not `true` where the server starts | Set it as described for your agent system above                                                 |

For errors once the engine runs, see [Troubleshooting](troubleshooting.md).

## Related pages

-   [Prerequisites](prerequisites.md)
-   [Setup and joining](setup.md)
-   [Agent systems](agent-systems.md)
-   [CI/CD and AWS CodePipeline](codepipeline.md)
