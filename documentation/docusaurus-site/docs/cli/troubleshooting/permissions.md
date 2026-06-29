---
sidebar_label: Roles and Permissions
title: Roles, Permissions, and API Key Troubleshooting
---

# Roles, Permissions, and API Key Troubleshooting

This page covers issues encountered when managing roles, constraints, user-role assignments, and API keys with the VamsCLI.

---

## Roles and Constraints

### Role or Constraint Already Exists

**Symptoms:**

-   `Role Already Exists: Role 'admin' already exists`
-   `Constraint Already Exists: Constraint already exists`

**Cause:** A role name or constraint identifier must be unique. The name or ID you supplied is already in use.

**Resolution:**

List the existing entities, then either pick a new name/ID or update the existing one.

```bash
vamscli role list
vamscli role constraint list

# Update instead of recreate
vamscli role update -r admin --description "Updated description"
vamscli role constraint update -c my-constraint --json-input constraint.json
```

### Role or Constraint Not Found

**Symptoms:**

-   `Role Not Found: Role 'nonexistent' not found`
-   `Constraint Not Found: Constraint 'nonexistent' not found`

**Cause:** The referenced role name or constraint ID does not exist. Names and IDs are case-sensitive.

**Resolution:**

List the available entities to confirm the exact spelling.

```bash
vamscli role list
vamscli role constraint list
vamscli role constraint get -c my-constraint
```

### Role Deletion Failed

**Symptoms:** `Role Deletion Error: Role deletion failed`.

**Cause:** The deletion could not complete on the backend. The backend automatically cleans up user-role assignments during deletion, so a persistent failure usually points to a backend or connectivity issue rather than lingering assignments.

**Resolution:**

The `delete` command requires the `--confirm` flag. Confirm the role exists, verify connectivity, then retry. Contact your VAMS administrator if the error persists — some system roles cannot be deleted.

```bash
vamscli role delete -r old-role --confirm
vamscli auth status
```

### Invalid Role Data

**Symptoms:** `Invalid Role Data: roleName contains invalid characters`.

**Cause:** The role data fails backend validation.

**Resolution:**

-   Use only alphanumeric characters, hyphens, and underscores in role names. Avoid `@`, `#`, `$`, and other special characters.
-   Keep descriptions to 256 characters or fewer.

```bash
# Valid
vamscli role create -r admin-role --description "Administrator role"

# Invalid — '@' is not allowed in a role name
vamscli role create -r admin@role --description "Administrator role"
```

### Invalid Constraint Data

**Symptoms:**

-   `Invalid Constraint Data: objectType must be one of: ...`
-   `Invalid Constraint Data: Constraint must include criteriaOr or criteriaAnd statements`

**Cause:** A constraint field is outside its allowed value set, or the constraint has no criteria.

**Resolution:**

Retrieve the deployment's valid object types, criteria operators, permissions, and permission types, then build the constraint to match.

```bash
vamscli role constraint permission-objects
```

A constraint must define at least one `criteriaAnd` or `criteriaOr` entry, `groupId` values must reference existing roles, and `userId` values must be at least three characters (typically an email).

```bash
vamscli role constraint create -c test --json-input '{
  "name": "Test",
  "description": "Test constraint",
  "objectType": "asset",
  "criteriaAnd": [{"field": "databaseId", "operator": "equals", "value": "db1"}],
  "groupPermissions": [{"groupId": "admin", "permission": "read", "permissionType": "allow"}]
}'
```

:::note
Constraint criteria `value` fields are validated as regular expressions. Supply a valid regex or a plain string that is also a valid pattern.
:::

### Missing Required Fields

**Symptoms:** `Invalid Input: --description is required when not using --json-input`.

**Cause:** A field required for the operation was omitted.

**Resolution:**

Supply the required options, or provide a complete payload with `--json-input`. `role update` requires at least one field to change.

```bash
vamscli role create -r admin --description "Administrator role"
vamscli role create -r admin --json-input '{"roleName":"admin","description":"Admin role"}'
vamscli role create --help
```

### User-Role Assignment Errors

**Symptoms:**

