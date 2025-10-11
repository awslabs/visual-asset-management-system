# Asset Management Commands

This document covers VamsCLI asset management, asset versioning, and asset links commands.

## Asset Management Commands

### `vamscli assets create`

Create a new asset in VAMS.

**Options:**

-   `-d, --database-id`: Database ID where the asset will be created (required)
-   `--asset-id`: Specific asset ID (auto-generated if not provided)
-   `--name`: Asset name (required unless using --json-input)
-   `--description`: Asset description (required unless using --json-input)
-   `--distributable/--no-distributable`: Whether the asset is distributable
-   `--tags`: Asset tags (can be used multiple times)
-   `--bucket-key`: Existing S3 bucket key to use
-   `--json-input`: JSON input file path or JSON string with all asset data
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Basic asset creation
vamscli assets create -d my-database --name "My Asset" --description "Asset description"

# Asset with tags and distributable flag
vamscli assets create -d my-database --name "Tagged Asset" --description "With tags" --tags tag1 --tags tag2 --distributable

# Using JSON input
vamscli assets create -d my-database --json-input '{"assetName":"test","description":"desc","isDistributable":true}'

# Using JSON input from file
vamscli assets create -d my-database --json-input @asset-data.json --json-output
```

### `vamscli assets update`

Update an existing asset in VAMS.

**Arguments:**

-   `asset_id`: Asset ID to update (positional argument, required)

**Options:**

-   `-d, --database-id`: Database ID containing the asset (required)
-   `--name`: New asset name
-   `--description`: New asset description
-   `--distributable/--no-distributable`: Update distributable flag
-   `--tags`: New asset tags (replaces existing tags)
-   `--json-input`: JSON input file path or JSON string with update data
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Update asset name
vamscli assets update my-asset -d my-database --name "Updated Name"

# Update multiple fields
vamscli assets update my-asset -d my-database --description "New description" --distributable

# Update tags
vamscli assets update my-asset -d my-database --tags newtag1 --tags newtag2

# Using JSON input
vamscli assets update my-asset -d my-database --json-input '{"assetName":"updated","tags":["new","tags"]}'
```

### `vamscli assets get`

Get details for a specific asset.

**Arguments:**

-   `asset_id`: Asset ID to retrieve (positional argument, required)

**Options:**

-   `-d, --database-id`: Database ID containing the asset (required)
-   `--show-archived`: Include archived assets in search
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Get asset details
vamscli assets get my-asset -d my-database

# Include archived assets
vamscli assets get my-asset -d my-database --show-archived

# JSON output for automation
vamscli assets get my-asset -d my-database --json-output
```

### `vamscli assets list`

List assets in a database or all assets.

**Options:**

-   `-d, --database-id`: Database ID to list assets from (optional for all assets)
-   `--show-archived`: Include archived assets
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# List assets in specific database
vamscli assets list -d my-database

# List all assets across databases
vamscli assets list

# Include archived assets
vamscli assets list -d my-database --show-archived

# JSON output for automation
vamscli assets list --json-output
```

### `vamscli assets archive`

Archive an asset (soft delete).

**Arguments:**

-   `asset_id`: Asset ID to archive (positional argument, required)

**Options:**

-   `-d, --database`: Database ID containing the asset (required)
-   `--reason`: Reason for archiving the asset
-   `--json-input`: JSON input file path or JSON string with parameters
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Basic archive
vamscli assets archive my-asset -d my-database

# Archive with reason
vamscli assets archive my-asset -d my-database --reason "No longer needed"

# Archive with JSON input from file
vamscli assets archive my-asset -d my-database --json-input archive-params.json

# Archive with JSON input string
vamscli assets archive my-asset -d my-database --json-input '{"reason": "Project completed"}'

# Archive with JSON output for automation
vamscli assets archive my-asset -d my-database --json-output
```

**JSON Input Format:**

```json
{
    "databaseId": "my-database",
    "assetId": "my-asset",
    "reason": "Optional reason for archiving"
}
```

**Features:**

-   Soft delete - asset can be recovered later
-   Asset moves to archived state and won't appear in normal listings
-   Use `--show-archived` flag with other commands to view archived assets
-   Comprehensive error handling with actionable guidance

### `vamscli assets delete`

Permanently delete an asset.

⚠️ **WARNING: This action cannot be undone!** ⚠️

**Arguments:**

-   `asset_id`: Asset ID to delete (positional argument, required)

**Options:**

-   `-d, --database`: Database ID containing the asset (required)
-   `--confirm`: Confirm permanent deletion (required for safety)
-   `--reason`: Reason for deleting the asset
-   `--json-input`: JSON input file path or JSON string with parameters
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Basic delete (requires confirmation)
vamscli assets delete my-asset -d my-database --confirm

# Delete with reason
vamscli assets delete my-asset -d my-database --confirm --reason "Project cancelled"

# Delete with JSON input from file
vamscli assets delete my-asset -d my-database --json-input delete-params.json

# Delete with JSON input string
vamscli assets delete my-asset -d my-database --json-input '{"confirmPermanentDelete": true, "reason": "No longer needed"}'

# Delete with JSON output for automation
vamscli assets delete my-asset -d my-database --confirm --json-output
```

