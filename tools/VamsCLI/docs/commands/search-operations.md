# Search Operations - Dual-Index OpenSearch

This guide covers VamsCLI search commands for finding assets and files using the dual-index OpenSearch system.

## Prerequisites

-   VamsCLI must be configured with `vamscli setup <api-gateway-url>`
-   User must be authenticated with `vamscli auth login`
-   OpenSearch must be enabled (NOOPENSEARCH feature switch must not be present)

## Feature Dependency

Search functionality requires OpenSearch to be enabled in your VAMS deployment. If the `NOOPENSEARCH` feature switch is enabled, search commands will be disabled and you should use alternative commands:

-   Use `vamscli assets list` instead of `vamscli search assets`
-   Use `vamscli database list-assets` for database-specific asset listing

## Dual-Index Architecture

VAMS uses a dual-index OpenSearch system with separate indexes for assets and files:

-   **Asset Index**: Optimized for asset metadata, descriptions, and properties
-   **File Index**: Optimized for file keys, extensions, and file-specific metadata

This architecture provides:

-   Better query performance
-   More precise search results
-   Optimized field mappings per entity type

## Available Commands

### `vamscli search assets`

Search across all assets with flexible filtering, metadata search, and sorting options.

#### Basic Usage

```bash
# Simple text search
vamscli search assets -q "training model"

# Search within specific database
vamscli search assets -q "model" -d my-database

# Search with asset type filter
vamscli search assets --asset-type "3d-model"

# Search with tag filters
vamscli search assets --tags "training,simulation"
```

#### Metadata Search (NEW)

The dual-index system provides powerful metadata search capabilities:

```bash
# Search for specific metadata field:value
vamscli search assets --metadata-query "MD_str_product:Training"

# Wildcard metadata search
vamscli search assets --metadata-query "MD_str_product:Train*"

# Multiple metadata conditions with AND
vamscli search assets --metadata-query "MD_str_product:Training AND MD_num_version:1"

# Multiple metadata conditions with OR
vamscli search assets --metadata-query "MD_str_category:A OR MD_str_category:B"

# Search only metadata field names
vamscli search assets --metadata-query "product" --metadata-mode key

# Search only metadata field values
vamscli search assets --metadata-query "Training" --metadata-mode value

# Search both field names and values (default)
vamscli search assets --metadata-query "Training" --metadata-mode both

# Combine general search with metadata search
vamscli search assets -q "model" --metadata-query "MD_str_category:Training"

# Exclude metadata from general search
vamscli search assets -q "model" --no-metadata
```

#### Advanced Features

```bash
# Get match explanations
vamscli search assets -q "model" --explain-results

# Include archived assets
vamscli search assets -q "model" --include-archived

# Sorting results
vamscli search assets -q "model" --sort-field "str_assetname" --sort-desc

# Pagination
vamscli search assets -q "model" --from 20 --size 50
```

#### Output Formats

```bash
# Default table format
vamscli search assets -q "model"

# JSON format for structured data
vamscli search assets -q "model" --output-format json

# CSV format for data analysis
vamscli search assets -q "model" --output-format csv > results.csv

# Legacy raw JSON output (full API response)
vamscli search assets -q "model" --jsonOutput
```

#### Parameters

| Parameter                          | Description                                | Example                                      |
| ---------------------------------- | ------------------------------------------ | -------------------------------------------- |
| `-d, --database`                   | Database ID to search within               | `-d my-database`                             |
| `-q, --query`                      | General text search query                  | `-q "training model"`                        |
| `--metadata-query`                 | Metadata search query (field:value format) | `--metadata-query "MD_str_product:Training"` |
| `--metadata-mode`                  | Metadata search mode (key/value/both)      | `--metadata-mode value`                      |
| `--include-metadata/--no-metadata` | Include metadata in general search         | `--no-metadata`                              |
| `--explain-results`                | Include match explanations                 | `--explain-results`                          |
| `--sort-field`                     | Field to sort by                           | `--sort-field str_assetname`                 |
| `--sort-desc/--sort-asc`           | Sort direction                             | `--sort-desc`                                |
| `--from`                           | Pagination start offset                    | `--from 20`                                  |
| `--size`                           | Results per page (max 2000)                | `--size 50`                                  |
| `--asset-type`                     | Filter by asset type                       | `--asset-type "3d-model"`                    |
| `--tags`                           | Filter by tags (comma-separated)           | `--tags "training,simulation"`               |
| `--include-archived`               | Include archived assets                    | `--include-archived`                         |
| `--output-format`                  | Output format (table/json/csv)             | `--output-format csv`                        |
| `--jsonOutput`                     | Raw API response as JSON                   | `--jsonOutput`                               |

