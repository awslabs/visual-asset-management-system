# cicd/github-actions — reference workflows (GitHub Enterprise Cloud and Server)

Sample workflows for every automation tier that uses GitHub Actions. Each one wraps the `vra` CLI; the engine
enforces every gate. Developed and linted (actionlint, offline tests in `cicd/tests/test_actions*.py`); not run
against a live GitHub organization.

Two repositories take part:

-   the customer's **VAMS fork** (the code): checks and scans of every branch, and the hand-off to CodePipeline;
-   the private **vra home repository** (`vra-home.yaml`, `ledger/`, `state/`): sync, build, deploys, operator actions.

| File                           | Copy into                               | Purpose                                                                                                  |
| ------------------------------ | --------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `pr-checks.yml`                | fork `.github/workflows/`               | VAMS unit suites, lint, synth + cfn_nag (the upstream `ci.yml` jobs), `vra` plan, customer hook          |
| `security-scan.yml`            | fork `.github/workflows/`               | ASH, CodeQL, dependency review, SBOM, gitleaks; SARIF to code scanning                                   |
| `hand-off-to-codepipeline.yml` | fork `.github/workflows/`               | `pipeline+actions` tier: after both check workflows pass on the trunk, start the release pipeline        |
| `deploy.yml`                   | home `.github/workflows/`               | Build once, then environment-protected deploy jobs per env, rendered from `vra --json ledger graph`      |
| `vra-deploy-env.yml`           | home `.github/workflows/`               | Reusable: deploy or promote one env, approval record from the environment reviewers, live tests          |
| `upstream-release.yml`         | home `.github/workflows/`               | Daily + manual: `vra sync`, merge a new VAMS release into `sync/release-*`, open the fork pull request   |
| `upstream-preview.yml`         | home `.github/workflows/`               | Daily + manual: merge the upstream development commit (preview lane), build, deploy and test dev-preview |
| `vra-watch.yml`                | home `.github/workflows/`               | Daily `vra watch` (autoDeploy envs only), the `hybrid` tier without the two upstream workflows           |
| `vra-operator.yml`             | home `.github/workflows/`               | Human-only actions (unlock, takeover, confirm) approved in the env's GitHub environment                  |
| `vra-home-publish.yml`         | home `.github/workflows/`               | Reusable: pushes a job's `state/` artifact to the home; the only job that holds `VRA_HOME_TOKEN`         |
| `vra-release.yml`              | (0.4.0)                                 | Replaced by `deploy.yml`; same deploy jobs, kept for one release. Use one of the two.                    |
| `setup-vra/`                   | used from the pinned `vra-src` checkout | Composite action: toolchain, engine, `GH_HOST`, git credentials from `VRA_GIT_TOKEN`                     |
| `action-pins.json`, `pins.py`  | stays in the package                    | Every action pinned to a commit SHA; `pins.py check` and the GHES rewrite                                |
| `scripts/`                     | used from the pinned `vra-src` checkout | `ash_report.py` (summary, SARIF split), `checks_passed.py` (hand-off gate)                               |

`pr-checks.yml` and `security-scan.yml` also have a `workflow_call` trigger (organization required workflows,
reuse from another workflow). The fork keeps the upstream `ci.yml` (it is VAMS core and every sync would conflict
on a deletion); disable it in the fork's Actions settings so the suites do not run twice.

## Automation tiers

| Tier               | Workflows                                                                                                                                                                |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `agent`            | none (an operator or agent runs `vra`)                                                                                                                                   |
| `hybrid`           | fork: `pr-checks.yml`, `security-scan.yml`; home: `upstream-release.yml`, `upstream-preview.yml` (or `vra-watch.yml`), `deploy.yml` for dev only, the rest through `vra` |
| `pipeline`         | none here (CodePipeline end to end, `cicd/pipeline/`)                                                                                                                    |
| `pipeline+actions` | fork: `pr-checks.yml`, `security-scan.yml`, `hand-off-to-codepipeline.yml`; home: `upstream-release.yml`                                                                 |

In `pipeline+actions` the release pipeline's fork source must not start executions on push; the hand-off starts
each execution for the commit that passed the checks (source revision override, client request token
`vra-<sha>`, so a second trigger for the same commit returns the same execution).

## Deploy jobs from the ledger

The `deploy-*` jobs of `deploy.yml` are generated from the engine's graph. After adding an env or changing a
`promotesFrom`, regenerate them and paste them between the "rendered" markers:

```bash
bash vra-src/cicd/codebuild/scripts/vra-py.sh ci_graph.py render --platform github --ledger ledger
```

