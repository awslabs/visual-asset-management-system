---
sidebar_label: Industry
title: Industry Commands
---

# Industry Commands

Industry-specific commands for engineering Bill of Materials (BOM) assembly, Product Lifecycle Management (PLM) XML import, and spatial GLB combination. Commands are grouped under `industry engineering` (BOM, PLM) and `industry spatial` (GLB).

---

## industry engineering bom bomassemble

Assemble GLB geometry from a BOM JSON hierarchy. Parses a BOM file, resolves each VAMS-stored source to an asset, retrieves its combined GLB, applies the per-node transform matrices, and merges everything into a single combined GLB per root node.

```bash
vamscli industry engineering bom bomassemble [OPTIONS]
```

| Option                     | Type | Required | Description                                                                             |
| -------------------------- | ---- | -------- | --------------------------------------------------------------------------------------- |
| `-j`, `--json-file`        | TEXT | Yes      | Path to the BOM JSON file                                                               |
| `-d`, `--database-id`      | TEXT | Yes      | Database ID containing the assets                                                       |
| `--local-path`             | PATH | No       | Local path for temporary files (default: system temp)                                   |
| `--keep-temp-files`        | Flag | No       | Keep temporary files after processing                                                   |
| `--asset-create-name`      | TEXT | No       | Create a new asset with this name and upload all generated GLB files                    |
| `--delete-temporary-files` | Flag | No       | Delete temp files after upload (default: true; applies only with `--asset-create-name`) |
| `--json-output`            | Flag | No       | Output raw JSON response                                                                |

### BOM JSON format

```json
{
    "sources": [
        { "source": "component_name_1", "storage": "VAMS" },
        { "source": "component_name_2", "storage": "VAMS" },
        { "source": "assembly_root", "storage": "no" }
    ],
    "scene": {
        "nodes": [
            { "node": "1", "source": "assembly_root" },
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

The file requires a top-level `sources` array and a `scene.nodes` array.

-   **sources**: Component definitions. `storage` is `"VAMS"` for a stored asset (resolved by matching `source` to a VAMS asset name) or `"no"` for a virtual assembly node that holds children but has no geometry of its own.
-   **scene.nodes**: The hierarchy. Each node has a unique `node` ID, a `source` reference, an optional `parent_node` (omit for a root node), and an optional `matrix`. Multiple root nodes are supported, and the same source may be referenced by multiple nodes.

### Transform matrix

The `matrix` field is a 4x4 transform stored as 16 floats in column-major order. When omitted, the node defaults to the identity matrix. Transforms are applied hierarchically so each child is positioned relative to its parent's coordinate system.

```text
[m00, m10, m20, m30, m01, m11, m21, m31, m02, m12, m22, m32, m03, m13, m23, m33]
```

-   Translation occupies `m03`, `m13`, `m23`.
-   Rotation and scale occupy the upper-left 3x3 submatrix.

:::note[Internal GLB retrieval]
For each VAMS-stored source, `bomassemble` invokes `industry spatial glbassetcombine` to obtain that asset's combined GLB (including any of its own child assets) before applying the BOM node transforms. Source names in the BOM must match the VAMS asset names exactly.
:::

When `--asset-create-name` is provided, the command creates the new asset and uploads the assembled root GLB(s), the downloaded and intermediate component GLBs, and the original BOM JSON file. Temporary files are then removed unless `--keep-temp-files` is set or a `--local-path` was supplied.

### Examples

```bash
# Basic assembly
vamscli industry engineering bom bomassemble -j assembly.json -d my-database

# Assembly with new asset creation
vamscli industry engineering bom bomassemble -j assembly.json -d my-database --asset-create-name "Complete Assembly"

# Custom temp directory
vamscli industry engineering bom bomassemble -j assembly.json -d my-database --local-path ./temp

# Keep temp files for debugging
vamscli industry engineering bom bomassemble -j assembly.json -d my-database --keep-temp-files

