---
sidebar_label: Metadata
title: Metadata and Schema Commands
---

# Metadata and Schema Commands

Manage metadata for assets, files, asset links, and databases through a unified API, and inspect the metadata schema definitions that control validation rules.

---

## Unified Metadata API

All metadata operations share a consistent request format and bulk semantics across every entity type (asset, file, asset link, database).

-   **List** commands return the complete metadata set as a `metadata` array.
-   **Update** commands accept a `metadata` array wrapped in a JSON object and run as a bulk upsert or full replace.
-   **Delete** commands accept a `metadataKeys` array wrapped in a JSON object.

Update operations support two modes via `--update-type`:

-   `update` (default) -- Upsert mode. Creates or updates the provided metadata and keeps unlisted keys.
-   `replace_all` -- Replace mode. Deletes unlisted keys and upserts the provided metadata, with rollback on failure.

:::info[Supported value types]
Each metadata item declares a `metadataValueType`. Values are always supplied as strings in JSON input; `object` and `array` values are JSON encoded into a string.

| Type      | Description                      | Example value               |
| --------- | -------------------------------- | --------------------------- |
| `string`  | Text values                      | `"My Asset"`                |
| `number`  | Integers or floats               | `"42"`, `"3.14"`            |
| `boolean` | True/false                       | `"true"`, `"false"`         |
| `object`  | JSON object (stored as a string) | `"\{\"polygons\": 50000\}"` |
| `array`   | JSON array (stored as a string)  | `"[\"wood\", \"metal\"]"`   |

File attributes (`--type attribute`) support only the `string` value type.
:::

:::note[JSON input shape]
`--json-input` accepts either an inline JSON string or a file path prefixed with `@` (for example `@metadata.json`). Update input must contain a `metadata` array; delete input must contain a `metadataKeys` array. Both arrays must be non-empty.

```json
// update --json-input
{ "metadata": [ { "metadataKey": "title", "metadataValue": "My Asset", "metadataValueType": "string" } ] }

// delete --json-input
{ "metadataKeys": ["title", "priority"] }
```

:::

:::note[Automatic pagination]
The metadata `list` commands (`asset`, `file`, `asset-link`, `database`) return the complete metadata set. The API responds one page at a time; the CLI follows the response `NextToken` and aggregates all pages automatically. Supplying `--starting-token` fetches only that single page (manual pagination).
:::

---

## metadata asset list

List all metadata for an asset.

```bash
vamscli metadata asset list [OPTIONS]
```

| Option                | Type    | Required | Description                                 |
| --------------------- | ------- | -------- | ------------------------------------------- |
| `-d`, `--database-id` | TEXT    | Yes      | Database ID                                 |
| `-a`, `--asset-id`    | TEXT    | Yes      | Asset ID                                    |
| `--asset-version-id`  | TEXT    | No       | Filter metadata by a specific asset version |
| `--page-size`         | INTEGER | No       | Page size for pagination (default: 3000)    |
| `--starting-token`    | TEXT    | No       | Token for manual single-page pagination     |
| `--json-output`       | FLAG    | No       | Output raw JSON response                    |

```bash
vamscli metadata asset list -d my-db -a my-asset
vamscli metadata asset list -d my-db -a my-asset --json-output
vamscli metadata asset list -d my-db -a my-asset --asset-version-id ver-123
```

---

## metadata asset update

Create or update asset metadata (bulk operation).

```bash
vamscli metadata asset update [OPTIONS]
```

| Option                | Type   | Required | Description                                    |
| --------------------- | ------ | -------- | ---------------------------------------------- |
| `-d`, `--database-id` | TEXT   | Yes      | Database ID                                    |
| `-a`, `--asset-id`    | TEXT   | Yes      | Asset ID                                       |
| `--json-input`        | TEXT   | Yes      | JSON string or `@file` with a `metadata` array |
| `--update-type`       | CHOICE | No       | `update` (upsert, default) or `replace_all`    |
| `--json-output`       | FLAG   | No       | Output raw JSON response                       |

```bash
vamscli metadata asset update -d my-db -a my-asset --json-input '{"metadata":[
  {"metadataKey":"title","metadataValue":"My 3D Model","metadataValueType":"string"},
  {"metadataKey":"priority","metadataValue":"1","metadataValueType":"number"},
  {"metadataKey":"active","metadataValue":"true","metadataValueType":"boolean"}
]}'
vamscli metadata asset update -d my-db -a my-asset --json-input @metadata.json
vamscli metadata asset update -d my-db -a my-asset --update-type replace_all --json-input @metadata.json
```

