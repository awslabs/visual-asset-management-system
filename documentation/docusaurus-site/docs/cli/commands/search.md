---
sidebar_label: Search
title: Search Commands
---

# Search Commands

Search assets and files using the dual-index Amazon OpenSearch Service system. VAMS maintains separate indexes for assets and files, allowing optimized queries and precise results per entity type.

:::note[Prerequisite]
Search requires Amazon OpenSearch Service to be enabled in your VAMS deployment. If the `NOOPENSEARCH` feature switch is enabled, search commands are unavailable and exit with a "Search Disabled" error. Use `vamscli assets list` as an alternative.
:::

---

## search assets

Search the asset index with general text search, advanced filters, metadata search, sorting, geospatial filtering, and pagination.

```bash
vamscli search assets [OPTIONS]
```

| Option                                 | Type    | Required | Description                                                                |
| -------------------------------------- | ------- | -------- | -------------------------------------------------------------------------- |
| `-q`, `--query`                        | TEXT    | No       | General text search query                                                  |
| `--metadata-query`                     | TEXT    | No       | Metadata search query in `field:value` format; supports `AND`/`OR`         |
| `--metadata-mode`                      | CHOICE  | No       | Metadata search scope: `key`, `value`, or `both` (default `both`)          |
| `--include-metadata` / `--no-metadata` | Flag    | No       | Include metadata fields in the general search (default: include)           |
| `--explain-results`                    | Flag    | No       | Include match explanations in results                                      |
| `--sort-field`                         | TEXT    | No       | Field to sort by (for example, `str_assetname`); omit to sort by `_score`  |
| `--sort-desc`                          | Flag    | No       | Sort descending (default is ascending when `--sort-field` is set)          |
| `--from`                               | INTEGER | No       | Pagination start offset (default: 0)                                       |
| `--size`                               | INTEGER | No       | Results per page (default: 100, max: 2000)                                 |
| `--filters`                            | TEXT    | No       | Advanced filters in query string or JSON array format                      |
| `--include-archived`                   | Flag    | No       | Include archived assets                                                    |
| `--geo-point`                          | TEXT    | No       | Geo point filter: `lat,lon` or `lat,lon,radiusMeters`                      |
| `--geo-bbox`                           | TEXT    | No       | Geo bounding box: `topLeftLat,topLeftLon,bottomRightLat,bottomRightLon`    |
| `--geo-geojson`                        | PATH    | No       | Path to a GeoJSON file (Geometry, Feature, or FeatureCollection)           |
| `--geo-relation`                       | CHOICE  | No       | Spatial relation: `intersects` (default), `within`, `contains`, `disjoint` |
| `--output-format`                      | CHOICE  | No       | `table` (default), `json`, or `csv`                                        |
| `--json-output`                        | Flag    | No       | Output the raw API response as JSON                                        |

:::note
`--output-format json` and `--json-output` are equivalent: both emit the raw API response. Use `--output-format csv` to write comma-separated rows to standard output, suitable for redirecting to a file.
:::

### Filter syntax

The `--filters` option accepts two formats. A query string is converted to an OpenSearch `query_string` clause; a JSON array is passed through as OpenSearch filter clauses.

**Query string format (recommended):**

```bash
--filters 'str_databaseid:"my-db"'
--filters 'str_databaseid:"my-db" AND str_assettype:"3d-model"'
--filters 'list_tags:("training" OR "simulation")'
--filters 'str_assetname:model*'
--filters '(str_assettype:"3d-model" OR str_assettype:"texture") AND str_databaseid:"my-db"'
```

**JSON array format (for advanced OpenSearch clauses):**

```bash
--filters '[{"term": {"str_assettype": "3d-model"}}]'
--filters '[{"range": {"num_version": {"gte": 1, "lte": 5}}}]'
--filters '[{"term": {"str_assettype": "3d-model"}}, {"range": {"num_version": {"gte": 1}}}]'
```

### Metadata search

`--metadata-query` searches indexed metadata fields. The query supports `AND`/`OR` operators within the metadata group, and the group as a whole is combined with `--query` and `--filters` using `AND` logic. `--metadata-mode` controls whether the query matches metadata field names (`key`), field values (`value`), or both (`both`, the default).

