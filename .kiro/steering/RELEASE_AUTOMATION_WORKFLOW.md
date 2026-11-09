# VAMS Release Automation Development Workflow & Rules

This document provides the guidelines for developing and extending the VAMS release automation tool in
`tools/VamsReleaseAutomation/`: the `vra` engine and its MCP server, its agent skill and adapters, its
CI/CD lanes and its IAM templates.

> **Steering Document Sync (bidirectional):** This document mirrors the Claude Code steering in `tools/VamsReleaseAutomation/CLAUDE.md` (and cross-cutting rules in the root `CLAUDE.md`). Whenever you change a rule, pattern, or convention here, make the equivalent change in `tools/VamsReleaseAutomation/CLAUDE.md` in the same change — and whenever that `CLAUDE.md` file changes, reflect it back here. Keep the two documents saying the same thing.

## Project Overview

VamsReleaseAutomation keeps a customer fork of VAMS current with upstream releases and moves each
change through the customer's environments. It has one engine and several thin front-ends:

-   **Engine**: the `vra` CLI, Python package `vra` under `engine/vra/` (entry point `vra.cli:main`).
    Every state change (sync, merge, build, deploy, migrate, back up, restore, copy data, promote,
    roll back) is a deterministic, resumable command with a dry-run mode and stable exit codes.
-   **MCP server**: `vra mcp` (`engine/vra/mcp_server.py`, extra `[mcp]`) exposes the engine as stdio MCP
    tools in read, write and gated tiers; every tool runs the CLI as an agent, so the gates are the same.
-   **Skill**: `skill/SKILL.md` and one SOP per workflow in `skill/workflows/`. The single source of
    agent behaviour for Codex, Claude Code, Kiro and Cursor.
-   **Adapters**: `adapters/{codex,claude,kiro,cursor}/` point an agent host at the skill and wire the MCP
    server; `adapters/shared/` holds the command guard hook and the cron script; `adapters/install.sh`
    installs one adapter. They never restate the skill.
-   **CI/CD lanes**: `cicd/` wraps the same CLI for AWS CodeBuild, the AWS CodePipeline CDK app
    (`cicd/pipeline/`, rendered and deployed by `vra pipeline` from `vra.yaml` `automation`), GitHub
    Actions samples for GitHub Enterprise Cloud and Server, GitLab CI and generic CI (Jenkins, Azure
    Pipelines). `automation.mode` (`agent`, `hybrid`, `pipeline`, `pipeline+actions`) names the tier.
-   **IAM**: `iam/actions/` (per-workflow action manifests that `vra preflight` simulates),
    `iam/cloudformation/` (deploy, data-copy, state-store and CDK execution-policy templates) and
    `iam/it-roles/` (inputs of the IT role package). `vra iam plan | apply | verify` (`engine/vra/iam.py`)
    drive VAMS's own IAM modes (boundary, pre-created roles, custom bootstrap roles) for strict accounts.

Customer data lives in the **shared home** folder (`VRA_HOME`), never in this directory: `vra-home.yaml`
(sync settings, hosts), `ledger/` (accounts, Regions, approvers, configuration overlays, approvals) and
`state/` (locks, runs, history, consumed approvals). `ledger-template/` is the ledger's skeleton.

The user and developer guides are in the documentation site:
`documentation/docusaurus-site/docs/deployment/release-automation/` and
`documentation/docusaurus-site/docs/deployment/productionizing/`.

---

## Architecture

### Directory Structure

> **Maintenance note:** Update this tree when adding modules, workflows or lanes. `tools/VamsReleaseAutomation/FILES.md` lists every
> file and is regenerated with `make index`; `make check` fails when it is out of date.