**JSON Input Format:**

```json
{
    "databaseId": "my-database",
    "assetId": "my-asset",
    "confirmPermanentDelete": true,
    "reason": "Optional reason for deletion"
}
```

**Safety Features:**

-   Requires explicit `--confirm` flag
-   Interactive confirmation prompt (unless using JSON input)
-   Clear warnings about permanent deletion
-   Deletes all associated data:
    -   Asset metadata and files
    -   All asset versions and history
    -   Asset links and relationships
    -   Comments and version history
    -   SNS topics and subscriptions

**What Gets Deleted:**
This command permanently removes:

-   The asset record from DynamoDB
-   All asset files and versions from S3
-   All asset links and relationships
-   All comments and version history
-   Associated metadata and auxiliary files
-   SNS topics and email subscriptions

## Asset Version Management Commands

VamsCLI provides comprehensive asset version management capabilities for tracking and managing different versions of assets in VAMS.

### `vamscli asset-version create`

Create a new asset version.

**Required Options:**

-   `-d, --database`: Database ID containing the asset (required)
-   `-a, --asset`: Asset ID to create version for (required)
-   `--comment`: Comment for the new version (required)

**Options:**

-   `--use-latest-files`: Use latest files in S3 (default: true)
-   `--files`: JSON string or file path with specific files to version
-   `--json-input`: JSON input file path or JSON string with complete request data
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Create version with latest files (default behavior)
vamscli asset-version create -d my-db -a my-asset --comment "New version with latest files"

# Create version with specific files
vamscli asset-version create -d my-db -a my-asset --comment "Specific files version" --files '[{"relativeKey":"model.obj","versionId":"abc123","isArchived":false}]'

# Create version with JSON input from file
vamscli asset-version create -d my-db -a my-asset --json-input @version-data.json

# Create version with JSON output for automation
vamscli asset-version create -d my-db -a my-asset --comment "Automated version" --json-output
```

**JSON Input Format:**

```json
{
    "useLatestFiles": true,
    "comment": "Version comment describing the changes"
}
```

**JSON Input Format (with specific files):**

```json
{
    "useLatestFiles": false,
    "comment": "Version with specific files",
    "files": [
        {
            "relativeKey": "model.obj",
            "versionId": "abc123def456",
            "isArchived": false
        },
        {
            "relativeKey": "texture.png",
            "versionId": "def456ghi789",
            "isArchived": false
        }
    ]
}
```

**Features:**

-   Creates snapshot of current asset state
-   Preserves file version history
-   Supports both latest files and specific file versions
-   Automatic version numbering
-   Email notifications to subscribers

### `vamscli asset-version revert`

Revert an asset to a previous version.

**Required Options:**

-   `-d, --database`: Database ID containing the asset (required)
-   `-a, --asset`: Asset ID to revert (required)
-   `-v, --version`: Version ID to revert to (required)

**Options:**

-   `--comment`: Comment for the new version created by revert operation
-   `--json-input`: JSON input file path or JSON string with complete request data
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Revert to previous version
vamscli asset-version revert -d my-db -a my-asset -v 1

# Revert with custom comment
vamscli asset-version revert -d my-db -a my-asset -v 2 --comment "Reverting due to issues found in version 3"

# Revert with JSON input
vamscli asset-version revert -d my-db -a my-asset -v 1 --json-input @revert-data.json

# Revert with JSON output for automation
vamscli asset-version revert -d my-db -a my-asset -v 1 --json-output
```

**JSON Input Format:**

```json
{
    "comment": "Reverting to stable version due to issues"
}
```

**Features:**

-   Creates new version from target version files
-   Preserves audit trail (original versions remain)
-   Handles missing or deleted files gracefully
-   Reports skipped files that couldn't be reverted
-   Email notifications to subscribers

### `vamscli asset-version list`

List all versions for an asset.

**Required Options:**

-   `-d, --database`: Database ID containing the asset (required)
-   `-a, --asset`: Asset ID to list versions for (required)

**Options:**

-   `--max-items`: Maximum number of versions to return (default: 100)
-   `--starting-token`: Pagination token for retrieving additional results
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# List all versions for an asset
vamscli asset-version list -d my-db -a my-asset

# List with pagination
vamscli asset-version list -d my-db -a my-asset --max-items 50

# Continue pagination
vamscli asset-version list -d my-db -a my-asset --starting-token "next-page-token"

# JSON output for automation
vamscli asset-version list -d my-db -a my-asset --json-output
```

**Output Features:**

-   Shows version ID with current version indicator
-   Creation date and creator information
-   Version comments and descriptions
-   File count for each version
-   Pipeline associations
-   Pagination support for assets with many versions

### `vamscli asset-version get`

Get details for a specific asset version.

**Required Options:**

-   `-d, --database`: Database ID containing the asset (required)
-   `-a, --asset`: Asset ID to get version details for (required)
-   `-v, --version`: Version ID to retrieve details for (required)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Get version details
vamscli asset-version get -d my-db -a my-asset -v 1

# Get version details with JSON output
vamscli asset-version get -d my-db -a my-asset -v 2 --json-output
```

