# Metadata

This page documents the metadata management endpoints in the VAMS API. VAMS provides a centralized metadata service that handles metadata across four entity types: assets, files, databases, and asset links.

For asset management, see [Assets](assets.md). For file operations, see [Files](files.md).

---

## Concepts

- **Metadata Item**: A key-value pair with an associated value type. Each item consists of a `metadataKey`, `metadataValue`, and `metadataValueType`.
- **Metadata Value Type**: The data type of the metadata value. Determines validation rules and how the value is displayed in the UI.
- **File Metadata vs. File Attributes**: Both use the same API path with a `type` query parameter. File metadata stores descriptive information, while file attributes store operational data (e.g., `primaryType`).
- **Bulk Operations**: All create, update, and delete operations support bulk processing of multiple metadata items in a single request. Responses include partial success information.
- **Schema Validation**: When metadata schemas are configured, metadata values are validated against the schema on create and update operations.

### Supported Value Types

| Type | Description | Example Value |
|------|-------------|---------------|
| `string` | Plain text string | `"Building A"` |
| `multiline_string` | Multi-line text | `"Line 1\nLine 2"` |
| `inline_controlled_list` | String from a controlled vocabulary | `"approved"` |
| `number` | Numeric value | `"42.5"` |
| `boolean` | Boolean value | `"true"` or `"false"` |
| `date` | ISO 8601 date string | `"2024-06-15T10:30:00Z"` |
| `xyz` | 3D coordinate | `"{\"x\": 1.0, \"y\": 2.0, \"z\": 3.0}"` |
| `wxyz` | Quaternion rotation | `"{\"w\": 1.0, \"x\": 0.0, \"y\": 0.0, \"z\": 0.0}"` |
| `matrix4x4` | 4x4 transformation matrix | `"[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]"` |
| `geopoint` | GeoJSON Point | `"{\"type\": \"Point\", \"coordinates\": [-73.9, 40.7]}"` |
| `geojson` | Any valid GeoJSON | `"{\"type\": \"Polygon\", \"coordinates\": [...]}"` |
| `lla` | Latitude/Longitude/Altitude | `"{\"lat\": 40.7, \"long\": -73.9, \"alt\": 100.0}"` |
| `json` | Arbitrary JSON | `"{\"custom\": \"data\"}"` |

:::note[Values Are Always Strings]
All metadata values are stored and transmitted as strings, regardless of type. The `metadataValueType` field indicates how the string should be interpreted and validated.
:::


---

## Asset Metadata

Asset-level metadata is attached to an asset within a database.

### Get Asset Metadata

`GET /database/{databaseId}/assets/{assetId}/metadata`

Retrieves all metadata items for the specified asset.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |
| `maxItems` | query | integer | No | Maximum items to return. Default: `30000`. |
| `pageSize` | query | integer | No | Page size for pagination. Default: `3000`. |
| `startingToken` | query | string | No | Continuation token from a previous response. |
| `assetVersionId` | query | string | No | Retrieve metadata from a specific asset version snapshot. |

**Response:**

