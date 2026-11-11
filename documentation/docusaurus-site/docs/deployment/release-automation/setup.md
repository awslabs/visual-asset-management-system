---
title: "Setup and joining"
sidebar_position: 5
description: "vra setup creates the shared home and ledger; vra setup --join points another machine at an existing home. Every question, the answers file and what setup writes."
---

# Setup and joining

`vra setup` creates the shared home, asks for every value the ledger needs, writes the ledger, then
runs `vra ledger validate` and `vra preflight --all`. `vra setup --join` points another machine at a
home that already exists. Complete the [Prerequisites](prerequisites.md) and the
[Installation](installation.md) first; preflight reports anything still missing as an IT request.

## Create a home

```bash
vra setup --home ~/vams-release-home
```

Without `--home`, setup asks for the folder (default `~/vams-release-home`). It writes
`vra-home.yaml`, the folder layout and the ledger, registers this machine in `hosts`, and writes the
pointer `~/.config/vra/config.yaml` so later commands find the home without `--home`. With
`sync.type: git` it initializes the folder as a git repository, adds the remote you give as `origin`
and commits the home. Push that first commit yourself and set the upstream branch, then protect the
branch for ledger changes:

```bash
git -C ~/vams-release-home push -u origin HEAD
```

Setup's result reports the home's real state: `home.published` is `true` only when the remote holds
the home's commits. Otherwise it is `false`, with `home.reason` (no git remote, no upstream branch yet,
`sync.type: none`, a refused push) and `home.publishCommand`, the command that publishes it (for a
refused push, `vra home sync`).

To repeat a setup without prompts, save the answers and replay them:

```bash
vra --non-interactive --json setup --answers answers.yaml
```

The answers file uses the keys in the tables below; dotted keys may also be nested YAML, and
per-environment answers go under `envs: {<env>: {...}}`. With `--non-interactive` every required key
must be in the file; setup lists the missing ones and exits `2`. Keep the answers file outside the home.
Setup never overwrites existing ledger files unless you pass `--force` (human-only).
`--skip-preflight` writes and validates without the IAM preflight, and `--dry-run` prints what it
would write.

```yaml
# answers.yaml (excerpt)
home:
    path: ~/vams-release-home
    sync: { type: git, remote: "git@git.example.com:<customer>/vra-home.git" }
project: { name: <customer>-vams }
fork: { url: "https://git.example.com/<customer>/<customer>-vams.git" }
githost: { type: gitlab, repo: <customer>/<customer>-vams, apiUrl: "https://git.example.com" }
envs:
    dev: { account: "111111111111", region: us-east-1 }
    test: { account: "222222222222", region: us-east-1, approvers: [qa-lead@example.com] }
    prod: { account: "333333333333", region: us-east-1, approvers: [release-manager@example.com] }
```

## Join an existing home

```bash
vra setup --join git@git.example.com:<customer>/vra-home.git
vra setup --join /mnt/shared/vams-release-home
```

A git URL is cloned into `~/vams-release-home` (`--home <dir>` for another folder, `--branch <b>` for
a branch); a local path is used where it is. Join pulls the home, validates its ledger, registers this
machine in `hosts` and publishes that change, writes the pointer, and returns the cron line for a watch
host. Join changes nothing in any environment.

## Questions

Keys and defaults come from `engine/vra/setup.py`.

### Home

| Answer key                         | Question                      | Default                | Notes                                                      |
| ---------------------------------- | ----------------------------- | ---------------------- | ---------------------------------------------------------- |
| `home.path`                        | Shared home folder            | `~/vams-release-home`  | `--home` or `VRA_HOME` answers it                          |
| `home.sync.type`                   | How the home folder is synced | `none`                 | `none`, `git` or `command` ([Shared home](shared-home.md)) |
| `home.sync.remote`                 | Git remote URL of the home    | blank                  | `git` only; blank sets it up later                         |
| `home.sync.pull`, `home.sync.push` | Pull and push commands        | required for `command` |                                                            |

### Project