**Output Features:**

-   Complete version metadata
-   Detailed file information with sizes
-   File version IDs and modification dates
-   File status indicators (archived, deleted)
-   Human-readable file size formatting

## Asset Links Management Commands

VamsCLI provides comprehensive asset links management capabilities for creating and managing relationships between assets in VAMS.

### `vamscli asset-links create`

Create a new asset link between two assets.

**Required Options:**

-   `--from-asset-id`: Source asset ID (required)
-   `--from-database-id`: Source asset database ID (required)
-   `--to-asset-id`: Target asset ID (required)
-   `--to-database-id`: Target asset database ID (required)
-   `--relationship-type`: Type of relationship - `related` or `parentChild` (required)

**Options:**

-   `--alias-id`: Optional alias ID for multiple parent-child relationships (parentChild type only, max 128 chars)
-   `--tags`: Tags for the asset link (can be used multiple times)
-   `--json-input`: JSON input file path or JSON string with all asset link data
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Create a related relationship
vamscli asset-links create --from-asset-id asset1 --from-database-id db1 --to-asset-id asset2 --to-database-id db2 --relationship-type related

# Create parent-child relationship with tags
vamscli asset-links create --from-asset-id parent --from-database-id db1 --to-asset-id child --to-database-id db1 --relationship-type parentChild --tags tag1 --tags tag2

# Create parent-child relationship with alias ID
vamscli asset-links create --from-asset-id parent --from-database-id db1 --to-asset-id child --to-database-id db1 --relationship-type parentChild --alias-id "primary-link"

# Create multiple relationships between same assets with different aliases
vamscli asset-links create --from-asset-id parent --from-database-id db1 --to-asset-id child --to-database-id db1 --relationship-type parentChild --alias-id "secondary-link"

# Create with comma-separated tags
vamscli asset-links create --from-asset-id asset1 --from-database-id db1 --to-asset-id asset2 --to-database-id db2 --relationship-type related --tags "tag1,tag2,tag3"

# Create with JSON input string including alias
vamscli asset-links create --json-input '{"fromAssetId":"asset1","fromAssetDatabaseId":"db1","toAssetId":"asset2","toAssetDatabaseId":"db2","relationshipType":"parentChild","assetLinkAliasId":"my-alias","tags":["tag1","tag2"]}'

# Create from JSON file
vamscli asset-links create --json-input @link-data.json --json-output
```

**JSON Input Format:**

```json
{
    "fromAssetId": "source-asset",
    "fromAssetDatabaseId": "source-database",
    "toAssetId": "target-asset",
    "toAssetDatabaseId": "target-database",
    "relationshipType": "parentChild",
    "assetLinkAliasId": "optional-alias",
    "tags": ["tag1", "tag2", "tag3"]
}
```

**Relationship Types:**

-   **`related`**: Bidirectional relationship between assets (no hierarchy, aliases not supported)
-   **`parentChild`**: Directional relationship with parent → child hierarchy (includes cycle detection, supports aliases)

**Alias ID Feature:**

The `--alias-id` option enables multiple parent-child relationships between the same pair of assets:

-   **Purpose**: Distinguish between different types of relationships (e.g., "primary-source", "backup-source")
-   **Restriction**: Can ONLY be used with `parentChild` relationship type
-   **Uniqueness**: Each alias must be unique for a given child asset
-   **Max Length**: 128 characters
-   **Optional**: Completely optional - existing functionality works without aliases

**Alias Use Cases:**

1. **Multiple Source Relationships**: Same child derived from same parent in different ways
2. **Versioned Relationships**: Track different versions of parent-child connections
3. **Role-Based Relationships**: Distinguish production vs. staging vs. development links

**Features:**

-   **Cycle Detection**: Prevents creating parent-child relationships that would create cycles
-   **Asset Validation**: Ensures both assets exist before creating link
-   **Permission Checking**: Validates user has permissions on both assets
-   **Duplicate Prevention**: Prevents creating duplicate relationships (considering alias)
-   **Alias Validation**: Enforces alias usage only for parentChild relationships

### `vamscli asset-links get`

Get details for a specific asset link.

**Required Options:**

-   `--asset-link-id`: Asset link ID to retrieve (required)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Get asset link details
vamscli asset-links get --asset-link-id 12345678-1234-1234-1234-123456789012

# Get with JSON output for automation
vamscli asset-links get --asset-link-id 12345678-1234-1234-1234-123456789012 --json-output
```

### `vamscli asset-links update`

Update an existing asset link.

**Required Options:**

-   `--asset-link-id`: Asset link ID to update (required)

**Options:**

-   `--alias-id`: Optional alias ID to update (max 128 chars, parentChild relationships only)
-   `--tags`: New tags for the asset link (replaces existing tags)
-   `--json-input`: JSON input file path or JSON string with update data
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Update tags (replaces all existing tags)
vamscli asset-links update --asset-link-id 12345678-1234-1234-1234-123456789012 --tags new-tag1 --tags new-tag2

