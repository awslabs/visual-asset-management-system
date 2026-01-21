# Asset Export Command

## Overview

The `vamscli assets export` command provides a powerful way to export comprehensive asset data including metadata, files, relationships, and optional presigned download URLs. This command is designed for mass exports and downstream data consumption, making it more performant than calling multiple separate CLI/API endpoints.

## Command Syntax

```bash
vamscli assets export [OPTIONS]
```

## Required Options

| Option          | Short | Description                                 |
| --------------- | ----- | ------------------------------------------- |
| `--database-id` | `-d`  | Database ID containing the asset            |
| `--asset-id`    | `-a`  | Asset ID (root of the asset tree to export) |

## Pagination Options

The command supports two pagination modes:

### Auto-Pagination (Default, Recommended)

Automatically fetches all pages and combines results into a single response. **This is enabled by default.**

| Option                                   | Description                                           |
| ---------------------------------------- | ----------------------------------------------------- |
| `--auto-paginate` / `--no-auto-paginate` | Enable/disable automatic pagination. Default: Enabled |
| `--max-assets INTEGER`                   | Maximum assets per page (1-1000). Default: 100        |

### Manual Pagination

Fetch one page at a time using pagination tokens for incremental processing. Use `--no-auto-paginate` to enable manual pagination.

| Option                  | Description                                     |
| ----------------------- | ----------------------------------------------- |
| `--no-auto-paginate`    | Disable automatic pagination for manual control |
| `--max-assets INTEGER`  | Maximum assets per page (1-1000). Default: 100  |
| `--starting-token TEXT` | Pagination token from previous response         |

**Note:** Auto-pagination and `--starting-token` are mutually exclusive.

## Relationship Fetching Options

Control how asset relationships and child trees are fetched:

| Option                           | Description                                                              |
| -------------------------------- | ------------------------------------------------------------------------ |
| `--no-fetch-relationships`       | Skip fetching asset relationships (single asset mode). Default: False    |
| `--fetch-entire-subtrees`        | Fetch entire children relationship sub-trees (full tree). Default: False |
| `--include-parent-relationships` | Include parent relationships in the relationship data. Default: False    |

**Behavior:**

-   **Default**: Fetches relationships for root asset + 1 level of children (excludes parent relationships)
-   **`--no-fetch-relationships`**: Exports only the single root asset, no relationships
-   **`--fetch-entire-subtrees`**: Fetches complete descendant tree (all levels)
-   **`--include-parent-relationships`**: Includes relationships where the root asset is the child (parent relationships)

**Note:** By default, parent relationships to the root asset are filtered out to focus on the descendant tree. Use `--include-parent-relationships` to include them.

## Filter Options

| Option                              | Description                                                                   |
| ----------------------------------- | ----------------------------------------------------------------------------- |
| `--generate-presigned-urls`         | Generate presigned download URLs for files                                    |
| `--include-folder-files`            | Include folder files in export                                                |
| `--include-only-primary-type-files` | Include only files with primaryType set                                       |
| `--include-archived-files`          | Include archived files in export. Default: False                              |
| `--file-extensions TEXT`            | Filter files by extension (e.g., `.gltf` `.bin`). Can be used multiple times. |

## Metadata Options

By default, all metadata is included. Use these flags to exclude specific metadata:

| Option                     | Description                             |
| -------------------------- | --------------------------------------- |
| `--no-file-metadata`       | Exclude file metadata from export       |
| `--no-asset-link-metadata` | Exclude asset link metadata from export |
| `--no-asset-metadata`      | Exclude asset metadata from export      |

## Input/Output Options

| Option              | Description                                             |
| ------------------- | ------------------------------------------------------- |
| `--json-input TEXT` | JSON input file path or JSON string with all parameters |
| `--json-output`     | Output raw JSON response (pure JSON, no CLI formatting) |

## Usage Examples

### Basic Export (Auto-Pagination - Default)

Export all assets in the tree automatically (default behavior):

```bash
vamscli assets export -d my-database -a my-asset
```

### Manual Pagination (Single Page)

