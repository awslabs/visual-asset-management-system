# API Key Management Commands

This guide covers the API key management commands in VamsCLI, which allow you to create, list, update, and delete API keys for programmatic access to VAMS.

## Table of Contents

-   [Overview](#overview)
-   [Prerequisites](#prerequisites)
-   [Commands](#commands)
    -   [api-key list](#api-key-list)
    -   [api-key create](#api-key-create)
    -   [api-key update](#api-key-update)
    -   [api-key delete](#api-key-delete)
-   [Using API Keys](#using-api-keys)
-   [Security Model](#security-model)
-   [Common Workflows](#common-workflows)
-   [JSON Input/Output](#json-inputoutput)
-   [Best Practices](#best-practices)

## Overview

API keys provide an alternative authentication method for VAMS, enabling programmatic access from scripts, CI/CD pipelines, and external integrations without requiring JWT tokens. Each API key:

-   Has a unique name and is associated with a VAMS user ID
-   Acts as that user with all their assigned roles and permissions
-   Can have an optional expiration date
-   Is shown in plaintext **only once** at creation time — store it securely

## Prerequisites

Before using API key commands, ensure you have:

1. **Completed Setup**: Run `vamscli setup <api-gateway-url>`
2. **Authenticated**: Run `vamscli auth login -u <username>`
3. **Appropriate Permissions**: Your user account must have API-level access to the API key management endpoints
4. **Target User Has Roles**: The user ID specified for the API key must have at least one role assigned in VAMS

## Commands

### api-key list

List all API keys in the VAMS system. Returns metadata only — API key values are never shown after creation.

#### Basic Usage

```bash
# List all API keys
vamscli api-key list

# List with JSON output
vamscli api-key list --json-output
```

#### Options

-   `--json-output`: Output raw JSON response

#### Example Output

```
Found 2 API key(s)
  Name                            Key ID                                User ID                         Expires                    Status
  -------------------------------------------------------------------------------------------------------------------------------
  CI Pipeline                     a1b2c3d4-e5f6-7890-abcd-ef1234567890  ci-bot@example.com              2027-01-01T00:00:00Z       Active
  Dev Testing                     f9e8d7c6-b5a4-3210-fedc-ba9876543210  dev@example.com                 Never                      Active
```

### api-key create

Create a new API key. The key value is displayed **only once** — save it immediately.

#### Basic Usage

```bash
# Create a basic API key
vamscli api-key create --name "CI Pipeline" --user-id ci-bot@example.com --description "CI/CD pipeline access"

# Create with expiration date
vamscli api-key create --name "Temp Key" --user-id dev@example.com --description "Temporary dev access" --expires-at 2027-06-30T23:59:59Z

# Create with JSON output (for automation)
vamscli api-key create --name "Script Key" --user-id bot@example.com --description "Automation key" --json-output
```

#### Options

-   `--name TEXT`: Name for the API key (required, immutable after creation)
-   `--user-id TEXT`: VAMS user ID this key acts as (required, must have roles assigned)
-   `--description TEXT`: Description of the API key (required)
-   `--expires-at TEXT`: Expiration date in ISO 8601 format, e.g. `2027-12-31` or `2027-12-31T23:59:59Z` (optional)
-   `--json-output`: Output raw JSON response

#### Example Output

```
API key created successfully
  API Key ID:    a1b2c3d4-e5f6-7890-abcd-ef1234567890
  Name:          CI Pipeline
  User ID:       ci-bot@example.com
  Created By:    admin@example.com
  Expires At:    Never

  API Key (SAVE THIS - shown only once):
  vams_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789AbCdEfGhIjKlMnOpQrStUv
```

### api-key update

Update an existing API key's description, expiration date, or active status. The key name and user ID cannot be changed after creation.

#### Basic Usage

```bash
# Update description
vamscli api-key update --api-key-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 --description "Updated description"

# Update expiration date
vamscli api-key update --api-key-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 --expires-at 2028-01-01T00:00:00Z

# Clear expiration (set to never expire)
vamscli api-key update --api-key-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 --expires-at ""

# Disable an API key
vamscli api-key update --api-key-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 --is-active false

# Re-enable an API key
vamscli api-key update --api-key-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 --is-active true

# Update multiple fields
vamscli api-key update --api-key-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 --description "Extended key" --expires-at 2028-06-30T23:59:59Z --json-output
```

#### Options

-   `--api-key-id TEXT`: ID of the API key to update (required)
-   `--description TEXT`: New description
-   `--expires-at TEXT`: New expiration date in ISO 8601 format (use empty string `""` to clear expiration)
-   `--is-active [true|false]`: Enable or disable the API key
-   `--json-output`: Output raw JSON response

At least one of `--description`, `--expires-at`, or `--is-active` must be provided.

#### Example Output

```
API key updated successfully
  API Key ID:    a1b2c3d4-e5f6-7890-abcd-ef1234567890
  Name:          CI Pipeline
  User ID:       ci-bot@example.com
  Description:   Updated description
  Created By:    admin@example.com
  Created At:    2026-03-09T12:00:00+00:00
  Expires At:    2028-01-01T00:00:00Z
  Active:        true
```

### api-key delete

Permanently delete an API key. This immediately revokes access for anyone using this key.

#### Basic Usage

```bash
# Delete an API key
vamscli api-key delete --api-key-id a1b2c3d4-e5f6-7890-abcd-ef1234567890

# Delete with JSON output
vamscli api-key delete --api-key-id a1b2c3d4-e5f6-7890-abcd-ef1234567890 --json-output
```

#### Options

-   `--api-key-id TEXT`: ID of the API key to delete (required)
-   `--json-output`: Output raw JSON response

#### Example Output

```
API key deleted successfully
```

## Using API Keys

Once created, use the API key in the `Authorization` header of API calls to VAMS. The key can be sent with or without a `Bearer` prefix:

```bash
# Direct API key (recommended)
curl -H "Authorization: vams_AbCdEfGhIjKlMnOpQrStUv..." https://your-vams-url/database

# With Bearer prefix (also works)
curl -H "Authorization: Bearer vams_AbCdEfGhIjKlMnOpQrStUv..." https://your-vams-url/database
```

The API key authenticates as the user ID specified during creation, with all roles and permissions assigned to that user.

## Security Model

-   **One-time display**: The plaintext API key is shown only at creation time and cannot be retrieved afterwards
-   **Hash-only storage**: Only a SHA-256 hash of the key is stored in DynamoDB (with KMS encryption at rest)
-   **Expiration enforcement**: Expired keys are rejected at the authorizer level
-   **Role-based access**: The key inherits all roles assigned to its associated user ID
-   **Immediate revocation**: Deleting a key immediately prevents its use

## Common Workflows

### Setting Up CI/CD Pipeline Access

```bash
# 1. Ensure the CI bot user has appropriate roles
vamscli role user create -u ci-bot@example.com --role-name pipeline-runner

# 2. Create an API key for the CI bot
vamscli api-key create \
  --name "GitHub Actions" \
  --user-id ci-bot@example.com \
  --description "GitHub Actions CI/CD pipeline" \
  --expires-at 2027-12-31T23:59:59Z \
  --json-output

# 3. Store the returned apiKey value as a CI/CD secret (e.g., GitHub Actions secret)
```

### Rotating an API Key

```bash
# 1. Create a new key with the same user ID
vamscli api-key create \
  --name "CI Pipeline v2" \
  --user-id ci-bot@example.com \
  --description "Rotated CI pipeline key" \
  --json-output

# 2. Update your systems with the new key value

# 3. Delete the old key
vamscli api-key delete --api-key-id OLD_KEY_ID
```

### Auditing API Keys

```bash
# Export all API keys for audit
vamscli api-key list --json-output > api-keys-audit.json

# Check for expired keys (using jq)
vamscli api-key list --json-output | jq '.Items[] | select(.expiresAt != "" and .expiresAt != null)'
```

## JSON Input/Output

### JSON Output Format

#### List Output

```json
{
    "Items": [
        {
            "apiKeyId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "apiKeyName": "CI Pipeline",
            "userId": "ci-bot@example.com",
            "description": "CI/CD pipeline access",
            "createdBy": "admin@example.com",
            "createdAt": "2026-03-09T12:00:00+00:00",
            "updatedAt": "2026-03-09T12:00:00+00:00",
            "expiresAt": "2027-01-01T00:00:00Z",
            "isActive": "true"
        }
    ]
}
```

#### Create Output

```json
{
    "apiKeyId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "apiKeyName": "CI Pipeline",
    "userId": "ci-bot@example.com",
    "description": "CI/CD pipeline access",
    "createdBy": "admin@example.com",
    "createdAt": "2026-03-09T12:00:00+00:00",
    "expiresAt": "2027-01-01T00:00:00Z",
    "isActive": "true",
    "apiKey": "vams_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789..."
}
```

Note: The `apiKey` field containing the plaintext key is only present in the create response.

## Best Practices

### Naming Conventions

-   Use descriptive names that identify the purpose: `"GitHub Actions CI"`, `"Monitoring Script"`, `"Data Import Tool"`
-   Names are immutable after creation, so choose carefully

### Expiration Dates

-   Always set expiration dates for temporary or external access
-   Use short-lived keys for CI/CD pipelines and rotate regularly
-   Review and clean up expired keys periodically

### User ID Selection

-   Create dedicated service account users (e.g., `ci-bot@example.com`) rather than using personal accounts
-   Assign minimal required roles to service accounts (principle of least privilege)
-   The user ID must exist in the VAMS user roles table with at least one role

### Key Storage

-   Store API keys in secrets management systems (AWS Secrets Manager, GitHub Secrets, HashiCorp Vault)
-   Never commit API keys to source code repositories
-   Never share API keys via email or chat

### MFA Considerations

-   API key authentication does not support MFA
-   Roles with `mfaRequired=true` will be **inactive** when authenticating via API key
-   If a user only has MFA-required roles, the API key will have no effective permissions

## See Also

-   [Role Management](role-management.md) - Managing roles and permissions
-   [Setup and Authentication Guide](setup-auth.md) - Initial setup and auth options
-   [Global Options and JSON Usage](global-options.md) - JSON patterns for automation
