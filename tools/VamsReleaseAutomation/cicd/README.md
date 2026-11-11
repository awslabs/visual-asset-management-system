# cicd/ — optional CI/CD lanes

The `vra` CLI driven by an agent (or an operator) is the default engine. Everything in this
directory is optional: each lane is a thin wrapper that installs a pinned toolchain and calls the
same `vra` commands with `--json --non-interactive`. No lane re-implements a gate; approval
records, separation of duties, promotion order, `autoDeploy` and change windows are all checked by
`vra`, so switching lanes never changes what is allowed.

| Path                                                         | Lane                                                                                                                                                                                                                                                                        |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `codebuild/buildspec-{validate,build,deploy,test,watch}.yml` | AWS CodeBuild projects                                                                                                                                                                                                                                                      |
| `codebuild/scripts/`                                         | Shared by every lane: `install-toolchain.sh`, `run-vra.sh` (exit-code mapping), `vra-home.sh` (home folder in a job), `ci_graph.py` (topology from the ledger), `record-approval.sh` + `approval_record.py` (the platform's own approval of THIS run → vra approval record) |
| `pipeline/`                                                  | CDK app for the tools account: release pipeline built from the ledger's promotion graph, preview pipeline, scheduled watch, notifications                                                                                                                                   |
| `oidc/vra-ci-oidc-role.yaml`                                 | Per-environment CI role: OIDC for GitHub Actions / GitLab CI (no long-lived keys), or `Provider=generic` assumed from a Jenkins / Azure agent role                                                                                                                          |
| `github-actions/`                                            | Reference workflows with environment protection rules                                                                                                                                                                                                                       |
| `gitlab-ci/`                                                 | Reference `.gitlab-ci.yml` with protected-environment jobs and per-environment token audiences                                                                                                                                                                              |
| `generic/`                                                   | Any other CI (Jenkins, Azure Pipelines): `vra-ci.sh` and the `VRA_CI_PROVIDER=generic` contract                                                                                                                                                                             |

## The home folder in CI

Every job works on the same home folder as every operator machine (`VRA_HOME`: `vra-home.yaml`, `ledger/`,
`state/`, `artifacts/`, `checkpoints/`). `codebuild/scripts/vra-home.sh` gives a job its copy:

1. `vra-home.sh checkout`: clone the home repository (`VRA_HOME_REPO`, branch `VRA_HOME_REF`) into
   `VRA_HOME`, or reuse the checkout the CI already made, and put it on the branch tip.
2. `vra-home.sh sync home-pull`: before the job reads state (`vra home sync`).
3. The job runs `vra`; the engine pulls before it takes a lock and pushes after it writes a lock, history,
   approval consumption or run state.
4. `vra-home.sh sync home-push`: at the end of the job, also after a failure. A push rejected because
   another job moved first is lock contention (exit 20).

Two ways to share state, chosen in `vra.yaml`:

| `stateStore.type` | What the jobs share                                                                    | Home repository access per job   |
| ----------------- | -------------------------------------------------------------------------------------- | -------------------------------- |
| `home` (default)  | Everything through the home repository (git sync in `vra-home.yaml`)                   | Read and push (`VRA_HOME_TOKEN`) |
| `s3`              | Locks, history, run state in S3 + DynamoDB (`iam/cloudformation/vra-state-store.yaml`) | Read only; nothing is pushed     |

Which jobs can write the home repository: with git sync, every job that runs `vra` pushes state, including
the build and test jobs, which run fork code (`npm ci`, Playwright). `vra-home.sh` refuses to publish a job's
changes outside `state/`, `artifacts/` and `checkpoints/`, and in CI the approval of a gated env comes from
the CI platform's own record, not from `ledger/approvals/`. Where fork code must not hold any write access to
the home repository, use `stateStore.type: s3` and give the jobs a read-only clone.

Bundles move between jobs as CI artifacts (`vra-home.sh bundle-out` / `bundle-in`), so every env deploys the
bytes the build produced.

## Topology comes from the ledger

The engine's view of the ledger, `vra --json ledger graph` (per env: `lane`, `promotesFrom`,
`requiresApproval`, `tier`, `isProdClass`, `requiredSuites`, `account`, `children`), defines the CI job graph.
No lane re-reads `envs/*/env.yaml`, so no CI file can disagree with the engine about a gate:

-   CodePipeline (`pipeline/`): stages come from `ledgerGraph` (a file with that output) or from `release.envs`;
    the Validate stage fails when the ledger's topology changed since the pipeline was deployed.
-   GitHub Actions, GitLab CI and generic CI: `ci_graph.py render --platform github|gitlab|generic` prints the
    deploy jobs (GitHub: with `requires-approval`; generic: the ordered `vra-ci.sh` steps); the validate /
    build job runs `ci_graph.py check` and fails on any difference, including a test step that misses a suite
    the ledger requires before the env promotes (`requiredSuites`).
-   Deploy jobs ask the ledger whether an env needs an approval (`ci_graph.py requires-approval`), not whether
    the job is a promotion, and pass `--approval` only then: an ungated promoted env gets no approval record, a
    gated root env gets one (scope `deploy`). A job rendered with another answer stops with exit 20.

Any topology works without code changes: several roots, fan-out (siblings run in parallel, each promoting
from its own `promotesFrom`), waves (a chain of gated envs), ungated intermediate envs.

## Concurrency

One job per env at a time on every lane: CodeBuild deploy projects run one build at a time, GitHub jobs use
the concurrency group `vra-env-<env>`, GitLab jobs the `resource_group` `vra-<env>`, Jenkins
`lock('vra-env-<env>')`. The vra env lock is the second guard: every deploy step passes
`--lock-wait ${VRA_LOCK_WAIT_MINUTES:-30}`, so a lock an operator or another host holds is waited for (the
holder's lease is heartbeated) before the step stops with exit 20. A release waiting for prod reviewers holds only
prod's group, never dev's. Pick one owner for dev deploys: when a release lane deploys dev on every trunk
update, run the schedule as `vra sync` (CodePipeline `watch.command`, GitHub `VRA_WATCH_COMMAND`, GitLab
`VRA_WATCH_COMMAND`); otherwise the second dev deploy stops with exit 20 (reported, not failed).

## Job environment

A CI image or job never exports a VAMS config variable (`STACK_NAME`, `REGION`, `ADMIN_EMAIL_ADDRESS`,
`ADMIN_USER_ID`, `CRED_TOKEN_TIMEOUT_SECONDS`, `PRESIGNED_URL_TIMEOUT_SECONDS`, `AWS_USE_FIPS_ENDPOINT`,
`AWS_USE_WAF`, `DEFAULT_USER_ROLE_NAME`). vra synthesizes from an allowlisted environment with the ledger
overlay's identity and stops a build that sees one of them unless `envs/<env>/env.yaml` `synthEnv` records it;
`run-vra.sh` refuses them before vra starts (exit 2), and the pipeline config refuses them in
`codebuild.environment`. FIPS endpoints follow env.yaml `fips`.

## Toolchain pins

Node 22, npm 11.19.1 (VAMS CI pin; anything ≥ 11.10 works), Python 3.12, uv 0.12.17, docker for
CDK asset bundling. The vra engine is installed from the pinned `vams-release-automation` checkout
(its root `pyproject.toml`) or a pinned wheel (`VRA_PACKAGE`). Third-party GitHub actions are pinned to commit SHAs.

Without internet egress: set `NPM_CONFIG_REGISTRY`, `PIP_INDEX_URL` and `UV_INDEX_URL` to a CodeArtifact or
internal mirror, pin the CDK CLI in the fork's `infra/package-lock.json` (or `AWS_CDK_VERSION`), mirror the
upstream VAMS repository and point `vra.yaml` `upstream.url` at it, and make the IAM endpoint reachable from
the job (preflight runs `iam:SimulatePrincipalPolicy`).

## Exit codes → CI result (`codebuild/scripts/run-vra.sh`)

| vra exit | Meaning         | CI result | What the operator does                                                                       |
| -------- | --------------- | --------- | -------------------------------------------------------------------------------------------- |
| 0        | ok              | success   | —                                                                                            |
| 10       | BLOCKED_ON_IT   | failed    | Send `it-requests/IT-REQUEST-<n>.md` from the run artifact to IT; then `vra resume <run-id>` |
| 20       | NEEDS_HUMAN     | failed    | Read `report.md` / `conflicts.json`; resolve, then resume                                    |
| 30       | TESTS_FAILED    | failed    | JUnit report group / test tab shows the failures; vra rolled back if the env policy says so  |
| 40       | ROLLBACK_FAILED | failed    | Page the on-call owner; the env may be inconsistent                                          |
| 1, 2     | error / usage   | failed    | Fix the job configuration                                                                    |

`VRA_SOFT_EXIT_CODES="10 20"` (set on the scheduled watch jobs) reports parked runs as success;
vra still notifies through the configured channel. When vra exits without printing its JSON envelope,
`run-vra.sh` writes one (`exitCode`, `status`, `message`) so callers always have JSON to parse.

Each job writes `vra-out/<step>.json` (vra stdout) and `<step>.result.json`
`{name, exitCode, outcome, runId}`, and uploads `vra-home/state/runs/**` (state, logs, JUnit, report, IT
requests) as build artifacts. JUnit XML from `vra-home/state/runs/**/junit/` feeds CodeBuild report groups,
GitLab test reports, Jenkins and Azure test results, or GitHub artifacts.

## What CI runs, and what stays with an operator

CI writes approval records for `deploy` and `promote` (24 h). The approver may add
`scope: emergency`, `accept-drift` or `allow-replacements` in the approval comment
(`ticket: CHG-1234; scope: emergency; notes: ...`). Restores (including `--include-auth-tables`, scope
`restore-auth-tables`), data copies and gated rollbacks are operator workflows (a person on a machine with the
home folder); a CI approval comment that asks for those scopes is refused. A CI deploy outside the env's change
window stops with exit 20; schedule the release run inside the window.

Human-only actions (`unlock`, `resume --takeover`, `resume --confirm`, `--abort-workflow-executions`,
`--writes-stopped`, `--allow-missing-scanners`, ...) never run from a release job. With no terminal vra
accepts them only with `--human-approval <record>`: subject `human:<action>`, scope `[<action>]`, bound to the
run, used once, approved by someone other than the initiator. `approval_record.py <platform> --human <action>`
writes it from the CI's own approval; `run-vra.sh` passes it when `VRA_HUMAN_APPROVAL` is set. Reference jobs:
`github-actions/vra-operator.yml`, the GitLab `operator` job, `vra-ci.sh operator`. Agents never run them.

## Scheduling the watch

CodeBuild (EventBridge Scheduler), GitHub (`vra-watch.yml`) and GitLab (pipeline schedule) run `vra watch`
without `--lanes`: the engine watches the lanes that are enabled and have an env. On a host with cron instead
of a CI scheduler, `vra-ci.sh cron` prints the line `vra watch --print-cron` builds for the home
(`vra --home <home> ... watch`).

## Which identity runs what

```
tools / CI account                                     env account (one per env)
  build tier   vra-codebuild-build-<region>       ── sts:AssumeRole ─▶ vra-synth-<env> (CDK lookup role only)
               vra-ci-<provider>-build (OIDC)
  dev tier     vra-codebuild-dev-<region>         ── sts:AssumeRole ─▶ vra-deploy-<root ungated env>, vra-deploy-dev-preview
               vra-ci-<provider>-dev / -vra-watch
  env tier     vra-codebuild-deploy-<env>-<region> ── sts:AssumeRole ─▶ vra-deploy-<env> ─▶ cdk-*-deploy-role ─▶ CloudFormation
               vra-ci-<provider>-<env>                                      (cfn exec role = vra-cdk-execution policy,
                                                                             mandatory boundary on created roles)
                                                  ── sts:AssumeRole ─▶ vra-data-copy-{source,target}-<env>
  test tier    vra-codebuild-test-<env>-<region>  ── sts:AssumeRole ─▶ vra-test-<env> (read endpoints + test secret)
  (optional)   vra-ci-<provider>-<env>-tests
```

Templates: `iam/cloudformation/`. With `stateStore.type: s3` every CI identity reads and writes run state,
creates history records and bundles only with `If-None-Match` (the state bucket policy enforces it for every
principal), claims approvals, never deletes anything in the store, and takes only lock items it needs: run
leases (`run#*`), the `env#<env>` locks of its envs, `env#watch` for the watch tier (the lock table's resource
policy admits only these items and the engine's attributes). Each identity that runs an env's gated actions
gets `kms:Verify` on the env's `approvalSigningKeyArn`. Each CI identity reaches only the envs of its tier: the build tier no
deploy role at all (synth uses `synthRoleArn` or no credentials for gated envs), the watch / dev tier the root
ungated envs and dev-preview only, every other env's tier only that env (OIDC roles are pinned by the roles'
`vra:env` tag, `DeployableEnvs`). The deploy role's `TrustedPrincipalArns` is the second lock: trust exactly
the env's own tier. The test tier (CodePipeline `release.isolateTests`, GitHub `isolate-tests`, GitLab
`VRA_ISOLATE_TESTS`) runs the live suites, which install fork dependencies, without any deploy role.

