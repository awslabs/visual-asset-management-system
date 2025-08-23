# Database and Tag Management Issues

This document covers common issues with database management and tag operations in VamsCLI.

## Database Management Issues

### Metadata Schema Issues

**Error:**

```
✗ Database Not Found: Database 'my-database' not found
```

**Solutions:**

1. Verify the database ID is correct and exists
2. Use `vamscli database list` to see available databases
3. Check if you have permission to access the database
4. Ensure the database hasn't been deleted or archived

**Error:**

```
✗ Authentication Error: Not authorized to view metadata schema
```

**Solutions:**

1. Verify you have permissions to view metadata schema
2. Contact your administrator for schema access permissions
3. Check if you're using the correct profile
4. Re-authenticate: `vamscli auth login -u <username>`

**Error:**

```
✗ API Error: Failed to get metadata schema
```

**Solutions:**

1. Check API connectivity: `vamscli auth status`
2. Verify the API Gateway URL is correct
3. Check if the VAMS API version supports metadata schema operations
4. Try with debug mode: `vamscli --debug metadata-schema get -d <database>`

**Troubleshooting Workflow:**

```bash
# 1. Verify database exists
vamscli database list

# 2. Check database details
vamscli database get -d <database-id>

# 3. Test metadata schema access
vamscli metadata-schema get -d <database-id>

# 4. Try with different pagination
vamscli metadata-schema get -d <database-id> --max-items 10

# 5. Use JSON output for debugging
vamscli metadata-schema get -d <database-id> --json-output
```

### Database Not Found

**Error:**

```
✗ Database Error: Database 'my-database' not found
```

**Solutions:**

1. Verify the database ID is correct
2. Check if you have permission to access the database
3. Use `vamscli database list` to see available databases
4. Contact your administrator about database access

### Database Already Exists

**Error:**

```
✗ Database Error: Database already exists
```

**Solutions:**

1. Use a different database ID
2. Use `vamscli database update` to modify the existing database
3. Check existing databases: `vamscli database list`

### Database Creation Failed

**Error:**

```
✗ Database Creation Error: Failed to create database
```

**Solutions:**

1. Verify the database ID format (lowercase, alphanumeric, hyphens, underscores)
2. Check if the default bucket ID is valid
3. Use `vamscli database list-buckets` to see available buckets
4. Ensure you have permissions to create databases

### Database Deletion Failed

**Error:**

```
✗ Database Deletion Error: Cannot delete database that contains active resources
```

**Solutions:**

1. Remove all assets from the database first
2. Cancel or complete all active workflows
3. Disable or remove all pipelines
4. Use `vamscli assets list -d <database>` to check for remaining assets

### Bucket Configuration Issues

**Error:**

```
✗ Bucket Error: Invalid bucket configuration
```

**Solutions:**

1. Use `vamscli database list-buckets` to see valid bucket configurations
2. Verify the bucket ID is correct (UUID format)
3. Ensure the bucket exists and is accessible
4. Contact your administrator about bucket permissions

## Tag Management Issues

### Tag Not Found

**Error:**

```
✗ Tag Not Found: Tag 'urgent' not found
```

**Solutions:**

1. Verify the tag name is correct
2. Use `vamscli tag list` to see available tags
3. Check if you have permission to access the tag
4. Create the tag first: `vamscli tag create --tag-name "urgent" --description "Urgent priority" --tag-type-name "priority"`

### Tag Already Exists

**Error:**

```
✗ Tag Already Exists: Tag 'urgent' already exists
```

**Solutions:**

1. Use a different tag name
2. Use `vamscli tag update` to modify the existing tag
3. Use `vamscli tag list` to see existing tags
4. Delete the existing tag first: `vamscli tag delete urgent --confirm`

### Tag Type Not Found

**Error:**

```
✗ Tag Type Not Found: Tag type 'priority' not found
```

**Solutions:**

1. Verify the tag type name is correct
2. Use `vamscli tag-type list` to see available tag types
3. Create the tag type first: `vamscli tag-type create --tag-type-name "priority" --description "Priority levels"`
4. Check if you have permission to access the tag type

