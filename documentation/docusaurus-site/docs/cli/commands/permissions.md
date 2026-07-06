---
sidebar_label: Permissions
title: Permission Commands
---

# Permission Commands

Manage roles, permission constraints, and user-role assignments. The `role` command contains subgroups for role management (`role`), user-role assignment (`role user`), and constraint management (`role constraint`, with a nested `role constraint template` group).

:::note[Pagination options]
The `list` commands (`role list`, `role user list`, `role constraint list`) share a common set of pagination options: `--page-size`, `--max-items`, `--starting-token`, and `--auto-paginate`. `--max-items` applies only with `--auto-paginate` (default 10000) and is ignored otherwise. `--auto-paginate` and `--starting-token` are mutually exclusive.
:::

---

## role list

List all roles with optional pagination.

```bash
vamscli role list [OPTIONS]
```

| Option             | Type    | Required | Description                                                                |
| ------------------ | ------- | -------- | -------------------------------------------------------------------------- |
| `--page-size`      | INTEGER | No       | Number of items per page                                                   |
| `--max-items`      | INTEGER | No       | Maximum total items to fetch (only with `--auto-paginate`, default: 10000) |
| `--starting-token` | TEXT    | No       | Token for manual pagination                                                |
| `--auto-paginate`  | Flag    | No       | Automatically fetch all items                                              |
| `--json-output`    | Flag    | No       | Output raw JSON response                                                   |

```bash
vamscli role list
vamscli role list --auto-paginate --max-items 5000
vamscli role list --page-size 200
vamscli role list --json-output
```

---

## role create

Create a new role.

```bash
vamscli role create [OPTIONS]
```

| Option                | Type | Required    | Description                                             |
| --------------------- | ---- | ----------- | ------------------------------------------------------- |
| `-r`, `--role-name`   | TEXT | Yes         | Role name to create                                     |
| `--description`       | TEXT | Conditional | Role description (required unless using `--json-input`) |
| `--source`            | TEXT | No          | Role source (e.g., `LDAP`)                              |
| `--source-identifier` | TEXT | No          | Source identifier                                       |
| `--mfa-required`      | Flag | No          | Enable MFA requirement                                  |
| `--json-input`        | TEXT | No          | JSON input file path or JSON string with all role data  |
| `--json-output`       | Flag | No          | Output raw JSON response                                |

When `--json-input` is provided, `--role-name` overrides the `roleName` field in the JSON.

```bash
vamscli role create -r admin --description "Administrator role"
vamscli role create -r secure-admin --description "Secure admin" --mfa-required
vamscli role create -r ldap-admin --description "LDAP admin" --source "LDAP" --source-identifier "cn=admin,dc=example"
vamscli role create -r admin --json-input '{"description":"Admin role","mfaRequired":true}'
```

---

## role update

Update an existing role. At least one field must be provided.

```bash
vamscli role update [OPTIONS]
```

| Option                | Type | Required | Description                         |
| --------------------- | ---- | -------- | ----------------------------------- |
| `-r`, `--role-name`   | TEXT | Yes      | Role name to update                 |
| `--description`       | TEXT | No       | New role description                |
| `--source`            | TEXT | No       | New source                          |
| `--source-identifier` | TEXT | No       | New source identifier               |
| `--mfa-required`      | Flag | No       | Enable MFA requirement              |
| `--no-mfa-required`   | Flag | No       | Disable MFA requirement             |
| `--json-input`        | TEXT | No       | JSON input file path or JSON string |
| `--json-output`       | Flag | No       | Output raw JSON response            |

```bash
vamscli role update -r admin --description "Updated description"
vamscli role update -r admin --mfa-required
vamscli role update -r admin --no-mfa-required
```

:::note
`--mfa-required` and `--no-mfa-required` are mutually exclusive. When not using `--json-input`, at least one of `--description`, `--source`, `--source-identifier`, `--mfa-required`, or `--no-mfa-required` must be supplied.
:::

---

## role delete

Delete a role. Requires `--confirm`.

```bash
vamscli role delete [OPTIONS]
```

| Option              | Type | Required | Description              |
| ------------------- | ---- | -------- | ------------------------ |
| `-r`, `--role-name` | TEXT | Yes      | Role name to delete      |
| `--confirm`         | Flag | Yes      | Confirm role deletion    |
| `--json-output`     | Flag | No       | Output raw JSON response |

