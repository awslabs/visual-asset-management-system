# Metadata

This page documents the metadata management endpoints in the VAMS API. VAMS provides a centralized metadata service that handles metadata across four entity types: assets, files, databases, and asset links.

For asset management, see [Assets](assets.md). For file operations, see [Files](files.md).

---

## Concepts

-   **Metadata Item**: A key-value pair with an associated value type. Each item consists of a `metadataKey`, `metadataValue`, and `metadataValueType`.
-   **Metadata Value Type**: The data type of the metadata value. Determines validation rules and how the value is displayed in the UI.
-   **File Metadata vs. File Attributes**: Both use the same API path with a `type` query parameter. File metadata stores descriptive information, while file attributes store operational data (e.g., `primaryType`).
-   **Bulk Operations**: All create, update, and delete operations support bulk processing of multiple metadata items in a single request. Responses include partial success information.
-   **Schema Validation**: When metadata schemas are configured, metadata values are validated against the schema on create and update operations.

### Supported Value Types

| Type                     | Description                         | Example Value                                             |
| ------------------------ | ----------------------------------- | --------------------------------------------------------- |
| `string`                 | Plain text string                   | `"Building A"`                                            |
| `multiline_string`       | Multi-line text                     | `"Line 1\nLine 2"`                                        |
| `inline_controlled_list` | String from a controlled vocabulary | `"approved"`                                              |
| `number`                 | Numeric value                       | `"42.5"`                                                  |
| `boolean`                | Boolean value                       | `"true"` or `"false"`                                     |
| `date`                   | ISO 8601 date string                | `"2024-06-15T10:30:00Z"`                                  |
| `xyz`                    | 3D coordinate                       | `"{\"x\": 1.0, \"y\": 2.0, \"z\": 3.0}"`                  |
| `wxyz`                   | Quaternion rotation                 | `"{\"w\": 1.0, \"x\": 0.0, \"y\": 0.0, \"z\": 0.0}"`      |
| `matrix4x4`              | 4x4 transformation matrix           | `"[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]"`             |
| `geopoint`               | GeoJSON Point                       | `"{\"type\": \"Point\", \"coordinates\": [-73.9, 40.7]}"` |
| `geojson`                | GeoJSON, nested at most 32 levels   | `"{\"type\": \"Polygon\", \"coordinates\": [...]}"`       |
| `lla`                    | Latitude/Longitude/Altitude         | `"{\"lat\": 40.7, \"long\": -73.9, \"alt\": 100.0}"`      |
| `json`                   | Arbitrary JSON                      | `"{\"custom\": \"data\"}"`                                |

:::note[Values Are Always Strings]
All metadata values are stored and transmitted as strings, regardless of type. The `metadataValueType` field indicates how the string should be interpreted and validated.
:::

:::note[GeoJSON Nesting Limit]
A `geojson` or `geopoint` value may nest `GeometryCollection` members at most 32 levels deep. A deeper value is rejected with a `400` naming the limit, as is a value too deeply nested for the JSON parser to read. The same limit applies to the `geoJson` filter on [Search](search.md) and to the shapes indexed for geospatial search, so a value the metadata API accepts is a value search can match.
:::

:::note[Incomplete Records]
A metadata record that carries no stored value, or no stored value type, is returned with that field as `null`. The record stays visible in the response along with its key. On a create or update, `null` in either field is read as the field not being supplied: `metadataValue` stores an empty value, and `metadataValueType` takes the default `"string"`. An item taken from a `GET` response can therefore be submitted back unchanged to complete the record.

Completing a record does not bypass schema validation. Where a schema marks the field as required, a write that leaves its value empty is rejected — supply a value for that field in the same request.
:::

---

## Asset Metadata

Asset-level metadata is attached to an asset within a database.

### Get Asset Metadata

`GET /database/{databaseId}/assets/{assetId}/metadata`

Retrieves metadata items for the specified asset, one page at a time. When more records exist than `pageSize`, the response includes a `NextToken`; pass it as `startingToken` to retrieve the next page. Records are ordered consistently across pages.

**Request Parameters:**

