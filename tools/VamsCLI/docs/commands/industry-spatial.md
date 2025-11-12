# Industry Spatial Commands

Industry-specific spatial data processing commands for VamsCLI.

## Command Group

```bash
vamscli industry spatial [COMMAND]
```

The spatial command group provides tools for processing and manipulating spatial 3D data, particularly GLB (GL Transmission Format Binary) files used in VAMS asset hierarchies.

---

## glbassetcombine

Combine multiple GLB files from an asset hierarchy into a single GLB file.

### Synopsis

```bash
vamscli industry spatial glbassetcombine [OPTIONS]
```

### Description

This command exports an asset hierarchy, downloads all GLB files, and combines them into a single GLB file using transform data from asset relationships. It can optionally create a new asset and upload the combined GLB along with the export JSON.

The command performs the following steps:

1. **Export Assets**: Uses the `assets export` command to retrieve the asset hierarchy and download all GLB files
2. **Save Export JSON**: Saves the export result to a JSON file for reference
3. **Process Hierarchy**: Recursively processes the asset tree, combining GLBs with their transforms
4. **Combine GLBs**: Merges multiple GLBs into a single file, applying transformation matrices
5. **Optional Upload**: Creates a new asset and uploads the combined GLB and export JSON (if `--asset-create-name` is provided)

### Transform Priority

The command applies transformations in the following priority order:

1. **Matrix**: Uses the `Matrix` metadata if provided (supports multiple formats)
    - 2D arrays (row-major or column-major): `[[1,0,0,0], [0,1,0,0], [0,0,1,0], [tx,ty,tz,1]]`
    - 1D arrays: `[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, tx, ty, tz, 1]`
    - Space-separated strings: `"1 0 0 0 0 1 0 0 0 0 1 0 tx ty tz 1"`
2. **Components**: Builds from `Transform`/`Translation`, `Rotation`, and `Scale` metadata components
    - Missing components use defaults: Translation=[0,0,0], Rotation=[0,0,0,1], Scale=[1,1,1]
3. **Identity**: Defaults to identity matrix if no transform data is available

### Asset Instancing

The command supports **asset instancing** where the same asset appears multiple times in the hierarchy with different alias IDs. This is common in assemblies with repeated components (e.g., multiple bolts, screws, or identical parts at different positions).

**How It Works:**

-   Each relationship with an alias ID creates a separate transform node
-   Node names include the alias suffix: `AssetName__AliasID`
-   Each instance can have its own unique transform matrix
-   The same GLB mesh data is duplicated for each instance

**Example Scenario:**

Input hierarchy:

```
Engine Assembly (root, no GLB)
├── Bolt (alias: 10, transform: position A)
├── Bolt (alias: 20, transform: position B)
├── Bolt (alias: 30, transform: position C)
└── Bolt (alias: 40, transform: position D)
```

Output GLB structure:

```
Scene
└── Engine_Assembly (transform node)
    ├── Bolt__10 (transform at position A)
    │   └── Mesh: bolt.glb
    ├── Bolt__20 (transform at position B)
    │   └── Mesh: bolt.glb
    ├── Bolt__30 (transform at position C)
    │   └── Mesh: bolt.glb
    └── Bolt__40 (transform at position D)
        └── Mesh: bolt.glb
```

**Real-World Example:**

For an assembly with 8 identical bolts at different positions:

```bash
vamscli industry spatial glbassetcombine -d assembly-db -a bolt-assembly-root
```

This creates 8 separate transform nodes (Bolt**10, Bolt**20, ..., Bolt\_\_80), each with its own position/rotation from the relationship metadata.

### Options

#### Required Options

-   `-d, --database-id TEXT` - Database ID containing the root asset **[REQUIRED]**
-   `-a, --asset-id TEXT` - Root asset ID to start the hierarchy from **[REQUIRED]**

#### Export Control Options

-   `--include-only-primary-type-files` - Include only files with primaryType set **[OPTIONAL, default: False]**
-   `--no-file-metadata` - Exclude file metadata from export **[OPTIONAL, default: False]**
-   `--no-asset-metadata` - Exclude asset metadata from export **[OPTIONAL, default: False]**
-   `--fetch-entire-subtrees` - Fetch entire children relationship sub-trees **[OPTIONAL, default: True]**
-   `--include-parent-relationships` - Include parent relationships in the relationship data **[OPTIONAL, default: False]**

#### Output Options

-   `--local-path PATH` - Local path for temporary files (default: system temp directory) **[OPTIONAL]**
-   `--asset-create-name TEXT` - Create a new asset with the combined GLB **[OPTIONAL]**
-   `--delete-temporary-files` - Delete temporary files after upload (only with `--asset-create-name`) **[OPTIONAL, default: True]**
-   `--json-output` - Output result as JSON **[OPTIONAL]**

### Examples

#### Basic GLB Combination

Combine GLBs from an asset hierarchy and save to a temporary directory:

```bash
vamscli industry spatial glbassetcombine -d my-database -a root-asset-id
```

Output:

```
Setting up temporary directory...
Exporting assets from database 'my-database'...
Export JSON saved to: /tmp/vams_glb_combine_xyz/glbassetcombine_20251111_120000/root-asset-id_export.json
Processing asset hierarchy and combining GLBs...
Building asset tree and combining GLBs...
Processing asset: Root Asset
  Processing asset: Child Asset 1
  Processing asset: Child Asset 2
Combined GLB created: /tmp/vams_glb_combine_xyz/glbassetcombine_20251111_120000/root-asset-id__COMBINED.glb
✓ GLB combination completed successfully!
Root Asset: Root Asset
Combined GLB: /tmp/vams_glb_combine_xyz/glbassetcombine_20251111_120000/root-asset-id__COMBINED.glb
Combined GLB Size: 15.3 MB
Export JSON: /tmp/vams_glb_combine_xyz/glbassetcombine_20251111_120000/root-asset-id_export.json
Assets Processed: 3
GLBs Combined: 3
Temporary Directory: /tmp/vams_glb_combine_xyz/glbassetcombine_20251111_120000
```

#### Combine and Create New Asset

Combine GLBs and automatically create a new asset with the result:

```bash
vamscli industry spatial glbassetcombine \
  -d my-database \
  -a root-asset-id \
  --asset-create-name "Combined 3D Model"
```

This will:

-   Combine all GLBs in the hierarchy
-   Create a new asset named "Combined 3D Model"
-   Upload the combined GLB file
-   Upload the export JSON file
-   Delete temporary files (default behavior)

#### Custom Temporary Directory

Use a specific directory for temporary files:

```bash
vamscli industry spatial glbassetcombine \
  -d my-database \
  -a root-asset-id \
  --local-path ./my-temp-folder
```

The command will create a timestamped subdirectory within `./my-temp-folder/` to avoid conflicts with other runs.

#### JSON Output for Automation

Get machine-readable JSON output for scripting:

```bash
vamscli industry spatial glbassetcombine \
  -d my-database \
  -a root-asset-id \
  --json-output
```

Output:

```json
{
    "status": "success",
    "combined_glb_path": "/tmp/vams_glb_combine_xyz/glbassetcombine_20251111_120000/root-asset-id__COMBINED.glb",
    "export_json_path": "/tmp/vams_glb_combine_xyz/glbassetcombine_20251111_120000/root-asset-id_export.json",
    "combined_glb_size": 16056320,
    "combined_glb_size_formatted": "15.3 MB",
    "root_asset_name": "Root Asset",
    "temporary_directory": "/tmp/vams_glb_combine_xyz/glbassetcombine_20251111_120000",
    "total_assets_processed": 3,
    "total_glbs_combined": 3
}
```

#### Advanced: Combine with Metadata Filtering

Combine GLBs while excluding certain metadata:

```bash
vamscli industry spatial glbassetcombine \
  -d my-database \
  -a root-asset-id \
  --no-file-metadata \
  --no-asset-metadata \
  --local-path ./output
```

### Output Structure

The command creates the following files in the temporary directory:

```
glbassetcombine_YYYYMMDD_HHMMSS/
├── {sanitized-root-asset-name}_export.json          # Export data with hierarchy
├── {sanitized-root-asset-name}__COMBINED.glb        # Final combined GLB
├── {asset-id}/                                      # Downloaded GLB files (preserved)
│   └── {filename}.glb
```

**Note**: File names use the sanitized root asset name (not asset ID) to make them more human-readable. Special characters are replaced with underscores.

**Example**: For root asset named "Engine Assembly #1", files will be:

-   `Engine_Assembly__1_export.json`
-   `Engine_Assembly__1__COMBINED.glb`

### Transform Metadata Formats

The command supports multiple transform metadata formats:

#### Matrix Format (Space-Separated)

```json
{
    "Matrix": {
        "valueType": "string",
        "value": "1 0 0 0 0 1 0 0 0 0 1 0 5 10 15 1"
    }
}
```

#### Matrix Format (JSON Array)

```json
{
    "Matrix": {
        "valueType": "array",
        "value": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 10, 15, 1]
    }
}
```

#### Component Format (Translation, Rotation, Scale)

```json
{
    "Translation": {
        "valueType": "xyz",
        "value": "{\"x\": 5, \"y\": 10, \"z\": 15}"
    },
    "Scale": {
        "valueType": "xyz",
        "value": "{\"x\": 1, \"y\": 1, \"z\": 1}"
    }
}
```

### Error Handling

The command continues processing even if individual GLB combinations fail, collecting all errors and reporting them at the end:

```
⚠ GLB combination completed with some failures
Root Asset: Root Asset
Combined GLB: /tmp/.../root-asset-id__COMBINED.glb
Combined GLB Size: 12.5 MB
Assets Processed: 5
GLBs Combined: 4

⚠ Failed Operations (1):
  1. combine_with_child: Failed to combine child.glb: Invalid GLB format
```

### Use Cases

#### 1. Consolidate Multi-Asset 3D Models

Combine a complex 3D model split across multiple assets into a single GLB for easier distribution:

```bash
vamscli industry spatial glbassetcombine \
  -d production-db \
  -a turbine-assembly-root \
  --asset-create-name "Turbine Assembly - Complete Model"
```

