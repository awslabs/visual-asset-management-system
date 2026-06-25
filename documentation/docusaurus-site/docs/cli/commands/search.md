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

| Option                                 | Type    | Description                                                                |
| -------------------------------------- | ------- | -------------------------------------------------------------------------- |
| `-q`, `--query`                        | TEXT    | General text search query (AND with filters)                               |
| `--filters`                            | TEXT    | Advanced filters (query string or JSON format)                             |
| `--metadata-query`                     | TEXT    | Metadata search query (AND with query/filters; supports AND/OR within)     |
| `--metadata-mode`                      | CHOICE  | Search mode: `key`, `value`, or `both` (default)                           |
| `--include-metadata` / `--no-metadata` | Flag    | Include metadata in general search                                         |
| `--explain-results`                    | Flag    | Include match explanations                                                 |
| `--sort-field`                         | TEXT    | Field to sort by                                                           |
| `--sort-desc` / `--sort-asc`           | Flag    | Sort direction                                                             |
| `--from`                               | INTEGER | Pagination start offset                                                    |
| `--size`                               | INTEGER | Results per page (max 2000)                                                |
| `--include-archived`                   | Flag    | Include archived assets                                                    |
| `--geo-point`                          | TEXT    | Geo filter as `lat,lon` or `lat,lon,radiusMeters`                          |
| `--geo-bbox`                           | TEXT    | Geo filter as `topLeftLat,topLeftLon,bottomRightLat,bottomRightLon`        |
| `--geo-geojson`                        | PATH    | Path to a GeoJSON file (Geometry, Feature, or FeatureCollection)           |
| `--geo-relation`                       | CHOICE  | Spatial relation: `intersects` (default), `within`, `contains`, `disjoint` |
| `--output-format`                      | CHOICE  | `table`, `json`, or `csv`                                                  |
| `--jsonOutput`                         | Flag    | Raw API response as JSON                                                   |

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

The `--metadata-query` supports AND/OR operators within the metadata group. The metadata group as a whole is combined with `--query` and `--filters` using AND logic.

```bash
# Single metadata condition
vamscli search assets --metadata-query "MD_str_product:Training"

# Wildcard in metadata value
vamscli search assets --metadata-query "MD_str_product:Train*"

# AND within metadata (both must match)
vamscli search assets --metadata-query "MD_str_product:Training AND MD_num_version:1"

# OR within metadata (either can match)
vamscli search assets --metadata-query "MD_str_color:red OR MD_str_color:blue"

# Combined: text query AND metadata (both must match)
vamscli search assets -q "model" --metadata-query "MD_str_category:Training"
```

### Geospatial filtering

The `--geo-*` options filter results by the derived `geo_MD_location` field on each indexed document. The indexer populates that field from a `location` metadata key (GeoJSON or `{latitude, longitude, altitude}` payload) or from individual `latitude` / `longitude` / `altitude` metadata fields. Provide exactly one of `--geo-point`, `--geo-bbox`, or `--geo-geojson`.

```bash
# Within 5 km of Seattle (point + radius)
vamscli search assets --geo-point "47.6062,-122.3321,5000"

# Inside a bounding box, requiring full containment
vamscli search assets --geo-bbox "47.7,-122.5,47.5,-122.2" --geo-relation within

# Inside an arbitrary GeoJSON polygon stored on disk
vamscli search assets --geo-geojson ./aoi.geojson

# Combined with text + metadata filters
vamscli search assets -q "tower" --metadata-query "MD_str_status:active" --geo-point "47.6,-122.3,2000"
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

Supports all the same options as `search assets`, including the `--geo-point`, `--geo-bbox`, `--geo-geojson`, and `--geo-relation` geospatial flags. Common file-specific filters:

```bash
vamscli search files --filters 'str_fileext:"gltf"'
vamscli search files --filters 'str_fileext:"png" AND str_databaseid:"my-database"'
vamscli search files --filters '[{"range": {"num_filesize": {"lte": 1048576}}}]'
vamscli search files --metadata-query "MD_str_format:GLTF2.0"
vamscli search files --geo-bbox "47.7,-122.5,47.5,-122.2" --geo-relation within
```

---

## search simple

Simplified search interface with user-friendly parameters.

```bash
vamscli search simple [OPTIONS]
```

| Option               | Type    | Description                                              |
| -------------------- | ------- | -------------------------------------------------------- |
| `-q`, `--query`      | TEXT    | General keyword search                                   |
| `--asset-name`       | TEXT    | Search by asset name                                     |
| `--asset-id`         | TEXT    | Search by asset ID                                       |
| `--asset-type`       | TEXT    | Filter by asset type                                     |
| `--file-key`         | TEXT    | Search by file key                                       |
| `--file-ext`         | TEXT    | Filter by file extension                                 |
| `-d`, `--database`   | TEXT    | Filter by database ID                                    |
| `--tags`             | TEXT    | Filter by tags (comma-separated)                         |
| `--metadata-key`     | TEXT    | Search metadata field names                              |
| `--metadata-value`   | TEXT    | Search metadata field values                             |
| `--entity-types`     | TEXT    | `asset`, `file`, or `asset,file` (default)               |
| `--include-archived` | Flag    | Include archived items                                   |
| `--geo-point`        | TEXT    | `lat,lon` or `lat,lon,radiusMeters`                      |
| `--geo-bbox`         | TEXT    | `topLeftLat,topLeftLon,bottomRightLat,bottomRightLon`    |
| `--geo-geojson`      | PATH    | Path to a GeoJSON file                                   |
| `--geo-relation`     | CHOICE  | `intersects` (default), `within`, `contains`, `disjoint` |
| `--from`             | INTEGER | Pagination offset                                        |
| `--size`             | INTEGER | Results per page (max 1000)                              |
| `--output-format`    | CHOICE  | `table`, `json`, or `csv`                                |

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

| Prefix            | Type      | Example Fields                                                        |
| ----------------- | --------- | --------------------------------------------------------------------- |
| `str_*`           | String    | `str_assetname`, `str_databaseid`, `str_fileext`, `str_key`           |
| `num_*`           | Numeric   | `num_filesize`                                                        |
| `date_*`          | Date      | `date_lastmodified`                                                   |
| `bool_*`          | Boolean   | `bool_isdistributable`, `bool_archived`                               |
| `list_*`          | List      | `list_tags`                                                           |
| `MD_*`            | Metadata  | `MD_str_product`, `MD_num_version`                                    |
| `geo_MD_location` | geo_shape | Derived from `location` or `latitude`/`longitude`/`altitude` metadata |

## Related Pages

-   [Asset Commands](assets.md)
-   [File Commands](files.md)
-   [Metadata Commands](metadata.md)
