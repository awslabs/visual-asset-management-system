---
title: "Configuration reference"
sidebar_position: 19
description: "Every key of vra-home.yaml, the ledger's vra.yaml and envs/<env>/env.yaml, configuration overlays, approval records, and the environment variables vra reads."
---

# Configuration reference

The shared home holds two kinds of configuration: `vra-home.yaml` (how the home is shared) and the
ledger (what is deployed where, and who approves it). The ledger schemas are in
[`engine/vra/data/schemas/`](https://github.com/awslabs/visual-asset-management-system/tree/main/tools/VamsReleaseAutomation/engine/vra/data/schemas);
unknown keys are rejected. A starting ledger is in
[`ledger-template/`](https://github.com/awslabs/visual-asset-management-system/tree/main/tools/VamsReleaseAutomation/ledger-template).

:::warning[No secret values]
The ledger stores only the names of AWS Secrets Manager secrets and AWS Systems Manager Parameter Store
SecureString parameters. `vra ledger validate` scans the whole ledger and rejects values that look like
keys, tokens or passwords.
:::

## Ledger layout

```text
ledger/
  vra.yaml                               # project settings
  envs/<env>/env.yaml                    # one environment
  envs/<env>/overlay/<repo-relative path> # files copied over the checkout before synth
  approvals/<env>/<id>.yaml              # approval records
  path-policy.yaml                       # merge resolution rules
  core-patches.md                        # register of fork edits outside the fork lanes
  core-patches/<name>.patch              # ledger core patches the register lists (applied to every build)
  tests/customer.yaml                    # customer regression hooks
```

| File                  | Described in                                                                                                                |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `path-policy.yaml`    | [Branch strategy: merge path policy](branch-strategy.md#merge-path-policy)                                                  |
| `core-patches.md`     | [Branch strategy: core-patch register](branch-strategy.md#core-patch-register), [Ledger core patches](#ledger-core-patches) |
| `tests/customer.yaml` | [Testing: customer regression hooks](testing.md#customer-regression-hooks)                                                  |
| `approvals/`          | [Approvals and gates](approvals-and-gates.md#approval-records)                                                              |

## vra-home.yaml

```yaml
schemaVersion: 1
homeId: home-0123456789ab
sync:
    type: git # none | git | command
    pushArtifacts: false
locks:
    leaseSeconds: 900
hosts:
    - { id: ops-ws-1a2b3c4d, hostname: ops-ws, registeredAt: "2026-01-10T09:00:00Z" }
```

| Key                      | Default      | Meaning                                                                           |
| ------------------------ | ------------ | --------------------------------------------------------------------------------- |
| `schemaVersion`          | `1`          | A newer version than the engine supports stops every command                      |
| `homeId`                 | set by setup | Names the machine-local work directory `~/.cache/vra/<homeId>`                    |
| `sync.type`              | `none`       | `none`, `git` or `command` ([Shared home](shared-home.md#synchronization))        |
| `sync.pull`, `sync.push` |              | Commands for `command` (string or argv list), run in the home with `VRA_HOME` set |
| `sync.branch`            |              | Branch of the home repository                                                     |
| `sync.pushArtifacts`     | `false`      | Also publish `artifacts/` (build bundles)                                         |
| `locks.leaseSeconds`     | `900`        | Lock lease; the heartbeat renews it every third                                   |
| `hosts[]`                |              | Machines registered by setup and join: `id`, `hostname`, `registeredAt`, `role`   |

## vra.yaml

```yaml
schemaVersion: 1
project: { name: <customer>-vams }
upstream: { url: "https://github.com/awslabs/visual-asset-management-system.git" }
fork: { url: "https://git.example.com/<customer>/<customer>-vams.git", localPath: null }
githost:
    {
        type: gitlab,
        repo: <customer>/<customer>-vams,
        apiUrl: "https://git.example.com",
        pushVendorBranches: true,
    }
trunk: main
tagPrefix: cust-
lanes:
    release: { source: main, vendorBranch: vendor/vams-main, autoMerge: false }
    preview:
        {
            source: development,
            vendorBranch: vendor/vams-development,
            targetBranch: preview/main,
            autoMerge: true,
        }
notifications:
    {
        type: teams-webhook,
        target: vra/notify-webhook,
        onlyOn: [BLOCKED_ON_IT, NEEDS_HUMAN, TESTS_FAILED, ROLLED_BACK, ROLLBACK_FAILED, FAILED],
    }
stateStore: { type: home }
toolsAccount: "111111111111"
build: { ash: false, sbom: true, signTags: false, pushTags: true, unitSuites: null, cdkCli: null }
cdk: { qualifier: hnb659fds }
engineVersion: null
```

| Key                                                  | Default                                        | Meaning                                                                                                                  |
| ---------------------------------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `project`                                            | required                                       | `{name, description}` (a plain string is the name)                                                                       |
| `upstream.url`                                       | the awslabs VAMS repository                    | Upstream remote                                                                                                          |
| `fork.url`, `fork.localPath`                         | required, `null`                               | Fork remote; an existing clone to use instead of the engine-managed one in the work directory (keep it outside the home) |
| `githost.type`                                       | `local`                                        | `github`, `gitlab`, `bitbucket`, `codecommit` or `local` (no pull requests)                                              |
| `githost.repo`                                       |                                                | Required for github, gitlab and bitbucket                                                                                |
| `githost.repositoryName`, `githost.region`           |                                                | Required for codecommit                                                                                                  |
| `githost.apiUrl`, `githost.tokenSecret`              | `null`                                         | Self-managed host API; token secret name (bitbucket). GitHub and GitLab use the `gh` and `glab` CLI login                |
| `githost.pushVendorBranches`                         | `true`                                         | Push the vendor mirrors to the fork                                                                                      |
| `trunk`                                              | `main`                                         | Fork trunk branch                                                                                                        |
| `tagPrefix`                                          | `""`                                           | Release tags `<tagPrefix>v<vams>-<n>`                                                                                    |
| `lanes.<lane>.source`                                | `main` / `development`                         | Upstream branch                                                                                                          |
| `lanes.<lane>.vendorBranch`                          | `vendor/vams-main` / `vendor/vams-development` | Fast-forward mirror                                                                                                      |
| `lanes.<lane>.targetBranch`                          | trunk / `preview/main`                         | Branch the sync branch is cut from and merged into                                                                       |
| `lanes.<lane>.autoMerge`                             | `false` / `true`                               | Preview only: fast-forward the target instead of a pull request; `true` on the release lane is an error                  |
| `lanes.<lane>.enabled`                               | `true`                                         | `false` turns the lane off; `lanes.preview` may be omitted                                                               |
| `notifications.{type, target, from, region, onlyOn}` | `type: none`                                   | Channel (`sns`, `email`, `slack-webhook`, `teams-webhook`); `target` is a secret name; `onlyOn` filters run statuses     |
| `stateStore.type`                                    | `home`                                         | `home` (the shared home; `local` is accepted) or `s3`                                                                    |
| `stateStore.{bucket, prefix, lockTable, region}`     | `s3` only                                      | Amazon S3 bucket, key prefix, Amazon DynamoDB lock table, Region                                                         |
| `toolsAccount`                                       | `null`                                         | Account of the orchestrator identity                                                                                     |
| `build.ash`, `build.sbom`                            | `false`, `true`                                | Scanners; an enabled scanner whose tool is missing stops the build                                                       |
| `build.signTags`, `build.pushTags`                   | `false`, `true`                                | `git tag -s` with the build host's own key (not verified by `vra`; prefer `signing.tags`); push tags                     |
| `build.signing.{kmsKeyArn, algorithm, region, tags}` | unset                                          | AWS KMS signing of manifests and, with `tags`, release tags ([Signed builds](approvals-and-gates.md#signed-builds))      |
| `build.unitSuites`                                   | the default build suites                       | Unit suites `vra build` and `vra watch` run; `[]` runs none                                                              |
| `build.cdkCli`                                       | `null`                                         | AWS CDK CLI version; `null` is the exact version in the fork's `infra/package-lock.json`, packed into the bundle         |
| `cdk.qualifier`                                      | `hnb659fds`                                    | CDK bootstrap qualifier                                                                                                  |
| `engineVersion`                                      | `null`                                         | The `vra` version this ledger is operated with; another version refuses gated actions                                    |
| `automation`                                         | `mode: agent`                                  | Automation tier and the AWS CodePipeline and GitHub Actions settings ([automation](#automation))                         |

### stateStore

With `type: s3`, runs, history, approval claims and bundles are kept in the bucket with conditional
writes and the locks in the table; the home is then pulled for the ledger and never published. Create
both with
[`vra-state-store.yaml`](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsReleaseAutomation/iam/cloudformation/vra-state-store.yaml),
whose bucket policy denies deleting history.

### automation

Records who drives the release ([Automation tiers](automation-tiers.md)). `vra ledger validate` checks it.

| Key                      | Default                                     | Meaning                                                                                                                                                           |
| ------------------------ | ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `automation.mode`        | `agent`                                     | `agent`, `hybrid`, `pipeline` or `pipeline+actions`                                                                                                               |
| `automation.pipeline`    | required for `pipeline`, `pipeline+actions` | The `cicd/pipeline` configuration without `ledgerGraph`, `ledgerDir` and `automationMode`, which `vra pipeline plan` sets. Ignored with a warning in `agent` mode |
| `automation.actions`     | optional                                    | Notes on the GitHub Actions side; vra reads nothing from it (the workflows use repository variables)                                                              |
| `automation.approvalEnv` | the first prod-class gated environment      | Environment whose approvers authorize a human-only `vra pipeline deploy` without a terminal (record subject `human:pipeline-deploy`)                              |

Main keys of `automation.pipeline` (the full set is `PipelineConfig` in
[`cicd/pipeline/lib/config.ts`](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsReleaseAutomation/cicd/pipeline/lib/config.ts)):

| Key                                                                          | Meaning                                                                                                                                                                                                                                 |
| ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `prefix`, `toolsAccount`, `region`                                           | Name prefix (default `vra`), tools account (else `vra.yaml` `toolsAccount`), Region; required                                                                                                                                           |
| `sources.fork`, `sources.vra`                                                | Required. `type` `codeconnections` (`provider`, `connectionArn` or created, `hostArn`, `repo`, `branch`, `triggerOnPush`), `codecommit` or `s3` (`bucketName` or created, `objectKey`)                                                  |
| `home.repoUrl`, `home.ref`, `home.tokenSecretName`                           | The home repository the jobs clone; `repoUrl` required                                                                                                                                                                                  |
| `notifications.topicArn`                                                     | Required; Amazon SNS topic for pipeline and watch failures                                                                                                                                                                              |
| `scope`                                                                      | `all` (default) or `dev` (stages only for environments reachable without an approval); `hybrid` requires `dev`                                                                                                                          |
| `release.testSuites`, `release.isolateTests`, `release.autoRollback`         | Suites of the Test actions; test projects with their own role (unset: every approval-gated environment; `false` is refused when a prod-class environment runs tests); roll back an ungated environment on failed tests (default `true`) |
| `watch.enabled`, `watch.schedule`, `watch.timeZone`, `watch.command`         | The scheduled job; `command` `sync-merge` (default in these tiers) or `sync`; `watch` is refused                                                                                                                                        |
| `iamRoles.mode`, `iamRoles.precreatedRoles`, `iamRoles.roleNamePrefix`       | `create` (default) or `precreated` (construct path to role name from IT); name prefix                                                                                                                                                   |
| `actionsHandoff.oidcProviderArn`, `.subjects`, `.audience`, `.repositoryIds` | Creates the GitHub hand-off role for `pipeline+actions`; the condition keys come from the provider's issuer (GHES, enterprise issuer), `repositoryIds` (`repository`, `ownerId`, `repositoryId`) adds the immutable subject             |
| `stateStore.keyArn`                                                          | Required with `vra.yaml` `stateStore.type: s3` (the bucket, prefix and table come from `vra.yaml`)                                                                                                                                      |
| `codebuild`, `permissionsBoundaryArn`, `signingKeyArn`                       | Image, environment, VPC; a boundary for every role the stack creates; the build signing key                                                                                                                                             |

## env.yaml

One file per environment, `ledger/envs/<env>/env.yaml`. Excerpt:

```yaml
account: "222222222222"
region: us-east-1
partition: aws
deployRoleArn: arn:<partition>:iam::222222222222:role/vra-deploy-test
templateBase: commercial
configName: vams
baseStackName: test
lane: release
promotesFrom: dev
autoDeploy: false
requiresApproval: true
tier: nonprod
approvers:
    - qa-lead@example.com
    - arn:<partition>:sts::111111111111:assumed-role/ReleaseApprover*/release-manager@example.com
synthRoleArn: arn:<partition>:iam::222222222222:role/vra-synth-test
rollbackPolicy: ask
dataCopy: { allowedSources: [prod], stagingBucket: vra-staging-333333333333-us-east-1 }
liveTest: { cliProfile: vra-test, auth: { type: password, secret: vra/test/live-test-user } }
registeredBuckets: { granted: false, bucketArns: [], kmsKeyArns: [] }
changeWindow: null
```

### Identity and access

| Key                                                   | Default                         | Meaning                                                                                                                                                                                                                                                                                                        |
| ----------------------------------------------------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`, `description`                                 |                                 | Labels                                                                                                                                                                                                                                                                                                         |
| `account`, `region`, `partition`                      | required, required, from Region | `partition` must match the Region and every role ARN                                                                                                                                                                                                                                                           |
| `deployRoleArn`, `dataCopyRoleArn`, `readOnlyRoleArn` | `null`                          | Roles `vra` assumes                                                                                                                                                                                                                                                                                            |
| `synthRoleArn`                                        | `null`                          | Build-only identity for `cdk synth` lookups; a gated environment never synthesizes with its deploy role                                                                                                                                                                                                        |
| `testRoleArn`                                         | `null`                          | Identity of `vra test` for its reads (live-test secret, stack outputs)                                                                                                                                                                                                                                         |
| `profile`                                             | `null`                          | Named AWS profile instead of AssumeRole                                                                                                                                                                                                                                                                        |
| `sessionDurationSeconds`                              | `3600`                          | AssumeRole duration (900 to 43200; chained sessions are capped at 3600)                                                                                                                                                                                                                                        |
| `refreshCredentials`                                  | `true`                          | Child processes get a `credential_process` profile that re-assumes the role on expiry; `false` exports static keys                                                                                                                                                                                             |
| `setSourceIdentity`                                   | `requiresApproval`              | Set `sts:SourceIdentity` to the initiator on role sessions                                                                                                                                                                                                                                                     |
| `permissionsBoundaryName`                             | `null`                          | IAM permissions boundary (CDK context `@aws-cdk/core:permissionsBoundary`) for every role VAMS creates; the build checks every synthesized role carries it; VAMS v2.6.6 and earlier need core patch CP-431 ([GitHub issue #431](iam-for-strict-environments.md#permission-boundary-mode-and-github-issue-431)) |
| `fips`                                                | `auto`                          | FIPS endpoints for the engine and for deploy, test and hook child processes (never synth); `auto` follows the overlay's `app.useFips` in `aws` and `aws-us-gov`                                                                                                                                                |

### iam

How IT controls the VAMS roles of the environment ([IAM for strict environments](iam-for-strict-environments.md)).

| Key                                                                | Default    | Meaning                                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------ | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `iam.mode`                                                         | `standard` | `standard`, `boundary`, `precreated`, `bootstrap`; combined with `+` or as a list; `all` is the three. Precreated with A/B slots is an error                                                                                                             |
| `iam.boundaryArn`                                                  | `null`     | Permission boundary policy ARN from IT, in the environment's partition and account; when blank, the build checks the ARN IT returned (`vra iam apply`), and stops when none is known                                                                     |
| `iam.rolePrefix`                                                   | `null`     | Role name prefix IT requires, at most 32 characters; required with `iam.mode: precreated` (IT's roles start with it, and the scoped CDK execution policy reaches them only by it)                                                                        |
| `iam.cliCredentialsSynthesizer`                                    | `false`    | `bootstrap` without bootstrap roles: deploy with the deploy role's own credentials                                                                                                                                                                       |
| `iam.quotaHeadroom`                                                | `10`       | IAM roles and managed policies kept free by the quota check before `cdk deploy`: the deploy stops (exit 20) when the account's roles plus those the change adds or replaces plus this headroom exceed the quota (applies in every mode, also `standard`) |
| `iam.bootstrap.qualifier`                                          | `null`     | CDK bootstrap qualifier, 1 to 10 characters                                                                                                                                                                                                              |
| `iam.bootstrap.<role>Arn`                                          | `null`     | `deployRoleArn`, `cloudFormationExecutionRoleArn`, `lookupRoleArn`, `fileAssetPublishingRoleArn`, `imageAssetPublishingRoleArn` if IT already created them                                                                                               |
| `iam.bootstrap.fileAssetsBucketName`, `.imageAssetsRepositoryName` | `null`     | Staging bucket and Amazon ECR repository of custom bootstrap resources                                                                                                                                                                                   |

`vra iam apply` writes the overlay files the mode needs (`infra/config/config.json`,
`infra/config/policy/iamRoleConfig.json`, `infra/cdk.json.vra-merge.json`); edit them only through a new
package and apply.

### Topology and gates

| Key                              | Default                                                                   | Meaning                                                                                              |
| -------------------------------- | ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `lane`                           | required                                                                  | `release` or `preview`                                                                               |
| `promotesFrom`                   | `null`                                                                    | The environment a build must have passed first                                                       |
| `autoDeploy`, `requiresApproval` | required                                                                  | Exclude each other                                                                                   |
| `approvers`                      | `[]`                                                                      | Required with `requiresApproval`; plain ids, IAM ARNs or ARN globs                                   |
| `tier`                           | derived                                                                   | `prod`, `nonprod` or `preview` ([Environments and topologies](environments-and-topologies.md#tiers)) |
| `rootEnv`                        | `false`                                                                   | A gated environment with no `promotesFrom`, deployed directly on purpose                             |
| `approvalMaxAgeHours`            | `72`                                                                      | A record without `expiresAt` expires this long after `approvedAt`                                    |
| `approvalSigningKeyArn`          | `null`                                                                    | Approval records must carry a valid AWS KMS signature by this key                                    |
| `dataOwners`                     | `approvers`                                                               | Who approves data leaving this environment as a data-copy source                                     |
| `requiredSuites`                 | `[live-cli]` on gated environments and those they promote from, else `[]` | Suites that must execute with at least one passing test                                              |
| `changeWindow`                   | `null`                                                                    | `{cron, durationMinutes, timezone}`; POSIX cron semantics                                            |
| `allowGenericCi`                 | `false`                                                                   | Accept a run from generic CI (`VRA_CI_PROVIDER=generic`) on this gated environment                   |
| `allowCrossPartitionPromotion`   | `false`                                                                   | Allow `promotesFrom` an environment in another partition                                             |

### Configuration and synth

| Key                            | Default                        | Meaning                                                                                                              |
| ------------------------------ | ------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| `templateBase`, `templatePath` | required                       | `commercial`, `govcloud`, `eusovereign` or `custom`; `templatePath` for `custom`                                     |
| `configName`, `baseStackName`  | from the overlay `config.json` | Must equal the overlay's `name` and `app.baseStackName`; lower case                                                  |
| `synthEnv`                     | `{}`                           | VAMS configuration environment variables passed to synth; any other VAMS variable in the environment stops the build |
| `tags`                         | `{}`                           | Tags for the environment                                                                                             |

### Deploy

| Key                                                                                  | Default                    | Meaning                                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------ | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `deployMode`, `rollbackPolicy`                                                       | `in-place`, `auto`         | Default mode; `ask` stops instead of rolling back                                                                                                                                                                                                                                                                                                           |
| `diffGate`                                                                           | `text`                     | `text` (fail-closed `cdk diff` parse) or `changeset` (AWS CloudFormation change sets with nested stacks)                                                                                                                                                                                                                                                    |
| `cloudfront.quotaHeadroom`                                                           | `2`                        | CloudFront policies and distributions kept free by the quota check before `cdk deploy`: the deploy stops (exit 20) when the account's custom cache, origin request or response headers policies or distributions in use, plus those the change adds or replaces, plus this headroom exceed the quota ([Workflows: quota checks](workflows.md#quota-checks)) |
| `driftCheck`                                                                         | on for gated environments  | Drift detection before deploy                                                                                                                                                                                                                                                                                                                               |
| `backupBeforeDeploy`, `cognitoExport`                                                | `true`, `false`            | Pre-deploy checkpoint; include an Amazon Cognito user export                                                                                                                                                                                                                                                                                                |
| `backup.allowDegraded`                                                               | `false`                    | Accept a checkpoint with warnings on a gated environment                                                                                                                                                                                                                                                                                                    |
| `reindex.mode`                                                                       | `lambda`                   | How `vra` rebuilds the search indexes after a deploy, restore or data copy and in `vra reindex`: `lambda` invokes the deployed reindexer and waits (15-minute limit), `direct` runs the build's `reindex_utility.py --mode direct` on the `vra` host ([Search reindex](backups-restore-and-migrations.md#search-reindex))                                   |
| `migrations.configOverrides`, `migrations.skip`                                      | `{}`, `[]`                 | Per-migration configuration values; migration ids to skip                                                                                                                                                                                                                                                                                                   |
| `ab.slots`, `ab.slotOverlays`, `ab.dnsSwitchHook`, `ab.slotLiveTest`                 |                            | A/B deployment ([Backups, restore and migrations](backups-restore-and-migrations.md#ab))                                                                                                                                                                                                                                                                    |
| `writeFreeze.{backend, maintenanceModeParameter, propagationSeconds, settleSeconds}` | `auto`, `null`, `30`, `60` | How restore and data copy freeze writes ([Write freeze](backups-restore-and-migrations.md#write-freeze))                                                                                                                                                                                                                                                    |

### Data copy and buckets

| Key                                                                    | Default             | Meaning                                                                                                        |
| ---------------------------------------------------------------------- | ------------------- | -------------------------------------------------------------------------------------------------------------- |
| `dataCopy.allowedSources`                                              | `[]`                | Environments allowed to copy into this one                                                                     |
| `dataCopy.allowAsTarget`                                               | derived             | `false` refuses copies into this environment; unset refuses them for prod-class environments                   |
| `dataCopy.maskHook`, `dataCopy.subsetHook`                             | `null`              | Per-table hooks run on the items before they are loaded                                                        |
| `dataCopy.stagingBucket`, `dataCopy.kmsKeyArn`                         | `null`              | Staging bucket and key for exports and staged objects                                                          |
| `dataCopy.batchOperationsThreshold`, `dataCopy.batchOperationsRoleArn` | `10000`, `null`     | Object count above which objects copy with Amazon S3 Batch Operations, and its role                            |
| `dataCopy.copyUserRoles`, `dataCopy.chainSourceRole`                   | `false`, `false`    | Copy user-role assignments; assume the source role from this environment's loader role                         |
| `dataCopy.bucketMap`                                                   | `null`              | Source asset bucket name to target asset bucket name ([Data copy: bucket map](data-copy.md#bucket-map))        |
| `registeredBuckets.{granted, bucketArns, kmsKeyArns}`                  | `false`, `[]`, `[]` | What the roles have been granted among registered asset buckets not named after the core stack, and their keys |

### Live tests

| Key                                                             | Default                 | Meaning                                                                                    |
| --------------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------ |
| `liveTest.apiUrl`, `liveTest.webUrl`                            | from the deployed stack | The web URL falls back to `/<configName>-<baseStackName>-<region>/web/deployedUrl`         |
| `liveTest.cliProfile`                                           | `vra-<env>`             | VamsCLI profile                                                                            |
| `liveTest.auth.{type, secret, userId, tokenCommand, expiresIn}` | `password`              | Sign-in ([Testing: sign-in for live tests](testing.md#sign-in-for-live-tests))             |
| `liveTest.credentialsSecret`                                    | `null`                  | The password secret (same as `auth.type: password` with `secret`)                          |
| `liveTest.runner`                                               | `any`                   | `in-vpc` when the VAMS API endpoint is private and the live suites must run inside the VPC |

## Overlays

Before synth, `vra build` copies every file under `envs/<env>/overlay/` over a scratch checkout of the
fork at the same relative path. Overlays are never committed to the fork.

| Overlay path                                | Contents                                                                                                                        |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `infra/config/config.json`                  | The environment's full VAMS deployment configuration                                                                            |
| `infra/config/custom/customConfig.json`     | Fork settings read by the custom CDK nested stack                                                                               |
| `infra/config/csp/cspAdditionalConfig.json` | Additional Content Security Policy sources                                                                                      |
| SAML or OIDC configuration files            | Identity provider metadata and settings (secret values stay in AWS Secrets Manager)                                             |
| `infra/config/policy/iamRoleConfig.json`    | VAMS IAM role customization, written by `vra iam apply`                                                                         |
| `<path>.vra-merge.json`                     | Deep-merged into the fork's JSON file `<path>` instead of replacing it (`vra iam apply` writes `infra/cdk.json.vra-merge.json`) |

The overlay `config.json` and `env.yaml` must name the same deployment: `name` equals `configName`,
`app.baseStackName` equals `baseStackName`, and `env.region` and `env.account` equal `region` and
`account`. `vra ledger validate` reports a mismatch as an error, and every command that derives a stack
name refuses. Synth passes `-c configFile=config.json`, the stack name, Region and account explicitly, so a
fork's `cdk.json` setting cannot select another file. VAMS appends the Region to `baseStackName`, so the
core stack is `<name>-core-<baseStackName>-<region>` and resource names are published under the Parameter
Store prefix `/<name>-<baseStackName>-<region>/resourceNames`. See
[Configuration reference](../configuration-reference.md) for the VAMS options themselves.

## Ledger core patches

A unified diff in `core-patches/<name>.patch` that `core-patches.md` lists on a bullet line
(`` -   `core-patches/<name>.patch` (CP-n, reason) ``) is applied by `vra build` and `vra iam plan` to their
scratch copy of the fork, never to the fork itself. Lines inside fenced code blocks are not read.

| Result    | Meaning                                                                                                                                                                                                    |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `applied` | The patch applied; the build manifest records it under `corePatches` (file, SHA-256, status)                                                                                                               |
| `present` | Its reverse applies: the fork already contains the change (committed, or shipped upstream), or every `Present-when:` marker of the patch matches (upstream shipped the fix in another form; `via: marker`) |
| exit `20` | None of these: refresh the patch for this VAMS version, or remove its entry                                                                                                                                |

`vra ledger validate` checks that every listed patch exists inside `core-patches/` and changes only
relative paths. A complete bundle is reused only while the listed patches are unchanged. A patch file may
carry `Present-when: <repository path> contains "<text>"` lines before its diff. The package ships
CP-431, `ledger-template/core-patches/iam-role-transform-boundary.patch`
([GitHub issue #431](iam-for-strict-environments.md#permission-boundary-mode-and-github-issue-431);
needed on VAMS v2.6.6 and earlier, reported `present` from v2.6.7), and CP-1210,
`ledger-template/core-patches/api-deployment-role-name.patch`
([GitHub issue #1210](iam-for-strict-environments.md#the-api-deployment-after-a-role-prefix-switch-github-issue-1210)).

## Configuration upgrades

New VAMS releases add, rename or remove keys in `infra/config/config.template.*.json`. Before each
upgrade, run per environment:

```bash
vra ledger upgrade-config --env test --to-ref vX.Y.Z           # report only
vra ledger upgrade-config --env test --to-ref vX.Y.Z --write   # write the proposal (human-only)
```

The report lists `newKeys` with `newKeyDefaults`, `removedKeys` (in the overlay, no longer in the
template) and `proposed` (the template merged with your values, removed keys dropped). It also compares
`infra/config/custom/customConfig.json` with its upstream copy (`result.customConfig.newKeys`); that file
is customer-owned, so only new upstream keys are proposed and your keys are never removed. Nothing is
written without `--write`, and nothing is deployed until the overlay change is committed to the ledger.

## Inspecting

```bash
vra ledger show --env prod     # effective settings, tier, gate, required suites, stack names, Parameter Store prefix
vra ledger diff --env test     # overlay now vs the overlay in the last deployed bundle
vra ledger graph               # promotion topology
vra ledger validate            # schema, gates, identity, topology, naming, secret scan
```

## Environment variables

| Variable                                                                                  | Meaning                                                                             |
| ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `VRA_HOME`                                                                                | Shared home folder (same as `--home`)                                               |
| `VRA_LEDGER`, `VRA_WORK_DIR`                                                              | Ledger directory without a home; machine-local work directory                       |
| `VRA_AGENT`, `VRA_MCP`                                                                    | An agent or the MCP server is driving the engine; human-only actions are refused    |
| `VRA_MCP_ENABLE_WRITES`, `VRA_MCP_ENABLE_GATED`, `VRA_MCP_TIMEOUT_SECONDS`                | MCP server tiers and tool timeout                                                   |
| `VRA_INITIATOR`                                                                           | Alias of the initiator; ignored when it names someone other than the AWS STS caller |
| `VRA_CI_PROVIDER`, `VRA_RUN_REF`, `VRA_APPROVAL_REF`, `VRA_CI_VERIFIED_BY`, `VRA_CI_NAME` | Generic CI identity ([CI/CD and AWS CodePipeline](codepipeline.md#generic-ci))      |
| `VRA_PIPELINE_NAME`, `VRA_PIPELINE_EXECUTION_ID`                                          | AWS CodePipeline execution of an AWS CodeBuild job                                  |
| `VRA_PIPELINE_RESUME`                                                                     | `false` stops a retried pipeline action from resuming its parked run                |
| `VRA_PIPELINE_APP_DIR`                                                                    | The `cicd/` folder `vra pipeline` synthesizes (default: the package's)              |
| `VRA_GUARD_MCP_ALLOW`                                                                     | Other MCP servers the Cursor guard allows in the workspace (comma-separated names)  |
| `VRA_GIT_NAME`, `VRA_GIT_EMAIL`                                                           | Commit identity of the engine's merge commits (default `vra-bot`)                   |
| `VRA_SUITE_PYTHON`, `VRA_NPM_VERSION`                                                     | Interpreter of Python suites; npm version of the lockfile checks                    |
| `VRA_CDK_CLI`                                                                             | AWS CDK CLI override for ungated environments                                       |
| `VRA_MIGRATION_PYTHON`                                                                    | Interpreter of the VAMS migration scripts                                           |
| `VRA_PACKAGE`                                                                             | Package root, used by the adapters and the cron script                              |

## Related pages

-   [Setup and joining](setup.md)
-   [Shared home](shared-home.md)
-   [Command reference](command-reference.md)
