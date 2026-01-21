# BOM (Bill of Materials) Commands

This document provides comprehensive guidance for using VamsCLI BOM (Bill of Materials) commands for assembling 3D geometries from hierarchical component structures.

## Overview

The BOM commands enable you to:

-   Parse BOM JSON files with parent-child relationships
-   Recursively traverse node hierarchies from leaves to root
-   Download and combine GLB files from VAMS assets
-   Apply transform matrices during geometry combination
-   Create new assets with assembled geometries
-   Handle complex multi-level assemblies

## Command Structure

```
vamscli industry engineering bom <command> [options]
```

## Available Commands

### `bomassemble`

Assemble GLB geometry from BOM JSON hierarchy.

#### Syntax

```bash
vamscli industry engineering bom bomassemble [OPTIONS]
```

#### Required Options

-   `--json-file, -j TEXT` - Path to BOM JSON file (e.g., example.json)
-   `--database-id, -d TEXT` - Database ID containing the assets

#### Optional Options

-   `--local-path TEXT` - Local path for temp files (default: system temp)
-   `--keep-temp-files` - Keep temporary files after processing
-   `--asset-create-name TEXT` - Create new asset with this name and upload all generated GLB files
-   `--delete-temporary-files` - Delete temp files after upload (default: True, only with --asset-create-name)
-   `--json-output` - Output raw JSON response

#### BOM JSON Format

The BOM JSON file must follow this structure:

```json
{
    "sources": [
        { "source": "component_name_1", "storage": "VAMS" },
        { "source": "component_name_2", "storage": "VAMS" },
        { "source": "assembly_root", "storage": "no" }
    ],
    "scene": {
        "nodes": [
            {
                "node": "1",
                "source": "assembly_root",
                "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
            },
            {
                "node": "2",
                "source": "component_name_1",
                "parent_node": "1",
                "matrix": [1, 0, 0, 0.5, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
            },
            {
                "node": "3",
                "source": "component_name_2",
                "parent_node": "1",
                "matrix": [1, 0, 0, -0.5, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
            }
        ]
    }
}
```

#### JSON Structure Fields

**Sources Array:**

-   `source` - Name of the component (must match VAMS asset name if stored)
-   `storage` - "VAMS" for stored assets, "no" for virtual assemblies

**Scene Nodes Array:**

-   `node` - Unique node identifier
-   `source` - Reference to source name
-   `parent_node` - Parent node ID (omit for root nodes)
-   `matrix` - 4x4 transform matrix as 16 floats (optional, defaults to identity)

#### Transform Matrix Format

The transform matrix is a 4x4 matrix stored as 16 floats in column-major order:

```
[m00, m10, m20, m30, m01, m11, m21, m31, m02, m12, m22, m32, m03, m13, m23, m33]
```

Where:

-   Translation: m03, m13, m23
-   Rotation/Scale: 3x3 upper-left submatrix
-   Homogeneous: m30, m31, m32, m33 (typically 0, 0, 0, 1)

#### Processing Flow

1. **Parse BOM JSON** - Validates structure and loads hierarchy
2. **Build Node Tree** - Creates parent-child relationships
3. **Find Root Nodes** - Identifies top-level assembly nodes
4. **Asset Lookup** - Searches VAMS for assets with matching names
5. **GLB Download** - Uses `glbassetcombine` to get combined GLB files
6. **Geometry Combination** - Recursively combines child geometries with transforms
7. **Asset Creation** - Optionally creates new asset and uploads results

#### Examples

**Basic Assembly:**

```bash
vamscli industry engineering bom bomassemble \
  --json-file engine_assembly.json \
  --database-id my-database
```

**Assembly with Asset Creation:**

```bash
vamscli industry engineering bom bomassemble \
  --json-file engine_assembly.json \
  --database-id my-database \
  --asset-create-name "Complete Engine Assembly"
```

**Custom Temp Directory:**

```bash
vamscli industry engineering bom bomassemble \
  --json-file engine_assembly.json \
  --database-id my-database \
  --local-path ./temp_assembly
```

**Keep Temp Files for Debugging:**

```bash
vamscli industry engineering bom bomassemble \
  --json-file engine_assembly.json \
  --database-id my-database \
  --keep-temp-files
```

**JSON Output:**

```bash
vamscli industry engineering bom bomassemble \
  --json-file engine_assembly.json \
  --database-id my-database \
  --json-output
```

#### Output Formats

**CLI Output:**

```
BOM JSON: engine_assembly.json
Database: my-database
Total Nodes: 4
Total Sources: 4
GLBs Downloaded: 3
Root Nodes Processed: 1

Assembled Root Nodes:
  Node 1 (engine_complete):
    GLB: /tmp/engine_complete_node_1_combined.glb
    Size: 2.5 MB

✓ New Asset Created:
  Asset ID: asset-123456
  Database: my-database
  Name: Complete Engine Assembly
  Total Files Uploaded: 4
  Files: engine_block.glb, piston.glb, crankshaft.glb, engine_assembly.json
```

**JSON Output:**

```json
{
    "status": "success",
    "bom_json_file": "engine_assembly.json",
    "database_id": "my-database",
    "total_nodes": 4,
    "total_sources": 4,
    "root_nodes_processed": 1,
    "assemblies": [
        {
            "root_node_id": "1",
            "root_source": "engine_complete",
            "combined_glb_path": "/tmp/engine_complete_node_1_combined.glb",
            "combined_glb_size": 2621440,
            "combined_glb_size_formatted": "2.5 MB"
        }
    ],
    "temporary_directory": "deleted",
    "glbs_downloaded": 3,
    "new_asset": {
        "asset_id": "asset-123456",
        "database_id": "my-database",
        "name": "Complete Engine Assembly",
        "files_uploaded": [
            "engine_block.glb",
            "piston.glb",
            "crankshaft.glb",
            "engine_assembly.json"
        ],
        "total_files": 4
    }
}
```