```bash
vamscli role delete -r old-role --confirm
```

:::note
In interactive (non-JSON) mode, an additional confirmation prompt appears even after `--confirm`. The backend automatically cleans up any user-role assignments for the deleted role.
:::

---

## role user list

List all user-role assignments, grouped by user ID, with optional pagination.

```bash
vamscli role user list [OPTIONS]
```

| Option             | Type    | Required | Description                                                                |
| ------------------ | ------- | -------- | -------------------------------------------------------------------------- |
| `--page-size`      | INTEGER | No       | Number of items per page                                                   |
| `--max-items`      | INTEGER | No       | Maximum total items to fetch (only with `--auto-paginate`, default: 10000) |
| `--starting-token` | TEXT    | No       | Token for manual pagination                                                |
| `--auto-paginate`  | Flag    | No       | Automatically fetch all items                                              |
| `--json-output`    | Flag    | No       | Output raw JSON response                                                   |

```bash
vamscli role user list
vamscli role user list --auto-paginate --json-output
```

---

## role user create

Assign one or more roles to a user.

```bash
vamscli role user create [OPTIONS]
```

| Option            | Type | Required    | Description                                                      |
| ----------------- | ---- | ----------- | ---------------------------------------------------------------- |
| `-u`, `--user-id` | TEXT | Yes         | User ID to assign roles to                                       |
| `--role-name`     | TEXT | Conditional | Role name to assign (repeatable; required unless `--json-input`) |
| `--json-input`    | TEXT | No          | JSON input file path or JSON string with user role data          |
| `--json-output`   | Flag | No          | Output raw JSON response                                         |

```bash
vamscli role user create -u user@example.com --role-name admin
vamscli role user create -u user@example.com --role-name admin --role-name viewer
vamscli role user create -u user@example.com --json-input '{"roleName":["admin","viewer"]}'
```

The user role JSON structure is:

```json
{
    "userId": "user@example.com",
    "roleName": ["admin", "viewer", "editor"]
}
```

---

## role user update

Replace all roles for a user (differential update). Roles not in the new list are removed, and new roles are added.

```bash
vamscli role user update [OPTIONS]
```

| Option            | Type | Required    | Description                                                      |
| ----------------- | ---- | ----------- | ---------------------------------------------------------------- |
| `-u`, `--user-id` | TEXT | Yes         | User ID to update roles for                                      |
| `--role-name`     | TEXT | Conditional | Role name to assign (repeatable; required unless `--json-input`) |
| `--json-input`    | TEXT | No          | JSON input file path or JSON string with user role data          |
| `--json-output`   | Flag | No          | Output raw JSON response                                         |

```bash
vamscli role user update -u user@example.com --role-name admin --role-name editor
```

:::note
The update is differential. If a user currently has `admin`, `viewer`, `editor` and you update to `admin`, `viewer`, the `editor` role is removed while `admin` and `viewer` are kept.
:::

---

## role user delete

Remove all roles from a user. Requires `--confirm`.

```bash
vamscli role user delete [OPTIONS]
```

| Option            | Type | Required | Description                      |
| ----------------- | ---- | -------- | -------------------------------- |
| `-u`, `--user-id` | TEXT | Yes      | User ID to remove all roles from |
| `--confirm`       | Flag | Yes      | Confirm user-role deletion       |
| `--json-output`   | Flag | No       | Output raw JSON response         |

```bash
vamscli role user delete -u user@example.com --confirm
```

:::warning
This removes all role assignments for the user. The user loses access to all resources granted through those roles. In interactive (non-JSON) mode, an additional confirmation prompt appears even after `--confirm`.
:::

---

## role constraint list

List all constraints with optional pagination.

```bash
vamscli role constraint list [OPTIONS]
```

| Option             | Type    | Required | Description                                                                |
| ------------------ | ------- | -------- | -------------------------------------------------------------------------- |
| `--page-size`      | INTEGER | No       | Number of items per page                                                   |
| `--max-items`      | INTEGER | No       | Maximum total items to fetch (only with `--auto-paginate`, default: 10000) |
| `--starting-token` | TEXT    | No       | Token for manual pagination                                                |
| `--auto-paginate`  | Flag    | No       | Automatically fetch all items                                              |
| `--json-output`    | Flag    | No       | Output raw JSON response                                                   |

