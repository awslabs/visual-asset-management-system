# W8 — Data copy prod -> test

Purpose: replace a lower environment's VAMS data with a full copy of a
higher environment's data, across accounts, on request.

-   Trigger: the human asks. Never cron.
-   Gate (enforced by the engine): the target MUST be allowed as a target
    (`dataCopy.allowAsTarget`; unset means prod-class envs, approval-gated with
    nothing promoting from them, are refused); the source MUST be listed in the
    target's `dataCopy.allowedSources`; an approval record filed under the
    TARGET env with subject `data-copy:<source>` is required when either env is
    approval-gated, AND an approval from the SOURCE env's data owner
    (`dataOwners`, else its `approvers`) filed under the source env with
    subject `data-copy-to:<target>` is required when the source is gated or
    has `dataOwners` **[HUMAN]**. A gated target also needs a complete
    checkpoint (the backup permissions are preflighted first).
-   Inputs: source env, target env, approval reference, source approval
    reference, optional database ids, optional mask hook (off by default).
-   Outputs: target tables and asset buckets replaced, OpenSearch reindexed,
    audit record in the run and in the target's env history.

What the engine does: before anything is written, checks that every
registered source asset bucket maps to a distinct target asset bucket
(automatic only one-to-one, else `dataCopy.bucketMap`; exit `20` naming each
unmapped bucket, nothing skipped), that no target bucket is a source bucket
or another env's bucket (exit `20`), and that both sides' roles can use
every registered bucket and its KMS key (exit `10`, IT request); then
checkpoint of the target, then (`--method export`,
default) PITR export of source tables to a staging bucket the
target can read (KMS grant), clear target tables, batch load with remapping
(bucket names, ids, SSM-resolved names, account ids in ARNs), asset copy in
two hops (the SOURCE data-copy role copies into `vra/<run>/assets/<bucket>/`
in the staging bucket, then the target role copies from staging into the
target buckets), OpenSearch reindex. Every source read uses the source
env's role, never the target's (`dataCopy.chainSourceRole: true` chains it
from the target loader role). A cross-account copy without a staging bucket
is refused. `--method scan` reads the source tables directly instead of an
export. Cognito users are NOT copied; user-role assignments are optional
(`dataCopy.copyUserRoles`). The record's `masked` field, when set, must
match whether a mask hook runs.

## Steps

1. Run `vra ledger show --env <target> --json`. Confirm the source is in
   `dataCopy.allowedSources`. If the target is `prod` or any prod-class env,
   stop: copying INTO production is out of scope for this workflow. The
   engine refuses it too (exit `20`, "data-copy refused"); you MUST NOT edit
   `dataCopy.allowAsTarget` to get around it.
2. **[HUMAN]** State plainly: "This deletes all VAMS data in `<target>`
   (account `<id>`, region `<r>`) and replaces it with a copy of `<source>`.
   Users in `<target>` keep their logins; their data is replaced." Ask the
   human to confirm, and ask whether the copy contains data that must be
   masked or subset. If yes, the customer supplies a mask hook command; you
   MUST NOT write one that the data owner has not reviewed.
3. Check for the approval record: `ledger/approvals/<target>/data-copy_<source>.yaml`
   by default, or the id/path the human gives you (pass it as
   `--approval <id|path>`). Its `build` field MUST be `data-copy:<source>` and
   its `env` the target. If missing, name the data owner (listed in the
   target's `approvers`, not the person starting the run) who must create it.
   Also check `ledger/approvals/<source>/data-copy-to_<target>.yaml` (or the
   id/path the human gives you, passed as `--source-approval <id|path>`):
   `build: data-copy-to:<target>`, `env: <source>`, an approver from the
   source's `dataOwners` (else its `approvers`), `scope: [data-copy]`.
   You MUST NOT write either record. Stop if one is missing.
4. Check that both envs run the same VAMS version (`vra ledger show`). If they
   differ, stop and propose promoting first (the engine itself refuses a
   source on a newer minor version than the target).
5. The engine checkpoints the target in its `data-copy.checkpoint` step;
   record that checkpoint id from the run for the human (undo is
   `vra restore --env <target> --checkpoint <id>`, approval subject
   `restore:<id>`).
6. Run `vra data-copy --from <source> --to <target> --approval <ref> [--source-approval <ref>] [--databases <ids>] [--mask-hook <cmd>] [--method export|scan] --dry-run --json`
   and report counts per table and per bucket from its `data-copy.plan` step (table item counts are
   approximate).
7. Run the same command without `--dry-run`. The engine lists the target's
   running VAMS workflow executions (report them) and freezes writes to the
   target before it clears its tables: with VAMS maintenance mode when the
   deployed target publishes it, otherwise the run parks with exit `20`
   until a person stops writes and confirms (**[HUMAN]**: they run
   `vra resume <run-id> --confirm` or re-run with `--writes-stopped`; you MUST
   NOT pass either, nor `--abort-workflow-executions`). The freeze is lifted
   at the end and on failure.
8. Branch on the exit code (SKILL.md section 3). On `1` mid-copy, do NOT
   start a fresh copy; run `vra resume <run-id>` once, then report.
    - `20` "unmapped source asset bucket(s)": every registered source asset
      bucket needs its own target bucket. Show the human the bucket names and
      the `dataCopy.bucketMap` snippet from the message. **[HUMAN]** The human
      decides the mapping (and registers a target bucket in VAMS if one is
      missing) and edits `envs/<target>/env.yaml`; you MUST NOT pick target
      buckets or drop a source bucket. Then `vra resume <run-id>`.
    - `20` "share bucket(s)" or "also used by <env>": a target bucket is a
      source bucket or another env's bucket. Stop and report the env named;
      there is no override and you MUST NOT edit the ledger to hide it.
    - `10` on a `data-copy.registered-buckets.*` step: a registered bucket or
      its KMS key on that side is not granted; `W9-permission-loop.md`.
9. Run `vra test --env <target> --suites live-cli --json`.
10. Report (W10): source/target, counts, checkpoint id, mask hook used or
    "none", duration.

## Stop conditions

-   Source not allowed, target is prod, versions differ, no target or source
    approval, incomplete target checkpoint, human did not confirm the
    destructive replace.