# JSON output
vamscli industry engineering bom bomassemble -j assembly.json -d my-database --json-output
```

---

## industry engineering plm plmxml import

Import Product Lifecycle Management data from PLM XML files into VAMS, creating assets, metadata, file uploads, and asset links with parallel processing.

```bash
vamscli industry engineering plm plmxml import [OPTIONS]
```

| Option                | Type    | Required | Description                                                                     |
| --------------------- | ------- | -------- | ------------------------------------------------------------------------------- |
| `-d`, `--database-id` | TEXT    | Yes      | Target database ID where assets are created                                     |
| `--plmxml-dir`        | PATH    | Yes      | Directory containing the PLM XML files (must exist)                             |
| `--max-workers`       | INTEGER | No       | Maximum number of parallel workers (default: 15)                                |
| `--upload-xml`        | Flag    | No       | Upload source PLM XML files to their corresponding root assets (default: false) |
| `--json-output`       | Flag    | No       | Output raw JSON response                                                        |

The command reads every `.xml` file in `--plmxml-dir`. Component item revisions become asset names (sanitized to meet VAMS asset-ID rules), the parent-child occurrence hierarchy becomes `parentChild` asset links, PLM UserData becomes asset and link metadata, and occurrence transforms are stored as a 4x4 `Matrix` (`matrix4x4`) on each link.

### Import phases

:::info[Four-phase import]
The import runs in four phases. Phase 3 runs only when the earlier phases produced link metadata.

-   **Phase 0 — XML parsing**: Parse all XML files, extract component definitions and relationships, and identify the root component of each file.
-   **Phase 1 — Asset creation**: Create VAMS assets in parallel, reusing existing assets with matching names instead of creating duplicates.
-   **Phase 2 — Parallel operations**: Concurrently create asset metadata, upload geometry files (and, with `--upload-xml`, the source XML), and create the parent-child asset links.
-   **Phase 3 — Link metadata**: Store the transform `Matrix` and remaining UserData fields on the created asset links.
    :::

When `--upload-xml` is set, only the root (top-level) component of each XML file receives the source XML upload; child components are skipped so the file is not uploaded multiple times. The final summary reports XML files uploaded, failed, and skipped.

:::tip[Worker count guidelines]

-   **Low (5-10)**: Conservative, for limited resources.
-   **Medium (15-20)**: Balanced (default: 15).
-   **High (25-30)**: Maximum throughput, requires adequate resources.
    :::

### Examples

```bash
# Basic import
vamscli industry engineering plm plmxml import -d my-database --plmxml-dir /data/plm/export

# Import with XML upload for audit trails
vamscli industry engineering plm plmxml import -d my-database --plmxml-dir /data/plm/export --upload-xml

# High-throughput import
vamscli industry engineering plm plmxml import -d my-database --plmxml-dir /data/plm/export --max-workers 25

