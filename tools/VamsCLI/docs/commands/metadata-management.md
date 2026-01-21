# Metadata Management Commands

This document covers VamsCLI unified metadata management commands for assets, files, asset links, and databases (v2.2+).

## Overview

VamsCLI provides comprehensive metadata management capabilities through a unified API that supports:

-   **Asset Metadata**: Custom key-value data attached to assets
-   **File Metadata**: Metadata for individual files within assets
-   **Asset Link Metadata**: Metadata for relationships between assets
-   **Database Metadata**: Metadata for entire databases
-   **Bulk Operations**: Create, update, or delete multiple metadata items in a single operation
-   **Update Modes**: Choose between upsert (update) or replace (replace_all) modes

## Unified Metadata API (v2.2+)

All metadata operations use a consistent request/response format:

**Request Format:**

```json
{
    "metadata": [
        {
            "metadataKey": "title",
            "metadataValue": "My Asset",
            "metadataValueType": "string"
        },
        {
            "metadataKey": "priority",
            "metadataValue": "1",
            "metadataValueType": "number"
        }
    ],
    "updateType": "update"
}
```

**Update Modes:**

-   `update` (default): Upsert mode - creates or updates provided metadata, keeps unlisted keys
-   `replace_all`: Replace mode - deletes unlisted keys, upserts provided metadata (with rollback on failure)

## Asset Metadata Commands

### `vamscli metadata asset list`

List all metadata for an asset.

**Required Options:**

-   `-d, --database-id`: Database ID containing the asset (required)
-   `-a, --asset-id`: Asset ID to list metadata for (required)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# List asset metadata
vamscli metadata asset list -d my-database -a my-asset

# List with JSON output for automation
vamscli metadata asset list -d my-database -a my-asset --json-output

# List with specific profile
vamscli metadata asset list -d my-database -a my-asset --profile production
```

**CLI Output Format:**

```
Asset Metadata (3 item(s)):
Key                      Value                    Type
--------------------------------------------------------------------------------
title                    My 3D Model              string
priority                 1                        number
properties               {"polygons": 50000}      object
```

**JSON Output Format:**

```json
{
    "metadata": [
        {
            "metadataKey": "title",
            "metadataValue": "My 3D Model",
            "metadataValueType": "string"
        },
        {
            "metadataKey": "priority",
            "metadataValue": "1",
            "metadataValueType": "number"
        },
        {
            "metadataKey": "properties",
            "metadataValue": "{\"polygons\": 50000}",
            "metadataValueType": "object"
        }
    ]
}
```

### `vamscli metadata asset update`

Create or update metadata for an asset (bulk operation).

**Required Options:**

-   `-d, --database-id`: Database ID containing the asset (required)
-   `-a, --asset-id`: Asset ID to update metadata for (required)
-   `--json-input`: JSON input file path or JSON string with metadata array (required)

**Options:**

-   `--update-type`: Update mode - 'update' (upsert, default) or 'replace_all' (replace)
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Update metadata (upsert mode - default)
vamscli metadata asset update -d my-database -a my-asset --json-input '[
  {"metadataKey": "title", "metadataValue": "My Asset", "metadataValueType": "string"},
  {"metadataKey": "priority", "metadataValue": "1", "metadataValueType": "number"}
]'

# Replace all metadata (replace mode)
vamscli metadata asset update -d my-database -a my-asset --update-type replace_all --json-input '[
  {"metadataKey": "title", "metadataValue": "New Asset", "metadataValueType": "string"}
]'

# Update from JSON file
vamscli metadata asset update -d my-database -a my-asset --json-input @metadata.json

# Update with JSON output for automation
vamscli metadata asset update -d my-database -a my-asset --json-input '[...]' --json-output
```

**JSON Input Format:**

```json
[
    {
        "metadataKey": "title",
        "metadataValue": "My 3D Model",
        "metadataValueType": "string"
    },
    {
        "metadataKey": "category",
        "metadataValue": "architecture",
        "metadataValueType": "string"
    },
    {
        "metadataKey": "priority",
        "metadataValue": "1",
        "metadataValueType": "number"
    },
    {
        "metadataKey": "active",
        "metadataValue": "true",
        "metadataValueType": "boolean"
    },
    {
        "metadataKey": "properties",
        "metadataValue": "{\"polygons\": 50000, \"materials\": [\"wood\", \"metal\"]}",
        "metadataValueType": "object"
    }
]
```

**Supported Value Types:**

