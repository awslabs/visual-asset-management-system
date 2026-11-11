# W6 — Rollback

Purpose: return an environment to its previous good build, and to its
pre-deploy data checkpoint if a migration ran.

-   Trigger: W4/W5 failure. Automatic in envs with `rollbackPolicy: auto` (the
    engine already did it); on request in envs with `rollbackPolicy: ask`.
-   Gate: a manual rollback of an approval-gated env needs an approval record
    for the target build (`--approval`), and restoring data needs one whose
    subject is `restore:<checkpointId>` (`--restore-approval`; default file
    `ledger/approvals/<env>/restore_<checkpointId>.yaml`) **[HUMAN]**. Its `scope`
    MUST include `rollback` (and `restore` for the data record). The automatic
    rollback inside a failed deploy runs under that deploy's approval.
-   Inputs: env, optional target build (default: last SUCCEEDED build in
    the env history (`vra history show --env <env>`)), optional checkpoint id.
-   Outputs: redeployed previous build, optional restored data, history line.

## Forward-only changes (read first)

Some changes cannot be undone by redeploying the previous build. If the
failed deploy's diff or migration plan included any of these, you MUST tell
the human before rolling back and MUST NOT roll back without their explicit go:

-   Cognito user pool schema changes (attributes cannot be removed or modified),
-   KMS key replacement or key-policy changes affecting existing data,
-   a data migration that already ran its apply step (code rollback alone
    leaves migrated data; restore the checkpoint or roll forward),
-   resource replacement of a data-bearing resource.

## Steps

1. Run `vra status <run-id> --json`. Confirm the env, the failed build, the
   checkpoint id recorded by W4, and whether a migration apply ran.
2. If the engine already rolled back (`ROLLED_BACK`), skip to step 6.
3. **[HUMAN]** For `rollbackPolicy: ask`, present: failed build, target build,
   whether data restore is needed and its approximate impact (writes after
   the checkpoint are lost), forward-only items. Wait for an explicit choice:
   roll back code only, roll back code + restore checkpoint, or roll forward
   (fix and redeploy).
4. Roll back:
    - code only: `vra rollback --env <env> [--to-build <B>] [--approval <A>] --json`
    - with data: `vra rollback --env <env> [--to-build <B>] --restore-checkpoint <C> [--approval <A>] [--restore-approval <R>] --json`
      (gated env: both approval records must exist first; you MUST NOT write
      them).
    - data only: `vra restore --env <env> --checkpoint <C> [--approval <R>] --json`
      (takes a `pre-restore` safety checkpoint first).
      A restore freezes writes while it clears and reloads the live tables. With
      VAMS maintenance mode (only when the deployed version publishes it) the
      engine does it alone. Otherwise the run parks with exit `20` asking a
      person to stop writes: **[HUMAN]** tell the human, who stops writes and
      runs `vra resume <run-id> --confirm` (or re-runs with `--writes-stopped`)
      themselves; you MUST NOT pass either. The engine lifts the freeze at the
      end and on failure, and lists running VAMS workflow executions first;
      report them, and stop them only if the human runs the command with
      `--abort-workflow-executions`. Report the `restore.verify` counts.
5. Branch on the exit code: `0` continue; `40` page a human now and stop;
   others per SKILL.md section 3. A successful rollback releases the env lock
   the failed run held. If `vra status` still lists the env under
   `heldLocks` and no run will resume it, tell the human; only they run
   `vra unlock --env <env> --run <run-id> --reason "<why>"` (recorded in
   history).
   A restore keeps its temporary `vra-restore-*` tables until its
   `restore.cleanup` step finishes; if a restore stops before that, report
   the tables named in the run so the human can decide.
6. Run `vra test --env <env> --json` to confirm the environment is healthy on
   the old build.
7. Run `vra report <run-id> --notify --json`.

## Stop conditions

-   Exit `40` (never attempt a second rollback yourself).
-   The human chose roll forward.
