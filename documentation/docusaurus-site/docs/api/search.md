# Search

This page documents the search endpoints in the VAMS API. VAMS uses Amazon OpenSearch to provide full-text and structured search across assets and files through a dual-index architecture.

For asset management, see [Assets](assets.md). For file operations, see [Files](files.md).

---

## Concepts

-   **Dual-Index Architecture**: VAMS maintains two separate OpenSearch indexes -- one for assets and one for files. Search queries can target either or both indexes.
-   **Entity Types**: Search results are categorized as either `asset` or `file`. You can filter by entity type.
-   **AND Query Logic**: The `query`, `metadataQuery`, and `filters` parameters are combined using AND logic. Results must match ALL specified criteria. Within a `metadataQuery`, individual field conditions can use AND or OR (e.g., `"color:red AND size:large"` or `"color:red OR color:blue"`).
-   **Metadata Search**: Metadata fields are indexed alongside core fields, enabling search by metadata keys, values, or both.
-   **Field Prefixes**: OpenSearch fields use type prefixes for proper mapping: `str_` (string/keyword), `num_` (number), `date_` (date), `bool_` (boolean), `list_` (array).
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
    "from": 0,
    "size": 100
}
```

| Field                     | Type    | Default      | Description                                                                                                                  |
| ------------------------- | ------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| `query`                   | string  | --           | General text search across all fields (AND with filters and metadata query). Max 5,000 characters.                           |
| `tokens`                  | array   | `[]`         | Structured search tokens for field-specific queries. See [Search Tokens](#search-tokens).                                    |
| `filters`                 | array   | `[]`         | Additional OpenSearch query_string filters. See [Search Filters](#search-filters).                                           |
| `sort`                    | array   | `["_score"]` | Sort configuration. See [Sorting](#sorting).                                                                                 |
| `operation`               | string  | `"AND"`      | Default logical operation for combining tokens (`"AND"` or `"OR"`).                                                          |
| `entityTypes`             | array   | `null`       | Filter by entity type: `["asset"]`, `["file"]`, or `["asset", "file"]`. When `null`, searches both.                          |
| `includeArchived`         | boolean | `false`      | Include archived items in results.                                                                                           |
| `aggregations`            | boolean | `true`       | Include aggregation facets in the response.                                                                                  |
| `metadataQuery`           | string  | --           | Metadata search query (AND with general query and filters). Supports AND/OR within the metadata group. Max 5,000 characters. |
| `metadataSearchMode`      | string  | `"both"`     | Metadata search scope: `"key"` (search keys only), `"value"` (search values only), or `"both"`.                              |
| `includeMetadataInSearch` | boolean | `true`       | Include metadata fields in the general `query` search.                                                                       |
| `explainResults`          | boolean | `false`      | Include match explanations in results.                                                                                       |
| `includeHighlights`       | boolean | `true`       | Include highlighted matching text in results.                                                                                |
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
                    "_rectype": "asset",
                    "str_databaseid": "my-database",
                    "str_assetid": "asset-001",
                    "str_assetname": "Building Model",
                    "str_assettype": "ifc",
                    "str_description": "Main building 3D model",
                    "list_tags": ["architecture", "building"],
                    "bool_isdistributable": true,
                    "date_lastmodified": "2024-06-15T10:30:00Z",
                    "str_asset_version_id": "v1"
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
                    "_rectype": "file",
                    "str_databaseid": "my-database",
                    "str_assetid": "asset-001",
                    "str_key": "/models/building.ifc",
                    "str_fileext": "ifc",
                    "num_size": 15728640,
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
        "asset_types": {
            "buckets": [
                { "key": "ifc", "doc_count": 45 },
                { "key": "obj", "doc_count": 30 },
                { "key": "glb", "doc_count": 25 }
            ]
        },
        "file_extensions": {
            "buckets": [
                { "key": "ifc", "doc_count": 50 },
                { "key": "jpg", "doc_count": 120 },
                { "key": "png", "doc_count": 80 }
            ]
        },
        "databases": {
            "buckets": [
                { "key": "my-database", "doc_count": 100 },
                { "key": "other-db", "doc_count": 50 }
            ]
        }
    },
    "aggregationTotal": 150
}
```

