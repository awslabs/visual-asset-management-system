# cicd/gitlab-ci — reference pipeline

Copy `.gitlab-ci.yml` into the private vra home project (the one holding `vra-home.yaml`, `ledger/` and
`state/`).

-   The deploy jobs follow the ledger's release lane. After adding an env or changing a `promotesFrom`,
    regenerate them with `ci_graph.py render --platform gitlab --ledger ledger` and paste them over the
    `deploy-*` jobs; the validate job runs `ci_graph.py check` and fails on any difference.
-   Envs with `requiresApproval` extend `.deploy-gated`: manual jobs on protected branches only, in protected
    environments. Restrict "Allowed to deploy" to release managers and add deployment approval rules (GitLab
    Premium; required: without deployment approvals the gated jobs refuse to deploy). The approver is a user
    who APPROVED the deployment, read from the Deployments API with `VRA_GITLAB_API_TOKEN` (`read_api`) by
    `approval_record.py gitlab`, never the user who played the job; that user (`GITLAB_USER_LOGIN`) is vra's
    initiator, and vra rejects an approver who is the initiator or not in `envs/<env>/env.yaml` `approvers`.
    Put `ticket: CHG-1234` in the approval comment. Ungated envs extend `.deploy-auto` and get no approval
    record, promoted or not.
-   The test step checks out the fork at the build commit from the bundle manifest, not the trunk tip.
-   Concurrency: every deploy job holds `resource_group: vra-<env>`, so one job per env runs at a time; the
    vra env lock is the second guard (deploys wait up to `VRA_LOCK_WAIT_MINUTES`, default 30).
-   `.deploy-gated` / `.deploy-auto` set `VRA_REQUIRES_APPROVAL`; the job re-reads the ledger
    (`vra ledger graph`) and stops with exit 20 when the rendered job disagrees.
-   Human-only actions: "Run pipeline" with `VRA_JOB=operator`, `VRA_ENV`, `VRA_OPERATOR_ACTION`
    (`unlock`, `takeover`, `confirm`), `VRA_RUN`, `VRA_REASON`. The manual `operator` job deploys to the env's
    protected environment (GitLab records it as a deployment), so its deployment approvals approve it;
    `approval_record.py gitlab --human <action>` writes the `human:<action>` record and vra runs with
    `--human-approval`. Release jobs never run in that pipeline.

## Home folder

Each job clones this project at the default branch into `vra-home/` (`cicd/codebuild/scripts/vra-home.sh`):
with the job token (read only) by default, or with `VRA_HOME_TOKEN` (project access token,
`write_repository`), so the jobs push the state they write. Leave `VRA_HOME_TOKEN` unset when `vra.yaml` uses
`stateStore.type: s3`. Protect `ledger/` and `vra-home.yaml` (merge requests only) if the token can push.

## AWS access: one token audience per environment

GitLab's default OIDC subject (`project_path:<group>/<project>:ref_type:branch:ref:main`) is the same for every
job on the branch, and scoping `VRA_CI_ROLE_ARN` to an environment only hides the role name. The boundary is
the audience: each job requests its own (`id_tokens: VRA_OIDC_TOKEN: aud: vra-<env>`; validate and build
`vra-build`, the watch `vra-watch`), the template `.vra` requests none, and each role trusts only its own
audience. A job's token is issued for its audience when the job starts, so code running inside the build job
cannot obtain a token the prod role accepts.

Deploy `cicd/oidc/vra-ci-oidc-role.yaml` with `Provider=gitlab`, `Repository=<group>/<project>` and
`CiEnvName=<environment>` once per GitLab environment (`build`, `watch`, each env; `<env>-tests` with
`DeployableEnvs=none`, `TestEnvs=<env>` when `VRA_ISOLATE_TESTS=true`). The audience defaults to `vra-<CiEnvName>`, and the
template refuses `sts.amazonaws.com` for GitLab. The first stack creates the IAM OIDC provider; give it
`ProviderAudiences` = every audience above (or add them to an existing provider's client ID list).
GitLab 18.7 and later can also put `deployment_tier` / `environment_protected` into the subject
(`ci_id_token_sub_claim_components`); match them with `SubjectPatterns`. Self-managed GitLab: replace
`gitlab.com` in the template's condition keys and provider URL with your host.

The SDK / CLI assume `VRA_CI_ROLE_ARN` through `AWS_WEB_IDENTITY_TOKEN_FILE` (refreshed automatically).

## Other settings

-   The daily watch: a pipeline schedule with `VRA_JOB=watch` (and `VRA_WATCH_COMMAND=sync` when this
    pipeline owns dev deploys); the release jobs are skipped in that pipeline. Scope the `watch`
    environment's role to the autoDeploy envs' accounts only.
-   Isolated live tests (`VRA_ISOLATE_TESTS=true`): the deploy job skips the suites and a `tests-<env>` job
    (template `.tests`) runs them in environment `<env>-tests` with a role that may assume only
    `vra-test-<env>`.
-   Docker jobs need a runner tagged `privileged` (CDK bundling). Pin images by digest through
    `VRA_CI_IMAGE` (`name@sha256:...`) and set `VRA_CI_IMAGE_DIGEST`; vra records both in the manifest.
-   The build role is deployed with `DeployableEnvs=none`, `AllowSynthRoles=true`; the validate and build
    jobs do not preflight gated envs (each deploy job preflights its own env).
-   JUnit reports: `vra-home/state/runs/**/junit/**/*.xml`.
