---
title: "Identity"
sidebar_position: 6
description: "How VAMS user identities are stored, what can and cannot be exported or restored, and how external identity providers change recovery."
---

# Identity

VAMS authenticates users through Amazon Cognito (optionally federated with SAML or OIDC) or through an
external OAuth identity provider. VAMS roles and permission constraints are stored in Amazon DynamoDB and
are backed up with the other tables. This page describes how to protect the identity layer and why an
external identity provider is recommended for production.

## What lives where

| Data                                                    | Store                    | Backed up with VAMS tables  |
| ------------------------------------------------------- | ------------------------ | --------------------------- |
| VAMS roles, constraints and user-role assignments       | Amazon DynamoDB          | Yes                         |
| User accounts, passwords, MFA settings (Amazon Cognito) | Amazon Cognito user pool | No                          |
| User accounts and credentials (external provider)       | Your identity provider   | No; managed by the provider |

## Amazon Cognito user data

Amazon Cognito has no built-in backup and restore for a user pool. Plan for these constraints:

-   **Passwords and MFA secrets cannot be exported.** User attributes can be listed with `ListUsers`, but
    password hashes and MFA secrets are not returned.
-   **Imported users must reset their password** unless you supply password hashes from a compatible source.
    When users are imported from a CSV file without hashes, they start in the `RESET_REQUIRED` state and set a
    new password at first sign-in. See [Importing users into a user pool](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-using-import-tool.html).
-   **Schema attributes are permanent.** A custom attribute cannot be removed or changed once the pool has it,
    so a change that needs a different schema means a new pool.
-   **A new pool means new user identifiers.** If the user pool is replaced, check that VAMS user-role
    assignments still reference the identifiers users sign in with.

Export user attributes regularly so that you can recreate accounts after the loss of a pool:

```bash
aws cognito-idp list-users --user-pool-id <USER_POOL_ID> --output json > cognito-users.json
```

`list-users` returns results in pages; the AWS CLI follows the pages automatically. Store the export as
sensitive data, because it contains personal information.

Restrict `cognito-idp:DeleteUserPool` in the production account with a service control policy (see
[Environment strategy](environment-strategy.md)). Amazon Cognito also offers
[deletion protection](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-deletion-protection.html)
on a user pool. The user pool is managed by AWS CloudFormation, so check the `DeletionProtection` property in
the synthesized template; a setting changed outside AWS CloudFormation is drift and can be reset by a later
deployment.

## External identity provider for production

For production, federate sign-in to your organization's identity provider, either through Amazon Cognito SAML or
OIDC federation or through the external OAuth provider option. User accounts, credentials, MFA and
lifecycle (joiners, movers and leavers) then stay in a system that your organization already backs up,
audits and operates. A lost or replaced Amazon Cognito user pool can be recreated with the same federation
settings, and users sign in again without password resets.

The same provider can serve the test environment, which also lets user-role assignments copied from
production keep working in test. See [Security architecture](../../architecture/security.md) for the
federation options and their configuration.

## Related pages

-   [Disaster recovery](disaster-recovery.md)
-   [Retention](retention.md)