Each job passes `requires-approval` (the ledger's `requiresApproval`); `vra-deploy-env.yml` re-reads the ledger and
stops with exit 20 when the two disagree, and passes `--approval` only for a gated env. The build job runs
`ci_graph.py check` on `deploy.yml` and fails on any difference. Deploys wait up to `vars.VRA_LOCK_WAIT_MINUTES`
(default 30) for a busy env lock.

## GitHub environments and approvals

| Environment   | Repository | Reviewers                      | AWS role (`VRA_CI_ROLE_ARN`)                                                              |
| ------------- | ---------- | ------------------------------ | ----------------------------------------------------------------------------------------- |
| `vra-build`   | home       | none                           | `CiEnvName=vra-build`, `DeployableEnvs=none`, `AllowSynthRoles=true` (only `vra-synth-*`) |
| `vra-sync`    | home       | none                           | only with `stateStore.type: s3`; else unset                                               |
| `vra-watch`   | home       | none                           | `DeployableEnvs=<autoDeploy envs>`                                                        |
| `<env>`       | home       | gated envs: required reviewers | `CiEnvName=<env>`, the env's account                                                      |
| `<env>-tests` | home       | none                           | only with `isolate-tests`: `DeployableEnvs=none`, `TestEnvs=<env>`                        |
| `vra-handoff` | fork       | none (CodePipeline approves)   | start executions of the release pipeline only (below)                                     |

-   Gated envs: required reviewers, "Prevent self-review", deployment branches = default branch. List the reviewer
    logins in `envs/<env>/env.yaml` `approvers` too. The deploy job reads the approvals of THIS run from the API and
    `approval_record.py github` writes the engine's approval record (approver = the reviewer, never the triggering
    actor; bound to this run; valid 24 h; used once). Put `ticket: CHG-1234; notes: ...` in the approval comment.
-   `vra-operator.yml` runs in the env's environment; its approval becomes a `human:<action>` record.
-   The test step checks out the fork at the build commit from the bundle manifest, not the trunk tip.

## AWS access (OIDC, no keys)

Deploy `cicd/oidc/vra-ci-oidc-role.yaml` in the tools account once per GitHub environment (`Provider=github`,
`Repository=<org>/<repo>`, `CiEnvName=<environment>`); it trusts only
`repo:<org>/<repo>:environment:<environment>`. Never trust a branch subject (`...:ref:refs/heads/main`): every
job on that branch would match it. Set the role ARN as the environment variable `VRA_CI_ROLE_ARN` of the matching
environment and add it to `TrustedPrincipalArns` of that env's `vra-deploy-<env>` role only.

| Setting               | GitHub Enterprise Cloud (github.com, `*.ghe.com`)                                                                                                         | GitHub Enterprise Server              |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| Issuer (IAM provider) | `https://token.actions.githubusercontent.com`, or `.../<enterprise>` when the enterprise uses a unique issuer URL                                         | `https://<host>/_services/token`      |
| Condition key prefix  | `token.actions.githubusercontent.com` (or `.../<enterprise>`)                                                                                             | `<host>/_services/token`              |
| Audience              | `sts.amazonaws.com` (`vars.VRA_OIDC_AUDIENCE` overrides; China partition: `sts.amazonaws.com.cn`)                                                         | same                                  |
| Subject               | `repo:<org>/<repo>:environment:<env>`; repositories created after 15 July 2026 (or opted in) use `repo:<org>@<org-id>/<repo>@<repo-id>:environment:<env>` | `repo:<org>/<repo>:environment:<env>` |

`cicd/oidc/github_trust.py` is the one source of this trust for the CI roles and the pipeline's hand-off role.
The shipped role template spells out the public github.com issuer; AWS CloudFormation cannot compute a condition
key from a parameter, so for GHES or a unique enterprise issuer render a copy for the issuer and deploy that:
`github_trust.py issuer --server https://<host>` prints the issuer, `github_trust.py template --issuer <issuer>
--out <file>` writes the template, and `github_trust.py policy --provider-arn <arn> --repo <org>/<repo>
--environment <env>` prints the trust policy for review. Pass the provider ARN as `OidcProviderArn`. For the
immutable subject set `RepositoryOwnerId` and `RepositoryId` (with `Repository`); the hand-off role takes
`actionsHandoff.repositoryIds`. The workflows only need
`vars.VRA_OIDC_AUDIENCE` when the audience is not `sts.amazonaws.com`. The IAM OIDC provider works in every
partition; GHES must serve `/_services/token/.well-known/openid-configuration` and `.../jwks` to AWS.

### Hand-off role