-   `string`: Text values
-   `number`: Numeric values (integers or floats)
-   `boolean`: true/false values
-   `object`: JSON objects (stored as JSON string)
-   `array`: JSON arrays (stored as JSON string)

**Update Modes:**

-   **update** (default): Upserts provided metadata, keeps existing unlisted keys
-   **replace_all**: Deletes unlisted keys, upserts provided metadata (atomic with rollback)

### `vamscli metadata asset delete`

Delete specific metadata keys from an asset.

**Required Options:**

-   `-d, --database-id`: Database ID containing the asset (required)
-   `-a, --asset-id`: Asset ID to delete metadata from (required)
-   `--json-input`: JSON input file path or JSON string with metadata keys array (required)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Delete specific metadata keys
vamscli metadata asset delete -d my-database -a my-asset --json-input '["title", "priority"]'

# Delete from JSON file
vamscli metadata asset delete -d my-database -a my-asset --json-input @keys-to-delete.json

# Delete with JSON output for automation
vamscli metadata asset delete -d my-database -a my-asset --json-input '["old_field"]' --json-output
```

**JSON Input Format:**

```json
["title", "priority", "old_field"]
```

## File Metadata Commands

### `vamscli metadata file list`

List all metadata for a specific file within an asset.

**Required Options:**

-   `-d, --database-id`: Database ID containing the asset (required)
-   `-a, --asset-id`: Asset ID containing the file (required)
-   `-f, --file-id`: File ID to list metadata for (required)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# List file metadata
vamscli metadata file list -d my-database -a my-asset -f file-uuid

# List with JSON output
vamscli metadata file list -d my-database -a my-asset -f file-uuid --json-output
```

**CLI Output Format:**

```
File Metadata (2 item(s)):
Key                      Value                    Type
--------------------------------------------------------------------------------
lod_level                high                     string
optimized                true                     boolean
```

### `vamscli metadata file update`

Create or update metadata for a specific file (bulk operation).

**Required Options:**

-   `-d, --database-id`: Database ID containing the asset (required)
-   `-a, --asset-id`: Asset ID containing the file (required)
-   `-f, --file-id`: File ID to update metadata for (required)
-   `--json-input`: JSON input file path or JSON string with metadata array (required)

**Options:**

-   `--update-type`: Update mode - 'update' (upsert, default) or 'replace_all' (replace)
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Update file metadata
vamscli metadata file update -d my-database -a my-asset -f file-uuid --json-input '[
  {"metadataKey": "lod_level", "metadataValue": "high", "metadataValueType": "string"},
  {"metadataKey": "optimized", "metadataValue": "true", "metadataValueType": "boolean"}
]'

# Replace all file metadata
vamscli metadata file update -d my-database -a my-asset -f file-uuid --update-type replace_all --json-input '[...]'
```

### `vamscli metadata file delete`

Delete specific metadata keys from a file.

**Required Options:**

-   `-d, --database-id`: Database ID containing the asset (required)
-   `-a, --asset-id`: Asset ID containing the file (required)
-   `-f, --file-id`: File ID to delete metadata from (required)
-   `--json-input`: JSON input file path or JSON string with metadata keys array (required)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Delete file metadata keys
vamscli metadata file delete -d my-database -a my-asset -f file-uuid --json-input '["old_field", "deprecated"]'
```

## Asset Link Metadata Commands

### `vamscli metadata asset-link list`

List all metadata for an asset link (relationship between assets).

**Required Options:**

-   `--asset-link-id`: Asset link ID to list metadata for (required)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# List asset link metadata
vamscli metadata asset-link list --asset-link-id link-uuid

# List with JSON output
vamscli metadata asset-link list --asset-link-id link-uuid --json-output
```

**CLI Output Format:**

```
Asset Link Metadata (2 item(s)):
Key                      Value                    Type
--------------------------------------------------------------------------------
relationship_type        parent-child             string
created_by               user@example.com         string
```

### `vamscli metadata asset-link update`

Create or update metadata for an asset link (bulk operation).

**Required Options:**

-   `--asset-link-id`: Asset link ID to update metadata for (required)
-   `--json-input`: JSON input file path or JSON string with metadata array (required)

**Options:**

-   `--update-type`: Update mode - 'update' (upsert, default) or 'replace_all' (replace)
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Update asset link metadata
vamscli metadata asset-link update --asset-link-id link-uuid --json-input '[
  {"metadataKey": "relationship_type", "metadataValue": "parent-child", "metadataValueType": "string"},
  {"metadataKey": "created_by", "metadataValue": "user@example.com", "metadataValueType": "string"}
]'

# Replace all asset link metadata
vamscli metadata asset-link update --asset-link-id link-uuid --update-type replace_all --json-input '[...]'
```

