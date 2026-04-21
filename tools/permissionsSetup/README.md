# VAMS Permission Setup Tool

Automates the creation of VAMS roles and constraint imports from JSON permission templates using the `vamscli` CLI and the `POST /auth/constraintsTemplateImport` API.

## Overview

When adding a new VAMS database, administrators must create a complete set of roles and constraints covering all object types (database, asset, pipeline, workflow, metadata schema, tags, tag types, web routes, and API routes). This tool automates that process using JSON templates that define common permission profiles.

The tool orchestrates:

1. **Role creation** via `vamscli role create`
2. **Constraint import** via `vamscli role constraint template import`, which sends the template to the server-side API for variable substitution, UUID generation, and bulk constraint creation
3. **User assignment** via `vamscli role user create`

Pre-built JSON templates are located in `documentation/permissionsTemplates/`. For detailed explanations of what each template provides (constraint matrices, design decisions, GLOBAL keyword usage, etc.), see [documentation/PermissionsGuide.md](../../documentation/PermissionsGuide.md).

## Prerequisites

-   Python 3.9+
-   `vamscli` installed and configured (`vamscli setup` and `vamscli auth login` completed)

## Quick Start

```bash
# Apply a database admin profile for a new database
python apply_template.py \
  --template documentation/permissionsTemplates/database-admin.json \
  --role-name my-db-admin \
  --variables '{"DATABASE_ID": "my-new-database"}'

# Apply a database user profile
python apply_template.py \
  --template documentation/permissionsTemplates/database-user.json \
  --role-name my-db-user \
  --variables '{"DATABASE_ID": "my-new-database"}'

# Apply a read-only profile
python apply_template.py \
  --template documentation/permissionsTemplates/database-readonly.json \
  --role-name my-db-viewer \
  --variables '{"DATABASE_ID": "my-new-database"}'

# Preview what would be created (dry run)
python apply_template.py \
  --template documentation/permissionsTemplates/database-admin.json \
  --role-name my-db-admin \
  --variables '{"DATABASE_ID": "my-new-database"}' \
  --dry-run

# Delete a role
python apply_template.py \
  --template documentation/permissionsTemplates/database-admin.json \
  --role-name my-db-admin --delete

# Assign a user to the created role
python apply_template.py \
  --template documentation/permissionsTemplates/database-user.json \
  --role-name my-db-user \
  --variables '{"DATABASE_ID": "my-new-database"}' \
  --assign-user john.doe

# Add a tag-based deny overlay to an existing role
python apply_template.py \
  --template documentation/permissionsTemplates/deny-tagged-assets.json \
  --role-name my-db-admin \
  --var TAG_VALUE=locked

# Using a variables file
python apply_template.py \
  --template documentation/permissionsTemplates/database-admin.json \
  --role-name my-db-admin \
  --variables-file vars.json

# Custom role description and MFA
python apply_template.py \
  --template documentation/permissionsTemplates/database-admin.json \
  --role-name my-secure-admin \
  --variables '{"DATABASE_ID": "my-new-database"}' \
  --role-description "Secure admin for my-new-database" --mfa-required

# Use a custom vamscli profile
python apply_template.py \
  --template documentation/permissionsTemplates/database-admin.json \
  --role-name my-db-admin \
  --variables '{"DATABASE_ID": "my-new-database"}' \
  --profile production
```

## Variable Input

Templates define their required variables in the JSON `variables` array. Variable values are provided to the script using any combination of these methods (later sources override earlier ones):

| Priority | Flag                      | Format            | Description                      |
| -------- | ------------------------- | ----------------- | -------------------------------- |
| 1        | `--variables-file` / `-f` | Path to JSON file | Load all variables from a file   |
| 2        | `--variables` / `-V`      | JSON string       | Inline JSON object of variables  |
| 3        | `--var` / `-v`            | `KEY=VALUE`       | Individual variable (repeatable) |

`ROLE_NAME` is always set from `--role-name` and cannot be overridden via other variable inputs. If `ROLE_NAME` is provided in `--variables` or `--variables-file` and conflicts with `--role-name`, the script will error.

### Variables file example

```json
{
    "DATABASE_ID": "my-new-database"
}
```

## Operations

| Flag                      | Action                                                       |
| ------------------------- | ------------------------------------------------------------ |
| (default)                 | Creates role and imports all constraints via the API         |
| `--dry-run`               | Prints what would be created without making changes          |
| `--delete`                | Deletes the role (see note below)                            |
| `--assign-user USERID`    | Also assigns the specified user to the role                  |
| `--role-description TEXT` | Custom role description (default: template name + role name) |
| `--mfa-required`          | Require MFA for the created role                             |

### Delete mode

The `--delete` flag deletes the role only. Since constraint IDs are server-generated UUIDs, individual constraint deletion by name is no longer practical from this tool. After deleting a role, use the VAMS UI or `vamscli role constraint` commands to clean up orphaned constraints if needed. To recreate a role with updated constraints, delete the role and re-run the tool in create mode.

## How It Works

1. The script loads a JSON template file from the path provided via `--template`
2. Variable values from `--variables`, `--variables-file`, and `--var` are merged and injected into the template as `variableValues`
3. The role is created via `vamscli role create`
4. The complete template (with `variableValues`) is sent to `vamscli role constraint template import`, which calls the `POST /auth/constraintsTemplateImport` API
5. The API handles variable substitution, UUID generation, groupId assignment, and bulk constraint creation server-side
6. Optionally, a user is assigned to the role via `vamscli role user create`

## Further Reading

-   **[PermissionsGuide.md](../../documentation/PermissionsGuide.md)** - Detailed constraint matrices, design decisions, GLOBAL keyword usage, and the full JSON template format reference
-   **[VamsCLI Role Management](../VamsCLI/docs/commands/role-management.md)** - CLI commands for roles, constraints, and template imports
-   **[documentation/permissionsTemplates/](../../documentation/permissionsTemplates/)** - Pre-built JSON template files