`vra-handoff` holds no deploy rights. The hand-off counts only runs on the trunk (`vars.VRA_TRUNK`, default
`main`) of the two workflow files themselves, checks that a dispatched SHA is on the trunk, and stops on ASH
findings at or above `vars.VRA_ASH_FAIL_ON` (default `high`) in the counted scan run's summary. Its role allows only (partition and account from the template parameters):

```json
{
    "Effect": "Allow",
    "Action": ["codepipeline:StartPipelineExecution", "codepipeline:GetPipelineExecution"],
    "Resource": "arn:${Partition}:codepipeline:${Region}:${ToolsAccount}:${ReleasePipelineName}"
}
```

## GitHub Enterprise Server

-   **Runners**: GHES has no GitHub-hosted runners. Set `vars.VRA_RUNS_ON` to the labels as JSON
    (`["self-hosted","linux","x64"]`). The runners need git, Docker (ASH), Python 3.12, Node 22, the aws CLI
    (hand-off) and outbound HTTPS to the AWS endpoints; `actions/setup-*` on runners without internet access need a
    tool cache.
-   **Actions**: most `actions/*` actions are bundled with GHES, at the version of the GHES release. The pins here
    are newer: enable GitHub Connect ("automatic access to GitHub.com actions") or sync each pinned action with
    `actions-sync` so the pinned commits exist on the instance. Third-party actions (`aws-actions/*`,
    `astral-sh/setup-uv`, `anchore/sbom-action`, `stelligent/cfn_nag`) always need one of the two. ASH is a
    repository checkout: mirror it and set `vars.VRA_ASH_REPO`.
-   **Artifacts**: `upload-artifact` v4+ and `download-artifact` v4+ do not run on GHES. Generate GHES copies with
    the v3 pins from `action-pins.json`:

    ```bash
    python3 cicd/github-actions/pins.py ghes cicd/github-actions ghes-workflows
    ```

-   **gh and git**: `setup-vra` sets `GH_HOST` from `GITHUB_SERVER_URL`; gh reads `GH_ENTERPRISE_TOKEN` for a GHES
    host (`GH_TOKEN` for github.com and `*.ghe.com`), so the workflows set both. git authenticates to the server
    with `VRA_GIT_TOKEN` through a credential helper that reads the environment.
-   **Code scanning, dependency review, secret scanning**: need GitHub Advanced Security (Code Security / Secret
    Protection) on private repositories on GHEC and GHES; dependency review on GHES also needs GitHub Connect and
    the dependency graph. Without code scanning set `vars.VRA_SARIF_UPLOAD=false`; SARIF stays in the artifacts.
    CodeQL on GHES uses the bundled `github/codeql-action` and its CodeQL bundle unless the pinned version is synced.
-   **Reusable workflows** across repositories: the called repository must allow access (repository Actions
    settings, "Accessible from repositories in the organization/enterprise").

## Security controls

-   Every action is pinned to a full commit SHA with its tag as a comment, listed in `action-pins.json`
    (`python3 cicd/github-actions/pins.py check`). Downloaded tools (Trivy, gitleaks) are pinned by version and
    SHA-256.
-   `permissions:` is `contents: read` at the workflow level; jobs add only what they use (`id-token: write` for
    OIDC, `actions: read` for the approvals API and run listing, `security-events: write` for SARIF).
-   Inputs reach scripts through `env:` only (never `${{ inputs.* }}` inside `run:`), and are validated before use.
-   Every checkout uses `persist-credentials: false`, except the home checkout of the fork's `vra-plan` job,
    which keeps the fork's read-only home token for a dry-run plan.
-   The home's write token never enters a job that runs fork or upstream code: those jobs read the home with
    `github.token`, keep the engine's commits local (`VRA_HOME_DEFER_PUSH=1`) and upload `state/` and
    `checkpoints/`; `vra-home-publish.yml` (fresh runner, no fork code) adopts only those paths and pushes.
-   Approval-gated envs always run their live suites in the `<env>-tests` job (role `vra-test-<env>` only),
    whatever `isolate-tests` says; a prod-class env refuses tests in the deploy job.
-   ASH fails on HIGH or CRITICAL findings for pushes (`vars.VRA_ASH_FAIL_ON`, default `high`) and only reports
    on pull requests (`vars.VRA_ASH_FAIL_ON_PR`, default `none`).
-   Concurrency: one job per env at a time (`vra-env-<env>`, queued, never cancelled); one upstream sync at a time
    (`vra-upstream-sync`); one hand-off per commit; pull request checks cancel superseded runs.
-   Pull requests from other repositories get no secrets: `vra-plan` is skipped for them.

## Home folder