# Automated import with JSON output
vamscli industry engineering plm plmxml import -d my-database --plmxml-dir /data/plm/export --json-output > results.json
```

---

## industry spatial glbassetcombine

Combine multiple GLB files from an asset hierarchy into a single GLB file, applying transform data from the asset relationships.

```bash
vamscli industry spatial glbassetcombine [OPTIONS]
```

| Option                              | Type | Required | Description                                                                             |
| ----------------------------------- | ---- | -------- | --------------------------------------------------------------------------------------- |
| `-d`, `--database-id`               | TEXT | Yes      | Database ID containing the root asset                                                   |
| `-a`, `--asset-id`                  | TEXT | Yes      | Root asset ID to start the hierarchy from                                               |
| `--include-only-primary-type-files` | Flag | No       | Include only files with `primaryType` set (default: false)                              |
| `--no-file-metadata`                | Flag | No       | Exclude file metadata from the export (default: false)                                  |
| `--no-asset-metadata`               | Flag | No       | Exclude asset metadata from the export (default: false)                                 |
| `--fetch-entire-subtrees`           | Flag | No       | Fetch entire child relationship subtrees (default: true)                                |
| `--include-parent-relationships`    | Flag | No       | Include parent relationships in the relationship data (default: false)                  |
| `--local-path`                      | PATH | No       | Local path for temporary files (default: system temp)                                   |
| `--asset-create-name`               | TEXT | No       | Create a new asset with the combined GLB                                                |
| `--delete-temporary-files`          | Flag | No       | Delete temp files after upload (default: true; applies only with `--asset-create-name`) |
| `--json-output`                     | Flag | No       | Output raw JSON response                                                                |

The command exports the asset hierarchy (downloading `.glb` files), builds a complete transform tree containing a node for every asset, merges each asset's GLB meshes onto its node, and writes the combined GLB. With `--asset-create-name` it then creates a new asset and uploads the combined GLB and the export JSON.

:::note[At least one GLB required]
The hierarchy must contain at least one GLB file. Assets without GLB files still receive an empty transform node so the parent-child structure is preserved, but the command fails if no GLB is found anywhere in the hierarchy.
:::

### Transform priority

The transform for each relationship is resolved in this order:

1. **Matrix**: Uses the `Matrix` metadata when present. Accepted forms are a 2D array (row-major or column-major), a 1D array of 16 values, or a space-separated string of 16 values. A matrix whose last row is `[tx, ty, tz, 1]` is treated as row-major and transposed to the column-major glTF convention; otherwise it is used as-is.
2. **Components**: Builds a matrix from `Transform`/`Translation`, `Rotation`, and `Scale` metadata components. Missing components default to Translation `[0, 0, 0]`, Rotation `[0, 0, 0, 1]`, and Scale `[1, 1, 1]`.
3. **Identity**: Falls back to the identity matrix when no transform data is available. Malformed transform metadata logs a warning and also falls back to identity.

### Asset instancing

The same asset can appear multiple times in the hierarchy with different alias IDs. Each relationship instance creates a separate transform node named `AssetName__AliasID`, and each instance can carry its own transform matrix while reusing the same GLB mesh data. This is common for assemblies with repeated components such as bolts or screws.

```text
Engine Assembly (root, no GLB)
├── Bolt (alias 10, transform A)
├── Bolt (alias 20, transform B)
└── Bolt (alias 30, transform C)
```

```text
Scene
└── Engine_Assembly (transform node)
    ├── Bolt__10  →  bolt.glb
    ├── Bolt__20  →  bolt.glb
    └── Bolt__30  →  bolt.glb
```

### Output structure

:::info[Temporary directory layout]
Each run creates a unique timestamped subdirectory. File names use the sanitized root asset name (special characters replaced with underscores), not the asset ID, for readability.

```text
glbassetcombine_YYYYMMDD_HHMMSS/
  {sanitized-root-asset-name}_export.json     # Export data with hierarchy
  {sanitized-root-asset-name}__COMBINED.glb   # Final combined GLB
  {asset-id}/                                 # Downloaded GLB files
```

:::

### Examples

```bash
# Basic GLB combination
vamscli industry spatial glbassetcombine -d my-database -a root-asset-id

# Combine and create a new asset
vamscli industry spatial glbassetcombine -d my-database -a root-asset-id --asset-create-name "Combined Model"

# Custom temp directory
vamscli industry spatial glbassetcombine -d my-database -a root-asset-id --local-path ./temp

# Exclude metadata from the export
vamscli industry spatial glbassetcombine -d my-database -a root-asset-id --no-file-metadata --no-asset-metadata

# JSON output for automation
vamscli industry spatial glbassetcombine -d my-database -a root-asset-id --json-output
```

---

## Related Pages

-   [Asset Commands](assets.md)
-   [File Commands](files.md)
-   [Search Commands](search.md)
-   [Automation and Scripting](../automation.md)
