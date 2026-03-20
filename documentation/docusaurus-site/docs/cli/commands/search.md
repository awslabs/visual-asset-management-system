---
sidebar_label: Search
title: Search Commands
---

# Search Commands

Search assets and files using the dual-index Amazon OpenSearch Service system.

:::note[Prerequisite]
Search requires Amazon OpenSearch Service to be enabled in your VAMS deployment. If the `NOOPENSEARCH` feature switch is enabled, search commands are unavailable. Use `vamscli assets list` as an alternative.
:::

---

## search assets

Search across all assets with advanced filtering, metadata search, and sorting.

```bash
vamscli search assets [OPTIONS]
```

| Option | Type | Description |
|---|---|---|
| `-q`, `--query` | TEXT | General text search query |
| `--filters` | TEXT | Advanced filters (query string or JSON format) |
| `--metadata-query` | TEXT | Metadata search query (`field:value` format) |
| `--metadata-mode` | CHOICE | Search mode: `key`, `value`, or `both` (default) |
| `--include-metadata` / `--no-metadata` | Flag | Include metadata in general search |
| `--explain-results` | Flag | Include match explanations |
| `--sort-field` | TEXT | Field to sort by |
| `--sort-desc` / `--sort-asc` | Flag | Sort direction |
| `--from` | INTEGER | Pagination start offset |
| `--size` | INTEGER | Results per page (max 2000) |
| `--include-archived` | Flag | Include archived assets |
| `--output-format` | CHOICE | `table`, `json`, or `csv` |
| `--jsonOutput` | Flag | Raw API response as JSON |

### Filter syntax

Filters support two formats:

**Query string format (recommended):**

```bash
--filters 'str_databaseid:"my-db"'
--filters 'str_databaseid:"my-db" AND str_assettype:"3d-model"'
--filters 'list_tags:("training" OR "simulation")'
--filters 'str_assetname:model*'
```

**JSON format (for advanced OpenSearch queries):**

```bash
--filters '[{"term": {"str_assettype": "3d-model"}}]'
--filters '[{"range": {"num_version": {"gte": 1, "lte": 5}}}]'
```

### Metadata search

```bash
vamscli search assets --metadata-query "MD_str_product:Training"
vamscli search assets --metadata-query "MD_str_product:Train*"
vamscli search assets --metadata-query "MD_str_product:Training AND MD_num_version:1"
vamscli search assets -q "model" --metadata-query "MD_str_category:Training"
```

### Examples

```bash
vamscli search assets -q "training model"
vamscli search assets --filters 'str_databaseid:"my-db" AND str_assettype:"3d-model"'
vamscli search assets -q "model" --output-format csv > results.csv
vamscli search assets -q "model" --explain-results --sort-field "str_assetname" --sort-asc
```

---

## search files

Search across all asset files with file-specific filtering.

```bash
vamscli search files [OPTIONS]
```

Supports all the same options as `search assets`. Common file-specific filters:

```bash
vamscli search files --filters 'str_fileext:"gltf"'
vamscli search files --filters 'str_fileext:"png" AND str_databaseid:"my-database"'
vamscli search files --filters '[{"range": {"num_filesize": {"lte": 1048576}}}]'
vamscli search files --metadata-query "MD_str_format:GLTF2.0"
```

---

## search simple

Simplified search interface with user-friendly parameters.

```bash
vamscli search simple [OPTIONS]
```

| Option | Type | Description |
|---|---|---|
| `-q`, `--query` | TEXT | General keyword search |
| `--asset-name` | TEXT | Search by asset name |
| `--asset-id` | TEXT | Search by asset ID |
| `--asset-type` | TEXT | Filter by asset type |
| `--file-key` | TEXT | Search by file key |
| `--file-ext` | TEXT | Filter by file extension |
| `-d`, `--database` | TEXT | Filter by database ID |
| `--tags` | TEXT | Filter by tags (comma-separated) |
| `--metadata-key` | TEXT | Search metadata field names |
| `--metadata-value` | TEXT | Search metadata field values |
| `--entity-types` | TEXT | `asset`, `file`, or `asset,file` (default) |
| `--include-archived` | Flag | Include archived items |
| `--from` | INTEGER | Pagination offset |
| `--size` | INTEGER | Results per page (max 1000) |
| `--output-format` | CHOICE | `table`, `json`, or `csv` |

```bash
vamscli search simple -q "training" --entity-types asset
vamscli search simple --file-ext "gltf" --entity-types file
vamscli search simple --metadata-key "product" --metadata-value "Training"
vamscli search simple -q "model" -d my-database
```

---

## search mapping

Retrieve the Amazon OpenSearch Service index mapping showing all available search fields for both indexes.

```bash
vamscli search mapping [--output-format table|json|csv] [--jsonOutput]
```

Use this to discover available field names and types for building filter queries.

---

## Search Field Reference

| Prefix | Type | Example Fields |
|---|---|---|
| `str_*` | String | `str_assetname`, `str_databaseid`, `str_fileext`, `str_key` |
| `num_*` | Numeric | `num_filesize` |
| `date_*` | Date | `date_lastmodified` |
| `bool_*` | Boolean | `bool_isdistributable`, `bool_archived` |
| `list_*` | List | `list_tags` |
| `MD_*` | Metadata | `MD_str_product`, `MD_num_version` |

## Related Pages

- [Asset Commands](assets.md)
- [File Commands](files.md)
- [Metadata Commands](metadata.md)
