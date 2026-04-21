# Role Management Commands

This guide covers the role management commands in VamsCLI, which allow you to create, list, update, and delete roles in your VAMS deployment.

## Table of Contents

-   [Overview](#overview)
-   [Prerequisites](#prerequisites)
-   [Role Commands](#role-commands)
    -   [role list](#role-list)
    -   [role create](#role-create)
    -   [role update](#role-update)
    -   [role delete](#role-delete)
-   [User Role Commands](#user-role-commands)
    -   [role user list](#role-user-list)
    -   [role user create](#role-user-create)
    -   [role user update](#role-user-update)
    -   [role user delete](#role-user-delete)
-   [Constraint Commands](#constraint-commands)
    -   [role constraint list](#role-constraint-list)
    -   [role constraint get](#role-constraint-get)
    -   [role constraint create](#role-constraint-create)
    -   [role constraint update](#role-constraint-update)
    -   [role constraint delete](#role-constraint-delete)
-   [Constraint Template Commands](#constraint-template-commands)
    -   [role constraint template import](#role-constraint-template-import)
-   [Common Workflows](#common-workflows)
-   [JSON Input/Output](#json-inputoutput)
-   [Best Practices](#best-practices)

## Overview

Roles in VAMS define sets of permissions that can be assigned to users. The role management commands allow administrators to:

-   List all available roles
-   Create new roles with specific configurations
-   Update existing role properties
-   Delete roles that are no longer needed

## Prerequisites

Before using role management commands, ensure you have:

1. **Completed Setup**: Run `vamscli setup <api-gateway-url>`
2. **Authenticated**: Run `vamscli auth login -u <username>`
3. **Appropriate Permissions**: Your user account must have permissions to manage roles

## Commands

### role list

List all roles in the VAMS system with optional pagination.

#### Basic Usage

```bash
# List all roles (uses API defaults)
vamscli role list

# List with JSON output
vamscli role list --json-output
```

#### Pagination Options

```bash
# Auto-pagination to fetch all items (default: up to 10,000)
vamscli role list --auto-paginate

# Auto-pagination with custom limit
vamscli role list --auto-paginate --max-items 5000

# Auto-pagination with custom page size
vamscli role list --auto-paginate --page-size 500

# Manual pagination with page size
vamscli role list --page-size 200

# Continue from a specific token
vamscli role list --starting-token "token123" --page-size 200
```

#### Options

-   `--page-size INTEGER`: Number of items per page
-   `--max-items INTEGER`: Maximum total items to fetch (only with --auto-paginate, default: 10000)
-   `--starting-token TEXT`: Token for pagination (manual pagination)
-   `--auto-paginate`: Automatically fetch all items
-   `--json-output`: Output raw JSON response

#### Example Output

```
Found 3 role(s):
--------------------------------------------------------------------------------
Role Name: admin
Description: Administrator role
ID: role-uuid-1
Created On: 2024-01-01T00:00:00
MFA Required: True
--------------------------------------------------------------------------------
Role Name: viewer
Description: Read-only access
ID: role-uuid-2
Created On: 2024-01-02T00:00:00
MFA Required: False
--------------------------------------------------------------------------------
```

### role create

Create a new role in VAMS.

#### Basic Usage

```bash
# Create a basic role
vamscli role create -r admin --description "Administrator role"

# Create a role with MFA requirement
vamscli role create -r secure-admin --description "Secure admin" --mfa-required

# Create a role with source information
vamscli role create -r ldap-admin \
  --description "LDAP administrator" \
  --source "LDAP" \
  --source-identifier "cn=admin,dc=example,dc=com"
```

#### JSON Input

```bash
# Create from JSON string
vamscli role create -r admin --json-input '{"roleName":"admin","description":"Admin role","mfaRequired":true}'

# Create from JSON file
vamscli role create -r admin --json-input role.json
```

Example `role.json`:

```json
{
    "roleName": "admin",
    "description": "Administrator role",
    "source": "LDAP",
    "sourceIdentifier": "cn=admin,dc=example,dc=com",
    "mfaRequired": true
}
```

#### Options

-   `-r, --role-name TEXT`: Role name to create (required)
-   `--description TEXT`: Role description (required unless using --json-input)
-   `--source TEXT`: Role source (optional)
-   `--source-identifier TEXT`: Source identifier (optional)
-   `--mfa-required`: Enable MFA requirement
-   `--json-input TEXT`: JSON input file path or JSON string with all role data
-   `--json-output`: Output raw JSON response

#### Example Output

```
✓ Role created successfully!
  Role Name: admin
  Message: Role admin created successfully
  Timestamp: 2024-01-01T00:00:00
```

### role update

Update an existing role in VAMS.

#### Basic Usage

```bash
# Update role description
vamscli role update -r admin --description "Updated description"

# Enable MFA requirement
vamscli role update -r admin --mfa-required

# Disable MFA requirement
vamscli role update -r admin --no-mfa-required

# Update source information
vamscli role update -r admin \
  --source "LDAP" \
  --source-identifier "cn=admin,dc=example,dc=com"

# Update multiple fields
vamscli role update -r admin \
  --description "Updated admin role" \
  --mfa-required
```

#### JSON Input

```bash
# Update from JSON string
vamscli role update -r admin --json-input '{"roleName":"admin","description":"Updated","mfaRequired":true}'

# Update from JSON file
vamscli role update -r admin --json-input role-update.json
```

#### Options

-   `-r, --role-name TEXT`: Role name to update (required)
-   `--description TEXT`: New role description
-   `--source TEXT`: New source
-   `--source-identifier TEXT`: New source identifier
-   `--mfa-required`: Enable MFA requirement
-   `--no-mfa-required`: Disable MFA requirement
-   `--json-input TEXT`: JSON input file path or JSON string with update data
-   `--json-output`: Output raw JSON response

#### Example Output

```
✓ Role updated successfully!
  Role Name: admin
  Message: Role admin updated successfully
  Timestamp: 2024-01-01T00:00:00
```

### role delete

Delete a role from VAMS.

⚠️ **WARNING**: This action will delete the role and cannot be undone!

#### Basic Usage

```bash
# Delete a role (requires confirmation)
vamscli role delete -r old-role --confirm

# Delete with JSON output
vamscli role delete -r old-role --confirm --json-output
```

#### Safety Features

1. **Confirmation Flag Required**: The `--confirm` flag must be provided
2. **Interactive Confirmation**: An additional confirmation prompt appears (unless using --json-output)
3. **Automatic Cleanup**: The backend automatically cleans up any user role assignments

#### Options

-   `-r, --role-name TEXT`: Role name to delete (required)
-   `--confirm`: Confirm role deletion (required)
-   `--json-output`: Output raw JSON response

#### Example Output

```
⚠️  You are about to delete role 'old-role'
This action cannot be undone!
Are you sure you want to proceed? [y/N]: y

✓ Role deleted successfully!
  Role Name: old-role
  Message: Role deleted
```

## User Role Commands

User role commands manage the assignment of roles to users in VAMS. These commands allow you to assign multiple roles to users, update role assignments, and remove all roles from a user.

### role user list

List all user role assignments in the VAMS system with optional pagination.

#### Basic Usage

```bash
# List all user role assignments (uses API defaults)
vamscli role user list

# List with JSON output
vamscli role user list --json-output
```

#### Pagination Options

```bash
# Auto-pagination to fetch all items (default: up to 10,000)
vamscli role user list --auto-paginate

# Auto-pagination with custom limit
vamscli role user list --auto-paginate --max-items 5000

# Auto-pagination with custom page size
vamscli role user list --auto-paginate --page-size 500

# Manual pagination with page size
vamscli role user list --page-size 200

# Continue from a specific token
vamscli role user list --starting-token "token123" --page-size 200
```

#### Options

-   `--page-size INTEGER`: Number of items per page
-   `--max-items INTEGER`: Maximum total items to fetch (only with --auto-paginate, default: 10000)
-   `--starting-token TEXT`: Token for pagination (manual pagination)
-   `--auto-paginate`: Automatically fetch all items
-   `--json-output`: Output raw JSON response

#### Example Output

```
Found 2 user role assignment(s):
--------------------------------------------------------------------------------
User ID: user1@example.com
Roles (2):
  [1] admin
  [2] viewer
Created On: 2024-01-01T00:00:00Z
--------------------------------------------------------------------------------
User ID: user2@example.com
Roles (1):
  [1] viewer
Created On: 2024-01-02T00:00:00Z
--------------------------------------------------------------------------------
```

### role user create

Assign roles to a user in VAMS.

#### Basic Usage

```bash
# Assign a single role to a user
vamscli role user create -u user@example.com --role-name admin

# Assign multiple roles to a user
vamscli role user create -u user@example.com \
  --role-name admin \
  --role-name viewer \
  --role-name editor
```

#### JSON Input

```bash
# Create from JSON string
vamscli role user create -u user@example.com --json-input '{"roleName":["admin","viewer"]}'

# Create from JSON file
vamscli role user create -u user@example.com --json-input user-roles.json
```

Example `user-roles.json`:

```json
{
    "userId": "user@example.com",
    "roleName": ["admin", "viewer", "editor"]
}
```

#### Options

-   `-u, --user-id TEXT`: User ID to assign roles to (required)
-   `--role-name TEXT`: Role name(s) to assign (can be specified multiple times)
-   `--json-input TEXT`: JSON input file path or JSON string with user role data
-   `--json-output`: Output raw JSON response

#### Example Output

```
✓ User roles assigned successfully!
  User ID: user@example.com
  Message: User roles created successfully
  Timestamp: 2024-01-01T00:00:00Z
  Operation: create
```

### role user update

Update roles for a user (differential update).

⚠️ **Note**: This performs a differential update - roles not in the new list are removed, and new roles are added.

#### Basic Usage

```bash
# Update to a single role (removes all other roles)
vamscli role user update -u user@example.com --role-name admin

# Update to multiple roles
vamscli role user update -u user@example.com \
  --role-name admin \
  --role-name viewer
```

#### JSON Input

```bash
# Update from JSON string
vamscli role user update -u user@example.com --json-input '{"roleName":["admin","viewer"]}'

# Update from JSON file
vamscli role user update -u user@example.com --json-input user-roles.json
```

#### Differential Update Behavior

The update command performs a differential update:

-   **Roles to Add**: New roles in the list that the user doesn't have
-   **Roles to Remove**: Existing roles not in the new list
-   **Roles to Keep**: Roles that are in both the existing and new lists

Example:

```bash
# User currently has: admin, viewer, editor
# Update to: admin, viewer
vamscli role user update -u user@example.com --role-name admin --role-name viewer
# Result: editor role is removed, admin and viewer are kept
```

#### Options

-   `-u, --user-id TEXT`: User ID to update roles for (required)
-   `--role-name TEXT`: Role name(s) to assign (can be specified multiple times)
-   `--json-input TEXT`: JSON input file path or JSON string with user role data
-   `--json-output`: Output raw JSON response

#### Example Output

```
✓ User roles updated successfully!
  User ID: user@example.com
  Message: User roles updated successfully
  Timestamp: 2024-01-01T00:00:00Z
  Operation: update
```

### role user delete

Delete all roles for a user.

⚠️ **WARNING**: This action will remove ALL role assignments for the user!

#### Basic Usage

```bash
# Delete all roles for a user (requires confirmation)
vamscli role user delete -u user@example.com --confirm

# Delete with JSON output
vamscli role user delete -u user@example.com --confirm --json-output
```

#### Safety Features

1. **Confirmation Flag Required**: The `--confirm` flag must be provided
2. **Interactive Confirmation**: An additional confirmation prompt appears (unless using --json-output)
3. **Complete Removal**: All role assignments for the user are permanently removed
4. **Access Revocation**: The user will lose access to all resources granted through these roles

#### Options

-   `-u, --user-id TEXT`: User ID to remove all roles from (required)
-   `--confirm`: Confirm user role deletion (required)
-   `--json-output`: Output raw JSON response

#### Example Output

```
⚠️  You are about to delete ALL roles for user 'user@example.com'
This action cannot be undone!
Are you sure you want to proceed? [y/N]: y

✓ User roles deleted successfully!
  User ID: user@example.com
  Message: User roles deleted successfully
  Timestamp: 2024-01-01T00:00:00Z
  Operation: delete
```

## Constraint Commands

Constraints in VAMS define fine-grained access control rules based on object properties and user/group permissions. Constraints allow you to restrict access to specific assets, files, or other objects based on criteria like database ID, asset type, tags, and more.

### role constraint list

List all constraints in the VAMS system with optional pagination.

#### Basic Usage

```bash
# List all constraints (uses API defaults)
vamscli role constraint list

# List with JSON output
vamscli role constraint list --json-output
```

#### Pagination Options

```bash
# Auto-pagination to fetch all items (default: up to 10,000)
vamscli role constraint list --auto-paginate

# Auto-pagination with custom limit
vamscli role constraint list --auto-paginate --max-items 5000

# Auto-pagination with custom page size
vamscli role constraint list --auto-paginate --page-size 500

# Manual pagination with page size
vamscli role constraint list --page-size 200

# Continue from a specific token
vamscli role constraint list --starting-token "token123" --page-size 200
```

#### Options

-   `--page-size INTEGER`: Number of items per page
-   `--max-items INTEGER`: Maximum total items to fetch (only with --auto-paginate, default: 10000)
-   `--starting-token TEXT`: Token for pagination (manual pagination)
-   `--auto-paginate`: Automatically fetch all items
-   `--json-output`: Output raw JSON response

#### Example Output

```
Found 2 constraint(s):
--------------------------------------------------------------------------------
Constraint ID: db-access-constraint
Name: Database Access Control
Description: Restrict access to specific databases
Object Type: asset
Criteria AND: 2 condition(s)
Group Permissions: 1
--------------------------------------------------------------------------------
Constraint ID: file-type-constraint
Name: File Type Restriction
Description: Restrict access to specific file types
Object Type: file
Criteria OR: 3 condition(s)
User Permissions: 2
--------------------------------------------------------------------------------
```

### role constraint get

Get detailed information about a specific constraint.

#### Basic Usage

```bash
# Get constraint details
vamscli role constraint get -c my-constraint

# Get with JSON output
vamscli role constraint get -c my-constraint --json-output
```

#### Options

-   `-c, --constraint-id TEXT`: Constraint ID to retrieve (required)
-   `--json-output`: Output raw JSON response

#### Example Output

```
Constraint Details:
  Constraint ID: db-access-constraint
  Name: Database Access Control
  Description: Restrict access to specific databases
  Object Type: asset
  Criteria AND (2 conditions):
    [1] databaseId equals db1
    [2] assetType contains model
  Group Permissions (1):
    [1] admin: read (allow)
  User Permissions (2):
    [1] user1@example.com: write (allow)
    [2] user2@example.com: read (allow)
  Date Created: 2024-01-01T00:00:00
  Date Modified: 2024-01-02T00:00:00
  Created By: admin@example.com
  Modified By: admin@example.com
```

### role constraint create

Create a new constraint in VAMS.

⚠️ **Note**: Due to the complexity of constraint data (criteria, permissions), it's recommended to use `--json-input` for creating constraints.

#### Basic Usage (Simple Constraint)

```bash
# Create a basic constraint with CLI options
vamscli role constraint create \
  -c my-constraint \
  --name "My Constraint" \
  --description "Test constraint" \
  --object-type "asset"
```

#### JSON Input (Recommended)

```bash
# Create from JSON file
vamscli role constraint create -c my-constraint --json-input constraint.json

# Create from JSON string
vamscli role constraint create -c my-constraint --json-input '{
  "name": "Database Access",
  "description": "Restrict to specific database",
  "objectType": "asset",
  "criteriaAnd": [
    {"field": "databaseId", "operator": "equals", "value": "db1"}
  ],
  "groupPermissions": [
    {"groupId": "admin", "permission": "read", "permissionType": "allow"}
  ]
}'
```

#### Constraint JSON Structure

```json
{
    "identifier": "constraint-id",
    "name": "Constraint Name",
    "description": "Constraint description",
    "objectType": "asset",
    "criteriaAnd": [
        {
            "field": "databaseId",
            "operator": "equals",
            "value": "db1"
        },
        {
            "field": "assetType",
            "operator": "contains",
            "value": "model"
        }
    ],
    "criteriaOr": [
        {
            "field": "tags",
            "operator": "in",
            "value": ["tag1", "tag2"]
        }
    ],
    "groupPermissions": [
        {
            "groupId": "admin",
            "permission": "read",
            "permissionType": "allow"
        },
        {
            "groupId": "viewer",
            "permission": "read",
            "permissionType": "deny"
        }
    ],
    "userPermissions": [
        {
            "userId": "user@example.com",
            "permission": "write",
            "permissionType": "allow"
        }
    ]
}
```

#### Constraint Fields

-   **identifier**: Unique constraint ID
-   **name**: Human-readable constraint name
-   **description**: Detailed description of the constraint
-   **objectType**: Type of object to constrain (e.g., "asset", "file")
-   **criteriaAnd**: Array of AND conditions (all must match)
-   **criteriaOr**: Array of OR conditions (at least one must match)
-   **groupPermissions**: Array of group-based permissions
-   **userPermissions**: Array of user-specific permissions

#### Criteria Operators

Common operators include:

-   `equals`: Exact match
-   `contains`: Substring match
-   `in`: Value in array
-   `startsWith`: Prefix match
-   `endsWith`: Suffix match
-   `regex`: Regular expression match

#### Permission Types

-   `allow`: Grant permission
-   `deny`: Explicitly deny permission

#### Options

-   `-c, --constraint-id TEXT`: Constraint ID to create (required)
-   `--name TEXT`: Constraint name (required unless using --json-input)
-   `--description TEXT`: Constraint description (required unless using --json-input)
-   `--object-type TEXT`: Object type (required unless using --json-input)
-   `--json-input TEXT`: JSON input file path or JSON string with all constraint data
-   `--json-output`: Output raw JSON response

#### Example Output

```
✓ Constraint created successfully!
  Constraint ID: my-constraint
  Message: Constraint my-constraint created/updated successfully
  Timestamp: 2024-01-01T00:00:00
  Operation: create
```

### role constraint update

Update an existing constraint in VAMS.

⚠️ **Note**: Updates replace the entire constraint. Use `--json-input` for complex updates to preserve existing criteria and permissions.

#### Basic Usage

```bash
# Update basic fields
vamscli role constraint update \
  -c my-constraint \
  --name "Updated Name" \
  --description "Updated description"

# Update from JSON file (recommended)
vamscli role constraint update -c my-constraint --json-input constraint-update.json
```

#### JSON Input (Recommended)

```bash
# Update from JSON string
vamscli role constraint update -c my-constraint --json-input '{
  "name": "Updated Constraint",
  "description": "Updated description",
  "objectType": "asset",
  "criteriaAnd": [
    {"field": "databaseId", "operator": "equals", "value": "db2"}
  ],
  "groupPermissions": [
    {"groupId": "admin", "permission": "read", "permissionType": "allow"}
  ]
}'
```

#### Update Workflow

When updating without `--json-input`:

1. CLI retrieves the existing constraint
2. Applies your specified updates
3. Sends the complete updated constraint to the API

This preserves existing criteria and permissions while updating specified fields.

#### Options

-   `-c, --constraint-id TEXT`: Constraint ID to update (required)
-   `--name TEXT`: New constraint name
-   `--description TEXT`: New constraint description
-   `--object-type TEXT`: New object type
-   `--json-input TEXT`: JSON input file path or JSON string with update data
-   `--json-output`: Output raw JSON response

#### Example Output

```
✓ Constraint updated successfully!
  Constraint ID: my-constraint
  Message: Constraint my-constraint created/updated successfully
  Timestamp: 2024-01-01T00:00:00
  Operation: update
```

### role constraint delete

Delete a constraint from VAMS.

⚠️ **WARNING**: This action will delete the constraint and all associated permissions!

#### Basic Usage

```bash
# Delete a constraint (requires confirmation)
vamscli role constraint delete -c old-constraint --confirm

# Delete with JSON output
vamscli role constraint delete -c old-constraint --confirm --json-output
```

#### Safety Features

1. **Confirmation Flag Required**: The `--confirm` flag must be provided
2. **Interactive Confirmation**: An additional confirmation prompt appears (unless using --json-output)
3. **Permanent Deletion**: All associated permissions are permanently removed

#### Options

-   `-c, --constraint-id TEXT`: Constraint ID to delete (required)
-   `--confirm`: Confirm constraint deletion (required)
-   `--json-output`: Output raw JSON response

#### Example Output

```
⚠️  You are about to delete constraint 'old-constraint'
This action cannot be undone!
Are you sure you want to proceed? [y/N]: y

✓ Constraint deleted successfully!
  Constraint ID: old-constraint
  Message: Constraint old-constraint deleted successfully
  Timestamp: 2024-01-01T00:00:00
```

## Constraint Template Commands

Constraint templates allow you to import multiple constraints at once from a pre-defined JSON template. Templates use variable placeholders (e.g., `{{DATABASE_ID}}`) that are substituted with values you provide. This is useful for setting up standard permission patterns for roles.

Pre-built templates are available in `documentation/permissionsTemplates/`:

-   `database-admin.json` - Full admin access to a specific database
-   `database-user.json` - Standard user access (create, edit, view)
-   `database-readonly.json` - Read-only access to a specific database
-   `global-readonly.json` - Read-only access across all databases
-   `deny-tagged-assets.json` - Deny access to assets with specific tags

For detailed explanations of what each template provides (constraint matrices, design decisions, GLOBAL keyword usage, and tier enforcement), see [documentation/PermissionsGuide.md](../../../../documentation/PermissionsGuide.md).

### role constraint template import

Import constraints from a JSON permission template.

#### Basic Usage

```bash
# Import from a template file
vamscli role constraint template import -j ./database-admin.json

# Import with JSON output
vamscli role constraint template import -j ./database-admin.json --json-output
```

#### Options

-   `--json-input, -j TEXT` (required): Template JSON data as a string or path to a JSON file
-   `--json-output`: Output raw JSON response

#### Template JSON Format

The template JSON must include:

-   `variableValues`: Dictionary of variable substitutions (must include `ROLE_NAME`)
-   `constraints`: List of constraint definitions

Optional fields:

-   `metadata`: Template name, description, and version
-   `variables`: Variable definitions with descriptions and defaults

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

#### Example Output

```
Importing 13 constraint(s) from template 'Database Admin' for role 'my-db-admin'...

Constraint template imported successfully!
  Template: Database Admin
  Role: my-db-admin
  Constraints Created: 13
  Constraint IDs:
    - a1b2c3d4-...
    - e5f6g7h8-...
    ...
  Message: Successfully imported 13 constraints from template 'Database Admin' for role 'my-db-admin'
  Timestamp: 2024-01-01T00:00:00
```

#### Example: Using a Pre-built Template

```bash
# 1. Copy a template and fill in variable values
cp documentation/permissionsTemplates/database-admin.json my-template.json

# 2. Edit my-template.json to add variableValues:
#    "variableValues": {"ROLE_NAME": "project-admin", "DATABASE_ID": "project-db"}

# 3. Import the template
vamscli role constraint template import -j my-template.json

# 4. Verify the constraints were created
vamscli role constraint list --json-output
```

## Common Workflows

### Creating a Standard Role and Assigning to Users

```bash
# 1. Create the role
vamscli role create -r viewer --description "Read-only access"

# 2. Verify creation
vamscli role list

# 3. Assign role to a user
vamscli role user create -u user@example.com --role-name viewer

# 4. Verify assignment
vamscli role user list
```

### Creating a Secure Role with MFA

```bash
# Create a role that requires MFA
vamscli role create -r secure-admin \
  --description "Secure administrator with MFA" \
  --mfa-required

# Verify MFA requirement
vamscli role list --json-output | grep -A 5 "secure-admin"
```

### Updating Role Configuration

```bash
# Enable MFA for an existing role
vamscli role update -r admin --mfa-required

# Update description and source
vamscli role update -r admin \
  --description "Updated administrator role" \
  --source "LDAP"
```

### Bulk Role Creation

```bash
# Create multiple roles from JSON file
cat > roles.json << EOF
{
  "roles": [
    {"roleName": "admin", "description": "Administrator", "mfaRequired": true},
    {"roleName": "editor", "description": "Content editor", "mfaRequired": false},
    {"roleName": "viewer", "description": "Read-only access", "mfaRequired": false}
  ]
}
EOF

# Note: Currently, create command handles one role at a time
# For bulk operations, use a script:
for role in admin editor viewer; do
  vamscli role create -r $role --json-input roles.json
done
```

### Cleaning Up Old Roles

```bash
# 1. List all roles to identify old ones
vamscli role list

# 2. Delete old role with confirmation
vamscli role delete -r old-role --confirm
```

### Managing User Role Assignments

```bash
# 1. List all user role assignments
vamscli role user list

# 2. Assign multiple roles to a user
vamscli role user create -u user@example.com \
  --role-name admin \
  --role-name viewer

# 3. Update user's roles (differential update)
vamscli role user update -u user@example.com \
  --role-name admin \
  --role-name editor
# This removes 'viewer' and adds 'editor'

# 4. Remove all roles from a user
vamscli role user delete -u user@example.com --confirm
```

### Bulk User Role Assignment

```bash
# Create user roles from JSON file
cat > user-roles.json << EOF
{
  "userId": "user@example.com",
  "roleName": ["admin", "viewer", "editor"]
}
EOF

vamscli role user create -u user@example.com --json-input user-roles.json

# Assign same roles to multiple users
for user in user1@example.com user2@example.com user3@example.com; do
  vamscli role user create -u $user --role-name viewer --role-name editor
done
```

### Auditing User Access

```bash
# Export all user role assignments for audit
vamscli role user list --auto-paginate --json-output > user-roles-audit.json

# Find all users with admin role
vamscli role user list --json-output | jq '.Items[] | select(.roleName[] == "admin") | .userId'

# Count users per role
vamscli role user list --json-output | jq '.Items[].roleName[]' | sort | uniq -c
```

### Creating Database-Specific Access Constraints

```bash
# Create a constraint that restricts access to a specific database
cat > db-constraint.json << EOF
{
  "name": "Database 1 Access",
  "description": "Restrict access to database 1 only",
  "objectType": "asset",
  "criteriaAnd": [
    {"field": "databaseId", "operator": "equals", "value": "db1"}
  ],
  "groupPermissions": [
    {"groupId": "db1-users", "permission": "read", "permissionType": "allow"}
  ]
}
EOF

vamscli role constraint create -c db1-access --json-input db-constraint.json
```

### Creating File Type Restrictions

```bash
# Create a constraint that restricts access to specific file types
cat > file-type-constraint.json << EOF
{
  "name": "PDF Files Only",
  "description": "Restrict access to PDF files",
  "objectType": "file",
  "criteriaOr": [
    {"field": "fileExtension", "operator": "equals", "value": ".pdf"},
    {"field": "fileExtension", "operator": "equals", "value": ".PDF"}
  ],
  "groupPermissions": [
    {"groupId": "document-viewers", "permission": "read", "permissionType": "allow"}
  ]
}
EOF

vamscli role constraint create -c pdf-only --json-input file-type-constraint.json
```

### Managing Constraint Permissions

```bash
# 1. Get existing constraint
vamscli role constraint get -c my-constraint --json-output > constraint.json

# 2. Edit the JSON file to add/modify permissions
# Edit constraint.json to add new groupPermissions or userPermissions

# 3. Update the constraint
vamscli role constraint update -c my-constraint --json-input constraint.json

# 4. Verify the update
vamscli role constraint get -c my-constraint
```

### Listing and Reviewing Constraints

```bash
# List all constraints
vamscli role constraint list

# Get detailed view of specific constraint
vamscli role constraint get -c my-constraint

# Export all constraints for backup
vamscli role constraint list --auto-paginate --json-output > constraints-backup.json
```

## JSON Input/Output

### JSON Input Format

When using `--json-input`, provide role data in this format:

```json
{
    "roleName": "admin",
    "description": "Administrator role",
    "source": "LDAP",
    "sourceIdentifier": "cn=admin,dc=example,dc=com",
    "mfaRequired": true
}
```

### JSON Output Format

When using `--json-output`, commands return pure JSON:

#### List Output

```json
{
    "Items": [
        {
            "roleName": "admin",
            "description": "Administrator role",
            "id": "role-uuid",
            "createdOn": "2024-01-01T00:00:00",
            "source": "LDAP",
            "sourceIdentifier": "cn=admin",
            "mfaRequired": true
        }
    ],
    "NextToken": "pagination-token"
}
```

#### Create/Update Output

```json
{
    "success": true,
    "message": "Role admin created successfully",
    "roleName": "admin",
    "operation": "create",
    "timestamp": "2024-01-01T00:00:00"
}
```

#### Delete Output

```json
{
    "message": "success"
}
```

## Best Practices

### Role Naming

-   Use descriptive, lowercase names with hyphens: `admin`, `content-editor`, `read-only-viewer`
-   Avoid special characters except hyphens and underscores
-   Keep names concise but meaningful

### MFA Requirements

-   Enable MFA for administrative roles: `--mfa-required`
-   Consider MFA for roles with write access
-   Document MFA requirements in role descriptions

### Source Tracking

-   Use `--source` to track where roles originate (e.g., "LDAP", "Active Directory", "Manual")
-   Use `--source-identifier` for external system references
-   This helps with role synchronization and auditing

### Role Lifecycle

1. **Create**: Start with minimal permissions
2. **Test**: Verify role works as expected
3. **Update**: Adjust permissions as needed
4. **Document**: Keep role descriptions current
5. **Delete**: Remove unused roles to reduce clutter

### Automation

For automation and scripting:

```bash
# Always use --json-output for machine-readable responses
vamscli role list --json-output > roles.json

# Check exit codes
if vamscli role create -r test --description "Test" --json-output; then
  echo "Role created successfully"
else
  echo "Role creation failed"
  exit 1
fi
```

### Error Handling

```bash
# Capture errors in scripts
if ! vamscli role create -r admin --description "Admin" 2>&1 | tee role-create.log; then
  echo "Error creating role. Check role-create.log for details"
  exit 1
fi
```

## Related Commands

-   `vamscli user-role` - Assign roles to users
-   `vamscli auth status` - Check current authentication and permissions
-   `vamscli features list` - View enabled features that may affect role behavior

## See Also

-   [Setup and Authentication Guide](setup-auth.md)
-   [Role Management Troubleshooting](../troubleshooting/role-issues.md)
-   [Global Options and JSON Usage](global-options.md)