### Tag Type Already Exists

**Error:**

```
✗ Tag Type Already Exists: Tag type 'priority' already exists
```

**Solutions:**

1. Use a different tag type name
2. Use `vamscli tag-type update` to modify the existing tag type
3. Use `vamscli tag-type list` to see existing tag types
4. Delete the existing tag type first (if not in use): `vamscli tag-type delete priority --confirm`

### Tag Type In Use

**Error:**

```
✗ Tag Type In Use: Cannot delete tag type that is currently in use by a tag
```

**Solutions:**

1. Delete all tags using this tag type first
2. Use `vamscli tag list --tag-type <name>` to see tags using this type
3. Update tags to use different tag types before deletion
4. Consider if the tag type should really be deleted

### Invalid Tag Data

**Error:**

```
✗ Invalid Tag Data: TagName, description and tagTypeName are required
```

**Solutions:**

1. Ensure all required fields are provided
2. Check JSON input format is correct
3. Verify tag names follow VAMS naming conventions (alphanumeric, spaces, hyphens, underscores, 1-256 characters)
4. Ensure descriptions are within 256 character limit

### Tag Creation Workflow Error

**Error:**

```
✗ Tag Type Not Found: TagTypeName priority doesn't exist
```

**Solutions:**

1. Create tag types before creating tags
2. Follow the correct workflow:

    ```bash
    # First create tag type
    vamscli tag-type create --tag-type-name "priority" --description "Priority levels"

    # Then create tags
    vamscli tag create --tag-name "urgent" --description "Urgent priority" --tag-type-name "priority"
    ```

3. Verify tag type exists: `vamscli tag-type list`

## Permission Issues

### Database Permission Error

**Error:**

```
✗ Database Permission Error: Not authorized to access database
```

**Solutions:**

1. Verify you have permissions on the database
2. Contact your administrator for database access
3. Check if you're using the correct profile
4. Ensure the database exists: `vamscli database list`

### Tag Permission Error

**Error:**

```
✗ Tag Permission Error: Not authorized to manage tags
```

**Solutions:**

1. Verify you have tag management permissions
2. Contact your administrator for tag permissions
3. Check if you're using the correct profile
4. Ensure you have the required role for tag operations

## Validation Issues

### Invalid Database ID Format

**Error:**

```
✗ Validation Error: Invalid database ID format
```

**Solutions:**

1. Use only lowercase letters, numbers, hyphens, and underscores
2. Ensure the ID is between 1-63 characters
3. Start with a letter or number
4. Don't use reserved words

**Valid examples:**

-   `my-database`
-   `prod_assets_db`
-   `database123`

**Invalid examples:**

-   `My-Database` (uppercase)
-   `database-` (ends with hyphen)
-   `-database` (starts with hyphen)

### Invalid Tag Name Format

**Error:**

```
✗ Validation Error: Invalid tag name format
```

**Solutions:**

1. Use alphanumeric characters, spaces, hyphens, and underscores
2. Keep names between 1-256 characters
3. Avoid special characters except spaces, hyphens, underscores
4. Use descriptive names

**Valid examples:**

-   `urgent`
-   `high-priority`
-   `3D Model`
-   `texture_file`

**Invalid examples:**

-   `urgent!` (special character)
-   `tag@name` (special character)
-   `` (empty string)

## Troubleshooting Workflows

### Database Operation Troubleshooting

```bash
# 1. Check if database exists
vamscli database list

# 2. Get database details
vamscli database get -d <database-id>

# 3. Check bucket configurations
vamscli database list-buckets

# 4. Verify permissions
vamscli assets list -d <database-id>

# 5. Try operation with debug mode
vamscli --debug database <operation> <parameters>
```

### Tag System Troubleshooting

```bash
# 1. List all tag types
vamscli tag-type list --show-tags

# 2. Check specific tag type
vamscli tag list --tag-type <type-name>

# 3. Verify tag exists
vamscli tag list

# 4. Check tag creation workflow
# First: vamscli tag-type create --tag-type-name "type" --description "desc"
# Then: vamscli tag create --tag-name "tag" --description "desc" --tag-type-name "type"

# 5. Try operation with debug mode
vamscli --debug tag <operation> <parameters>
```