---

## metadata asset delete

Delete specific metadata keys from an asset (bulk operation).

```bash
vamscli metadata asset delete [OPTIONS]
```

| Option                | Type | Required | Description                                        |
| --------------------- | ---- | -------- | -------------------------------------------------- |
| `-d`, `--database-id` | TEXT | Yes      | Database ID                                        |
| `-a`, `--asset-id`    | TEXT | Yes      | Asset ID                                           |
| `--json-input`        | TEXT | Yes      | JSON string or `@file` with a `metadataKeys` array |
| `--json-output`       | FLAG | No       | Output raw JSON response                           |

```bash
vamscli metadata asset delete -d my-db -a my-asset --json-input '{"metadataKeys":["title","priority"]}'
vamscli metadata asset delete -d my-db -a my-asset --json-input @keys-to-delete.json
```

---

## metadata file list

List metadata or attributes for a specific file within an asset.

```bash
vamscli metadata file list [OPTIONS]
```

| Option                | Type    | Required | Description                                 |
| --------------------- | ------- | -------- | ------------------------------------------- |
| `-d`, `--database-id` | TEXT    | Yes      | Database ID                                 |
| `-a`, `--asset-id`    | TEXT    | Yes      | Asset ID                                    |
| `--file-path`         | TEXT    | Yes      | Relative file path                          |
| `--type`              | CHOICE  | Yes      | `metadata` or `attribute`                   |
| `--asset-version-id`  | TEXT    | No       | Filter metadata by a specific asset version |
| `--page-size`         | INTEGER | No       | Page size for pagination (default: 3000)    |
| `--starting-token`    | TEXT    | No       | Token for manual single-page pagination     |
| `--json-output`       | FLAG    | No       | Output raw JSON response                    |

```bash
vamscli metadata file list -d my-db -a my-asset --file-path "models/file.gltf" --type metadata
vamscli metadata file list -d my-db -a my-asset --file-path "models/file.gltf" --type attribute --json-output
vamscli metadata file list -d my-db -a my-asset --file-path "models/file.gltf" --type metadata --asset-version-id ver-123
```

---

## metadata file update

Create or update file metadata or attributes (bulk operation).

```bash
vamscli metadata file update [OPTIONS]
```

| Option                | Type   | Required | Description                                    |
| --------------------- | ------ | -------- | ---------------------------------------------- |
| `-d`, `--database-id` | TEXT   | Yes      | Database ID                                    |
| `-a`, `--asset-id`    | TEXT   | Yes      | Asset ID                                       |
| `--file-path`         | TEXT   | Yes      | Relative file path                             |
| `--type`              | CHOICE | Yes      | `metadata` or `attribute`                      |
| `--json-input`        | TEXT   | Yes      | JSON string or `@file` with a `metadata` array |
| `--update-type`       | CHOICE | No       | `update` (upsert, default) or `replace_all`    |
| `--json-output`       | FLAG   | No       | Output raw JSON response                       |

```bash
vamscli metadata file update -d my-db -a my-asset --file-path "models/file.gltf" --type metadata --json-input @metadata.json
vamscli metadata file update -d my-db -a my-asset --file-path "models/file.gltf" --type attribute --update-type replace_all --json-input @attributes.json
```

:::note
File attributes (`--type attribute`) accept only the `string` value type. Use `--type metadata` for typed values such as `number`, `boolean`, `object`, or `array`.
:::

---

## metadata file delete

Delete metadata or attribute keys from a file (bulk operation).

```bash
vamscli metadata file delete [OPTIONS]
```

| Option                | Type   | Required | Description                                        |
| --------------------- | ------ | -------- | -------------------------------------------------- |
| `-d`, `--database-id` | TEXT   | Yes      | Database ID                                        |
| `-a`, `--asset-id`    | TEXT   | Yes      | Asset ID                                           |
| `--file-path`         | TEXT   | Yes      | Relative file path                                 |
| `--type`              | CHOICE | Yes      | `metadata` or `attribute`                          |
| `--json-input`        | TEXT   | Yes      | JSON string or `@file` with a `metadataKeys` array |
| `--json-output`       | FLAG   | No       | Output raw JSON response                           |

```bash
vamscli metadata file delete -d my-db -a my-asset --file-path "models/file.gltf" --type metadata --json-input '{"metadataKeys":["old_field"]}'
vamscli metadata file delete -d my-db -a my-asset --file-path "models/file.gltf" --type attribute --json-input '{"metadataKeys":["old_attr"]}'
```