| Parameter        | Location | Type    | Required | Description                                                                                       |
| ---------------- | -------- | ------- | -------- | ------------------------------------------------------------------------------------------------- |
| `databaseId`     | path     | string  | Yes      | Database identifier.                                                                              |
| `assetId`        | path     | string  | Yes      | Asset identifier.                                                                                 |
| `maxItems`       | query    | integer | No       | Maximum items to return. Default: `1000`. Maximum: `1000`; a larger value is rejected with `400`. |
| `pageSize`       | query    | integer | No       | Page size for pagination. Default: `100`. Maximum: `1000`; a larger value is rejected with `400`. |
| `startingToken`  | query    | string  | No       | Continuation token from a previous response.                                                      |
| `assetVersionId` | query    | string  | No       | Retrieve metadata from a specific asset version snapshot.                                         |

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
    "restrictMetadataOutsideSchemas": false,
    "NextToken": "eyJ...",
    "message": "Success"
}
```

The response always includes `restrictMetadataOutsideSchemas` (a boolean that is `true` when the database restricts metadata to schema-defined fields and at least one schema exists) and a `message` field.

:::info[Schema Enrichment Fields]
When a metadata schema applies to the entity, each metadata item is enriched with additional schema fields: `metadataSchemaName`, `metadataSchemaField`, `metadataSchemaRequired`, `metadataSchemaSequence`, `metadataSchemaDefaultValue`, `metadataSchemaDependsOn`, `metadataSchemaMultiFieldConflict`, and `metadataSchemaControlledListKeys`. These fields are omitted (or `null`) when no schema defines the item. This enrichment applies to the asset, file, database, and asset link metadata GET responses.
:::

**Error Responses:**

| Status | Description                                     |
| ------ | ----------------------------------------------- |
| `400`  | Invalid parameters or pagination token.         |
| `403`  | Not authorized to view metadata for this asset. |
| `404`  | Asset not found.                                |
| `500`  | Internal server error.                          |

---

### Create Asset Metadata

`POST /database/{databaseId}/assets/{assetId}/metadata`

Adds new metadata items to an asset. Supports bulk creation of multiple items in a single request.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

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

| Field                          | Type   | Required | Description                                             |
| ------------------------------ | ------ | -------- | ------------------------------------------------------- |
| `metadata`                     | array  | Yes      | List of metadata items. Must contain at least one item. |
| `metadata[].metadataKey`       | string | Yes      | Metadata key (1-256 characters).                        |
| `metadata[].metadataValue`     | string | Yes      | Metadata value as string. `null` stores an empty value. |
| `metadata[].metadataValueType` | string | No       | Value type. `null` or omitted: `"string"`.              |

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

| Status | Description                                                         |
| ------ | ------------------------------------------------------------------- |
| `400`  | Invalid parameters, validation error, or schema validation failure. |
| `403`  | Not authorized to create metadata for this asset.                   |
| `404`  | Asset not found.                                                    |
| `500`  | Internal server error.                                              |

---

### Update Asset Metadata

`PUT /database/{databaseId}/assets/{assetId}/metadata`

Updates existing metadata items for an asset. Supports two update modes.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

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

| Field        | Type   | Required | Description                                                                              |
| ------------ | ------ | -------- | ---------------------------------------------------------------------------------------- |
| `metadata`   | array  | Yes      | List of metadata items to update.                                                        |
| `updateType` | string | No       | `"update"` (default, upserts provided items) or `"replace_all"` (replaces all metadata). |

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

| Status | Description                                       |
| ------ | ------------------------------------------------- |
| `400`  | Invalid parameters or validation error.           |
| `403`  | Not authorized to update metadata for this asset. |
| `404`  | Asset not found.                                  |
| `500`  | Internal server error.                            |

---

### Delete Asset Metadata

`DELETE /database/{databaseId}/assets/{assetId}/metadata`

Removes metadata items from an asset by key.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Request Body:**

```json
{
    "metadataKeys": ["material", "height_meters"]
}
```

| Field          | Type          | Required | Description                                                     |
| -------------- | ------------- | -------- | --------------------------------------------------------------- |
| `metadataKeys` | array[string] | Yes      | List of metadata keys to delete. Must contain at least one key. |

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

| Status | Description                                       |
| ------ | ------------------------------------------------- |
| `400`  | Invalid parameters.                               |
| `403`  | Not authorized to delete metadata for this asset. |
| `404`  | Asset not found.                                  |
| `500`  | Internal server error.                            |

---

## File Metadata

File-level metadata is attached to individual files within an asset. The same endpoint path handles both file metadata and file attributes, distinguished by a `type` query parameter.

### Get File Metadata

`GET /database/{databaseId}/assets/{assetId}/metadata/file`

Retrieves metadata for a specific file within an asset.

**Request Parameters:**

| Parameter        | Location | Type    | Required | Description                                                                                       |
| ---------------- | -------- | ------- | -------- | ------------------------------------------------------------------------------------------------- |
| `databaseId`     | path     | string  | Yes      | Database identifier.                                                                              |
| `assetId`        | path     | string  | Yes      | Asset identifier.                                                                                 |
| `filePath`       | query    | string  | Yes      | Relative file path.                                                                               |
| `type`           | query    | string  | Yes      | `"metadata"` to retrieve file metadata, or `"attribute"` to retrieve file attributes.             |
| `maxItems`       | query    | integer | No       | Maximum items to return. Default: `1000`. Maximum: `1000`; a larger value is rejected with `400`. |
| `pageSize`       | query    | integer | No       | Page size for pagination. Default: `100`. Maximum: `1000`; a larger value is rejected with `400`. |
| `startingToken`  | query    | string  | No       | Continuation token.                                                                               |
| `assetVersionId` | query    | string  | No       | Retrieve metadata from a specific asset version snapshot.                                         |

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
    "restrictMetadataOutsideSchemas": false,
    "NextToken": null,
    "message": "Success"
}
```

