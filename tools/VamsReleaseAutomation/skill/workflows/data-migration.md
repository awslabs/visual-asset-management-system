# Data migration (version delta)

Purpose: run VAMS's `infra/deploymentDataMigration/v<A>_to_v<B>` scripts for
every version an environment crosses, with a checkpoint first and a restore
path, and choose A/B when the upgrade matrix requires it.

-   Trigger: W2 step 7 (note), W4 step 3, W7 step 5, or the human asks.
-   Gate: applying in an approval-gated env happens inside the gated deploy
    (`vra deploy` / `vra promote`); a standalone `vra migrate` apply in a gated
    env needs the human's explicit go **[HUMAN]**.
-   Inputs: env, current version (ledger history, or the deployed core stack for
    an imported env), target version (build manifest or source tree).
-   Outputs: migration plan, dry-run output, checkpoint id, apply results.

## Steps

1. Run `vra migrate --env <env> --build <buildId> --dry-run --json` (or
   `--source <VAMS tree>/infra/deploymentDataMigration`; with neither, the
   deployed build, or for an env with no vra deploy history the newest build
   in the home with a synth for it). The engine resolves `--from` (the env
   history, or the deployed core stack's description for an imported env
   with no vra deploy history) and `--to` (the build's or source tree's VAMS
   version).
   Read `migrate.versions` in the run: each value and its source. Pass
   `--from <cur>` / `--to <target>` only when the human gives them or when the
   engine stops with exit `20` naming that flag; never guess a version.
2. Read the chain of migration directories, per-step `requiresAB`,
   `manualSteps` and `notes` from the upgrade matrix, and the dry-run output.
3. If the chain is empty, say so and continue the calling workflow.
4. If any step lists `manualSteps`, the engine stops before that migration
   (exit `20`, "has manual steps the automation cannot do"). **[HUMAN]** List
   the steps; the human does them and then runs
   `vra resume <run-id> --confirm` themselves; the engine records who
   confirmed and continues. A plain `vra resume` keeps waiting. You MUST NOT
   perform manual steps yourself with `aws` / scripts and MUST NOT pass
   `--confirm` (with `VRA_AGENT=1` the engine refuses it). `notes` are advice
   only and never stop a run.
5. If any step requires A/B, follow "A/B" below instead of an in-place deploy.
6. In-place: the checkpoint is taken by `vra deploy` (W4). For a standalone
   migration run `vra backup --env <env> --json` first and record the
   checkpoint id. Never apply without a checkpoint taken in the same run.
7. Apply via the calling workflow (`vra deploy` runs the dry runs and the
   apply after `cdk deploy`). For a standalone apply: **[HUMAN]** confirm, then
   `vra migrate --env <env> --build <buildId> --json`, with the same `--from` /
   `--to` as the dry run when they were passed.
8. On failure: do NOT re-run a partially applied migration. Report the step
   that failed and offer W6 with `--restore-checkpoint <id>` or a roll
   forward, per the human's choice.

## A/B (blue/green)

Required for some major upgrades (see the VAMS `deploymentDataMigration`
README at the target ref; the engine stops an in-place deploy when the upgrade
matrix says `requiresAB`). The engine models it as `vra deploy --mode ab`
using the env's `ab.slots` (`{blue: <baseStackName>, green: <baseStackName>}`):
the idle slot is deployed, the live slot's data copied into it, migrated,
smoke-tested, then cut over.

1. **[HUMAN]** Explain: a second full stack set is created (cost, quotas,
   new endpoints), data is copied and migrated into it, and the cutover is a
   DNS / URL switch the human controls. The old stack stays until the human
   retires it.
2. Run `vra deploy --env <env> --build <buildId> --mode ab [--approval <ref>] --json`.
   The build must include both slot synths (`<env>@blue`, `<env>@green`).
3. Cutover: if `ab.dnsSwitchHook` is set the engine runs it. Otherwise the run
   stops with exit `20` after the smoke on the new slot. **[HUMAN]** The human
   switches DNS / identity provider callback URLs, then runs
   `vra resume <run-id> --confirm` themselves, which records the switch and
   the new live slot.
4. After cutover, validate with `vra test --env <env> --json`.
5. **[HUMAN]** Retiring the old slot is a human action after the soak period;
   the engine never deletes it. You MUST NOT delete stacks.
