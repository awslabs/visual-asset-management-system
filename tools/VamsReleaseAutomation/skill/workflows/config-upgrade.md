# Config upgrade (ledger vs new VAMS templates)

Purpose: when a new VAMS ref changes `infra/config/config.template.*.json`,
carry each environment's config forward without silently dropping new keys or
keeping removed ones.

-   Trigger: W2 step 6; synth failure naming a config key (W3/W5 triage);
    W0 step 12; the human asks.
-   Gate: changing a value in an approval-gated env's overlay **[HUMAN]**.
-   Inputs: env, target ref (vendor tag or sha).
-   Outputs: a proposal; after the human writes it, an updated
    `ledger/envs/<env>/overlay/infra/config/config.json` (and new keys
    in `envs/<env>/overlay/infra/config/custom/customConfig.json`) in the ledger,
    a note for the PR / report.

## Steps

1. Run `vra ledger upgrade-config --env <env> --to-ref <ref> --json` (report
   only; it writes nothing). Read `result.newKeys`, `result.newKeyDefaults`,
   `result.removedKeys` and `result.proposed` (the env's `templateBase`
   template at `<ref>` merged with the env's values, removed keys dropped).
   Also read `result.customConfig.newKeys`: new keys in upstream's
   `infra/config/custom/customConfig.json`. That file is customer-owned (path
   policy `fork`), so only new keys are proposed; customer keys are never
   removed or changed.
2. For each added key: explain what it controls (read the template and the
   upstream docs at that ref:
   `git show <ref>:documentation/docusaurus-site/docs/deployment/...` or the
   config reference page). Propose the upstream default unless the env's
   partition/template suggests otherwise (for example GovCloud or EU Sovereign
   restrictions).
3. For each removed key: say whether the env set a non-default value (the
   feature may have moved or been dropped; check the upstream CHANGELOG).
4. For each key present in both whose template default changed: say whether
   the env relies on the old default (key absent from the overlay) and
   therefore changes behaviour. The engine does not list these; compare the
   template at the env's current version with `<ref>` (`git show`).
5. **[HUMAN]** Show the proposal per env. Ask the human to accept, edit
   specific values, or defer. For approval-gated envs, the human MUST accept
   each value change explicitly.
6. Writing the proposal is human-only. Give the human the command to run
   themselves: `vra ledger upgrade-config --env <env> --to-ref <ref> --write`.
   It writes `result.proposed` to the overlay `config.json` and, when
   `result.customConfig.newKeys` is not empty, adds those keys to the overlay
   `customConfig.json` (`result.written` lists the files). Where the human
   chose a value different from `proposed`, they edit that one key in the
   overlay afterwards.
7. After the human says it is written, call `ledger_validate` and
   `ledger_diff` for the env and report the result.
8. Never put a secret value in an overlay; only Secrets Manager / SSM names.
9. The human commits (and, with git sync, pushes) the ledger change in the
   shared home; you MUST NOT commit or push it.
10. Re-run the step that failed (usually `vra build`).
