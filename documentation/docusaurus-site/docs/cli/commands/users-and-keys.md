---
sidebar_label: Users and API Keys
title: User and API Key Commands
---

# User and API Key Commands

Manage Amazon Cognito users and create API keys for programmatic access to VAMS. The `user cognito` commands administer the Amazon Cognito user pool, while the `api-key` commands issue long-lived credentials that authenticate as a VAMS user without a JWT token.

---

## Amazon Cognito User Management

The `user cognito` command group manages users in the Amazon Cognito user pool: listing, creating, updating, deleting, and resetting passwords. Amazon Cognito handles password generation and email/phone verification.

:::note[Prerequisites]
User management commands require Amazon Cognito authentication to be enabled in your VAMS deployment, and your authenticated user must have admin permissions for user management. If Amazon Cognito is not the active authentication provider, these commands return a Cognito operation error.
:::

---

## user cognito list

List all Amazon Cognito users in the user pool, with manual or automatic pagination.

```bash
vamscli user cognito list [OPTIONS]
```

| Option             | Type    | Required | Description                                                               |
| ------------------ | ------- | -------- | ------------------------------------------------------------------------- |
| `--page-size`      | INTEGER | No       | Number of items per page                                                  |
| `--max-items`      | INTEGER | No       | Maximum total items to fetch (only with `--auto-paginate`, default 10000) |
| `--starting-token` | TEXT    | No       | Token for manual pagination                                               |
| `--auto-paginate`  | Flag    | No       | Automatically fetch all items                                             |
| `--json-output`    | Flag    | No       | Output raw JSON response                                                  |

:::note
`--auto-paginate` and `--starting-token` are mutually exclusive. Use `--auto-paginate` to fetch all pages automatically, or `--starting-token` to retrieve a single page from a prior `NextToken`. `--max-items` applies only with `--auto-paginate` and is ignored otherwise.
:::

```bash
vamscli user cognito list
vamscli user cognito list --auto-paginate
vamscli user cognito list --auto-paginate --max-items 5000
vamscli user cognito list --page-size 50 --starting-token "token123"
vamscli user cognito list --json-output | jq '.Items[] | select(.userStatus == "CONFIRMED")'
```

Each entry reports `userId`, `email`, `phone` (if set), `userStatus`, `enabled`, `mfaEnabled`, and creation/modification timestamps.

---

## user cognito create

Create a new user. Amazon Cognito generates a temporary password that is returned in the response; the user must change it on first login.

```bash
vamscli user cognito create -u <USER_ID> -e <EMAIL> [OPTIONS]
```

| Option            | Type | Required | Description                                         |
| ----------------- | ---- | -------- | --------------------------------------------------- |
| `-u`, `--user-id` | TEXT | Yes      | User ID (must be email format)                      |
| `-e`, `--email`   | TEXT | Yes      | Email address                                       |
| `-p`, `--phone`   | TEXT | No       | Phone number in E.164 format (e.g., `+12345678900`) |
| `--json-output`   | Flag | No       | Output raw JSON response                            |

:::tip[Phone Number Format]
Phone numbers must be in E.164 format: `+` followed by country code and number with no spaces or dashes. Examples: `+12345678900` (US), `+442071234567` (UK), `+81312345678` (Japan).
:::

```bash
vamscli user cognito create -u user@example.com -e user@example.com
vamscli user cognito create -u user@example.com -e user@example.com -p +12345678900
vamscli user cognito create -u user@example.com -e user@example.com --json-output
```

---

## user cognito update

Update a user's email address and/or phone number. At least one of `--email` or `--phone` must be provided.

```bash
vamscli user cognito update -u <USER_ID> [OPTIONS]
```

| Option            | Type | Required    | Description                      |
| ----------------- | ---- | ----------- | -------------------------------- |
| `-u`, `--user-id` | TEXT | Yes         | User ID to update                |
| `-e`, `--email`   | TEXT | Conditional | New email address                |
| `-p`, `--phone`   | TEXT | Conditional | New phone number in E.164 format |
| `--json-output`   | Flag | No          | Output raw JSON response         |

```bash
vamscli user cognito update -u user@example.com -e newemail@example.com
vamscli user cognito update -u user@example.com -p +12345678900
vamscli user cognito update -u user@example.com -e newemail@example.com -p +12345678900
```

---

## user cognito delete

Permanently delete a user from the Amazon Cognito user pool.

```bash
vamscli user cognito delete -u <USER_ID> --confirm [OPTIONS]
```

| Option            | Type | Required | Description              |
| ----------------- | ---- | -------- | ------------------------ |
| `-u`, `--user-id` | TEXT | Yes      | User ID to delete        |
| `--confirm`       | Flag | Yes      | Confirm user deletion    |
| `--json-output`   | Flag | No       | Output raw JSON response |

```bash
vamscli user cognito delete -u user@example.com --confirm
vamscli user cognito delete -u user@example.com --confirm --json-output
```