Export the first page only (disable auto-pagination):

```bash
vamscli assets export -d my-database -a my-asset --no-auto-paginate --max-assets 100
```

### Export with Filters

Export with file type filtering and presigned URLs:

```bash
vamscli assets export -d my-database -a my-asset --auto-paginate \
  --file-extensions .gltf --file-extensions .bin \
  --generate-presigned-urls
```

### Export Single Asset Without Relationships

Export only the root asset without fetching relationships:

```bash
vamscli assets export -d my-database -a my-asset --no-fetch-relationships
```

### Export with Full Tree (All Descendants)

Export the complete descendant tree (all levels):

```bash
vamscli assets export -d my-database -a my-asset \
  --fetch-entire-subtrees --auto-paginate
```

### Export with Parent Relationships

Include parent relationships in the relationship data (by default, parent relationships are filtered out):

```bash
vamscli assets export -d my-database -a my-asset \
  --include-parent-relationships --auto-paginate
```

### Export Only Primary Type Files

Export only files that have a primaryType set:

```bash
vamscli assets export -d my-database -a my-asset --auto-paginate \
  --include-only-primary-type-files
```

### Export Including Archived Files

Include archived files in the export:

```bash
vamscli assets export -d my-database -a my-asset \
  --include-archived-files --auto-paginate
```

### Export Without Metadata

Export asset and file data without metadata:

```bash
vamscli assets export -d my-database -a my-asset --auto-paginate \
  --no-file-metadata --no-asset-metadata --no-asset-link-metadata
```

### Manual Pagination

Fetch data page by page (requires disabling auto-pagination):

```bash
# First page
vamscli assets export -d my-database -a my-asset --no-auto-paginate --max-assets 100

# Subsequent pages using returned token
vamscli assets export -d my-database -a my-asset --no-auto-paginate \
  --starting-token "eyJsYXN0QXNzZXRJbmRleCI6OTksImFzc2V0VHJlZSI6W..."
```

### JSON Output for Downstream Processing

Export with pure JSON output (no CLI formatting):

```bash
vamscli assets export -d my-database -a my-asset --auto-paginate \
  --json-output > export-data.json
```

### Export and Download Files

Export asset data and automatically download all files:

```bash
vamscli assets export -d my-database -a my-asset --auto-paginate \
  --download-files --local-path /downloads
```

### Export and Download with Filters

Export and download only specific file types:

```bash
vamscli assets export -d my-database -a my-asset --auto-paginate \
  --download-files --local-path /downloads \
  --file-extensions .gltf --file-extensions .bin
```

### Export and Download with Organization

Organize downloaded files by asset ID:

```bash
vamscli assets export -d my-database -a my-asset --auto-paginate \
  --download-files --local-path /downloads --organize-by-asset
```

### Export and Download Flattened

Download all files to a single directory (ignore folder structure):

```bash
vamscli assets export -d my-database -a my-asset --auto-paginate \
  --download-files --local-path /downloads --flatten-downloads
```

### Complex Parameters via JSON Input

Use JSON input for complex parameter combinations:

```bash
# Create export-params.json
cat > export-params.json << EOF
{
  "databaseId": "my-database",
  "assetId": "my-asset",
  "autoPaginate": true,
  "generatePresignedUrls": true,
  "fileExtensions": [".gltf", ".bin", ".jpg"],
  "includeOnlyPrimaryTypeFiles": true,
  "fetchEntireSubtrees": true,
  "includeArchivedFiles": false,
  "maxAssets": 200
}
EOF

# Execute export
vamscli assets export -d placeholder -a placeholder \
  --json-input export-params.json --json-output
```

## Output Format

### CLI Mode Output

#### Auto-Pagination:

```
Fetching page 1...
Fetching page 2 (retrieved 500 assets so far)...
Fetching page 3 (retrieved 1000 assets so far)...
✓ Export completed successfully!

Total assets in tree: 1,234
Assets retrieved: 1,234
Pages retrieved: 3
Relationships: 1,233

All data has been retrieved and combined.

Sample assets (showing 3 of 1234):
  1. Root Asset (ID: root-asset)
     Database: my-database
     Files: 15
     Metadata fields: 5
  2. Child Asset 1 (ID: child-1)
     Database: my-database
     Files: 8
     Metadata fields: 3
  3. Child Asset 2 (ID: child-2)
     Database: my-database
     Files: 12
```