```bash
# Exact field:value match
vamscli search assets --metadata-query "MD_str_product:Training"

# Wildcard in the metadata value
vamscli search assets --metadata-query "MD_str_product:Train*"

# Multiple metadata conditions (both must match)
vamscli search assets --metadata-query "MD_str_product:Training AND MD_num_version:1"

# Multiple metadata conditions (either may match)
vamscli search assets --metadata-query "MD_str_color:red OR MD_str_color:blue"

# Match metadata field names only
vamscli search assets --metadata-query "product" --metadata-mode key

# Match metadata field values only
vamscli search assets --metadata-query "Training" --metadata-mode value

# Combine text query with metadata search (both must match)
vamscli search assets -q "model" --metadata-query "MD_str_category:Training"

# Exclude metadata from the general text search
vamscli search assets -q "model" --no-metadata
```

### Geospatial filtering

The `--geo-*` options filter results by the derived `geo_MD_location` field on each indexed document. The indexer populates that field from a `location` metadata key (GeoJSON or a `{latitude, longitude, altitude}` payload) or from individual `latitude` / `longitude` / `altitude` metadata fields. Provide exactly one of `--geo-point`, `--geo-bbox`, or `--geo-geojson`; supplying more than one is rejected as invalid parameters.

| Option           | Payload shape sent to the API                                                           |
| ---------------- | --------------------------------------------------------------------------------------- |
| `--geo-point`    | `{ point: { lat, lon[, radiusMeters] } }`                                               |
| `--geo-bbox`     | `{ bbox: { topLeft: { lat, lon }, bottomRight: { lat, lon } } }`                        |
| `--geo-geojson`  | `{ geoJson: <parsed file contents> }`                                                   |
| `--geo-relation` | Adds `relation` to the payload (`intersects` default, `within`, `contains`, `disjoint`) |

```bash
# Within 5 km of Seattle (point + radius)
vamscli search assets --geo-point "47.6062,-122.3321,5000"

# Inside a bounding box, requiring full containment
vamscli search assets --geo-bbox "47.7,-122.5,47.5,-122.2" --geo-relation within

# Inside an arbitrary GeoJSON polygon stored on disk
vamscli search assets --geo-geojson ./aoi.geojson

# Combined with text and metadata filters
vamscli search assets -q "tower" --metadata-query "MD_str_status:active" --geo-point "47.6,-122.3,2000"
```

### Examples

```bash
vamscli search assets -q "training model"
vamscli search assets --filters 'str_databaseid:"my-db" AND str_assettype:"3d-model"'
vamscli search assets -q "model" --sort-field "str_assetname" --sort-desc
vamscli search assets -q "model" --from 20 --size 50
vamscli search assets -q "model" --explain-results --json-output
vamscli search assets -q "model" --output-format csv > results.csv
```

---

## search files

Search the file index with file-specific filtering, metadata search, sorting, geospatial filtering, and pagination. Accepts the same options as `search assets`, including all `--geo-*` flags and `--metadata-query` / `--metadata-mode`.

```bash
vamscli search files [OPTIONS]
```