```
tools/VamsReleaseAutomation/
  pyproject.toml             # engine packaging, exact pins, pytest config (strict markers, `temporary`)
  Makefile                   # make check | test | install-dev | index
  FILES.md                   # generated file index (scripts/gen_index.py)
  engine/
    vra/
      cli.py                 # argparse parser and dispatch; --json on stdout, logs on stderr
      runner.py              # workflow registry, run execution, status -> exit code, `vra resume`
      errors.py              # exit codes and exception types (the agent contract)
      state.py               # runs (rev, create-only), locks + lease keeper, history, approval claims, backends
      home.py                # the shared home: layout, sync (none | git | command), join, migrate
      human.py               # human-only actions: refused for agents, TTY or --human-approval otherwise
      iam.py                 # `vra iam` plan, apply, verify: IT role package, overlay wiring, IAM gates
      core_patches.py        # ledger core patches (core-patches/*.patch) applied to build and plan sources
      pipeline.py            # automation.mode validation, rendered pipeline config, plan/deploy/status
      pipeline_cli.py        # `vra pipeline` subcommands; pipeline_iam.py: the pipeline's IT package
      mcp_server.py          # `vra mcp`: tool tiers over the CLI, structured tool errors
      ledger.py              # ledger loading, validation, import, upgrade-config
      approvals.py           # approval records and separation of duties
      identity.py            # initiator resolution (the separation-of-duties input)
      gitops.py, githost.py  # sync, merge with path policy, pull requests per git host
      build.py, deploy.py    # build once (synth per env, bundle, tag); deploy, promote, rollback
      migrate.py             # VAMS data-migration discovery and chaining
      backup.py, restore.py  # checkpoints and restore
      datacopy.py, freeze.py # data copy between envs; write freeze and workflow executions
      buckets.py             # registered asset buckets
      permissions.py         # denial classification, IAM simulation, IT requests
      preflight_cmd.py       # `vra preflight`
      tests_runner.py        # VAMS unit suites, live smokes, customer suites
      report.py, watch.py    # reports and notifications; the cron entry point
      setup.py               # first-run wizard
      aws.py                 # partition-generic sessions and ARN helpers, credential_process
      vams_resources.py      # resolve a deployed stack's resource names from SSM
      sshsig.py, cron.py, util.py, context.py
      data/                  # schemas, default path policy, IAM manifest copies, suites, upgrade matrix
    tests/                   # engine tests (moto for AWS; no network)
  skill/                     # SKILL.md + workflows/W0..W10 and helper SOPs
  adapters/                  # codex/, claude/, kiro/, cursor/ front-ends (MCP wiring, permissions, guard hook);
                             #   shared/vra-guard.py + tests, shared/vra-cron.sh; install.sh;
                             #   kiro/generate_agents.py (source of the Kiro agent files);
                             #   cursor/gen_cli_json.py (Cursor CLI permissions from the Claude settings)
  cicd/                      # codebuild/, pipeline/ (CDK app), github-actions/, gitlab-ci/, generic/, oidc/
  iam/                       # actions/ (reviewed manifests), cloudformation/, it-roles/, scripts/
  ledger-template/           # skeleton of the customer's private ledger; core-patches/ (shipped patches)
  scripts/                   # check.sh and its helpers (doc commands, agent policy, files, index); tests/
```

### Layers

1. `cli.py` parses arguments and hands a `Context` (`context.py`) to a workflow in `runner.py`.
2. A workflow is a sequence of named, checkpointed steps (`state.py`). A step that fails or needs a
   decision parks the run with a status; `vra resume` re-runs the gates and continues.
3. Workflow modules (`build.py`, `deploy.py`, `datacopy.py`, ...) call AWS only through `aws.py`
   sessions and git only through `gitops.py` / `githost.py`, so tests can inject fakes.

---

## Mandatory Rules

1. **The exit codes are the agent contract.** `0` OK, `1` other error, `2` usage error, `10`
   BLOCKED_ON_IT, `20` NEEDS_HUMAN, `30` TESTS_FAILED / ROLLED_BACK, `40` ROLLBACK_FAILED
   (`errors.py`). Never renumber one or reuse it for a new meaning. A new exit code lands in the same
   change in `errors.py`, `skill/SKILL.md`, every adapter that branches on it, the CI lanes
   (`cicd/codebuild/scripts/run-vra.sh`, the GitHub Actions and GitLab CI references) and the workflows
   page of the user guide.
2. **Gates are enforced in the engine.** Approval binding, separation of duties, promotion order,
   change windows, `autoDeploy` and every human-only action (`human.HUMAN_ONLY`) are checked in `vra`
   code. An adapter, a slash command, a CI lane, the MCP server or the guard hook may add a check, never
   replace one. A gate that exists only in front-end instructions is a defect.
3. **The human-only set is one set.** It is defined by `engine/vra/human.py` and named in
   `skill/SKILL.md` (between the `human-only` markers); the MCP server exposes no tool or parameter for
   any of it, and the guard, the Kiro agents, the Codex rules, the Claude Code settings and the Cursor CLI
   permissions deny it.
   `scripts/check_agent_policy.py` (in `make check`) fails on a difference, and on a workflow step the
   guard blocks that is not handed to a person.
4. **The shared home is the state.** Every state write goes through `state.Backend` (compare-before-write
   on runs, create-only records, owner-token locks) and is published by `Home.publish`; nothing writes
   the ledger except a person and `vra setup`. Two-machine behaviour is tested with two homes cloned
   from one bare remote.