## Advanced Usage

### Complex Hierarchies

The BOM system supports arbitrarily deep hierarchies:

```json
{
    "sources": [
        { "source": "screw", "storage": "VAMS" },
        { "source": "bracket", "storage": "VAMS" },
        { "source": "bracket_assembly", "storage": "no" },
        { "source": "panel", "storage": "VAMS" },
        { "source": "panel_assembly", "storage": "no" },
        { "source": "complete_unit", "storage": "no" }
    ],
    "scene": {
        "nodes": [
            { "node": "1", "source": "complete_unit" },
            { "node": "2", "source": "panel_assembly", "parent_node": "1" },
            { "node": "3", "source": "bracket_assembly", "parent_node": "2" },
            { "node": "4", "source": "bracket", "parent_node": "3" },
            {
                "node": "5",
                "source": "screw",
                "parent_node": "3",
                "matrix": [1, 0, 0, 0.1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
            },
            {
                "node": "6",
                "source": "screw",
                "parent_node": "3",
                "matrix": [1, 0, 0, -0.1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
            },
            { "node": "7", "source": "panel", "parent_node": "2" }
        ]
    }
}
```

### Multiple Root Nodes

The system can handle multiple root assemblies in a single BOM:

```json
{
    "scene": {
        "nodes": [
            { "node": "1", "source": "left_assembly" },
            { "node": "2", "source": "right_assembly" },
            { "node": "3", "source": "left_component", "parent_node": "1" },
            { "node": "4", "source": "right_component", "parent_node": "2" }
        ]
    }
}
```

### Transform Applications

Transforms are applied hierarchically:

-   Child transforms are relative to parent coordinate systems
-   Identity matrix is used when no transform is specified
-   Transforms accumulate down the hierarchy

## Integration with Other Commands

### GLB Asset Combine

The BOM assembly uses the `glbassetcombine` command internally:

```bash
# This is called automatically for each VAMS asset
vamscli industry spatial glbassetcombine -d database_name -a asset_id
```

### Asset Creation

When using `--asset-create-name`, the following commands are invoked:

```bash
# Create new asset
vamscli assets create -d database_id --name "Assembly Name"

# Upload GLB files
vamscli file upload -d database_id -a asset_id file1.glb file2.glb

# Upload BOM JSON
vamscli file upload -d database_id -a asset_id bom.json
```

## Error Handling

### Common Errors

**Invalid BOM JSON:**

```
BOM Assembly Error: Invalid BOM JSON: missing 'scene.nodes' structure
```

**Asset Not Found:**

```
Warning: Asset not found: component_name
```

**GLB Combine Failure:**

```
Warning: glbassetcombine failed for component_name: <error details>
```

**No Root Nodes:**

```
BOM Assembly Error: No root nodes found in BOM hierarchy
```

### Troubleshooting

1. **Validate BOM JSON Structure:**

    - Ensure `sources` and `scene.nodes` arrays exist
    - Verify node IDs are unique
    - Check parent-child references are valid

2. **Check Asset Names:**

    - Asset names in BOM must exactly match VAMS asset names
    - Use search commands to verify asset existence

3. **Verify Database Access:**

    - Ensure database ID is correct
    - Verify user has read access to assets

4. **GLB File Issues:**
    - Check that assets have GLB files
    - Verify GLB files are valid and not corrupted

## Performance Considerations

### Large Assemblies

For assemblies with many components:

-   Use `--local-path` to specify fast storage for temp files
-   Consider `--keep-temp-files` for debugging large assemblies
-   Monitor disk space usage during processing

### Memory Usage

-   GLB files are loaded into memory during combination
-   Large assemblies may require significant RAM
-   Process components in smaller batches if memory is limited

### Network Optimization

-   Asset downloads are cached during processing
-   Multiple references to the same asset reuse cached GLB files
-   Use reliable network connection for large asset downloads

## Best Practices

### BOM Design

1. **Hierarchical Organization:**

    - Group related components under sub-assemblies
    - Use meaningful node IDs and source names
    - Maintain consistent naming conventions

2. **Transform Management:**

    - Use identity matrices when no transform is needed
    - Apply transforms at appropriate hierarchy levels
    - Test transforms with simple geometries first

3. **Asset Preparation:**
    - Ensure all referenced assets exist in VAMS
    - Verify GLB files are properly formatted
    - Use consistent coordinate systems across assets

### Workflow Integration

1. **Development Workflow:**

    - Start with simple 2-3 component assemblies
    - Test individual asset GLB files first
    - Use `--keep-temp-files` during development

2. **Production Workflow:**

    - Validate BOM JSON before processing
    - Use `--asset-create-name` for final assemblies
    - Clean up temp files with `--delete-temporary-files`

3. **Quality Assurance:**
    - Review assembled GLB files in 3D viewers
    - Verify component positioning and scaling
    - Test with different BOM variations

## Related Commands

-   [`industry spatial glbassetcombine`](industry-spatial.md#glbassetcombine) - Combine GLB files from asset hierarchies
-   [`assets create`](asset-management.md#create) - Create new assets
-   [`file upload`](file-operations.md#upload) - Upload files to assets
-   [`search simple`](search-operations.md#simple) - Search for assets by name

## See Also

-   [Industry Spatial Commands](industry-spatial.md) - Related 3D geometry commands
-   [Asset Management](asset-management.md) - Asset creation and management
-   [File Operations](file-operations.md) - File upload and management
-   [Global Options](global-options.md) - Common CLI options and JSON output
