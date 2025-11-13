# Metadata Management Commands

This document covers VamsCLI metadata management commands for assets, files, and metadata schemas.

## Metadata Management Commands

VamsCLI provides comprehensive metadata management capabilities for attaching custom key-value data to assets and individual files within assets, as well as managing the metadata schema that defines the structure and validation rules for metadata fields.

## Metadata Schema Management

### `vamscli metadata-schema get`

Get the metadata schema configuration for a database, showing all defined metadata fields, their data types, requirements, and dependencies.

**Required Options:**

-   `-d, --database`: Database ID to get metadata schema for (required)

**Options:**

-   `--max-items`: Maximum number of items to return (default: 1000)
-   `--page-size`: Number of items per page (default: 100)
-   `--starting-token`: Token for pagination
-   `--json-input`: JSON input file path or JSON string with pagination parameters
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Get metadata schema for a database
vamscli metadata-schema get -d my-database

# Get with pagination
vamscli metadata-schema get -d my-database --max-items 50 --page-size 25

# Get with JSON output for automation
vamscli metadata-schema get -d my-database --json-output

# Get with JSON input for pagination
vamscli metadata-schema get -d my-database --json-input '{"maxItems":100,"pageSize":50}'

# Get with JSON input file
vamscli metadata-schema get -d my-database --json-input pagination.json

# Get with specific profile
vamscli metadata-schema get -d my-database --profile production
```

**JSON Input Format (Pagination Parameters):**

```json
{
    "maxItems": 100,
    "pageSize": 50,
    "startingToken": "..."
}
```

**CLI Output Format:**

```
Metadata Schema for Database (3 field(s)):
Field Name               Data Type       Required   Depends On
--------------------------------------------------------------------------------
title                    string          Yes        None
category                 string          No         title
priority                 number          Yes        category, title
--------------------------------------------------------------------------------
More results available. Use --starting-token 'token' to see additional fields.
```

**JSON Output Format:**

```json
{
    "message": {
        "Items": [
            {
                "field": "title",
                "datatype": "string",
                "required": true,
                "dependsOn": []
            },
            {
                "field": "category",
                "datatype": "string",
                "required": false,
                "dependsOn": ["title"]
            }
        ],
        "NextToken": "pagination-token"
    }
}
```

**Schema Field Properties:**

-   **field**: Name of the metadata field
-   **datatype**: Data type (string, number, boolean, array, object, enum, datetime)
-   **required**: Whether the field is required when creating metadata
-   **dependsOn**: Array of other fields that must be filled out first

**Use Cases:**

-   **Validation**: Understand metadata requirements before creating assets
-   **Documentation**: Generate documentation of metadata structure
-   **Automation**: Build forms or validation logic based on schema
-   **Compliance**: Ensure metadata follows organizational standards

## Asset and File Metadata Management

### `vamscli metadata get`

Get metadata for an asset or specific file.

**Required Options:**

-   `-d, --database`: Database ID containing the asset (required)
-   `-a, --asset`: Asset ID to get metadata for (required)

**Options:**

-   `--file-path`: File path for file-specific metadata (optional)
-   `--json-input`: JSON input file path or JSON string with parameters
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Get asset metadata
vamscli metadata get -d my-database -a my-asset

# Get file-specific metadata
vamscli metadata get -d my-database -a my-asset --file-path "/models/file.gltf"

# Get with JSON input
vamscli metadata get --json-input '{"database_id": "my-db", "asset_id": "my-asset"}'

# Get with JSON output for automation
vamscli metadata get -d my-database -a my-asset --json-output
```

**JSON Input Format:**

```json
{
    "database_id": "my-database",
    "asset_id": "my-asset",
    "file_path": "/models/file.gltf"
}
```

**Output Features:**

-   Shows all metadata key-value pairs
-   Supports complex JSON values (objects, arrays)
-   CLI-friendly formatted output by default
-   Raw JSON output available for automation
-   File-specific metadata when file path provided

### `vamscli metadata create`

Create metadata for an asset or specific file.

**Required Options:**

-   `-d, --database`: Database ID containing the asset (required)
-   `-a, --asset`: Asset ID to add metadata to (required)

**Options:**

-   `--file-path`: File path for file-specific metadata (optional)
-   `--json-input`: JSON input file path or JSON string with metadata
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Create metadata interactively
vamscli metadata create -d my-database -a my-asset
# Prompts: Enter key, enter value (supports JSON)

# Create file-specific metadata
vamscli metadata create -d my-database -a my-asset --file-path "/models/file.gltf"

# Create with JSON input string
vamscli metadata create -d my-database -a my-asset --json-input '{"title": "My Asset", "tags": ["3d", "model"], "properties": {"polygons": 50000}}'

# Create from JSON file
vamscli metadata create -d my-database -a my-asset --json-input @metadata.json