**Error Responses:**

| Status | Description                               |
| ------ | ----------------------------------------- |
| `400`  | Invalid parameters or missing `filePath`. |
| `403`  | Not authorized.                           |
| `404`  | Asset or file not found.                  |
| `500`  | Internal server error.                    |

---

### Create File Metadata

`POST /database/{databaseId}/assets/{assetId}/metadata/file`

Adds metadata items to a specific file.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Request Body:**

```json
{
    "filePath": "/models/building.ifc",
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

| Field      | Type   | Required | Description                    |
| ---------- | ------ | -------- | ------------------------------ |
| `filePath` | string | Yes      | Relative file path.            |
| `type`     | string | Yes      | `"metadata"` or `"attribute"`. |
| `metadata` | array  | Yes      | List of metadata items.        |

**Response:**

Returns a bulk operation response (same format as asset metadata).

**Error Responses:**

| Status | Description              |
| ------ | ------------------------ |
| `400`  | Invalid parameters.      |
| `403`  | Not authorized.          |
| `404`  | Asset or file not found. |
| `500`  | Internal server error.   |

---

### Update File Metadata

`PUT /database/{databaseId}/assets/{assetId}/metadata/file`

Updates metadata items for a specific file.

**Request Body:**

```json
{
    "filePath": "/models/building.ifc",
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

`filePath` and `type` are required. `type` is `"metadata"` or `"attribute"`; `updateType` is `"update"` (default) or `"replace_all"`.

**Response:**

Returns a bulk operation response.

**Error Responses:**

| Status | Description              |
| ------ | ------------------------ |
| `400`  | Invalid parameters.      |
| `403`  | Not authorized.          |
| `404`  | Asset or file not found. |
| `500`  | Internal server error.   |

---

### Delete File Metadata

`DELETE /database/{databaseId}/assets/{assetId}/metadata/file`

Removes metadata items from a specific file.

**Request Body:**

```json
{
    "filePath": "/models/building.ifc",
    "type": "metadata",
    "metadataKeys": ["author"]
}
```

`filePath` and `type` are required. `type` is `"metadata"` or `"attribute"`.

**Response:**

Returns a bulk operation response.

**Error Responses:**

| Status | Description              |
| ------ | ------------------------ |
| `400`  | Invalid parameters.      |
| `403`  | Not authorized.          |
| `404`  | Asset or file not found. |
| `500`  | Internal server error.   |

---

## Database Metadata

Database-level metadata is attached to a database and applies to the entire collection.

### Get Database Metadata

`GET /database/{databaseId}/metadata`

Retrieves metadata items for the specified database, one page at a time.

**Request Parameters:**

| Parameter       | Location | Type    | Required | Description                                                                                       |
| --------------- | -------- | ------- | -------- | ------------------------------------------------------------------------------------------------- |
| `databaseId`    | path     | string  | Yes      | Database identifier.                                                                              |
| `maxItems`      | query    | integer | No       | Maximum items to return. Default: `1000`. Maximum: `1000`; a larger value is rejected with `400`. |
| `pageSize`      | query    | integer | No       | Page size for pagination. Default: `100`. Maximum: `1000`; a larger value is rejected with `400`. |
| `startingToken` | query    | string  | No       | Continuation token.                                                                               |

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
    "restrictMetadataOutsideSchemas": false,
    "NextToken": null,
    "message": "Success"
}
```

**Error Responses:**

| Status | Description                                        |
| ------ | -------------------------------------------------- |
| `400`  | Invalid parameters.                                |
| `403`  | Not authorized to view metadata for this database. |
| `404`  | Database not found.                                |
| `500`  | Internal server error.                             |

---

### Create Database Metadata

`POST /database/{databaseId}/metadata`

Adds metadata items to a database.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |

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

| Status | Description                                      |
| ------ | ------------------------------------------------ |
| `400`  | Invalid parameters or schema validation failure. |
| `403`  | Not authorized.                                  |
| `404`  | Database not found.                              |
| `500`  | Internal server error.                           |

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

| Status | Description            |
| ------ | ---------------------- |
| `400`  | Invalid parameters.    |
| `403`  | Not authorized.        |
| `404`  | Database not found.    |
| `500`  | Internal server error. |

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

| Status | Description            |
| ------ | ---------------------- |
| `400`  | Invalid parameters.    |
| `403`  | Not authorized.        |
| `404`  | Database not found.    |
| `500`  | Internal server error. |

---

## Asset Link Metadata

Metadata can be attached to asset links (relationships between assets).

### Get Asset Link Metadata

`GET /asset-links/{assetLinkId}/metadata`

Retrieves metadata items for the specified asset link, one page at a time.

**Request Parameters:**

| Parameter       | Location | Type    | Required | Description                                                                                       |
| --------------- | -------- | ------- | -------- | ------------------------------------------------------------------------------------------------- |
| `assetLinkId`   | path     | string  | Yes      | Asset link identifier (UUID).                                                                     |
| `maxItems`      | query    | integer | No       | Maximum items to return. Default: `1000`. Maximum: `1000`; a larger value is rejected with `400`. |
| `pageSize`      | query    | integer | No       | Page size for pagination. Default: `100`. Maximum: `1000`; a larger value is rejected with `400`. |
| `startingToken` | query    | string  | No       | Continuation token.                                                                               |

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
    "restrictMetadataOutsideSchemas": false,
    "NextToken": null,
    "message": "Success"
}
```

