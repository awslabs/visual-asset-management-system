# Search Operations

This guide covers VamsCLI search commands for finding assets and files using OpenSearch functionality.

## Prerequisites

-   VamsCLI must be configured with `vamscli setup <api-gateway-url>`
-   User must be authenticated with `vamscli auth login`
-   OpenSearch must be enabled (NOOPENSEARCH feature switch must not be present)

## Feature Dependency

Search functionality requires OpenSearch to be enabled in your VAMS deployment. If the `NOOPENSEARCH` feature switch is enabled, search commands will be disabled and you should use alternative commands:

-   Use `vamscli assets list` instead of `vamscli search assets`
-   Use `vamscli database list-assets` for database-specific asset listing

## Available Commands

### `vamscli search assets`

Search across all assets with flexible filtering and sorting options.

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

#### Advanced Usage

```bash
# Property-based filtering
vamscli search assets --property-filters '[{"propertyKey":"str_description","operator":"=","value":"training"}]'

# Sorting results
vamscli search assets -q "model" --sort-field "str_assetname" --sort-desc

# Pagination
vamscli search assets -q "model" --from 20 --size 50

# Large result sets with automatic pagination
vamscli search assets -q "model" --max-results 5000
```

#### Output Formats

```bash
# Default table format
vamscli search assets -q "model"

# JSON format for structured data
vamscli search assets -q "model" --output-format json

# CSV format for data analysis
vamscli search assets -q "model" --output-format csv > results.csv

# Legacy raw JSON output
vamscli search assets -q "model" --jsonOutput
```

#### JSON Input

```bash
# Use JSON file for complex search parameters
vamscli search assets --jsonInput search_params.json
```

Example `search_params.json`:

```json
{
    "query": "training model",
    "database": "my-database",
    "operation": "AND",
    "tokens": [
        {
            "propertyKey": "str_assettype",
            "operator": "=",
            "value": "3d-model"
        }
    ],
    "sort_field": "str_assetname",
    "sort_desc": false,
    "from": 0,
    "size": 100,
    "output_format": "table"
}
```

#### Parameters

| Parameter                       | Description                      | Example                        |
| ------------------------------- | -------------------------------- | ------------------------------ |
| `-d, --database`                | Database ID to search within     | `-d my-database`               |
| `-q, --query`                   | General text search query        | `-q "training model"`          |
| `--operation`                   | Token operation (AND/OR)         | `--operation OR`               |
| `--sort-field`                  | Field to sort by                 | `--sort-field str_assetname`   |
| `--sort-desc/--sort-asc`        | Sort direction                   | `--sort-desc`                  |
| `--from`                        | Pagination start offset          | `--from 20`                    |
| `--size`                        | Results per page (max 2000)      | `--size 50`                    |
| `--max-results`                 | Maximum total results            | `--max-results 5000`           |
| `--asset-type`                  | Filter by asset type             | `--asset-type "3d-model"`      |
| `--tags`                        | Filter by tags (comma-separated) | `--tags "training,simulation"` |
| `--property-filters`            | JSON property filter tokens      | See examples above             |
| `--output-format`               | Output format (table/json/csv)   | `--output-format csv`          |
| `--jsonInput`                   | JSON file with search parameters | `--jsonInput params.json`      |
| `--jsonOutput`                  | Raw API response as JSON         | `--jsonOutput`                 |
| `--show-progress/--no-progress` | Show pagination progress         | `--no-progress`                |

### `vamscli search files`

Search across all asset files with file-specific filtering options.

#### Basic Usage

```bash
# Search files by extension
vamscli search files --file-ext "gltf"

# Search files with text query
vamscli search files -q "texture"

# Search files within specific database
vamscli search files -q "model" -d my-database
```

#### Advanced Usage

```bash
# Combine multiple filters
vamscli search files --file-ext "png" --asset-type "texture" --tags "ui,interface"

# Property-based file search
vamscli search files --property-filters '[{"propertyKey":"str_filename","operator":"=","value":"model.gltf"}]'

# Sort by file properties
vamscli search files -q "texture" --sort-field "str_filename" --sort-asc
```

#### Parameters

| Parameter                | Description                      | Example                     |
| ------------------------ | -------------------------------- | --------------------------- |
| `-d, --database`         | Database ID to search within     | `-d my-database`            |
| `-q, --query`            | General text search query        | `-q "texture"`              |
| `--operation`            | Token operation (AND/OR)         | `--operation AND`           |
| `--sort-field`           | Field to sort by                 | `--sort-field str_filename` |
| `--sort-desc/--sort-asc` | Sort direction                   | `--sort-asc`                |
| `--from`                 | Pagination start offset          | `--from 0`                  |
| `--size`                 | Results per page (max 2000)      | `--size 100`                |
| `--max-results`          | Maximum total results            | `--max-results 1000`        |
| `--file-ext`             | Filter by file extension         | `--file-ext "gltf"`         |
| `--asset-type`           | Filter by parent asset type      | `--asset-type "3d-model"`   |
| `--tags`                 | Filter by tags (comma-separated) | `--tags "texture,ui"`       |
| `--property-filters`     | JSON property filter tokens      | See asset examples          |
| `--output-format`        | Output format (table/json/csv)   | `--output-format json`      |
| `--jsonInput`            | JSON file with search parameters | `--jsonInput params.json`   |
| `--jsonOutput`           | Raw API response as JSON         | `--jsonOutput`              |