# Create with JSON output for automation
vamscli metadata create -d my-database -a my-asset --json-input '{"title": "Test Asset"}' --json-output
```

**JSON Input Format (Direct Metadata):**

```json
{
    "database_id": "my-database",
    "asset_id": "my-asset",
    "file_path": "/models/file.gltf",
    "title": "My 3D Model",
    "category": "architecture",
    "properties": {
        "polygons": 50000,
        "materials": ["wood", "metal"]
    },
    "tags": ["building", "exterior"],
    "active": true,
    "priority": 1
}
```

**JSON Input Format (Explicit Metadata Key):**

```json
{
    "database_id": "my-database",
    "asset_id": "my-asset",
    "metadata": {
        "title": "My 3D Model",
        "category": "architecture",
        "properties": {
            "polygons": 50000,
            "materials": ["wood", "metal"]
        }
    }
}
```

**Interactive Mode Features:**

-   Prompts for key-value pairs
-   Supports JSON values (objects, arrays, numbers, booleans)
-   Smart JSON parsing with string fallback
-   Allows overwriting existing keys
-   Type 'done' to finish input

**Supported Value Types:**

-   **Strings**: Plain text values
-   **Numbers**: Integers and floats (parsed automatically)
-   **Booleans**: true/false values (parsed automatically)
-   **Objects**: JSON objects like `{"key": "value", "number": 42}`
-   **Arrays**: JSON arrays like `["item1", "item2", 123]`

### `vamscli metadata update`

Update existing metadata for an asset or specific file.

**Required Options:**

-   `-d, --database`: Database ID containing the asset (required)
-   `-a, --asset`: Asset ID to update metadata for (required)

**Options:**

-   `--file-path`: File path for file-specific metadata (optional)
-   `--json-input`: JSON input file path or JSON string with metadata
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Update metadata interactively
vamscli metadata update -d my-database -a my-asset
# Prompts for key-value pairs to update

# Update file-specific metadata
vamscli metadata update -d my-database -a my-asset --file-path "/models/file.gltf"

# Update with JSON input string
vamscli metadata update -d my-database -a my-asset --json-input '{"title": "Updated Asset", "version": 2, "last_modified": "2024-01-15"}'

# Update from JSON file
vamscli metadata update -d my-database -a my-asset --json-input @updated_metadata.json

# Update with JSON output for automation
vamscli metadata update -d my-database -a my-asset --json-input '{"status": "reviewed"}' --json-output
```

**JSON Input Format:**
Same as create command - supports both direct metadata and explicit metadata key formats.

**Update Behavior:**

-   Merges new metadata with existing metadata
-   Overwrites existing keys with new values
-   Adds new keys that don't exist
-   Preserves existing keys not mentioned in update
-   Supports partial updates

### `vamscli metadata delete`

Delete metadata for an asset or specific file.

**Required Options:**

-   `-d, --database`: Database ID containing the asset (required)
-   `-a, --asset`: Asset ID to delete metadata from (required)

**Options:**

-   `--file-path`: File path for file-specific metadata (optional)
-   `--json-input`: JSON input file path or JSON string with parameters
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Delete asset metadata (requires confirmation)
vamscli metadata delete -d my-database -a my-asset

# Delete file-specific metadata
vamscli metadata delete -d my-database -a my-asset --file-path "/models/file.gltf"

# Delete with JSON input
vamscli metadata delete --json-input '{"database_id": "my-db", "asset_id": "my-asset"}'

# Delete with JSON output for automation
vamscli metadata delete -d my-database -a my-asset --json-output
```

**JSON Input Format:**

```json
{
    "database_id": "my-database",
    "asset_id": "my-asset",
    "file_path": "/models/file.gltf"
}
```

**Safety Features:**

-   Interactive confirmation prompt
-   Clear warnings about permanent deletion
-   Shows what will be deleted (asset vs file metadata)
-   Cannot be undone once confirmed

**What Gets Deleted:**

-   **Asset Metadata**: All metadata associated with the asset
-   **File Metadata**: All metadata associated with the specific file
-   **Hierarchical Metadata**: File metadata inherits from parent directories and asset

## Metadata Management Workflow Examples

### Basic Metadata Operations

```bash
# Create asset with initial metadata
vamscli metadata create -d my-db -a my-asset --json-input '{
  "title": "3D Building Model",
  "category": "architecture",
  "created_by": "john.doe@example.com",
  "properties": {
    "polygons": 75000,
    "materials": ["concrete", "glass", "steel"],
    "dimensions": {"width": 50, "height": 120, "depth": 30}
  },
  "tags": ["building", "commercial", "downtown"],
  "priority": 1,
  "active": true
}'

# Add file-specific metadata
vamscli metadata create -d my-db -a my-asset --file-path "/models/building.gltf" --json-input '{
  "lod_level": "high",
  "optimized": true,
  "file_size_mb": 15.2,
  "compression": "draco"
}'

# Get all metadata
vamscli metadata get -d my-db -a my-asset
vamscli metadata get -d my-db -a my-asset --file-path "/models/building.gltf"
```

### Interactive Metadata Creation

```bash
# Interactive metadata creation
vamscli metadata create -d my-db -a my-asset