### `vamscli metadata asset-link delete`

Delete specific metadata keys from an asset link.

**Required Options:**

-   `--asset-link-id`: Asset link ID to delete metadata from (required)
-   `--json-input`: JSON input file path or JSON string with metadata keys array (required)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Delete asset link metadata keys
vamscli metadata asset-link delete --asset-link-id link-uuid --json-input '["old_field"]'
```

## Database Metadata Commands

### `vamscli metadata database list`

List all metadata for a database.

**Required Options:**

-   `-d, --database-id`: Database ID to list metadata for (required)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# List database metadata
vamscli metadata database list -d my-database

# List with JSON output
vamscli metadata database list -d my-database --json-output
```

**CLI Output Format:**

```
Database Metadata (3 item(s)):
Key                      Value                    Type
--------------------------------------------------------------------------------
project                  Downtown Complex         string
client                   City Planning Dept       string
status                   active                   string
```

### `vamscli metadata database update`

Create or update metadata for a database (bulk operation).

**Required Options:**

-   `-d, --database-id`: Database ID to update metadata for (required)
-   `--json-input`: JSON input file path or JSON string with metadata array (required)

**Options:**

-   `--update-type`: Update mode - 'update' (upsert, default) or 'replace_all' (replace)
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Update database metadata
vamscli metadata database update -d my-database --json-input '[
  {"metadataKey": "project", "metadataValue": "Downtown Complex", "metadataValueType": "string"},
  {"metadataKey": "client", "metadataValue": "City Planning Dept", "metadataValueType": "string"}
]'

# Replace all database metadata
vamscli metadata database update -d my-database --update-type replace_all --json-input '[...]'
```

### `vamscli metadata database delete`

Delete specific metadata keys from a database.

**Required Options:**

-   `-d, --database-id`: Database ID to delete metadata from (required)
-   `--json-input`: JSON input file path or JSON string with metadata keys array (required)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Delete database metadata keys
vamscli metadata database delete -d my-database --json-input '["old_project", "deprecated_field"]'
```

## Metadata Management Workflow Examples

### Basic Asset Metadata Operations

```bash
# Create initial asset metadata
vamscli metadata asset update -d my-db -a my-asset --json-input '[
  {"metadataKey": "title", "metadataValue": "3D Building Model", "metadataValueType": "string"},
  {"metadataKey": "category", "metadataValue": "architecture", "metadataValueType": "string"},
  {"metadataKey": "priority", "metadataValue": "1", "metadataValueType": "number"},
  {"metadataKey": "active", "metadataValue": "true", "metadataValueType": "boolean"},
  {"metadataKey": "properties", "metadataValue": "{\"polygons\": 75000, \"materials\": [\"concrete\", \"glass\"]}", "metadataValueType": "object"}
]'

# List all metadata
vamscli metadata asset list -d my-db -a my-asset

# Update specific fields (upsert mode)
vamscli metadata asset update -d my-db -a my-asset --json-input '[
  {"metadataKey": "title", "metadataValue": "Updated Building Model", "metadataValueType": "string"},
  {"metadataKey": "version", "metadataValue": "2", "metadataValueType": "number"}
]'

# Delete specific keys
vamscli metadata asset delete -d my-db -a my-asset --json-input '["old_field", "deprecated"]'
```

### File-Specific Metadata

```bash
# Add metadata to a specific file
vamscli metadata file update -d my-db -a my-asset -f file-uuid --json-input '[
  {"metadataKey": "lod_level", "metadataValue": "high", "metadataValueType": "string"},
  {"metadataKey": "optimized", "metadataValue": "true", "metadataValueType": "boolean"},
  {"metadataKey": "file_size_mb", "metadataValue": "15.2", "metadataValueType": "number"},
  {"metadataKey": "compression", "metadataValue": "draco", "metadataValueType": "string"}
]'

# List file metadata
vamscli metadata file list -d my-db -a my-asset -f file-uuid

# Update file metadata
vamscli metadata file update -d my-db -a my-asset -f file-uuid --json-input '[
  {"metadataKey": "lod_level", "metadataValue": "ultra", "metadataValueType": "string"}
]'
```

### Asset Link Metadata

