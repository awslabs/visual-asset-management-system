# it-roles/ — inputs of the IT role package

`vra iam plan --env <env>` writes the IT role package of a strict-IAM environment into the shared
home (`state/iam/<env>/<n>/`). It is generated from the VAMS code the environment is built from,
never written by hand. Its inputs:

| Input                      | Where it comes from                                                                                                                                                                                                                                                       |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `iam-policy-report.json`   | A `cdk synth` of the fork with `app.iamRoleConfig.useCustomVamsStackRoles` and `vamsStacks.generateReportOnly` forced on (VAMS `infra/lib/helper/iamRoleCustomization.ts`). One entry per role: construct path, trust policy, managed policies, identity statements       |
| the synthesized templates  | The same synth: resource types and literal names behind the report's `(<construct path>.Arn)` references, and which functions back CloudFormation custom resources (deploy-time roles)                                                                                    |
| `cdk-bootstrap-roles.json` | The CDK bootstrap roles (trust, policies) of the CDK CLI the fork pins, used for `bootstrap` mode when the synth's own `node_modules/aws-cdk` is not available (`--report`). Refresh with `iam/scripts/extract_cdk_bootstrap_roles.py`; the engine copy is byte-identical |
| `../actions/*.yaml`        | The roles vra itself runs as, listed in every package (deploy, synth, test, data-copy and tools identities)                                                                                                                                                               |

The package's `it-roles.yaml` is optional for IT to deploy: it creates every role with the
`RolePrefix` and `PermissionsBoundaryArn` parameters, inline policies of at most 10,240
characters (larger ones split into managed policies), and trust conditions that name a VAMS
resource created later (the Cognito identity pool) as parameters that match nothing until IT
sets them after the first deploy.

Resource ARNs a role needs on resources VAMS has not created yet are written as the name
pattern CloudFormation will use (the core stack name, lower case for buckets), or exactly when
the template names the resource. References the report leaves as unresolved CDK tokens are
scoped by the core stack name for DynamoDB tables and SNS topics, and to the service in the
env's account and region otherwise; `IT-ROLES.md` lists each one so IT can narrow it.

`IT-ROLES.md` also lists every broad grant carried over from VAMS (statements on `Resource: "*"` and
wildcard actions) with its role and VAMS source, so IT can review them. In `precreated` mode the
`iam:PassRole` grants name exactly the roles VAMS passes, by their suggested IT names.

IT answers in `it-response.yaml` (optional `itTicket`, recorded in history). On an approval-gated
environment the approval record of `vra iam apply` has the subject
`iam-apply:<packageId>:<first 16 hex characters of the response's SHA-256>`, so it approves that exact
response; `vra --dry-run iam apply` prints the SHA-256 and the subject. The apply writes the overlay,
`applied.json` and a create-only `iam-apply` history record (package, response SHA-256, overlay hashes);
preflight, build and deploy refuse an `applied.json` that no such record matches. With a `boundary`
mode the build checks that every role carries the boundary from env.yaml `iam.boundaryArn`, else from
IT's response or the applied overlay, and stops when none is known.
