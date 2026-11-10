---
title: "Troubleshooting"
sidebar_position: 21
description: "Day-to-day operation, reading reports, resuming runs, locks and contention between machines, failed rollbacks, restore drills, emergency changes and common errors."
---

# Troubleshooting

This page covers day-to-day operation of release automation and what to do when a run stops.

## Daily operation

| When                        | What happens                                                                                      | Who acts               |
| --------------------------- | ------------------------------------------------------------------------------------------------- | ---------------------- |
| On the schedule             | `vra watch` runs: sync, then merge, build, deploy and test for any lane with new upstream changes | Automation             |
| After the run               | The report is sent to the notification channel                                                    | Release owner reads it |
| Exit `0` with a new release | `dev` has the new build; plan the promotion to `test`                                             | Release owner          |
| Exit `10`                   | Forward the IT request                                                                            | Release owner          |
| Exit `20`                   | Resolve conflicts or provide a decision                                                           | Developer or agent     |
| Exit `30`                   | Triage failed tests; `dev` was rolled back when its policy is `auto`                              | Developer or agent     |
| Exit `40`                   | Page the environment owner                                                                        | On-call                |

A cycle with no upstream change still produces a short report. If no report arrives, the schedule
itself is broken; check the cron host or the Amazon EventBridge rule.

## Reading a run

```bash
vra status                    # latest runs and their status
vra status --all              # every run and lock of every machine
vra status <run-id>           # step-by-step state
vra report <run-id>           # Markdown report
```

| Report section | What to look for                                                                           |
| -------------- | ------------------------------------------------------------------------------------------ |
| Summary        | Run status, lane, upstream ref, build id, environments touched                             |
| Upstream       | New tags or commits                                                                        |
| Merge          | Policy-resolved paths, conflicts resolved by a person or agent, touched core-patch entries |
| Config upgrade | New or removed configuration keys per environment                                          |
| Build          | Suite results, scanner findings, SBOM location, version-roll notes                         |
| Deploy         | Checkpoint id, diff highlights (especially replacements), migrations applied               |
| Tests          | Pass and fail counts per suite, links to JUnit                                             |
| IT requests    | Open requests and whether they block                                                       |
| Open problems  | Anything that needs a person                                                               |

Run files are in the shared home under `state/runs/<run-id>/`: `state.json`, `logs/<step>.log`,
`junit/`, `it-requests/`, `conflicts.json`, `report.md` and `report.json`.

## Resuming a run

```bash
vra resume <run-id>
vra resume <run-id> --confirm     # the run asked for manual work and a person did it
```

Resume re-simulates the actions of any open IT request (writing `IT-REQUEST-<n+1>` and exiting `10` if
some are still denied), re-runs every gate step, then continues from the first step that is not done.
Completed steps are skipped, so it is safe to run more than once. A conflicted merge continues with
`vra merge --continue --run-id <run-id>` on the machine that ran it. Runs that ended `SUCCEEDED`,
`ROLLED_BACK` or `ROLLBACK_FAILED` cannot be resumed; start a new run. `vra --dry-run resume` is
refused (exit `2`) for a run that was not started with `--dry-run`, because a resume always changes the
stored run; read it with `vra report <run-id>` instead.

## Locks and contention

