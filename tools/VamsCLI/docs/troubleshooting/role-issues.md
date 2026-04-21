# Role Management Troubleshooting

This guide helps you troubleshoot common issues when working with role management commands in VamsCLI.

## Table of Contents

-   [Common Errors](#common-errors)
    -   [Role Already Exists](#role-already-exists)
    -   [Role Not Found](#role-not-found)
    -   [Role Deletion Failed](#role-deletion-failed)
    -   [Invalid Role Data](#invalid-role-data)
    -   [Missing Required Fields](#missing-required-fields)
-   [User Role Errors](#user-role-errors)
    -   [User Role Already Exists](#user-role-already-exists)
    -   [User Role Not Found](#user-role-not-found)
    -   [Invalid User Role Data](#invalid-user-role-data)
    -   [User Role Deletion Failed](#user-role-deletion-failed)
-   [Constraint Errors](#constraint-errors)
    -   [Constraint Already Exists](#constraint-already-exists)
    -   [Constraint Not Found](#constraint-not-found)
    -   [Invalid Constraint Data](#invalid-constraint-data)
    -   [Constraint Validation Errors](#constraint-validation-errors)
-   [Permission Issues](#permission-issues)
-   [Pagination Problems](#pagination-problems)
-   [JSON Input/Output Issues](#json-inputoutput-issues)
-   [Best Practices](#best-practices)

## Common Errors

### Role Already Exists

**Error Message:**

```
✗ Role Already Exists: Role already exists: Role 'admin' already exists
```

**Cause:** You're trying to create a role with a name that already exists in the system.

**Solutions:**

1. **Check existing roles:**

    ```bash
    vamscli role list
    ```

2. **Use a different role name:**

    ```bash
    vamscli role create -r admin-v2 --description "Administrator role"
    ```

3. **Update the existing role instead:**
    ```bash
    vamscli role update -r admin --description "Updated description"
    ```

### Role Not Found

**Error Message:**

```
✗ Role Not Found: Role 'nonexistent' not found
```

**Cause:** The specified role doesn't exist in the system.

**Solutions:**

1. **List available roles:**

    ```bash
    vamscli role list
    ```

2. **Check role name spelling:**

    - Role names are case-sensitive
    - Verify exact role name from the list output

3. **Create the role if needed:**
    ```bash
    vamscli role create -r correct-name --description "Role description"
    ```

### Role Deletion Failed

**Error Message:**

```
✗ Role Deletion Error: Role deletion failed: Role is assigned to active users
```

**Cause:** The role is currently assigned to one or more users and cannot be deleted.

**Solutions:**

1. **Check user role assignments:**

    ```bash
    # List users with this role (if user-role commands are available)
    vamscli user-role list --role admin
    ```

2. **Remove role assignments first:**

    ```bash
    # Unassign role from users before deletion
    vamscli user-role remove -u user@example.com -r admin
    ```

3. **Wait for automatic cleanup:**

    - The backend automatically cleans up user role assignments during deletion
    - If you still see this error, there may be a backend issue

4. **Contact administrator:**
    - If the role cannot be deleted, contact your VAMS administrator
    - There may be system roles that cannot be deleted

### Invalid Role Data

**Error Message:**

```
✗ Invalid Role Data: Invalid role data: roleName contains invalid characters
```

**Cause:** The role data doesn't meet validation requirements.

**Common Issues:**

1. **Invalid role name characters:**

    - Role names must follow the OBJECT_NAME pattern
    - Use only alphanumeric characters, hyphens, and underscores
    - Avoid special characters like @, #, $, etc.

2. **Description too long:**

    - Descriptions must be 256 characters or less
    - Keep descriptions concise and meaningful

3. **Invalid source value:**
    - Source must be one of the allowed values
    - Check backend constants for ALLOWED_ROLE_SOURCES

**Solutions:**

```bash
# ✅ Valid role name
vamscli role create -r admin-role --description "Admin"

# ❌ Invalid role name
vamscli role create -r admin@role --description "Admin"  # Contains @

# ✅ Valid description
vamscli role create -r admin --description "Administrator role with full access"

# ❌ Description too long
vamscli role create -r admin --description "$(printf 'A%.0s' {1..300})"  # Over 256 chars
```

### Missing Required Fields

**Error Message:**

```
✗ Invalid Input: --description is required when not using --json-input
```

**Cause:** Required fields are missing from the command.

**Solutions:**

1. **Provide required fields:**

    ```bash
    # Create requires: role-name and description
    vamscli role create -r admin --description "Administrator role"

    # Update requires: role-name and at least one field to update
    vamscli role update -r admin --description "Updated description"
    ```

2. **Use JSON input:**

    ```bash
    vamscli role create -r admin --json-input '{"roleName":"admin","description":"Admin"}'
    ```

3. **Check command help:**
    ```bash
    vamscli role create --help
    vamscli role update --help
    ```

## User Role Errors

### User Role Already Exists

**Error Message:**

```
✗ User Role Already Exists: One or more roles already exist for this user
```

**Cause:** You're trying to assign a role to a user who already has that role.

**Solutions:**

1. **Check existing user role assignments:**

    ```bash
    vamscli role user list
    ```

2. **Use update instead of create:**

    ```bash
    # Update adds new roles and keeps existing ones
    vamscli role user update -u user@example.com --role-name admin --role-name viewer
    ```

3. **Filter to specific user:**
    ```bash
    # Check roles for specific user
    vamscli role user list --json-output | jq '.Items[] | select(.userId == "user@example.com")'
    ```

### User Role Not Found

**Error Message:**

```
✗ User Role Not Found: User roles for 'user@example.com' not found
```

**Cause:** The specified user doesn't have any role assignments.

**Solutions:**

1. **List all user role assignments:**

    ```bash
    vamscli role user list
    ```

2. **Create role assignments for the user:**

    ```bash
    vamscli role user create -u user@example.com --role-name viewer
    ```

3. **Verify user ID spelling:**
    - User IDs are case-sensitive
    - Verify exact user ID from the list output

### Invalid User Role Data

**Error Message:**

```
✗ Invalid User Role Data: Invalid user role data: Role 'invalid-role' does not exist in the system
```

**Cause:** You're trying to assign a role that doesn't exist in the system.

**Solutions:**

1. **List available roles:**

    ```bash
    vamscli role list
    ```

2. **Create the role first:**

    ```bash
    vamscli role create -r required-role --description "Role description"
    ```

3. **Check role name spelling:**

    - Role names are case-sensitive
    - Verify exact role name from the role list

4. **Validate user ID format:**

    ```bash
    # ✅ Valid user ID (email format, at least 3 characters)
    vamscli role user create -u user@example.com --role-name admin

    # ❌ Invalid user ID (too short)
    vamscli role user create -u ab --role-name admin

    # ❌ Invalid user ID (invalid format)
    vamscli role user create -u "invalid user" --role-name admin
    ```

### User Role Deletion Failed

**Error Message:**

```
✗ User Role Deletion Error: User role deletion failed: Database error
```

**Cause:** The deletion operation failed due to a backend error.

**Solutions:**

1. **Retry the operation:**

    ```bash
    vamscli role user delete -u user@example.com --confirm
    ```

2. **Check user exists:**

    ```bash
    vamscli role user list --json-output | jq '.Items[] | select(.userId == "user@example.com")'
    ```

3. **Verify backend connectivity:**

    ```bash
    vamscli auth status
    ```

4. **Contact administrator:**
    - If the error persists, there may be a backend issue
    - Provide error details to your VAMS administrator

## Constraint Errors

### Constraint Already Exists

**Error Message:**

```
✗ Constraint Already Exists: Constraint already exists
```

**Cause:** You're trying to create a constraint with an ID that already exists in the system.

**Solutions:**

1. **Check existing constraints:**

    ```bash
    vamscli role constraint list
    ```

2. **Use a different constraint ID:**

    ```bash
    vamscli role constraint create -c my-constraint-v2 --json-input constraint.json
    ```

3. **Update the existing constraint instead:**

    ```bash
    vamscli role constraint update -c my-constraint --json-input constraint.json
    ```

4. **Get details of existing constraint:**
    ```bash
    vamscli role constraint get -c my-constraint
    ```

### Constraint Not Found

**Error Message:**

```
✗ Constraint Not Found: Constraint 'nonexistent' not found
```

**Cause:** The specified constraint doesn't exist in the system.

**Solutions:**

1. **List available constraints:**

    ```bash
    vamscli role constraint list
    ```

2. **Check constraint ID spelling:**

    - Constraint IDs are case-sensitive
    - Verify exact constraint ID from the list output

3. **Create the constraint if needed:**
    ```bash
    vamscli role constraint create -c correct-id --json-input constraint.json
    ```

### Invalid Constraint Data

**Error Message:**

```
✗ Invalid Constraint Data: Invalid constraint data: objectType must be one of: asset, file
```

**Cause:** The constraint data doesn't meet validation requirements.

**Common Issues:**

1. **Invalid objectType:**

    - Must be one of the allowed object types (typically "asset" or "file")
    - Check backend constants for ALLOWED_CONSTRAINT_OBJECT_TYPES

2. **Invalid operator:**

    - Operators must be from the allowed list
    - Common operators: equals, contains, in, startsWith, endsWith, regex
    - Check backend constants for ALLOWED_CONSTRAINT_OPERATORS

3. **Invalid permission:**

    - Permissions must be from the allowed list
    - Common permissions: read, write, delete, update
    - Check backend constants for ALLOWED_CONSTRAINT_PERMISSIONS

4. **Invalid permissionType:**

    - Must be either "allow" or "deny"
    - Check backend constants for ALLOWED_CONSTRAINT_PERMISSION_TYPES

5. **Missing criteria:**
    - At least one of criteriaAnd or criteriaOr must be provided
    - Cannot have empty arrays for both

**Solutions:**

```bash
# ✅ Valid objectType
vamscli role constraint create -c test --json-input '{
  "name": "Test",
  "description": "Test",
  "objectType": "asset",
  "criteriaAnd": [{"field": "databaseId", "operator": "equals", "value": "db1"}]
}'

# ❌ Invalid objectType
vamscli role constraint create -c test --json-input '{
  "name": "Test",
  "objectType": "invalid",
  ...
}'

# ✅ Valid criteria with proper operator
{
  "criteriaAnd": [
    {"field": "databaseId", "operator": "equals", "value": "db1"}
  ]
}

# ❌ Invalid operator
{
  "criteriaAnd": [
    {"field": "databaseId", "operator": "invalid_op", "value": "db1"}
  ]
}

# ✅ Valid permissions
{
  "groupPermissions": [
    {"groupId": "admin", "permission": "read", "permissionType": "allow"}
  ]
}

# ❌ Invalid permission type
{
  "groupPermissions": [
    {"groupId": "admin", "permission": "read", "permissionType": "maybe"}
  ]
}
```

### Constraint Validation Errors

**Error Message:**

```
✗ Invalid Constraint Data: Constraint must include criteriaOr or criteriaAnd statements
```

**Cause:** The constraint doesn't have any criteria defined.

**Solutions:**

1. **Add at least one criteria:**

    ```bash
    # Must have criteriaAnd or criteriaOr (or both)
    vamscli role constraint create -c test --json-input '{
      "name": "Test",
      "description": "Test",
      "objectType": "asset",
      "criteriaAnd": [
        {"field": "databaseId", "operator": "equals", "value": "db1"}
      ]
    }'
    ```

2. **Validate regex patterns in criteria values:**

    - Criteria values are validated as regex patterns
    - Ensure values are valid regex or use simple strings

3. **Validate groupId references:**

    - groupId must reference an existing role
    - Create the role first if it doesn't exist:

    ```bash
    vamscli role create -r db1-users --description "Database 1 users"
    ```

4. **Validate userId format:**
    - userId must be a valid user identifier (typically email format)
    - Must be at least 3 characters long

**Common Validation Errors:**

```bash
# ❌ No criteria
{
  "criteriaAnd": [],
  "criteriaOr": []
}

# ✅ At least one criteria
{
  "criteriaAnd": [
    {"field": "databaseId", "operator": "equals", "value": "db1"}
  ]
}

# ❌ Invalid groupId (role doesn't exist)
{
  "groupPermissions": [
    {"groupId": "nonexistent-role", "permission": "read", "permissionType": "allow"}
  ]
}

# ✅ Valid groupId (role exists)
{
  "groupPermissions": [
    {"groupId": "admin", "permission": "read", "permissionType": "allow"}
  ]
}

# ❌ Invalid userId format
{
  "userPermissions": [
    {"userId": "ab", "permission": "read", "permissionType": "allow"}  # Too short
  ]
}

# ✅ Valid userId
{
  "userPermissions": [
    {"userId": "user@example.com", "permission": "read", "permissionType": "allow"}
  ]
}
```

## Permission Issues

### Access Denied

**Error Message:**

```
✗ Authentication Error: Access forbidden. You do not have permission to perform this action.
```

**Cause:** Your user account lacks permissions to manage roles.

**Solutions:**

1. **Check your authentication:**

    ```bash
    vamscli auth status
    ```

2. **Verify your role permissions:**

    - Contact your VAMS administrator
    - Request role management permissions
    - Ensure you're authenticated with the correct account

3. **Re-authenticate:**
    ```bash
    vamscli auth login -u your-admin-account@example.com
    ```

### Token Expired

**Error Message:**

```
✗ Authentication Error: Authentication token has expired.
```

**Cause:** Your authentication token has expired.

**Solutions:**

```bash
# Re-authenticate
vamscli auth login -u your-username@example.com
```

## Pagination Problems

### Conflicting Pagination Options

**Error Message:**

```
Cannot use --auto-paginate with --starting-token.
```

**Cause:** You're trying to use both auto-pagination and manual pagination at the same time.

**Solutions:**

```bash
# ✅ Use auto-pagination
vamscli role list --auto-paginate

# ✅ Use manual pagination
vamscli role list --page-size 100
vamscli role list --starting-token "token123" --page-size 100

# ❌ Don't mix them
vamscli role list --auto-paginate --starting-token "token123"  # ERROR
```

### Max Items Without Auto-Paginate

**Warning Message:**

```
Warning: --max-items only applies with --auto-paginate. Ignoring --max-items.
```

**Cause:** You specified `--max-items` without `--auto-paginate`.

**Solutions:**

```bash
# ✅ Correct usage
vamscli role list --auto-paginate --max-items 5000

# ⚠️  Ignored (warning shown)
vamscli role list --max-items 5000  # max-items is ignored
```

### Too Many Results

**Issue:** Auto-pagination stops at the maximum limit.

**Message:**

```
⚠️  Reached maximum of 10000 items. More items may be available.
```

**Solutions:**

```bash
# Increase max-items limit
vamscli role list --auto-paginate --max-items 20000

# Use manual pagination for very large datasets
vamscli role list --page-size 1000 > page1.json
# Get next token from output, then:
vamscli role list --starting-token "token-from-page1" --page-size 1000 > page2.json
```

## JSON Input/Output Issues

### Invalid JSON Input

**Error Message:**

```
✗ Invalid Input: Invalid JSON input: 'invalid json' is neither valid JSON nor a readable file path
```

**Cause:** The JSON input is malformed or the file doesn't exist.

**Solutions:**

1. **Validate JSON syntax:**

    ```bash
    # Test JSON validity
    echo '{"roleName":"admin","description":"Admin"}' | python -m json.tool

    # If valid, use it
    vamscli role create -r admin --json-input '{"roleName":"admin","description":"Admin"}'
    ```

2. **Check file path:**

    ```bash
    # Verify file exists
    ls -l role.json

    # Use absolute path if needed
    vamscli role create -r admin --json-input /full/path/to/role.json
    ```

3. **Common JSON errors:**

    ```bash
    # ❌ Missing quotes
    '{"roleName":admin}'  # Should be "admin"

    # ❌ Trailing comma
    '{"roleName":"admin",}'  # Remove trailing comma

    # ❌ Single quotes
    "{'roleName':'admin'}"  # Use double quotes

    # ✅ Valid JSON
    '{"roleName":"admin","description":"Admin"}'
    ```

### JSON Output Parsing

**Issue:** Difficulty parsing JSON output in scripts.

**Solutions:**

```bash
# Use jq for JSON parsing
vamscli role list --json-output | jq '.Items[] | select(.roleName == "admin")'

# Use Python for complex parsing
vamscli role list --json-output | python -c "
import sys, json
data = json.load(sys.stdin)
for role in data['Items']:
    if role.get('mfaRequired'):
        print(role['roleName'])
"

# Save to file for processing
vamscli role list --json-output > roles.json
```

## Best Practices

### Error Prevention

1. **Always list before modifying:**

    ```bash
    vamscli role list  # Check current state
    vamscli role update -r admin --description "Updated"
    ```

2. **Use descriptive role names:**

    - Avoid generic names like "role1", "test"
    - Use meaningful names: "content-editor", "asset-viewer"

3. **Test with --json-output first:**

    ```bash
    # Test command with JSON output to see exact response
    vamscli role create -r test --description "Test" --json-output
    ```

4. **Backup before bulk operations:**
    ```bash
    # Export current roles before making changes
    vamscli role list --json-output > roles-backup-$(date +%Y%m%d).json
    ```

### Debugging

1. **Enable verbose mode:**

    ```bash
    vamscli --verbose role list
    vamscli --verbose role create -r admin --description "Admin"
    ```

2. **Check logs:**

    ```bash
    # View CLI logs
    cat ~/.config/vamscli/logs/vamscli.log

    # On Windows
    type %APPDATA%\vamscli\logs\vamscli.log
    ```

3. **Test API connectivity:**
    ```bash
    vamscli auth status  # Verify authentication
    vamscli features list  # Check feature availability
    ```

### Recovery

1. **Role creation failed mid-process:**

    ```bash
    # Check if role was partially created
    vamscli role list --json-output | grep "role-name"

    # If exists, update it
    vamscli role update -r role-name --description "Corrected"

    # If doesn't exist, retry creation
    vamscli role create -r role-name --description "Description"
    ```

2. **Accidental role deletion:**

    - Roles cannot be recovered once deleted
    - Restore from backup if available
    - Recreate the role with same configuration

3. **Lost role configuration:**
    ```bash
    # If you have a backup
    vamscli role create -r admin --json-input roles-backup.json
    ```

## Getting Help

If you continue to experience issues:

1. **Check command help:**

    ```bash
    vamscli role --help
    vamscli role create --help
    vamscli role update --help
    vamscli role delete --help
    ```

2. **Review documentation:**

    - [Role Management Guide](../commands/role-management.md)
    - [Setup and Authentication](../commands/setup-auth.md)
    - [Global Options](../commands/global-options.md)

3. **Check VAMS API status:**

    - Verify VAMS deployment is running
    - Check API Gateway URL is correct
    - Ensure network connectivity

4. **Contact support:**
    - Provide error messages and command used
    - Include relevant log files
    - Describe expected vs. actual behavior

## Related Documentation

-   [Role Management Commands](../commands/role-management.md)
-   [Setup and Authentication Issues](setup-auth-issues.md)
-   [General Troubleshooting](general-troubleshooting.md)
-   [Network and Configuration Issues](network-config-issues.md)
