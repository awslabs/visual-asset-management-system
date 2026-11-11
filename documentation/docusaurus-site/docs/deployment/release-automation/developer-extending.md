---
title: "Developer guide: extending"
sidebar_position: 25
description: "How to add a git host, a CI provider, an agent system, a write-freeze backend, a test suite, an IAM action or a ledger core patch to release automation."
---

# Developer guide: extending

Each extension point is an interface in the engine plus a schema entry, a test and a documentation row.
Keep every rule in the engine; adapters, templates and the skill only call it.

## Git hosts

A git host adapter opens or finds the pull request for a sync branch. It never pushes; `gitops.py`
pushes with plain pushes.

1. Subclass `GitHost` in `engine/vra/githost.py` and implement `find_pr(head, base)` and
   `create_pr(head, base, title, body)`, each returning `{"url", "number", ...}`. `ensure_pr` combines
   them and handles `--dry-run`.
2. Register the class in `ADAPTERS` under its `githost.type`.
3. Add the type to the `githost.type` enum in `engine/vra/data/schemas/vra.schema.json`, to the choices
   in `setup.py`, and any keys it needs (for example a token secret name; never a token).
4. Read credentials from the platform's CLI login or from AWS Secrets Manager by name, never from the
   ledger.
5. Add tests in `engine/tests/test_gitops.py` with a fake shell or a stubbed client, and a row in
   [Configuration reference: vra.yaml](configuration-reference.md#vrayaml).

## CI providers

The engine needs two facts from a CI platform: who started the run (the initiator) and which run it is
(the run reference that approval records are bound to).

1. Detect the platform in `identity.ci_provider()` from a variable the platform sets and a job cannot
   fake for another platform.
2. In `identity.resolve()`, build the `Identity` from the platform's own actor variable and a stable
   `run_ref` (`<platform>:<project>/<run id>`). Never trust a variable the job itself sets for the
   initiator.
3. Add an approval writer to
   [`approval_record.py`](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsReleaseAutomation/cicd/codebuild/scripts/approval_record.py)
   that reads the platform's approval through its API and writes a record with `source` equal to the
   run reference.
4. Add a template under `cicd/<platform>/` that renders its stages from `vra ledger graph`, runs one job
   per environment with that environment's role, never gives the build job gated credentials, and
   pulls and publishes the shared home with `cicd/codebuild/scripts/vra-home.sh`.
5. Test the identity rules in `engine/tests/test_release_gates.py` and the template in
   `cicd/tests/test_ci_lanes.py`.

A platform that cannot meet step 1 uses the generic contract instead (`VRA_CI_PROVIDER=generic`); see
[CI/CD and AWS CodePipeline: generic CI](codepipeline.md#generic-ci).

## Agent systems

The package supports Codex, Kiro, Claude Code and Cursor. The Cursor adapter is the most recent example of
the full set of steps:

1. Add `adapters/<agent>/` with the agent's instruction file pointing at `skill/SKILL.md` (Cursor: one
   always-applied rule in `.cursor/rules/`), its MCP configuration for `vra mcp` (read and write tiers
   allowed without prompts, gated tier off), its permission lists, and a pre-execution hook that runs
   `adapters/shared/vra-guard.py`. Keep commands as thin pointers (Cursor: one skill per command with
   `disable-model-invocation: true`).
2. If its hook event names the shell command differently, extend `extract_command()` in the guard and add
   a case to `adapters/shared/test_vra_guard.py`. If the agent expects a JSON decision instead of an exit
   code (Cursor: `{"permission": "allow" | "deny" | "ask"}`), add an output format next to `--format cursor`.
   If it has a hook for MCP calls, map it to the guard's MCP policy: `MCP_ALLOW` and `MCP_ASK` must equal
   the Claude Code `mcp__vra__*` allow and ask lists (a test asserts it).
3. Generate permission lists from the Claude Code settings when the agent's format allows it
   (`adapters/cursor/gen_cli_json.py`; its `--check` and `adapters/shared/test_cursor_adapter.py` fail when
   the generated file is stale).
4. Add an `--agent <name>` branch to `adapters/shared/vra-cron.sh` and an `install.sh` case, and the
   installed paths to the package `.gitignore`.
5. If the agent has a permission deny list, add it to `scripts/check_agent_policy.py`, which then checks
   that it denies every human-only action, and that its hooks run the guard fail-closed.
6. Document it in the adapter README, in [Installation](installation.md) and in
   [Agent systems](agent-systems.md).

When a new human-only action or MCP tool appears, update every adapter in the same change: the skill's
human-only list, the Claude Code deny (or allow) list, every Kiro agent (`adapters/kiro/generate_agents.py`,
then `kiro-cli agent upgrade`), the Codex rules, the guard tables, and the regenerated Cursor list.
`make check` fails while one of them is missing.

## Write-freeze backends

A write-freeze backend stops writes to an environment while restore or data copy clears and reloads its
tables.

1. Subclass `WriteFreeze` in `engine/vra/freeze.py`: `supported()` returns whether the deployed
   environment offers the mechanism (detect it; never assume it), `engage(step_id, purpose)` stops writes
   and returns a JSON-able record, `release(record)` lets writes resume. Both must be idempotent, because
   a resumed run calls them again.
2. Register it in `BACKENDS`, and add the name to the `writeFreeze.backend` enum in
   `env.schema.json` and to the `--write-freeze` choices in `cli.py`.
3. Add the IAM actions it needs to `iam/actions/restore.yaml` and `data-copy-target.yaml` with a
   `feature:` key, so preflight simulates them only when the environment uses the backend, and copy the
   manifests to `engine/vra/data/iam-actions/` (the check compares them).
4. Test engage, release, resume and failure in `engine/tests/test_freeze_restore_signing.py`.

## Test suites

-   **Unit suites** are data: add an entry to `engine/vra/data/suites.yaml` with a list of
    `{cwd, cmd}` steps, optional `junit` (the flag that makes the tool write JUnit), `optionalPath` (skip
    when the file does not exist at the built commit) or `optionalTool`. Add it to `defaultBuildSuites`
    only when every fork should run it. `vra ledger validate` rejects unknown suite names in
    `build.unitSuites`.
-   **Live suites** are code in `engine/vra/tests_runner.py`. A live suite must record executed and passed
    counts, write JUnit, never print credentials, and refuse to create data outside a dedicated test
    database on approval-gated environments.
-   Mirror a change to VAMS's own `.github/workflows/ci.yml` in `suites.yaml` in the same release.

## IAM actions

Every AWS action a workflow calls is listed in `iam/actions/<workflow>.yaml`, the source the role
templates, preflight and IT requests are built from. When code calls a new action:

1. Add the statement to the manifest (`sid`, `actions`, `resources` with placeholders such as
   `${CoreStack}` and `${RegisteredBucket}`, optional `forEach`, `feature` or `optional`).
2. Copy the manifest to `engine/vra/data/iam-actions/`; `make check` fails when the copies differ.
3. Add the action to the matching role template in `iam/cloudformation/` and run the IAM tests.

## Ledger core patches

A ledger core patch is a unified diff in `<ledger>/core-patches/<name>.patch` that `core-patches.md` lists on
a bullet line. `engine/vra/core_patches.py` applies the listed patches to the scratch copy of the fork in
`vra build` (step `build.source`) and `vra iam plan`, records them in the build manifest (`corePatches`),
and treats a patch whose reverse applies as already `present`. When you ship a patch with the package:

1. Make it against the oldest supported VAMS version, with `a/` and `b/` prefixes and repository-relative
   paths, under `ledger-template/core-patches/`, with a header that names the upstream issue.
2. Add a template entry in a fenced block of `ledger-template/core-patches.md`; fenced lines are never
   read, so the template ledger stays inactive.
3. Prove the effect on the synthesized templates, not on a version string (the boundary patch is checked
   by `iam.check_assembly`), and test it against the real VAMS source in `engine/tests/fixtures/`
   (`engine/tests/test_core_patches.py`).

## Related pages

-   [Developer guide: architecture](developer-architecture.md)
-   [Developer guide: testing and release](developer-testing-and-release.md)