### `vamscli search files`

Search across all asset files with file-specific filtering and metadata search options.

#### Basic Usage

```bash
# Search files by extension
vamscli search files --file-ext "gltf"

# Search files with text query
vamscli search files -q "texture"

# Search files within specific database
vamscli search files -q "model" -d my-database
```

#### Metadata Search for Files

```bash
# Search file metadata
vamscli search files --metadata-query "MD_str_format:GLTF2.0"

# Combine file extension with metadata
vamscli search files --file-ext "gltf" --metadata-query "MD_num_polycount:>10000"
```

#### Advanced Usage

```bash
# Combine multiple filters
vamscli search files --file-ext "png" --tags "ui,interface"

# Sort by file properties
vamscli search files -q "texture" --sort-field "str_key" --sort-asc

# Include archived files
vamscli search files --file-ext "gltf" --include-archived
```

#### Parameters

| Parameter                          | Description                           | Example                                 |
| ---------------------------------- | ------------------------------------- | --------------------------------------- |
| `-d, --database`                   | Database ID to search within          | `-d my-database`                        |
| `-q, --query`                      | General text search query             | `-q "texture"`                          |
| `--metadata-query`                 | Metadata search query                 | `--metadata-query "MD_str_format:GLTF"` |
| `--metadata-mode`                  | Metadata search mode (key/value/both) | `--metadata-mode value`                 |
| `--include-metadata/--no-metadata` | Include metadata in general search    | `--no-metadata`                         |
| `--explain-results`                | Include match explanations            | `--explain-results`                     |
| `--sort-field`                     | Field to sort by                      | `--sort-field str_key`                  |
| `--sort-desc/--sort-asc`           | Sort direction                        | `--sort-asc`                            |
| `--from`                           | Pagination start offset               | `--from 0`                              |
| `--size`                           | Results per page (max 2000)           | `--size 100`                            |
| `--file-ext`                       | Filter by file extension              | `--file-ext "gltf"`                     |
| `--tags`                           | Filter by tags (comma-separated)      | `--tags "texture,ui"`                   |
| `--include-archived`               | Include archived files                | `--include-archived`                    |
| `--output-format`                  | Output format (table/json/csv)        | `--output-format json`                  |
| `--jsonOutput`                     | Raw API response as JSON              | `--jsonOutput`                          |

### `vamscli search simple` (NEW)

Simplified search interface with user-friendly parameters. Easier to use than complex search commands.

#### Basic Usage

```bash
# General keyword search
vamscli search simple -q "training"

# Search by asset name
vamscli search simple --asset-name "model"

# Search by file extension
vamscli search simple --file-ext "gltf"

# Search within database
vamscli search simple -d my-database --tags "training,simulation"
```

#### Entity Type Filtering

```bash
# Search only assets
vamscli search simple -q "training" --entity-types asset

# Search only files
vamscli search simple --file-ext "gltf" --entity-types file

# Search both assets and files (default)
vamscli search simple -q "model" --entity-types asset,file
```

#### Asset-Specific Search

```bash
# Search by asset name
vamscli search simple --asset-name "training-model"

# Search by asset ID
vamscli search simple --asset-id "asset-123"

# Search by asset type
vamscli search simple --asset-type "3d-model"

# Combine asset filters
vamscli search simple --asset-name "model" --asset-type "3d-model" --entity-types asset
```

#### File-Specific Search

```bash
# Search by file key
vamscli search simple --file-key "model.gltf"

# Search by file extension
vamscli search simple --file-ext "png"

# Combine file filters
vamscli search simple --file-key "texture" --file-ext "png" --entity-types file
```

#### Metadata Search

```bash
# Search metadata field names
vamscli search simple --metadata-key "product"

# Search metadata field values
vamscli search simple --metadata-value "Training"

# Search both metadata keys and values
vamscli search simple --metadata-key "product" --metadata-value "Training"
```

#### Parameters

