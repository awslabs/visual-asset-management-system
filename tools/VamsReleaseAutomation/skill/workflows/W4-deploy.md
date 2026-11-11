# W4 — Deploy one environment

Purpose: deploy an existing build bundle to one environment safely.

-   Trigger: W3 for auto-deploy environments; the human for any other.
-   Gate: environments with `requiresApproval: true` need an approval record in
    `ledger/approvals/<env>/<buildId>.yaml` written by an approver listed in the env's
    `approvers` who is not the run's initiator. The engine enforces this;
    `changeWindow` blocks prod outside the window. An env with `promotesFrom`
    also needs promotion evidence (the same build deployed and tested in the
    source env), whether you call `vra deploy` or `vra promote`.
-   Inputs: env, build id, approval reference (gated envs), mode
    (`in-place` default | `ab`).
-   Outputs: deployed stacks, checkpoint id, `cdk diff` report, migration
    results, smoke result, env history record (`state/history/<env>/` in the home).

The engine runs, in order: env lock, promotion check (envs with
`promotesFrom`), gates (approval, change window; re-checked on every resume),
preflight (gated envs: also that the CDK bootstrap roles trust
`sts:SetSourceIdentity`), backup checkpoint (gated envs refuse a checkpoint with warnings
unless `backup.allowDegraded`), drift check (default on for gated envs; stops
on drift unless `--accept-drift`), data-protection gate: `cdk diff` report, or
CloudFormation change sets with nested stacks when the env sets
`diffGate: changeset` (both stop on a non-zero
`cdk diff` exit, on output it cannot parse, on replace/remove of a
data-bearing resource unless `--allow-replacements` or `--mode ab`, and flags
forward-only changes such as a Cognito user pool schema change), migration
plan (stops if the upgrade requires A/B and the mode is in-place; with
migrations planned, running VAMS workflow executions are listed and reported,
aborted only with the human-only `--abort-workflow-executions`), registered
asset bucket check (the checkpoint, data migrations and an A/B slot copy
touch bucket data: every bucket registered in VAMS and its KMS key must be
usable by the deploy role and recorded in `registeredBuckets`, else an IT
request and exit `10` before anything changes; a deploy with none of those
steps is not blocked, its report lists the IT request as pending), change
window re-check, deploy from `cdk.out/<env>`, [A/B: data copy into the new
slot], per migration: window re-check, manual-step confirmation, dry-run,
apply; post-deploy smoke (the env's required suites must run, not just pass),
[A/B: DNS switch], history record. You do not run those parts individually.

If the run parks or fails after it changed the env, the engine keeps the env
lock (`heldLocks` in `vra status`) so no other run deploys over a half-done
change. `vra resume` or a rollback releases it; only a human may run
`vra unlock`.

## Steps

1. Run `vra status --json` and `vra ledger show --env <env> --json`. Confirm
   env, account, region, lane and build with what the human asked for. A
   preview-lane build MUST only go to a preview-lane environment.
2. Gated environment only:
   a. **[HUMAN]** Confirm the human asked for this exact deploy in this session.
   b. Check `ledger/approvals/<env>/<buildId>.yaml` exists (or the id/path the human
   gives you; `--approval` takes an id, an absolute path or a path relative
   to the ledger). If not, tell the human which approver(s) must create it
   and what fields it needs (`build: <buildId>`, `env`, `approver`, `approvedAt`
   and `scope: [deploy]` (or `[promote]`) required; `expiresAt` defaults to 72 h
   after `approvedAt`; `ticket, notes` optional; in CI the approval writer
   adds `source` for the current pipeline run). You MUST NOT write it. Stop.
3. Read `data-migration.md` step 1 for the version delta. If the upgrade
   matrix marks the transition as requiring A/B, use `--mode ab` and follow
   `data-migration.md` section "A/B". Otherwise use the env's default mode.
4. Optional but RECOMMENDED for gated envs: run
   `vra deploy --env <env> --build <buildId> --approval <ref> --dry-run --json`
   and show the human the `cdk diff` summary (stack replacements, IAM
   changes, deletions, Cognito / KMS changes; the full text is
   `cdk-diff-<env>.txt` in the run directory). The engine itself stops with
   exit `20` on a replacement or deletion of a data-bearing resource, a failed
   or unparseable diff, or drift.
   **[HUMAN]** Only the human decides between `--mode ab`,
   `--allow-replacements` and `--accept-drift`. You MUST NOT add either flag;
   with `VRA_AGENT=1` the engine also wants an approval record scoped
   `allow-replacements` / `accept-drift`, which an approver writes.
5. Run `vra deploy --env <env> --build <buildId> [--approval <ref>] [--mode <mode>] --json`.
6. Branch on the exit code:
    - `0`: continue with W5 for this env.
    - `10`: `W9-permission-loop.md` (also for a registered asset bucket the
      role cannot use; the IT request names the bucket and says whether it is
      newly registered).
    - `0` with an open IT request in the output (`itRequests[]`, "pending, did
      not block this run" in the report): the deploy succeeded; hand the
      request to the human per W9 before the next backup, restore, data copy
      or migration on this env.
    - `20`: missing/expired/used approval, missing promotion evidence, change
      window, lock held, drift, diff needing review, an upgrade that requires
      A/B, or manual work. Report the reason. If the message asks for manual
      work (an upgrade-matrix manual step, or an A/B DNS switch with no
      `ab.dnsSwitchHook`): **[HUMAN]** the human does it and runs
      `vra resume <run-id> --confirm` themselves (a plain `vra resume` keeps
      waiting; the engine refuses `--confirm` from an agent). Otherwise stop.
    - `30`: post-deploy smoke failed; W5 triage, then W6 per `rollbackPolicy`.
    - `40`: page a human; stop.
7. Record the checkpoint id from the output in your report; W6 needs it.

## Stop conditions

-   No approval record for a gated env.
-   The human did not confirm a destructive diff or drift.
-   A held lock on the env (report it; the human decides on `vra unlock`).
-   Exit `10`, `20`, `40`.
