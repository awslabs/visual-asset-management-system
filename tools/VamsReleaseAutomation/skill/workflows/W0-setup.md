# W0 — Setup or join, then preflight

Purpose: create the shared home (ledger and state) or point this machine at
an existing one, and prove every environment is reachable with the
permissions each workflow needs.

-   Trigger: first run on this machine (no home configured), or the human asks
    to add an environment.
-   Gate: none (no environment is changed). `vra setup` without `--force` and
    `vra setup --join` are agent-runnable; `--force`, `vra ledger import` and
    `vra home migrate` are human-only.
-   Inputs: human answers (SKILL.md section 6); optional existing
    `config.json` / `customConfig.json` per environment.
-   Outputs: the home (`vra-home.yaml`, `ledger/`, `state/`, `artifacts/`,
    `checkpoints/`), the machine pointer `~/.config/vra/config.yaml`,
    preflight results, IT requests if any.

## Steps

1. Call `home_show` (or run `vra home show --json`). If a home is configured
   and `ledger_validate` passes, tell the human and ask whether they want to
   add or change an environment instead. Stop unless they say yes.
2. Ask whether a home already exists on another machine or in a git remote.
    - Yes: **[HUMAN]** confirm the path or URL, then run
      `vra setup --join <path-or-git-url> --json` (add `--home <dir>` to clone
      into a folder other than `~/vams-release-home`, `--branch <b>` for a
      branch). Report `validation` and the `cron` line it returns. Continue
      at step 10.
    - A ledger from a vra version without a shared home: the human migrates it
      with `vra home migrate --from <old ledger> --old-work-dir <old .vra> --to <new home>`
      (human-only). Continue at step 9 once they have.
    - No: continue.
3. Ask the setup questions in the order of SKILL.md section 6. You MUST NOT
   ask for or accept secret values; ask for Secrets Manager / SSM names. You
   MUST NOT invent account ids or role ARNs.
   For each environment you MUST ask the registered-bucket question: "Have
   the vra roles of `<env>` (deploy role, data-copy role) been granted access
   to the asset buckets registered in VAMS that are not named after the core
   stack (customer-named buckets, buckets in other accounts), and to their
   KMS keys?" Default no. If yes, ask for the bucket ARN patterns and the KMS
   key ARNs and write them under `envs.<env>.registeredBuckets` (`granted`,
   `bucketArns`, `kmsKeyArns`). You MUST NOT invent bucket names or answer yes
   on the human's behalf. Tell the human that with `no`, the first backup,
   restore, data copy or deploy with data migrations stops with an IT
   request listing the exact actions per bucket and key (exit 10).
4. If the human is unsure about branches or topology, explain the model with
   `branch-strategy-advisor.md` before continuing.
5. Write the answers to a YAML file outside the home (for example
   `~/.cache/vra/setup-answers.yaml`), with the home under `home.path` and
   `home.sync.type` (`home.sync.remote` for git, `home.sync.pull` and
   `home.sync.push` for command). Recommend for production use: one AWS
   account per environment plus an optional tools account; single-account
   installs are allowed.
6. **[HUMAN]** Show the answers file and ask the human to confirm it. Do not
   continue without an explicit yes.
7. Run `vra setup --answers <answers file> --non-interactive --json`. Branch
   on the exit code (SKILL.md section 3). A `2` naming an existing home means
   step 2 applies (join it).
8. For each environment with an existing config, hand the human the import
   command; it is human-only: `vra ledger import --env <env> --from <path-to-config.json>`.
   They run it themselves, review the overlay and commit it.
9. Call `ledger_validate`. Fix only problems the output names, by re-asking
   the human for the value and having them correct the ledger; then re-run.
10. For each environment call `preflight` (`env`, all workflows). On exit
    `10`, follow `W9-permission-loop.md`; preflight gaps in different
    environments MAY be batched into one hand-off to the human.
11. Call `ledger_show` per environment and check `tier`,
    `requiresApproval` and `requiredSuites` with the human; call
    `ledger_graph` and confirm the promotion topology.
12. Run `vra ledger upgrade-config --env <env> --to-ref <current vendor ref> --json`
    for each environment (report only) and walk the human through any new
    keys (`config-upgrade.md`).
13. Tell the human how the home is shared: with `sync.type: git`, the engine
    publishes state itself, and every ledger change (env settings, approval
    records) is their commit and push. You MUST NOT commit or push the home.
    Read `home.published` from setup's result: when it is `false`, tell the
    human `home.reason` and hand them `home.publishCommand` (the home's first
    push is theirs); never say the home is shared before it is `true`.
14. Call `report` and summarize (W10).

## Stop conditions

-   The human declines to confirm the answers file or the join target.
-   Any preflight returns `10` (park until IT confirms).
-   `vra setup` returns `2` twice.
