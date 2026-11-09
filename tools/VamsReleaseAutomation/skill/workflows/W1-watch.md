# W1 — Upstream watch (daily cron)

Purpose: detect a new VAMS release (tag on upstream `main`) or new commits on
upstream `development`, and push them through the auto-deploy lanes.

-   Trigger: schedule (EventBridge / cron) or the human asks "check upstream".
-   Gate: none. `vra watch` only acts on environments with `autoDeploy: true`
    and can never reach an approval-gated environment.
-   Inputs: the shared home (ledger and state).
-   Outputs: run state, sync/merge/build/deploy/test results for the auto
    lanes, a report.

The recommended cron runs `vra watch` WITHOUT an agent; the agent is invoked
only when the exit code is non-zero. When you are the cron entry point, follow
the same steps.

## Steps

1. Call `status` with `all_runs` (every machine that uses the home). If a run
   for any auto-deploy environment is `RUNNING`, `BLOCKED_ON_IT` or
   `NEEDS_HUMAN`, or another machine holds the `watch` lock, do not start
   another one: report the open run and stop. (The env and watch locks
   enforce this too; a second watch exits `20`.)
2. Run `vra watch --non-interactive --json`.
3. Branch on the exit code:
    - `0`: nothing new, or the auto lanes went green. Go to step 4.
    - `10`: `W9-permission-loop.md` (in cron: write the report, notify, stop).
    - `20`: if `conflicts.json` exists for the run, follow
      `conflict-resolution.md`; in a headless run with no human, resolve only
      what that file allows without escalation, then stop with `NEEDS_HUMAN`
      for the rest.
    - `30`: `W5-test.md` triage section.
    - `40`: page a human (`vra report <run-id> --notify`), stop.
    - `1` / `2`: SKILL.md section 3.
4. Run `vra report <run-id> --notify --json` (W10). The report MUST say,
   per lane, whether a new release / preview commit was found, the build id,
   the environments touched and the outcome.
5. If a new release reached `dev` and passed, add to the report: "Build
   `<buildId>` is eligible for promotion to `test`" with the command the
   human would ask you to run (W7). You MUST NOT promote from cron.

## Stop conditions

-   Any open blocked/needs-human run on the same lane.
-   Any exit `10`, `20` (after allowed resolution), `40`.