| Answer key                                           | Question                                     | Default                       | Writes                                                                                    |
| ---------------------------------------------------- | -------------------------------------------- | ----------------------------- | ----------------------------------------------------------------------------------------- |
| `project.name`                                       | Project name                                 | `vams-fork`                   | `vra.yaml` `project.name`                                                                 |
| `upstream`                                           | Upstream VAMS git URL                        | the awslabs VAMS repository   | `upstream.url`                                                                            |
| `fork`                                               | Customer fork git URL                        | required                      | `fork.url`                                                                                |
| `githost.type`                                       | Git host                                     | `github`                      | `github`, `gitlab`, `bitbucket`, `codecommit` or `local` (`local` opens no pull requests) |
| `githost.repo`                                       | Repository (owner/name)                      | derived from the fork URL     | github, gitlab, bitbucket                                                                 |
| `githost.tokenSecret`                                | Secret name holding the Bitbucket token      | required for bitbucket        |                                                                                           |
| `githost.repositoryName`, `githost.region`           | CodeCommit repository and Region             | derived from the fork URL     | codecommit                                                                                |
| `githost.apiUrl`                                     | GitLab URL                                   | blank (gitlab.com)            | gitlab                                                                                    |
| `trunk`                                              | Fork trunk branch                            | `main`                        |                                                                                           |
| `tagPrefix`                                          | Release tag prefix                           | `cust-`                       | Tags look like `cust-vX.Y.Z-1`                                                            |
| `lanes.release.source`                               | Upstream release branch                      | `main`                        |                                                                                           |
| `lanes.preview.source`, `lanes.preview.targetBranch` | Upstream preview branch, fork preview branch | `development`, `preview/main` |                                                                                           |

### State, notifications and build