Each home job checks out the home repository into `vra-home/` and runs `cicd/codebuild/scripts/vra-home.sh`
(pull at the start, publish at the end, also after a failure). Secret `VRA_HOME_TOKEN` (fine-grained token,
contents write on the home repository) is used only by `vra-home-publish.yml`, which each state-writing job calls
as `publish-<job>`; leave it unset when `vra.yaml` uses `stateStore.type: s3`. `VRA_GIT_TOKEN` must not have
write access to the home repository. Protect `ledger/**` and `vra-home.yaml` with a ruleset (changes through reviewed pull
requests only) if the token can push to the default branch.

## Variables and secrets

| Name                                                                            | Where                     | Use                                                                 |
| ------------------------------------------------------------------------------- | ------------------------- | ------------------------------------------------------------------- |
| `VRA_AWS_REGION`, `VRA_PACKAGE_REPO`, `VRA_PACKAGE_REF`                         | both repositories         | region; the pinned vra package (owner/name, tag or commit)          |
| `VRA_FORK_REPO`                                                                 | home                      | the fork (live-test checkout)                                       |
| `VRA_CI_ROLE_ARN`                                                               | each GitHub environment   | the environment's OIDC role                                         |
| `VRA_RUNS_ON`, `VRA_OIDC_AUDIENCE`                                              | both (optional)           | runner labels (JSON), token audience                                |
| `VRA_LOCK_WAIT_MINUTES`, `VRA_WATCH_COMMAND`                                    | home (optional)           | lock wait; `sync` when `deploy.yml` owns dev                        |
| `VRA_PREVIEW_ENV`, `VRA_PREVIEW_AUTO_ROLLBACK`                                  | home (optional)           | preview env (default dev-preview); rollback on failure              |
| `VRA_HOME_REPO`, `VRA_PR_HOOKS`, `VRA_SYNTH_REGION`                             | fork (optional)           | `vra-plan` job; customer hook path; synth region                    |
| `VRA_SARIF_UPLOAD`, `VRA_ASH_FAIL_ON`, `VRA_DEPENDENCY_FAIL_ON`, `VRA_ASH_REPO` | fork (optional)           | scan reporting and thresholds (ASH `high` for pushes); ASH mirror   |
| `VRA_ASH_FAIL_ON_PR`, `VRA_TRUNK`                                               | fork (optional)           | ASH threshold on pull requests (`none`); hand-off trunk (`main`)    |
| `VRA_HANDOFF_PIPELINE`, `VRA_PIPELINE_SOURCE_ACTION`                            | fork (`pipeline+actions`) | release pipeline name; its fork source action (default Fork)        |
| secret `VRA_GIT_TOKEN`                                                          | both                      | read the fork and the package; push `sync/*`; open pull requests    |
| secret `VRA_HOME_TOKEN`                                                         | home (fork: read only)    | only the publish job pushes; the fork's `vra-plan` reads the ledger |

Trigger `deploy.yml` from the fork with a `repository_dispatch` of type `vra-fork-trunk-updated` (payload
`{"ref": "<sha>"}`), run it manually, or call it from another workflow. Pull requests opened with
`VRA_GIT_TOKEN` trigger the fork's workflows (a pull request opened with `GITHUB_TOKEN` would not).

## Verify

```bash
python3 cicd/github-actions/pins.py check
actionlint -shellcheck=shellcheck cicd/github-actions/*.yml      # actionlint 1.7.12 was used
python3 -m pytest cicd/tests/test_actions.py
```

## Sources

-   [OpenID Connect reference](https://docs.github.com/en/actions/reference/security/oidc) (subject formats,
    immutable subject claims, enterprise issuer URL)
-   [Configuring OpenID Connect in AWS, GitHub Enterprise Server](https://docs.github.com/en/enterprise-server@latest/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
    (issuer `https://HOSTNAME/_services/token`, condition keys, self-hosted runners)
-   [About using actions in your enterprise (GHES)](https://docs.github.com/en/enterprise-server@latest/admin/managing-github-actions-for-your-enterprise/managing-access-to-actions-from-githubcom/about-using-actions-in-your-enterprise)
    (bundled actions, GitHub Connect, actions-sync)
-   [actions/upload-artifact](https://github.com/actions/upload-artifact) and
    [actions/download-artifact](https://github.com/actions/download-artifact) (v4+ not supported on GHES)
-   [actions/dependency-review-action](https://github.com/actions/dependency-review-action) (GHAS, GHES installation)
-   [aws-actions/configure-aws-credentials](https://github.com/aws-actions/configure-aws-credentials) (`audience`)
-   [gh environment variables](https://cli.github.com/manual/gh_help_environment) (`GH_HOST`, `GH_ENTERPRISE_TOKEN`)