**Error Responses:**

| Status | Description                                          |
| ------ | ---------------------------------------------------- |
| `400`  | Invalid parameters or pagination token.              |
| `403`  | Not authorized to view metadata for this asset link. |
| `404`  | Asset link not found.                                |
| `500`  | Internal server error.                               |

---

### Create Asset Link Metadata

`POST /asset-links/{assetLinkId}/metadata`

Adds metadata items to an asset link. Supports bulk creation.

**Request Parameters:**

| Parameter     | Location | Type   | Required | Description                   |
| ------------- | -------- | ------ | -------- | ----------------------------- |
| `assetLinkId` | path     | string | Yes      | Asset link identifier (UUID). |

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

| Status | Description                             |
| ------ | --------------------------------------- |
| `400`  | Invalid parameters or validation error. |
| `403`  | Not authorized.                         |
| `404`  | Asset link not found.                   |
| `500`  | Internal server error.                  |

---

### Update Asset Link Metadata

`PUT /asset-links/{assetLinkId}/metadata`

Updates metadata items for an asset link.

**Request Parameters:**

| Parameter     | Location | Type   | Required | Description                   |
| ------------- | -------- | ------ | -------- | ----------------------------- |
| `assetLinkId` | path     | string | Yes      | Asset link identifier (UUID). |

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

| Field        | Type   | Required | Description                              |
| ------------ | ------ | -------- | ---------------------------------------- |
| `metadata`   | array  | Yes      | List of metadata items to update.        |
| `updateType` | string | No       | `"update"` (default) or `"replace_all"`. |

**Response:**

Returns a bulk operation response.

**Error Responses:**

| Status | Description            |
| ------ | ---------------------- |
| `400`  | Invalid parameters.    |
| `403`  | Not authorized.        |
| `404`  | Asset link not found.  |
| `500`  | Internal server error. |

---

### Delete Asset Link Metadata

`DELETE /asset-links/{assetLinkId}/metadata`

