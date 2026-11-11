# W7 — Promote dev -> test -> prod

Purpose: move the SAME build bundle to the next environment, with an approval
at each step and a change record.

-   Trigger: the human asks to promote. Never cron.
-   Gate: approval record for the target env; prod is the final approval. The
    engine refuses preview-lane builds, builds without a SUCCEEDED deploy
    followed by a SUCCEEDED `vra test` on the `promotesFrom` env, and builds
    whose commit is not on the fork trunk (the sync PR must be merged with a
    merge commit, not squash or rebase). The test record must show that the
    source env's required suites (`requiredSuites`, default `live-cli` for
    gated envs and the envs that feed them) actually ran: a run where every
    test was skipped is `NO_TESTS`, not a pass, and records written by
    `vra` < 0.2.0 carry no suite counts and do not count. The same check runs
    when someone calls `vra deploy` on the target directly.
-   Inputs: source env, target env, build id, approval reference.
-   Outputs: deployed target env, test results, release notes you write for
    the change record (the engine does not generate them).

## Steps

1. Run `vra status --json` and `vra ledger show --env <target> --json`.
   Confirm `promotesFrom` of the target equals the source env. If it does
   not, stop and explain the promotion chain.
2. Confirm the build SUCCEEDED (deploy, then `vra test`) on the source env;
   quote the run ids. If no test run followed the deploy, run
   `vra test --env <src> --json` first. Confirm the sync PR that produced the
   build is merged into the trunk; if not, tell the human the engine will
   refuse the promotion until it is merged with a merge commit. If test UAT on `test` is part of the customer's process, ask the
   human whether UAT is accepted **[HUMAN]** before promoting test -> prod.
3. **[HUMAN]** Confirm the human asked for this promotion in this session.
4. Check `ledger/approvals/<target>/<buildId>.yaml`. If missing, tell the human which
   approver must create it (approver MUST differ from the initiator; `scope`
   MUST include `promote`; it expires after the env's `approvalMaxAgeHours`
   unless `expiresAt` says otherwise). An approval is single use: a record
   already consumed by another run is refused, so a retry after a failed
   promotion needs a new record. You MUST NOT write it. Stop.
5. Run the data-migration check (`data-migration.md` step 1) for the target's
   current version vs the build's version; a promotion can cross more
   versions than the source env did.
6. RECOMMENDED: `vra promote --from <src> --to <target> --build <buildId> --approval <ref> --dry-run --json`
   and show the `cdk diff` summary, as in W4 step 4.
7. Run `vra promote --from <src> --to <target> --build <buildId> --approval <ref> --json`.
8. Branch on the exit code (SKILL.md section 3). Exit `30` in prod with
   `rollbackPolicy: ask` goes to W6 with the human.
9. Run `vra test --env <target> --json` (promote runs only the post-deploy
   smoke), then `vra report <run-id> --notify --json`.
10. Write release notes for the change record and give them to the human:
    upstream VAMS version and tag, customer build tag, core patches carried,
    config keys changed, migrations applied, test summary. Quote facts from
    the report; do not invent them.

## Stop conditions

-   Promotion chain mismatch, source not SUCCEEDED, required suites not run,
    no approval (or an expired or already used one), UAT not accepted.