```bash
vamscli role constraint list
vamscli role constraint list --auto-paginate --json-output
```

---

## role constraint get

Get details for a specific constraint, including all criteria, group permissions, and user permissions.

```bash
vamscli role constraint get [OPTIONS]
```

| Option                  | Type | Required | Description               |
| ----------------------- | ---- | -------- | ------------------------- |
| `-c`, `--constraint-id` | TEXT | Yes      | Constraint ID to retrieve |
| `--json-output`         | Flag | No       | Output raw JSON response  |

```bash
vamscli role constraint get -c my-constraint
vamscli role constraint get -c my-constraint --json-output
```

---

## role constraint create

Create a new constraint.

```bash
vamscli role constraint create [OPTIONS]
```

| Option                  | Type | Required    | Description                                                   |
| ----------------------- | ---- | ----------- | ------------------------------------------------------------- |
| `-c`, `--constraint-id` | TEXT | Yes         | Constraint ID to create                                       |
| `--name`                | TEXT | Conditional | Constraint name (required unless using `--json-input`)        |
| `--description`         | TEXT | Conditional | Constraint description (required unless using `--json-input`) |
| `--object-type`         | TEXT | Conditional | Object type (required unless using `--json-input`)            |
| `--json-input`          | TEXT | No          | JSON input file path or JSON string with all constraint data  |
| `--json-output`         | Flag | No          | Output raw JSON response                                      |

:::tip
Due to the complexity of constraint data, use `--json-input` for any constraint that includes criteria or permissions. Without `--json-input`, the `--name`, `--description`, and `--object-type` options create a constraint with empty criteria and permission arrays.
:::

```bash
vamscli role constraint create -c my-constraint --json-input constraint.json
vamscli role constraint create -c simple-constraint --name "My Constraint" --description "Test" --object-type asset
```

When `--json-input` is provided, `--constraint-id` overrides the `identifier` field in the JSON.

### Constraint JSON structure

```json
{
    "identifier": "constraint-id",
    "name": "Constraint Name",
    "description": "Constraint description",
    "objectType": "asset",
    "criteriaAnd": [{ "field": "databaseId", "operator": "equals", "value": "db1" }],
    "criteriaOr": [{ "field": "tags", "operator": "in", "value": ["tag1", "tag2"] }],
    "groupPermissions": [{ "groupId": "admin", "permission": "read", "permissionType": "allow" }],
    "userPermissions": [
        { "userId": "user@example.com", "permission": "write", "permissionType": "allow" }
    ]
}
```

-   `criteriaAnd` — array of conditions that must all match.
-   `criteriaOr` — array of conditions where at least one must match.
-   `groupPermissions` / `userPermissions` — `permissionType` is `allow` or `deny`.

### Criteria operators

| Operator     | Description              |
| ------------ | ------------------------ |
| `equals`     | Exact match              |
| `contains`   | Substring match          |
| `in`         | Value in array           |
| `startsWith` | Prefix match             |
| `endsWith`   | Suffix match             |
| `regex`      | Regular expression match |

:::tip
Use `role constraint permission-objects` to retrieve the deployment's valid object types, criteria fields, operators, permissions, and permission types before authoring a constraint.
:::

---

## role constraint update

Update an existing constraint. The update replaces the entire constraint.

```bash
vamscli role constraint update [OPTIONS]
```

| Option                  | Type | Required | Description                         |
| ----------------------- | ---- | -------- | ----------------------------------- |
| `-c`, `--constraint-id` | TEXT | Yes      | Constraint ID to update             |
| `--name`                | TEXT | No       | New constraint name                 |
| `--description`         | TEXT | No       | New constraint description          |
| `--object-type`         | TEXT | No       | New object type                     |
| `--json-input`          | TEXT | No       | JSON input file path or JSON string |
| `--json-output`         | Flag | No       | Output raw JSON response            |

```bash
vamscli role constraint update -c my-constraint --json-input constraint-update.json
vamscli role constraint update -c my-constraint --name "Updated Name" --description "Updated description"
```