| Symptom                                                   | Cause                                                                          | Action                                                                                     |
| --------------------------------------------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| Exit `20`, the environment lock is held by another run    | Another run is deploying, or parked mid-change                                 | `vra status --all`; wait (`--lock-wait <minutes>`), or resume or roll back the holding run |
| Exit `20`, a second `vra watch` stops                     | Another machine holds the `watch` lock                                         | Run one scheduled watch per home; the other is redundant                                   |
| Exit `20`, the run is live on another machine             | `vra resume` from a machine that did not start the run, inside its lease       | Resume it on the machine that started it                                                   |
| The starting machine is gone and the lease expired        | The run cannot be resumed from elsewhere without a takeover                    | `vra resume <run-id> --takeover` (human-only; recorded)                                    |
| `vra status --all` lists a run under `orphanedRuns`       | A `RUNNING` run whose run lease expired: its process or machine went away      | Check nothing of it still runs, then `vra resume <run-id> --takeover` (human-only)         |
| Exit `20`, the run did not start, first publish refused   | The run's first push was rejected or refused; it is parked `NEEDS_HUMAN`       | Fix the home (`vra home sync`), then `vra resume <run-id>` or run the command again        |
| Exit `20`, the shared home push was rejected              | Another machine changed the same records first; the local change was dropped   | Run the command again; it re-reads the state                                               |
| Exit `20`, cannot fast-forward the shared home            | The local home diverged from its remote (a manual commit, an interrupted push) | Resolve it with git in the home, then `vra home sync`                                      |
| Exit `20`, the shared home has uncommitted ledger changes | A gated action needs a clean ledger at the remote HEAD                         | Commit and push the ledger change (or discard it), then retry                              |
| Exit `20`, `stale` run state                              | A newer copy of the run was written by another process                         | Read `vra status <run-id>` and retry from the current state                                |

### Dirty locks

A run that parks or fails after it started changing an environment (the AWS CloudFormation deploy, a table
clear and reload, a migration) keeps the environment lock and marks it dirty. A dirty lock never
expires, so no other run deploys a different build onto the half-changed environment.

-   Fix the cause and `vra resume <run-id>`: the run continues with its lock.
-   A retry of the AWS CodePipeline stage (or any resume from another machine) runs on a new host with
    another owner token, so it stops at the run's own dirty lock (exit `20`). Before the retry, a person
    releases the lock with `vra unlock --env <env> --run <run-id> --reason "..."` (with an approval
    record of scope `[unlock]` when no terminal is involved), confirming no AWS CloudFormation stack of the
    environment is `*_IN_PROGRESS`. The retried stage then resumes the run from the step that stopped.
    When the resumed run finishes and releases the lock, its report drops the "stays locked" note, and
    the job's report step reports and notifies that run.
-   With `rollbackPolicy: ask`, `vra rollback --env <env>` needs the lock: release it first with
    `vra unlock --env <env> --run <run-id> --reason "rolling back after failed smoke, CHG-123"`.