# Update alias ID only
vamscli asset-links update --asset-link-id 12345678-1234-1234-1234-123456789012 --alias-id "updated-alias"

# Update both alias and tags
vamscli asset-links update --asset-link-id 12345678-1234-1234-1234-123456789012 --alias-id "new-alias" --tags tag1 --tags tag2

# Clear all tags
vamscli asset-links update --asset-link-id 12345678-1234-1234-1234-123456789012 --tags ""

# Update with JSON input
vamscli asset-links update --asset-link-id 12345678-1234-1234-1234-123456789012 --json-input '{"assetLinkAliasId":"my-alias","tags":["updated-tag1","updated-tag2"]}'

# Update from JSON file
vamscli asset-links update --asset-link-id 12345678-1234-1234-1234-123456789012 --json-input @update-data.json --json-output
```

**JSON Input Format:**

```json
{
    "assetLinkAliasId": "updated-alias",
    "tags": ["updated-tag1", "updated-tag2", "updated-tag3"]
}
```

**Updatable Fields:**

-   **Alias ID**: Can update or set alias for parentChild relationships
-   **Tags**: Can update tags for any relationship type

**Current Limitations:**

-   Relationship type and connected assets cannot be changed
-   To change relationship type or assets, delete the link and create a new one

### `vamscli asset-links delete`

Delete an asset link permanently.

**Required Options:**

-   `--asset-link-id`: Asset link ID to delete (required)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Delete asset link (requires confirmation)
vamscli asset-links delete --asset-link-id 12345678-1234-1234-1234-123456789012

# Delete with JSON output
vamscli asset-links delete --asset-link-id 12345678-1234-1234-1234-123456789012 --json-output
```

**Safety Features:**

-   Interactive confirmation prompt
-   Clear warnings about permanent deletion
-   Deletes associated metadata automatically

**What Gets Deleted:**

-   The asset link relationship
-   All associated metadata
-   Link appears in neither asset's link list

### `vamscli asset-links list`

List all asset links for a specific asset.

**Required Options:**

-   `-d, --database-id`: Database ID containing the asset (required)
-   `--asset-id`: Asset ID to get links for (required)

**Options:**

-   `--tree-view`: Display children as a tree structure (for parent-child relationships)
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# List all asset links for an asset
vamscli asset-links list -d my-database --asset-id my-asset

# List with tree view for hierarchical display
vamscli asset-links list -d my-database --asset-id my-asset --tree-view

# List with JSON output for automation
vamscli asset-links list -d my-database --asset-id my-asset --json-output
```

**Output Features:**

-   **Related Assets**: Bidirectional relationships
-   **Parent Assets**: Assets that have this asset as a child
-   **Child Assets**: Assets that are children of this asset
-   **Tree View**: Hierarchical display of parent-child relationships
-   **Alias Display**: Shows alias IDs for links that have them (e.g., "(Alias: primary-link)")
-   **Unauthorized Counts**: Number of linked assets user cannot access

**Example Output with Aliases:**

```
Asset Links for parent-asset in database my-database:
Related Assets (0):
  None

Parent Assets (0):
  None

Child Assets (2):
  • child-asset (my-database) - Link ID: 12345678... (Alias: primary-relationship)
  • child-asset (my-database) - Link ID: 87654321... (Alias: secondary-relationship)
```

## Asset Links Alias ID Feature

### Overview

The `assetLinkAliasId` feature enables multiple parent-child relationships between the same pair of assets by providing a unique identifier for each relationship. This allows for more complex asset hierarchies where the same child asset can have multiple distinct relationships with the same parent asset.

### Key Concepts

**What is an Alias ID?**

An **alias ID** is an optional string identifier (max 128 characters) that can be assigned to a parent-child asset link. It allows you to:

-   Create multiple parent-child relationships between the same parent and child assets
-   Distinguish between different types of relationships (e.g., "primary-source", "backup-source", "reference-copy")
-   Maintain unique identifiers for each relationship instance

**Restrictions:**

-   **Relationship Type**: Alias IDs can ONLY be used with `parentChild` relationship type
-   **Not for Related Links**: The `related` relationship type does NOT support aliases
-   **Uniqueness**: Each alias must be unique for a given child asset
-   **Optional**: Alias IDs are completely optional - existing functionality works without them

### Alias Usage Examples

**Create with Alias:**

```bash
# Create a parent-child link with an alias
vamscli asset-links create \
  --from-asset-id parent-asset \
  --from-database-id my-database \
  --to-asset-id child-asset \
  --to-database-id my-database \
  --relationship-type parentChild \
  --alias-id "primary-relationship"

# Create multiple relationships between same assets with different aliases
vamscli asset-links create \
  --from-asset-id parent-asset \
  --from-database-id my-database \
  --to-asset-id child-asset \
  --to-database-id my-database \
  --relationship-type parentChild \
  --alias-id "secondary-relationship"