#### Manual Pagination (First Page):

```
Fetching asset export data...
✓ Export completed successfully!

Assets in this page: 500
Total assets in tree: 1,234
Relationships: 499

More data available. Use the NextToken below for the next page:
eyJsYXN0QXNzZXRJbmRleCI6NDk5LCJhc3NldFRyZWUiOlt...

Or use --auto-paginate to fetch all pages automatically.
```

### JSON Mode Output

#### Auto-Pagination:

```json
{
    "assets": [
        {
            "is_root_lookup_asset": true,
            "id": "root-asset",
            "databaseid": "my-database",
            "assetid": "root-asset",
            "bucketid": "bucket-123",
            "assetname": "Root Asset",
            "bucketname": "my-bucket",
            "bucketprefix": "assets/",
            "assettype": "model",
            "description": "Root asset description",
            "isdistributable": true,
            "tags": ["tag1", "tag2"],
            "asset_version_id": "1",
            "asset_version_createdate": "2024-01-15T10:30:00Z",
            "asset_version_comment": "Initial version",
            "archived": false,
            "metadata": {
                "customField": {
                    "valueType": "string",
                    "value": "customValue"
                }
            },
            "files": [
                {
                    "fileName": "model.gltf",
                    "key": "assets/root-asset/model.gltf",
                    "relativePath": "/model.gltf",
                    "isFolder": false,
                    "size": 1024000,
                    "dateCreatedCurrentVersion": "2024-01-15T10:30:00Z",
                    "versionId": "v1",
                    "storageClass": "STANDARD",
                    "isArchived": false,
                    "currentAssetVersionFileVersionMismatch": false,
                    "primaryType": "model",
                    "previewFile": "/model.previewFile.jpg",
                    "metadata": {
                        "fileCustomField": {
                            "valueType": "string",
                            "value": "fileValue"
                        }
                    },
                    "presignedFileDownloadUrl": "https://s3.amazonaws.com/bucket/file?signature=...",
                    "presignedFileDownloadExpiresIn": 86400
                }
            ]
        }
    ],
    "relationships": [
        {
            "parentAssetId": "root-asset",
            "parentAssetDatabaseId": "my-database",
            "childAssetId": "child-asset",
            "childAssetDatabaseId": "my-database",
            "assetLinkType": "parentChild",
            "assetLinkId": "link-123",
            "assetLinkAliasId": null,
            "metadata": {
                "linkCustomField": {
                    "valueType": "string",
                    "value": "linkValue"
                }
            }
        }
    ],
    "totalAssetsInTree": 1234,
    "assetsRetrieved": 1234,
    "pagesRetrieved": 3,
    "autoPaginated": true
}
```

#### Manual Pagination:

```json
{
  "assets": [...],
  "relationships": [...],
  "NextToken": "eyJsYXN0QXNzZXRJbmRleCI6NDk5...",
  "totalAssetsInTree": 1234,
  "assetsInThisPage": 500
}
```

## Response Fields

### Root Level Fields

| Field               | Type    | Description                                                          |
| ------------------- | ------- | -------------------------------------------------------------------- |
| `assets`            | Array   | Array of asset objects (may include unauthorized asset placeholders) |
| `relationships`     | Array   | Array of asset link relationships (first page only)                  |
| `NextToken`         | String  | Pagination token for next page (manual pagination)                   |
| `totalAssetsInTree` | Integer | Total number of assets in the tree                                   |
| `assetsInThisPage`  | Integer | Number of assets in current page (manual pagination)                 |
| `assetsRetrieved`   | Integer | Total assets retrieved (auto-pagination only)                        |
| `pagesRetrieved`    | Integer | Number of pages fetched (auto-pagination only)                       |
| `autoPaginated`     | Boolean | Whether auto-pagination was used (auto-pagination only)              |

