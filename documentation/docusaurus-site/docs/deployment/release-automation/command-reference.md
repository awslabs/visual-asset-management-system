---
title: "Command reference"
sidebar_position: 20
description: "Every vra command and flag, the JSON output, and the exit codes scripts, pipelines and agents branch on."
---

# Command reference

`vra --help` and `vra <command> --help` on the installed version are authoritative. Commands marked
human-only are refused for agents and need an interactive terminal or `--human-approval` (see
[Approvals and gates: human-only actions](approvals-and-gates.md#human-only-actions)).

## Global flags

Global flags go before or after the command.

| Flag                        | Meaning                                                                                                       |
| --------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `--home <dir>`              | Shared home (default `VRA_HOME`, else the home `vra setup` registered in `~/.config/vra/config.yaml`)         |
| `--ledger <dir>`            | Ledger directory without a shared home (single-machine mode; default `VRA_LEDGER` or `./ledger`)              |
| `--work-dir <dir>`          | Machine-local work directory (default `VRA_WORK_DIR`; `~/.cache/vra/<home id>` with a home, `./.vra` without) |
| `--json`                    | JSON envelope on stdout; logs on stderr                                                                       |
| `--dry-run`                 | Change nothing; show what would happen                                                                        |
| `--non-interactive`         | Never prompt                                                                                                  |
| `--run-id <id>`             | Explicit id of a new run, or the target run of `merge --continue` / `--abort`                                 |
| `--lock-wait <minutes>`     | Wait for a busy lock instead of stopping                                                                      |
| `--human-approval <record>` | Approval record (subject `human:<action>`) that authorizes a human-only action without a terminal             |
| `--version`                 | Print the version                                                                                             |

## Setup and the shared home

| Command                                                                                                     | Purpose                                                                                                                    |
| ----------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `vra setup [--answers file.yaml] [--skip-preflight] [--force]`                                              | Create the shared home and ledger, validate, preflight ([Setup](setup.md)); `--force` overwrites ledger files (human-only) |
| `vra setup --join <path-or-git-url> [--branch B]`                                                           | Point this machine at an existing home                                                                                     |
| `vra home show`                                                                                             | Path, home id, sync type, lease, registered hosts                                                                          |
| `vra home sync`                                                                                             | Pull, then publish anything written locally                                                                                |
| `vra home migrate --from <old ledger> [--old-work-dir <dir>] [--to <new home>] [--sync none\|git\|command]` | Build a home from a ledger without one (human-only)                                                                        |
| `vra mcp`                                                                                                   | Run the MCP server on stdio ([Agent systems](agent-systems.md#the-mcp-server))                                             |

## Ledger

| Command                                                  | Purpose                                                                                                  |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `vra ledger validate [--env E]`                          | Schema, gates, identity, topology, naming, IAM settings, automation, core patches and secret scan        |
| `vra ledger show [--env E]`                              | Effective settings; per environment its tier, gate, required suites, stack names and current deploy      |
| `vra ledger diff --env E`                                | The overlay now vs the overlay of the last successful deploy                                             |
| `vra ledger graph [--env E]`                             | Promotion topology: every environment with its parent, gate and tier                                     |
| `vra ledger import --env E --from <config.json>`         | Import an existing VAMS configuration into the overlay (human-only)                                      |
| `vra ledger upgrade-config --env E --to-ref R [--write]` | Report new and removed configuration keys at an upstream ref; `--write` writes the proposal (human-only) |

## Release workflow

| Command                                                                                                                                                                  | Purpose                                                                                          |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| `vra watch [--lanes release,preview] [--suites S] [--no-notify] [--print-cron]`                                                                                          | One scheduled cycle: sync, merge, build, deploy and test the auto-deploy environments, report    |
| `vra sync`                                                                                                                                                               | Fetch upstream, fast-forward the vendor mirrors, report new releases and preview commits         |
| `vra merge --lane release\|preview [--ref R]`                                                                                                                            | Merge an upstream ref into `sync/<lane>-<ref>`                                                   |
| `vra merge --continue`, `vra merge --abort`                                                                                                                              | Continue or abandon a parked merge (with `--run-id`)                                             |
| `vra build --lane L --ref R [--suites S\|none] [--envs E1,E2] [--allow-missing-scanners]`                                                                                | Unit suites, per-environment synth, scans, bundle, tag; `--allow-missing-scanners` is human-only |
| `vra deploy --env E --build B [--approval A] [--mode in-place\|ab] [--emergency] [--allow-replacements] [--accept-drift] [--abort-workflow-executions]`                  | Deploy a build to an environment                                                                 |
| `vra test --env E [--suites unit,synth,live-cli,live-web,customer]`                                                                                                      | Run suites against an environment                                                                |
| `vra promote --from E1 --to E2 --build B --approval A [--mode in-place\|ab] [--emergency] [--allow-replacements] [--accept-drift] [--abort-workflow-executions]`         | Promote the same build                                                                           |
| `vra rollback --env E [--to-build B] [--restore-checkpoint C] [--approval A] [--restore-approval R] [--write-freeze B] [--writes-stopped] [--abort-workflow-executions]` | Redeploy the previous good build, optionally restore data                                        |

`--emergency` deploys outside the change window with an approval scoped `emergency`.
`--allow-replacements` and `--accept-drift` override the data-protection and drift gates with an
approval scoped to them on gated environments. `--abort-workflow-executions` and `--writes-stopped` are
human-only.

## Data

| Command                                                                                                                                                                                              | Purpose                                                                                                                                                                              |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `vra backup --env E`                                                                                                                                                                                 | Take a checkpoint                                                                                                                                                                    |
| `vra restore --env E --checkpoint C [--approval A] [--include-auth-tables] [--write-freeze auto\|maintenance-mode\|manual] [--writes-stopped] [--abort-workflow-executions]`                         | Restore an environment to a checkpoint                                                                                                                                               |
| `vra migrate --env E [--from V] [--to V] [--build B \| --source DIR]`                                                                                                                                | Chain the VAMS data migrations between two versions; unset values come from the deploy history or, for an imported environment, the deployed core stack and the build or source tree |
| `vra data-copy --from E1 --to E2 --approval A [--source-approval S] [--databases ids] [--mask-hook cmd] [--method export\|scan] [--write-freeze B] [--writes-stopped] [--abort-workflow-executions]` | Copy data between environments                                                                                                                                                       |

## Permissions, runs and history

| Command                                                       | Purpose                                                                                                                           |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `vra preflight --env E [--workflow W]`, `vra preflight --all` | IAM simulation of what a workflow needs; IT request on gaps                                                                       |
| `vra resume <run-id> [--confirm] [--takeover]`                | Continue a parked or failed run; `--confirm` and `--takeover` are human-only; refused with `--dry-run` (a resume changes the run) |
| `vra status [run-id] [--all]`                                 | Run state; `--all` lists every run and lock of every machine, and orphaned runs                                                   |
| `vra report [run-id] [--notify]`                              | Markdown and JSON report; `--notify` sends it                                                                                     |
| `vra unlock --env E [--run R] --reason TEXT`                  | Release a lock held by a parked or failed run, recorded in history (human-only)                                                   |
| `vra history show --env E`                                    | The environment history in sequence order                                                                                         |
| `vra history verify [--env E]`                                | Check every history record; warns about orphaned runs                                                                             |

## IAM roles from IT

| Command                                                                                        | Purpose                                                                                                                                                                                                                                                                                               |
| ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `vra iam plan --env E [--mode boundary\|precreated\|bootstrap\|all] [--ref R] [--report PATH]` | Discovery synth of the ref with the environment's overlay (no AWS writes), then the IT role package `state/iam/<env>/<n>/`; `--mode` combines with `+`; `--report` reads an existing `iam-policy-report.json`                                                                                         |
| `vra iam apply --env E --from <it-response.yaml> [--package N] [--approval A]`                 | Check IT's response against the package and write the ledger overlay, `applied.json` and an `iam-apply` history record (human-only); gated environments need a record with subject `iam-apply:<packageId>:<responseSha256[:16]>`; `--dry-run` shows the overlay, the response SHA-256 and the subject |
| `vra iam verify --env E`                                                                       | Read-only: the roles exist, carry the boundary, trust the right principals and allow what the package needs, and grant nothing beyond it (extra principals, policies, actions); exit `10` on problems, unreadable items listed as not checked                                                         |

See [IAM for strict environments](iam-for-strict-environments.md).

## Pipelines

| Command                                                                                                   | Purpose                                                                                                                                                                                                              |
| --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `vra pipeline plan [--offline]`                                                                           | Render the pipeline from `automation.pipeline` and `vra ledger graph`, synthesize it offline, compare it with the deployed stack, write the pipeline's IT package                                                    |
| `vra pipeline deploy [--plan ID] [--approval A]`                                                          | Deploy the planned template into the tools account; human-only for a first deploy, IAM changes and removed approvals, compared again with the deployed stack at deploy time (record subject `human:pipeline-deploy`) |
| `vra pipeline status`                                                                                     | Stack outputs, stage states, pending approvals, recent executions, topology drift                                                                                                                                    |
| `vra pipeline sync-ledger [--deploy] [--approval A]`                                                      | Plan again after a topology change in the ledger; exit `20` until deployed                                                                                                                                           |
| `vra pipeline push-source [--lane release\|preview] [--package] [--bucket B] [--key K] [--pipeline NAME]` | Upload the fork branch (or the package with `--package`) to an Amazon S3 source the stack created and start the pipeline                                                                                             |

`vra pipeline` commands record plans and deploys under `state/pipeline/` instead of creating runs. Agents
may run `plan`, `status` and `sync-ledger`; the adapters ask a person before `deploy`, `push-source` and
`sync-ledger --deploy`. See
[CI/CD and AWS CodePipeline](codepipeline.md).

`vra credential-process` is internal: the engine's child processes use it, and it prints nothing
without the engine's per-process nonce.

## JSON output

With `--json`, stdout is one JSON object:

```json
{
    "exitCode": 0,
    "status": "SUCCEEDED",
    "runId": "build-20260110T101500Z-1a2b",
    "workflow": "build",
    "runDir": "/home/ops/vams-release-home/state/runs/build-20260110T101500Z-1a2b",
    "message": "build cust-vX.Y.Z-1 written",
    "result": {},
    "buildId": "cust-vX.Y.Z-1"
}
```

`buildId` appears for build, deploy and promote runs, `prUrl` for merge runs that opened or found a pull
request, and `itRequests` when a run has open IT requests. Uncaught errors still print the envelope with
`exitCode: 1`.

## Exit codes

| Code | Status            | Meaning                                                                                                                                                                                                                          | What to do                                                                                |
| ---- | ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `0`  | `SUCCEEDED`       | Done                                                                                                                                                                                                                             | Continue                                                                                  |
| `10` | `BLOCKED_ON_IT`   | A permission is missing, or the environment needs IAM roles from IT; an IT request was written                                                                                                                                   | Send the request (or the IT role package) to IT; `vra resume <run-id>` when it is granted |
| `20` | `NEEDS_HUMAN`     | A merge conflict, a missing or invalid approval, a change window, a held lock, contention with another machine, manual work, a human-only action, a core patch that no longer applies, IAM roles without the configured boundary | Read `vra status <run-id>`; act on the message                                            |
| `30` | `TESTS_FAILED`    | Tests or a smoke failed; rolled back when `rollbackPolicy: auto`                                                                                                                                                                 | Triage the JUnit results; roll back or fix forward                                        |
| `40` | `ROLLBACK_FAILED` | A rollback failed; the environment needs a person now                                                                                                                                                                            | [Troubleshooting: failed rollback](troubleshooting.md#failed-rollback)                    |
| `2`  | -                 | Usage error                                                                                                                                                                                                                      | Correct the command                                                                       |
| `1`  | `FAILED`          | Any other error                                                                                                                                                                                                                  | Read the step log                                                                         |

Other run statuses are `RUNNING` and `ROLLED_BACK` (a failed change was rolled back; the command exits
`30`). Through the MCP server, a non-zero code is a tool error with `exitCode` and `meaning`.

## Related pages

-   [Workflows](workflows.md)
-   [Configuration reference](configuration-reference.md)