#### 2. Create Simplified Versions

Combine specific parts of a hierarchy for simplified viewing:

```bash
vamscli industry spatial glbassetcombine \
  -d my-db \
  -a component-root \
  --include-only-primary-type-files \
  --local-path ./simplified-models
```

#### 3. Archive Complete Hierarchies

Export and combine entire asset hierarchies for archival:

```bash
vamscli industry spatial glbassetcombine \
  -d archive-db \
  -a project-root \
  --fetch-entire-subtrees \
  --local-path ./archives/project-2024
```

#### 4. Automated Pipeline Integration

Use in CI/CD pipelines with JSON output:

```bash
result=$(vamscli industry spatial glbassetcombine \
  -d $DB_ID \
  -a $ASSET_ID \
  --json-output)

combined_path=$(echo $result | jq -r '.combined_glb_path')
echo "Combined GLB created at: $combined_path"
```

### Performance Considerations

-   **Large Hierarchies**: Processing time increases with the number of assets and GLB file sizes
-   **Network Speed**: Download time depends on file sizes and network bandwidth
-   **Disk Space**: Ensure sufficient disk space for downloaded files and combined output
-   **Memory**: GLB combining operations load files into memory

### Implementation Details

#### Tree-First Approach

The command uses a **tree-first approach** to ensure correct glTF structure:

1. **Build Complete Transform Tree**: Creates transform nodes for ALL assets in the hierarchy, regardless of whether they have GLB files
2. **Merge Meshes**: Attaches GLB meshes to the appropriate nodes in the pre-built tree
3. **Write Combined GLB**: Outputs the final glTF file with proper node hierarchy

This approach ensures:

-   ✅ Complete transform hierarchy preserved
-   ✅ Empty transform nodes created for assets without GLBs
-   ✅ Correct parent-child relationships maintained
-   ✅ Each relationship instance gets its own node

#### Matrix Format Detection

The command automatically detects matrix format:

**Row-Major Detection**: If the last row is `[tx, ty, tz, 1.0]`, the matrix is row-major and will be transposed to column-major.

**Column-Major Detection**: If the last row is NOT `[tx, ty, tz, 1.0]`, the matrix is assumed to be column-major and used directly.

**Example Row-Major Input** (from your data):

```json
"[[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, -1.0, 0.0, 0.0], [0.075, 0.001, 5.095, 1.0]]"
```

**Converted to Column-Major Output**:

```
[1.0, 0.0, 0.0, 0.0,    # Column 0
 0.0, 0.0, -1.0, 0.0,   # Column 1
 0.0, 1.0, 0.0, 0.0,    # Column 2
 0.075, 0.001, 5.095, 1.0]  # Column 3 (translation)
```

### Troubleshooting

#### No GLB Files Found

```
✗ GLB Combine Error: No GLB files found in asset hierarchy
```

**Solution**: Ensure the asset hierarchy contains at least one GLB file. Use `vamscli assets export` to verify file types in the hierarchy.

**Note**: The command requires at least one GLB file to be present. Assets without GLBs will still have transform nodes created, but at least one asset must have a GLB file.

#### Invalid Characters in Asset Name

The command automatically:

-   Sanitizes asset names for file naming by replacing special characters with underscores
-   Preserves alphanumeric characters, underscores, hyphens, and spaces
-   Uses sanitized names for both output files and glTF node names

**Example**: Asset name `"Bolt/Assembly#1"` becomes:

-   File names: `Bolt_Assembly_1_export.json`, `Bolt_Assembly_1__COMBINED.glb`
-   Node name: `Bolt_Assembly_1`

#### Transform Parsing Errors

If transform metadata is malformed, the command:

-   Logs a warning about the parsing failure
-   Falls back to identity matrix for that relationship
-   Continues processing other relationships

#### Multiple Instances Not Showing

If you expect multiple instances but only see one node:

**Check**: Verify that relationships have unique `assetLinkAliasId` values

```bash
vamscli assets export -d my-db -a root-asset --json-output | jq '.relationships[] | {parent: .parentAssetId, child: .childAssetId, alias: .assetLinkAliasId}'
```

**Expected**: Each instance should have a different alias ID

#### Temporary Directory Conflicts

Each command run creates a unique timestamped subdirectory to prevent conflicts between multiple runs.

**Format**: `glbassetcombine_YYYYMMDD_HHMMSS/`

### Related Commands

-   `vamscli assets export` - Export asset hierarchies with file downloads
-   `vamscli assets create` - Create new assets
-   `vamscli file upload` - Upload files to assets

### Notes

-   Downloaded GLB files are preserved in the temporary directory unless `--delete-temporary-files` is used with `--asset-create-name`
-   Output file names use the sanitized root asset name for better readability
-   Transform matrices are applied in column-major order (glTF standard)
-   Each relationship instance with an alias ID creates a separate transform node
-   Multiple GLBs per asset are added as separate meshes under the same transform node

---

## See Also

-   [Asset Management Commands](asset-management.md)
-   [File Operations Commands](file-operations.md)
-   [Global Options](global-options.md)