**Error Responses:**

| Status | Description                                      |
| ------ | ------------------------------------------------ |
| `400`  | Invalid search parameters.                       |
| `403`  | Not authorized to access search.                 |
| `500`  | Internal server error or OpenSearch unavailable. |

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

| Status | Description                      |
| ------ | -------------------------------- |
| `400`  | Invalid search parameters.       |
| `403`  | Not authorized to access search. |
| `500`  | Internal server error.           |

---

### Get Index Mappings

`GET /search`

Retrieves the field mappings for both asset and file indexes. Use this to discover available search fields, their types, and the field prefix naming convention.

**Request Parameters:**

None.

**Response:**

```json
{
    "mappings": {
        "dynamic_templates": [
            {
                "strings_as_keywords": {
                    "match_mapping_type": "string",
                    "match": "str_*",
                    "mapping": {
                        "type": "keyword",
                        "fields": {
                            "search": {
                                "type": "text"
                            }
                        }
                    }
                }
            }
        ],
        "properties": {
            "_rectype": { "type": "keyword" },
            "str_databaseid": { "type": "keyword" },
            "str_assetid": { "type": "keyword" },
            "str_assetname": { "type": "keyword" },
            "str_assettype": { "type": "keyword" },
            "str_description": { "type": "keyword" },
            "str_key": { "type": "keyword" },
            "str_fileext": { "type": "keyword" },
            "num_size": { "type": "long" },
            "date_lastmodified": { "type": "date" },
            "bool_isdistributable": { "type": "boolean" },
            "list_tags": { "type": "keyword" }
        }
    }
}
```

**Error Responses:**

| Status | Description                      |
| ------ | -------------------------------- |
| `403`  | Not authorized to access search. |
| `500`  | Internal server error.           |

---

## Search Tokens

Search tokens provide structured, field-specific search within the advanced search endpoint.

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
-   Range queries: `num_size:[1000 TO 5000]`
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
When sorting by indexed fields, use the prefixed field names (e.g., `str_assetname`, `date_lastmodified`, `num_size`). Sorting on non-prefixed or text-analyzed fields may produce unexpected results.
:::

---

## Available Search Fields

### Asset Index Fields

| Field                  | Type    | Description                      |
| ---------------------- | ------- | -------------------------------- |
| `_rectype`             | keyword | Always `"asset"`.                |
| `str_databaseid`       | keyword | Database identifier.             |
| `str_assetid`          | keyword | Asset identifier.                |
| `str_assetname`        | keyword | Asset display name.              |
| `str_assettype`        | keyword | File type classification.        |
| `str_description`      | keyword | Asset description.               |
| `list_tags`            | keyword | Asset tags (array).              |
| `bool_isdistributable` | boolean | Whether asset can be downloaded. |
| `date_lastmodified`    | date    | Last modification date.          |
| `str_asset_version_id` | keyword | Current asset version ID.        |

### File Index Fields

| Field               | Type    | Description                         |
| ------------------- | ------- | ----------------------------------- |
| `_rectype`          | keyword | Always `"file"`.                    |
| `str_databaseid`    | keyword | Database identifier.                |
| `str_assetid`       | keyword | Parent asset identifier.            |
| `str_assetname`     | keyword | Parent asset name.                  |
| `str_key`           | keyword | S3 object key (relative file path). |
| `str_fileext`       | keyword | File extension.                     |
| `num_size`          | long    | File size in bytes.                 |
| `str_etag`          | keyword | S3 ETag.                            |
| `str_s3_version_id` | keyword | S3 version ID.                      |
| `date_lastmodified` | date    | Last modification date.             |

### Metadata Fields

Metadata fields are dynamically indexed using the same prefix convention:

-   `str_meta_{key}` -- String metadata values
-   `num_meta_{key}` -- Numeric metadata values
-   `date_meta_{key}` -- Date metadata values
-   `bool_meta_{key}` -- Boolean metadata values

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