:::danger[Permanent Deletion]
This action is permanent and cannot be undone. All user data and sessions are removed. The `--confirm` flag is required, and in interactive (non-JSON) mode an additional confirmation prompt appears before deletion proceeds.
:::

---

## user cognito reset-password

Reset a user's password. Amazon Cognito generates a new temporary password that is returned in the response; the user must change it on their next login.

```bash
vamscli user cognito reset-password -u <USER_ID> --confirm [OPTIONS]
```

| Option            | Type | Required | Description               |
| ----------------- | ---- | -------- | ------------------------- |
| `-u`, `--user-id` | TEXT | Yes      | User ID to reset password |
| `--confirm`       | Flag | Yes      | Confirm password reset    |
| `--json-output`   | Flag | No       | Output raw JSON response  |

```bash
vamscli user cognito reset-password -u user@example.com --confirm
vamscli user cognito reset-password -u user@example.com --confirm --json-output
```

:::warning
The `--confirm` flag is required to prevent accidental password resets. The user's existing sessions remain valid until the temporary password is used.
:::

---

## API Key Management

API keys provide an alternative authentication method for VAMS, enabling programmatic access from scripts, CI/CD pipelines, and external integrations without a JWT token. Each key is associated with a VAMS user ID and authenticates as that user with all of the user's assigned roles and permissions.