| Answer key                                              | Question                                                    | Default                      | Notes                                                                                    |
| ------------------------------------------------------- | ----------------------------------------------------------- | ---------------------------- | ---------------------------------------------------------------------------------------- |
| `stateStore.type`                                       | State backend                                               | `home`                       | `home` (the shared home) or `s3` (Amazon S3 and Amazon DynamoDB)                         |
| `stateStore.bucket`, `.prefix`, `.lockTable`, `.region` | State bucket, key prefix, lock table, Region                | `-`, `vra`, `vra-locks`, `-` | `s3` only                                                                                |
| `toolsAccount`                                          | Tools or CI account id                                      | blank                        |                                                                                          |
| `notifications.type`                                    | Notification channel                                        | `none`                       | `none`, `sns`, `email`, `slack-webhook`, `teams-webhook`                                 |
| `notifications.target`, `notifications.region`          | Secret name holding the topic ARN, addresses or webhook URL | required unless `none`       |                                                                                          |
| `notifications.from`                                    | Amazon SES verified sender                                  | required for `email`         |                                                                                          |
| `build.ash`, `build.sbom`, `build.signTags`             | ASH scan, CycloneDX SBOM, git-signed tags                   | `false`, `true`, `false`     | An enabled scanner must be installed on the build host, otherwise `vra build` stops      |
| `build.signing.kmsKeyArn`, `build.signing.tags`         | AWS KMS signing key for manifests, also sign release tags   | blank, `true`                | Recommended for production ([Approvals and gates](approvals-and-gates.md#signed-builds)) |

### Environments

`envNames` (default `dev, dev-preview, test, prod`) sets the environments. Known names get presets:
`dev` (release lane, auto-deploy, rollback `auto`), `dev-preview` (preview lane, auto-deploy), `test`
(promotes from `dev`, approval, rollback `ask`, accepts data copies from `prod`) and `prod` (promotes
from `test`, approval, rollback `ask`). Any other name is an approval-gated release environment that
promotes from the previous release-lane environment in the list. Setup refuses a `promotesFrom` that
names no environment.

| Answer key                                                     | Question                                                                                                                                                      | Default                                                                       | Notes                                                                                                                                          |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `account`, `region`                                            | AWS account id, Region                                                                                                                                        | previous environment's, `us-east-1`                                           | required                                                                                                                                       |
| `partition`                                                    | Partition                                                                                                                                                     | derived from the Region                                                       | must match the Region and every role ARN                                                                                                       |
| `profile`                                                      | Named AWS profile                                                                                                                                             | blank (assume `deployRoleArn`)                                                |                                                                                                                                                |
| `deployRoleArn`                                                | Deploy role ARN                                                                                                                                               | `arn:<partition>:iam::<account>:role/vra-deploy`                              | from `vra-deploy-role.yaml`; rename per environment                                                                                            |
| `templateBase`, `templatePath`                                 | VAMS config template                                                                                                                                          | `govcloud` in `aws-us-gov`, `eusovereign` in `aws-eusc`, else `commercial`    | `templatePath` only for `custom`                                                                                                               |
| `configName`, `baseStackName`                                  | config.json `name`, `app.baseStackName`                                                                                                                       | `vams`, the environment name in lower case without punctuation                | lower case; unique per account and Region                                                                                                      |
| `lane`, `promotesFrom`                                         | Lane, promotes from                                                                                                                                           | preset                                                                        |                                                                                                                                                |
| `autoDeploy`, `requiresApproval`, `approvers`                  | Auto-deploy from cron, require approval, approver ids                                                                                                         | preset, preset, none                                                          | `autoDeploy` and `requiresApproval` exclude each other; approvers required with approval                                                       |
| `tier`                                                         | Environment tier                                                                                                                                              | `preview` on the preview lane, `prod` for gated `prod*` names, else `nonprod` | `prod` tiers accept no data copies and no scanner waivers                                                                                      |
| `rootEnv`                                                      | Gated environment with no `promotesFrom`, deployed directly on purpose                                                                                        | `no`                                                                          | asked only for gated roots                                                                                                                     |
| `synthRoleArn`, `dataOwners`, `permissionsBoundaryName`        | Build-only synth role, data owners, CDK permissions boundary                                                                                                  | blank                                                                         | asked only for gated environments                                                                                                              |
| `iam.mode`                                                     | How IT controls the VAMS roles of this environment                                                                                                            | `standard`                                                                    | `standard`, `boundary`, `precreated`, `bootstrap`, combined with `+`, or `all` ([IAM for strict environments](iam-for-strict-environments.md)) |
| `iam.boundaryArn`, `iam.rolePrefix`                            | Permission boundary policy ARN from IT, role name prefix IT requires                                                                                          | blank                                                                         | asked for `boundary` and `precreated`; a blank boundary is filled in from IT's response; the prefix is required for `precreated`               |
| `iam.cliCredentialsSynthesizer`, `iam.bootstrap.qualifier`     | Deploy without CDK bootstrap roles, CDK bootstrap qualifier                                                                                                   | `no`, `hnb659fds`                                                             | asked for `bootstrap`                                                                                                                          |
| `iam.bootstrap.have`, `iam.bootstrap.<roleArnKey>`             | Has IT already created the bootstrap roles; their five ARNs                                                                                                   | `no`                                                                          | without the ARNs, `vra iam plan` lists the bootstrap roles for IT                                                                              |
| `deployMode`, `rollbackPolicy`                                 | Default deploy mode, rollback policy                                                                                                                          | `in-place`, preset                                                            |                                                                                                                                                |
| `dataCopy.allowedSources`, `dataCopy.stagingBucket`            | Environments allowed to copy data into this one, staging bucket                                                                                               | preset, blank                                                                 | staging bucket asked only when sources are set                                                                                                 |
| `registeredBuckets.granted`                                    | Have this environment's vra roles been granted access to the asset buckets registered in VAMS that are not named after the core stack, and to their KMS keys? | `no`                                                                          | VAMS registers buckets at any time, also customer-named and other-account buckets                                                              |
| `registeredBuckets.bucketArns`, `registeredBuckets.kmsKeyArns` | Granted bucket ARNs or patterns, their KMS key ARNs                                                                                                           | blank                                                                         | asked only when granted; keys in the environment's own account and Region need no entry                                                        |
| `liveTest.webUrl`, `liveTest.apiUrl`, `liveTest.cliProfile`    | Live-test URLs, VamsCLI profile                                                                                                                               | blank, blank, `vra-<env>`                                                     | the web URL falls back to the deployed URL in Parameter Store                                                                                  |
| `liveTest.auth.type`                                           | Live-test sign-in                                                                                                                                             | `password`                                                                    | `password`, `apiKey`, `token` or `none` ([Testing](testing.md#sign-in-for-live-tests))                                                         |
| `liveTest.auth.secret`, `.userId`, `.tokenCommand`             | Secret name, user id, token command                                                                                                                           | per type                                                                      |                                                                                                                                                |
| `changeWindow`, `changeWindowMinutes`, `changeWindowTimezone`  | Change window cron, length, time zone                                                                                                                         | none, `120`, `UTC`                                                            | deploys outside need `--emergency` and an approval                                                                                             |
| `adminEmailAddress`                                            | VAMS admin e-mail                                                                                                                                             | `admin@example.com`                                                           | written to the overlay `config.json` `app.adminEmailAddress`                                                                                   |

## What setup writes

```text
<home>/
  vra-home.yaml
  .gitignore  .gitattributes
  ledger/
    vra.yaml
    envs/<env>/env.yaml
    envs/<env>/overlay/infra/config/config.json  # upstream template + account, Region, names, admin e-mail
    path-policy.yaml       # engine default
    core-patches.md        # empty register (fork paths and ledger patch files)
    tests/customer.yaml    # []
    approvals/README.md
    README.md
  state/  artifacts/  checkpoints/
```

The ledger matches
[`ledger-template/`](https://github.com/awslabs/visual-asset-management-system/tree/main/tools/VamsReleaseAutomation/ledger-template).
Each key is described in the [Configuration reference](configuration-reference.md).

## Start from an existing deployment

To manage a VAMS deployment that already exists, import its configuration into the environment's
overlay after setup:

```bash
vra ledger import --env prod --from ./current-prod/infra/config/config.json
```

The import takes unset `env.yaml` values (`configName`, `baseStackName`, Region, account) from the
file, fills an empty `env.region` or `env.account` in the file from `env.yaml`, copies a sibling
`custom/customConfig.json`, and refuses a file that names another deployment or contains a literal
secret. `vra ledger import` is human-only. Add Content Security Policy additions and SAML or OIDC
files to `envs/<env>/overlay/` by hand, and customer regression hooks to `tests/customer.yaml`.

## Validation and preflight

After writing the ledger, setup runs:

1. `vra ledger validate`: schema, gate consistency, identity (each `env.yaml` and its overlay
   `config.json` name the same deployment), naming, topology, the IAM settings, the `automation` block,
   the listed core patches and a secret scan of the whole ledger.
2. `vra preflight --all` (unless `--skip-preflight` or `--dry-run`): for each environment and workflow,
   simulates the actions the workflow needs against the environment's role. Gaps become IT requests and
   setup exits `10`. See [Permissions and IT requests](permissions-and-it-requests.md).

Setup's result has one note per environment on its registered buckets. With `granted: no`, the first
backup checkpoint, restore, data copy or deploy with data migrations writes an IT request for every
bucket outside the core stack's names and stops with exit `10` until the grant is recorded.

An environment with an IAM mode other than `standard` needs an IT role package before its first build:
run `vra iam plan --env <env>` and follow [IAM for strict environments](iam-for-strict-environments.md).
To run the release from AWS CodePipeline or GitHub Actions, add an `automation` block to `vra.yaml`
([Automation tiers](automation-tiers.md)); setup writes none.

Check the names and topology before the first deploy:

```bash
vra ledger show --env dev     # tier, gate, required suites, resolved stack names, Parameter Store prefix
vra ledger graph              # every environment with its parent, gate and tier
```

## Related pages

-   [Installation](installation.md)
-   [Shared home](shared-home.md)
-   [Environments and topologies](environments-and-topologies.md)
-   [Configuration reference](configuration-reference.md)
