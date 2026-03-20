---
sidebar_label: Users and API Keys
title: User and API Key Commands
---

# User and API Key Commands

Manage Amazon Cognito users and API keys for programmatic access to VAMS.

---

## Amazon Cognito User Management

:::note[Prerequisite]
User management commands require Amazon Cognito authentication to be enabled in your VAMS deployment. Your user must have admin permissions.
:::

### user cognito list

List all Amazon Cognito users with pagination support.

```bash
vamscli user cognito list [OPTIONS]
```

| Option             | Type    | Description                        |
| ------------------ | ------- | ---------------------------------- |
| `--page-size`      | INTEGER | Items per page                     |
| `--max-items`      | INTEGER | Max items (with `--auto-paginate`) |
| `--starting-token` | TEXT    | Pagination token                   |
| `--auto-paginate`  | Flag    | Fetch all items                    |
| `--json-output`    | Flag    | Raw JSON response                  |

### user cognito create

Create a new user. Amazon Cognito generates a temporary password returned in the response.

```bash
vamscli user cognito create -u <USER_ID> -e <EMAIL> [-p <PHONE>] [--json-output]
```

| Option            | Type | Required | Description                                         |
| ----------------- | ---- | -------- | --------------------------------------------------- |
| `-u`, `--user-id` | TEXT | Yes      | User ID (must be email format)                      |
| `-e`, `--email`   | TEXT | Yes      | Email address                                       |
| `-p`, `--phone`   | TEXT | No       | Phone number in E.164 format (e.g., `+12345678900`) |

:::tip[Phone Number Format]
Phone numbers must be in E.164 format: `+` followed by country code and number with no spaces or dashes. Examples: `+12345678900` (US), `+442071234567` (UK).
:::

### user cognito update

Update a user's email or phone number. At least one field must be provided.

```bash
vamscli user cognito update -u user@example.com -e newemail@example.com
vamscli user cognito update -u user@example.com -p +12345678900
```

### user cognito delete

Permanently delete a user. Requires `--confirm`.

```bash
vamscli user cognito delete -u user@example.com --confirm
```

:::danger
This action is permanent and cannot be undone. All user data and sessions are removed.
:::

### user cognito reset-password

Reset a user's password, generating a new temporary password. Requires `--confirm`.

```bash
vamscli user cognito reset-password -u user@example.com --confirm
```

---

## API Key Management

API keys provide programmatic access to VAMS without requiring JWT tokens. Each key is associated with a VAMS user ID and inherits that user's roles and permissions.

:::warning[API Key Security]
The API key value is displayed only once at creation time. Store it securely immediately. Only a SHA-256 hash is retained in the database.
:::

### api-key list

List all API keys. Returns metadata only -- key values are never shown after creation.

```bash
vamscli api-key list [--json-output]
```

### api-key create

Create a new API key.

```bash
vamscli api-key create [OPTIONS]
```

| Option          | Type | Required | Description                                              |
| --------------- | ---- | -------- | -------------------------------------------------------- |
| `--name`        | TEXT | Yes      | Name for the API key (immutable after creation)          |
| `--user-id`     | TEXT | Yes      | VAMS user ID this key acts as (must have roles assigned) |
| `--description` | TEXT | Yes      | Description                                              |
| `--expires-at`  | TEXT | No       | Expiration date in ISO 8601 format                       |
| `--json-output` | Flag | No       | Raw JSON response                                        |

```bash
vamscli api-key create --name "CI Pipeline" --user-id ci-bot@example.com --description "CI/CD access"
vamscli api-key create --name "Temp Key" --user-id dev@example.com --description "Temporary" --expires-at 2027-06-30T23:59:59Z
```

### api-key update

Update description, expiration, or active status. At least one field must be provided.

```bash
vamscli api-key update --api-key-id <UUID> [OPTIONS]
```

| Option          | Type   | Description                                 |
| --------------- | ------ | ------------------------------------------- |
| `--api-key-id`  | TEXT   | API key ID (required)                       |
| `--description` | TEXT   | New description                             |
| `--expires-at`  | TEXT   | New expiration (empty string `""` to clear) |
| `--is-active`   | CHOICE | `true` or `false`                           |

```bash
vamscli api-key update --api-key-id UUID --description "Updated description"
vamscli api-key update --api-key-id UUID --is-active false
vamscli api-key update --api-key-id UUID --expires-at ""
```

### api-key delete

Permanently delete an API key. Immediately revokes access.

```bash
vamscli api-key delete --api-key-id UUID [--json-output]
```

### Using API keys

Pass the API key in the `Authorization` header of API calls:

```bash
curl -H "Authorization: vams_AbCdEfGhIjKlMnOp..." https://your-vams-url/database
```

:::note[MFA Considerations]
API key authentication does not support MFA. Roles with `mfaRequired=true` are inactive when authenticating via API key.
:::

---

## Workflow Examples

### CI/CD pipeline setup

```bash
# Ensure bot user has roles
vamscli role user create -u ci-bot@example.com --role-name pipeline-runner

# Create API key
vamscli api-key create --name "GitHub Actions" --user-id ci-bot@example.com --description "CI/CD" --expires-at 2027-12-31T23:59:59Z --json-output

# Store the returned apiKey value as a CI/CD secret
```

### API key rotation

```bash
# Create new key
vamscli api-key create --name "CI Pipeline v2" --user-id ci-bot@example.com --description "Rotated key" --json-output

# Update systems with new key value, then delete old key
vamscli api-key delete --api-key-id OLD_KEY_ID
```

## Related Pages

-   [Setup and Authentication](setup-and-auth.md)
-   [Permission Commands](permissions.md)
-   [API Keys User Guide](../../user-guide/api-keys.md)
