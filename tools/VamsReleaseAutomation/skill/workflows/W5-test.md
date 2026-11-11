# W5 — Test one environment, and failure triage

Purpose: run VAMS's own suites plus the customer's regression hooks against a
deployed environment, and turn failures into a clear cause.

-   Trigger: after W4; or the human asks to (re)test an environment.
-   Gate: none. Live suites read and write test data in the environment; the
    engine scopes them to the env's `liveTest` settings.
-   Inputs: env, optional `--suites` subset (`unit,synth,live-cli,live-web,customer`).
-   Outputs: JUnit under `<runDir>/junit/`, summary.

## Steps

1. Run `vra test --env <env> --json` (all suites unless the human named a
   subset).
2. Branch on the exit code:
    - `0`: record pass counts per suite; continue (W7 eligibility, W10).
      The result is `SUCCEEDED` only when every required suite of the env
      (`requiredSuites`; default `live-cli` for gated envs and the envs that
      feed them) executed at least one test and none failed.
    - `30` with `NO_TESTS` or `INCOMPLETE`: every test was skipped, or a
      required suite did not run (missing `liveTest` settings, a filtered
      `--suites` list). That is not a pass and does not make the build
      promotable. Report which suite ran nothing; the usual fix is the
      ledger's `liveTest` block (Test harness row below).
    - `30`: go to "Triage". The engine has already rolled back when
      `rollbackPolicy: auto`; confirm in `vra status <run-id> --json`.
    - other: SKILL.md section 3.

## Triage

You MUST classify each failing test into one of these and say which in the
report, with the JUnit file and log excerpt (no secrets):

| Class               | Signals                                                                           | Action                                                                                                      |
| ------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Upstream defect     | Fails on the same build with fork lanes empty / fails in upstream CI for that ref | Report; customer decides wait vs core patch                                                                 |
| Fork regression     | Failure in a fork-lane path or a customer suite; passes on the vendor ref         | Report to the customer's developers with the failing path                                                   |
| Config drift        | Synth/runtime error naming a config key; ledger overlay older than template       | `config-upgrade.md`                                                                                         |
| Data migration      | Errors on records shaped by an older version; migration skipped/failed            | `data-migration.md`                                                                                         |
| Permission          | `AccessDenied` etc. not caught by preflight                                       | Engine should have exited `10`; if not, report as an engine gap and run W9 manually from the step log       |
| Environment / flake | Timeouts, throttling, capacity, a test that passes on re-run with no change       | `vra test --env <env> --suites <failed suite> --json` ONCE; if it passes, report as flaky with both run ids |
| Test harness        | Missing env var / credentials secret name for live suites                         | Ask the human to fix the ledger `liveTest` block                                                            |

3. You MUST NOT edit a test to make it pass, mark a test skipped, or change a
   customer suite's command in `tests/customer.yaml` without the human's
   explicit request.
4. If `rollbackPolicy: ask` and the run is `TESTS_FAILED`, follow `W6-rollback.md`.
5. Write the triage into the run report (W10).

## Stop conditions

-   A second failure of the same suite after one re-run.
