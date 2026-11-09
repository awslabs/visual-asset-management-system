---
title: "IAM governance"
sidebar_position: 7
description: "Govern the IAM roles of a production VAMS deployment: VAMS IAM modes, permission boundaries and name prefixes, IT-created roles, CDK bootstrap roles, reviews on every upgrade, and verification."
---

# IAM governance

A VAMS deployment creates many IAM roles: one per AWS Lambda function, roles for the API, search,
pipelines, custom resources and the CDK bootstrap. In a production account those roles are part of your
security boundary. This page describes how to keep them under your organization's control and review
them on every change.

## Choose who creates the roles

VAMS supports these IAM setups through its IAM role customization (see the
[configuration reference](../configuration-reference.md)). They can be combined.

| Mode                           | How it works                                                                                              | Fits when                                                                  |
| ------------------------------ | --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| VAMS creates its roles         | Each deploy creates and updates the roles CloudFormation needs                                            | A dedicated account whose deploy role may create roles                     |
| Permission boundary and prefix | VAMS creates the roles under a boundary and a name prefix your IT team defines                            | IT allows role creation only under a boundary (for example through an SCP) |
| Pre-created roles              | IT creates every role before the deploy; VAMS references them by name and creates none                    | Only IT may create IAM roles                                               |
| Custom bootstrap roles         | IT provides the CDK bootstrap roles (deploy, CloudFormation execution, lookup, asset publishing), or none | The CDK bootstrap template is not allowed as is                            |

:::warning[Permission boundary mode needs a fork fix]
In VAMS versions affected by GitHub issue #431 the `IamRoleTransform` aspect replaces the configured
permission boundary with `AWS::NoValue` on every role, so a boundary set in `cdk.json` never reaches a
role. Until VAMS fixes it, apply the core patch shipped with release automation
(`ledger-template/core-patches/iam-role-transform-boundary.patch`) or use pre-created roles. Check the
synthesized templates: every `AWS::IAM::Role` must carry the boundary before it is deployed.
:::

## Review the role set on every change

The set of roles changes with VAMS releases and with configuration: enabling a pipeline, a search
backend or a feature adds roles. Treat the role set as a reviewed artifact:

-   Generate the full list from a synth before the first deploy. With
    `app.iamRoleConfig.useCustomVamsStackRoles` and `generateReportOnly: true`, VAMS writes an IAM policy
    report into `cdk.out` that lists every role, its trust and its policies. Never deploy that assembly:
    its functions reference roles without a name.
-   Review the difference to the previous release before each upgrade, not the full list again.
-   Keep the reviewed list, the policies and the approval in your change records.

[Release automation](../release-automation/iam-for-strict-environments.md) automates this loop: `vra iam
plan` writes the role package with a delta to the previous one, `vra iam apply` (a person) wires IT's
answer into the environment's configuration, `vra iam verify` checks the roles read-only, and every
build stops while the roles it needs differ from what IT provided.

## Boundaries and names

-   Give every VAMS role a boundary that allows the union of the actions VAMS needs, and nothing outside
    the environment's resources. Allow `iam:PassRole` between VAMS roles on the environment's role name
    pattern: the pipeline service passes the pipeline execution role.
-   VAMS names every role explicitly (`<prefix><construct name>-<region>`), so deploys need
    `CAPABILITY_NAMED_IAM` and two deployments in one Region and account need different base stack names.
-   Pre-created role names are plain names; VAMS builds role ARNs without an IAM path.
-   The Amazon Cognito unauthenticated role trusts the identity pool VAMS creates. Its trust condition can
    only be completed after the first deploy; plan for that step.

## Separate who changes roles from who deploys

-   The identity that deploys VAMS should not also be able to change its own permissions. With pre-created
    roles, the deploy role needs no `iam:CreateRole` at all.
-   Record who answered each role request and who wired it into the configuration. In release
    automation, `vra iam apply` is human-only and needs an approval record on approval-gated
    environments; the history records each apply.
-   Verify after each change: role exists, carries the boundary, trusts only the intended principals, and
    allows what the deployment needs (`iam:SimulatePrincipalPolicy`).

## Related pages

-   [IAM for strict environments](../release-automation/iam-for-strict-environments.md)
-   [Environment strategy](environment-strategy.md)
-   [Change management](change-management.md)
-   [Security architecture](../../architecture/security.md)
