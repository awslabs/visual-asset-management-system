# Ledger template

A complete example of the customer-private ledger repo that `vra` reads (`--ledger`, or
`$VRA_LEDGER`). It records how each VAMS environment is configured and every deploy, rollback,
restore, data copy and approval. It never holds secrets: only Secrets Manager / SSM names.

All values are placeholders: accounts `111111111111` (tools), `222222222222` (dev + dev-preview),
`333333333333` (test), `444444444444` (prod), and `example.com` identities.

## Layout

```
vra.yaml                         project: upstream, fork, git host, trunk, tag prefix, lanes,
                                 notifications, state store, tools account
envs/
  dev/          env.yaml         release lane, autoDeploy, rollbackPolicy auto
                overlay/infra/config/config.json            full VAMS config for this env
                overlay/infra/config/custom/customConfig.json
                history.example.jsonl                       what vra appends to history.jsonl
  dev-preview/  ...              preview lane (VAMS development), autoDeploy, never promoted
  test/         ...              promotesFrom dev, requiresApproval, prod -> test copy target
  prod/         ...              promotesFrom test, requiresApproval (final), change window,
                overlay/infra/config/csp/cspAdditionalConfig.json
approvals/<env>/<id>.yaml        approval records (examples are expired on purpose); `build` is the
                                 subject: build id, restore:<checkpoint> or data-copy:<source>
path-policy.yaml                 merge rules for upstream -> fork merges
core-patches.md                  register of fork edits outside the VAMS customization lanes
core-patches/                    ledger core patches the register lists (shipped: iam-role-transform-boundary.patch,
                                 api-deployment-role-name.patch)
tests/customer.yaml              customer regression hooks (+ tests/customer/api_smoke.sh)
variants/govcloud/               the prod env for AWS GovCloud (US); how to switch every env
```

The overlay `config.json` files are generated from VAMS `origin/development`
`infra/config/config.template.commercial.json` (GovCloud variant: `config.template.govcloud.json`),
with only `env.account`, `env.region`, `app.baseStackName`, `app.adminEmailAddress`,
`app.adminUserId`, the banner text, and (prod) the CloudFront custom domain changed. `vra build`
copies everything under `overlay/` over a scratch checkout of the fork before synth, so the
upstream tree never holds environment values. An overlay file named `<path>.vra-merge.json` is
deep-merged into the fork's JSON file `<path>` instead of replacing it.

`vra iam apply` (envs with an `iam.mode` other than `standard`) writes three overlay files:
`app.iamRoleConfig` in `overlay/infra/config/config.json`, `overlay/infra/config/policy/iamRoleConfig.json`
(pre-created VAMS roles, custom bootstrap roles) and `overlay/infra/cdk.json.vra-merge.json` (the
`environments.aws` permission boundary and role name prefix). Commit them through review like any
other ledger change. The IT role packages themselves live in the shared home's `state/iam/<env>/<n>/`.

## Turn it into your ledger

1. Create an empty private repo (GitHub, GitLab, Bitbucket or CodeCommit) in your organization.
   Restrict write access: whoever can edit `envs/prod/env.yaml` `approvers` controls who may
   approve prod. Protect the default branch and require reviews.
2. Easiest path: run `vra setup` (it asks for every value and writes this layout), or copy this
   directory and edit it:
    - `vra.yaml`: project name, your fork URL and git host, `tagPrefix`, notification channel,
      state store (deploy `iam/cloudformation/vra-state-store.yaml` first for `type: s3`).
    - `envs/<env>/env.yaml`: real accounts, region, partition, role ARNs (outputs of
      `iam/cloudformation/vra-deploy-role.yaml` and `vra-data-copy-roles.yaml`), approvers, change
      window, live-test secret names. Rename, add or remove envs as needed; keep `promotesFrom`
      chains acyclic and never let a release env promote from `dev-preview`.
    - `envs/<env>/overlay/infra/config/config.json`: import your existing config with
      `vra ledger import --env <env> --from <path/to/config.json>`, or edit the generated file.
      Values VAMS fixes at first deploy (for example the admin user and email) cannot change later.
    - GovCloud or EU Sovereign: follow `variants/govcloud/README.md`.
3. Delete the examples: `approvals/*/*.yaml`, `envs/*/history.example.jsonl`, the CP-000 lines
   in `core-patches.md`. vra creates `history.jsonl` itself.
4. Validate offline, then check permissions against the real accounts:
    ```bash
    vra --ledger . ledger validate
    vra --ledger . preflight --env dev --workflow deploy
    ```
5. Commit. From then on, change config and approvers only through reviewed PRs. vra appends to
   `envs/<env>/history.jsonl` on every deploy, rollback, restore and data copy; commit those
   changes after each run so the audit trail stays versioned.

## What never goes in the ledger

Passwords, tokens, webhook URLs, private keys, AWS access keys. Put them in Secrets Manager
(names like `vra/<env>/test-user`, `vra/notifications-target`) and reference the name.
`vra ledger validate` scans for literal secrets and access key ids and fails if it finds one.