```

**Update Alias:**

```bash
# Update only the alias ID
vamscli asset-links update \
  --asset-link-id 12345678-1234-1234-1234-123456789012 \
  --alias-id "updated-alias"

# Update both alias and tags
vamscli asset-links update \
  --asset-link-id 12345678-1234-1234-1234-123456789012 \
  --alias-id "new-alias" \
  --tags tag1 --tags tag2
```

**View with Aliases:**

```bash
# Get single asset link (shows alias if present)
vamscli asset-links get --asset-link-id 12345678-1234-1234-1234-123456789012

# List shows aliases in output
vamscli asset-links list -d my-database --asset-id parent-asset
```

### Alias Use Cases

**1. Multiple Source Relationships:**

```bash
# Primary source relationship
vamscli asset-links create \
  --from-asset-id source-model \
  --to-asset-id derived-asset \
  --relationship-type parentChild \
  --alias-id "primary-source"

# Reference source relationship
vamscli asset-links create \
  --from-asset-id source-model \
  --to-asset-id derived-asset \
  --relationship-type parentChild \
  --alias-id "reference-source"
```

**2. Versioned Relationships:**

```bash
# Version 1 relationship
vamscli asset-links create \
  --from-asset-id template-v1 \
  --to-asset-id instance \
  --relationship-type parentChild \
  --alias-id "v1-instance"

# Version 2 relationship
vamscli asset-links create \
  --from-asset-id template-v2 \
  --to-asset-id instance \
  --relationship-type parentChild \
  --alias-id "v2-instance"
```

**3. Role-Based Relationships:**

```bash
# Production relationship
vamscli asset-links create \
  --from-asset-id master-config \
  --to-asset-id deployment \
  --relationship-type parentChild \
  --alias-id "production"

# Staging relationship
vamscli asset-links create \
  --from-asset-id master-config \
  --to-asset-id deployment \
  --relationship-type parentChild \
  --alias-id "staging"
```

### Alias Error Handling

**Invalid Relationship Type:**

```bash
# This will fail - alias not allowed for 'related' type
vamscli asset-links create \
  --from-asset-id asset1 \
  --to-asset-id asset2 \
  --relationship-type related \
  --alias-id "will-fail"

# Error: The --alias-id option can only be used with 'parentChild' relationship type
```

**Duplicate Alias:**

```bash
# Backend will reject duplicate aliases for same child asset
vamscli asset-links create \
  --from-asset-id parent \
  --to-asset-id child \
  --relationship-type parentChild \
  --alias-id "existing-alias"

# Error: Asset link already exists (if alias already used)
```

### Best Practices for Aliases

1. **Use Descriptive Aliases**: Choose meaningful names that describe the relationship purpose

    - ✅ Good: `"primary-source"`, `"backup-reference"`, `"v2-template"`
    - ❌ Bad: `"link1"`, `"test"`, `"abc"`

2. **Consistent Naming**: Use a consistent naming convention across your project

    - Example: `"{purpose}-{version}"` → `"source-v1"`, `"source-v2"`

3. **Document Aliases**: Keep track of what each alias represents in your project documentation

4. **Avoid Overuse**: Only use aliases when you genuinely need multiple relationships between the same assets

## Asset Management Workflow Examples

### Basic Asset Operations

```bash
# Create asset with tags
vamscli assets create -d my-db --name "My Model" --description "3D model asset" --tags model --tags urgent --distributable

# Upload files to asset
vamscli file upload -d my-db -a my-model /path/to/model.gltf

# Create initial version
vamscli asset-version create -d my-db -a my-model --comment "Initial version"

# Update asset metadata
vamscli assets update my-model -d my-db --description "Updated 3D model with textures"

# List all versions
vamscli asset-version list -d my-db -a my-model
```

### Asset Relationship Management

```bash
# Create related relationships (bidirectional)
vamscli asset-links create --from-asset-id model1 --from-database-id db1 --to-asset-id texture1 --to-database-id db1 --relationship-type related --tags "related-files"

# Create parent-child hierarchy
vamscli asset-links create --from-asset-id main-model --from-database-id db1 --to-asset-id lod1-model --to-database-id db1 --relationship-type parentChild --tags "lod-hierarchy"

# Create parent-child with alias for multiple relationships
vamscli asset-links create --from-asset-id main-model --from-database-id db1 --to-asset-id variant-model --to-database-id db1 --relationship-type parentChild --alias-id "high-quality-variant"

vamscli asset-links create --from-asset-id main-model --from-database-id db1 --to-asset-id variant-model --to-database-id db1 --relationship-type parentChild --alias-id "low-quality-variant"

# View all relationships for an asset (shows aliases)
vamscli asset-links list -d db1 --asset-id main-model --tree-view
```

### Version Management Workflow

```bash
# Create version after making changes
vamscli asset-version create -d my-db -a my-asset --comment "Added new textures and materials"

# Get details for specific version
vamscli asset-version get -d my-db -a my-asset -v 2

