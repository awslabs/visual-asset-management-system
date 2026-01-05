# Metadata Schema Commands

Metadata schema commands allow you to view and manage metadata schema definitions in VAMS. Metadata schemas define the structure, validation rules, and data types for metadata that can be associated with different entities (databases, assets, files, asset links).

## Table of Contents

-   [Overview](#overview)
-   [Entity Types](#entity-types)
-   [Commands](#commands)
    -   [list](#list)
    -   [get](#get)
-   [Common Options](#common-options)
-   [Examples](#examples)
-   [Troubleshooting](#troubleshooting)

## Overview

Metadata schemas provide a structured way to define and validate metadata across your VAMS deployment. Each schema:

-   Defines field names, data types, and validation rules
-   Can be required or optional
-   Supports field dependencies
-   Can restrict values with controlled lists
-   Can be scoped to specific file types (for file metadata)

## Entity Types

Metadata schemas support five entity types:

| Entity Type         | Description                   | Use Case                                             |
| ------------------- | ----------------------------- | ---------------------------------------------------- |
| `databaseMetadata`  | Metadata for databases        | Database-level configuration and properties          |
| `assetMetadata`     | Metadata for assets           | Asset classification, categorization, and properties |
| `fileMetadata`      | Metadata for files            | File-specific information and attributes             |
| `fileAttribute`     | File attributes (string-only) | Simple file tags and labels                          |
| `assetLinkMetadata` | Metadata for asset links      | Relationship-specific information                    |

## Commands

### list

List metadata schemas with optional filters.

```bash
vamscli metadata-schema list [OPTIONS]
```

#### Options

| Option              | Type    | Description                                                                                             |
| ------------------- | ------- | ------------------------------------------------------------------------------------------------------- |
| `-d, --database-id` | TEXT    | Filter by database ID                                                                                   |
| `-e, --entity-type` | CHOICE  | Filter by entity type (databaseMetadata, assetMetadata, fileMetadata, fileAttribute, assetLinkMetadata) |
| `--page-size`       | INTEGER | Number of items per page                                                                                |
| `--max-items`       | INTEGER | Maximum total items to fetch (default: 1000)                                                            |
| `--starting-token`  | TEXT    | Token for pagination                                                                                    |
| `--json-input`      | TEXT    | JSON input file path or JSON string with parameters                                                     |
| `--json-output`     | FLAG    | Output raw JSON response                                                                                |

#### Examples

**List all metadata schemas:**

```bash
vamscli metadata-schema list
```

**List schemas for a specific database:**

```bash
vamscli metadata-schema list -d my-database
```

**List schemas by entity type:**

```bash
vamscli metadata-schema list -e assetMetadata
vamscli metadata-schema list -e fileMetadata
vamscli metadata-schema list -e fileAttribute
```

**List schemas with both filters:**

```bash
vamscli metadata-schema list -d my-database -e assetMetadata
```

**With pagination:**

```bash
# Custom page size and max items
vamscli metadata-schema list --page-size 50 --max-items 200

# Manual pagination with token
vamscli metadata-schema list --starting-token "eyJtZXRhZGF0YVNjaGVtYUlkIjoi..."
```

**JSON output:**

```bash
vamscli metadata-schema list -d my-database --json-output
```

**JSON input for complex parameters:**

```bash
vamscli metadata-schema list --json-input '{"databaseId":"my-db","metadataEntityType":"assetMetadata","maxItems":100}'

# Or from file
vamscli metadata-schema list --json-input params.json
```

#### Output Format

**CLI Output:**

```
Found 2 metadata schema(s):
====================================================================================================
ID: schema-abc123
Database: my-database
Name: Asset Metadata Schema
Entity Type: assetMetadata
Enabled: Yes
Fields: 5
Created: 2024-01-15T10:30:00Z
Modified: 2024-01-20T14:45:00Z
----------------------------------------------------------------------------------------------------
ID: schema-def456
Database: my-database
Name: File Metadata Schema
Entity Type: fileMetadata
Enabled: Yes
Fields: 3
File Restrictions: .glb,.gltf,.obj
Created: 2024-01-16T11:00:00Z
----------------------------------------------------------------------------------------------------

Next token: eyJtZXRhZGF0YVNjaGVtYUlkIjoi...
Use --starting-token to get the next page
```

**JSON Output:**

```json
{
    "Items": [
        {
            "metadataSchemaId": "schema-abc123",
            "databaseId": "my-database",
            "schemaName": "Asset Metadata Schema",
            "metadataSchemaEntityType": "assetMetadata",
            "enabled": true,
            "fields": {
                "fields": [
                    {
                        "metadataFieldKeyName": "title",
                        "metadataFieldValueType": "string",
                        "required": true,
                        "defaultMetadataFieldValue": "Untitled"
                    }
                ]
            },
            "dateCreated": "2024-01-15T10:30:00Z",
            "dateModified": "2024-01-20T14:45:00Z",
            "createdBy": "user@example.com",
            "modifiedBy": "admin@example.com"
        }
    ],
    "NextToken": "eyJtZXRhZGF0YVNjaGVtYUlkIjoi..."
}
```

---

### get

Get a specific metadata schema by ID.

```bash
vamscli metadata-schema get [OPTIONS]
```

#### Options

| Option              | Type | Required | Description              |
| ------------------- | ---- | -------- | ------------------------ |
| `-d, --database-id` | TEXT | Yes      | Database ID              |
| `-s, --schema-id`   | TEXT | Yes      | Metadata schema ID       |
| `--json-output`     | FLAG | No       | Output raw JSON response |

#### Examples

**Get a specific schema:**

```bash
vamscli metadata-schema get -d my-database -s schema-abc123
```

**JSON output:**

```bash
vamscli metadata-schema get -d my-database -s schema-abc123 --json-output
```

#### Output Format

**CLI Output:**

```
Metadata Schema Details:
====================================================================================================
  ID: schema-abc123
  Database: my-database
  Name: Asset Metadata Schema
  Entity Type: assetMetadata
  Enabled: Yes
  Created: 2024-01-15T10:30:00Z
  Modified: 2024-01-20T14:45:00Z
  Created By: user@example.com
  Modified By: admin@example.com

Fields (4):
----------------------------------------------------------------------------------------------------
Field Name                     Type                 Required   Default
----------------------------------------------------------------------------------------------------
title                          string               Yes        Untitled
  └─ Depends on: None
category                       string               No         None
  └─ Depends on: title
priority                       number               Yes        None
  └─ Depends on: category, title
status                         inline_controlled_list Yes      draft
  └─ Allowed values: draft, review, approved, published
----------------------------------------------------------------------------------------------------
```

**JSON Output:**

```json
{
    "metadataSchemaId": "schema-abc123",
    "databaseId": "my-database",
    "schemaName": "Asset Metadata Schema",
    "metadataSchemaEntityType": "assetMetadata",
    "enabled": true,
    "fields": {
        "fields": [
            {
                "metadataFieldKeyName": "title",
                "metadataFieldValueType": "string",
                "required": true,
                "defaultMetadataFieldValue": "Untitled"
            },
            {
                "metadataFieldKeyName": "category",
                "metadataFieldValueType": "string",
                "required": false,
                "dependsOnFieldKeyName": ["title"]
            },
            {
                "metadataFieldKeyName": "priority",
                "metadataFieldValueType": "number",
                "required": true,
                "dependsOnFieldKeyName": ["category", "title"]
            },
            {
                "metadataFieldKeyName": "status",
                "metadataFieldValueType": "inline_controlled_list",
                "required": true,
                "controlledListKeys": ["draft", "review", "approved", "published"],
                "defaultMetadataFieldValue": "draft"
            }
        ]
    },
    "dateCreated": "2024-01-15T10:30:00Z",
    "dateModified": "2024-01-20T14:45:00Z",
    "createdBy": "user@example.com",
    "modifiedBy": "admin@example.com"
}
```

## Common Options

### Global Options

All metadata schema commands support these global options:

-   `--profile PROFILE_NAME` - Use a specific profile
-   `--token-override TOKEN` - Override authentication token for this command
-   `--verbose` - Enable verbose logging

### JSON Input/Output

**JSON Input** (`--json-input`):

-   Accepts JSON string or file path
-   Overrides command-line options
-   Useful for complex parameter sets

**JSON Output** (`--json-output`):

-   Returns raw API response as JSON
-   Suitable for scripting and automation
-   Suppresses CLI formatting and status messages

## Examples

### Basic Usage

**View all schemas in your deployment:**

```bash
vamscli metadata-schema list
```

**View schemas for a specific database:**

```bash
vamscli metadata-schema list -d production-db
```

**View only asset metadata schemas:**

```bash
vamscli metadata-schema list -e assetMetadata
```

### Filtering and Pagination

**Filter by database and entity type:**

```bash
vamscli metadata-schema list -d my-database -e fileMetadata
```

**Custom pagination:**

```bash
# First page with 50 items
vamscli metadata-schema list --page-size 50 --max-items 200

# Next page using token from previous response
vamscli metadata-schema list --starting-token "eyJtZXRhZGF0YVNjaGVtYUlkIjoi..."
```

### Detailed Schema Information

**Get complete schema details:**

```bash
vamscli metadata-schema get -d my-database -s schema-abc123
```

**Get schema details as JSON:**

```bash
vamscli metadata-schema get -d my-database -s schema-abc123 --json-output > schema.json
```

### Automation and Scripting

**List schemas and parse with jq:**

```bash
vamscli metadata-schema list -d my-database --json-output | jq '.Items[] | {id: .metadataSchemaId, name: .schemaName, type: .metadataSchemaEntityType}'
```

**Get schema and extract field names:**

```bash
vamscli metadata-schema get -d my-database -s schema-abc123 --json-output | jq '.fields.fields[] | .metadataFieldKeyName'
```

**Count schemas by entity type:**

```bash
for type in databaseMetadata assetMetadata fileMetadata fileAttribute assetLinkMetadata; do
  count=$(vamscli metadata-schema list -e $type --json-output | jq '.Items | length')
  echo "$type: $count schemas"
done
```

### Using Different Profiles

**List schemas from production environment:**

```bash
vamscli --profile production metadata-schema list -d prod-database
```

**Get schema with token override:**

```bash
vamscli --token-override "your-token-here" metadata-schema get -d my-database -s schema-abc123
```

## Troubleshooting

### Common Issues

**"Database not found" error:**

```bash
# Verify database exists
vamscli database list

# Check if you have access to the database
vamscli database get -d my-database
```

**"Metadata schema not found" error:**

```bash
# List available schemas for the database
vamscli metadata-schema list -d my-database

# Verify the schema ID is correct
vamscli metadata-schema list -d my-database --json-output | jq '.Items[] | .metadataSchemaId'
```

**"Authentication failed" error:**

```bash
# Re-authenticate
vamscli auth login

# Check authentication status
vamscli auth status
```

**Empty results when filtering:**

```bash
# Try without filters to see all schemas
vamscli metadata-schema list

# Check if the entity type is correct (case-sensitive)
vamscli metadata-schema list -e assetMetadata  # Correct
vamscli metadata-schema list -e AssetMetadata  # May not work
```

### Pagination Issues

**Token expired or invalid:**

-   Pagination tokens are temporary and may expire
-   Always use the `NextToken` from the most recent response
-   Don't reuse old tokens

**Missing results:**

-   Check if `maxItems` limit was reached
-   Use `--max-items` to increase the limit
-   Use pagination to fetch all results

### Performance Tips

**For large result sets:**

```bash
# Use smaller page sizes to reduce memory usage
vamscli metadata-schema list --page-size 50

# Filter by database to reduce result set
vamscli metadata-schema list -d specific-database

# Filter by entity type for targeted results
vamscli metadata-schema list -e assetMetadata
```

**For automation:**

```bash
# Use JSON output for reliable parsing
vamscli metadata-schema list --json-output

# Combine with jq for data extraction
vamscli metadata-schema list --json-output | jq '.Items[] | select(.enabled == true)'
```

## Field Data Types

Metadata schemas support the following field data types:

| Data Type                | Description           | Example Values                        |
| ------------------------ | --------------------- | ------------------------------------- |
| `string`                 | Text values           | "My Asset", "Description text"        |
| `number`                 | Numeric values        | 42, 3.14, -10                         |
| `boolean`                | True/false values     | true, false                           |
| `array`                  | List of values        | ["tag1", "tag2", "tag3"]              |
| `object`                 | Nested JSON object    | {"key": "value", "nested": {...}}     |
| `inline_controlled_list` | Predefined value list | Must be one of the controlledListKeys |

## Field Properties

Each field in a metadata schema can have:

-   **metadataFieldKeyName**: The field name/key
-   **metadataFieldValueType**: The data type (see above)
-   **required**: Whether the field is mandatory
-   **defaultMetadataFieldValue**: Default value if not provided
-   **dependsOnFieldKeyName**: List of fields this field depends on
-   **controlledListKeys**: Allowed values (for inline_controlled_list type only)

## Schema Properties

Each metadata schema includes:

-   **metadataSchemaId**: Unique identifier
-   **databaseId**: Database the schema belongs to (or "GLOBAL")
-   **schemaName**: Human-readable name
-   **metadataSchemaEntityType**: Entity type (see above)
-   **enabled**: Whether the schema is active
-   **fileKeyTypeRestriction**: File extensions (for fileMetadata/fileAttribute only)
-   **fields**: Array of field definitions
-   **dateCreated**: Creation timestamp
-   **dateModified**: Last modification timestamp
-   **createdBy**: Creator user ID
-   **modifiedBy**: Last modifier user ID

## Related Commands

-   `vamscli database list` - List databases
-   `vamscli database get` - Get database details
-   `vamscli metadata get` - Get metadata for assets/files
-   `vamscli metadata create` - Create metadata
-   `vamscli metadata update` - Update metadata

## See Also

-   [Database Administration Commands](database-admin.md)
-   [Asset Management Commands](asset-management.md)
-   [Metadata Schema Troubleshooting](../troubleshooting/database-tag-issues.md#metadata-schema-issues)
