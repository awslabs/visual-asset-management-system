---
sidebar_label: Industry
title: Industry Command Troubleshooting
---

# Industry Command Troubleshooting

This page covers issues encountered when running the VamsCLI industry commands: BOM assembly, PLM XML import, and spatial GLB combination.

---

## BOM Assembly

These issues apply to `vamscli industry engineering bom bomassemble`, which assembles a combined GLB from a BOM JSON hierarchy.

### Invalid BOM JSON Structure

**Symptoms:**

-   `BOM Assembly Error: Invalid BOM JSON: missing 'scene.nodes' structure`
-   `BOM Assembly Error: Invalid BOM JSON: missing 'sources' field`
-   `BOM Assembly Error: Invalid JSON file: Expecting ',' delimiter`

**Cause:**

The BOM JSON file is missing required fields or contains syntax errors. The assembler requires both a top-level `sources` array and a `scene.nodes` array.

**Resolution:**

Validate the file against the expected structure, in which `sources` lists every referenced component and `scene.nodes` defines the node hierarchy:

```json
{
    "sources": [
        { "source": "component1", "storage": "VAMS" },
        { "source": "assembly", "storage": "no" }
    ],
    "scene": {
        "nodes": [
            { "node": "1", "source": "assembly" },
            { "node": "2", "source": "component1", "parent_node": "1" }
        ]
    }
}
```

Run the JSON through a validator to catch missing commas, trailing commas, unmatched braces, or unescaped quotes.

### Asset or Database Not Found

**Symptoms:**

-   `Warning: Asset not found: component_name`
-   `Database Error: Database not found: database_id`
-   `API Error: Search request failed: 400 Bad Request`

**Cause:**

A `source` value in the BOM does not match an asset in the target database, the database ID is wrong or inaccessible, or the search request to resolve components failed.

**Resolution:**

1. Confirm connectivity and the active session with `vamscli auth status`.
2. List databases to verify the ID and your access: `vamscli database list`.
3. Resolve component names exactly (asset names are case-sensitive and must not contain stray whitespace):

    ```bash
    vamscli search simple -d database_id -q "component_name"
    ```

### Node Hierarchy Errors

**Symptoms:**

-   `BOM Assembly Error: No root nodes found in BOM hierarchy`
-   `BOM Assembly Error: Circular reference detected in node hierarchy`
-   `Warning: Node references non-existent parent: parent_node_id`

**Cause:**

The `scene.nodes` graph is not a valid tree. Every node has a `parent_node` (no root), a cycle exists, or a `parent_node` points to a node ID that is not defined.

**Resolution:**

Ensure at least one node omits `parent_node` (that node is the root), that parent-child references form an acyclic tree, and that every `parent_node` value matches an existing `node` ID. Define parent nodes before their children in the array.

### Invalid or Extreme Transform Matrix

**Symptoms:**

-   `GLB Combine Error: Invalid transform matrix: expected 16 values, got 12`
-   `Warning: Extreme transform values detected, geometry may be distorted`

**Cause:**

A node `matrix` field does not contain exactly 16 float values, or the values are far outside a reasonable range (often a unit mismatch, such as millimeters versus meters).

**Resolution:**

Provide a complete 4x4 matrix of 16 floats, or omit the `matrix` field to use the default identity transform:

```json
"matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
```

Review extreme values against the asset's coordinate system units before re-running.

### GLB Combination Failures

**Symptoms:**

-   `Warning: glbassetcombine failed for component_name: Failed to combine GLB files`
-   `Warning: No geometry found for node node_id: component_name`
-   `GLB Combine Error: Failed to read GLB file: Invalid GLB header`

**Cause:**

