---
title: "GitHub Actions"
sidebar_position: 18
description: "Sample GitHub Actions workflows for GitHub Enterprise Cloud and GitHub Enterprise Server: pull request checks, security scans, upstream sync, environment-protected deploys, OIDC roles and the hand-off to AWS CodePipeline."
---

# GitHub Actions

[`cicd/github-actions/`](https://github.com/awslabs/visual-asset-management-system/tree/main/tools/VamsReleaseAutomation/cicd/github-actions)
holds sample workflows for GitHub Enterprise Cloud (github.com and `*.ghe.com`) and GitHub Enterprise
Server. Each workflow wraps the `vra` CLI; the engine enforces every gate, so a workflow cannot deploy
what an operator could not.

:::note[Not tested against a live organization]
The workflows are linted with actionlint and shellcheck and covered by offline tests
(`cicd/tests/test_actions*.py`), but they have not been run in a GitHub organization. Run them first in a
sandbox organization or repository with sandbox AWS accounts.
:::

## Two repositories

-   The customer's **VAMS fork** (the code): checks and scans of every branch and pull request, and in the
    `pipeline+actions` tier the hand-off to CodePipeline.
-   The private **home repository** (`vra-home.yaml`, `ledger/`, `state/`): upstream sync, the build,
    environment-protected deploys and the human-only operator actions.

## The workflows

| File                           | Repository | Trigger                                                      | What it does                                                                                                                       |
| ------------------------------ | ---------- | ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `pr-checks.yml`                | fork       | Pull requests and pushes to fork branches; `workflow_call`   | The VAMS unit suites and lint (the upstream `ci.yml` jobs with pinned actions), synth with cfn_nag, a `vra` plan, a customer hook  |
| `security-scan.yml`            | fork       | Pull requests, pushes, schedule; `workflow_call`             | Automated Security Helper (ASH), CodeQL, dependency review, an SBOM and gitleaks; SARIF to code scanning                           |
| `hand-off-to-codepipeline.yml` | fork       | Both check workflows passed on the trunk                     | `pipeline+actions` only: starts the release pipeline for that commit                                                               |
| `deploy.yml`                   | home       | `repository_dispatch` from the fork, manual, `workflow_call` | Builds once, then one environment-protected deploy job per environment, rendered from `vra --json ledger graph`                    |
| `vra-deploy-env.yml`           | home       | Called by `deploy.yml` and `upstream-preview.yml`            | Deploys or promotes one environment with the approval of its reviewers, then runs the live tests                                   |
| `upstream-release.yml`         | home       | Daily and manual                                             | `vra sync`; merges a new VAMS release into `sync/release-*` and opens the fork pull request; never deploys                         |
| `upstream-preview.yml`         | home       | Daily and manual                                             | Merges the upstream development commit (preview lane), builds, deploys and tests the preview environment, rolls it back on failure |
| `vra-watch.yml`                | home       | Daily                                                        | `vra watch` (environments with `autoDeploy` only), the `hybrid` tier without the two upstream workflows                            |
| `vra-operator.yml`             | home       | Manual                                                       | Human-only actions (unlock, takeover, confirm) approved in the environment's GitHub environment                                    |
| `vra-home-publish.yml`         | home       | Called after every job that writes state                     | Pushes that job's `state/` to the home repository with `VRA_HOME_TOKEN`; the only job that holds the home's write token            |

`setup-vra/` is a composite action (toolchain, engine, `GH_HOST`, git credentials) used from a pinned
checkout of the release automation package. The fork keeps the upstream `ci.yml` (VAMS core; deleting it
would conflict on every sync); disable it in the fork's Actions settings so the suites do not run twice.
`pr-checks.yml` runs a `vra` plan (`vra --dry-run build`), which shows the build steps but does not
synthesize; its `synth` job mirrors the upstream synth with cfn_nag.

## Workflows per automation tier

| Tier               | Fork                                                                 | Home repository                                                                                |
| ------------------ | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `agent`            | none (optional checks)                                               | none                                                                                           |
| `hybrid`           | `pr-checks.yml`, `security-scan.yml`                                 | `upstream-release.yml`, `upstream-preview.yml` (or `vra-watch.yml`), `deploy.yml` for dev only |
| `pipeline`         | none (CodePipeline end to end)                                       | none                                                                                           |
| `pipeline+actions` | `pr-checks.yml`, `security-scan.yml`, `hand-off-to-codepipeline.yml` | `upstream-release.yml`                                                                         |

See [Automation tiers](automation-tiers.md).

## Deploy jobs from the ledger

The `deploy-*` jobs of `deploy.yml` are generated from the engine's graph. After adding an environment or
changing a `promotesFrom`, regenerate them and paste them between the "rendered" markers:

```bash
bash vra-src/cicd/codebuild/scripts/vra-py.sh ci_graph.py render --platform github --ledger ledger
```

`--lane preview --build-job merge-build` prints the preview lane's jobs for `upstream-preview.yml`. Each job
passes `requires-approval` from the ledger; `vra-deploy-env.yml` reads the ledger again and stops with exit
`20` when the two disagree, and passes `--approval` only for a gated environment. The build job runs
`ci_graph.py check` on `deploy.yml` and fails on any difference. Deploys wait up to
`vars.VRA_LOCK_WAIT_MINUTES` (default 30) for a busy environment lock.

## GitHub environments and required reviewers

| Environment   | Repository | Reviewers                              | AWS role (`VRA_CI_ROLE_ARN`)                                                              |
| ------------- | ---------- | -------------------------------------- | ----------------------------------------------------------------------------------------- |
| `vra-build`   | home       | none                                   | `CiEnvName=vra-build`, `DeployableEnvs=none`, `AllowSynthRoles=true` (only `vra-synth-*`) |
| `vra-sync`    | home       | none                                   | Only with `stateStore.type: s3`                                                           |
| `vra-watch`   | home       | none                                   | `DeployableEnvs=<autoDeploy environments>`                                                |
| `<env>`       | home       | Gated environments: required reviewers | `CiEnvName=<env>`, in the environment's account                                           |
| `<env>-tests` | home       | none                                   | Only with `isolate-tests`: `DeployableEnvs=none`, `TestEnvs=<env>`                        |
| `vra-handoff` | fork       | none (CodePipeline approves)           | Starts executions of the release pipeline only                                            |

For a gated environment, set required reviewers, enable "Prevent self-review" and limit deployment branches
to the default branch. List the reviewer logins in the environment's `env.yaml` `approvers` too. The deploy
job reads the approvals of the current run from the GitHub API and writes the engine's approval record:
the approver is the reviewer (never the triggering actor), the record is bound to the run, valid for 24
hours and used once. Reviewers put `ticket: CHG-1234; notes: ...` in the approval comment.
`vra-operator.yml` runs in the environment's GitHub environment and turns its approval into a
`human:<action>` record. See [Approvals and gates](approvals-and-gates.md).

## AWS access with OIDC

No workflow uses long-lived AWS keys. Deploy
[`vra-ci-oidc-role.yaml`](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsReleaseAutomation/cicd/oidc/vra-ci-oidc-role.yaml)
in the tools account once per GitHub environment (`Provider=github`, `Repository=<org>/<repo>`,
`CiEnvName=<environment>`). Each role trusts only `repo:<org>/<repo>:environment:<environment>`; never trust a
branch subject, which every job on the branch would match. Set the role ARN as the environment variable
`VRA_CI_ROLE_ARN` of the matching GitHub environment, and add it to `TrustedPrincipalArns` of that
environment's `vra-deploy-<env>` role only.

| Setting               | GitHub Enterprise Cloud                                                                                                                   | GitHub Enterprise Server              |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| Issuer (IAM provider) | `https://token.actions.githubusercontent.com`, or `.../<enterprise>` with a unique enterprise issuer                                      | `https://<host>/_services/token`      |
| Condition key prefix  | `token.actions.githubusercontent.com` (or `.../<enterprise>`)                                                                             | `<host>/_services/token`              |
| Audience              | `sts.amazonaws.com` (`vars.VRA_OIDC_AUDIENCE` overrides)                                                                                  | Same                                  |
| Subject               | `repo:<org>/<repo>:environment:<env>`; repositories that use immutable subjects: `repo:<org>@<org-id>/<repo>@<repo-id>:environment:<env>` | `repo:<org>/<repo>:environment:<env>` |

One script is the source of this trust for the CI roles and the pipeline's hand-off role:
`cicd/oidc/github_trust.py`. AWS CloudFormation cannot compute a condition key from a parameter, so for
GitHub Enterprise Server or a unique enterprise issuer render the role template for your issuer and deploy
the rendered copy:

```bash
issuer="$(python3 cicd/oidc/github_trust.py issuer --server https://ghes.example.com)"   # ghes.example.com/_services/token
python3 cicd/oidc/github_trust.py template --issuer "${issuer}" --out vra-ci-oidc-role.ghes.yaml
python3 cicd/oidc/github_trust.py policy --provider-arn <provider ARN> --repo <org>/<repo> --environment prod
```

Pass the provider ARN as `OidcProviderArn`. For the immutable subject set `RepositoryOwnerId` and
`RepositoryId` (with `Repository`), or `actionsHandoff.repositoryIds` for the hand-off role; `policy`
prints the trust policy for review. GitHub Enterprise Server must serve
`/_services/token/.well-known/openid-configuration` and its JWKS to AWS.

The `vra-handoff` role holds no deploy rights. It allows only `codepipeline:StartPipelineExecution` and
`codepipeline:GetPipelineExecution` on the release pipeline. When `automation.pipeline.actionsHandoff` is
set, the pipeline stack creates it (`<prefix>-actions-handoff-<region>`, trusting the configured OIDC
subjects such as `repo:<org>/<fork>:environment:vra-handoff`) and lists it in the pipeline's IT package.

## Hand-off to CodePipeline

In `pipeline+actions` the release pipeline's fork source does not start an execution on push. When both
`pr-checks.yml` and `security-scan.yml` passed for a trunk commit, `hand-off-to-codepipeline.yml` starts the
pipeline with a source revision override for that commit (source action `Fork`, or
`vars.VRA_PIPELINE_SOURCE_ACTION`) and the client request token `vra-<sha>`, so a second trigger for the
same commit returns the same execution. The gate counts only runs on the trunk (`vars.VRA_TRUNK`, default
`main`) from the two workflow files themselves (`.github/workflows/vra-pr-checks.yml`,
`.github/workflows/vra-security-scan.yml`), so a branch cannot satisfy it with a workflow of the same name;
a dispatched SHA must be on the trunk (`compare` status `identical` or `behind`). It then reads the ASH
summary of the counted scan run and stops on unsuppressed findings at or above `vars.VRA_ASH_FAIL_ON`
(default `high`). On GitHub Enterprise Server with the v3 artifact actions the cross-run download is not
available; keep `VRA_ASH_FAIL_ON` at `high` on the scan itself there. Deploys, approvals and tests then run in CodePipeline as described
in [CI/CD and AWS CodePipeline](codepipeline.md).

## GitHub Enterprise Server notes

-   **Runners.** GitHub Enterprise Server has no GitHub-hosted runners. Set `vars.VRA_RUNS_ON` to the
    runner labels as JSON (`["self-hosted","linux","x64"]`). Runners need git, Docker (for ASH), Python 3.12,
    Node.js 22, the AWS CLI (for the hand-off) and outbound HTTPS to the AWS endpoints.
-   **Actions.** Most `actions/*` actions are bundled with the server at the version of its release; the
    pinned commits are newer. Enable GitHub Connect or sync each pinned action with `actions-sync`.
    Third-party actions always need one of the two. Mirror ASH and set `vars.VRA_ASH_REPO`.
-   **Artifacts.** `upload-artifact` and `download-artifact` v4 and later do not run on GitHub Enterprise
    Server. `python3 cicd/github-actions/pins.py ghes cicd/github-actions ghes-workflows` writes copies with
    the v3 pins from `action-pins.json`.
-   **gh and git.** `setup-vra` sets `GH_HOST` from `GITHUB_SERVER_URL`; the workflows set both `GH_TOKEN` and
    `GH_ENTERPRISE_TOKEN`, and git authenticates with `VRA_GIT_TOKEN` through a credential helper.
-   **Code scanning, dependency review and secret scanning** need GitHub Advanced Security on private
    repositories; dependency review on GitHub Enterprise Server also needs GitHub Connect. Without code
    scanning, set `vars.VRA_SARIF_UPLOAD=false`; the SARIF files stay in the run artifacts.

## Security controls

-   Every action is pinned to a full commit SHA, listed in `action-pins.json` and checked with
    `python3 cicd/github-actions/pins.py check`. Downloaded tools are pinned by version and SHA-256.
-   Workflow `permissions:` is `contents: read`; jobs add only what they use (`id-token: write` for OIDC,
    `actions: read` for the approvals API, `security-events: write` for SARIF).
-   Inputs reach scripts only through `env:` and are validated before use. Every checkout uses
    `persist-credentials: false`, except the home checkout of the fork's `vra-plan` job: it keeps the
    fork's read-only `VRA_HOME_TOKEN` for the engine's `git pull` and runs a dry-run plan only.
-   The home's write token (`secrets.VRA_HOME_TOKEN`) never enters a job that runs fork or upstream code
    (`npm ci`, synth, the live suites). Those jobs read the home with the job's own `github.token`, keep the
    engine's commits local (`VRA_HOME_DEFER_PUSH=1`) and upload `state/` and `checkpoints/` as an artifact;
    the reusable `vra-home-publish.yml` job, on a fresh runner with no fork code, copies it into a fresh
    checkout (`vra-home.sh adopt`: only those paths, no links) and pushes. With a git home this publishes
    CI state at the end of each job, so prefer `stateStore.type: s3` (live locks in Amazon DynamoDB) when
    operators also deploy CI-managed environments by hand. `VRA_GIT_TOKEN` must not have write access to
    the home repository.
-   The live suites of approval-gated environments always run in the separate `<env>-tests` job (role
    `vra-test-<env>` only); a prod-class environment refuses tests in the deploy job.
-   `vra-security-scan` fails on unsuppressed HIGH or CRITICAL ASH findings for pushes
    (`vars.VRA_ASH_FAIL_ON`, default `high`) and only reports them on pull requests
    (`vars.VRA_ASH_FAIL_ON_PR`, default `none`).
-   One job per environment at a time (`vra-env-<env>`, queued, never cancelled), one upstream sync at a
    time, one hand-off per commit. Pull requests from other repositories get no secrets.

## Variables and secrets

| Name                                                    | Where                     | Use                                                                   |
| ------------------------------------------------------- | ------------------------- | --------------------------------------------------------------------- |
| `VRA_AWS_REGION`, `VRA_PACKAGE_REPO`, `VRA_PACKAGE_REF` | Both repositories         | Region; the pinned release automation package (repository, tag)       |
| `VRA_FORK_REPO`                                         | Home                      | The fork (live-test checkout)                                         |
| `VRA_CI_ROLE_ARN`                                       | Each GitHub environment   | The environment's OIDC role                                           |
| `VRA_RUNS_ON`, `VRA_OIDC_AUDIENCE`                      | Both (optional)           | Runner labels (JSON), token audience                                  |
| `VRA_LOCK_WAIT_MINUTES`, `VRA_WATCH_COMMAND`            | Home (optional)           | Lock wait; `sync` when `deploy.yml` owns dev                          |
| `VRA_PREVIEW_ENV`, `VRA_PREVIEW_AUTO_ROLLBACK`          | Home (optional)           | Preview environment (default `dev-preview`); rollback on failure      |
| `VRA_HANDOFF_PIPELINE`, `VRA_PIPELINE_SOURCE_ACTION`    | Fork (`pipeline+actions`) | Release pipeline name; its fork source action (default `Fork`)        |
| Secret `VRA_GIT_TOKEN`                                  | Both                      | Read the fork and the package, push `sync/*`, open pull requests      |
| Secret `VRA_HOME_TOKEN`                                 | Home (fork: read only)    | Only the publish job pushes with it; unset with `stateStore.type: s3` |
| `VRA_TRUNK`, `VRA_ASH_FAIL_ON`, `VRA_ASH_FAIL_ON_PR`    | Fork (optional)           | Hand-off trunk (default `main`); ASH thresholds (`high`, `none`)      |

The full list, including the scan thresholds, is in the
[workflows README](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsReleaseAutomation/cicd/github-actions/README.md).

## Related pages

-   [Automation tiers](automation-tiers.md)
-   [CI/CD and AWS CodePipeline](codepipeline.md)
-   [Shared home](shared-home.md)
-   [Approvals and gates](approvals-and-gates.md)
