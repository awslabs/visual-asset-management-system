# Search

This page documents the search endpoints in the VAMS API. VAMS uses Amazon OpenSearch to provide full-text and structured search across assets and files through a dual-index architecture.

For asset management, see [Assets](assets.md). For file operations, see [Files](files.md).

---

## Concepts

-   **Dual-Index Architecture**: VAMS maintains two separate OpenSearch indexes -- one for assets and one for files. Search queries can target either or both indexes.
-   **Entity Types**: Search results are categorized as either `asset` or `file`. You can filter by entity type.
-   **AND Query Logic**: The `query`, `metadataQuery`, and `filters` parameters are combined using AND logic. Results must match ALL specified criteria. Within a `metadataQuery`, individual field conditions can use AND or OR (e.g., `"color:red AND size:large"` or `"color:red OR color:blue"`).
-   **Metadata Search**: Metadata is indexed alongside the core fields, enabling search by metadata keys, values, or both. It is stored differently from a core field -- see [Metadata Fields](#metadata-fields).
-   **Field Prefixes**: The core OpenSearch fields use type prefixes for proper mapping: `str_` (string/keyword), `num_` (number), `date_` (date), `bool_` (boolean), `list_` (array). Metadata keys do not carry one.
-   **Aggregations**: Search responses can include faceted aggregation data (e.g., counts by asset type, file extension, database).

---

## Endpoints

### Advanced Search

`POST /search`

Executes a search query across the asset and file indexes with full control over query construction, filtering, sorting, pagination, and aggregations.

**Request Body:**

```json
{
    "query": "building model",
    "tokens": [
        {
            "operation": "AND",
            "operator": "=",
            "propertyKey": "str_assettype",
            "value": "ifc"
        }
    ],
    "filters": [
        {
            "query_string": {
                "query": "str_databaseid:my-database"
            }
        }
    ],
    "sort": ["_score"],
    "operation": "AND",
    "entityTypes": ["asset", "file"],
    "includeArchived": false,
    "aggregations": true,
    "metadataQuery": "material:concrete",
    "metadataSearchMode": "both",
    "includeMetadataInSearch": true,
    "explainResults": false,
    "includeHighlights": true,
    "geoSearch": {
        "relation": "intersects",
        "point": { "lat": 47.6062, "lon": -122.3321, "radiusMeters": 5000 }
    },
    "from": 0,
    "size": 100
}
```

| Field                     | Type    | Default      | Description                                                                                                                  |
| ------------------------- | ------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `query`                   | string  | --           | General text search across all fields (AND with filters and metadata query). Max 5,000 characters.                           |
| `tokens`                  | array   | `[]`         | Accepted for request compatibility but does not affect results. Use `filters` or `metadataQuery` for field-specific queries. |
| `filters`                 | array   | `[]`         | Additional OpenSearch query_string filters. See [Search Filters](#search-filters).                                           |
| `sort`                    | array   | `["_score"]` | Sort configuration. See [Sorting](#sorting).                                                                                 |
| `operation`               | string  | `"AND"`      | Accepted for request compatibility but does not affect results.                                                              |
| `entityTypes`             | array   | `null`       | Filter by entity type: `["asset"]`, `["file"]`, or `["asset", "file"]`. When `null`, searches both.                          |
| `includeArchived`         | boolean | `false`      | Include archived items in results.                                                                                           |
| `aggregations`            | boolean | `true`       | Include aggregation facets in the response.                                                                                  |
| `metadataQuery`           | string  | --           | Metadata search query (AND with general query and filters). Supports AND/OR within the metadata group. Max 5,000 characters. |
| `metadataSearchMode`      | string  | `"both"`     | Metadata search scope: `"key"` (search keys only), `"value"` (search values only), or `"both"`.                              |
| `includeMetadataInSearch` | boolean | `true`       | Include metadata fields in the general `query` search.                                                                       |
| `explainResults`          | boolean | `false`      | Include match explanations in results.                                                                                       |
| `includeHighlights`       | boolean | `true`       | Include highlighted matching text in results.                                                                                |
| `geoSearch`               | object  | --           | Geospatial filter against the `geo_MD_location` field. See [Geospatial Search](#geospatial-search).                          |
| `from`                    | integer | `0`          | Starting offset for pagination (0-10,000).                                                                                   |
| `size`                    | integer | `100`        | Number of results to return (1-2,000).                                                                                       |

:::warning[Pagination Limits]
The combined value of `from` + `size` cannot exceed 10,000. This is an OpenSearch limitation. For deep pagination, use more specific search criteria to narrow results.
:::

**Response:**

```json
{
    "took": 42,
    "timed_out": false,
    "_shards": {
        "total": 2,
        "successful": 2,
        "skipped": 0,
        "failed": 0
    },
    "hits": {
        "total": {
            "value": 150,
            "relation": "eq"
        },
        "max_score": 8.5,
        "hits": [
            {
                "_index": "vams-assets",
                "_id": "my-database:asset-001",
                "_score": 8.5,
                "_source": {
                    "str_rectype": "asset",
                    "str_databaseid": "my-database",
                    "str_assetid": "asset-001",
                    "str_assetname": "Building Model",
                    "str_assettype": "ifc",
                    "str_description": "Main building 3D model",
                    "list_tags": ["architecture", "building"],
                    "bool_isdistributable": true,
                    "date_lastmodified": "2024-06-15T10:30:00Z",
                    "str_asset_version_id": "v1",
                    "MD_": { "material": "concrete" }
                },
                "highlight": {
                    "str_assetname": ["<em>Building</em> <em>Model</em>"]
                }
            },
            {
                "_index": "vams-files",
                "_id": "my-database:asset-001:/models/building.ifc",
                "_score": 7.2,
                "_source": {
                    "str_rectype": "file",
                    "str_databaseid": "my-database",
                    "str_assetid": "asset-001",
                    "str_key": "/models/building.ifc",
                    "str_fileext": "ifc",
                    "num_filesize": 15728640,
                    "str_etag": "\"d41d8cd98f00b204e9800998ecf8427e\"",
                    "str_s3_version_id": "abc123",
                    "date_lastmodified": "2024-06-15T10:30:00Z"
                },
                "highlight": {
                    "str_key": ["/models/<em>building</em>.ifc"]
                }
            }
        ]
    },
    "aggregations": {
        "str_assettype": {
            "buckets": [
                { "key": "ifc", "doc_count": 45 },
                { "key": "obj", "doc_count": 30 },
                { "key": "glb", "doc_count": 25 }
            ]
        },
        "str_fileext": {
            "buckets": [
                { "key": "ifc", "doc_count": 50 },
                { "key": "jpg", "doc_count": 120 },
                { "key": "png", "doc_count": 80 }
            ]
        },
        "str_databaseid": {
            "buckets": [
                { "key": "my-database", "doc_count": 100 },
                { "key": "other-db", "doc_count": 50 }
            ]
        },
        "list_tags": {
            "buckets": [
                { "key": "architecture", "doc_count": 60 },
                { "key": "building", "doc_count": 40 }
            ]
        }
    },
    "aggregationTotal": 150
}
```

**Error Responses:**

| Status | Description                                                         |
| ------ | ------------------------------------------------------------------- |
| `400`  | Invalid search parameters.                                          |
| `403`  | Not authorized to access search.                                    |
| `404`  | Search is not available when the OpenSearch feature is not enabled. |
| `500`  | Internal server error.                                              |

---

### Simple Search

`POST /search/simple`

Executes a simplified search with basic parameters for easy API integration. The system automatically constructs the OpenSearch query from the provided fields.

**Request Body:**

```json
{
    "query": "building",
    "entityTypes": ["asset"],
    "databaseId": "my-database",
    "assetName": "Building",
    "assetType": "ifc",
    "tags": ["architecture"],
    "metadataKey": "material",
    "metadataValue": "concrete",
    "includeArchived": false,
    "from": 0,
    "size": 100
}
```

| Field             | Type          | Default | Description                                              |
| ----------------- | ------------- | ------- | -------------------------------------------------------- |
| `query`           | string        | --      | General keyword search across all fields.                |
| `entityTypes`     | array         | --      | Filter by entity type: `["asset"]`, `["file"]`, or both. |
| `assetName`       | string        | --      | Search by asset name.                                    |
| `assetId`         | string        | --      | Search by asset ID.                                      |
| `assetType`       | string        | --      | Filter by asset type.                                    |
| `fileKey`         | string        | --      | Search by S3 file key.                                   |
| `fileExtension`   | string        | --      | Filter by file extension (e.g., `"ifc"` or `".ifc"`).    |
| `databaseId`      | string        | --      | Filter by database ID.                                   |
| `tags`            | array[string] | --      | Search by tags.                                          |
| `metadataKey`     | string        | --      | Search metadata field names.                             |
| `metadataValue`   | string        | --      | Search metadata field values.                            |
| `includeArchived` | boolean       | `false` | Include archived items.                                  |
| `from`            | integer       | `0`     | Starting offset (0-10,000).                              |
| `size`            | integer       | `100`   | Number of results (1-2,000).                             |

:::tip[When to Use Simple Search]
Use simple search when you need basic filtering by known fields. Use [Advanced Search](#advanced-search) when you need structured tokens, custom filters, or fine-grained control over query behavior.
:::

**Response:**

Same format as [Advanced Search](#advanced-search).

**Error Responses:**

| Status | Description                                                         |
| ------ | ------------------------------------------------------------------- |
| `400`  | Invalid search parameters.                                          |
| `403`  | Not authorized to access search.                                    |
| `404`  | Search is not available when the OpenSearch feature is not enabled. |
| `500`  | Internal server error.                                              |

---

### Get Index Mappings

`GET /search`

Retrieves the field mappings for both asset and file indexes. Use this to discover available search fields, their types, and the field prefix naming convention.

**Request Parameters:**

None.

**Response:**

The response returns the mappings for both indexes, keyed by `asset_index` and `file_index`.

```json
{
    "mappings": {
        "asset_index": {
            "mappings": {
                "properties": {
                    "str_rectype": { "type": "keyword" },
                    "str_databaseid": { "type": "keyword" },
                    "str_assetid": { "type": "keyword" },
                    "str_assetname": { "type": "keyword" },
                    "str_assettype": { "type": "keyword" },
                    "str_description": { "type": "keyword" },
                    "date_lastmodified": { "type": "date" },
                    "bool_isdistributable": { "type": "boolean" },
                    "list_tags": { "type": "keyword" }
                }
            }
        },
        "file_index": {
            "mappings": {
                "properties": {
                    "str_rectype": { "type": "keyword" },
                    "str_databaseid": { "type": "keyword" },
                    "str_assetid": { "type": "keyword" },
                    "str_key": { "type": "keyword" },
                    "str_fileext": { "type": "keyword" },
                    "num_filesize": { "type": "long" },
                    "date_lastmodified": { "type": "date" },
                    "list_tags": { "type": "keyword" }
                }
            }
        }
    }
}
```

**Error Responses:**

| Status | Description                                                         |
| ------ | ------------------------------------------------------------------- |
| `403`  | Not authorized to access search.                                    |
| `404`  | Search is not available when the OpenSearch feature is not enabled. |
| `500`  | Internal server error.                                              |

---

## Search Tokens

:::warning[Not applied to results]
The `tokens` array and the top-level `operation` field are accepted by the request model for compatibility but are not applied by the query builder — they do not affect which results are returned. For field-specific queries, use `filters` (see [Search Filters](#search-filters)) or `metadataQuery`.
:::

The token structure is described below for reference.

```json
{
    "operation": "AND",
    "operator": "=",
    "propertyKey": "str_assettype",
    "value": "ifc"
}
```

| Field         | Type   | Default | Description                                                                                            |
| ------------- | ------ | ------- | ------------------------------------------------------------------------------------------------------ |
| `operation`   | string | `"AND"` | How to combine with other tokens: `"AND"` or `"OR"`.                                                   |
| `operator`    | string | `"="`   | Comparison operator: `"="` (exact match), `":"` (contains), `"!="` (not equal), `"!:"` (not contains). |
| `propertyKey` | string | --      | The field to search. Use `null` or `"all"` for multi-field search.                                     |
| `value`       | string | --      | The value to search for. Required, minimum 1 character.                                                |

### Token Examples

**Exact match on asset type:**

```json
{ "operator": "=", "propertyKey": "str_assettype", "value": "ifc" }
```

**Contains search on asset name:**

```json
{ "operator": ":", "propertyKey": "str_assetname", "value": "building" }
```

**Exclude a database:**

```json
{ "operator": "!=", "propertyKey": "str_databaseid", "value": "test-database" }
```

**Multi-field search:**

```json
{ "operator": ":", "propertyKey": null, "value": "building" }
```

---

## Search Filters

Filters use OpenSearch query_string syntax for advanced filtering.

```json
{
    "query_string": {
        "query": "str_databaseid:my-database AND str_assettype:ifc"
    }
}
```

The `query` value follows [OpenSearch query_string syntax](https://opensearch.org/docs/latest/query-dsl/full-text/query-string/), supporting:

-   Field-specific queries: `str_assettype:ifc`
-   Boolean operators: `AND`, `OR`, `NOT`
-   Wildcards: `str_assetname:build*`
-   Range queries: `num_filesize:[1000 TO 5000]`
-   Grouping: `(str_assettype:ifc OR str_assettype:obj)`

---

## Sorting

The `sort` field accepts an array of sort specifications. Each item can be a string (field name, ascending) or an object with field and order.

**Sort by score (default):**

```json
"sort": ["_score"]
```

**Sort by field:**

```json
"sort": [
    {"str_assetname": {"order": "asc"}},
    "_score"
]
```

:::note[Sort Field Prefixes]
When sorting by indexed fields, use the prefixed field names (e.g., `str_assetname`, `date_lastmodified`, `num_filesize`). Sorting on non-prefixed or text-analyzed fields may produce unexpected results.
:::

---

## Geospatial Search

VAMS indexes a derived `geo_MD_location` field of OpenSearch type `geo_shape` on every asset and file document. The indexer populates it from each entity's metadata using the following priority:

1. A metadata key named `location` (case-insensitive) containing either:
    - A GeoJSON Geometry, Feature, or FeatureCollection (Point, Polygon, MultiPolygon, etc.).
    - A JSON object with `latitude` / `longitude` and optional `altitude` keys.
    - A `"lat,lon"` or `"lat,lon,altitude"` string.
2. Individual `latitude`, `longitude`, and optional `altitude` metadata fields.

If neither is present, the document has no `geo_MD_location` and is excluded from geospatial filters.

To filter search results by location, supply a `geoSearch` object on the request body. Provide **exactly one** of `point`, `bbox`, or `geoJson`:

```json
{
    "geoSearch": {
        "relation": "intersects",
        "point": { "lat": 47.6062, "lon": -122.3321, "radiusMeters": 5000 }
    }
}
```

```json
{
    "geoSearch": {
        "relation": "within",
        "bbox": {
            "topLeft": { "lat": 47.7, "lon": -122.5 },
            "bottomRight": { "lat": 47.5, "lon": -122.2 }
        }
    }
}
```

```json
{
    "geoSearch": {
        "relation": "intersects",
        "geoJson": {
            "type": "Polygon",
            "coordinates": [
                [
                    [-122.5, 47.7],
                    [-122.2, 47.7],
                    [-122.2, 47.5],
                    [-122.5, 47.5],
                    [-122.5, 47.7]
                ]
            ]
        }
    }
}
```

| Field                | Type   | Description                                                                                                                                            |
| -------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `relation`           | string | `"intersects"` (default), `"within"`, `"contains"`, or `"disjoint"`. Spatial relation between the input shape and the indexed `geo_MD_location` shape. |
| `point.lat`          | number | Latitude in decimal degrees (-90 to 90).                                                                                                               |
| `point.lon`          | number | Longitude in decimal degrees (-180 to 180).                                                                                                            |
| `point.radiusMeters` | number | Optional radius around the point. When provided, the input is treated as a circle.                                                                     |
| `bbox.topLeft`       | object | Northwest corner of the bounding box (`{lat, lon}`).                                                                                                   |
| `bbox.bottomRight`   | object | Southeast corner of the bounding box.                                                                                                                  |
| `geoJson`            | object | GeoJSON Geometry, Feature, or FeatureCollection. See the limits below.                                                                                 |

:::note[GeoJSON Filter Limits]
A submitted `geoJson` shape carries at most 100,000 coordinate positions and nests `GeometryCollection` members at most 32 levels deep. A shape exceeding either limit is rejected with a `400` naming it, so the whole search request fails rather than returning partial results. The nesting limit is the same one the `geojson` metadata value type enforces (see [Metadata](metadata.md)), which keeps the shapes a filter accepts and the shapes indexed in `geo_MD_location` aligned.
:::

:::note[Backward Compatibility]
Documents indexed before the introduction of `geo_MD_location` will not match geospatial filters until reindexed. Map views in the web UI continue to render points and shapes from legacy `MD_.location` and `MD_.latitude` / `MD_.longitude` metadata as a fallback.
:::

---

## Available Search Fields

### Asset Index Fields

| Field                  | Type      | Description                                                                       |
| ---------------------- | --------- | --------------------------------------------------------------------------------- |
| `str_rectype`          | keyword   | Always `"asset"`.                                                                 |
| `str_databaseid`       | keyword   | Database identifier.                                                              |
| `str_assetid`          | keyword   | Asset identifier.                                                                 |
| `str_assetname`        | keyword   | Asset display name.                                                               |
| `str_assettype`        | keyword   | File type classification.                                                         |
| `str_description`      | keyword   | Asset description.                                                                |
| `list_tags`            | keyword   | Asset tags (array).                                                               |
| `bool_isdistributable` | boolean   | Whether asset can be downloaded.                                                  |
| `date_lastmodified`    | date      | Last modification date.                                                           |
| `str_asset_version_id` | keyword   | Current asset version ID.                                                         |
| `geo_MD_location`      | geo_shape | GeoJSON shape derived from metadata. See [Geospatial Search](#geospatial-search). |

### File Index Fields

| Field               | Type      | Description                                                                       |
| ------------------- | --------- | --------------------------------------------------------------------------------- |
| `str_rectype`       | keyword   | Always `"file"`.                                                                  |
| `str_databaseid`    | keyword   | Database identifier.                                                              |
| `str_assetid`       | keyword   | Parent asset identifier.                                                          |
| `str_assetname`     | keyword   | Parent asset name.                                                                |
| `str_key`           | keyword   | S3 object key (relative file path).                                               |
| `str_fileext`       | keyword   | File extension.                                                                   |
| `num_filesize`      | long      | File size in bytes.                                                               |
| `str_etag`          | keyword   | S3 ETag.                                                                          |
| `str_s3_version_id` | keyword   | S3 version ID.                                                                    |
| `date_lastmodified` | date      | Last modification date.                                                           |
| `geo_MD_location`   | geo_shape | GeoJSON shape derived from metadata. See [Geospatial Search](#geospatial-search). |

### Metadata Fields

Metadata does not follow the prefix convention above. Both indexes store all of a record's metadata in one field, `MD_`, mapped as an OpenSearch `flat_object`, and the file index stores file attributes the same way in `AB_`. Keys appear inside those objects verbatim, with no type prefix, so metadata `{"product": "Training"}` reads back in a hit's `_source` as:

```json
"MD_": { "product": "Training" }
```

One field per index rather than one field per key is what keeps a deployment's metadata vocabulary from growing the index mapping without bound.

| Field             | Type        | Description                                                               |
| ----------------- | ----------- | ------------------------------------------------------------------------- |
| `MD_`             | flat_object | All of the record's metadata, keys verbatim. Both indexes.                |
| `AB_`             | flat_object | All of the file's attributes, keys verbatim. File index only.             |
| `geo_MD_location` | geo_shape   | Shape derived from metadata. See [Geospatial Search](#geospatial-search). |

Three separate things are worth keeping apart when reading the rest of this page:

-   **What the index stores** — the `MD_` and `AB_` objects above.
-   **What a query addresses internally** — one key as `MD_.{key}` or `AB_.{key}`, and a value-only search as `MD_._value` (plus `AB_._value` on the file index), the `flat_object` subfield holding every value in the object.
-   **What a request may submit** — see below. The submitted spelling is normalized before the query is built, so it need not match either of the above.

#### Metadata Key Spellings a Request May Use

`metadataKey` and `metadataQuery` accept the metadata key in three spellings, all of which resolve to the same field:

| Submitted        | Resolves to   | Notes                                                                  |
| ---------------- | ------------- | ---------------------------------------------------------------------- |
| `product`        | `MD_.product` | Canonical. A key with no prefix is read as metadata.                   |
| `MD_product`     | `MD_.product` | Entity prefix. Use `AB_{key}` to address a file attribute instead.     |
| `MD_str_product` | `MD_.product` | Type prefix (`str_`, `num_`, `bool_`, `date_`, `list_`, `gp_`, `gs_`). |

Do not carry the dot of the internal query path into a request. `MD_.product` is not one of the accepted spellings — it resolves to `MD_..product`, which no document holds, so the search succeeds and returns nothing.

---

## Search Response Structure

All search endpoints return the same response structure.

| Field                     | Type    | Description                                     |
| ------------------------- | ------- | ----------------------------------------------- |
| `took`                    | integer | Time in milliseconds for the search to execute. |
| `timed_out`               | boolean | Whether the search timed out.                   |
| `_shards`                 | object  | Shard execution statistics.                     |
| `hits.total.value`        | integer | Total number of matching documents.             |
| `hits.total.relation`     | string  | `"eq"` (exact count) or `"gte"` (lower bound).  |
| `hits.max_score`          | float   | Highest relevance score in results.             |
| `hits.hits`               | array   | Array of matching documents.                    |
| `hits.hits[]._index`      | string  | OpenSearch index name.                          |
| `hits.hits[]._id`         | string  | Document identifier.                            |
| `hits.hits[]._score`      | float   | Relevance score for this document.              |
| `hits.hits[]._source`     | object  | The indexed document fields.                    |
| `hits.hits[].highlight`   | object  | Highlighted matching text (if enabled).         |
| `hits.hits[].explanation` | object  | Match explanation (if requested).               |
| `aggregations`            | object  | Faceted aggregation buckets (if requested).     |
| `aggregationTotal`        | integer | True total from aggregation bucket sums.        |