---

## metadata asset-link list

List all metadata for an asset link.

```bash
vamscli metadata asset-link list [OPTIONS]
```

| Option             | Type    | Required | Description                              |
| ------------------ | ------- | -------- | ---------------------------------------- |
| `--asset-link-id`  | TEXT    | Yes      | Asset link ID                            |
| `--page-size`      | INTEGER | No       | Page size for pagination (default: 3000) |
| `--starting-token` | TEXT    | No       | Token for manual single-page pagination  |
| `--json-output`    | FLAG    | No       | Output raw JSON response                 |

```bash
vamscli metadata asset-link list --asset-link-id link-uuid
vamscli metadata asset-link list --asset-link-id link-uuid --json-output
```

---

## metadata asset-link update

Create or update asset link metadata (bulk operation).

```bash
vamscli metadata asset-link update [OPTIONS]
```

| Option            | Type   | Required | Description                                    |
| ----------------- | ------ | -------- | ---------------------------------------------- |
| `--asset-link-id` | TEXT   | Yes      | Asset link ID                                  |
| `--json-input`    | TEXT   | Yes      | JSON string or `@file` with a `metadata` array |
| `--update-type`   | CHOICE | No       | `update` (upsert, default) or `replace_all`    |
| `--json-output`   | FLAG   | No       | Output raw JSON response                       |

```bash
vamscli metadata asset-link update --asset-link-id link-uuid --json-input @metadata.json
vamscli metadata asset-link update --asset-link-id link-uuid --update-type replace_all --json-input @metadata.json
```

---

## metadata asset-link delete

Delete specific metadata keys from an asset link (bulk operation).

```bash
vamscli metadata asset-link delete [OPTIONS]
```

| Option            | Type | Required | Description                                        |
| ----------------- | ---- | -------- | -------------------------------------------------- |
| `--asset-link-id` | TEXT | Yes      | Asset link ID                                      |
| `--json-input`    | TEXT | Yes      | JSON string or `@file` with a `metadataKeys` array |
| `--json-output`   | FLAG | No       | Output raw JSON response                           |

```bash
vamscli metadata asset-link delete --asset-link-id link-uuid --json-input '{"metadataKeys":["old_field"]}'
```

---

## metadata database list

List all metadata for a database.

```bash
vamscli metadata database list [OPTIONS]
```

| Option                | Type    | Required | Description                              |
| --------------------- | ------- | -------- | ---------------------------------------- |
| `-d`, `--database-id` | TEXT    | Yes      | Database ID                              |
| `--page-size`         | INTEGER | No       | Page size for pagination (default: 3000) |
| `--starting-token`    | TEXT    | No       | Token for manual single-page pagination  |
| `--json-output`       | FLAG    | No       | Output raw JSON response                 |

```bash
vamscli metadata database list -d my-db
vamscli metadata database list -d my-db --json-output
```

---

## metadata database update

Create or update database metadata (bulk operation).

```bash
vamscli metadata database update [OPTIONS]
```

| Option                | Type   | Required | Description                                    |
| --------------------- | ------ | -------- | ---------------------------------------------- |
| `-d`, `--database-id` | TEXT   | Yes      | Database ID                                    |
| `--json-input`        | TEXT   | Yes      | JSON string or `@file` with a `metadata` array |
| `--update-type`       | CHOICE | No       | `update` (upsert, default) or `replace_all`    |
| `--json-output`       | FLAG   | No       | Output raw JSON response                       |

```bash
vamscli metadata database update -d my-db --json-input @metadata.json
vamscli metadata database update -d my-db --update-type replace_all --json-input @metadata.json
```

---

## metadata database delete

Delete specific metadata keys from a database (bulk operation).

```bash
vamscli metadata database delete [OPTIONS]
```

| Option                | Type | Required | Description                                        |
| --------------------- | ---- | -------- | -------------------------------------------------- |
| `-d`, `--database-id` | TEXT | Yes      | Database ID                                        |
| `--json-input`        | TEXT | Yes      | JSON string or `@file` with a `metadataKeys` array |
| `--json-output`       | FLAG | No       | Output raw JSON response                           |

```bash
vamscli metadata database delete -d my-db --json-input '{"metadataKeys":["old_project","deprecated_field"]}'
```

---

## metadata-schema list

List metadata schemas with optional filters. Metadata schemas define the structure and validation rules for metadata associated with each entity type.

```bash
vamscli metadata-schema list [OPTIONS]
```

