---
sidebar_label: Industry
title: Industry Commands
---

# Industry Commands

Industry-specific commands for BOM assembly, PLM XML import, and spatial GLB combination.

---

## BOM Assembly

Assemble GLB geometry from a Bill of Materials (BOM) JSON hierarchy. Parses a BOM file, downloads GLB files from VAMS assets, applies transform matrices, and combines them into assembled geometries.

### bomassemble

```bash
vamscli industry engineering bom bomassemble [OPTIONS]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `--json-file`, `-j` | TEXT | Yes | Path to BOM JSON file |
| `-d`, `--database-id` | TEXT | Yes | Database ID containing the assets |
| `--local-path` | PATH | No | Local path for temporary files (default: system temp) |
| `--keep-temp-files` | Flag | No | Keep temporary files after processing |
| `--asset-create-name` | TEXT | No | Create a new asset with this name and upload results |
| `--delete-temporary-files` | Flag | No | Delete temp files after upload (default: true, only with `--asset-create-name`) |
| `--json-output` | Flag | No | Output raw JSON response |

### BOM JSON format

```json
{
    "sources": [
        {"source": "component_name_1", "storage": "VAMS"},
        {"source": "assembly_root", "storage": "no"}
    ],
    "scene": {
        "nodes": [
            {"node": "1", "source": "assembly_root"},
            {"node": "2", "source": "component_name_1", "parent_node": "1",
             "matrix": [1, 0, 0, 0.5, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}
        ]
    }
}
```

- **sources**: Component definitions. `"VAMS"` for stored assets, `"no"` for virtual assemblies.
- **scene.nodes**: Hierarchy with node IDs, parent references, and optional 4x4 transform matrices (16 floats, column-major order).

### Examples

```bash
# Basic assembly
vamscli industry engineering bom bomassemble --json-file assembly.json -d my-database

# Assembly with new asset creation
vamscli industry engineering bom bomassemble --json-file assembly.json -d my-database --asset-create-name "Complete Assembly"

# Keep temp files for debugging
vamscli industry engineering bom bomassemble --json-file assembly.json -d my-database --keep-temp-files
```

---

## PLM XML Import

Import Product Lifecycle Management data from PLM XML files into VAMS with parallel processing.

### plmxml import

```bash
vamscli industry engineering plm plmxml import [OPTIONS]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `-d`, `--database-id` | TEXT | Yes | Target database ID |
| `--plmxml-dir` | PATH | Yes | Directory containing PLM XML files |
| `--max-workers` | INTEGER | No | Maximum parallel workers (default: 15) |
| `--upload-xml` | Flag | No | Upload source XML files to root assets |
| `--json-output` | Flag | No | Output raw JSON response |

### Import process

The import runs in four phases:

1. **XML Parsing**: Parse all XML files, extract components and relationships.
2. **Asset Creation**: Create VAMS assets in parallel, skip duplicates.
3. **Parallel Operations**: Upload geometry files, create metadata, create asset links concurrently.
4. **Link Metadata**: Store transform matrices and UserData fields on links.

### Examples

```bash
# Basic import
vamscli industry engineering plm plmxml import -d my-database --plmxml-dir /data/plm/export

# Import with XML upload for audit trails
vamscli industry engineering plm plmxml import -d my-database --plmxml-dir /data/plm/export --upload-xml

# High-performance import
vamscli industry engineering plm plmxml import -d my-database --plmxml-dir /data/plm/export --max-workers 25

# Automated import with JSON output
vamscli industry engineering plm plmxml import -d my-database --plmxml-dir /data/plm/export --json-output > results.json
```

:::tip[Worker Count Guidelines]
- **Low (5-10)**: Conservative, for limited resources
- **Medium (15-20)**: Balanced (default: 15)
- **High (25-30)**: Maximum throughput, requires adequate resources
:::

---

## Spatial GLB Combine

Combine multiple GLB files from an asset hierarchy into a single GLB file, applying transform data from asset relationships.

### glbassetcombine

```bash
vamscli industry spatial glbassetcombine [OPTIONS]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `-d`, `--database-id` | TEXT | Yes | Database ID |
| `-a`, `--asset-id` | TEXT | Yes | Root asset ID |
| `--include-only-primary-type-files` | Flag | No | Include only files with primaryType set |
| `--no-file-metadata` | Flag | No | Exclude file metadata from export |
| `--no-asset-metadata` | Flag | No | Exclude asset metadata |
| `--fetch-entire-subtrees` | Flag | No | Fetch entire subtrees (default: true) |
| `--include-parent-relationships` | Flag | No | Include parent relationships |
| `--local-path` | PATH | No | Local path for temporary files |
| `--asset-create-name` | TEXT | No | Create new asset with combined GLB |
| `--delete-temporary-files` | Flag | No | Delete temp files after upload |
| `--json-output` | Flag | No | Output raw JSON |

### Transform priority

1. **Matrix**: Uses `Matrix` metadata (2D array, 1D array, or space-separated string)
2. **Components**: Builds from `Transform`/`Translation`, `Rotation`, `Scale` metadata
3. **Identity**: Defaults to identity matrix if no transform data is available

### Asset instancing

The command supports asset instancing where the same asset appears multiple times with different alias IDs and transforms. Each relationship instance creates a separate transform node named `AssetName__AliasID`.

### Examples

```bash
# Basic GLB combination
vamscli industry spatial glbassetcombine -d my-database -a root-asset-id

# Combine and create new asset
vamscli industry spatial glbassetcombine -d my-database -a root-asset-id --asset-create-name "Combined Model"

# JSON output for automation
vamscli industry spatial glbassetcombine -d my-database -a root-asset-id --json-output
```

### Output structure

```
glbassetcombine_YYYYMMDD_HHMMSS/
  {root-asset-name}_export.json        # Export data with hierarchy
  {root-asset-name}__COMBINED.glb      # Final combined GLB
  {asset-id}/                          # Downloaded GLB files
```

---

## Related Pages

- [Asset Commands](assets.md)
- [File Commands](files.md)
- [Automation and Scripting](../automation.md)