# Revert to previous stable version if needed
vamscli asset-version revert -d my-db -a my-asset -v 1 --comment "Reverting to last stable version"
```

## Asset Links Metadata Management Commands

VamsCLI provides comprehensive metadata management for asset links, allowing you to attach custom key-value data to relationships between assets.

### `vamscli asset-links-metadata list`

List all metadata for an asset link.

**Arguments:**

-   `asset_link_id`: Asset link ID to list metadata for (positional argument, required)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# List all metadata for an asset link
vamscli asset-links-metadata list abc123-def456-ghi789-012345

# List with JSON output for automation
vamscli asset-links-metadata list abc123-def456-ghi789-012345 --json-output
```

**Output Features:**

-   Shows all metadata key-value pairs
-   Displays metadata types (string, number, boolean, date, xyz)
-   CLI-friendly formatted output by default
-   Raw JSON output available for automation

### `vamscli asset-links-metadata create`

Create metadata for an asset link.

**Arguments:**

-   `asset_link_id`: Asset link ID to add metadata to (positional argument, required)

**Options:**

-   `-k, --key`: Metadata key (required unless using --json-input)
-   `-v, --value`: Metadata value (required unless using --json-input)
-   `-t, --type`: Metadata type - `string`, `number`, `boolean`, `date`, `xyz`, `wxyz`, `matrix4x4`, `geopoint`, `geojson`, `lla`, `json` (default: string)
-   `--json-input`: JSON file containing metadata fields
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Create string metadata (default type)
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "description" --value "Connection between models"

# Create numeric metadata
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "distance" --value "15.5" --type number

# Create boolean metadata
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "is_primary" --value "true" --type boolean

# Create date metadata
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "created_date" --value "2023-12-01T10:30:00Z" --type date

# Create JSON metadata
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "config" --value '{"enabled": true, "count": 5}' --type json

# Create XYZ coordinate metadata
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "offset" --value '{"x": 1.5, "y": 2.0, "z": 0.5}' --type xyz

# Create WXYZ quaternion metadata
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "rotation" --value '{"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0}' --type wxyz

# Create 4x4 transformation matrix metadata
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "transform" --value '[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]' --type matrix4x4

# Create GeoJSON Point metadata
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "location" --value '{"type": "Point", "coordinates": [-74.0060, 40.7128]}' --type geopoint

# Create GeoJSON Polygon metadata
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "boundary" --value '{"type": "Polygon", "coordinates": [[[-74.1, 40.7], [-74.0, 40.7], [-74.0, 40.8], [-74.1, 40.8], [-74.1, 40.7]]]}' --type geojson

# Create LLA coordinate metadata
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "position" --value '{"lat": 40.7128, "long": -74.0060, "alt": 10.5}' --type lla

# Create from JSON file
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --json-input metadata.json

