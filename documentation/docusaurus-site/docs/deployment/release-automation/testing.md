---
title: "Testing"
sidebar_position: 12
description: "The test suites vra runs, live-test sign-in, the contract for customer regression hooks, and how to choose environment configurations so tests cover what you deploy."
---

# Testing

This page describes the test suites `vra` runs, where each one runs, the contract for your own
regression tests, and how to choose environment configurations so that tests cover what you deploy.

## Suites

```bash
vra test --env dev --suites unit,synth,live-cli,live-web,customer
```

| Suite      | What runs                                                      | Where                     | When                        |
| ---------- | -------------------------------------------------------------- | ------------------------- | --------------------------- |
| `unit`     | The VAMS CI suites (table below) on the fork at the build ref  | Build host                | Every build                 |
| `synth`    | `cdk synth` for every environment of the lane with its overlay | Build host                | Every build                 |
| `security` | Automated Security Helper (ASH) scan, if enabled               | Build host                | Every build                 |
| `live-cli` | `vamscli` smoke against the deployed API                       | Build host to environment | After deploy, `vra test`    |
| `live-web` | Playwright specs in `web/e2e` against the deployed web app     | Build host to environment | `vra test`                  |
| `customer` | Your hooks from `tests/customer.yaml`                          | Build host to environment | `vra test`, per hook `envs` |

### VAMS unit suites

These mirror the upstream `.github/workflows/ci.yml` and are defined in
[`engine/vra/data/suites.yaml`](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsReleaseAutomation/engine/vra/data/suites.yaml). Check `ci.yml` at your target VAMS
version when you upgrade, because upstream adds and reorganizes jobs. Select suites with
`vra build --suites <csv>|none` or `build.unitSuites` in `vra.yaml` (`[]` = none; this is the only switch for
`vra watch` builds).