```json
{
    "metadata": [
        {
            "metadataKey": "material",
            "metadataValue": "concrete",
            "metadataValueType": "string"
        },
        {
            "metadataKey": "height_meters",
            "metadataValue": "45.5",
            "metadataValueType": "number"
        },
        {
            "metadataKey": "position",
            "metadataValue": "{\"x\": 100.0, \"y\": 50.0, \"z\": 0.0}",
            "metadataValueType": "xyz"
        }
    ],
    "NextToken": "eyJ..."
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or pagination token. |
| `403` | Not authorized to view metadata for this asset. |
| `404` | Asset not found. |
| `500` | Internal server error. |

---

### Create Asset Metadata

`POST /database/{databaseId}/assets/{assetId}/metadata`

Adds new metadata items to an asset. Supports bulk creation of multiple items in a single request.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |

**Request Body:**

```json
{
    "metadata": [
        {
            "metadataKey": "material",
            "metadataValue": "concrete",
            "metadataValueType": "string"
        },
        {
            "metadataKey": "height_meters",
            "metadataValue": "45.5",
            "metadataValueType": "number"
        }
    ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `metadata` | array | Yes | List of metadata items. Must contain at least one item. |
| `metadata[].metadataKey` | string | Yes | Metadata key (1-256 characters). |
| `metadata[].metadataValue` | string | Yes | Metadata value as string. |
| `metadata[].metadataValueType` | string | No | Value type. Default: `"string"`. |

**Response:**

```json
{
    "success": true,
    "totalItems": 2,
    "successCount": 2,
    "failureCount": 0,
    "successfulItems": ["material", "height_meters"],
    "failedItems": [],
    "message": "All 2 metadata items created successfully",
    "timestamp": "2024-06-15T10:30:00Z"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters, validation error, or schema validation failure. |
| `403` | Not authorized to create metadata for this asset. |
| `404` | Asset not found. |
| `500` | Internal server error. |

---

### Update Asset Metadata

`PUT /database/{databaseId}/assets/{assetId}/metadata`

Updates existing metadata items for an asset. Supports two update modes.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |

**Request Body:**

```json
{
    "metadata": [
        {
            "metadataKey": "material",
            "metadataValue": "steel",
            "metadataValueType": "string"
        }
    ],
    "updateType": "update"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `metadata` | array | Yes | List of metadata items to update. |
| `updateType` | string | No | `"update"` (default, upserts provided items) or `"replace_all"` (replaces all metadata). |

:::warning[REPLACE_ALL Mode]
The `replace_all` update type deletes all existing metadata and replaces it with the provided items. This mode requires the user to have `PUT`, `POST`, and `DELETE` permissions on the entity. It is limited to 500 items per operation and includes automatic rollback on failure.
:::


**Response:**

```json
{
    "success": true,
    "totalItems": 1,
    "successCount": 1,
    "failureCount": 0,
    "successfulItems": ["material"],
    "failedItems": [],
    "message": "All 1 metadata items updated successfully",
    "timestamp": "2024-06-15T10:30:00Z"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or validation error. |
| `403` | Not authorized to update metadata for this asset. |
| `404` | Asset not found. |
| `500` | Internal server error. |

---

### Delete Asset Metadata

`DELETE /database/{databaseId}/assets/{assetId}/metadata`

Removes metadata items from an asset by key.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |

**Request Body:**

```json
{
    "metadataKeys": ["material", "height_meters"]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `metadataKeys` | array[string] | Yes | List of metadata keys to delete. Must contain at least one key. |

**Response:**

```json
{
    "success": true,
    "totalItems": 2,
    "successCount": 2,
    "failureCount": 0,
    "successfulItems": ["material", "height_meters"],
    "failedItems": [],
    "message": "All 2 metadata items deleted successfully",
    "timestamp": "2024-06-15T10:30:00Z"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized to delete metadata for this asset. |
| `404` | Asset not found. |
| `500` | Internal server error. |

---

## File Metadata

File-level metadata is attached to individual files within an asset. The same endpoint path handles both file metadata and file attributes, distinguished by a `type` query parameter.

### Get File Metadata

`GET /database/{databaseId}/assets/{assetId}/metadata/file`

Retrieves metadata for a specific file within an asset.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |
| `key` | query | string | Yes | Relative file path. |
| `type` | query | string | No | `"metadata"` (default) or `"attribute"` to retrieve file attributes instead. |
| `maxItems` | query | integer | No | Maximum items to return. |
| `pageSize` | query | integer | No | Page size for pagination. |
| `startingToken` | query | string | No | Continuation token. |

**Response:**

```json
{
    "metadata": [
        {
            "metadataKey": "author",
            "metadataValue": "John Smith",
            "metadataValueType": "string"
        }
    ],
    "NextToken": null
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or missing `key`. |
| `403` | Not authorized. |
| `404` | Asset or file not found. |
| `500` | Internal server error. |

---

### Create File Metadata

`POST /database/{databaseId}/assets/{assetId}/metadata/file`

Adds metadata items to a specific file.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |

**Request Body:**

```json
{
    "key": "/models/building.ifc",
    "type": "metadata",
    "metadata": [
        {
            "metadataKey": "author",
            "metadataValue": "John Smith",
            "metadataValueType": "string"
        }
    ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `key` | string | Yes | Relative file path. |
| `type` | string | No | `"metadata"` (default) or `"attribute"`. |
| `metadata` | array | Yes | List of metadata items. |

**Response:**

Returns a bulk operation response (same format as asset metadata).

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized. |
| `404` | Asset or file not found. |
| `500` | Internal server error. |

---

### Update File Metadata

`PUT /database/{databaseId}/assets/{assetId}/metadata/file`

Updates metadata items for a specific file.

**Request Body:**

```json
{
    "key": "/models/building.ifc",
    "type": "metadata",
    "metadata": [
        {
            "metadataKey": "author",
            "metadataValue": "Jane Doe",
            "metadataValueType": "string"
        }
    ],
    "updateType": "update"
}
```

**Response:**

Returns a bulk operation response.

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized. |
| `404` | Asset or file not found. |
| `500` | Internal server error. |

---

### Delete File Metadata

`DELETE /database/{databaseId}/assets/{assetId}/metadata/file`

Removes metadata items from a specific file.

**Request Body:**

```json
{
    "key": "/models/building.ifc",
    "type": "metadata",
    "metadataKeys": ["author"]
}
```

**Response:**

Returns a bulk operation response.

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized. |
| `404` | Asset or file not found. |
| `500` | Internal server error. |

---

## Database Metadata

Database-level metadata is attached to a database and applies to the entire collection.

### Get Database Metadata

`GET /database/{databaseId}/metadata`

Retrieves all metadata items for the specified database.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `maxItems` | query | integer | No | Maximum items to return. Default: `30000`. |
| `pageSize` | query | integer | No | Page size for pagination. Default: `3000`. |
| `startingToken` | query | string | No | Continuation token. |

**Response:**

```json
{
    "metadata": [
        {
            "metadataKey": "project_name",
            "metadataValue": "Downtown Development",
            "metadataValueType": "string"
        },
        {
            "metadataKey": "project_start_date",
            "metadataValue": "2024-01-15T00:00:00Z",
            "metadataValueType": "date"
        }
    ],
    "NextToken": null
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized to view metadata for this database. |
| `404` | Database not found. |
| `500` | Internal server error. |

---

### Create Database Metadata

`POST /database/{databaseId}/metadata`

Adds metadata items to a database.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |

**Request Body:**

```json
{
    "metadata": [
        {
            "metadataKey": "project_name",
            "metadataValue": "Downtown Development",
            "metadataValueType": "string"
        }
    ]
}
```

**Response:**

Returns a bulk operation response.

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or schema validation failure. |
| `403` | Not authorized. |
| `404` | Database not found. |
| `500` | Internal server error. |

---

### Update Database Metadata

`PUT /database/{databaseId}/metadata`

Updates metadata items for a database.

**Request Body:**

```json
{
    "metadata": [
        {
            "metadataKey": "project_name",
            "metadataValue": "Updated Project Name",
            "metadataValueType": "string"
        }
    ],
    "updateType": "update"
}
```

**Response:**

Returns a bulk operation response.

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized. |
| `404` | Database not found. |
| `500` | Internal server error. |

---

### Delete Database Metadata

`DELETE /database/{databaseId}/metadata`

Removes metadata items from a database.

**Request Body:**

```json
{
    "metadataKeys": ["project_name"]
}
```

**Response:**

Returns a bulk operation response.

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized. |
| `404` | Database not found. |
| `500` | Internal server error. |

---

## Asset Link Metadata

Metadata can be attached to asset links (relationships between assets).

### Get Asset Link Metadata

`GET /asset-links/{assetLinkId}/metadata`

Retrieves all metadata items for the specified asset link.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `assetLinkId` | path | string | Yes | Asset link identifier (UUID). |
| `maxItems` | query | integer | No | Maximum items to return. Default: `30000`. |
| `pageSize` | query | integer | No | Page size for pagination. Default: `3000`. |
| `startingToken` | query | string | No | Continuation token. |

**Response:**

```json
{
    "metadata": [
        {
            "metadataKey": "relationship_type",
            "metadataValue": "structural_support",
            "metadataValueType": "string"
        }
    ],
    "NextToken": null
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or pagination token. |
| `403` | Not authorized to view metadata for this asset link. |
| `404` | Asset link not found. |
| `500` | Internal server error. |

---

### Create Asset Link Metadata

`POST /asset-links/{assetLinkId}/metadata`

Adds metadata items to an asset link. Supports bulk creation.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `assetLinkId` | path | string | Yes | Asset link identifier (UUID). |

**Request Body:**

```json
{
    "metadata": [
        {
            "metadataKey": "relationship_type",
            "metadataValue": "structural_support",
            "metadataValueType": "string"
        }
    ]
}
```

**Response:**

Returns a bulk operation response.

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or validation error. |
| `403` | Not authorized. |
| `404` | Asset link not found. |
| `500` | Internal server error. |

---

### Update Asset Link Metadata

`PUT /asset-links/{assetLinkId}/metadata`

Updates metadata items for an asset link.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `assetLinkId` | path | string | Yes | Asset link identifier (UUID). |

**Request Body:**

```json
{
    "metadata": [
        {
            "metadataKey": "relationship_type",
            "metadataValue": "updated_value",
            "metadataValueType": "string"
        }
    ],
    "updateType": "update"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `metadata` | array | Yes | List of metadata items to update. |
| `updateType` | string | No | `"update"` (default) or `"replace_all"`. |

**Response:**

Returns a bulk operation response.

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized. |
| `404` | Asset link not found. |
| `500` | Internal server error. |

---

### Delete Asset Link Metadata

`DELETE /asset-links/{assetLinkId}/metadata`

Removes metadata items from an asset link.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `assetLinkId` | path | string | Yes | Asset link identifier (UUID). |

**Request Body:**

```json
{
    "metadataKeys": ["relationship_type"]
}
```

**Response:**

Returns a bulk operation response.

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized. |
| `404` | Asset link not found. |
| `500` | Internal server error. |

---

## Bulk Operation Response Format

All metadata create, update, and delete operations return a consistent bulk operation response:

```json
{
    "success": true,
    "totalItems": 3,
    "successCount": 2,
    "failureCount": 1,
    "successfulItems": ["key1", "key2"],
    "failedItems": [
        {
            "key": "key3",
            "error": "Validation failed: value must be a valid number"
        }
    ],
    "message": "2 of 3 metadata items processed successfully",
    "timestamp": "2024-06-15T10:30:00Z"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | `true` if at least one item succeeded. |
| `totalItems` | integer | Total number of items in the request. |
| `successCount` | integer | Number of items that succeeded. |
| `failureCount` | integer | Number of items that failed. |
| `successfulItems` | array[string] | List of metadata keys that succeeded. |
| `failedItems` | array[object] | List of failed items with error details. |
| `message` | string | Human-readable summary of the operation. |
| `timestamp` | string | ISO 8601 timestamp of the operation. |

:::info[Partial Success]
Bulk operations can partially succeed. Check both `successCount` and `failureCount` to determine the overall result. The `failedItems` array provides per-item error details for troubleshooting.
:::


---

## Metadata Limits

| Limit | Value | Description |
|-------|-------|-------------|
| Maximum metadata records per entity | 500 | Maximum number of metadata key-value pairs per asset, file, database, or asset link. |
| Maximum key length | 256 characters | Maximum length of a `metadataKey`. |
| Maximum items per REPLACE_ALL | 500 | Maximum metadata items in a single `replace_all` operation. |
