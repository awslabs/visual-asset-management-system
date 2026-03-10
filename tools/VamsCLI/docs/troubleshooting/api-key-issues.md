# API Key Management Troubleshooting

This guide helps you troubleshoot common issues when working with API key management commands in VamsCLI.

## Table of Contents

-   [Common Errors](#common-errors)
    -   [API Key Not Found](#api-key-not-found)
    -   [API Key Creation Failed](#api-key-creation-failed)
    -   [User Has No Roles](#user-has-no-roles)
    -   [Invalid Expiration Date](#invalid-expiration-date)
    -   [Missing Required Fields](#missing-required-fields)
-   [Authentication Issues](#authentication-issues)
    -   [API Key Not Working](#api-key-not-working)
    -   [API Key Expired](#api-key-expired)
    -   [No Roles for API Key User](#no-roles-for-api-key-user)
-   [Permission Issues](#permission-issues)
-   [JSON Input/Output Issues](#json-inputoutput-issues)
-   [Best Practices](#best-practices)

## Common Errors

### API Key Not Found

**Error Message:**

```
API Key Not Found: API key not found
```

**Cause:** The specified API key ID does not exist in the system, or it has already been deleted.

**Solutions:**

1. **Verify the API key ID:**

    ```bash
    vamscli api-key list --json-output
    ```

2. **Check for typos in the ID** -- API key IDs are UUIDs (e.g., `a1b2c3d4-e5f6-7890-abcd-ef1234567890`)

3. **The key may have been deleted** -- once deleted, an API key cannot be recovered

### API Key Creation Failed

**Error Message:**

```
API Key Creation Error: Failed to create API key
```

**Cause:** The API key creation request failed. This could be due to invalid input, network issues, or backend errors.

**Solutions:**

1. **Check all required fields are provided:**

    ```bash
    vamscli api-key create \
      --name "My Key" \
      --user-id "user@example.com" \
      --description "Key description"
    ```

2. **Verify the name format** -- the API key name must match the pattern `[a-zA-Z0-9\-._\s]{1,256}`

3. **Check with verbose mode for more details:**

    ```bash
    vamscli --verbose api-key create \
      --name "My Key" \
      --user-id "user@example.com" \
      --description "Key description"
    ```

### User Has No Roles

**Error Message:**

```
Validation Error: User 'user@example.com' has no roles assigned. Cannot create API key for a user without roles.
```

**Cause:** The user ID specified for the API key does not have any roles assigned in VAMS. API keys can only be created for users with at least one role, because the key authenticates as that user.

**Solutions:**

1. **Check if the user has roles:**

    ```bash
    vamscli role user list --json-output
    ```

2. **Assign roles to the user first:**

    ```bash
    vamscli role user create -u user@example.com --role-name viewer
    ```

3. **Then create the API key:**

    ```bash
    vamscli api-key create \
      --name "My Key" \
      --user-id "user@example.com" \
      --description "Key description"
    ```

### Invalid Expiration Date

**Error Message:**

```
Validation Error: Invalid date format: 'not-a-date'. Use ISO 8601 format (e.g. 2026-12-31 or 2026-12-31T23:59:59Z)
```

**Cause:** The expiration date provided is not in a valid ISO 8601 format.

**Solutions:**

1. **Use date-only format:**

    ```bash
    vamscli api-key create --name "Key" --user-id "user@example.com" --description "Desc" --expires-at 2027-12-31
    ```

2. **Use full datetime format:**

    ```bash
    vamscli api-key create --name "Key" --user-id "user@example.com" --description "Desc" --expires-at 2027-12-31T23:59:59Z
    ```

### Missing Required Fields

**Error Message:**

```
Error: Missing option '--name'.
Error: Missing option '--user-id'.
Error: Missing option '--description'.
```

**Cause:** Required options were not provided.

**Solutions:**

All three fields are required for `api-key create`:

```bash
vamscli api-key create \
  --name "Key Name" \
  --user-id "user@example.com" \
  --description "What this key is for"
```

For `api-key update`, at least one of `--description` or `--expires-at` must be provided:

```bash
vamscli api-key update --api-key-id UUID --description "New description"
```

## Authentication Issues

### API Key Not Working

**Symptom:** API calls using the API key return 401 or 403 errors.

**Solutions:**

1. **Verify the key format** -- API keys start with `vams_`. Use the key exactly as it was displayed at creation:

    ```bash
    curl -H "Authorization: vams_AbCdEf..." https://your-vams-url/database
    ```

2. **Bearer prefix is also supported:**

    ```bash
    curl -H "Authorization: Bearer vams_AbCdEf..." https://your-vams-url/database
    ```

3. **Check if the key is still active:**

    ```bash
    vamscli api-key list --json-output
    ```

    Look for `isActive: "true"` on your key.

4. **Check if the key has expired:**

    ```bash
    vamscli api-key list --json-output
    ```

    Look at the `expiresAt` field.

5. **Verify the user ID still has roles assigned:**

    ```bash
    vamscli role user list --json-output
    ```

### API Key Expired

**Symptom:** API calls return "API key has expired" error.

**Solutions:**

1. **Update the expiration date:**

    ```bash
    vamscli api-key update --api-key-id UUID --expires-at 2028-12-31T23:59:59Z
    ```

2. **Or create a new key** if you prefer:

    ```bash
    vamscli api-key create \
      --name "Renewed Key" \
      --user-id "user@example.com" \
      --description "Renewed access" \
      --expires-at 2028-12-31T23:59:59Z
    ```

### No Roles for API Key User

**Symptom:** API calls return "No roles for API key user" error.

**Cause:** The user ID associated with the API key no longer has any roles assigned. This can happen if someone removes the user's role assignments after the API key was created.

**Solutions:**

1. **Re-assign roles to the user:**

    ```bash
    vamscli role user create -u user@example.com --role-name viewer
    ```

2. **Verify with:**

    ```bash
    vamscli role user list --json-output
    ```

## Permission Issues

**Symptom:** "Not Authorized" error when trying to manage API keys.

**Cause:** Your user account does not have API-level authorization to access the API key management endpoints (`/auth/api-keys`).

**Solutions:**

1. **Check your current role permissions** -- ask your VAMS administrator to grant access to the `/auth/api-keys` API route

2. **Verify your auth status:**

    ```bash
    vamscli auth status
    ```

## JSON Input/Output Issues

### Capturing API Key Value in Scripts

The API key is only shown once at creation. Use `--json-output` to capture it programmatically:

```bash
# Capture the key value in a variable
KEY_RESPONSE=$(vamscli api-key create \
  --name "Script Key" \
  --user-id "bot@example.com" \
  --description "Automated key" \
  --json-output)

# Extract the key value with jq
API_KEY=$(echo "$KEY_RESPONSE" | jq -r '.apiKey')
echo "API Key: $API_KEY"

# Extract the key ID for future reference
KEY_ID=$(echo "$KEY_RESPONSE" | jq -r '.apiKeyId')
echo "Key ID: $KEY_ID"
```

### Parsing List Output

```bash
# Get all key IDs
vamscli api-key list --json-output | jq -r '.Items[].apiKeyId'

# Find keys for a specific user
vamscli api-key list --json-output | jq '.Items[] | select(.userId == "user@example.com")'

# Find expired keys
vamscli api-key list --json-output | jq '.Items[] | select(.expiresAt != "")'
```

## Best Practices

### Key Lifecycle

1. **Always set expiration dates** for temporary or external keys
2. **Rotate keys regularly** -- create a new key, update systems, then delete the old one
3. **Review keys periodically** with `vamscli api-key list` to find unused or expired keys
4. **Delete unused keys immediately** to reduce security risk

### Troubleshooting Checklist

When an API key isn't working, check in this order:

1. Is the key format correct? (starts with `vams_`)
2. Is the key active? (`isActive: "true"`)
3. Has the key expired? (check `expiresAt`)
4. Does the user ID still have roles? (check `role user list`)
5. Are the roles appropriate for the API being called?

### Debugging with Verbose Mode

Always use `--verbose` when troubleshooting:

```bash
vamscli --verbose api-key list
vamscli --verbose api-key create --name "Test" --user-id "user@example.com" --description "Debug test"
```

This shows API request/response details, timing, and full error information.

## See Also

-   [API Key Management Commands](../commands/api-key-management.md)
-   [Role Management Troubleshooting](role-issues.md)
-   [Setup and Authentication Troubleshooting](setup-auth-issues.md)
-   [General Troubleshooting](general-troubleshooting.md)
