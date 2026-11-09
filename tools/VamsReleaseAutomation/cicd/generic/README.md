# cicd/generic — any other CI system

Jenkins, Azure Pipelines and every other CI system run vra through `vra-ci.sh`, with the same commands,
exit codes, artifacts and gates as the CodeBuild, GitHub Actions and GitLab lanes.

| File                                                           | What it is                                                                                                            |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `vra-ci.sh`                                                    | Entry script, one call per step: `home`, `validate`, `build`, `deploy`, `test`, `watch`, `operator`, `cron`, `finish` |
| `Jenkinsfile`                                                  | Declarative pipeline: build, dev, `input`-gated test and prod, one lock per env                                       |
| `azure-pipelines.yml` + `vra-steps-{setup,finish,promote}.yml` | Stages with deployment jobs on environments that carry an Approvals check                                             |

Neither reference was exercised on a live Jenkins controller or Azure DevOps organization.

## The contract

The CI sets these before every step. The engine reads them when `VRA_CI_PROVIDER=generic`.

| Variable                                                      | Meaning                                                                                                                       |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `VRA_CI_PROVIDER`                                             | `generic`                                                                                                                     |
| `VRA_INITIATOR`                                               | Who started the run, as the CI authenticated them (Jenkins build-cause user, Azure `Build.RequestedForEmail`). Never typed in |
| `VRA_RUN_REF`                                                 | Unique, stable id of the run, e.g. `jenkins:<job>/<build>`, `azure-devops:<project>/builds/<id>`. Approval records bind to it |
| `VRA_APPROVAL_REF`                                            | The CI's id of the approval step (Jenkins input id, Azure approval id). Recorded in the approval record                       |
| `VRA_HOME`, `VRA_HOME_REPO`, `VRA_HOME_REF`, `VRA_HOME_TOKEN` | The shared home folder, see `../codebuild/scripts/vra-home.sh`                                                                |

`vra-ci.sh deploy` writes an approval record only when the ledger gates the env (`requiresApproval`). The
approver is passed with `--approver` and must come from the CI's approval step (Jenkins `input`
`submitterParameter`, the Azure approvals API for the run), never from a run parameter. `approval_record.py
generic` refuses an approver equal to `VRA_INITIATOR`; the engine checks the approver against
`envs/<env>/env.yaml` `approvers`, the run binding (`source` equals `VRA_RUN_REF`) and single use.

The initiator and approver are asserted by the CI, not verified by a platform API vra knows. Treat the CI
controller and its credentials store as part of the trust boundary of every gated env it deploys.

## Step sequence

```text
vra-ci.sh home                                   # every job: clone / reuse the home folder, pull
vra-ci.sh validate                               # ledger validate (+ preflight of VRA_PREFLIGHT_ENVS)
vra-ci.sh build --lane release --ref <fork ref>  # prints the build id; hand vra-bundle/ to later jobs
vra-ci.sh deploy --env dev --build <id>          # root env, no approval
vra-ci.sh test --env dev --fork-dir fork         # fork checked out at manifest.json "commit"
vra-ci.sh deploy --env test --build <id> --promote-from dev --approver <who> --comment "ticket: CHG-1"
vra-ci.sh finish                                 # every job, also on failure: report, publish state
```

Follow the ledger's release lane for the order and the approvals:
`bash ../codebuild/scripts/vra-py.sh ci_graph.py render --platform generic --ledger <home>/ledger` prints these
steps from `vra ledger graph` (an `--approver` only for gated envs, the suites each env needs before it
promotes). Deploy steps wait up to `VRA_LOCK_WAIT_MINUTES` (default 30) for a busy env lock.

Human-only actions (unlock, takeover, confirm, `--abort-workflow-executions`, ...) run through an approval step
of the CI too: `vra-ci.sh operator --action unlock --env prod --approver <who> -- unlock --env prod --reason
"<why>"` writes the `human:unlock` record (bound to `VRA_RUN_REF`) and runs vra with `--human-approval`.

A host with cron and no CI scheduler: `vra-ci.sh cron` prints the crontab line (`vra watch --print-cron`).

## What each job needs

-   A home folder shared with every other job and operator: a clone of the home repository (git sync,
    `VRA_HOME_TOKEN` to push) or `stateStore.type: s3` in `vra.yaml` (read-only clone).
-   One AWS identity per tier (build: `vra-synth-<env>` only; each env: `vra-deploy-<env>` only); see
    `../README.md` "Which identity runs what". `../oidc/vra-ci-oidc-role.yaml` with `Provider=generic` creates
    `vra-ci-generic-<env>`, trusted by the agent role of that env's stage (`TrustedPrincipalArns`, optional
    `ExternalId`), with the same env pinning and state-store access as the OIDC roles.
-   One job per env at a time (Jenkins `lock('vra-env-<env>')`, Azure exclusive-lock check on the environment).
    The vra env lock is the second guard and stops a concurrent run with exit 20.
-   Docker on build and deploy agents (CDK asset bundling).