### Tag Type Dependency Troubleshooting

```bash
# 1. Check which tags use the tag type
vamscli tag list --tag-type <type-name>

# 2. Update or delete dependent tags first
vamscli tag update --tag-name <tag> --tag-type-name <new-type>
# or
vamscli tag delete <tag> --confirm

# 3. Then delete the tag type
vamscli tag-type delete <type-name> --confirm

# 4. Verify deletion
vamscli tag-type list
```

## Advanced Troubleshooting

### Database State Verification

```bash
# Check database state comprehensively
vamscli database get -d <database-id> --json-output

# Check associated assets
vamscli assets list -d <database-id> --json-output

# Check bucket configuration
vamscli database list-buckets --json-output

# Verify permissions
vamscli --debug database get -d <database-id>
```

### Tag System State Verification

```bash
# Check complete tag system state
vamscli tag-type list --show-tags --json-output

# Check specific tag relationships
vamscli tag list --tag-type <type> --json-output

# Verify tag creation dependencies
vamscli --debug tag create --tag-name "test" --description "test" --tag-type-name "nonexistent"
```

## Recovery Procedures

### Database Recovery

```bash
# If database appears corrupted or inaccessible
# 1. Check if database exists
vamscli database list --show-deleted

# 2. Try to access with different profile
vamscli database get -d <database-id> --profile <different-profile>

# 3. If database is deleted, recreate it
vamscli database create -d <database-id> --description "Recreated database"

# 4. Restore assets if needed (manual process)
```

### Tag System Recovery

```bash
# If tag system is corrupted
# 1. Document current state
vamscli tag-type list --show-tags --json-output > tag-system-backup.json

# 2. Recreate tag types
vamscli tag-type create --tag-type-name "priority" --description "Priority levels"

# 3. Recreate tags
vamscli tag create --tag-name "urgent" --description "Urgent priority" --tag-type-name "priority"

# 4. Verify restoration
vamscli tag-type list --show-tags
```

## Common Error Patterns

### Database-Related Patterns

-   **Not Found**: Usually indicates incorrect ID or insufficient permissions
-   **Already Exists**: Use update operations instead of create
-   **Validation Failed**: Check ID format and naming conventions
-   **Operation Failed**: Often due to resource dependencies or permissions

### Tag-Related Patterns

-   **Workflow Errors**: Create tag types before tags
-   **Dependency Errors**: Cannot delete tag types that are in use
-   **Validation Errors**: Check naming conventions and required fields
-   **Permission Errors**: Verify tag management permissions

## Best Practices for Avoiding Issues

### Database Management

1. Use descriptive, consistent naming conventions
2. Verify bucket configurations before creating databases
3. Check permissions before performing operations
4. Clean up unused databases regularly

### Tag Management

1. Plan tag system architecture before implementation
2. Create tag types before creating tags
3. Use consistent naming conventions
4. Document tag system design and usage

### General Troubleshooting

1. Always use debug mode for detailed error information
2. Check resource existence before performing operations
3. Verify permissions and access rights
4. Use JSON output for programmatic error handling

## Frequently Asked Questions

### Q: Why can't I delete a database?

**A:** Databases cannot be deleted if they contain active assets, workflows, or pipelines. Clean up all resources first.

### Q: Why can't I delete a tag type?

**A:** Tag types cannot be deleted if they're being used by existing tags. Delete or reassign the tags first.

### Q: Why do I get "already exists" errors?

**A:** Use update commands instead of create commands for existing resources, or choose different names.

### Q: How do I fix validation errors?

**A:** Check naming conventions, required fields, and parameter formats. Use command help for guidance.

### Q: Why do database operations fail?

**A:** Usually due to permissions, invalid bucket configurations, or resource dependencies. Check each systematically.

### Q: How do I troubleshoot tag workflow errors?

**A:** Ensure tag types exist before creating tags, and follow the correct creation order.