### Permission Handling

When exporting asset trees, some assets may be inaccessible due to permissions. The export handles this gracefully:

-   **Unauthorized assets** are included as placeholder objects with minimal information
-   **Downloads** automatically skip unauthorized assets without errors
-   **CLI output** shows count of unauthorized assets skipped
-   **Export continues** successfully even with partial permissions

### Asset Object Fields

#### Authorized Assets

| Field                      | Type    | Description                                 |
| -------------------------- | ------- | ------------------------------------------- |
| `is_root_lookup_asset`     | Boolean | Whether this is the root asset of the query |
| `id`                       | String  | Asset ID                                    |
| `databaseid`               | String  | Database ID                                 |
| `assetid`                  | String  | Asset ID (duplicate for compatibility)      |
| `bucketid`                 | String  | S3 bucket ID                                |
| `assetname`                | String  | Asset name                                  |
| `bucketname`               | String  | S3 bucket name                              |
| `bucketprefix`             | String  | S3 bucket prefix                            |
| `assettype`                | String  | Asset type                                  |
| `description`              | String  | Asset description                           |
| `isdistributable`          | Boolean | Whether asset is distributable              |
| `tags`                     | Array   | Asset tags                                  |
| `asset_version_id`         | String  | Current version ID                          |
| `asset_version_createdate` | String  | Version creation date                       |
| `asset_version_comment`    | String  | Version comment                             |
| `archived`                 | Boolean | Whether asset is archived                   |
| `metadata`                 | Object  | Asset metadata (if included)                |
| `files`                    | Array   | Array of file objects                       |

#### Unauthorized Assets (Permission Denied)

When a user lacks permissions to access an asset in the tree, a placeholder object is returned:

| Field               | Type    | Description                           |
| ------------------- | ------- | ------------------------------------- |
| `assetId`           | String  | Asset ID                              |
| `databaseId`        | String  | Database ID                           |
| `unauthorizedAsset` | Boolean | Always `true` for unauthorized assets |

**Note:** Unauthorized assets have no file data, metadata, or detailed information. They are included to maintain tree structure integrity but are automatically skipped during downloads.

### File Object Fields

| Field                                    | Type    | Description                              |
| ---------------------------------------- | ------- | ---------------------------------------- |
| `fileName`                               | String  | File name                                |
| `key`                                    | String  | Full S3 key                              |
| `relativePath`                           | String  | Relative path within asset               |
| `isFolder`                               | Boolean | Whether this is a folder                 |
| `size`                                   | Integer | File size in bytes (null for folders)    |
| `dateCreatedCurrentVersion`              | String  | File version creation date               |
| `versionId`                              | String  | S3 version ID                            |
| `storageClass`                           | String  | S3 storage class                         |
| `isArchived`                             | Boolean | Whether file is archived                 |
| `currentAssetVersionFileVersionMismatch` | Boolean | Version mismatch indicator               |
| `primaryType`                            | String  | Primary type metadata (if set)           |
| `previewFile`                            | String  | Relative path to preview file            |
| `metadata`                               | Object  | File metadata (if included)              |
| `presignedFileDownloadUrl`               | String  | Presigned download URL (if requested)    |
| `presignedFileDownloadExpiresIn`         | Integer | URL expiration in seconds (if requested) |

### Relationship Object Fields

| Field                   | Type   | Description                     |
| ----------------------- | ------ | ------------------------------- |
| `parentAssetId`         | String | Parent asset ID                 |
| `parentAssetDatabaseId` | String | Parent database ID              |
| `childAssetId`          | String | Child asset ID                  |
| `childAssetDatabaseId`  | String | Child database ID               |
| `assetLinkType`         | String | Link type (e.g., "parentChild") |
| `assetLinkId`           | String | Asset link ID                   |
| `assetLinkAliasId`      | String | Asset link alias ID (if set)    |
| `metadata`              | Object | Link metadata (if included)     |

## Use Cases

### 1. Complete Asset Tree Export

Export an entire asset tree with all related data for backup or migration:

```bash
vamscli assets export -d production-db -a root-asset \
  --auto-paginate --json-output > complete-export.json
```

### 2. Complete Tree Export with All Descendants

Export the entire asset tree including all descendant levels:

```bash
vamscli assets export -d my-db -a my-asset --auto-paginate \
  --fetch-entire-subtrees --json-output > complete-tree.json
```

### 3. Export with Parent Relationships

Export asset tree including parent relationships (useful for understanding the full context of an asset):

```bash
vamscli assets export -d my-db -a my-asset --auto-paginate \
  --include-parent-relationships --json-output > export-with-parents.json
```

### 4. Filtered Export for Specific File Types

Export only specific file types (e.g., 3D models):

```bash
vamscli assets export -d my-db -a my-asset --auto-paginate \
  --file-extensions .gltf --file-extensions .bin --file-extensions .jpg \
  --json-output > models-export.json
```

### 5. Export with Download URLs

Export asset data with presigned URLs for immediate file downloads:

```bash
vamscli assets export -d my-db -a my-asset --auto-paginate \
  --generate-presigned-urls --json-output > export-with-urls.json
```

### 6. Lightweight Export (No Metadata)

Export asset structure without metadata for faster processing:

```bash
vamscli assets export -d my-db -a my-asset --auto-paginate \
  --no-file-metadata --no-asset-metadata --no-asset-link-metadata \
  --json-output > lightweight-export.json
```

### 7. Export with Partial Permissions

Export a tree where some assets may be unauthorized (gracefully handles permission denials):

```bash
# Export continues successfully, unauthorized assets are skipped
vamscli assets export -d my-db -a my-asset --auto-paginate

# Output will show:
# Unauthorized assets (skipped): 3
```

### 8. Single Asset Export (No Relationships)

Export only a single asset without traversing relationships:

```bash
vamscli assets export -d my-db -a my-asset \
  --no-fetch-relationships --json-output > single-asset.json
```

### 9. Incremental Processing with Manual Pagination

Process large exports incrementally:

```bash
# Process first batch
vamscli assets export -d my-db -a my-asset --max-assets 100 \
  --json-output > page-1.json

# Extract N from page-1.json and process next batch
STARTING_TOKEN=$(jq -r '.NextToken' page-1.json)
vamscli assets export -d my-db -a my-asset \
  --starting-token "$STARTING_TOKEN" --json-output > page-2.json
```

## Performance Considerations

### Auto-Pagination

-   **Pros:**
    -   Simple to use - one command gets all data
    -   Automatic retry and error handling
    -   Progress indication in CLI mode
-   **Cons:**

    -   Higher memory usage for large trees
    -   Longer execution time for very large datasets
    -   All-or-nothing approach

-   **Best for:**
    -   Complete exports
    -   Datasets under 10,000 assets
    -   Backup and migration scenarios

### Manual Pagination

-   **Pros:**
    -   Lower memory footprint
    -   Can process data incrementally
    -   Better for streaming workflows
-   **Cons:**

    -   Requires token management
    -   More complex scripting
    -   Manual error handling

-   **Best for:**
    -   Very large datasets (>10,000 assets)
    -   Streaming/incremental processing
    -   Memory-constrained environments

### Optimization Tips

1. **Use Filters**: Reduce response size by filtering unnecessary data

    ```bash
    --file-extensions .gltf --include-only-primary-type-files
    ```

2. **Exclude Metadata**: Skip metadata if not needed

    ```bash
    --no-file-metadata --no-asset-metadata
    ```

3. **Adjust Page Size**: Balance between API calls and response size

    ```bash
    --max-assets 50   # Smaller pages for faster responses
    --max-assets 1000 # Larger pages for fewer API calls (default: 100)
    ```

4. **Control Tree Depth**: Limit relationship traversal if not needed

    ```bash
    --no-fetch-relationships  # Single asset only
    # Default: root + 1 level
    --fetch-entire-subtrees   # Complete tree (all levels)
    ```

5. **Use JSON Output**: Avoid CLI formatting overhead
    ```bash
    --json-output
    ```

