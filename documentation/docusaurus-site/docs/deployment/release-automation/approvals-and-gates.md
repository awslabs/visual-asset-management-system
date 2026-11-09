---
title: "Approvals and gates"
sidebar_position: 9
description: "The gates the engine enforces, approval records and their scopes, separation of duties, change windows, signed builds and human-only actions."
---

# Approvals and gates

Every gate is enforced in the engine, in code, before the change it protects. The same gates apply
whether a person, a scheduled job, a CI pipeline or an agent runs the command.

## Gates

| Gate               | Rule                                                                                                                                                                                                              |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Approval           | Deploy, promote, rollback, restore, standalone migrate and data copy on an environment with `requiresApproval` need an approval record (below)                                                                    |
| Auto-deploy        | `vra watch` acts only on `autoDeploy` environments and never on an approval-gated one                                                                                                                             |
| Promotion evidence | A build deploys to an environment with `promotesFrom` only after it deployed and passed its required suites there; into a gated environment only from the fork trunk                                              |
| Preview            | Preview-lane builds are never promoted                                                                                                                                                                            |
| Data protection    | In-place deploys never replace or remove a data-bearing resource without `--allow-replacements`; a failed or unparseable diff stops the deploy                                                                    |
| Drift              | Drift stops a gated deploy unless `--accept-drift`                                                                                                                                                                |
| Change window      | `changeWindow` blocks deploys outside the window unless `--emergency` with an approval; re-checked before `cdk deploy` and before each migration                                                                  |
| Build provenance   | Gated environments refuse builds without a pinned CDK CLI and prod-class environments refuse builds with waived scanners; with `build.signing`, unsigned or invalid builds are refused                            |
| Checkpoints        | Gated environments refuse a checkpoint with warnings unless `backup.allowDegraded`                                                                                                                                |
| Write freeze       | Restore and data copy clear live tables only while writes are frozen ([Backups, restore and migrations](backups-restore-and-migrations.md#write-freeze))                                                          |
| Data copy          | Never into a prod-class environment; never into a bucket that is a source or another environment's ([Data copy](data-copy.md))                                                                                    |
| Registered buckets | A phase that touches bucket data stops with an IT request for a bucket or key the role cannot be shown to use ([Permissions](permissions-and-it-requests.md#registered-asset-buckets))                            |
| Shared home        | With `sync.type: git`, a gated action runs only on a clean ledger at the remote HEAD with the approval record committed                                                                                           |
| Engine version     | With `vra.yaml` `engineVersion`, gated actions refuse to run with another engine version                                                                                                                          |
| IAM package        | An environment with an `iam.mode` builds only with an applied, current IT role package, and only when every synthesized role carries its boundary ([IAM for strict environments](iam-for-strict-environments.md)) |
| Pipeline deploy    | `vra pipeline deploy` deploys only the planned template; a first deploy, IAM changes or removed approvals need a person ([Automation tiers](automation-tiers.md))                                                 |

The environment lock is taken before the gates, and the gates run again on every `vra resume`.

## Approval records

An approval record is a YAML file in `ledger/approvals/<env>/`, written by the approver and committed
through your review process:

```yaml
# ledger/approvals/prod/cust-vX.Y.Z-3.yaml
build: cust-vX.Y.Z-3 # gate subject (required)
env: prod # required
approver: release-manager@example.com # required; listed in approvers, not the initiator
approvedAt: "2026-01-10T15:00:00Z" # required
scope: [promote] # required
expiresAt: null # optional; default approvedAt + approvalMaxAgeHours (72)
ticket: CHG-0042
notes: UAT signed off in test.
source: null # the CI run it was given in; required in CI
masked: null # data copy: the masking state the approver saw
```

| Field               | Rule                                                                                                                                                                                      |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `build`             | The gate subject (table below)                                                                                                                                                            |
| `approver`          | Must match an entry of the environment's `approvers` (plain ids, IAM ARNs or ARN globs; an assumed-role ARN only matches an ARN entry)                                                    |
| `approvedAt`        | Refused when more than five minutes in the future                                                                                                                                         |
| `scope`             | `deploy`, `promote`, `rollback`, `restore`, `restore-auth-tables`, `data-copy`, `migrate`, `emergency`, `allow-replacements`, `accept-drift`, or a human-only action                      |
| `expiresAt`         | Refused after it; default `approvedAt` + `approvalMaxAgeHours`                                                                                                                            |
| `source`, `binding` | In CI, `source` must equal the current run (`codepipeline:<pipeline>/<execution>`, `github:<repo>/actions/runs/<id>`, `gitlab:<project>/pipelines/<id>`, or `VRA_RUN_REF` for generic CI) |
| `masked`            | Data copy only; must match the run's masking                                                                                                                                              |
| `signature`         | Required when the environment sets `approvalSigningKeyArn` (an AWS KMS signature of the record)                                                                                           |

| Action                               | Subject (`build`)          | Filed under     | Default file                    |
| ------------------------------------ | -------------------------- | --------------- | ------------------------------- |
| Deploy, promote, rollback            | the build id               | the target      | `<buildId>.yaml`                |
| Restore                              | `restore:<checkpointId>`   | the environment | `restore_<checkpointId>.yaml`   |
| Standalone `vra migrate`             | `migrate:<from>-<to>`      | the environment | `migrate_<from>-<to>.yaml`      |
| Data copy, target side               | `data-copy:<sourceEnv>`    | the target      | `data-copy_<sourceEnv>.yaml`    |
| Data copy, source data owner         | `data-copy-to:<targetEnv>` | the source      | `data-copy-to_<targetEnv>.yaml` |
| Human-only action without a terminal | `human:<action>`           | the environment | none; pass `--human-approval`   |

Without `--approval`, the engine looks for the default file. `--approval` takes a record id, an
absolute path or a path relative to the ledger. An `--approval` passed to an environment that does not
require one is still verified and recorded.

Each record authorizes one run. When the gate passes, the engine writes a claim
`state/approvals-consumed/<env>/<sha256>.json`; another run's claim blocks while that run is running or
once it recorded a change with the record. A rollback of a gated environment therefore needs a new
record: the one that approved the original deploy was consumed by it.

## Separation of duties

The approver must not be the run's initiator, and for gated environments must not be an author or
committer of the built commit (the manifest records them as `changeAuthors`). The initiator is taken
from the platform, never from a variable the job sets:

| Where            | Initiator                                                                                           |
| ---------------- | --------------------------------------------------------------------------------------------------- |
| Workstation      | The AWS STS caller identity; `VRA_INITIATOR` is an alias that is ignored when it names someone else |
| AWS CodePipeline | The execution's trigger, read with `codepipeline:GetPipelineExecution`                              |
| GitHub Actions   | `GITHUB_TRIGGERING_ACTOR` (else `GITHUB_ACTOR`)                                                     |
| GitLab CI        | `GITLAB_USER_LOGIN`                                                                                 |
| Generic CI       | `VRA_INITIATOR` with `VRA_RUN_REF`; the CI role's STS caller joins the names compared               |

Approval-gated sessions carry `sts:SourceIdentity` set to the initiator by default, so AWS CloudTrail in
the environment account attributes every call to a person. See
[Permissions and IT requests](permissions-and-it-requests.md#source-identity).

## In CI

When a run reference exists (any supported CI platform), a gated environment needs `--approval` naming
a record bound to this run; records found in the ledger are not bound to the run and are not used. The
CI templates write the record from the platform's own approval (CodePipeline manual approval, GitHub
environment reviewers ([GitHub Actions](github-actions.md)), GitLab deployment approvals) with
[`approval_record.py`](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsReleaseAutomation/cicd/codebuild/scripts/approval_record.py).
Generic CI is accepted for a gated environment only when it sets `allowGenericCi: true`. See
[CI/CD and AWS CodePipeline](codepipeline.md).

## Signed builds

`vra.yaml` `build.signing.kmsKeyArn` names one asymmetric AWS KMS key (key spec `ECC_NIST_P256`, usage
`SIGN_VERIFY`). The build signs every manifest, and with `build.signing.tags: true` writes the release tag
as an SSH-format signed tag that `git verify-tag` checks with the public key in
`signatures/<buildId>.tag-signer.pub`. Deploy, promote and rollback verify the manifest signature and
the tag (valid for the key, tag object equals the build commit, tag message names the manifest digest)
and refuse unsigned or invalid ones. Only build identities get `kms:Sign`; deploying identities get
`kms:Verify` and `kms:GetPublicKey`. Signing is optional; `vra ledger validate` warns for prod-class
environments without it.

## Human-only actions

Some flags and commands record that a person did something, waive a gate, take state from another run,
stop work in the environment, or rewrite the ledger. The engine allows them only from a person:

| Action                                                   | What it does                                                                                                                                                                                                                                                                  |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `vra resume <run-id> --confirm`                          | Records that a person did the manual work a parked step waits for                                                                                                                                                                                                             |
| `vra resume <run-id> --takeover`                         | Takes over a run from another machine after its lease expired                                                                                                                                                                                                                 |
| `vra unlock --env E --reason TEXT`                       | Releases a lock another run holds                                                                                                                                                                                                                                             |
| `--writes-stopped`                                       | Records that a person stopped writes to the environment                                                                                                                                                                                                                       |
| `--abort-workflow-executions`                            | Stops running VAMS workflow executions                                                                                                                                                                                                                                        |
| `--allow-missing-scanners`                               | Waives the build's security scanners                                                                                                                                                                                                                                          |
| `vra ledger import`, `vra ledger upgrade-config --write` | Rewrites an environment's VAMS configuration                                                                                                                                                                                                                                  |
| `vra setup --force`                                      | Overwrites ledger files                                                                                                                                                                                                                                                       |
| `vra home migrate`                                       | Builds a shared home from an older ledger                                                                                                                                                                                                                                     |
| `vra iam apply`                                          | Wires the roles IT returned into an environment's VAMS configuration; on gated environments also needs an approval record with subject `iam-apply:<packageId>:<responseSha16>` (the response's SHA-256, first 16 hex characters; `--dry-run` prints it) and scope `iam-apply` |

When `VRA_AGENT=1` or `VRA_MCP=1` is set (every agent adapter and the MCP server set them), the engine
refuses each of these with exit `20` and `result.humanOnly`. Otherwise a person confirms at their own
interactive terminal by typing the environment or action name. A typed confirmation is accepted only when
no agent or CI marker is set (for example `CLAUDECODE`, `CODEX_SANDBOX`, `CURSOR_AGENT`,
`VRA_AGENT_SESSION`, `CI`, `GITHUB_ACTIONS`, `CODEBUILD_BUILD_ID`), stdin and stderr are terminals that
are not the pseudo terminal of a wrapper (`script`, `unbuffer`, `expect`), and no agent CLI (`claude`,
`codex`, `kiro-cli`, `cursor-agent` and others) runs in the parent process chain; a chain that cannot be
read counts as an agent. In every other case (a CI job, or an agent that cleared `VRA_AGENT`) the action
needs `--human-approval <record>`: an approval record with subject `human:<action>` and scope
`[<action>]`, checked like every other record and single use.

`--emergency`, `--allow-replacements`, `--accept-drift` and `--include-auth-tables` need an approval
record scoped to them on gated environments; agents never pass them.

`vra pipeline deploy` is human-only only for some plans: a first deploy of the pipeline stack, a plan that
changes IAM resources, or one that removes a manual approval (action `pipeline-deploy`). For those, a
person confirms at a terminal or passes `--approval` with a record of `automation.approvalEnv` whose
subject is `human:pipeline-deploy` and scope `pipeline-deploy`. The engine decides this from the
deployed stack as it is at deploy time, not from the stored plan: a plan whose comparison no longer holds
stops with exit `20` ("plan again"). Agents run `vra pipeline plan`, `status` and `sync-ledger` (without
`--deploy`); every adapter asks a person before `vra pipeline deploy`, `push-source` and
`sync-ledger --deploy`.

## Related pages

-   [Environments and topologies](environments-and-topologies.md)
-   [Agent systems](agent-systems.md)
-   [Configuration reference](configuration-reference.md)