| Parameter            | Description                             | Example                        |
| -------------------- | --------------------------------------- | ------------------------------ |
| `-q, --query`        | General keyword search                  | `-q "training"`                |
| `--asset-name`       | Search by asset name                    | `--asset-name "model"`         |
| `--asset-id`         | Search by asset ID                      | `--asset-id "asset-123"`       |
| `--asset-type`       | Filter by asset type                    | `--asset-type "3d-model"`      |
| `--file-key`         | Search by file key                      | `--file-key "model.gltf"`      |
| `--file-ext`         | Filter by file extension                | `--file-ext "gltf"`            |
| `-d, --database`     | Filter by database ID                   | `-d my-database`               |
| `--tags`             | Filter by tags (comma-separated)        | `--tags "training,simulation"` |
| `--metadata-key`     | Search metadata field names             | `--metadata-key "product"`     |
| `--metadata-value`   | Search metadata field values            | `--metadata-value "Training"`  |
| `--entity-types`     | Filter by entity type (asset/file/both) | `--entity-types asset,file`    |
| `--include-archived` | Include archived items                  | `--include-archived`           |
| `--from`             | Pagination offset                       | `--from 0`                     |
| `--size`             | Results per page (max 1000)             | `--size 100`                   |
| `--output-format`    | Output format (table/json/csv)          | `--output-format json`         |

### `vamscli search mapping`

Retrieve the OpenSearch index mapping showing all available search fields for both indexes.

#### Usage

```bash
# View available search fields for both indexes
vamscli search mapping

# Export field mapping as CSV
vamscli search mapping --output-format csv > fields.csv

# Get raw mapping data
vamscli search mapping --jsonOutput
```

The mapping command now returns information for both the asset index and file index, showing which fields are available for each entity type.

#### Parameters

| Parameter         | Description                    | Example               |
| ----------------- | ------------------------------ | --------------------- |
| `--output-format` | Output format (table/json/csv) | `--output-format csv` |
| `--jsonOutput`    | Raw mapping as JSON            | `--jsonOutput`        |

## Search Field Types

The dual-index system contains various field types with specific prefixes:

### Common to Both Indexes

-   **str\_\*** - String fields (text search, exact match)
-   **num\_\*** - Numeric fields (range queries, sorting)
-   **date\_\*** - Date fields (date range queries)
-   **bool\_\*** - Boolean fields (true/false filtering)
-   **list\_\*** - List fields (array values like tags)
-   **MD\_\*** - Metadata fields (custom metadata)

### Asset Index Fields

-   `str_assetname` - Asset name
-   `str_description` - Asset description
-   `str_databaseid` - Database identifier
-   `str_assettype` - Asset type
-   `str_assetid` - Asset ID
-   `list_tags` - Asset tags
-   `bool_isdistributable` - Distribution flag
-   `bool_archived` - Archive status
-   `MD_*` - Custom metadata fields

### File Index Fields

-   `str_key` - S3 file key (full path)
-   `str_fileext` - File extension
-   `str_assetname` - Parent asset name
-   `str_databaseid` - Database identifier
-   `str_assetid` - Parent asset ID
-   `num_filesize` - File size in bytes
-   `date_lastmodified` - Last modification date
-   `list_tags` - File tags
-   `bool_archived` - Archive status
-   `MD_*` - Custom metadata fields

## Metadata Search Syntax

### Field:Value Format

```bash
# Exact match
--metadata-query "MD_str_product:Training"

# Wildcard match
--metadata-query "MD_str_product:Train*"

# Multiple conditions with AND (all must match)
--metadata-query "MD_str_product:Training AND MD_num_version:1"

# Multiple conditions with OR (any can match)
--metadata-query "MD_str_category:A OR MD_str_category:B"
```

### Search Modes

```bash
# Search only field names (find assets with specific metadata fields)
--metadata-query "product" --metadata-mode key

# Search only field values (find specific values across all metadata)
--metadata-query "Training" --metadata-mode value

# Search both field names and values (default)
--metadata-query "Training" --metadata-mode both
```

### Metadata Field Naming

Metadata fields in OpenSearch use the `MD_` prefix followed by the type and name:

-   `MD_str_<name>` - String metadata
-   `MD_num_<name>` - Numeric metadata
-   `MD_date_<name>` - Date metadata
-   `MD_bool_<name>` - Boolean metadata

Example: If you have metadata `{"product": "Training"}`, it's stored as `MD_str_product` in OpenSearch.

## Output Formats

### Table Format (Default)

```
Search Results (2 found):

Asset: training-model-001
  Database: vr-models-training
  Type: 3d-model
  Description: Training model for VR simulation
  Tags: training, simulation, vr
  Score: 0.95

Asset: training-model-002
  Database: vr-models-training
  Type: 3d-model
  Description: Advanced training model
  Tags: training, advanced
  Score: 0.87
```

### Table Format with Explanations

When using `--explain-results`:

```
Asset: training-model-001
  Database: vr-models-training
  Type: 3d-model
  Description: Training model for VR simulation
  Tags: training, simulation, vr
  Score: 0.95
  Match Type: combined
  Matched Fields: str_assetname, MD_str_product
```

### JSON Format

```json
[
    {
        "indexType": "asset",
        "assetName": "training-model-001",
        "database": "vr-models-training",
        "assetType": "3d-model",
        "description": "Training model for VR simulation",
        "tags": ["training", "simulation", "vr"],
        "score": 0.95
    }
]
```

### JSON Format with Explanations

```json
[
    {
        "indexType": "asset",
        "assetName": "training-model-001",
        "database": "vr-models-training",
        "score": 0.95,
        "explanation": {
            "query_type": "combined",
            "index_type": "asset",
            "matched_fields": ["str_assetname", "MD_str_product"],
            "match_reasons": {
                "str_assetname": "Matched core asset field 'str_assetname'",
                "MD_str_product": "Matched metadata field 'MD_str_product'"
            }
        }
    }
]
```

### CSV Format

```csv
Asset Name,Database,Type,Description,Tags,Score
training-model-001,vr-models-training,3d-model,"Training model for VR","training, simulation, vr",0.95
training-model-002,vr-models-training,3d-model,"Advanced training model","training, advanced",0.87
```

## Performance Tips

1. **Use Specific Filters**: Narrow results with database, asset type, or tag filters
2. **Use Simple Search**: For common queries, `vamscli search simple` is easier and just as powerful
3. **Limit Result Size**: Use `--size` parameter to control page size
4. **Use Entity Types**: Specify `--entity-types` to search only the relevant index
5. **Metadata Search**: Use `--metadata-mode` to search only keys or values when appropriate

## Integration with Other Commands

Search results can be used with other VamsCLI commands:

```bash
# Export search results and process with other tools
vamscli search assets -q "model" --output-format csv | Select-String "training"

# Use JSON output for scripting
vamscli search assets -q "model" --output-format json | ConvertFrom-Json | Where-Object { $_.score -gt 0.8 }
```

## Profile Support

Search commands support all VamsCLI profile features:

```bash
# Use specific profile
vamscli search assets -q "model" --profile production

# Search with token override
vamscli search assets -q "model" --token-override <token> --user-id user@example.com
```

## Examples by Use Case

### Finding Assets by Type

```bash
# Find all 3D models
vamscli search assets --asset-type "3d-model"

# Find textures with specific tags
vamscli search assets --asset-type "texture" --tags "ui,interface"

# Find assets with specific metadata
vamscli search assets --metadata-query "MD_str_category:Training"
```

### Finding Files by Extension

```bash
# Find all GLTF files
vamscli search files --file-ext "gltf"

# Find PNG files in specific database
vamscli search files --file-ext "png" -d my-database

# Find files with specific metadata
vamscli search files --metadata-query "MD_str_format:GLTF2.0"
```

### Using Simple Search

```bash
# Quick asset search
vamscli search simple --asset-name "training" --entity-types asset

# Quick file search
vamscli search simple --file-ext "gltf" --entity-types file

# Search with metadata
vamscli search simple --metadata-key "product" --metadata-value "Training"

# Search across both assets and files
vamscli search simple -q "model" -d my-database
```

### Data Export and Analysis

```bash
# Export all assets to CSV
vamscli search simple --entity-types asset --output-format csv > all_assets.csv

# Export specific database assets
vamscli search assets -d production --output-format csv > production_assets.csv

# Get structured JSON for processing
vamscli search assets -q "model" --output-format json > results.json
```

### Complex Queries

```bash
# Multi-criteria asset search
vamscli search assets \
  -q "training" \
  -d "vr-models" \
  --asset-type "3d-model" \
  --tags "simulation,training" \
  --metadata-query "MD_str_category:A" \
  --sort-field "str_assetname" \
  --sort-asc \
  --explain-results

# Combined general and metadata search
vamscli search assets \
  -q "model" \
  --metadata-query "MD_str_product:Training AND MD_num_version:2" \
  --metadata-mode both \
  --output-format json
```

## Troubleshooting

For common search issues and solutions, see:

-   [Search Issues Troubleshooting](../troubleshooting/search-issues.md)

## Next Steps

-   [Asset Management Commands](asset-management.md) - Managing assets after finding them
-   [File Operations Commands](file-operations.md) - Working with files found in search
-   [Metadata Management](metadata-management.md) - Working with asset/file metadata
-   [Global Options](global-options.md) - Profile and authentication options