```bash
# Add metadata to asset relationship
vamscli metadata asset-link update --asset-link-id link-uuid --json-input '[
  {"metadataKey": "relationship_type", "metadataValue": "parent-child", "metadataValueType": "string"},
  {"metadataKey": "created_by", "metadataValue": "user@example.com", "metadataValueType": "string"},
  {"metadataKey": "created_date", "metadataValue": "2024-01-15T10:30:00Z", "metadataValueType": "string"}
]'

# List asset link metadata
vamscli metadata asset-link list --asset-link-id link-uuid
```

### Database-Level Metadata

```bash
# Add database metadata
vamscli metadata database update -d my-database --json-input '[
  {"metadataKey": "project", "metadataValue": "Downtown Complex", "metadataValueType": "string"},
  {"metadataKey": "client", "metadataValue": "City Planning Dept", "metadataValueType": "string"},
  {"metadataKey": "status", "metadataValue": "active", "metadataValueType": "string"},
  {"metadataKey": "budget", "metadataValue": "1500000", "metadataValueType": "number"}
]'

# List database metadata
vamscli metadata database list -d my-database
```

### Bulk Operations with Replace Mode

```bash
# Replace all metadata (atomic operation)
vamscli metadata asset update -d my-db -a my-asset --update-type replace_all --json-input '[
  {"metadataKey": "title", "metadataValue": "New Asset", "metadataValueType": "string"},
  {"metadataKey": "status", "metadataValue": "active", "metadataValueType": "string"}
]'
# This deletes all existing metadata and creates only these two fields
# If operation fails, all changes are rolled back

# Upsert mode (default) - keeps existing keys
vamscli metadata asset update -d my-db -a my-asset --json-input '[
  {"metadataKey": "version", "metadataValue": "3", "metadataValueType": "number"}
]'
# This adds/updates 'version' but keeps all other existing metadata
```

### Automation and Scripting

```bash
# Batch metadata updates
for asset in $(vamscli assets list -d my-db --json-output | jq -r '.assets[].assetId'); do
  vamscli metadata asset update -d my-db -a "$asset" --json-input '[
    {"metadataKey": "processed", "metadataValue": "true", "metadataValueType": "boolean"},
    {"metadataKey": "batch_id", "metadataValue": "batch-2024-001", "metadataValueType": "string"},
    {"metadataKey": "processed_date", "metadataValue": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'", "metadataValueType": "string"}
  ]'
done

# Extract metadata for reporting
vamscli metadata asset list -d my-db -a my-asset --json-output | jq '.metadata[] | select(.metadataKey == "title" or .metadataKey == "priority")'

# Conditional metadata updates
current_status=$(vamscli metadata asset list -d my-db -a my-asset --json-output | jq -r '.metadata[] | select(.metadataKey == "status") | .metadataValue')
if [ "$current_status" = "draft" ]; then
  vamscli metadata asset update -d my-db -a my-asset --json-input '[
    {"metadataKey": "status", "metadataValue": "review", "metadataValueType": "string"},
    {"metadataKey": "review_date", "metadataValue": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'", "metadataValueType": "string"}
  ]'
fi
```

## Metadata Best Practices

### Metadata Structure Guidelines

-   **Use consistent naming**: Use snake_case or camelCase consistently across all metadata
-   **Choose appropriate types**: Use correct metadataValueType for each field
-   **Organize with objects**: Group related metadata in JSON objects for complex data
-   **Include timestamps**: Add creation and modification dates for tracking
-   **Document your schema**: Maintain documentation of your metadata structure

### Entity-Specific Metadata

-   **Asset Metadata**: Project info, ownership, status, global properties
-   **File Metadata**: File-specific properties, processing info, technical details
-   **Asset Link Metadata**: Relationship properties, creation info, link-specific data
-   **Database Metadata**: Project-level info, client details, database-wide settings

### Update Mode Selection

-   **Use 'update' (default) when:**
    -   Adding new metadata fields
    -   Updating specific fields while preserving others
    -   Incrementally building metadata over time
-   **Use 'replace_all' when:**
    -   Completely resetting metadata to a known state
    -   Removing all old fields and starting fresh
    -   Ensuring no legacy fields remain

### Example Metadata Structures

**Asset Metadata:**

```json
[
    { "metadataKey": "title", "metadataValue": "3D Building Model", "metadataValueType": "string" },
    { "metadataKey": "category", "metadataValue": "architecture", "metadataValueType": "string" },
    { "metadataKey": "priority", "metadataValue": "1", "metadataValueType": "number" },
    { "metadataKey": "active", "metadataValue": "true", "metadataValueType": "boolean" },
    {
        "metadataKey": "created_date",
        "metadataValue": "2024-01-15T10:30:00Z",
        "metadataValueType": "string"
    },
    {
        "metadataKey": "properties",
        "metadataValue": "{\"polygons\": 75000, \"materials\": [\"concrete\", \"glass\"]}",
        "metadataValueType": "object"
    }
]
```