## Error Handling

### Common Errors

#### Asset Not Found

```
✗ Asset Not Found: Asset 'my-asset' not found in database 'my-database'
Use 'vamscli assets get -d my-database my-asset' to check if the asset exists.
```

**Solution:** Verify the asset ID and database ID are correct.

#### Database Not Found

```
✗ Database Not Found: Database 'my-database' not found
Use 'vamscli database list' to see available databases.
```

**Solution:** Check available databases with `vamscli database list`.

#### Invalid Export Parameters

```
✗ Invalid Export Parameters: Invalid export parameters: maxAssets must be between 1 and 1000
Check your export parameters and try again. Use --help for parameter details.
```

**Solution:** Verify all parameters are within valid ranges.

#### Mutually Exclusive Options

```
Error: Options --auto-paginate and --starting-token cannot be used together.
Use --auto-paginate for automatic pagination or --starting-token for manual pagination.
```

**Solution:** Choose either auto-pagination or manual pagination, not both.

### JSON Mode Errors

In JSON mode, errors are returned as JSON objects:

```json
{
    "error": "Asset 'my-asset' not found in database 'my-database'",
    "error_type": "AssetNotFoundError"
}
```

## Integration Examples

### Python Script Integration

```python
import subprocess
import json

# Execute export with auto-pagination
result = subprocess.run([
    'vamscli', 'assets-export', 'export',
    '-d', 'my-database',
    '-a', 'root-asset',
    '--auto-paginate',
    '--generate-presigned-urls',
    '--json-output'
], capture_output=True, text=True)

if result.returncode == 0:
    export_data = json.loads(result.stdout)

    # Process assets
    for asset in export_data['assets']:
        print(f"Processing asset: {asset['assetname']}")

        # Download files using presigned URLs
        for file in asset['files']:
            if file.get('presignedFileDownloadUrl'):
                download_file(file['presignedFileDownloadUrl'], file['fileName'])
else:
    error_data = json.loads(result.stdout)
    print(f"Export failed: {error_data['error']}")
```

### Bash Script Integration

```bash
#!/bin/bash

# Export with auto-pagination
vamscli assets export \
  -d "$DATABASE_ID" \
  -a "$ASSET_ID" \
  --auto-paginate \
  --file-extensions .gltf --file-extensions .bin \
  --json-output > export.json

# Check if export succeeded
if [ $? -eq 0 ]; then
    echo "Export successful"

    # Extract asset count
    ASSET_COUNT=$(jq '.assetsRetrieved' export.json)
    echo "Retrieved $ASSET_COUNT assets"

    # Process each asset
    jq -c '.assets[]' export.json | while read asset; do
        ASSET_ID=$(echo "$asset" | jq -r '.assetid')
        echo "Processing asset: $ASSET_ID"
    done
else
    echo "Export failed"
    jq '.error' export.json
    exit 1
fi
```

## Best Practices

1. **Use Auto-Pagination for Complete Exports**

    - Simplifies scripting
    - Handles pagination automatically
    - Provides combined results

2. **Use JSON Output for Automation**

    - Pure JSON output (no CLI noise)
    - Easy to parse programmatically
    - Consistent error format

3. **Filter Aggressively**

    - Reduce response size
    - Faster processing
    - Lower memory usage

4. **Generate Presigned URLs When Needed**

    - Enables immediate file downloads
    - URLs expire after configured timeout
    - Useful for batch download scripts

5. **Handle Pagination Tokens Securely**
    - Tokens contain asset tree structure
    - Don't share tokens between users
    - Tokens have timestamps for validation

## Comparison with Other Commands

### vs. `vamscli assets get`

| Feature        | `assets get` | `assets-export export` |
| -------------- | ------------ | ---------------------- |
| Single asset   | ✓            | ✓                      |
| Asset tree     | ✗            | ✓                      |
| File metadata  | Limited      | Complete               |
| Relationships  | ✗            | ✓                      |
| Pagination     | ✗            | ✓                      |
| Presigned URLs | ✗            | ✓                      |
| Performance    | Fast         | Optimized for bulk     |

