# BOM (Bill of Materials) Commands

This directory contains the BOM (Bill of Materials) commands for VamsCLI, which enable assembly of 3D geometries from hierarchical component structures.

## Files

- `Dynamic_BOM.py` - Main BOM assembly command implementation
- `__init__.py` - Module initialization and command registration
- `data/example.json` - Example BOM JSON file with engine assembly
- `README.md` - This documentation file

## Command Overview

The BOM commands provide functionality to:

1. **Parse BOM JSON files** with parent-child relationships
2. **Traverse node hierarchies** recursively from leaves to root
3. **Download GLB files** from VAMS assets using asset names
4. **Combine geometries** with proper transform matrix application
5. **Create new assets** with assembled results
6. **Handle complex assemblies** with multiple levels and components

## Key Features

### Hierarchical Processing
- Supports arbitrarily deep component hierarchies
- Processes nodes bottom-up (children before parents)
- Handles multiple root nodes in single BOM

### Transform Management
- Applies 4x4 transform matrices during geometry combination
- Uses identity matrix as default when no transform specified
- Supports translation, rotation, and scaling transforms

### Asset Integration
- Searches VAMS for assets by name
- Uses `glbassetcombine` for component GLB retrieval
- Caches downloaded GLBs to avoid redundant downloads

### Output Options
- CLI formatted output with assembly details
- JSON output for programmatic integration
- Optional asset creation with file uploads
- Configurable temporary file management

## Usage Example

```bash
# Basic BOM assembly
vamscli industry engineering bom bomassemble \
  --json-file data/example.json \
  --database-id my-database

# Assembly with new asset creation
vamscli industry engineering bom bomassemble \
  --json-file data/example.json \
  --database-id my-database \
  --asset-create-name "Engine Assembly v1.0"

# JSON output for automation
vamscli industry engineering bom bomassemble \
  --json-file data/example.json \
  --database-id my-database \
  --json-output
```

## BOM JSON Structure

The BOM JSON file must contain:

### Sources Array
Defines all components referenced in the assembly:
```json
"sources": [
  { "source": "component_name", "storage": "VAMS" },
  { "source": "assembly_name", "storage": "no" }
]
```

### Scene Nodes Array
Defines the hierarchical structure:
```json
"scene": {
  "nodes": [
    {
      "node": "unique_id",
      "source": "component_name",
      "parent_node": "parent_id",
      "matrix": [16 float values],
      "bomdata": { "optional": "metadata" }
    }
  ]
}
```

## Implementation Details

### Core Functions

- `parse_bom_json()` - Validates and loads BOM JSON structure
- `build_node_tree()` - Creates parent-child relationship tree
- `find_root_nodes()` - Identifies top-level assembly nodes
- `get_asset_id_by_name()` - Searches VAMS for assets by name
- `download_glb_for_node()` - Retrieves combined GLB using glbassetcombine
- `combine_node_geometries()` - Recursively combines child geometries
- `combine_glb_files_with_transforms()` - Merges GLB files with transforms

### Error Handling

The implementation includes comprehensive error handling for:
- Invalid BOM JSON structure
- Missing or inaccessible assets
- GLB processing failures
- File system issues
- Network connectivity problems

### Performance Optimizations

- GLB caching to avoid redundant downloads
- Bottom-up processing to minimize memory usage
- Configurable temporary directory management
- Parallel processing where possible

## Testing

Unit tests are located in `tests/industry/engineering/test_bom_commands.py` and cover:
- Command help and parameter validation
- BOM JSON parsing and validation
- Node tree building and traversal
- Asset lookup and GLB processing
- Error handling scenarios
- Output formatting

## Dependencies

The BOM commands depend on:
- `glb_combiner` utilities for GLB file processing
- `industry.spatial.glb` commands for asset GLB combination
- `assets` and `file` commands for asset creation and uploads
- Standard VamsCLI authentication and API client infrastructure

## Related Commands

- `industry spatial glbassetcombine` - Combines GLB files from asset hierarchies
- `assets create` - Creates new assets in VAMS
- `file upload` - Uploads files to assets
- `search simple` - Searches for assets by name

## Documentation

Complete documentation is available in:
- `docs/commands/bom-commands.md` - Command reference and examples
- `docs/troubleshooting/bom-issues.md` - Troubleshooting guide
- `data/example.json` - Example BOM JSON structure