On GitHub the job's environment is part of the OIDC subject (`repo:<repo>:environment:<env>`). On GitLab the
default subject is the same for every job on a branch, so each job requests its own token audience
(`id_tokens` `aud: vra-<env>`) and each role trusts only its own audience; scoping the role ARN variable to an
environment only hides the name. On CodeBuild each env has its own projects and service role.

## Requirements the lanes place on the engine

-   Identity: vra takes the initiator from the platform (`GITHUB_TRIGGERING_ACTOR`, `GITLAB_USER_LOGIN`,
    the CodePipeline execution trigger, `VRA_INITIATOR` with `VRA_CI_PROVIDER=generic`) and requires gated-env
    approvals to be bound to the run (`source`), so the lanes pass `VRA_PIPELINE_NAME` /
    `VRA_PIPELINE_EXECUTION_ID` (CodePipeline) or `VRA_RUN_REF` (generic) and write the record with
    `approval_record.py`.
-   `vra build --json` prints `buildId` (the lanes read `vra-out/build.json`).
-   `vra deploy|promote --approval <file>.yaml` reads the approval record (fields in
    `engine/vra/data/schemas/approval.schema.json`). The lanes write JSON content into a
    `.yaml` file because the engine treats `--approval` as a path only for `.yaml` / `.yml`.
-   Credentials: chained role sessions are capped at one hour and VAMS deploys can take longer. The
    engine handles this itself (refreshable sessions; `cdk` and migration children use a
    `credential_process` profile that runs `vra credential-process`). The CI job's own identity must
    stay valid for the whole run (see [Long deploys and credentials](../../../documentation/docusaurus-site/docs/deployment/release-automation/codepipeline.md#long-deploys-and-credentials)).
-   `vra report --notify` sends the run report through `vra.yaml` `notifications`. In a job,
    `run-vra.sh` hands a `report` step that names no run the run of the job's own earlier `vra` step
    (`vra-out/last-run-id`), so a retried (resumed) deploy reports itself and not the newest run in the home.

## Private API endpoints

With `api.apiGatewayRest.endpointType: PRIVATE` in an env's overlay, the VAMS API is reachable only inside its
VPC. The live suites and customer hooks of that env must run there: CodeBuild projects in the VPC
(`pipeline` `codebuild.vpc`, with the `execute-api` VPC endpoint), a self-hosted GitHub runner or GitLab runner
in the VPC, or a Jenkins / Azure agent there. Deploys themselves only call AWS APIs and work from outside.
