---
title: "Prerequisites"
sidebar_position: 2
description: "What release automation needs before setup: operator software, the fork, AWS accounts, the CDK bootstrap with a scoped execution policy, the vra roles and secrets."
---

# Prerequisites

Complete these before you run `vra setup`. Setup's preflight reports any permission that is still
missing as an IT request, so an account team can grant it before the first deploy. The IAM templates are
in [`iam/`](https://github.com/awslabs/visual-asset-management-system/tree/main/tools/VamsReleaseAutomation/iam);
they are partition-generic and work in the commercial partition, AWS GovCloud (US) and the AWS European
Sovereign Cloud.

## Operator software

| Software             | Notes                                                                           |
| -------------------- | ------------------------------------------------------------------------------- |
| Python 3.12 or later | For `vra`, in its own virtual environment; see [Installation](installation.md)  |
| Git                  | With credentials for the fork repository and, for git sync, the home repository |
| Node.js and npm      | At the versions VAMS requires ([Prerequisites](../prerequisites.md))            |
| Docker               | Required by VAMS synth for container-built AWS Lambda functions and pipelines   |
| AWS CLI v2           | For credentials and the account setup below; `vra` calls AWS through the SDK    |
| `gh` or `glab`       | For pull requests on GitHub or GitLab (`githost.type`)                          |
| An agent (optional)  | Codex, Kiro, Claude Code or Cursor ([Agent systems](agent-systems.md))          |
| Disk                 | Several GB per build bundle in the shared home's `artifacts/`                   |

## The fork

Create the fork repository, the vendor mirrors, the trunk and the branch protection as described in
[Branch strategy: set up the fork](branch-strategy.md#set-up-the-fork). For several machines, also create a
private repository for the shared home ([Shared home](shared-home.md#git)).

## AWS accounts

1. **Decide the topology.** One account per environment is recommended, plus an optional tools account for
   the CI pipeline and the optional Amazon S3 state backend. See
   [Environments and topologies: accounts](environments-and-topologies.md#accounts) and
   [Productionizing VAMS: environment strategy](../productionizing/environment-strategy.md).
2. **Enable the Regions** you deploy to, including opt-in Regions. List every Region a VAMS stack of the
   account lives in: `us-east-1` for the Amazon CloudFront WAF stack when CloudFront and WAF are on, and
   the Region of every external asset bucket.

Per environment account, in this order:

| Step | Template or command                                                                                                                                                                 | Result                                                                                                                                                                       |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | [`vra-cdk-execution-policy.yaml`](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsReleaseAutomation/iam/cloudformation/vra-cdk-execution-policy.yaml) | The scoped AWS CloudFormation execution policy and its mandatory permissions boundary, with the env's `configName` and `iam.rolePrefix` as `ConfigName` and `RoleNamePrefix` |
| 2    | `cdk bootstrap` (below)                                                                                                                                                             | The CDK bootstrap roles, using that policy                                                                                                                                   |
| 3    | [`vra-deploy-role.yaml`](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsReleaseAutomation/iam/cloudformation/vra-deploy-role.yaml)                   | `vra-deploy-<env>` (and optionally `vra-synth-<env>`, `vra-test-<env>`); record its ARN as `deployRoleArn`                                                                   |
| 4    | [`vra-data-copy-roles.yaml`](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsReleaseAutomation/iam/cloudformation/vra-data-copy-roles.yaml)           | Only for [data copy](data-copy.md): `Side=target` in the target account first, then `Side=source`                                                                            |

Tools account: [`vra-state-store.yaml`](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsReleaseAutomation/iam/cloudformation/vra-state-store.yaml)
only with `stateStore.type: s3`, then the optional CI pipeline ([CI/CD and AWS CodePipeline](codepipeline.md)).

## CDK bootstrap

VAMS deploys through the standard AWS CDK bootstrap roles. Bootstrap every environment account and every
Region it deploys into once, as an account administrator, with the scoped execution policy instead of
`AdministratorAccess`:

```bash
PARTITION=$(aws sts get-caller-identity --query Arn --output text | cut -d: -f2)
ACCOUNT=222222222222                 # environment account (placeholder)
REGION=us-east-1
POLICY="arn:${PARTITION}:iam::${ACCOUNT}:policy/vra-cdk-execution"
npx cdk bootstrap --show-template > bootstrap-template.yaml
python3 tools/VamsReleaseAutomation/iam/scripts/patch-bootstrap-template.py bootstrap-template.yaml > bootstrap-template.vra.yaml
npx cdk bootstrap "aws://${ACCOUNT}/${REGION}" --template bootstrap-template.vra.yaml \
  --cloudformation-execution-policies "${POLICY}"
```

-   **Trust model.** The recommended model is a hop through `vra-deploy-<env>`: the orchestrator assumes
    the deploy role in the environment account, which assumes the CDK roles, so the bootstrap needs no
    `--trust`. Preflight simulates that role, and it carries its own boundary and session length.
-   **Source identity.** The patch adds `sts:SetSourceIdentity` to the bootstrap roles' trust, which gated
    environments need because their sessions carry the initiator as source identity. Re-run it whenever the
    CDK CLI's bootstrap template changes.
-   **VAMS roles only.** The scoped execution policy creates, changes, deletes and passes only roles named as
    VAMS names them (`<CoreRoleNameStem>...` for the core stack, `<ConfigName>-...` for roles CloudFormation
    names, `<RoleNamePrefix>...`), never the CDK bootstrap or `vra-*` roles, so a template cannot reach the
    account's other roles. `CoreRoleNameStem` is the config name without its non-alphanumeric characters plus
    `core`, cut to 16 characters; `vra iam plan` computes it. `iam.mode: precreated` requires `iam.rolePrefix`.
    Environments that share an account share this policy: give them the same `configName` and
    `iam.rolePrefix`.
-   **Permissions boundary.** The scoped execution policy refuses to create a role without its boundary.
    Set the boundary's name as `permissionsBoundaryName` in every environment of that account; `vra build`
    applies it to every role VAMS synthesizes and checks the synthesized templates. VAMS v2.6.6 and
    earlier, whose `IamRoleTransform` drops the boundary
    ([GitHub issue #431](https://github.com/awslabs/visual-asset-management-system/issues/431), fixed in
    v2.6.7), need the boundary core patch CP-431 in the ledger
    ([IAM for strict environments](iam-for-strict-environments.md#permission-boundary-mode-and-github-issue-431)).
-   **Custom bootstrap roles.** The default bootstrap uses one CDK qualifier per ledger (`vra.yaml`
    `cdk.qualifier`). When IT provides the bootstrap roles, or none at all (the CLI-credentials
    synthesizer), set `iam.mode: bootstrap` for the environment; `vra iam plan` lists the roles and
    `vra iam apply` wires their ARNs and the qualifier into the VAMS configuration.

The full guide, with the trade-offs of the scoped policy, is
[`cdk-bootstrap.md`](https://github.com/awslabs/visual-asset-management-system/blob/main/tools/VamsReleaseAutomation/iam/cdk-bootstrap.md).

:::warning[One account is one trust boundary]
Every VAMS stack in an account shares that account's CDK bootstrap roles and execution policy, and every
`vra-deploy-<env>` role in it can assume them. A deploy role can therefore change any VAMS stack in its
account. Put environments that must be isolated from each other in separate accounts.
:::

## The deploy role

`vra-deploy-role.yaml` creates `vra-deploy-<env>`, the role `vra` assumes for deploy, backup, restore,
migrate, test and preflight. Its main parameters:

| Parameter                                                     | Meaning                                                                                                  |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `TrustedPrincipalArns`, `ExternalId`                          | Who may assume it: the operators' role or exactly one CI tier role for this environment                  |
| `EnvTier`, `RequireSourceIdentity`                            | `dev`, `test` or `prod`; `auto` requires `sts:SourceIdentity` for test and prod                          |
| `CreateSynthRole`, `SynthTrustedPrincipalArns`                | A build-only `vra-synth-<env>` that can only assume the CDK lookup role; set it as `synthRoleArn`        |
| `CreateTestRole`, `TestTrustedPrincipalArns`                  | A live-test-only `vra-test-<env>`; set it as `testRoleArn`                                               |
| `EnableRestore`, `EnableChangeSetGate`, `EnableWorkflowAbort` | Optional policies for restore (with the maintenance-mode switch), the change-set gate and workflow abort |
| `AdditionalBucketArns`, `AdditionalKmsKeyArns`                | Registered asset buckets not named after the core stack, and their keys                                  |
| `ExternalTableKmsKeyArn`                                      | An external key VAMS tables are encrypted with                                                           |

Never add a build or watch identity to a gated environment's `TrustedPrincipalArns`.

## Secrets

Create AWS Secrets Manager secrets for the notification target (if any) and each environment's live-test
sign-in (a user and password, or a VAMS API key), under `vra/<baseStackName>/` so the deploy and test
roles can read them. The ledger records only their names.

## Permissions without your own roles

If your accounts need IT to create roles or grant actions, run setup anyway: preflight writes an IT
request for every gap, with the exact actions, resources and a least-privilege policy. See
[Permissions and IT requests](permissions-and-it-requests.md). When IT also creates the roles VAMS
creates for itself, or requires a boundary and name prefix on them, see
[IAM for strict environments](iam-for-strict-environments.md).

## Related pages

-   [Installation](installation.md)
-   [Setup and joining](setup.md)
-   [Permissions and IT requests](permissions-and-it-requests.md)
-   [Deploy the solution](../deploy-the-solution.md)