**File Metadata:**

```json
[
    { "metadataKey": "lod_level", "metadataValue": "high", "metadataValueType": "string" },
    { "metadataKey": "optimized", "metadataValue": "true", "metadataValueType": "boolean" },
    { "metadataKey": "file_size_mb", "metadataValue": "15.2", "metadataValueType": "number" },
    { "metadataKey": "compression", "metadataValue": "draco", "metadataValueType": "string" }
]
```

**Asset Link Metadata:**

```json
[
    {
        "metadataKey": "relationship_type",
        "metadataValue": "parent-child",
        "metadataValueType": "string"
    },
    {
        "metadataKey": "created_by",
        "metadataValue": "user@example.com",
        "metadataValueType": "string"
    },
    { "metadataKey": "link_strength", "metadataValue": "0.95", "metadataValueType": "number" }
]
```

**Database Metadata:**

```json
[
    {
        "metadataKey": "project",
        "metadataValue": "Downtown Complex",
        "metadataValueType": "string"
    },
    {
        "metadataKey": "client",
        "metadataValue": "City Planning Dept",
        "metadataValueType": "string"
    },
    { "metadataKey": "budget", "metadataValue": "1500000", "metadataValueType": "number" },
    { "metadataKey": "active", "metadataValue": "true", "metadataValueType": "boolean" }
]
```

## Migration from Old Commands

If you're migrating from the old metadata commands (pre-v2.2), here are the key changes:

**Old Commands (Deprecated):**

```bash
# Old asset metadata commands
vamscli metadata get -d my-db -a my-asset
vamscli metadata create -d my-db -a my-asset --json-input '{...}'
vamscli metadata update -d my-db -a my-asset --json-input '{...}'
vamscli metadata delete -d my-db -a my-asset

# Old asset link metadata commands
vamscli asset-links-metadata get --asset-link-id link-uuid
vamscli asset-links-metadata create --asset-link-id link-uuid --json-input '{...}'
vamscli asset-links-metadata update --asset-link-id link-uuid --json-input '{...}'
vamscli asset-links-metadata delete --asset-link-id link-uuid
```

**New Commands (v2.2+):**

```bash
# New unified metadata commands
vamscli metadata asset list -d my-db -a my-asset
vamscli metadata asset update -d my-db -a my-asset --json-input '[...]'
vamscli metadata asset delete -d my-db -a my-asset --json-input '["key1", "key2"]'

vamscli metadata file list -d my-db -a my-asset -f file-uuid
vamscli metadata file update -d my-db -a my-asset -f file-uuid --json-input '[...]'
vamscli metadata file delete -d my-db -a my-asset -f file-uuid --json-input '["key1"]'

vamscli metadata asset-link list --asset-link-id link-uuid
vamscli metadata asset-link update --asset-link-id link-uuid --json-input '[...]'
vamscli metadata asset-link delete --asset-link-id link-uuid --json-input '["key1"]'

vamscli metadata database list -d my-db
vamscli metadata database update -d my-db --json-input '[...]'
vamscli metadata database delete -d my-db --json-input '["key1"]'
```

**Key Differences:**

1. **Unified Structure**: All entity types use the same command pattern
2. **Bulk Operations**: All operations support multiple metadata items
3. **Array Format**: JSON input uses array format with explicit type information
4. **Update Modes**: New `--update-type` parameter for upsert vs replace behavior
5. **Delete Format**: Delete operations use array of keys instead of full metadata objects

## Related Commands

-   **[Metadata Schema Management](metadata-schema.md)** - Manage metadata validation rules
-   **[Asset Management](asset-management.md)** - Create and manage assets
-   **[File Operations](file-operations.md)** - Upload and manage files
-   **[Asset Links](asset-management.md#asset-relationship-management)** - Create relationships between assets
-   **[Database Administration](database-admin.md)** - Manage databases

## Troubleshooting

For metadata-related issues, see:

-   **[Database and Tag Issues](../troubleshooting/database-tag-issues.md)** - Metadata validation and schema problems
-   **[Asset and File Issues](../troubleshooting/asset-file-issues.md)** - Asset and file metadata problems
-   **[General Troubleshooting](../troubleshooting/general-troubleshooting.md)** - Debug mode and logging
