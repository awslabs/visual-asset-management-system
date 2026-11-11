---
title: "FAQ"
sidebar_position: 22
description: "Answers to common questions about VAMS release automation."
---

# FAQ

## Does `vra` change VAMS?

No. It reads the VAMS repository, merges it into your fork, and runs the documented VAMS build, deploy
and migration commands. Your custom code lives in the VAMS fork lanes.

## Can the daily schedule deploy to test or production?

No. `vra watch` acts only on environments with `autoDeploy: true`, and the ledger validator rejects
`autoDeploy: true` on an environment that requires approval. Give the scheduled identity credentials for
the auto-deploy accounts only, so the restriction also holds at the IAM layer.

## Why is there a separate `dev-preview` environment?

It deploys upstream `development` with your custom code, so you see merge conflicts, configuration
changes and breakages before the next release ships. Its builds can never be promoted.

## Why not one branch per environment?

Environment branches let environments drift and make it hard to prove that production runs what was
tested. `vra` builds one artifact from one commit and promotes that artifact; environment differences
live in the ledger.

## Can I run all environments in one account?

Yes. Give each environment a unique `baseStackName` in its overlay `config.json`. VAMS appends the
Region to that name, so stacks and SSM parameters stay separate. One account per environment is still
recommended because it limits the impact of a mistake, separates permissions cleanly and lets you apply
different guardrails to production. An account is one trust boundary: an administrator of the account
can reach every environment in it, so a test environment in the production account is as sensitive as
production. See [Environments and topologies: accounts](environments-and-topologies.md#accounts).

## Which partitions and templates are supported?

Any. Each environment records its `partition` and `templateBase` (`commercial`, `govcloud`,
`eusovereign` or `custom`). ARNs are built from the partition, never hardcoded. Service and feature
availability differs between partitions; check the VAMS configuration templates and the AWS service
availability for your Region.

## What happens on a merge conflict?

Paths covered by `path-policy.yaml` are resolved automatically. Anything else is written to
`conflicts.json` and the run exits `20`. An agent proposes a resolution and runs the affected tests, or a
developer resolves it, `git add`s the files, then runs `vra merge --continue --run-id <run-id>`.
Before merging, `vra` also stops if the fork changed core files (outside the fork lanes) that are not
registered in `core-patches.md`, so no customer change is overwritten without review. See
[Branch strategy](branch-strategy.md).

## Can someone approve their own change?

No. The approver must be listed in the environment's `approvers` and must not be the run's initiator.
The initiator comes from the CI platform (AWS CodeBuild and AWS CodePipeline, GitHub Actions, GitLab) or, locally,
from the AWS identity that runs `vra`; a self-set `VRA_INITIATOR` cannot change it. Each approval names
the actions it covers (`scope`), expires, is used once, and in CI is tied to the pipeline run it was
given in. See [Approvals and gates](approvals-and-gates.md#approval-records).

## What happens if a deploy fails?

AWS CloudFormation rolls back the stack update. `vra` then applies the environment's `rollbackPolicy`:
`auto` redeploys the previous good build and restores the checkpoint if a migration ran; `ask` stops and
notifies. See [Workflows: rollback](workflows.md#rollback).

## Can every change be rolled back?

No. Amazon Cognito user pool schema changes and some data migrations are forward-only. `vra` stops an
in-place deploy whose `cdk diff` replaces or removes a data-bearing resource (override with
`--allow-replacements`, or use `--mode ab`), and stops before any migration whose upgrade-matrix entry
lists manual steps until you confirm with `vra resume <run-id> --confirm`. The diff report also flags
Amazon Cognito user pool schema changes and changed deletion/replace policies on data resources. Other
forward-only changes are not detected automatically; review the diff report and release notes before
approving. See [Backups, restore and migrations](backups-restore-and-migrations.md#forward-only-changes).

## How do several operators and a CI pipeline share one installation?

They point at the same shared home. With `sync.type: git`, the home is a private git repository: the
engine pulls before it reads and pushes after it writes, locks are created only if they do not exist,
and a run started on one machine is resumed on another only after its lease expired, with a recorded
takeover. Join a new machine with `vra setup --join <git-url>`. See [Shared home](shared-home.md).

## Do I need Amazon S3 and Amazon DynamoDB for the state?

No. The shared home is the default backend. `stateStore.type: s3` is an optional backend for
installations that run many CI jobs in parallel or want the state in AWS.

## Does production-to-test copy include users?

No. Amazon Cognito users are never copied. User-role assignments can optionally be copied when the same
user identities exist in the target, for example with a shared external identity provider.

## My accounts do not let me create roles. Can I still use `vra`?

Yes. Preflight and every run turn permission gaps into an IT request with the exact actions, resources
and a least-privilege policy, then park the run until you resume it. See
[Permissions and IT requests](permissions-and-it-requests.md).

## IT creates every IAM role. Can VAMS still be deployed?

Yes. Set `iam.mode` in the environment's `env.yaml` (`precreated`, `boundary`, `bootstrap` or a
combination). `vra iam plan` writes the full list of roles and policies for IT, `vra iam apply` (run by a
person) wires the names IT returns into the VAMS configuration, and every build stops when an upgrade
needs a role IT has not created yet. See [IAM for strict environments](iam-for-strict-environments.md).

## Can I run the whole release from a pipeline instead of an agent?

Yes. `automation.mode` selects `agent`, `hybrid`, `pipeline` or `pipeline+actions`; `vra pipeline plan`
and `vra pipeline deploy` create the AWS CodePipeline from the ledger. The gates and the ledger are the
same in every tier. See [Automation tiers](automation-tiers.md).

## Do I need an AI agent?

No. The engine runs on its own, and the CI templates run without one. An agent adds value for conflict
resolution, failure triage and report writing.

## Which agent should I choose?

The one your team already uses. Codex, Claude Code, Kiro and Cursor share one skill and the same MCP
server, so they get the same behavior. See [Agent systems](agent-systems.md).

## Where are secrets stored?

In AWS Secrets Manager or AWS Systems Manager Parameter Store. The ledger stores only their names, and
`vra ledger validate` rejects values that look like secrets.

## How do I contribute a fix back to VAMS?

Cut a branch from `vendor/vams-development`, remove anything customer-specific, follow the upstream
`CONTRIBUTING.md`, and target `development`. Report security issues privately. See
[Branch strategy](branch-strategy.md#contribute-generic-fixes-upstream).

## Does using `vra` make my system compliant?

No. It provides controls and evidence for the change path. See
[Controls mapping](controls-mapping.md).