Removes metadata items from an asset link.

**Request Parameters:**

| Parameter     | Location | Type   | Required | Description                   |
| ------------- | -------- | ------ | -------- | ----------------------------- |
| `assetLinkId` | path     | string | Yes      | Asset link identifier (UUID). |

**Request Body:**

```json
{
    "metadataKeys": ["relationship_type"]
}
```

**Response:**

Returns a bulk operation response.

**Error Responses:**

| Status | Description            |
| ------ | ---------------------- |
| `400`  | Invalid parameters.    |
| `403`  | Not authorized.        |
| `404`  | Asset link not found.  |
| `500`  | Internal server error. |

---

## Metadata Schemas

A metadata schema declares the fields that metadata on a given entity type should carry, along with each field's value type, display order, dependencies, and default value. Schemas drive the schema-enrichment fields on the metadata `GET` responses, and a database that sets `restrictMetadataOutsideSchemas` accepts only metadata keys an applicable schema declares.

A schema is scoped to one database and one entity type. Use `GLOBAL` as the `databaseId` for a schema that applies across every database. Schemas are authorized with the `metadataSchema` object type on `databaseId`, `metadataSchemaName`, and `metadataSchemaEntityType`.

### Entity types

| Entity type           | Applies to                                                                |
| --------------------- | ------------------------------------------------------------------------- |
| `databaseMetadata`    | Database-level metadata                                                   |
| `assetMetadata`       | Asset-level metadata                                                      |
| `fileMetadata`        | File-level metadata                                                       |
| `fileAttribute`       | File attributes. Only the `string` value type is accepted on these fields |
| `assetLinkMetadata`   | Asset-link metadata                                                       |

### Field definitions

A schema's `fields` object holds a `fields` array of 1 to 500 field definitions. Field key names must be unique within a schema.

