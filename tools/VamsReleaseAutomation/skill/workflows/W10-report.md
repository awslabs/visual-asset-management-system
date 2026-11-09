# W10 — Report and notify

Purpose: leave one clear, auditable summary at the end of every run.

-   Trigger: end of every workflow, every exit code.
-   Gate: none.
-   Inputs: run id.
-   Outputs: `<runDir>/report.md` + `report.json`; notification on
    the ledger's channel when `--notify`.

## Steps

1. Run `vra report <run-id> --json` (add `--notify` for cron runs, exit `40`,
   promotions, data copies and rollbacks).
2. Read `report.md` (the report is engine-written; you do not edit files in
   the shared home). Prepare a short triage summary:
    - outcome in one sentence,
    - run id, workflow, env(s), lane, build id, upstream ref, final status, exit code,
    - for failures: class (W5 triage), evidence path, next decision and who owns it,
    - for `BLOCKED_ON_IT`: IT request number and path,
    - for `NEEDS_HUMAN`: the exact question.
3. Never include secret values, tokens, decoded authorization messages
   containing credentials, or customer data samples.
4. To the human (or, headless, as your final output), reply with at most
   about ten lines: the triage summary and the single decision needed (if
   any). Point to `report.md` for details.

For data copies the engine adds a "Data copy" section (a "Data copy (dry-run
plan)" section for `--dry-run`): table count, items cleared and written (or
approximate source/target item counts), tables not copied, and objects per
bucket. Quote those counts; do not recount.

## Metrics (from the engine)

The engine computes DORA-style metrics per environment over the last 30
days of the env history (`vra history show --env <env>`) (deploys, failed deploys, change failure rate, mean
minutes to restore, current build and VAMS version). It does not compute lead
time or upstream lag. You MAY quote them; you MUST NOT compute or alter them by hand.