-   A `ROLLBACK_FAILED` run keeps its lock; follow [Failed rollback](#failed-rollback) and unlock only
    when the environment is consistent again.

### Stale locks

A lock can be left behind when the host running `vra` is killed. Before you release one, confirm in
`vra status --all` that the run is not live on any machine, and that no AWS CloudFormation stack of the
environment is in an `*_IN_PROGRESS` state. Prefer `vra resume <run-id>` on the machine that started the
run; only when the run cannot be resumed, run
`vra unlock --env <env> --run <run-id> --reason "<why>"` (human-only, recorded in the history). Never
release a lock while an AWS CloudFormation update is in progress.

A stopped CI build leaves the run lock of a build run, which belongs to no environment. Without a
terminal, `vra --human-approval approvals/<env>/<record>.yaml resume <build-run-id> --takeover` takes it
over: the record (subject `human:takeover`, scope `[takeover]`) is checked against the approvers of the
environment it names.

## Registered asset bucket blocked a run

VAMS users can register asset buckets at any time (customer-named, in other accounts, with their own AWS KMS
keys). Every backup checkpoint, restore, data copy and deploy with data migrations lists them again.

-   **Exit `10` naming a bucket.** The IT request's "Registered asset buckets" table lists each bucket and
    key, whether it is newly registered, and the exact actions. After IT grants them (role stacks:
    `AdditionalBucketArns`, `AdditionalKmsKeyArns`; in another account also the bucket or key policy), add
    the ARNs to `env.yaml` `registeredBuckets` with `granted: true`, commit the ledger and
    `vra resume <run-id>`.
-   **A deploy reports a pending IT request but succeeded.** It touched no bucket data. Handle the request
    before the next backup, restore, data copy or migration.
-   **Exit `20`, unmapped source asset buckets.** Set `dataCopy.bucketMap` on the target as the message
    shows, registering a target bucket first when needed, and resume. Nothing was written.
-   **Exit `20`, a bucket used by another environment.** Give the environment its own bucket in VAMS. There
    is no override.

## IAM roles and IT packages

These apply to environments whose `iam.mode` is not `standard`
([IAM for strict environments](iam-for-strict-environments.md)).

-   **Exit `10`, IT request of kind `iam-package`.** The request names the reason. "No package": run
    `vra iam plan --env <env>`. "Not applied": IT's answer is missing; a person runs
    `vra iam apply --env <env> --from it-response.yaml` and pushes the ledger. A precreated build that needs
    new roles wrote package `n+1` itself; send its "Changes since" section to IT. A boundary build with new
    roles asks for `vra iam plan --env <env> --ref <built ref>`. Then `vra resume <run-id>`.
-   **Exit `20`, "IAM role(s) ... carry no permission boundary".** The fork's VAMS version (v2.6.6 or
    earlier) replaces the boundary with `AWS::NoValue` (GitHub issue #431, fixed in VAMS v2.6.7). Upgrade
    the fork, or add the core patch CP-431, `core-patches/iam-role-transform-boundary.patch`, to the ledger
    and list it in `core-patches.md`, or use `iam.mode: precreated`. Never deploy the build unbounded.
-   **A first `precreated` deploy fails with "`<prefix>...-frameworkonEvent-...` is not authorized to perform:
    `lambda:GetFunction`" on a function named like `vams-core-<base>-us-east--<Logical>-<random>`.** The core
    stack name is longer than 25 characters, and AWS CloudFormation keeps only 25 of them in a generated Lambda
    name. IAM packages planned by vra before 0.5.0's fix granted `function:<core stack>*`, which matches no such
    function. Run `vra iam plan --env <env>` again (the new package grants `function:<first 25 characters>*`),
    have IT apply it, then `vra iam apply` and resume. The IT request of the failed run names one exact function
    that AWS CloudFormation deleted on rollback; use the new package instead.
-   **A `precreated` deploy or teardown of a pipeline registration fails with "`<prefix>...-frameworkonEvent-...`
    is not authorized to perform: `lambda:GetFunction`" on `importGlobalPipelineWorkflow`, often as
    `ROLLBACK_FAILED`.** IAM packages planned by vra before this 0.5.0 fix granted the provider role
    `function:arn:aws:lambda:...:function:<core stack>*`, a doubled ARN that matches nothing (the role's
    report names the handler by `Ref`, its function name). Run `vra iam plan --env <env>` again, have IT apply
    the new package, then `vra iam apply`. A stack left in `ROLLBACK_FAILED` deletes cleanly once IT's role
    carries the new policy.
-   **Exit `2` or a `ledger validate` error, "iam.mode precreated needs iam.rolePrefix".** In `precreated` mode IT's
    roles must start with a prefix that is VAMS's alone, because the scoped CDK execution policy reaches them only
    by it. Agree a prefix with IT (for example `ITVAMS-`), set `iam.rolePrefix` in the environment's `env.yaml`
    and run `vra iam plan --env <env>` again.
-   **HTTP 500 on every API route after switching to a `rolePrefix`, with
    `x-amzn-errortype: AuthorizerConfigurationException`.** The role replacement left the REST API stage
    on its old deployment, whose authorizer credentials name a deleted role (GitHub issue #1210). Add the
    core patch CP-1210, `core-patches/api-deployment-role-name.patch`, to the ledger, list it in `core-patches.md`,
    and deploy a new build. `vra ledger validate` warns about a missing entry. See
    [IAM for strict environments](iam-for-strict-environments.md#the-api-deployment-after-a-role-prefix-switch-github-issue-1210).
-   **Exit `20`, "core patch ... does not apply".** Upstream changed the patched file. If the change is
    already in the source (the reverse diff applies, or the patch's `Present-when:` marker matches), the
    build reports the patch as `present` instead; otherwise refresh the patch for the new VAMS version, or
    remove its entry once upstream ships the fix.
-   **Exit `20`, the overlay changed after apply, or bootstrap role ARNs differ.** An IAM overlay file was
    edited by hand. Apply a new package instead of editing the overlay.
-   **`vra iam apply` refuses the response.** The message lists each problem: a construct path left out,
    another package's id, a boundary in another account or partition, a role ARN with a path. Fix the
    response; nothing was written.
-   **`vra iam verify` lists items under `notChecked`.** The verifying identity cannot read them. Give the
    deploy role `EnableIamVerify=true` (or set `readOnlyRoleArn`) and run it again; unchecked items never
    pass. A statement that mixes actions IAM authorizes differently (KMS key actions with `kms:ListKeys`) is
    simulated one action at a time.
-   **`vra iam verify` lists `kms:ListKeys`, `kms:ListAliases` or SNS subscription actions under
    `notEffective`.** Known limitation: VAMS's own policy grants these on resources they never match
    (`kms:ListKeys` takes only `*`), so IAM denies them for VAMS-created roles too. They are labelled "not
    effective in VAMS's policy, not an IT gap" and are not problems; leave the IT roles as they are. The same
    action denied on `*`, or any other action, stays a problem.
-   **Exit `10` on `cloudformation:DescribeStackDriftDetectionStatus` for `*`.** The deploy role came from an
    older `vra-deploy-role.yaml`, which scoped this action to the stack ARNs. The action accepts
    only `*`. Update the role stack from the current template.
-   **Exit `10` on `cloudformation:DetectStackResourceDrift` at `deploy.<env>.drift`.** Drift detection
    runs the per-resource detection as the caller. The deploy role came from an older
    `vra-deploy-role.yaml` without that action; update the role stack from the current template, then
    retry the pipeline action (the parked run resumes).
-   **Exit `20` at `deploy.<env>.diff`, "the IAM quota of ... cannot hold this deploy".** The change adds
    or replaces more IAM roles (or managed policies) than the account has free, counting
    `iam.quotaHeadroom` (default 10). A permission-boundary or role-prefix switch replaces every VAMS role,
    and the old and new roles coexist during the swap. Nothing was deployed. Raise the account's IAM quota
    (Service Quotas > IAM > "Roles per account") or free roles, then `vra resume <run-id>`. This check
    applies in every IAM mode, also `standard`.
-   **Exit `10` on `iam:GetAccountSummary` for `*`.** The deploy role came from an older
    `vra-deploy-role.yaml` without the `IamQuotaRead` statement. Update the role stack from the current
    template. A rollback only notes the quota and never stops on it.
-   **Exit `20` at `deploy.<env>.diff`, "the CloudFront quota of ... cannot hold this deploy".** The
    change adds CloudFront cache policies, origin request policies, response headers policies or a
    distribution (a first install on CloudFront adds 2, 1, 1 and 1), and the account has fewer free than
    that plus `cloudfront.quotaHeadroom` (default 2). The message names the quota and its code, for
    example "Cache policies per AWS account (L-7D134442): 20 in use + 2 needed (2 added, 0 replaced) +
    headroom 2 > quota 20". Nothing was deployed. Request an increase (Service Quotas > Amazon
    CloudFront > the named quota; the quotas are global, so use US East (N. Virginia)) or delete unused custom
    policies or distributions (`aws cloudfront list-cache-policies --type custom`), then
    `vra resume <run-id>`. "AWS default, quota not readable" means Service Quotas returned no value and
    the documented default was used; check the quota in the console.
-   **Exit `10` on `cloudfront:List*` or `servicequotas:GetServiceQuota` for `*`.** The deploy role came
    from a `vra-deploy-role.yaml` older than 0.5.1, without the `CloudFrontQuotaRead` and
    `CloudFrontQuotaLimitsRead` statements. Update the role stack from the current template. Only
    deploys that add CloudFront resources read them; a rollback only notes the quota.
-   **Exit `10` on `secretsmanager:GetSecretValue` for the live-test secret.** The role templates grant
    `vra/<BaseStackName>/*`. Store `liveTest.auth.secret` under that name.

## Pipelines

-   **`vra pipeline deploy` exits `20` for an agent.** The plan is a first deploy, changes IAM or removes an
    approval; a person deploys it at a terminal or with a `human:pipeline-deploy` record of the approval
    environment.
-   **`vra pipeline deploy` refuses the plan.** The ledger or the synthesized template moved since the plan,
    or the plan was made `--offline`. Run `vra pipeline plan` again.
-   **The CodeConnections source stays failed.** A connection the stack created is `PENDING` until a person
    completes the handshake in the console.
-   **Validate stops `BLOCKED_ON_IT` with the AWS CodeBuild tier role as the principal.** The environment has
    no `deployRoleArn`, so the job ran as its tier role, which may only assume `vra-deploy-<env>`. Do not
    widen the tier role: have IT deploy `vra-deploy-role.yaml` for the environment and set `deployRoleArn`
    (current versions refuse this in `vra ledger validate`).
-   **Build exits `2`: "cannot resolve '<id>' in the fork" with an Amazon S3 fork source.** The zip was uploaded
    by an older `vra pipeline push-source` without `.vra-revision`, so the build took the Amazon S3 version id as
    the ref. Run `vra pipeline push-source` again (it uploads such an object once more) and redeploy the
    pipeline from the current package.
-   **The post-deploy smoke fails with "vamscli not found".** Neither the deployed build's bundle nor the
    runner has a VamsCLI. Builds from this release on carry a wheel built from the fork's `tools/VamsCLI`
    (manifest `vamscli`), which the smoke and the `live-cli` suite install on their own; an older bundle
    has none. Rebuild, or install VamsCLI on the runner (or set `VRA_VAMSCLI` to its path). The deploy is
    recorded as failed and rolled back per `rollbackPolicy`.
-   **"VamsCLI wheel ... does not match its manifest sha256".** The wheel in the bundle changed after the
    build. The engine refuses to run it; rebuild instead of copying a wheel in.
-   **The deploy job's smoke fails with AccessDenied on `sts:AssumeRole` for `vra-test-<env>`.** Engines
    before this release read the smoke's sign-in secret as the env's test role, which trusts only the test
    job. Update the engine package (`vra pipeline push-source --package`); the post-deploy smoke now reads
    with the deploy role. Do not add the deploy job to the test role's trust.
-   **The first deploy of a new environment stops at the smoke: "cannot read the live-test secret".** A fresh
    stack has no smoke user yet. Once the stack's Amazon Cognito user pool exists, create the user and store
    `{"username", "password"}` as the secret named in `liveTest.auth.secret` (`vra/<baseStackName>/...`),
    then retry the pipeline action (or `vra resume <run-id>`); the run continues at the smoke. A denied
    read parks the run with an IT request instead.
-   **A retried pipeline action starts over.** Only runs that parked (`BLOCKED_ON_IT`, `NEEDS_HUMAN`) resume;
    a `FAILED` or `TESTS_FAILED` run gets a new attempt. `VRA_PIPELINE_RESUME=false` turns resuming off.

## Failed rollback

`ROLLBACK_FAILED` (exit `40`) means `vra` could not return the environment to its previous good state.
Treat it as an incident for that environment.

1. **Stop changes.** Disable the schedule so `watch` does not try again.
2. **Read the state.** `vra status <run-id>` and the step logs show whether the redeploy, the data
   restore or the smoke failed.
3. **Check the stacks.** A stack in `UPDATE_ROLLBACK_FAILED` needs `continue-update-rollback` in AWS
   AWS CloudFormation, possibly skipping the failed resources. Resolve the cause first (often a resource
   changed outside AWS CloudFormation, or a missing permission). A failed first install left in
   `ROLLBACK_FAILED` has to be deleted before the next deploy. When `cdk deploy` itself leaves a stack in
   one of these states, the run's message names the stuck stack and the first failure (for example an
   account quota), and lists any permission errors that blocked AWS CloudFormation's rollback. Granting
   those permissions alone does not make the deploy succeed: fix the first failure as well.
4. **Check the data.** If a migration ran, data may be in the new shape while the code is old. Restore
   the run's checkpoint (`vra restore --env <env> --checkpoint <id> --approval <id|path>`), or restore
   tables to a point in time from PITR.
5. **Roll back again.** `vra rollback --env <env> --to-build <last good build> --restore-checkpoint <id> --approval <A> --restore-approval <R>`
   (approvals only on gated environments).
6. **Close out.** Record the incident, re-enable the schedule after the fix, and add a test that would
   have caught the failure.

`unknown build <id> (no .../artifacts/<id>/manifest.json)` from a pipeline's rollback means the job could
not find the previous build's bundle. A CI job has only its own execution's bundle. Redeploy the
pipeline stack so its jobs get `VRA_BUNDLE_STORE` (0.5.0 and later). Builds made before that were never
copied to the bundle store, so roll back to one of them from a machine that has its bundle, or rebuild
it. `bundle of <id> does not match the manifest <env> deployed` means the stored bundle is not the one
the environment ran. Do not deploy it; find out who changed the bucket. A `build.upload` step that takes
many minutes copies a bundle of thousands of files; 0.5.0 sends 16 at a time (`VRA_STORE_WORKERS`), so
raise the number on a job with more network headroom. `build <id> already exists in the bundle store
with different content` from a CI build before the 0.5.0 fix means the job rebuilt a commit that
already had a bundle (for example after `vra iam apply`). Upgrade the package; the next build reuses
the stored bundle or takes the next free id. The old refusal could add files to the stored bundle, so a
later deploy or rollback of that build stops at `bundle integrity check failed`; deploy a newer build
instead.

A rollback or restore that failed during its frozen part has already lifted the write freeze (the
report's "Write freeze" section says so). If the report says the freeze could not be lifted, turn VAMS
maintenance mode off by hand (the parameter is named there) or, for the manual backend, tell users that
writes may resume once the data is consistent.

## Restore drill

Run one before first production use and then quarterly. It proves that a checkpoint can be restored
with the IAM you grant, how long it takes for your table count, and that the counts match.

1. Take a checkpoint of a non-production environment (`vra backup --env dev`) and write an approval
   record for `restore:<checkpoint id>` (scope `[restore]`), even if the environment does not require
   one; a passed approval is always verified and recorded.
2. Stop writes and confirm it on the command, unless the deployed VAMS publishes maintenance mode:
   `vra restore --env dev --checkpoint <id> --approval restore_<id> --writes-stopped`.
3. Read the run: `restore.start`, `restore.verify` (item counts), `restore.verify-buckets` (object
   sets), `restore.settle` (rows VAMS wrote for the rolled-back objects), `restore.cleanup`, and the
   report's "Write freeze" section.
4. Run `vra test --env dev`, and keep both reports as the drill record. Repeat with the deploy role from
   `vra-deploy-role.yaml` (`EnableRestore=true`) rather than an administrator role, so the drill also
   proves the IAM grants.

## Emergency production change

An emergency change still goes through `vra`, so it is recorded, checkpointed and reversible.

1. Make the fix on a `hotfix/*` branch from the trunk tag running in production, and merge it into the
   trunk through an expedited review.
2. Build it: `vra build --lane release --ref <merge commit>`.
3. Deploy to `dev` and run the suites, even if briefly.
4. Have the approver write the record with the emergency ticket id and `scope: [promote, emergency]`.
5. Promote through `test` if at all possible. If the change window blocks production:
   `vra promote --from test --to prod --build <id> --approval <record> --emergency`.
6. Follow up with a post-incident review, and contribute the fix upstream if it is generic.

`--emergency` bypasses only the change window, never the approval or separation-of-duties checks. The
window is checked again before `cdk deploy` and before migrations, so a long run that crosses the end of
the window stops there unless it is an emergency.

## Common errors

| Message                                                             | Cause and fix                                                                                                                                                                             |
| ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `env.yaml` and the overlay `config.json` name different deployments | `configName`, `baseStackName`, Region or account differ between the two; correct the one that is wrong                                                                                    |
| VAMS configuration variables in the environment stop the build      | A variable such as `STACK_NAME` or `AWS_USE_WAF` is set in the shell; unset it, or record it in `env.yaml` `synthEnv`                                                                     |
| `vra history verify` reports an edited or unsynced record           | A history file was changed by hand or not pushed; restore it from the home's git history and `vra home sync`                                                                              |
| Setup is missing required values                                    | `--non-interactive` with an incomplete answers file; add the listed keys                                                                                                                  |
| `<root> is already a vra home`                                      | Setup on an existing home; use `vra setup --join <path>`                                                                                                                                  |
| The source identity trust check fails                               | The CDK bootstrap roles do not trust `sts:SetSourceIdentity`; patch the bootstrap template ([Prerequisites](prerequisites.md))                                                            |
| `cdk synth for <env> needs AWS context lookups`                     | A gated environment without `synthRoleArn` or `readOnlyRoleArn` synthesizes with no credentials; set one, or commit the lookups in `infra/cdk.context.json`                               |
| The synth role of `<env>` could not be assumed                      | Checked before synth by `vra build` and `vra iam plan`; with `setSourceIdentity` on (default for gated environments) the role's trust must allow `sts:SetSourceIdentity`                  |
| A human-only action is refused (`result.humanOnly`)                 | An agent or a job without a terminal ran it; run it yourself at a terminal, or pass `--human-approval <record>` (`vra iam apply`: its own `--approval`, the record bound to the response) |
| An MCP tool is not listed                                           | Its tier is off on the server; enable `VRA_MCP_ENABLE_WRITES` / `VRA_MCP_ENABLE_GATED` where intended, or run the CLI yourself                                                            |
| Cursor asks before every `vra` MCP call, or denies another server   | The guard asks for `vra` tools it does not know and denies other servers; update the adapter, or list the server in `VRA_GUARD_MCP_ALLOW`                                                 |
| `no SSM resource names under <prefix>` at the backup checkpoint     | The core stack exists but publishes no resource names; check its status. Since 0.5.0 a stack whose first create failed (`ROLLBACK_COMPLETE`) is treated as a first install and re-created |

## Delivery metrics

`vra report` computes these per environment from the last 30 days of history ("Environment metrics" in
`report.md`, `metrics` in `report.json`), following the definitions of the DORA research program.

| Metric               | How `vra` measures it                                                                          |
| -------------------- | ---------------------------------------------------------------------------------------------- |
| Deployment frequency | Deploys per environment in the window                                                          |
| Change failure rate  | Failed deploys divided by deploys                                                              |
| Time to restore      | Minutes from a failed deploy to the next successful deploy or rollback of the same environment |

The report also shows the current build and VAMS version per environment and open IT requests. Lead
time and upstream lag are not computed; derive them from the release tags and `vra history show`.

## Routine maintenance

| Task                                                            | Frequency                   |
| --------------------------------------------------------------- | --------------------------- |
| [Restore drill](#restore-drill)                                 | Quarterly                   |
| `vra history verify`                                            | Weekly, and before an audit |
| Review `core-patches.md` and plan removals                      | Each upgrade                |
| Review `approvers` lists and push access to the home repository | Quarterly                   |
| Prune old build bundles and run directories                     | Per your retention policy   |
| `vra preflight --all`                                           | After any IAM or SCP change |

## Related pages

-   [Shared home](shared-home.md)
-   [Command reference: exit codes](command-reference.md#exit-codes)
-   [Productionizing VAMS: disaster recovery](../productionizing/disaster-recovery.md)
