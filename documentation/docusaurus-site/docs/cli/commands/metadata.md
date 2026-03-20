---
sidebar_label: Metadata
title: Metadata and Schema Commands
---

# Metadata and Schema Commands

Manage metadata for assets, files, asset links, and databases through a unified API. Also view metadata schema definitions that control validation rules.

---

## Unified Metadata API

All metadata operations use a consistent format with bulk support and two update modes:

- **update** (default): Upsert mode -- creates or updates provided metadata, keeps unlisted keys.
- **replace_all**: Replace mode -- deletes unlisted keys, upserts provided metadata (with rollback on failure).

### Supported value types

| Type | Description | Example |
|---|---|---|
| `string` | Text values | `"My Asset"` |
| `number` | Numeric values | `42`, `3.14` |
| `boolean` | True/false | `"true"`, `"false"` |
| `object` | JSON objects (stored as string) | `"\{\"key\": \"value\"\}"` |
| `array` | JSON arrays (stored as string) | `"[\"a\", \"b\"]"` |

---

## metadata asset list

List all metadata for an asset.

```bash
vamscli metadata asset list -d <DB> -a <ASSET> [--asset-version-id <VER>] [--json-output]
```

---

## metadata asset update

Create or update asset metadata (bulk operation).

```bash
vamscli metadata asset update -d <DB> -a <ASSET> --json-input <JSON> [--update-type update|replace_all] [--json-output]
```

### JSON input format

```json
[
    {"metadataKey": "title", "metadataValue": "My 3D Model", "metadataValueType": "string"},
    {"metadataKey": "priority", "metadataValue": "1", "metadataValueType": "number"},
    {"metadataKey": "active", "metadataValue": "true", "metadataValueType": "boolean"}
]
```

```bash
vamscli metadata asset update -d my-db -a my-asset --json-input @metadata.json
vamscli metadata asset update -d my-db -a my-asset --update-type replace_all --json-input @metadata.json
```

---

## metadata asset delete

Delete specific metadata keys from an asset.

```bash
vamscli metadata asset delete -d <DB> -a <ASSET> --json-input '["title", "priority"]'
```

---

## metadata file list

List metadata or attributes for a specific file.

```bash
vamscli metadata file list -d <DB> -a <ASSET> --file-path "models/file.gltf" --type metadata [--asset-version-id <VER>] [--json-output]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `-d`, `--database-id` | TEXT | Yes | Database ID |
| `-a`, `--asset-id` | TEXT | Yes | Asset ID |
| `--file-path` | TEXT | Yes | Relative file path |
| `--type` | CHOICE | Yes | `metadata` or `attribute` |

---

## metadata file update

Create or update file metadata (bulk operation).

```bash
vamscli metadata file update -d <DB> -a <ASSET> -f <FILE_ID> --json-input <JSON> [--update-type update|replace_all]
```

---

## metadata file delete

Delete metadata keys from a file.

```bash
vamscli metadata file delete -d <DB> -a <ASSET> -f <FILE_ID> --json-input '["old_field"]'
```

---

## metadata asset-link list

List all metadata for an asset link.

```bash
vamscli metadata asset-link list --asset-link-id <UUID> [--json-output]
```

---

## metadata asset-link update

Create or update asset link metadata.

```bash
vamscli metadata asset-link update --asset-link-id <UUID> --json-input <JSON> [--update-type update|replace_all]
```

---

## metadata asset-link delete

Delete metadata keys from an asset link.

```bash
vamscli metadata asset-link delete --asset-link-id <UUID> --json-input '["old_field"]'
```

---

## metadata database list

List all metadata for a database.

```bash
vamscli metadata database list -d <DB> [--json-output]
```

---

## metadata database update

Create or update database metadata.

```bash
vamscli metadata database update -d <DB> --json-input <JSON> [--update-type update|replace_all]
```

---

## metadata database delete

Delete metadata keys from a database.

```bash
vamscli metadata database delete -d <DB> --json-input '["old_project"]'
```

---

## metadata-schema list

List metadata schemas with optional filters.

```bash
vamscli metadata-schema list [OPTIONS]
```

| Option | Type | Required | Description |
|---|---|---|---|
| `-d`, `--database-id` | TEXT | No | Filter by database ID |
| `-e`, `--entity-type` | CHOICE | No | Filter: `databaseMetadata`, `assetMetadata`, `fileMetadata`, `fileAttribute`, `assetLinkMetadata` |
| `--page-size` | INTEGER | No | Items per page |
| `--max-items` | INTEGER | No | Maximum items (default: 1000) |
| `--starting-token` | TEXT | No | Pagination token |
| `--json-output` | Flag | No | Output raw JSON |

```bash
vamscli metadata-schema list
vamscli metadata-schema list -d my-database -e assetMetadata
```

---

## metadata-schema get

Get a specific metadata schema by ID.

```bash
vamscli metadata-schema get -d <DB> -s <SCHEMA_ID> [--json-output]
```

Output includes schema name, entity type, enabled status, field definitions with types, default values, dependencies, and controlled list values.

---

## Workflow Examples

### Asset metadata lifecycle

```bash
# Create initial metadata
vamscli metadata asset update -d my-db -a my-asset --json-input '[
  {"metadataKey": "title", "metadataValue": "3D Building Model", "metadataValueType": "string"},
  {"metadataKey": "priority", "metadataValue": "1", "metadataValueType": "number"}
]'

# List metadata
vamscli metadata asset list -d my-db -a my-asset

# Replace all metadata atomically
vamscli metadata asset update -d my-db -a my-asset --update-type replace_all --json-input '[
  {"metadataKey": "title", "metadataValue": "New Asset", "metadataValueType": "string"}
]'

# Delete specific keys
vamscli metadata asset delete -d my-db -a my-asset --json-input '["old_field"]'
```

## Related Pages

- [Asset Commands](assets.md)
- [File Commands](files.md)
- [Database Commands](database.md)