Per-node GLB retrieval relies on the spatial GLB combine step (see [Spatial GLB Combine](#spatial-glb-combine)). The component asset has no GLB files, or a GLB file is corrupt.

**Resolution:**

1. Confirm the component asset contains GLB files: `vamscli file list -d database_id -a asset_id`.
2. Test the component's GLB combination in isolation: `vamscli industry spatial glbassetcombine -d database_id -a asset_id`.
3. If a file is corrupt, re-download and validate it, then re-upload:

    ```bash
    vamscli file download -d database_id -a asset_id -f filename.glb
    ```

### File System and Permission Errors

**Symptoms:**

-   `OSError: [Errno 28] No space left on device`
-   `PermissionError: [Errno 13] Permission denied: '/tmp/vams_bom_assembly_'`
-   `Warning: Failed to clean up temporary directory`

**Cause:**

The temporary working directory has insufficient disk space, is not writable, or could not be removed after processing.

**Resolution:**

Point the command at a writable location with adequate free space using `--local-path`:

```bash
vamscli industry engineering bom bomassemble \
  --json-file bom.json \
  --database-id db \
  --local-path ./temp
```

Use `--keep-temp-files` to retain intermediate files for inspection and avoid cleanup errors during debugging, then remove the directory manually.

### Asset Creation or Upload Failures

**Symptoms:**

-   `Asset Creation Error: Failed to create asset: Asset name already exists`
-   `File Upload Error: Failed to upload file: Connection timeout`

**Cause:**

When `--asset-create-name` is supplied, the target asset name already exists, or a network interruption occurred during upload of the assembled GLB.

**Resolution:**

Choose a unique name (check existing assets with `vamscli assets list -d database_id`) and retry:

```bash
vamscli industry engineering bom bomassemble \
  --json-file bom.json \
  --database-id db \
  --asset-create-name "Engine Assembly v2"
```

### Memory or Slow Processing

**Symptoms:**

-   `MemoryError: Unable to allocate memory for GLB processing`
-   BOM assembly runs for a very long time.

**Cause:**

Large assemblies load GLB data into memory, and deep hierarchies with many components increase both memory pressure and processing time.

**Resolution:**

Break large assemblies into smaller sub-assemblies and pre-combine them, place `--local-path` on fast storage, and use `--keep-temp-files` so cached component GLBs are not re-downloaded on retry.

:::tip
For a reproducible baseline, validate the pipeline against a minimal BOM containing two or three components before scaling up to a full assembly.
:::

---

## PLM XML Import

These issues apply to `vamscli industry engineering plm plmxml import`, which creates assets, metadata, files, and asset links from PLM XML files.

### No XML Files or Directory Not Found

**Symptoms:**

-   `✗ No XML files found in: /path/to/directory`
-   `✗ PLM XML directory not found: /path/to/directory`
-   `✗ Path is not a directory: /path/to/directory`

**Cause:**

The `--plmxml-dir` value does not resolve to an existing directory, or the directory contains no files with a `.xml` extension.

**Resolution:**

Use an absolute path to the directory, confirm the files use the `.xml` extension, and verify read permissions on the directory and its contents.

### Asset Creation Failures

**Symptoms:**

The Phase 1 summary reports failures, for example `Assets: 100 created, 20 existing, 30 failed`.

**Cause:**

Component item revisions map to asset IDs containing forbidden characters, the user lacks write access to the target database, or the API was unreachable during creation.

**Resolution:**

Asset IDs are sanitized automatically, so persistent failures usually indicate a permission or connectivity problem. Verify write access to the database and check `vamscli auth status`. Inspect the CLI logs for the specific per-asset error.

### XML Upload Failures

**Symptoms:**

The summary reports `XML Files Failed` greater than zero when `--upload-xml` is set.

**Cause:**

A source XML file was moved or deleted during the import, exceeds the VAMS file size limit, or the user lacks file upload permission.

**Resolution:**

Keep the XML files in place for the full duration of the import, confirm file upload permission, and verify the file size is within VAMS limits.

:::note
With `--upload-xml`, only root (top-level) components receive the source XML. Child components are intentionally skipped, so a large `XML Files Skipped (non-root)` count is expected and not an error.
:::

### Performance and Memory Issues

**Symptoms:**

-   Import takes longer than expected.
-   Out-of-memory errors during large imports.

**Cause:**

Parallelism is controlled by `--max-workers` (default 15). Too few workers underutilizes the connection; too many overload system CPU and memory.

**Resolution:**

Tune `--max-workers` to your environment. Raise it (for example, `--max-workers 25`) to increase throughput when resources allow; lower it (for example, `--max-workers 10`) to reduce memory use. For very large datasets, split the XML files across directories and run sequential imports.

```bash
vamscli industry engineering plm plmxml import \
  -d engineering-db \
  --plmxml-dir /data/plm/export \
  --max-workers 10
```

---

## Spatial GLB Combine

These issues apply to `vamscli industry spatial glbassetcombine`, which combines GLB files across an asset hierarchy into a single GLB.

### No GLB Files Found

**Symptoms:**

-   `✗ GLB Combine Error: No GLB files found in asset hierarchy`

**Cause:**

The command builds transform nodes for every asset in the hierarchy but requires at least one asset to contain a GLB file. None of the assets in the exported hierarchy has a GLB.

**Resolution:**

Confirm that the hierarchy contains GLB geometry before combining. Export the hierarchy and inspect the file types:

```bash
vamscli assets export -d my-db -a root-asset --json-output
```

### Missing or Duplicated Asset Instances

**Symptoms:**

-   Repeated components (for example, multiple identical bolts) appear as a single node instead of one node per instance.

**Cause:**

Instancing is keyed on the asset link alias ID. Relationships that share the same `assetLinkAliasId`, or have none, collapse into one transform node rather than creating `AssetName__AliasID` nodes per instance.

**Resolution:**

Verify each repeated relationship has a unique alias ID:

```bash
vamscli assets export -d my-db -a root-asset --json-output | jq '.relationships[] | {parent: .parentAssetId, child: .childAssetId, alias: .assetLinkAliasId}'
```

Each instance should report a distinct `assetLinkAliasId`.

### Transform Parsing Errors

**Symptoms:**

-   A warning that transform metadata could not be parsed, after which the affected component is placed without its intended transform.

**Cause:**

The `Matrix` metadata value, or the `Translation`/`Rotation`/`Scale` components, are malformed or in an unrecognized format.

**Resolution:**

The command logs the parsing failure, falls back to the identity matrix for that relationship, and continues. To fix placement, correct the transform metadata to a supported form — a 1D or 2D matrix array, a space-separated matrix string, or component values — and re-run. A row-major matrix is detected and transposed automatically when its last row is `[tx, ty, tz, 1.0]`.

### Partial Combination Failures

**Symptoms:**

-   `⚠ GLB combination completed with some failures`, followed by a list such as `combine_with_child: Failed to combine child.glb: Invalid GLB format`.

**Cause:**

One or more child GLB files are corrupt or otherwise unreadable. The command continues past individual failures and reports them at the end rather than aborting.

**Resolution:**

Re-download and validate the named GLB files, then re-upload corrected versions and run the combine again. The combined output still includes every component that processed successfully.

### Temporary Directory and Naming

**Symptoms:**

-   Output file or directory names differ from the asset ID, or runs appear to write to different folders.

**Cause:**

Output files are named from the sanitized root asset name (special characters become underscores), and each run writes to a unique timestamped subdirectory (`glbassetcombine_YYYYMMDD_HHMMSS/`) to avoid conflicts between runs.

**Resolution:**

This is expected behavior. To control the parent location, pass `--local-path`; a timestamped subdirectory is still created within it:

```bash
vamscli industry spatial glbassetcombine \
  -d my-database \
  -a root-asset-id \
  --local-path ./output
```

:::tip
Use `--json-output` to capture the resolved `combined_glb_path`, `total_assets_processed`, and `total_glbs_combined` for scripting and automated pipelines.
:::

---

## Related Pages

-   [Industry Commands](../commands/industry.md)
-   [Asset Commands](../commands/assets.md)
-   [General CLI Troubleshooting](./general.md)