The options table is identical to [`search assets`](#search-assets), with two differences: `--include-archived` includes archived files, and `--sort-field` typically targets file fields such as `str_key` or `str_fileext`. The `--size` maximum is 2000.

### File filter examples

```bash
vamscli search files --filters 'str_fileext:"gltf"'
vamscli search files --filters 'str_fileext:"png" AND str_databaseid:"my-database"'
vamscli search files --filters 'str_key:*texture* AND str_fileext:"png"'
vamscli search files --filters 'list_tags:("ui" OR "interface")'

# File size (bytes) requires the JSON array format
vamscli search files --filters '[{"range": {"num_filesize": {"lte": 1048576}}}]'
vamscli search files --filters '[{"range": {"num_filesize": {"gte": 1048576, "lte": 10485760}}}]'

# Metadata and geospatial
vamscli search files --metadata-query "MD_str_format:GLTF2.0"
vamscli search files --geo-bbox "47.7,-122.5,47.5,-122.2" --geo-relation within

# Text query and output formats
vamscli search files -q "texture" --sort-field "str_key"
vamscli search files -q "texture" --output-format csv > files.csv
```

---

## search simple

Simplified search interface with user-friendly parameters. Resolves to a single combined request across the asset and file indexes and is easier to use than `search assets` / `search files` for most needs.

```bash
vamscli search simple [OPTIONS]
```

| Option               | Type    | Required | Description                                                                |
| -------------------- | ------- | -------- | -------------------------------------------------------------------------- |
| `-q`, `--query`      | TEXT    | No       | General keyword search                                                     |
| `--asset-name`       | TEXT    | No       | Search by asset name                                                       |
| `--asset-id`         | TEXT    | No       | Search by asset ID                                                         |
| `--asset-type`       | TEXT    | No       | Filter by asset type                                                       |
| `--file-key`         | TEXT    | No       | Search by file key                                                         |
| `--file-ext`         | TEXT    | No       | Filter by file extension                                                   |
| `-d`, `--database`   | TEXT    | No       | Filter by database ID                                                      |
| `--tags`             | TEXT    | No       | Filter by tags (comma-separated)                                           |
| `--metadata-key`     | TEXT    | No       | Search metadata field names                                                |
| `--metadata-value`   | TEXT    | No       | Search metadata field values                                               |
| `--entity-types`     | TEXT    | No       | Entity types to search: `asset`, `file`, or `asset,file` (comma-separated) |
| `--include-archived` | Flag    | No       | Include archived items                                                     |
| `--geo-point`        | TEXT    | No       | Geo point filter: `lat,lon` or `lat,lon,radiusMeters`                      |
| `--geo-bbox`         | TEXT    | No       | Geo bounding box: `topLeftLat,topLeftLon,bottomRightLat,bottomRightLon`    |
| `--geo-geojson`      | PATH    | No       | Path to a GeoJSON file (Geometry, Feature, or FeatureCollection)           |
| `--geo-relation`     | CHOICE  | No       | Spatial relation: `intersects` (default), `within`, `contains`, `disjoint` |
| `--from`             | INTEGER | No       | Pagination start offset (default: 0)                                       |
| `--size`             | INTEGER | No       | Results per page (default: 100, max: 1000)                                 |
| `--output-format`    | CHOICE  | No       | `table` (default), `json`, or `csv`                                        |
| `--json-output`      | Flag    | No       | Output the raw API response as JSON                                        |

:::note
`--entity-types` accepts only `asset` and `file`; any other value is rejected as invalid parameters. When omitted, both indexes are searched.
:::

```bash
vamscli search simple -q "training"
vamscli search simple --asset-name "model" --entity-types asset
vamscli search simple --asset-type "3d-model" --entity-types asset
vamscli search simple --file-key "texture" --file-ext "png" --entity-types file
vamscli search simple --metadata-key "product" --metadata-value "Training"
vamscli search simple -d my-database --tags "simulation,training"
vamscli search simple --geo-point "47.6062,-122.3321,5000"
vamscli search simple -q "model" --output-format csv > results.csv
```

---

## search mapping

Retrieve the Amazon OpenSearch Service index mapping, listing the available fields and types for the asset and file indexes. Use this to discover field names for building `--filters` and sort queries.

```bash
vamscli search mapping [OPTIONS]
```

| Option            | Type   | Required | Description                             |
| ----------------- | ------ | -------- | --------------------------------------- |
| `--output-format` | CHOICE | No       | `table` (default), `json`, or `csv`     |
| `--json-output`   | Flag   | No       | Output the raw mapping response as JSON |

In `table` and `csv` modes the output lists each index, field name, and field type.

```bash
vamscli search mapping
vamscli search mapping --output-format csv > fields.csv
vamscli search mapping --json-output
```

---

## Output formats

All search commands support three output formats via `--output-format` (default `table`):

-   **table** — Aligned columns derived from the `_source` fields of each hit, prefixed with the total result count. The mapping command renders `Index | Field | Type` rows.
-   **json** — The raw API response. Equivalent to passing `--json-output`.
-   **csv** — Comma-separated rows written directly to standard output, with list values joined by semicolons. Suitable for redirecting to a file.

---

## Search field reference

Field names follow type-prefixed conventions. Use `search mapping` to enumerate the fields available in your deployment.

| Prefix            | Type      | Example fields                                                                                                 |
| ----------------- | --------- | -------------------------------------------------------------------------------------------------------------- |
| `str_*`           | String    | `str_assetname`, `str_description`, `str_databaseid`, `str_assettype`, `str_assetid`, `str_key`, `str_fileext` |
| `num_*`           | Numeric   | `num_version`, `num_filesize`                                                                                  |
| `date_*`          | Date      | `date_lastmodified`                                                                                            |
| `bool_*`          | Boolean   | `bool_isdistributable`, `bool_archived`                                                                        |
| `list_*`          | List      | `list_tags`                                                                                                    |
| `MD_*`            | Metadata  | `MD_str_<name>`, `MD_num_<name>`, `MD_date_<name>`, `MD_bool_<name>`                                           |
| `geo_MD_location` | geo_shape | Derived from `location` or `latitude` / `longitude` / `altitude` metadata                                      |

Metadata stored as `{"product": "Training"}` is indexed as `MD_str_product`. Asset-only fields such as `str_description` and `bool_isdistributable` live in the asset index; file-only fields such as `str_key`, `str_fileext`, and `num_filesize` live in the file index.

## Related Pages

-   [Asset Commands](assets.md)
-   [File Commands](files.md)
-   [Metadata Commands](metadata.md)