# Example interactive session:
# Enter metadata key (or 'done' to finish): title
# Enter value for 'title' (JSON supported): My 3D Model
# Added: title = "My 3D Model"
#
# Enter metadata key (or 'done' to finish): properties
# Enter value for 'properties' (JSON supported): {"polygons": 50000, "textured": true}
# Added: properties = {"polygons": 50000, "textured": true}
#
# Enter metadata key (or 'done' to finish): tags
# Enter value for 'tags' (JSON supported): ["architecture", "building"]
# Added: tags = ["architecture", "building"]
#
# Enter metadata key (or 'done' to finish): done
```

### Metadata Updates and Management

```bash
# Update specific metadata fields
vamscli metadata update -d my-db -a my-asset --json-input '{
  "title": "Updated 3D Building Model",
  "version": 2,
  "last_modified": "2024-01-15T14:30:00Z",
  "properties": {
    "polygons": 85000,
    "materials": ["concrete", "glass", "steel", "aluminum"],
    "optimized": true
  }
}'

# Update file-specific metadata
vamscli metadata update -d my-db -a my-asset --file-path "/models/building.gltf" --json-input '{
  "lod_level": "ultra",
  "compression": "meshopt",
  "file_size_mb": 18.7
}'

# Check updated metadata
vamscli metadata get -d my-db -a my-asset --json-output | jq '.metadata'
```

### Automation and Scripting

```bash
# Batch metadata operations with JSON output
for asset in $(vamscli assets list -d my-db --json-output | jq -r '.assets[].assetId'); do
  vamscli metadata create -d my-db -a "$asset" --json-input '{
    "processed": true,
    "batch_id": "batch-2024-001",
    "processed_date": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
  }'
done

# Extract metadata for reporting
vamscli metadata get -d my-db -a my-asset --json-output | jq '.metadata | {title, category, properties}'
```

### File Hierarchy Metadata

```bash
# Asset-level metadata (applies to entire asset)
vamscli metadata create -d my-db -a building-model --json-input '{
  "project": "Downtown Complex",
  "client": "City Planning Dept",
  "status": "approved"
}'

# Directory-level metadata (applies to files in directory)
vamscli metadata create -d my-db -a building-model --file-path "/textures/" --json-input '{
  "resolution": "4K",
  "format": "PNG",
  "color_space": "sRGB"
}'

# File-specific metadata (applies to individual file)
vamscli metadata create -d my-db -a building-model --file-path "/models/building.gltf" --json-input '{
  "lod_level": "high",
  "polygon_count": 75000,
  "optimized": true
}'

# Metadata inheritance: file inherits from directory and asset levels
vamscli metadata get -d my-db -a building-model --file-path "/models/building.gltf"
# Shows combined metadata from asset, directory, and file levels
```

## Metadata Best Practices

### Metadata Structure Guidelines

-   **Use consistent naming**: Use snake_case or camelCase consistently
-   **Organize with objects**: Group related metadata in JSON objects
-   **Include timestamps**: Add creation and modification dates
-   **Use appropriate types**: Numbers for numeric data, booleans for flags
-   **Document metadata schema**: Maintain documentation of your metadata structure

### File vs Asset Metadata

-   **Asset Metadata**: Project info, ownership, status, global properties
-   **Directory Metadata**: Common properties for files in a directory
-   **File Metadata**: File-specific properties, processing info, technical details

### Example Metadata Schema

```json
{
    "title": "Human-readable asset title",
    "description": "Detailed asset description",
    "category": "Asset category (architecture, character, vehicle, etc.)",
    "project": "Project or client name",
    "created_by": "Creator email or name",
    "created_date": "2024-01-15T10:30:00Z",
    "last_modified": "2024-01-20T14:45:00Z",
    "status": "draft|review|approved|archived",
    "priority": 1,
    "properties": {
        "polygon_count": 50000,
        "materials": ["material1", "material2"],
        "dimensions": { "width": 10, "height": 5, "depth": 8 },
        "file_size_mb": 25.6
    },
    "tags": ["tag1", "tag2", "tag3"],
    "custom_fields": {
        "client_id": "CLIENT-001",
        "project_phase": "phase-2",
        "approval_required": true
    }
}
```

### Automation Examples

```bash
# Bulk metadata updates
cat asset_list.txt | while read asset_id; do
  vamscli metadata update -d my-db -a "$asset_id" --json-input '{
    "last_processed": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
    "batch_id": "batch-2024-001"
  }'
done

# Extract metadata for reporting
vamscli metadata get -d my-db -a my-asset --json-output | jq '.metadata | {
  title,
  category,
  status,
  created_date,
  properties: {
    polygon_count: .properties.polygon_count,
    file_size_mb: .properties.file_size_mb
  }
}'

# Conditional metadata updates
current_status=$(vamscli metadata get -d my-db -a my-asset --json-output | jq -r '.metadata.status // "unknown"')
if [ "$current_status" = "draft" ]; then
  vamscli metadata update -d my-db -a my-asset --json-input '{"status": "review", "review_date": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}'
fi
```