### vs. Multiple API Calls

**Traditional Approach** (Multiple Commands):

```bash
# Get asset
vamscli assets get -d db -a asset1 > asset1.json

# Get files
vamscli file list -d db -a asset1 > files1.json

# Get metadata
vamscli metadata get -d db -a asset1 > metadata1.json

# Get relationships
vamscli asset-links get -d db -a asset1 > links1.json

# Repeat for each child asset...
```

**New Approach** (Single Command):

```bash
# Get everything in one call
vamscli assets export -d db -a asset1 --auto-paginate \
  --json-output > complete-export.json
```

**Benefits:**

-   90% fewer API calls
-   Consistent data snapshot
-   Automatic relationship traversal
-   Built-in pagination
-   Better performance

## Permission Handling

### Unauthorized Assets in Tree

When exporting asset trees, you may encounter assets you don't have permission to access. The CLI handles this gracefully:

#### Behavior:

-   **Export continues**: The command doesn't fail when encountering unauthorized assets
-   **Placeholder objects**: Unauthorized assets are returned with minimal information:
    ```json
    {
        "assetId": "unauthorized-asset-id",
        "databaseId": "my-database",
        "unauthorizedAsset": true
    }
    ```
-   **Downloads skip**: File downloads automatically skip unauthorized assets
-   **CLI feedback**: Shows count of unauthorized assets in output

#### Example Output:

```
Export Summary:
  Total assets in tree: 100
  Assets retrieved: 100
  Pages retrieved: 1
  Relationships: 99
  Unauthorized assets (skipped): 5

  All data has been retrieved and combined.
```

#### Download Handling:

```
Download Summary:
  Total files: 50
  Successfully downloaded: 50
  Failed: 0
  Skipped (unauthorized): 5 asset(s)
  Total size: 125.5 MB
  Duration: 45.2s
  Average speed: 2.8 MB/s
```

### Best Practices for Partial Permissions:

1. **Use `--json-output`** to programmatically filter unauthorized assets:

    ```bash
    vamscli assets export -d my-db -a my-asset --auto-paginate --json-output | \
      jq '.assets | map(select(.unauthorizedAsset != true))'
    ```

2. **Check unauthorized count** before processing:

    ```python
    import json
    export_data = json.loads(result.stdout)
    unauthorized = [a for a in export_data['assets'] if a.get('unauthorizedAsset')]
    authorized = [a for a in export_data['assets'] if not a.get('unauthorizedAsset')]
    print(f"Processing {len(authorized)} authorized assets, skipping {len(unauthorized)}")
    ```

3. **Downloads are safe** - unauthorized assets are automatically skipped without errors

## Troubleshooting

### Large Exports Timing Out

If auto-pagination times out on very large trees:

```bash
# Use manual pagination with smaller pages
vamscli assets export -d my-db -a my-asset --max-assets 50
```

### Memory Issues

If running out of memory with auto-pagination:

```bash
# Use manual pagination and process incrementally
vamscli assets export -d my-db -a my-asset --max-assets 100 \
  --json-output | process-batch.sh
```

### Missing Presigned URLs

If presigned URLs are not generated:

```bash
# Ensure flag is set
vamscli assets export -d my-db -a my-asset \
  --generate-presigned-urls --json-output
```

### Unexpected File Count

If file count is lower than expected:

```bash
# Check if filters are excluding files
vamscli assets export -d my-db -a my-asset \
  --include-folder-files  # Include folders
  --json-output
```

## Related Commands

-   `vamscli assets get` - Get single asset details
-   `vamscli assets list` - List assets in database
-   `vamscli assets download` - Download asset files
-   `vamscli asset-links get` - Get asset relationships
-   `vamscli file list` - List files in asset
-   `vamscli metadata get` - Get asset metadata

## See Also

-   [Asset Management Commands](./asset-management.md)
-   [File Operations Commands](./file-operations.md)
-   [Global Options](./global-options.md)
-   [Troubleshooting Guide](../troubleshooting/asset-file-issues.md)