| Field                       | Type          | Required | Description                                                                                                                                                    |
| --------------------------- | ------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `metadataFieldKeyName`      | string        | Yes      | Field key name (1-256 chars). Matches the `metadataKey` of the metadata record it governs.                                                                      |
| `metadataFieldValueType`    | string        | Yes      | One of the [supported value types](#supported-value-types). Accepted case-insensitively.                                                                        |
| `required`                  | boolean       | No       | Whether a value must be supplied for this field. Defaults to `false`.                                                                                          |
| `sequence`                  | number        | No       | Display order, 0-based; lower numbers appear first.                                                                                                            |
| `dependsOnFieldKeyName`     | array[string] | No       | Field key names this field depends on, at most 500 entries of 256 characters each.                                                                              |
| `controlledListKeys`        | array[string] | No       | Allowed values, at most 1,000 entries of 256 characters each. Required when `metadataFieldValueType` is `inline_controlled_list`, and rejected for other types. |
| `defaultMetadataFieldValue` | string        | No       | Default value. Validated against `metadataFieldValueType`, and for a controlled list must be one of `controlledListKeys`.                                       |

---

### List metadata schemas

Retrieves metadata schemas, optionally filtered by database and entity type.

```
GET /metadataschema
```

#### Query parameters

| Parameter            | Type   | Required | Default | Description                                                                    |
| -------------------- | ------ | -------- | ------- | ------------------------------------------------------------------------------ |
| `databaseId`         | string | No       | `null`  | Return only the schemas scoped to this database. `GLOBAL` is accepted.          |
| `metadataEntityType` | string | No       | `null`  | Return only the schemas for this entity type. Accepted case-insensitively.      |
| `maxItems`           | number | No       | `30000` | Maximum number of items to return                                              |
| `pageSize`           | number | No       | `3000`  | Number of items per page                                                       |
| `startingToken`      | string | No       | `null`  | Pagination token from a previous response's `NextToken`                         |

#### Response

```json
{
    "Items": [
        {
            "metadataSchemaId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "databaseId": "architecture-db",
            "metadataSchemaEntityType": "assetMetadata",
            "schemaName": "Building record",
            "fileKeyTypeRestriction": null,
            "fields": {
                "fields": [
                    {
                        "metadataFieldKeyName": "building_name",
                        "metadataFieldValueType": "string",
                        "required": true,
                        "sequence": 0
                    },
                    {
                        "metadataFieldKeyName": "review_status",
                        "metadataFieldValueType": "inline_controlled_list",
                        "required": false,
                        "sequence": 1,
                        "controlledListKeys": ["draft", "in_review", "approved"],
                        "defaultMetadataFieldValue": "draft"
                    }
                ]
            },
            "enabled": true,
            "dateCreated": "2026-03-15T10:30:00",
            "dateModified": "2026-03-15T10:30:00",
            "createdBy": "user@example.com",
            "modifiedBy": "user@example.com"
        }
    ],
    "NextToken": null
}
```

`NextToken` is present only when more schemas remain.

#### Error responses

| Status | Description                            |
| ------ | -------------------------------------- |
| `400`  | Invalid parameters or pagination token |
| `403`  | Not authorized                         |
| `500`  | Internal server error                  |

---

### Get a metadata schema

Retrieves a single metadata schema by its identifier.

```
GET /database/{databaseId}/metadataSchema/{metadataSchemaId}
```

#### Path parameters

| Parameter          | Type   | Required | Description                                     |
| ------------------ | ------ | -------- | ----------------------------------------------- |
| `databaseId`       | string | Yes      | Database identifier. `GLOBAL` is accepted.      |
| `metadataSchemaId` | string | Yes      | Metadata schema identifier                      |

#### Response

Returns a single schema object in the same format as the items in the list response.

#### Error responses

| Status | Description                 |
| ------ | --------------------------- |
| `400`  | Invalid path parameters     |
| `403`  | Not authorized              |
| `404`  | Metadata schema not found   |
| `500`  | Internal server error       |

---

### Create a metadata schema

Creates a metadata schema for one database and entity type.

```
POST /metadataschema
```

#### Request body

| Field                      | Type    | Required | Description                                                                                                                                         |
| -------------------------- | ------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `databaseId`               | string  | Yes      | Database the schema applies to. Use `GLOBAL` for a schema that applies across every database. The database must exist.                               |
| `metadataSchemaEntityType` | string  | Yes      | Entity type the schema governs. See [Entity types](#entity-types).                                                                                   |
| `schemaName`               | string  | Yes      | Schema name (1-256 chars).                                                                                                                          |
| `fields`                   | object  | Yes      | Field definitions. See [Field definitions](#field-definitions).                                                                                     |
| `fileKeyTypeRestriction`   | string  | No       | Comma-delimited file extensions the schema applies to, each at most 10 characters. Accepted only for `fileMetadata` and `fileAttribute` entity types. |
| `enabled`                  | boolean | No       | Whether the schema is enforced. Defaults to `true`.                                                                                                 |

#### Request body example

```json
{
    "databaseId": "architecture-db",
    "metadataSchemaEntityType": "assetMetadata",
    "schemaName": "Building record",
    "enabled": true,
    "fields": {
        "fields": [
            {
                "metadataFieldKeyName": "building_name",
                "metadataFieldValueType": "string",
                "required": true,
                "sequence": 0
            },
            {
                "metadataFieldKeyName": "review_status",
                "metadataFieldValueType": "inline_controlled_list",
                "sequence": 1,
                "controlledListKeys": ["draft", "in_review", "approved"],
                "defaultMetadataFieldValue": "draft"
            }
        ]
    }
}
```

#### Response

```json
{
    "success": true,
    "message": "Metadata schema 'Building record' created successfully",
    "metadataSchemaId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "operation": "create",
    "timestamp": "2026-03-15T10:30:00.000000"
}
```

#### Error responses

| Status | Description                                                                       |
| ------ | --------------------------------------------------------------------------------- |
| `400`  | Validation error, a `fileKeyTypeRestriction` on an unsupported entity type, or a `databaseId` that does not exist |
| `403`  | Not authorized                                                                    |
| `500`  | Internal server error                                                             |

---

### Update a metadata schema

Updates a metadata schema. The schema to update is identified by `metadataSchemaId` in the request body; `databaseId` and `metadataSchemaEntityType` are fixed at creation and cannot be changed.

```
PUT /metadataschema
```

#### Request body

At least one field other than `metadataSchemaId` must be provided. Supplying `fields` replaces the schema's entire field set.

| Field                    | Type    | Required | Description                                                         |
| ------------------------ | ------- | -------- | ------------------------------------------------------------------- |
| `metadataSchemaId`       | string  | Yes      | Identifier of the schema to update                                  |
| `schemaName`             | string  | No       | Updated schema name (1-256 chars)                                   |
| `fields`                 | object  | No       | Replacement field definitions. See [Field definitions](#field-definitions). |
| `fileKeyTypeRestriction` | string  | No       | Updated comma-delimited file extensions                             |
| `enabled`                | boolean | No       | Toggle schema enforcement                                           |

#### Response

```json
{
    "success": true,
    "message": "Metadata schema updated successfully",
    "metadataSchemaId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "operation": "update",
    "timestamp": "2026-03-16T14:20:00.000000"
}
```

#### Error responses

| Status | Description                                                                     |
| ------ | ------------------------------------------------------------------------------- |
| `400`  | Validation error, no updatable field supplied, or the schema does not exist      |
| `403`  | Not authorized                                                                  |
| `500`  | Internal server error                                                           |

---

### Delete a metadata schema

Deletes a metadata schema.

```
DELETE /database/{databaseId}/metadataSchema/{metadataSchemaId}
```

#### Path parameters

| Parameter          | Type   | Required | Description                                |
| ------------------ | ------ | -------- | ------------------------------------------ |
| `databaseId`       | string | Yes      | Database identifier. `GLOBAL` is accepted. |
| `metadataSchemaId` | string | Yes      | Metadata schema identifier                 |

#### Request body

The request body is required and must confirm the deletion.

| Field           | Type    | Required | Description                                  |
| --------------- | ------- | -------- | -------------------------------------------- |
| `confirmDelete` | boolean | Yes      | Must be `true`; the delete is rejected otherwise |

```json
{
    "confirmDelete": true
}
```

#### Response

```json
{
    "success": true,
    "message": "Metadata schema deleted successfully",
    "metadataSchemaId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "operation": "delete",
    "timestamp": "2026-03-16T14:20:00.000000"
}
```

#### Error responses

| Status | Description                                                                        |
| ------ | ---------------------------------------------------------------------------------- |
| `400`  | Invalid path parameters, `confirmDelete` not `true`, or the schema does not exist    |
| `403`  | Not authorized                                                                     |
| `500`  | Internal server error                                                              |

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

| Field             | Type          | Description                              |
| ----------------- | ------------- | ---------------------------------------- |
| `success`         | boolean       | `true` if at least one item succeeded.   |
| `totalItems`      | integer       | Total number of items in the request.    |
| `successCount`    | integer       | Number of items that succeeded.          |
| `failureCount`    | integer       | Number of items that failed.             |
| `successfulItems` | array[string] | List of metadata keys that succeeded.    |
| `failedItems`     | array[object] | List of failed items with error details. |
| `message`         | string        | Human-readable summary of the operation. |
| `timestamp`       | string        | ISO 8601 timestamp of the operation.     |

:::info[Partial Success]
Bulk operations can partially succeed. Check both `successCount` and `failureCount` to determine the overall result. The `failedItems` array provides per-item error details for troubleshooting.
:::

---

## Metadata Limits

| Limit                               | Value          | Description                                                                          |
| ----------------------------------- | -------------- | ------------------------------------------------------------------------------------ |
| Maximum metadata records per entity | 500            | Maximum number of metadata key-value pairs per asset, file, database, or asset link. |
| Maximum key length                  | 256 characters | Maximum length of a `metadataKey`.                                                   |
| Maximum items per REPLACE_ALL       | 500            | Maximum metadata items in a single `replace_all` operation.                          |
| Maximum `pageSize` and `maxItems`   | 1,000          | Largest value either metadata pagination parameter may carry on a read.              |

:::note[Paging a metadata read]
`pageSize` and `maxItems` each size a single response, and the page served is the smaller of the two. `pageSize` defaults to 100 and `maxItems` to 1,000, so a read with no pagination parameters returns 100 records. A value above the maximum in the table above is rejected with `400` rather than reduced to it, so a caller asking for more than one response can hold learns that from the answer instead of reading a shortened page as the complete set. When records remain beyond the page, the response carries a `NextToken`; pass it as `startingToken` to read the next page, and repeat until no `NextToken` is returned — the whole set is reachable that way whatever the page size.
:::

:::note[Reserved metadata keys]
`REINDEX_METADATA_RECORD` is reserved for VAMS internal use; a create or update that supplies it is refused. A key carrying the `VAMS_` prefix or a leading underscore is accepted and returned by every metadata read, and is excluded from search indexing — a key with a leading underscore is also absent from asset export output.
:::