5. **No AWS calls and no network in tests.** Tests use moto and injected fakes. `scripts/check.sh`
   points `AWS_ENDPOINT_URL` at a closed local port so a missed mock fails instead of reaching AWS.
6. **The reviewed IAM manifests are the source.** `iam/actions/*.yaml` is what reviewers read;
   `engine/vra/data/iam-actions/` is a byte-identical copy the installed engine reads. Edit
   `iam/actions/` and copy it over. `ledger-template/path-policy.yaml` and
   `engine/vra/data/defaults/path-policy.yaml` are identical in the same way. `make check` fails on a
   difference.
7. **Partition-generic ARNs.** Code and deployable templates derive the partition (`aws.py`,
   `Aws.PARTITION`, `${AWS::Partition}`); `arn:aws:` appears only in tests, examples and docs. The
   neutrality scan enforces it for `engine/vra/`, `adapters/shared/`, `cicd/` code and `iam/`.
8. **Customer-neutral.** Placeholder accounts only (`111111111111` to `555555555555`), `example.com`
   identities, no customer names. A customer keeps private terms out of the package with
   `VRA_NEUTRAL_TERMS_FILE`, never by adding them to the scanner.
9. **The skill is the only source of agent behaviour.** A new rule for agents goes into
   `skill/SKILL.md` or the workflow SOP; the adapters keep pointing at it. Every `vra ...` command in
   the Markdown (skill, adapters, docs) is checked against the real parser by
   `scripts/check_doc_commands.py`, so a renamed flag fails `make check`.
10. **Exact pins.** Python dependencies are exact in `pyproject.toml`. The CDK app
    (`cicd/pipeline/`) uses exact versions, `engine-strict` and `min-release-age = 7` in its `.npmrc`,
    and a lockfile resolved under that policy.

---

## Common Changes

### Adding a CLI command or flag

1. Add the subcommand or argument in `cli.py` and the workflow in `runner.py`.
2. If it changes state, make it a checkpointed run with `--dry-run` support.
3. If only a human may use it, add it to `human.HUMAN_ONLY` and call `human.require`, add it to the
   skill's human-only list, deny it in the guard (`adapters/shared/vra-guard.py`), the Kiro agents
   (`adapters/kiro/generate_agents.py`, then `kiro-cli agent upgrade`), the Codex rules and the Claude
   Code settings, regenerate the Cursor CLI permissions (`python3 adapters/cursor/gen_cli_json.py`), and
   add a sample to `scripts/check_agent_policy.py`. Never expose it as an MCP tool or parameter.
4. If agents may use it, add an MCP tool in the right tier of `mcp_server.tools()`, and list it in the
   Claude Code allow (or ask) list, the guard's `MCP_ALLOW` (or `MCP_ASK`) and the Kiro agents' tools.
5. Document it in the command reference and the workflow page of the user guide, and in the skill SOP
   that uses it.
6. Add tests in `engine/tests/` (and `adapters/shared/test_vra_guard.py` for a guarded flag).

### Adding a ledger field

Update the JSON schema in `engine/vra/data/schemas/`, `ledger.py` validation, `ledger-template/`, the
setup wizard if it asks for the value (`setup.py`), and the configuration reference page of the user
guide.

### Adding an IAM action

Add it to `iam/actions/<workflow>.yaml` and the matching `iam/cloudformation/` role, copy the manifest
to `engine/vra/data/iam-actions/`, and extend the permission tests (`test_permissions.py`,
`test_iam_and_ci_roles.py`).

### Changing an agent adapter

Keep adapters thin: instructions point at the skill, the MCP server is the primary surface, the CLI is
the fallback behind the guard. Kiro agent files are generated: edit `adapters/kiro/generate_agents.py`,
run it into a scratch directory, run `kiro-cli agent upgrade --no-backup <dir>` (adds the V3
permissions and hooks) and copy the result to `adapters/kiro/.kiro/agents/`. `make check` validates them
when `kiro-cli` is installed. Validate Claude Code files with `claude doctor` in an installed copy. The
Cursor CLI permissions are generated from the Claude Code settings: after changing those, run
`python3 adapters/cursor/gen_cli_json.py` (`--check` and `adapters/shared/test_cursor_adapter.py` fail while
it is stale).

### Ledger core patches

