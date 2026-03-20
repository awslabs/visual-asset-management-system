---
sidebar_label: Permissions
title: Permission Commands
---

# Permission Commands

Manage roles, permission constraints, and user-role assignments. The `role` command contains three subgroups for role management, constraint management, and user-role assignment.

---

## Role Management

### role list

List all roles with optional pagination.

```bash
vamscli role list [--auto-paginate] [--page-size N] [--json-output]
```

### role create

Create a new role.

```bash
vamscli role create [OPTIONS]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `-r`, `--role-name` | TEXT | Yes | Role name |
| `--description` | TEXT | Conditional | Role description (required unless using `--json-input`) |
| `--source` | TEXT | No | Role source (e.g., `LDAP`) |
| `--source-identifier` | TEXT | No | Source identifier |
| `--mfa-required` | Flag | No | Enable MFA requirement |
| `--json-input` | TEXT | No | JSON input |
| `--json-output` | Flag | No | Output raw JSON |

```bash
vamscli role create -r admin --description "Administrator role"
vamscli role create -r secure-admin --description "Secure admin" --mfa-required
vamscli role create -r ldap-admin --description "LDAP admin" --source "LDAP" --source-identifier "cn=admin,dc=example"
```

### role update

Update an existing role.

```bash
vamscli role update -r admin --description "Updated description"
vamscli role update -r admin --mfa-required
vamscli role update -r admin --no-mfa-required
```

### role delete

Delete a role. Requires `--confirm`.

```bash
vamscli role delete -r old-role --confirm
```

---

## User-Role Assignment

### role user list

List all user-role assignments.

```bash
vamscli role user list [--auto-paginate] [--json-output]
```

### role user create

Assign roles to a user.

```bash
vamscli role user create -u user@example.com --role-name admin --role-name viewer
```

### role user update

Replace all roles for a user (differential update). Roles not in the new list are removed.

```bash
vamscli role user update -u user@example.com --role-name admin --role-name editor
```

### role user delete

Remove all roles from a user.

```bash
vamscli role user delete -u user@example.com --confirm
```

:::warning
This removes all role assignments. The user loses access to all resources granted through those roles.
:::

---

## Constraint Management

Constraints define fine-grained access control rules based on object properties.

### role constraint list

```bash
vamscli role constraint list [--auto-paginate] [--json-output]
```

### role constraint get

```bash
vamscli role constraint get -c my-constraint [--json-output]
```

### role constraint create

:::tip
Due to the complexity of constraint data, it is recommended to use `--json-input` for creating constraints.
:::

```bash
vamscli role constraint create -c my-constraint --json-input constraint.json
```

#### Constraint JSON structure

```json
{
    "identifier": "constraint-id",
    "name": "Constraint Name",
    "description": "Constraint description",
    "objectType": "asset",
    "criteriaAnd": [
        {"field": "databaseId", "operator": "equals", "value": "db1"}
    ],
    "criteriaOr": [
        {"field": "tags", "operator": "in", "value": ["tag1", "tag2"]}
    ],
    "groupPermissions": [
        {"groupId": "admin", "permission": "read", "permissionType": "allow"}
    ],
    "userPermissions": [
        {"userId": "user@example.com", "permission": "write", "permissionType": "allow"}
    ]
}
```

#### Criteria operators

| Operator | Description |
|---|---|
| `equals` | Exact match |
| `contains` | Substring match |
| `in` | Value in array |
| `startsWith` | Prefix match |
| `endsWith` | Suffix match |
| `regex` | Regular expression match |

### role constraint update

```bash
vamscli role constraint update -c my-constraint --json-input constraint-update.json
```

:::note
Updates replace the entire constraint. Use `--json-input` for complex updates to preserve existing criteria and permissions.
:::

### role constraint delete

```bash
vamscli role constraint delete -c old-constraint --confirm
```

---

## Constraint Template Import

Import multiple constraints from a pre-defined JSON permission template. Templates use variable placeholders (e.g., `\{\{DATABASE_ID\}\}`) that are substituted with values you provide.

```bash
vamscli role constraint template import -j ./database-admin.json
```

### Available templates

Pre-built templates are available in `documentation/permissionsTemplates/`:

| Template | Description |
|---|---|
| `database-admin.json` | Full admin access to a specific database |
| `database-user.json` | Standard user access (create, edit, view) |
| `database-readonly.json` | Read-only access to a specific database |
| `global-readonly.json` | Read-only access across all databases |
| `deny-tagged-assets.json` | Deny access to assets with specific tags |

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
                {"field": "databaseId", "operator": "equals", "value": "{{DATABASE_ID}}"}
            ],
            "groupPermissions": [
                {"action": "GET", "type": "allow"}
            ]
        }
    ]
}
```

### Template usage example

```bash
# Copy and customize a template
cp documentation/permissionsTemplates/database-admin.json my-template.json
# Edit my-template.json to add variableValues

# Import the template
vamscli role constraint template import -j my-template.json

# Verify
vamscli role constraint list --json-output
```

---

## Related Pages

- [Users and API Keys](users-and-keys.md)
- [Setup and Authentication](setup-and-auth.md)
- [Permissions User Guide](../../user-guide/permissions.md)
