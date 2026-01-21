# BOM Command Troubleshooting

This document provides troubleshooting guidance for BOM (Bill of Materials) command issues in VamsCLI.

## Common Issues and Solutions

### BOM JSON Structure Issues

#### Invalid BOM JSON Structure

**Error:**

```
BOM Assembly Error: Invalid BOM JSON: missing 'scene.nodes' structure
```

**Cause:** The BOM JSON file is missing required fields or has incorrect structure.

**Solution:**

1. Verify your BOM JSON has the required structure:

    ```json
    {
      "sources": [...],
      "scene": {
        "nodes": [...]
      }
    }
    ```

2. Check that all required fields are present:

    - `sources` array with source definitions
    - `scene.nodes` array with node hierarchy

3. Validate JSON syntax using a JSON validator

#### Invalid JSON File

**Error:**

```
BOM Assembly Error: Invalid JSON file: Expecting ',' delimiter: line 5 column 10 (char 45)
```

**Cause:** The JSON file contains syntax errors.

**Solution:**

1. Use a JSON validator to check syntax
2. Common issues:
    - Missing commas between array elements
    - Trailing commas after last elements
    - Unmatched brackets or braces
    - Unescaped quotes in strings

#### Missing Sources Field

**Error:**

```
BOM Assembly Error: Invalid BOM JSON: missing 'sources' field
```

**Cause:** The BOM JSON is missing the `sources` array.

**Solution:**
Add the `sources` array with all referenced components:

```json
{
  "sources": [
    { "source": "component1", "storage": "VAMS" },
    { "source": "component2", "storage": "VAMS" },
    { "source": "assembly", "storage": "no" }
  ],
  "scene": { ... }
}
```

### Asset and Database Issues

#### Asset Not Found

**Warning:**

```
Warning: Asset not found: component_name
```

**Cause:** The asset name in the BOM doesn't match any asset in the database.

**Solution:**

1. Verify asset names exactly match VAMS asset names:

    ```bash
    vamscli search simple -d database_id -q "component_name"
    ```

2. Check for common naming issues:

    - Case sensitivity
    - Extra spaces or special characters
    - Different naming conventions

3. List all assets to find correct names:
    ```bash
    vamscli assets list -d database_id
    ```

#### Database Not Found

**Error:**

```
Database Error: Database not found: database_id
```

**Cause:** The specified database ID doesn't exist or you don't have access.

**Solution:**

1. List available databases:

    ```bash
    vamscli database list
    ```

2. Verify database ID spelling and case
3. Check database permissions with your administrator

#### Search API Errors

**Error:**

```
API Error: Search request failed: 400 Bad Request
```

**Cause:** Search request format is invalid or API is unavailable.

**Solution:**

1. Check API connectivity:

    ```bash
    vamscli auth status
    ```

2. Verify database access:

    ```bash
    vamscli database get -d database_id
    ```

3. Try manual search to test API:
    ```bash
    vamscli search simple -d database_id -q "*"
    ```

### GLB Processing Issues

#### GLB Combine Failures

**Warning:**

```
Warning: glbassetcombine failed for component_name: Failed to combine GLB files
```

**Cause:** The GLB combination process failed for a component.

**Solution:**

1. Test the asset's GLB files individually:

    ```bash
    vamscli industry spatial glbassetcombine -d database_id -a asset_id
    ```

2. Check if the asset has GLB files:

    ```bash
    vamscli assets get -d database_id -a asset_id
    ```

3. Verify GLB file integrity by downloading manually:
    ```bash
    vamscli file download -d database_id -a asset_id -f filename.glb
    ```

#### No GLB Files Found

**Warning:**

```
Warning: No geometry found for node node_id: component_name
```

**Cause:** The asset doesn't contain any GLB files.

**Solution:**

1. Check asset files:

    ```bash
    vamscli file list -d database_id -a asset_id
    ```

2. Upload GLB files if missing:

    ```bash
    vamscli file upload -d database_id -a asset_id component.glb
    ```

3. Verify file types and extensions

#### GLB File Corruption

**Error:**

```
GLB Combine Error: Failed to read GLB file: Invalid GLB header
```

**Cause:** GLB files are corrupted or invalid.

**Solution:**

1. Re-download the GLB file:

    ```bash
    vamscli file download -d database_id -a asset_id -f filename.glb
    ```

2. Validate GLB file with 3D software
3. Re-upload corrected GLB file if needed

### Node Hierarchy Issues

#### No Root Nodes Found

**Error:**

```
BOM Assembly Error: No root nodes found in BOM hierarchy
```

**Cause:** All nodes have parent references, creating no root nodes.

**Solution:**

1. Ensure at least one node has no `parent_node` field:

    ```json
    {
        "node": "1",
        "source": "root_assembly"
        // No parent_node field = root node
    }
    ```

2. Check for circular references in parent-child relationships

#### Circular References

**Error:**

```
BOM Assembly Error: Circular reference detected in node hierarchy
```

**Cause:** Node hierarchy contains circular parent-child references.

**Solution:**

1. Review node relationships for cycles
2. Ensure parent-child relationships form a tree structure
3. Use a directed acyclic graph (DAG) validator

#### Invalid Parent References

**Warning:**

```
Warning: Node references non-existent parent: parent_node_id
```

**Cause:** A node references a parent that doesn't exist.

**Solution:**

1. Verify all `parent_node` values reference existing node IDs
2. Check for typos in node ID references
3. Ensure parent nodes are defined before children in the array

### Transform Matrix Issues

