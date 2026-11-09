# W3 — Build

Purpose: produce one immutable, hash-pinned bundle per commit that every
environment of the lane will deploy.

-   Trigger: W2 finished, or the human asks to build a ref.
-   Gate: none.
-   Inputs: lane, ref (merged trunk sha / sync branch / `preview/main` sha).
-   Outputs: `<home>/artifacts/<buildId>/manifest.json`, per-env `cdk.out/<env>/`,
    JUnit, SBOM, scan results; tag `<tagPrefix>v<vams>-<n>` (release lane only).

## Steps

1. Run `vra status --json` and make sure no build for the same lane and ref
   is already `RUNNING`.
2. Run `vra build --lane <lane> --ref <ref> --json`.
3. Branch on the exit code:
    - `0`: record `buildId`, tag (release lane) and the manifest hash. The
      manifest also records the CDK CLI version (pinned from the repo's
      `package-lock.json`), the build environment, any scanner waiver and,
      with `build.signing`, the KMS signature (and, with `signing.tags`, a
      KMS-signed release tag that deploy, promote and rollback verify).
      Synth runs with build-only
      credentials (`synthRoleArn`, else `readOnlyRoleArn`); a gated env never
      lends its deploy role to synth.
    - `30`: unit, lint, synth or security-scan failure. Triage with the rules
      in `W5-test.md` section "Triage". A synth failure for one environment
      usually means a ledger overlay is stale: run `config-upgrade.md` for that
      environment. A security-scan finding is reported, never suppressed by you.
    - `20` "scanner not installed": an enabled security scanner (ASH, the
      SBOM tool) is missing on this host, and the build fails closed. Report
      it; the fix is to install the tool. Only a human may run the build with
      `--allow-missing-scanners` (the waiver is recorded in the manifest, and
      every prod-class env refuses such a build). You MUST NOT pass it.
    - `30` also covers a unit suite that executed no tests at all.
    - `10`, `20`, `40`, `1`, `2`: SKILL.md section 3.
4. You MUST NOT re-tag, delete or move a tag, or rebuild the same ref to get
   a different result. A fix is a new commit and a new build.
5. Hand off to W4 for the lane's auto-deploy environments (`dev` for release,
   `dev-preview` for preview), or report the build id to the human.

## Stop conditions

-   Exit `30` whose cause is in upstream core code (report it; the customer
    decides whether to carry a core patch or wait for upstream).