The `api-key` commands manage keys for any user and require API-level access to the key management endpoints. The [`api-key user` sub-commands](#api-key-user-list) let any user manage their own keys without administrative access.

:::warning[API Key Security]
The plaintext API key value is displayed only once, at creation time, and cannot be retrieved afterwards. Store it securely immediately. Only a SHA-256 hash of the key is retained in Amazon DynamoDB (encrypted at rest with AWS KMS). Expired keys are rejected at the authorizer level, and deleting a key revokes its access immediately.
:::

---

## api-key list

List all API keys in the VAMS system. Returns metadata only -- key values are never shown after creation.

```bash
vamscli api-key list [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli api-key list
vamscli api-key list --json-output > api-keys-audit.json
```

---

## api-key create

Create a new API key. The key value is displayed only once -- save it immediately.

```bash
vamscli api-key create [OPTIONS]
```

| Option          | Type | Required | Description                                                                                    |
| --------------- | ---- | -------- | ---------------------------------------------------------------------------------------------- |
| `--name`        | TEXT | Yes      | Name for the API key (immutable after creation)                                                |
| `--user-id`     | TEXT | Yes      | VAMS user ID this key acts as (must have at least one role assigned)                           |
| `--description` | TEXT | Yes      | Description of the API key                                                                     |
| `--expires-at`  | TEXT | No       | Expiration date in ISO 8601 format (e.g., `2026-12-31T23:59:59Z`); omit for a non-expiring key |
| `--json-output` | Flag | No       | Output raw JSON response                                                                       |

```bash
vamscli api-key create --name "CI Pipeline" --user-id ci-bot@example.com --description "CI/CD pipeline access"
vamscli api-key create --name "Temp Key" --user-id dev@example.com --description "Temporary" --expires-at 2027-06-30T23:59:59Z
vamscli api-key create --name "Script Key" --user-id bot@example.com --description "Automation" --json-output
```

:::tip[Service Accounts]
Create dedicated service-account users (e.g., `ci-bot@example.com`) for automation rather than using personal accounts, and assign them only the roles they need. The target user ID must already exist in the VAMS user-roles table with at least one role assigned.
:::

---

## api-key update

Update an API key's description, expiration, or active status. The name and user ID cannot be changed after creation. At least one field must be provided.

```bash
vamscli api-key update --api-key-id <UUID> [OPTIONS]
```

| Option          | Type   | Required | Description                                                         |
| --------------- | ------ | -------- | ------------------------------------------------------------------- |
| `--api-key-id`  | TEXT   | Yes      | ID of the API key to update                                         |
| `--description` | TEXT   | No       | New description                                                     |
| `--expires-at`  | TEXT   | No       | New expiration date in ISO 8601 format (empty string `""` to clear) |
| `--is-active`   | CHOICE | No       | Enable or disable the key (`true` or `false`)                       |
| `--json-output` | Flag   | No       | Output raw JSON response                                            |

```bash
vamscli api-key update --api-key-id UUID --description "Updated description"
vamscli api-key update --api-key-id UUID --is-active false
vamscli api-key update --api-key-id UUID --expires-at ""
```

---

## api-key delete

Permanently delete an API key. This immediately revokes access for anyone using the key.

```bash
vamscli api-key delete --api-key-id <UUID> [OPTIONS]
```

| Option          | Type | Required | Description                 |
| --------------- | ---- | -------- | --------------------------- |
| `--api-key-id`  | TEXT | Yes      | ID of the API key to delete |
| `--json-output` | Flag | No       | Output raw JSON response    |

```bash
vamscli api-key delete --api-key-id UUID
vamscli api-key delete --api-key-id UUID --json-output
```

---

## Self-Service API Keys (`api-key user`)

The `api-key user` sub-commands let any authenticated user manage their own API keys without administrative access. Keys created here are always tied to the calling user, so there is no `--user-id` option.

:::info[Self-Service Expiration Limit]
Self-service keys require an expiration date, and the expiration may be at most **365 days** from the key's creation date. The expiration cannot be cleared, and it cannot be extended beyond 365 days from the original creation date. After the window elapses, create a new key to rotate.
:::

---

## api-key user list

List your own API keys. Returns metadata only.

```bash
vamscli api-key user list [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli api-key user list
vamscli api-key user list --json-output
```

---

## api-key user create

Create a new API key tied to your own user. The key value is displayed only once -- save it immediately.

```bash
vamscli api-key user create [OPTIONS]
```

| Option          | Type | Required | Description                                                               |
| --------------- | ---- | -------- | ------------------------------------------------------------------------- |
| `--name`        | TEXT | Yes      | Name for the API key (immutable after creation)                           |
| `--description` | TEXT | Yes      | Description of the API key                                                |
| `--expires-at`  | TEXT | Yes      | Expiration date in ISO 8601 format (required, max 365 days from creation) |
| `--json-output` | Flag | No       | Output raw JSON response                                                  |

```bash
vamscli api-key user create --name "My Script" --description "Automation" --expires-at 2026-12-31T23:59:59Z
vamscli api-key user create --name "Dev Key" --description "Testing" --expires-at 2026-09-30 --json-output
```

---

## api-key user update

Update one of your own API keys. The expiration cannot be cleared and cannot be set beyond 365 days from the key's original creation date. At least one field must be provided.

```bash
vamscli api-key user update --api-key-id <UUID> [OPTIONS]
```

| Option          | Type   | Required | Description                                                                        |
| --------------- | ------ | -------- | ---------------------------------------------------------------------------------- |
| `--api-key-id`  | TEXT   | Yes      | ID of your API key to update                                                       |
| `--description` | TEXT   | No       | New description                                                                    |
| `--expires-at`  | TEXT   | No       | New expiration in ISO 8601 format (within 365 days of creation; cannot be cleared) |
| `--is-active`   | CHOICE | No       | Enable or disable the key (`true` or `false`)                                      |
| `--json-output` | Flag   | No       | Output raw JSON response                                                           |

```bash
vamscli api-key user update --api-key-id UUID --expires-at 2026-11-30T23:59:59Z
vamscli api-key user update --api-key-id UUID --is-active false
```

---

## api-key user delete

Delete one of your own API keys. This immediately revokes access.

```bash
vamscli api-key user delete --api-key-id <UUID> [OPTIONS]
```

| Option          | Type | Required | Description                  |
| --------------- | ---- | -------- | ---------------------------- |
| `--api-key-id`  | TEXT | Yes      | ID of your API key to delete |
| `--json-output` | Flag | No       | Output raw JSON response     |

```bash
vamscli api-key user delete --api-key-id UUID
vamscli api-key user delete --api-key-id UUID --json-output
```

---

## Using API Keys

Pass the API key in the `Authorization` header of API calls. The key works with or without a `Bearer` prefix:

```bash
curl -H "Authorization: vams_AbCdEfGhIjKlMnOp..." https://your-vams-url/database
curl -H "Authorization: Bearer vams_AbCdEfGhIjKlMnOp..." https://your-vams-url/database
```

The request authenticates as the key's associated user ID, with all roles and permissions assigned to that user.

:::note[MFA Considerations]
API key authentication does not support MFA. Roles with `mfaRequired=true` are inactive when authenticating via API key. A user whose only roles require MFA has no effective permissions through an API key.
:::

---

## Workflow Examples

### CI/CD pipeline setup

```bash
# Ensure the bot user has roles assigned
vamscli role user create -u ci-bot@example.com --role-name pipeline-runner

# Create an API key for the bot user
vamscli api-key create --name "GitHub Actions" --user-id ci-bot@example.com --description "CI/CD" --expires-at 2027-12-31T23:59:59Z --json-output

# Store the returned apiKey value as a CI/CD secret
```

### API key rotation

```bash
# Create a new key with the same user ID
vamscli api-key create --name "CI Pipeline v2" --user-id ci-bot@example.com --description "Rotated key" --json-output

# Update systems with the new key value, then delete the old key
vamscli api-key delete --api-key-id OLD_KEY_ID
```

### Self-service rotation

Self-service keys cannot be extended beyond 365 days from creation, so rotation means creating a fresh key:

```bash
vamscli api-key user create --name "My Script v2" --description "Rotated automation key" --expires-at 2027-06-01T00:00:00Z --json-output
vamscli api-key user delete --api-key-id OLD_KEY_ID
```

### Bulk user creation

```bash
for email in user1@example.com user2@example.com user3@example.com; do
    vamscli user cognito create -u "$email" -e "$email" --json-output
done
```

---

## Related Pages

-   [Setup and Authentication](setup-and-auth.md)
-   [Permission Commands](permissions.md)
-   [API Keys User Guide](../../user-guide/api-keys.md)