`engine/vra/core_patches.py` applies the `core-patches/<name>.patch` files `core-patches.md` lists (bullet
lines outside fenced code blocks) to the scratch source of `vra build` and `vra iam plan`, never to the fork
clone; results `applied`, `present` (reverse applies, or every `Present-when:` marker in the patch's
preamble matches: upstream shipped the fix in another form) or exit `20`. A shipped patch lives in
`ledger-template/core-patches/` with a fenced template entry in `ledger-template/core-patches.md`, and its
effect is proven on the synthesized templates (CP-431, the boundary patch for GitHub issue #431, needed on
VAMS 2.6.6 and earlier and `present` by marker from 2.6.7: `iam.check_assembly`; CP-1210, the API
deployment patch for GitHub issue #1210), tested against real VAMS sources in `engine/tests/fixtures/iam/`.

### The CDK pipeline app (`cicd/pipeline/`)

A single-stack CDK app with cdk-nag `AwsSolutionsChecks`. Every suppression carries a specific reason
and an `appliesTo` scope (root `CLAUDE.md` Rule 4). Artifacts and logs use a customer-managed AWS KMS
key (Rule 8). The jest suite asserts that no unsuppressed AwsSolutions finding remains.

### Rolling the version

The tool is versioned independently of VAMS. The version is duplicated in three files that move
together: `pyproject.toml` (`[project]` `version`), `engine/vra/__init__.py` (`__version__`, what
`vra --version` reports) and `cicd/pipeline/package.json` (`version`, and the lockfile);
`cicd/tests/test_ci_v040.py` fails when they differ.

---

## Keeping Steering Documents in Sync

This document mirrors the Claude Code steering in `tools/VamsReleaseAutomation/CLAUDE.md`.
Synchronization is bidirectional (root `CLAUDE.md` Rule 11): a rule changed here lands there in the
same change, and the reverse.

The Claude Code command `/release-automation` (`.claude/commands/release-automation.md`) is a thin
entry point over `tools/VamsReleaseAutomation/skill/SKILL.md` (root `CLAUDE.md` Rule 12).
`skill/SKILL.md` has no Kiro mirror: the Kiro adapter loads it directly.

Update the user and developer guides under
`documentation/docusaurus-site/docs/deployment/release-automation/` when a workflow, flag, ledger
field, exit code or extension point changes.

---

## Testing

Work in a virtual environment of this package. Runtime is Python 3.12; development uses Python 3.13.
The guard (`adapters/shared/vra-guard.py`) is the exception: the hooks run it with the host's `python3`,
so it stays Python 3.9 compatible (`from __future__ import annotations`, standard library only) and fails
closed on any internal error; `adapters/shared/test_vra_guard.py` runs it under 3.9 when available.

```bash
cd tools/VamsReleaseAutomation
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv/Scripts/activate
pip install -e '.[dev]'
make check                       # every offline check below
python -m pytest                 # engine, guard and script tests only
python -m pytest -m temporary -q --collect-only
```

`make check` (`scripts/check.sh`) runs the engine, guard and script tests, parses every YAML and JSON
file, validates `ledger-template/` and its AWS GovCloud (US) variant, checks the identical copies,
checks every `vra ...` command in the Markdown against the parser, checks the agent policy, validates
the Kiro agent files (when `kiro-cli` is installed), checks `FILES.md`, resolves every
relative Markdown link and GitHub source link, and runs the neutrality scan. The scans cover this
directory and the package's own files elsewhere in the repository (its documentation pages, the Kiro
steering document, the Claude Code command and the CI workflow), named by explicit path. Set
`VAMS_REPO` to a VAMS clone to also verify links into other VAMS pages.

The CDK app has its own suite:

```bash
cd tools/VamsReleaseAutomation/cicd/pipeline
npm ci --ignore-scripts
npm run typecheck
npm test                         # jest, including the cdk-nag check
```

CI runs both in the release-automation jobs, on Python 3.12.7 and npm 11.19.1, and ESLint covers
`cicd/pipeline/{lib,bin,test}` with the root configuration. Lint the GitHub Actions samples with
`actionlint -shellcheck=shellcheck cicd/github-actions/*.yml` and `python3 cicd/github-actions/pins.py check`.

### Mark a Temporary Test with `@pytest.mark.temporary`

A test written to prove one specific change landed carries `@pytest.mark.temporary` (registered in
`pyproject.toml`; the suite runs `--strict-markers`) plus a line saying what it pins. The gate tests
(approval binding, separation of duties, the fail-closed diff, IAM escalation paths, the guard's deny
list) are durable: the thing each forbids stays writable. Full criterion: root `CLAUDE.md` Rule 13.

---

## Gold Standard References

-   Exit-code contract: `engine/vra/errors.py`
-   A checkpointed workflow with gates: `engine/vra/deploy.py`
-   Agent behaviour: `skill/SKILL.md`
-   Command guard: `adapters/shared/vra-guard.py`
-   Shared home and locks: `engine/vra/home.py`, `engine/vra/state.py`
-   MCP tiers: `engine/vra/mcp_server.py`