-   `User Role Already Exists: One or more roles already exist for this user`
-   `User Role Not Found: User roles for 'user@example.com' not found`
-   `Invalid User Role Data: Role 'invalid-role' does not exist in the system`

**Cause:** You assigned a role the user already holds, referenced a user with no assignments, or referenced a role that does not exist.

**Resolution:**

`role user create` adds assignments; `role user update` performs a differential update (roles not in the new list are removed). Confirm the role exists and the user ID is valid (case-sensitive, at least three characters, typically an email).

```bash
vamscli role user list
vamscli role user create -u user@example.com --role-name viewer
vamscli role user update -u user@example.com --role-name admin --role-name viewer
```

### Constraint Template Import Errors

**Symptoms:**

-   `Invalid Template Data: Missing 'variableValues' field`
-   `Invalid Template Data: Missing 'ROLE_NAME' in variableValues`
-   `Template Import Error: ...`

**Cause:** The template JSON is missing required structure. Every template must include a `variableValues` object containing `ROLE_NAME` (used as the `groupId` for all created constraints) and a non-empty `constraints` array.

**Resolution:**

```bash
vamscli role constraint template import -j ./database-admin.json
```

See the example templates in `documentation/permissionsTemplates/` for the expected structure.

### Pagination Conflicts

**Symptoms:**

-   `Cannot use --auto-paginate with --starting-token.`
-   `Warning: --max-items only applies with --auto-paginate. Ignoring --max-items.`
-   `Reached maximum of 10000 items. More items may be available.`

**Cause:** `--auto-paginate` (fetch all pages) and `--starting-token` (manual paging) are mutually exclusive. `--max-items` caps the auto-paginated total and only applies with `--auto-paginate`; its default is 10,000.

**Resolution:**

```bash
# Auto-paginate, raising the cap for large datasets
vamscli role list --auto-paginate --max-items 20000

# Manual paging with an explicit page size
vamscli role list --page-size 200
vamscli role list --starting-token "token123" --page-size 200
```

### Invalid JSON Input

**Symptoms:** `Invalid JSON input: '...' is neither valid JSON nor a readable file path`.

**Cause:** The value passed to `--json-input` is malformed JSON and is not a path to an existing file.

**Resolution:**

`--json-input` accepts either an inline JSON string or a file path. Validate the JSON and confirm the file exists. Use double quotes for keys and string values, and remove trailing commas.

```bash
echo '{"roleName":"admin","description":"Admin"}' | python -m json.tool
vamscli role create -r admin --json-input /full/path/to/role.json
```

---

## API Keys

### API Key Not Found

**Symptoms:** `API Key Not Found: API key not found`.

**Cause:** The supplied API key ID does not exist or the key was deleted. Deleted keys cannot be recovered.

**Resolution:**

List keys to confirm the ID. API key IDs are UUIDs (for example, `a1b2c3d4-e5f6-7890-abcd-ef1234567890`).

```bash
vamscli api-key list --json-output
```

### API Key Creation Failed

**Symptoms:** `API Key Creation Error: Failed to create API key`.

**Cause:** Invalid input, a name format violation, or a backend error.

**Resolution:**

The admin `api-key create` command requires `--name`, `--user-id`, and `--description`. The API key name must match the pattern `[a-zA-Z0-9\-._\s]\{1,256\}`. Re-run with `--verbose` for full request and response detail.

```bash
vamscli api-key create \
  --name "CI Pipeline" \
  --user-id "admin@example.com" \
  --description "CI/CD pipeline key"

vamscli --verbose api-key create --name "My Key" --user-id "user@example.com" --description "Debug"
```

### User Has No Roles

**Symptoms:** `Validation Error: User 'user@example.com' has no roles assigned. Cannot create API key for a user without roles.`

**Cause:** An API key authenticates as the user it is bound to, so that user must hold at least one role before a key can be created.

**Resolution:**

Assign a role first, then create the key.

```bash
vamscli role user list --json-output
vamscli role user create -u user@example.com --role-name viewer
vamscli api-key create --name "My Key" --user-id "user@example.com" --description "Key description"
```

### Invalid Expiration Date

**Symptoms:** `Validation Error: Invalid date format: '...'. Use ISO 8601 format (e.g. 2026-12-31 or 2026-12-31T23:59:59Z)`.