| Option                | Type    | Required | Description                                                                                                                                                |
| --------------------- | ------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-d`, `--database-id` | TEXT    | No       | Filter by database ID                                                                                                                                      |
| `-e`, `--entity-type` | CHOICE  | No       | Filter: `databaseMetadata`, `assetMetadata`, `fileMetadata`, `fileAttribute`, `assetLinkMetadata`                                                          |
| `--page-size`         | INTEGER | No       | Number of items per page (default: 100)                                                                                                                    |
| `--max-items`         | INTEGER | No       | Maximum total items to fetch (default: 1000)                                                                                                               |
| `--starting-token`    | TEXT    | No       | Token for pagination                                                                                                                                       |
| `--json-input`        | TEXT    | No       | JSON string or file with parameters (`databaseId`, `metadataEntityType`, `maxItems`, `pageSize`, `startingToken`); overrides matching command-line options |
| `--json-output`       | FLAG    | No       | Output raw JSON response                                                                                                                                   |

```bash
vamscli metadata-schema list
vamscli metadata-schema list -d my-database -e assetMetadata
vamscli metadata-schema list --page-size 50 --max-items 200
vamscli metadata-schema list --json-input '{"databaseId":"my-db","metadataEntityType":"assetMetadata","maxItems":100}'
```

:::info[Entity types]
| Entity type | Applies to |
| ------------------- | ------------------------------------- |
| `databaseMetadata` | Databases |
| `assetMetadata` | Assets |
| `fileMetadata` | Files (typed metadata) |
| `fileAttribute` | File attributes (string-only) |
| `assetLinkMetadata` | Asset links |

Entity type values are case-insensitive.
:::

---

## metadata-schema get

Get a specific metadata schema by ID, including field definitions, data types, requirements, dependencies, and controlled list values.

```bash
vamscli metadata-schema get [OPTIONS]
```

| Option                | Type | Required | Description              |
| --------------------- | ---- | -------- | ------------------------ |
| `-d`, `--database-id` | TEXT | Yes      | Database ID              |
| `-s`, `--schema-id`   | TEXT | Yes      | Metadata schema ID       |
| `--json-output`       | FLAG | No       | Output raw JSON response |

```bash
vamscli metadata-schema get -d my-database -s schema-abc123
vamscli metadata-schema get -d my-database -s schema-abc123 --json-output
```

The output reports the schema name, entity type, enabled status, file restrictions, timestamps, and each field's name, type, required flag, default value, dependencies, and allowed (controlled list) values.

:::info[Schema field data types]
| Data type | Description |
| ------------------------ | ---------------------------------------------------- |
| `string` | Text values |
| `number` | Integers or floats |
| `boolean` | True/false values |
| `array` | List of values |
| `object` | Nested JSON object |
| `inline_controlled_list` | Value must be one of the field's `controlledListKeys` |
:::

---

## Workflow Examples

### Asset metadata lifecycle

```bash
# Create initial metadata (upsert mode)
vamscli metadata asset update -d my-db -a my-asset --json-input '{"metadata":[
  {"metadataKey":"title","metadataValue":"3D Building Model","metadataValueType":"string"},
  {"metadataKey":"priority","metadataValue":"1","metadataValueType":"number"},
  {"metadataKey":"properties","metadataValue":"{\"polygons\": 75000}","metadataValueType":"object"}
]}'

# List metadata
vamscli metadata asset list -d my-db -a my-asset

# Replace all metadata atomically
vamscli metadata asset update -d my-db -a my-asset --update-type replace_all --json-input '{"metadata":[
  {"metadataKey":"title","metadataValue":"New Asset","metadataValueType":"string"}
]}'

# Delete specific keys
vamscli metadata asset delete -d my-db -a my-asset --json-input '{"metadataKeys":["old_field"]}'
```

### Scripting with JSON output

```bash
# Apply metadata to every asset in a database
for asset in $(vamscli assets list -d my-db --json-output | jq -r '.assets[].assetId'); do
  vamscli metadata asset update -d my-db -a "$asset" --json-input '{"metadata":[
    {"metadataKey":"processed","metadataValue":"true","metadataValueType":"boolean"}
  ]}'
done

# Inspect a schema's field names
vamscli metadata-schema get -d my-db -s schema-abc123 --json-output | jq '.fields.fields[].metadataFieldKeyName'
```

## Related Pages

-   [Asset Commands](assets.md)
-   [File Commands](files.md)
-   [Database Commands](database.md)
