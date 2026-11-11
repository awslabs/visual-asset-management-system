# IAM fixtures: real VAMS v2.6.6 cloud assemblies

Each `vams-2.6.6-<variant>.json.gz` is a real `cdk synth` of VAMS v2.6.6 (tag `v2.6.6`, commit
`1661edfb1`, aws-cdk CLI 2.1135.0 from its `infra/package-lock.json`), reduced by
`scripts/make_iam_fixture.py` to what `vra iam` reads: the full `iam-policy-report.json`, each
template's resources as type + `aws:cdk:path` (plus role properties, literal resource names,
Lambda role links and custom-resource service tokens) and the stack artifacts of
`manifest.json`. The synth account id is replaced by the placeholder `111111111111`.
`engine/tests/iam_fixtures.py` lays a fixture out as a `cdk.out` directory again.

Synth: a detached worktree of the VAMS clone at `v2.6.6`, `npm ci` in `infra/`, a placeholder
`web/dist/index.html` (the IAM report does not depend on the web build), `infra/config/config.json`
from the named template with a dummy account id and `app.baseStackName` `prod`, and

```bash
npx cdk synth --all --quiet -o <out> -c configFile=config.json -c stack-name=prod \
  -c region=<region> -c account=<dummy account id>
```

with no AWS credentials (the GovCloud VPC's availability-zone lookup committed in
`infra/cdk.context.json`, as a gated env without synth credentials would).

| Variant                          | Template   | Region        | IAM settings                                                                                                                                                                  | Result                                                                                                     |
| -------------------------------- | ---------- | ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `commercial-report`              | commercial | us-east-1     | `useCustomVamsStackRoles`, `generateReportOnly: true`                                                                                                                         | 141 report entries: 88 roles to create, 53 references to one existing service-linked role                  |
| `govcloud-report`                | govcloud   | us-gov-west-1 | same                                                                                                                                                                          | 145 entries: 92 roles (VPC flow logs, provisioned OpenSearch, ALB web hosting), 53 references              |
| `commercial-precreated`          | commercial | us-east-1     | `generateReportOnly: false`, all 88 construct paths mapped in `precreatedRoles`                                                                                               | synth succeeds; no `AWS::IAM::Role` in any template                                                        |
| `commercial-precreated-partial`  | commercial | us-east-1     | same with one path left out                                                                                                                                                   | synth fails ("IAM Role is being created at path ... preventSynthesis"); report still written               |
| `commercial-boundary`            | commercial | us-east-1     | report only + `environments.aws` boundary and prefix `ITVAMS-`                                                                                                                | prefix applied; every role's `PermissionsBoundary` is `AWS::NoValue`                                       |
| `commercial-boundary-fixed`      | commercial | us-east-1     | same, with the aspect's boundary branch fixed (the same result as the core patch `ledger-template/core-patches/iam-role-transform-boundary.patch`, re-synthesized 2026-11-04) | every role carries the boundary ARN                                                                        |
| `commercial-cli`                 | commercial | us-east-1     | report only + `useCustomBootstrapRoles` with `useCliCredentialsSynthesizer`                                                                                                   | no bootstrap role ARNs in `manifest.json`                                                                  |
| `commercial-cdkflag-patched`     | commercial | us-east-1     | report only, the boundary core patch applied, `-c @aws-cdk/core:permissionsBoundary={"name":...}` on the command line                                                         | no role carries a boundary: the CDK CLI passes `-c` values as strings, CDK reads the key only as an object |
| `commercial-cdkboundary-patched` | commercial | us-east-1     | standard roles, the boundary core patch applied, `@aws-cdk/core:permissionsBoundary` `{"name": "it-vams-boundary"}` in `infra/cdk.json` context                               | every role carries the CDK boundary (`Fn::Join` of the partition and the policy name)                      |

`vams-2.6.6-iam-role-transform.aspect.ts` is the v2.6.6 source of
`infra/lib/aspects/iam-role-transform.aspect.ts` (identical on `development` at 2026-11-04); the core
patch tests apply `ledger-template/core-patches/iam-role-transform-boundary.patch` to it.

`vams-2.6.6-rest-api-gateway-construct.ts` and `vams-development-a9ac094c4-rest-api-gateway-construct.ts` are
the sources of `infra/lib/nestedStacks/apiLambda/constructs/rest-api-gateway-construct.ts` at v2.6.6 and at
development `a9ac094c4` (2026-11-06); the core patch tests apply
`ledger-template/core-patches/api-deployment-role-name.patch` (GitHub issue #1210) to both.