# Create with JSON output
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "status" --value "active" --json-output
```

**JSON Input Format:**

```json
{
    "metadataKey": "description",
    "metadataValue": "Connection between 3D models",
    "metadataValueType": "string"
}
```

**Metadata Types:**

-   **`string`**: Plain text values (default)
-   **`number`**: Numeric values (integers or floats)
-   **`boolean`**: Boolean values (true/false)
-   **`date`**: ISO date format (e.g., 2023-12-01T10:30:00Z)
-   **`json`**: Any valid JSON object or array
-   **`xyz`**: 3D coordinates as JSON objects with x, y, z numeric values
-   **`wxyz`**: Quaternion as JSON objects with w, x, y, z numeric values
-   **`matrix4x4`**: 4x4 transformation matrix as JSON array
-   **`geopoint`**: GeoJSON Point objects for geographic coordinates
-   **`geojson`**: Any valid GeoJSON object (Point, Polygon, Feature, etc.)
-   **`lla`**: Latitude/Longitude/Altitude coordinates as JSON objects

**Coordinate and Matrix Formats:**

**XYZ Coordinate Format:**

```json
{
    "x": 1.5,
    "y": 2.0,
    "z": 0.5
}
```

**WXYZ Quaternion Format:**

```json
{
    "w": 1.0,
    "x": 0.0,
    "y": 0.0,
    "z": 0.0
}
```

**MATRIX4X4 Format:**

```json
[
    [1, 0, 0, 0],
    [0, 1, 0, 0],
    [0, 0, 1, 0],
    [0, 0, 0, 1]
]
```

**GEOPOINT Format:**

```json
{
    "type": "Point",
    "coordinates": [-74.006, 40.7128]
}
```

**LLA Coordinate Format:**

```json
{
    "lat": 40.7128,
    "long": -74.006,
    "alt": 10.5
}
```

### `vamscli asset-links-metadata update`

Update existing metadata for an asset link.

**Arguments:**

-   `asset_link_id`: Asset link ID containing the metadata (positional argument, required)
-   `metadata_key`: Metadata key to update (positional argument, required)

**Options:**

-   `-v, --value`: New metadata value (required unless using --json-input)
-   `-t, --type`: Metadata type - `string`, `number`, `boolean`, `date`, `xyz`, `wxyz`, `matrix4x4`, `geopoint`, `geojson`, `lla`, `json` (default: string)
-   `--json-input`: JSON file containing metadata fields
-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Update string metadata
vamscli asset-links-metadata update abc123-def456-ghi789-012345 description --value "Updated connection info"

# Update numeric metadata
vamscli asset-links-metadata update abc123-def456-ghi789-012345 distance --value "20.0" --type number

# Update boolean metadata
vamscli asset-links-metadata update abc123-def456-ghi789-012345 is_active --value "false" --type boolean

# Update date metadata
vamscli asset-links-metadata update abc123-def456-ghi789-012345 last_modified --value "2023-12-15T14:30:00Z" --type date

# Update JSON metadata
vamscli asset-links-metadata update abc123-def456-ghi789-012345 config --value '{"enabled": false, "priority": 1}' --type json

# Update XYZ coordinates
vamscli asset-links-metadata update abc123-def456-ghi789-012345 offset --value '{"x": 2.0, "y": 3.0, "z": 1.0}' --type xyz

# Update WXYZ quaternion
vamscli asset-links-metadata update abc123-def456-ghi789-012345 rotation --value '{"w": 0.707, "x": 0.707, "y": 0.0, "z": 0.0}' --type wxyz

# Update 4x4 transformation matrix
vamscli asset-links-metadata update abc123-def456-ghi789-012345 transform --value '[[2,0,0,5],[0,2,0,10],[0,0,2,15],[0,0,0,1]]' --type matrix4x4

# Update GeoJSON Point location
vamscli asset-links-metadata update abc123-def456-ghi789-012345 location --value '{"type": "Point", "coordinates": [-73.9857, 40.7484]}' --type geopoint

# Update GeoJSON area boundary
vamscli asset-links-metadata update abc123-def456-ghi789-012345 boundary --value '{"type": "Polygon", "coordinates": [[[-74.2, 40.6], [-74.0, 40.6], [-74.0, 40.8], [-74.2, 40.8], [-74.2, 40.6]]]}' --type geojson

# Update LLA coordinates
vamscli asset-links-metadata update abc123-def456-ghi789-012345 position --value '{"lat": 40.7484, "long": -73.9857, "alt": 15.2}' --type lla

# Update from JSON file
vamscli asset-links-metadata update abc123-def456-ghi789-012345 description --json-input updated_metadata.json

# Update with JSON output
vamscli asset-links-metadata update abc123-def456-ghi789-012345 status --value "inactive" --json-output
```

**JSON Input Format:**

```json
{
    "metadataValue": "Updated connection information",
    "metadataValueType": "string"
}
```

### `vamscli asset-links-metadata delete`

Delete metadata for an asset link.

**Arguments:**

-   `asset_link_id`: Asset link ID containing the metadata (positional argument, required)
-   `metadata_key`: Metadata key to delete (positional argument, required)

**Options:**

-   `--json-output`: Output raw JSON response

**Examples:**

```bash
# Delete metadata key
vamscli asset-links-metadata delete abc123-def456-ghi789-012345 description

# Delete with JSON output
vamscli asset-links-metadata delete abc123-def456-ghi789-012345 offset --json-output
```

**Safety Features:**

-   Permanent deletion (cannot be undone)
-   Clear confirmation of what was deleted
-   Error handling for non-existent keys

## Asset Links Metadata Workflow Examples

### Basic Metadata Operations

```bash
# Create asset link first
vamscli asset-links create --from-asset-id model1 --from-database-id db1 --to-asset-id texture1 --to-database-id db1 --relationship-type related

# Add descriptive metadata
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "description" --value "Model uses this texture"

# Add distance metadata
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "distance" --value "5.2" --type number

# Add coordinate offset
vamscli asset-links-metadata create abc123-def456-ghi789-012345 --key "offset" --value '{"x": 1.0, "y": 0.0, "z": 0.5}' --type xyz

# List all metadata
vamscli asset-links-metadata list abc123-def456-ghi789-012345

# Update metadata
vamscli asset-links-metadata update abc123-def456-ghi789-012345 distance --value "6.8" --type number

# Delete metadata when no longer needed
vamscli asset-links-metadata delete abc123-def456-ghi789-012345 offset
```

### Advanced Metadata Management

```bash
# Create complex metadata from JSON file
cat > link_metadata.json << EOF
{
  "metadataKey": "transformation_matrix",
  "metadataValue": "{\"matrix\": [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}",
  "metadataValueType": "string"
}
EOF

vamscli asset-links-metadata create abc123-def456-ghi789-012345 --json-input link_metadata.json

# Batch operations with JSON output for automation
vamscli asset-links-metadata list abc123-def456-ghi789-012345 --json-output | jq '.metadata[] | select(.metadataValueType == "number")'
```

### `vamscli assets download`

Download files from an asset.

**Arguments:**

-   `local_path`: Local directory path for downloads (optional when using --shareable-links-only)

**Required Options:**

-   `-d, --database`: Database ID containing the asset (required)
-   `-a, --asset`: Asset ID to download from (required)

**Download Options:**