| Suite                | Commands                                                                                                                                                                                                     |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `lint`               | lockfile sync (`npm ci --dry-run` with the npm version VAMS CI pins) of the root, `web`, `infra` and documentation projects, root `npm ci`, `npm audit --omit=dev`, `npm run prettier-check`, `npm run lint` |
| `web`                | `npm ci`, `npm run typecheck`, `npx jest --ci`, `npm run build.lowmem`                                                                                                                                       |
| `infra`              | `npm ci`, `npm run typecheck`, `npx jest --ci`                                                                                                                                                               |
| `backend`            | `pip install -r requirements-dev.txt`, `pytest -n auto --dist loadfile`                                                                                                                                      |
| `vamscli`, `vamsmcp` | `pip install -e .[dev]`, `pytest` in `tools/VamsCLI` / `tools/VamsMCP`                                                                                                                                       |
| `pipelines`          | `python backendPipelines/ci/pipeline_tests.py run --group <g>` for groups 1 to 3                                                                                                                             |
| `ash`                | AWS Automated Security Helper, run by `vra build` when `build.ash: true` (a missing `ash` tool stops the build; see [Results](#results))                                                                     |

`${PYTHON}` in `suites.yaml` is `VRA_SUITE_PYTHON` (default `python3`), and the pinned npm version is
`VRA_NPM_VERSION`. The `vams-release-automation` suite runs this tool's own check, for forks that carry
it under `tools/`.

Your custom code runs inside these suites, because it lives in the VAMS fork lanes. Add unit tests for
custom handlers, routes, CDK resources and CLI commands next to the custom code, following the VAMS
developer guide.

### Sign-in for live tests

`liveTest.auth` in `env.yaml` decides how the live suites sign in:

| `type`     | Secret or setting                                                      | Use for                                                                   |
| ---------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `password` | `secret`: an AWS Secrets Manager secret with `username` and `password` | Amazon Cognito user pools with local passwords                            |
| `apiKey`   | `secret`: a secret with `userId` and `apiKey` (a VAMS API key)         | Pools with MFA, SAML or OIDC federation, or an external identity provider |
| `token`    | `userId` and `tokenCommand` (an argv list that prints a VAMS token)    | Any identity provider you can script; `expiresIn` sets the token lifetime |

```yaml
liveTest:
    apiUrl: "https://api.example.com"
    cliProfile: vra-test
    auth: { type: apiKey, secret: vra/test/live-test-api-key }
```

API keys and tokens sign in with `vamscli auth login --user-id ... --token-override-stdin`, so the
value never appears in a command line. `live-web` needs a password and is skipped (recorded in the
result) for the other types. `credentialsSecret` is accepted as the password secret. An unset
`liveTest.webUrl` is read from the deployed URL in Parameter Store
(`/<configName>-<baseStackName>-<region>/web/deployedUrl`). With `testRoleArn`, `vra test` reads
secrets and parameters with that role instead of the deploy role. The post-deploy smoke of `vra deploy`,
`vra promote` and `vra rollback` always reads them with the deploy role: it runs where only the deploy
identity is trusted (in a pipeline, the deploy job), and `vra-test-<env>` trusts only the env's test
identity.

### Live CLI smoke

The `live-cli` suite signs in through the VamsCLI profile in `liveTest.cliProfile`, then exercises read
paths: list databases, list assets, read an asset, and fetch a file download URL. It does not create or
delete data in approval-gated environments. Use a non-administrator user with access to a dedicated test
database so the smoke also exercises authorization.

The VamsCLI it runs is the fork's own, at the build's commit: `vra build` packs a wheel built from
`tools/VamsCLI` into the bundle and records its SHA-256 in the manifest (`vamscli`). The post-deploy smoke
and the `live-cli` suite install that wheel into a virtual environment of its own, after the hash matched,
so a CI job needs no VamsCLI installed and never takes one from a package index. After a rollback, the
smoke uses the wheel of the build rolled back to, or that of the build it left when the older bundle has
none. `VRA_VAMSCLI` (the path of a VamsCLI you installed) overrides it.

### Live web tests

The `live-web` suite runs the Playwright specs in `web/e2e`. They take their target and credentials
from environment variables; `vra` sets them from the ledger:

| Variable                       | Source                                        |
| ------------------------------ | --------------------------------------------- |
| `E2E_BASE_URL`                 | `liveTest.webUrl`                             |
| `E2E_USERNAME`, `E2E_PASSWORD` | the `password` secret (never written to logs) |

The upstream core specs are written to pass against any deployment and not to mutate it; read
`web/e2e/CLAUDE.md` at your VAMS version for the current variables and rules. If your edge protection
rate-limits sign-ins, the specs reuse a saved session; see that file for the refresh option.

## Customer regression hooks

Declare your own tests in `tests/customer.yaml`:

```yaml
- name: ingest-roundtrip
  cmd: ./tests/ingest_roundtrip.sh
  envs: [dev, dev-preview, test]
  timeoutMin: 30
- name: custom-viewer-render
  cmd: npx playwright test --config tests/custom-viewers.config.ts
  envs: [dev, test]
  timeoutMin: 20
```

`vra` runs each hook whose `envs` includes the target environment, from the ledger directory of the
shared home, with these environment variables:

| Variable          | Value                                         |
| ----------------- | --------------------------------------------- |
| `VRA_ENV`         | Environment name                              |
| `VRA_RUN_DIR`     | The run directory                             |
| `VRA_JUNIT_DIR`   | Directory where the hook must write JUnit XML |
| `VAMS_API_URL`    | `liveTest.apiUrl`                             |
| `VAMS_WEB_URL`    | `liveTest.webUrl`                             |
| `VAMSCLI_PROFILE` | `liveTest.cliProfile`                         |

The contract:

-   Exit code `0` means pass; any other exit code means fail.
-   Write JUnit XML to `$VRA_JUNIT_DIR`. If a hook writes none, `vra` records one test case for the hook
    from its exit code.
-   Finish within `timeoutMin` (default 30), or the hook is stopped and reported as failed (exit `124`).
-   Read credentials from AWS Secrets Manager or the `vamscli` profile; never print them.
-   In approval-gated environments, do not create, change or delete data outside a dedicated test
    database, and clean up what you create.

## Configuration matrix

VAMS behaves differently depending on its configuration template and options. A test only proves what
it ran against, so give each environment the same configuration shape as the environment it promotes
to. `test` should match `prod` in every option below; `dev` should match where cost allows.

| Dimension             | Options                                                                             | What changes                                                                          | Test focus                                                  |
| --------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Web distribution      | Amazon CloudFront (`app.useCloudFront`) or Application Load Balancer (`app.useAlb`) | Edge, TLS, CSP delivery, web bucket naming, A/B constraints                           | Web sign-in, viewers, file download through the chosen path |
| Encryption            | `app.useKmsCmkEncryption.enabled`, optional external key                            | Key policies, cross-account access, replica keys                                      | Upload, download, data copy, restore                        |
| Networking            | `app.useGlobalVpc.*`, VPC endpoints, private API endpoint                           | Lambda placement, endpoint reachability, Amazon OpenSearch Service provisioned access | All live suites from the network location real users use    |
| FIPS                  | `app.useFips`                                                                       | FIPS endpoints for the SDK and CLI                                                    | Live CLI and hooks must use FIPS endpoints where required   |
| Search                | OpenSearch Serverless, OpenSearch provisioned, or none                              | Indexing, search UI, reindex procedures                                               | Search smoke, reindex after data copy                       |
| Authentication        | Amazon Cognito, Cognito with SAML or OIDC, external OAuth provider                  | Sign-in flow, test-user provisioning                                                  | Live tests with a user from the real identity path          |
| Partition             | Commercial, AWS GovCloud (US), AWS European Sovereign Cloud                         | Service and feature availability, ARNs, endpoints                                     | Synth and deploy in the target partition                    |
| Add-ons and pipelines | `app.addons`, enabled pipelines                                                     | Extra stacks, container builds                                                        | The pipelines you actually use                              |

A practical approach for a GovCloud production that uses the ALB template:

-   `dev`: the GovCloud template with the ALB path, smaller capacity settings.
-   `dev-preview`: same as `dev`, so upstream `development` changes to the ALB path surface early.
-   `test`: identical options to `prod`, including AWS KMS customer-managed key, VPC and FIPS.
-   `prod`: the production overlay.

If you also maintain a commercial deployment with Amazon CloudFront, give it its own environment chain
or at least its own `dev` environment, because a passing ALB deployment does not prove the CloudFront
path.

## Results

JUnit XML from every suite is collected under `state/runs/<run-id>/junit/`, summarized in the run
report, and used by the promotion gate: `vra promote` (and `vra deploy` into an environment with
`promotesFrom`) refuses a build whose tests did not pass on the source environment.

Nothing executed is never a pass:

-   A test run is `SUCCEEDED` only when at least one test executed, none failed, and every required suite
    of the environment (`requiredSuites`; default `[live-cli]` on approval-gated environments and on the
    environments they promote from) has at least one passing test.
    A run where everything was skipped records `NO_TESTS`; a run without a required suite records
    `INCOMPLETE`. Both exit `30` and neither is promotion evidence. The history record keeps per-suite
    counts (`suiteCounts`), which the promotion gate checks.
-   The post-deploy smoke fails when `live-cli` is required and executed nothing.
-   Build unit suites that executed nothing (every command skipped) fail the build.
-   An enabled scanner (`build.ash`, `build.sbom`) whose tool is not installed stops the build with `20`.
    `vra build --allow-missing-scanners` builds anyway and records the waiver in the manifest; such a build
    is refused by every prod-class environment, and an agent (`VRA_AGENT=1`) cannot pass the flag.

## Related pages

-   [Workflows: test](workflows.md#test)
-   [Troubleshooting](troubleshooting.md)