### `vamscli search mapping`

Retrieve the OpenSearch index mapping showing all available search fields.

#### Usage

```bash
# View available search fields
vamscli search mapping

# Export field mapping as CSV
vamscli search mapping --output-format csv > fields.csv

# Get raw mapping data
vamscli search mapping --jsonOutput
```

#### Parameters

| Parameter         | Description                    | Example               |
| ----------------- | ------------------------------ | --------------------- |
| `--output-format` | Output format (table/json/csv) | `--output-format csv` |
| `--jsonOutput`    | Raw mapping as JSON            | `--jsonOutput`        |

## Search Field Types

The search index contains various field types with specific prefixes:

-   **str\_\*** - String fields (text search, exact match)
-   **num\_\*** - Numeric fields (range queries, sorting)
-   **date\_\*** - Date fields (date range queries)
-   **bool\_\*** - Boolean fields (true/false filtering)
-   **list\_\*** - List fields (array values like tags)
-   **geo\_\*** - Geographic fields (location-based search)

Common search fields:

-   `str_assetname` - Asset name
-   `str_description` - Asset description
-   `str_databaseid` - Database identifier
-   `str_assettype` - Asset type
-   `str_filename` - File name (files only)
-   `str_fileext` - File extension (files only)
-   `list_tags` - Asset tags
-   `num_size` - File size in bytes (files only)

## Property Filter Syntax

Property filters use JSON syntax for complex queries:

```json
[
    {
        "propertyKey": "str_description",
        "operator": "=",
        "value": "training"
    },
    {
        "propertyKey": "str_assettype",
        "operator": "!=",
        "value": "deprecated"
    }
]
```

### Supported Operators

-   `=` - Equals (exact match)
-   `!=` - Not equals
-   `:` - Contains (text search)
-   `!:` - Does not contain

### Operation Types

-   `AND` - All filters must match (default)
-   `OR` - Any filter can match

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

### JSON Format

```json
[
    {
        "assetName": "training-model-001",
        "database": "vr-models-training",
        "type": "3d-model",
        "description": "Training model for VR simulation",
        "tags": ["training", "simulation", "vr"],
        "score": 0.95
    }
]
```

### CSV Format

```csv
Asset Name,Database,Type,Description,Tags,Score
training-model-001,vr-models-training,3d-model,"Training model for VR","training, simulation, vr",0.95
training-model-002,vr-models-training,3d-model,"Advanced training model","training, advanced",0.87
```

## Pagination

### Automatic Pagination

For large result sets (>2000 items), VamsCLI automatically handles pagination:

```bash
# Fetch up to 5000 results with progress indicators
vamscli search assets -q "model" --max-results 5000
# Output: Fetching page 1... Fetching page 2... Fetching page 3... Complete.
```

### Manual Pagination

```bash
# Get specific page of results
vamscli search assets -q "model" --from 100 --size 50

# Control page size (1-2000)
vamscli search assets -q "model" --size 200
```

## Performance Tips

1. **Use Specific Filters**: Narrow results with database, asset type, or tag filters
2. **Limit Result Size**: Use `--max-results` for large queries
3. **CSV for Large Exports**: Use CSV format for exporting large datasets
4. **Property Filters**: Use property filters for precise matching instead of text search

## Integration with Other Commands

Search results can be used with other VamsCLI commands:

```bash
# Export search results and process with other tools
vamscli search assets -q "model" --output-format csv | grep "training"

# Use JSON output for scripting
vamscli search assets -q "model" --output-format json | jq '.[] | .assetName'
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
```

### Finding Files by Extension

```bash
# Find all GLTF files
vamscli search files --file-ext "gltf"

# Find large image files
vamscli search files --file-ext "png" --property-filters '[{"propertyKey":"num_size","operator":">","value":"1000000"}]'
```

### Data Export and Analysis

```bash
# Export all assets to CSV
vamscli search assets --output-format csv > all_assets.csv

# Export specific database assets
vamscli search assets -d production --output-format csv > production_assets.csv

# Get structured JSON for processing
vamscli search assets -q "model" --output-format json | jq '.[] | select(.score > 0.8)'
```

### Complex Queries

```bash
# Multi-criteria search
vamscli search assets \
  -q "training" \
  -d "vr-models" \
  --asset-type "3d-model" \
  --tags "simulation,training" \
  --operation "AND" \
  --sort-field "str_assetname" \
  --sort-asc \
  --output-format json
```

## Next Steps

-   [Asset Management Commands](asset-management.md) - Managing assets after finding them
-   [File Operations Commands](file-operations.md) - Working with files found in search
-   [Global Options](global-options.md) - Profile and authentication options
-   [Troubleshooting Search Issues](../troubleshooting/search-issues.md) - Common problems and solutions