**Cause:** `--expires-at` was not a valid ISO 8601 date or datetime.

**Resolution:**

Use a date (`2027-12-31`) or a full datetime (`2027-12-31T23:59:59Z`).

```bash
vamscli api-key create --name "Key" --user-id "user@example.com" --description "Desc" --expires-at 2027-12-31
```

:::note[Self-service keys cap at 365 days]
The self-service `api-key user create` command always binds the key to your authenticated user and **requires** `--expires-at`, which may be at most 365 days from creation. The expiration cannot be cleared, and `api-key user update` cannot extend it beyond 365 days from the key's original creation date. After the window elapses, create a new key to rotate.

```bash
vamscli api-key user create --name "My Script" --description "Automation" --expires-at 2027-06-30T23:59:59Z
```

:::

### Missing Required Fields

**Symptoms:** `Error: Missing option '--name'.` (or `--user-id`, `--description`).

**Cause:** A required option was not supplied.

**Resolution:**

Admin `api-key create` requires `--name`, `--user-id`, and `--description`. The `api-key update` and `api-key user update` commands require `--api-key-id` plus at least one of `--description`, `--expires-at`, or `--is-active`.

```bash
vamscli api-key update --api-key-id UUID --description "New description"
```

### API Key Returns 401 or 403

**Symptoms:** API calls authenticated with a key return 401 or 403.

**Cause:** The key is malformed, inactive, expired, or its bound user lost its roles.

**Resolution:**

Work through the checks in order:

1. The key must be used exactly as displayed at creation — VAMS keys start with `vams_`. A `Bearer` prefix is also accepted.

    ```bash
    curl -H "Authorization: vams_AbCdEf..." https://your-vams-url/database
    curl -H "Authorization: Bearer vams_AbCdEf..." https://your-vams-url/database
    ```

2. Confirm the key is active (`isActive: "true"`) and not past its `expiresAt`.

    ```bash
    vamscli api-key list --json-output
    ```

3. Confirm the bound user still has roles (a `No roles for API key user` error indicates the assignments were removed after the key was created).

    ```bash
    vamscli role user list --json-output
    vamscli role user create -u user@example.com --role-name viewer
    ```

For an expired key, update the expiration (admin keys) or create a replacement.

```bash
vamscli api-key update --api-key-id UUID --expires-at 2028-12-31T23:59:59Z
```

### Capturing the Key Value in Scripts

The API key value is shown only once, at creation. Capture it with `--json-output`.

```bash
KEY_RESPONSE=$(vamscli api-key create \
  --name "Script Key" --user-id "bot@example.com" --description "Automated key" --json-output)

API_KEY=$(echo "$KEY_RESPONSE" | jq -r '.apiKey')
KEY_ID=$(echo "$KEY_RESPONSE" | jq -r '.apiKeyId')
```

---

## Permission and Authentication Errors

These errors apply to both role/constraint and API key management commands.

### Access Forbidden or Not Authorized

**Symptoms:**

-   `Authentication Error: Access forbidden. You do not have permission to perform this action.`
-   `Not Authorized` when managing roles, constraints, or API keys.

**Cause:** Your account lacks API-level (Tier 1) authorization for the management endpoints — for example, the `/auth/api-keys` route for API key commands, or the roles and constraints routes.

**Resolution:**

Confirm your authentication, then ask your VAMS administrator to grant your role access to the required API routes.

```bash
vamscli auth status
```

### Token Expired

**Symptoms:** `Authentication Error: Authentication token has expired.`

**Cause:** Your session token expired. Cognito tokens auto-refresh; override tokens do not and fail immediately on 401.

**Resolution:**

Re-authenticate, or set a fresh override token for external auth.

```bash
vamscli auth login -u your-username@example.com
```

:::tip
Run any command with `--verbose` to see API request and response detail, timing, and full error information when diagnosing permission or authentication problems.
:::

---

## Related Pages

-   [Roles, Constraints, and Permissions Commands](../commands/permissions.md)
-   [Users and API Keys Commands](../commands/users-and-keys.md)
-   [Setup and Authentication Troubleshooting](./setup-auth.md)
-   [General CLI Troubleshooting](./general.md)