#### Invalid Transform Matrix

**Error:**

```
GLB Combine Error: Invalid transform matrix: expected 16 values, got 12
```

**Cause:** Transform matrix doesn't have exactly 16 values.

**Solution:**

1. Ensure transform matrices have exactly 16 float values:

    ```json
    "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    ```

2. Use identity matrix if no transform needed:

    ```json
    "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    ```

3. Omit matrix field to use default identity transform

#### Extreme Transform Values

**Warning:**

```
Warning: Extreme transform values detected, geometry may be distorted
```

**Cause:** Transform matrix contains very large or very small values.

**Solution:**

1. Review transform values for reasonableness
2. Check coordinate system units (meters vs millimeters)
3. Verify transform matrix calculation

### File System Issues

#### Insufficient Disk Space

**Error:**

```
OSError: [Errno 28] No space left on device
```

**Cause:** Not enough disk space for temporary files.

**Solution:**

1. Free up disk space
2. Use `--local-path` to specify location with more space:

    ```bash
    vamscli industry engineering bom bomassemble \
      --json-file bom.json \
      --database-id db \
      --local-path /path/to/large/storage
    ```

3. Clean up previous temporary files

#### Permission Denied

**Error:**

```
PermissionError: [Errno 13] Permission denied: '/tmp/vams_bom_assembly_'
```

**Cause:** Insufficient permissions to create temporary directories.

**Solution:**

1. Check permissions on temp directory
2. Use `--local-path` with writable directory:

    ```bash
    vamscli industry engineering bom bomassemble \
      --json-file bom.json \
      --database-id db \
      --local-path ./temp
    ```

3. Run with appropriate user permissions

#### Temp Directory Cleanup Issues

**Warning:**

```
Warning: Failed to clean up temporary directory: /tmp/bom_assembly_123
```

**Cause:** Temporary files couldn't be deleted.

**Solution:**

1. Manually clean up the directory
2. Check file permissions and locks
3. Use `--keep-temp-files` to avoid cleanup errors during debugging

### Asset Creation Issues

#### Asset Creation Failed

**Error:**

```
Asset Creation Error: Failed to create asset: Asset name already exists
```

**Cause:** An asset with the specified name already exists.

**Solution:**

1. Use a different asset name:

    ```bash
    vamscli industry engineering bom bomassemble \
      --json-file bom.json \
      --database-id db \
      --asset-create-name "Unique Assembly Name v2"
    ```

2. Check existing assets:
    ```bash
    vamscli assets list -d database_id
    ```

#### File Upload Failed

**Error:**

```
File Upload Error: Failed to upload file: Connection timeout
```

**Cause:** Network issues during file upload.

**Solution:**

1. Check network connectivity
2. Retry the operation
3. Use smaller batch sizes for large assemblies
4. Verify API gateway accessibility

### Memory and Performance Issues

#### Out of Memory

**Error:**

```
MemoryError: Unable to allocate memory for GLB processing
```

**Cause:** Assembly is too large for available memory.

**Solution:**

1. Process smaller sub-assemblies separately
2. Increase system memory
3. Use `--local-path` on fast storage to reduce memory pressure
4. Break large assemblies into smaller components

#### Slow Processing

**Issue:** BOM assembly takes very long time.

**Cause:** Large number of components or complex hierarchies.

**Solution:**

1. Use `--keep-temp-files` to avoid re-downloading on retries
2. Optimize BOM hierarchy to reduce depth
3. Pre-combine sub-assemblies
4. Use faster storage for `--local-path`

## Debugging Techniques

### Enable Verbose Logging

Use `--json-output` to get detailed processing information:

```bash
vamscli industry engineering bom bomassemble \
  --json-file bom.json \
  --database-id db \
  --json-output
```

### Keep Temporary Files

Use `--keep-temp-files` to inspect intermediate results:

```bash
vamscli industry engineering bom bomassemble \
  --json-file bom.json \
  --database-id db \
  --keep-temp-files \
  --local-path ./debug_temp
```

### Test Individual Components

Test each component separately:

```bash
# Test asset search
vamscli search simple -d database_id -q "component_name"

# Test GLB combination
vamscli industry spatial glbassetcombine -d database_id -a asset_id

# Test asset access
vamscli assets get -d database_id -a asset_id
```

### Validate BOM Structure

Create a minimal test BOM with 2-3 components:

```json
{
    "sources": [
        { "source": "simple_component", "storage": "VAMS" },
        { "source": "test_assembly", "storage": "no" }
    ],
    "scene": {
        "nodes": [
            { "node": "1", "source": "test_assembly" },
            { "node": "2", "source": "simple_component", "parent_node": "1" }
        ]
    }
}
```

## Getting Help

### Log Analysis

When reporting issues, include:

1. Complete error messages
2. BOM JSON structure (sanitized)
3. Asset and database IDs
4. VamsCLI version: `vamscli --version`
5. Command line used
6. Temporary file contents (if using `--keep-temp-files`)

### Support Information

Provide the following information when seeking support:

-   Operating system and version
-   VamsCLI version
-   Database and asset details
-   BOM JSON structure
-   Complete error messages
-   Steps to reproduce the issue

### Related Documentation

-   [BOM Commands Guide](../commands/bom-commands.md) - Complete command reference
-   [Industry Spatial Troubleshooting](industry-spatial-issues.md) - GLB processing issues
-   [Asset Management Troubleshooting](asset-file-issues.md) - Asset and file issues
-   [General Troubleshooting](general-troubleshooting.md) - Common CLI issues
