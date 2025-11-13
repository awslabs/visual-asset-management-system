# PLM Commands

Product Lifecycle Management (PLM) commands for importing and managing PLM data in VAMS.

## Table of Contents

-   [Overview](#overview)
-   [Command Structure](#command-structure)
-   [PLM XML Import](#plm-xml-import)
    -   [Basic Usage](#basic-usage)
    -   [XML File Upload](#xml-file-upload)
    -   [Advanced Options](#advanced-options)
-   [Import Process](#import-process)
-   [Output and Statistics](#output-and-statistics)
-   [Examples](#examples)
-   [Troubleshooting](#troubleshooting)

## Overview

The PLM commands enable importing Product Lifecycle Management data from PLM XML files into VAMS. The import process creates assets, metadata, file uploads, and asset links with parallel processing for improved performance.

### Key Features

-   **Parallel Processing**: Configurable worker pool for concurrent operations
-   **Component Hierarchy**: Preserves parent-child relationships from PLM XML
-   **Metadata Extraction**: Automatically extracts and stores component metadata
-   **Geometry Files**: Uploads associated geometry files to assets
-   **XML Source Preservation**: Optional upload of source XML files to root assets
-   **Transform Matrices**: Stores transformation data in asset link metadata
-   **Progress Tracking**: Real-time progress bars for each phase

## Command Structure

```
vamscli industry engineering plm plmxml import [OPTIONS]
```

### Required Options

-   `-d, --database-id TEXT`: Target Database ID where assets will be created

-   `--plmxml-dir PATH`: Path to directory containing PLM XML files to import

### Optional Options

-   `--max-workers INTEGER`: Maximum number of parallel workers (default: 15)

    -   Higher values increase parallelism but consume more resources
    -   Recommended range: 10-30 depending on system capabilities

-   `--upload-xml`: Upload source PLMXML files to their corresponding root assets (default: False)

    -   Only root (top-level) components receive the XML file
    -   Useful for audit trails and source preservation
    -   Original XML filename is preserved

-   `--json-output`: Output raw JSON response instead of formatted CLI output

## PLM XML Import

### Basic Usage

Import PLM XML files without uploading source XML:

```bash
vamscli industry engineering plm plmxml import \
  -d my-database \
  --plmxml-dir /path/to/plmxml
```

This will:

1. Parse all XML files in the directory
2. Create VAMS assets for each component
3. Upload geometry files referenced in the XML
4. Create asset links for parent-child relationships
5. Store metadata and transform matrices

### XML File Upload

Upload source XML files to root assets for audit trails:

```bash
vamscli industry engineering plm plmxml import \
  -d my-database \
  --plmxml-dir /path/to/plmxml \
  --upload-xml
```

**XML Upload Behavior:**

-   Only **root (top-level) components** from each XML file receive the XML upload
-   Child components are skipped to avoid duplicate uploads
-   Original XML filename is preserved (e.g., `assembly.xml`, `part123.xml`)
-   Statistics show uploaded, failed, and skipped XML files

**Example Scenario:**

-   XML file contains 1 root assembly with 5 child parts
-   **Without `--upload-xml`**: No XML files uploaded
-   **With `--upload-xml`**: XML file uploaded to root assembly only (1 upload, 5 skipped)

### Advanced Options

#### Custom Worker Count

Adjust parallelism based on system resources:

```bash
vamscli industry engineering plm plmxml import \
  -d my-database \
  --plmxml-dir /path/to/plmxml \
  --max-workers 20
```

**Worker Count Guidelines:**

-   **Low (5-10)**: Conservative, suitable for limited resources
-   **Medium (15-20)**: Balanced performance (default: 15)
-   **High (25-30)**: Maximum throughput, requires adequate resources

#### JSON Output

Get machine-readable output for automation:

```bash
vamscli industry engineering plm plmxml import \
  -d my-database \
  --plmxml-dir /path/to/plmxml \
  --upload-xml \
  --json-output
```

## Import Process

The import process consists of four phases:

### Phase 0: XML Parsing

-   Parses all XML files in the directory
-   Extracts component definitions and relationships
-   Identifies root components per XML file
-   Creates XML-to-component mapping

**Output:**

```
📋 Phase 0: Parsing 3 XML files...
✓ Parsed 150 components and 149 relationships in 2.34s
```

### Phase 1: Asset Creation

-   Creates VAMS assets in parallel
-   Uses component item_revision as asset name
-   Checks for existing assets to avoid duplicates
-   Sanitizes asset IDs to comply with VAMS requirements

**Output:**

```
🏗️  Phase 1: Creating assets (max 15 workers)...
✓ Assets: 120 created, 30 existing, 0 failed in 45.67s
```

### Phase 2: Parallel Operations

Runs three operations concurrently:

1. **Metadata Creation**: Stores component metadata on assets
2. **File Uploads**: Uploads geometry files and optionally XML files
3. **Asset Link Creation**: Creates parent-child relationships

**Output:**

```
⚡ Phase 2: Processing metadata/files/links (max 15 workers)...
✓ Metadata: 150 created, Files: 120 uploaded, XML: 3 uploaded, Links: 149 created in 89.23s
```

### Phase 3: Link Metadata

-   Creates metadata on asset links
-   Stores transform matrices (4x4 transformation matrices)
-   Stores additional UserData fields from PLM XML

**Output:**

```
🔗 Phase 3: Creating link metadata (max 15 workers)...
✓ Link metadata: 298 created in 12.45s
```

## Output and Statistics

### CLI Output

The command provides a comprehensive summary:

```
✅ Import Complete - Final Summary:
  Total Duration: 149.69s

  📄 XML Source:
     Files Processed: 3
     Components Found: 150
     Relationships Found: 149

  🏗️  VAMS Entities Created:
     Assets: 120 created, 30 existing, 0 failed
     Asset Metadata: 150 created
     Geometry Files Uploaded: 120
     XML Files Uploaded: 3
     XML Files Skipped (non-root): 147
     Asset Links: 149 created, 0 failed
     Asset Link Metadata: 298 created

  🎯 Top-Level Parent Assets (3):
     • Assembly_A/Rev1 (ID: asset-uuid-1)
     • Assembly_B/Rev2 (ID: asset-uuid-2)
     • Assembly_C/Rev1 (ID: asset-uuid-3)
```

### JSON Output

Machine-readable format for automation:

```json
{
    "success": true,
    "metadata": {
        "version": "2.0",
        "description": "PLM Structure with component definitions and hierarchical relationships",
        "created": "2024-01-15",
        "format": "Parallel processing with configurable worker pool"
    },
    "statistics": {
        "total_files_processed": 3,
        "total_assets_processed": 150,
        "total_components": 150,
        "total_relationships": 149,
        "assets_created": 120,
        "assets_existing": 30,
        "assets_failed": 0,
        "metadata_created": 150,
        "geometry_files_uploaded": 120,
        "xml_files_uploaded": 3,
        "xml_files_failed": 0,
        "xml_files_skipped": 147,
        "xml_upload_enabled": true,
        "asset_links_created": 149,
        "asset_link_metadata_created": 298,
        "asset_link_failures": 0,
        "top_level_parents": [
            {
                "asset_id": "asset-uuid-1",
                "asset_name": "Assembly_A/Rev1",
                "item_revision": "Assembly_A/Rev1"
            }
        ]
    },
    "timing": {
        "total_duration_seconds": 149.69,
        "xml_parsing_seconds": 2.34,
        "asset_creation_seconds": 45.67,
        "parallel_operations_seconds": 89.23,
        "link_metadata_seconds": 12.45
    }
}
```

## Examples

### Example 1: Basic Import

Import PLM XML files with default settings:

```bash
vamscli industry engineering plm plmxml import \
  -d engineering-db \
  --plmxml-dir /data/plm/export
```

### Example 2: Import with XML Upload

Import and preserve source XML files:

```bash
vamscli industry engineering plm plmxml import \
  -d engineering-db \
  --plmxml-dir /data/plm/export \
  --upload-xml
```

### Example 3: High-Performance Import

Use more workers for faster processing:

```bash
vamscli industry engineering plm plmxml import \
  -d engineering-db \
  --plmxml-dir /data/plm/export \
  --upload-xml \
  --max-workers 25
```

### Example 4: Automated Import

Use JSON output for CI/CD pipelines:

```bash
vamscli industry engineering plm plmxml import \
  -d engineering-db \
  --plmxml-dir /data/plm/export \
  --upload-xml \
  --json-output > import-results.json
```

### Example 5: Import with Profile

Use a specific profile for different environments:

```bash
vamscli --profile production industry engineering plm plmxml import \
  -d prod-engineering-db \
  --plmxml-dir /data/plm/export \
  --upload-xml
```

## Troubleshooting

### No XML Files Found

**Error:**

```
✗ No XML files found in: /path/to/directory
```

**Solution:**

-   Verify the directory path is correct
-   Ensure XML files have `.xml` extension
-   Check file permissions

### Directory Not Found

**Error:**

```
✗ PLM XML directory not found: /path/to/directory
```

**Solution:**

-   Verify the directory exists
-   Check for typos in the path
-   Use absolute paths to avoid confusion

### Asset Creation Failures

**Symptom:**

```
✓ Assets: 100 created, 20 existing, 30 failed in 45.67s
```

**Possible Causes:**

1. **Invalid Asset Names**: Asset IDs contain forbidden characters

    - Solution: The command automatically sanitizes asset IDs
    - Check logs for specific failures

2. **Database Permissions**: Insufficient permissions to create assets

    - Solution: Verify user has write access to the database

3. **API Connectivity**: Network issues or API unavailability
    - Solution: Check API gateway URL and network connectivity

### XML Upload Failures

**Symptom:**

```
XML Files Failed: 2
```

**Possible Causes:**

1. **File Not Found**: XML file was moved or deleted during import

    - Solution: Ensure XML files remain in place during import

2. **File Size Limits**: XML file exceeds upload size limits

    - Solution: Check VAMS file size limits and adjust if needed

3. **Permissions**: Insufficient permissions to upload files
    - Solution: Verify user has file upload permissions

### Performance Issues

**Symptom:** Import takes longer than expected

**Solutions:**

1. **Adjust Worker Count**: Increase `--max-workers` for more parallelism

    ```bash
    --max-workers 25
    ```

2. **Check System Resources**: Monitor CPU and memory usage

    - Reduce workers if system is overloaded

3. **Network Latency**: Slow API responses
    - Consider running closer to the API gateway
    - Check network bandwidth

### Memory Issues

**Symptom:** Out of memory errors during large imports

**Solutions:**

1. **Reduce Worker Count**: Lower parallelism to reduce memory usage

    ```bash
    --max-workers 10
    ```

2. **Split Import**: Process XML files in smaller batches

    - Move files to separate directories
    - Run multiple imports sequentially

3. **Increase System Memory**: Allocate more RAM if possible

## Related Commands

-   `vamscli assets create`: Create individual assets
-   `vamscli file upload`: Upload files to assets
-   `vamscli asset-links create`: Create asset relationships
-   `vamscli search simple`: Search for imported assets

## See Also

-   [Asset Management Commands](asset-management.md)
-   [File Operations Commands](file-operations.md)
-   [Database Commands](database-admin.md)
-   [Troubleshooting Guide](../troubleshooting/asset-file-issues.md)