:::note
When `--json-input` is omitted, the CLI first retrieves the existing constraint, applies the supplied `--name`, `--description`, or `--object-type` updates, and sends the merged result — preserving existing criteria and permissions. At least one of these fields must be provided. Use `--json-input` for complex changes to criteria or permissions.
:::

---

## role constraint delete

Delete a constraint and all its associated permissions. Requires `--confirm`.

```bash
vamscli role constraint delete [OPTIONS]
```

| Option                  | Type | Required | Description                 |
| ----------------------- | ---- | -------- | --------------------------- |
| `-c`, `--constraint-id` | TEXT | Yes      | Constraint ID to delete     |
| `--confirm`             | Flag | Yes      | Confirm constraint deletion |
| `--json-output`         | Flag | No       | Output raw JSON response    |

```bash
vamscli role constraint delete -c old-constraint --confirm
```

:::warning
This permanently removes the constraint and all associated permissions. In interactive (non-JSON) mode, an additional confirmation prompt appears even after `--confirm`.
:::

---

## role constraint permission-objects

List the constraint object types and their valid fields, the criteria operators, the permissions (HTTP actions), and the permission types. This returns the deployment's master mapping used when authoring constraints.

```bash
vamscli role constraint permission-objects [OPTIONS]
```

| Option          | Type | Required | Description              |
| --------------- | ---- | -------- | ------------------------ |
| `--json-output` | Flag | No       | Output raw JSON response |

```bash
vamscli role constraint permission-objects
vamscli role constraint permission-objects --json-output
```

---

## role constraint template import

Import multiple constraints from a pre-defined JSON permission template. Templates use variable placeholders (e.g., `\{\{DATABASE_ID\}\}`) that are substituted with the values you provide in `variableValues`.

```bash
vamscli role constraint template import [OPTIONS]
```

| Option               | Type | Required | Description                                           |
| -------------------- | ---- | -------- | ----------------------------------------------------- |
| `-j`, `--json-input` | TEXT | Yes      | Template JSON data as a string or path to a JSON file |
| `--json-output`      | Flag | No       | Output raw JSON response                              |

```bash
vamscli role constraint template import -j ./database-admin.json
vamscli role constraint template import -j ./database-admin.json --json-output
```

:::note
The template must include a `variableValues` object containing `ROLE_NAME` and a non-empty `constraints` array. `ROLE_NAME` is used as the `groupId` for all created constraint permissions. Imports missing these fields are rejected before any API call.
:::

### Available templates

Pre-built templates are available in `documentation/permissionsTemplates/`:

| Template                  | Description                               |
| ------------------------- | ----------------------------------------- |
| `database-admin.json`     | Full admin access to a specific database  |
| `database-user.json`      | Standard user access (create, edit, view) |
| `database-readonly.json`  | Read-only access to a specific database   |
| `global-readonly.json`    | Read-only access across all databases     |
| `deny-tagged-assets.json` | Deny access to assets with specific tags  |

### Template JSON format

```json
{
    "metadata": {
        "name": "Database Admin",
        "description": "Full admin access to a database",
        "version": "1.0"
    },
    "variableValues": {
        "ROLE_NAME": "my-db-admin",
        "DATABASE_ID": "my-database-id"
    },
    "constraints": [
        {
            "name": "{{ROLE_NAME}}-asset-access",
            "description": "Allow asset access in {{DATABASE_ID}}",
            "objectType": "asset",
            "criteriaAnd": [
                { "field": "databaseId", "operator": "equals", "value": "{{DATABASE_ID}}" }
            ],
            "groupPermissions": [
                { "action": "GET", "type": "allow" },
                { "action": "PUT", "type": "allow" }
            ]
        }
    ]
}
```

### Template usage example

```bash
# Copy and customize a template, then add variableValues (must include ROLE_NAME)
cp documentation/permissionsTemplates/database-admin.json my-template.json

# Import the template
vamscli role constraint template import -j my-template.json

# Verify
vamscli role constraint list --json-output
```

---

## Related Pages

-   [Users and API Keys](users-and-keys.md)
-   [Setup and Authentication](setup-and-auth.md)
-   [Permissions User Guide](../../user-guide/permissions.md)