-   `--file-key`: Specific asset file key to download
-   `--recursive`: Download all files from folder tree structure
-   `--flatten-download-tree`: Ignore asset file tree, download files flat
-   `--asset-preview`: Download only the asset preview file
-   `--file-previews`: Additionally download file preview files
-   `--asset-link-children-tree-depth INTEGER`: Traverse asset link children tree to specified depth
-   `--shareable-links-only`: Return presigned URLs without downloading

**Performance Options:**

-   `--parallel-downloads INTEGER`: Max parallel downloads (default: 5)
-   `--retry-attempts INTEGER`: Retry attempts per file (default: 3)
-   `--timeout INTEGER`: Download timeout per file in seconds (default: 300)
-   `--hide-progress`: Hide download progress display

**Standard Options:**

-   `--json-input`: JSON input with all parameters
-   `--json-output`: Output raw JSON response

**Examples:**

**Basic Downloads:**

```bash
# Download whole asset
vamscli assets download /local/path -d my-db -a my-asset

# Download specific file
vamscli assets download /local/path -d my-db -a my-asset --file-key "/model.gltf"

# Download folder recursively
vamscli assets download /local/path -d my-db -a my-asset --file-key "/models/" --recursive

# Download asset preview only
vamscli assets download /local/path -d my-db -a my-asset --asset-preview
```

**Advanced Downloads:**

```bash
# Download with file previews
vamscli assets download /local/path -d my-db -a my-asset --file-key "/model.gltf" --file-previews

# Download asset tree (2 levels deep)
vamscli assets download /local/path -d my-db -a my-asset --asset-link-children-tree-depth 2

# Flatten download (ignore folder structure)
vamscli assets download /local/path -d my-db -a my-asset --file-key "/models/" --flatten-download-tree

# High-performance download with custom settings
vamscli assets download /local/path -d my-db -a my-asset --parallel-downloads 10 --retry-attempts 5
```

**Shareable Links:**

```bash
# Get shareable links only (no download)
vamscli assets download -d my-db -a my-asset --shareable-links-only

# Get shareable link for specific file
vamscli assets download -d my-db -a my-asset --file-key "/model.gltf" --shareable-links-only

# Get shareable link for asset preview
vamscli assets download -d my-db -a my-asset --asset-preview --shareable-links-only
```

**JSON Input/Output:**

```bash
# Download with JSON input
vamscli assets download --json-input '{"database":"my-db","asset":"my-asset","local_path":"/downloads","file_key":"/model.gltf","shareable_links_only":true}'

# Download with JSON output for automation
vamscli assets download /local/path -d my-db -a my-asset --json-output
```

**JSON Input Format:**

```json
{
    "database": "my-database",
    "asset": "my-asset",
    "local_path": "/local/download/path",
    "file_key": "/specific/file.gltf",
    "recursive": true,
    "flatten_download_tree": false,
    "asset_preview": false,
    "file_previews": true,
    "asset_link_children_tree_depth": 2,
    "shareable_links_only": false,
    "parallel_downloads": 5,
    "retry_attempts": 3,
    "timeout": 300,
    "hide_progress": false
}
```

**Download Scenarios:**

1. **Individual File Download**

    - Downloads specific file by key to local path
    - Maintains relative path structure
    - Supports file preview downloads

2. **Folder Download**

    - Downloads all files under folder prefix
    - `--recursive` includes all subdirectories
    - `--flatten-download-tree` ignores folder structure

3. **Whole Asset Download**

    - Downloads all files from asset
    - Maintains asset folder structure
    - Skips archived files automatically

4. **Asset Preview Download**

    - Downloads only the asset preview file
    - Cannot be combined with other file options
    - Error if asset has no preview

5. **Asset Tree Download**

    - Traverses asset link children tree
    - Creates folder for each asset
    - Supports configurable depth levels

6. **Shareable Links Only**
    - Generates presigned URLs without downloading
    - No local path required
    - URLs expire in 24 hours by default

**File Path Handling:**

-   **Structured Downloads**: Maintains asset file tree structure
-   **Flattened Downloads**: Downloads files to single directory (detects conflicts)
-   **Asset Tree Downloads**: Creates asset-named folders for multi-asset downloads
-   **Preview Files**: Places preview files next to base files

**Progress Display:**

-   Real-time progress bars with file-level status
-   Download speed and ETA calculations
-   Active download count monitoring
-   Failed file reporting with error details

**Error Handling:**

-   Asset not found or not distributable
-   File not found or archived
-   Network connectivity issues
-   Filename conflicts in flattened downloads
-   Asset tree traversal failures

**Performance Features:**

-   Configurable parallel downloads (1-20 concurrent)
-   Automatic retry with exponential backoff
-   Timeout protection for stuck downloads
-   Progress callback system for monitoring

### Asset Lifecycle Management

```bash
# Archive asset when no longer actively used
vamscli assets archive my-asset -d my-database --reason "Project completed"

# Permanently delete asset (requires confirmation)
vamscli assets delete my-asset -d my-database --confirm --reason "No longer needed"
```
