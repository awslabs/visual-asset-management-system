# Databases API

The Databases API allows you to create, retrieve, update, and delete databases. Databases are the top-level organizational containers in VAMS that hold assets, pipelines, and workflows. Each database has an associated Amazon S3 bucket for asset storage.

:::info[Authorization]
All endpoints require a valid JWT token in the `Authorization` header. Database endpoints enforce Casbin authorization using the `database` object type.
:::

---

## List all databases

Retrieves all databases.

```
GET /database
```

### Query parameters

| Parameter       | Type   | Required | Default | Description                             |
| --------------- | ------ | -------- | ------- | --------------------------------------- |
| `maxItems`      | number | No       | `100`   | Maximum number of items to return       |
| `pageSize`      | number | No       | `100`   | Number of items per page                |
| `startingToken` | string | No       | `null`  | Pagination token from previous response |
| `showDeleted`   | string | No       | `false` | Include soft-deleted databases          |

### Response

```json
{
    "Items": [
        {
            "databaseId": "architecture-db",
            "description": "3D architectural models and floor plans",
            "dateCreated": "March 15 2026 - 10:30:00",
            "assetCount": 42,
            "defaultBucketId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "bucketName": "vams-assets-abc123",
            "baseAssetsPrefix": "assets/",
            "restrictMetadataOutsideSchemas": false,
            "restrictFileUploadsToExtensions": ""
        }
    ],
    "NextToken": null
}
```

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `403`  | Not authorized        |
| `500`  | Internal server error |

---

## Get a database

Retrieves a single database by its identifier.

```
GET /database/{databaseId}
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |

### Response

Returns a single database object in the same format as the items in the list response.

### Error responses

| Status | Description                 |
| ------ | --------------------------- |
| `400`  | Invalid `databaseId` format |
| `403`  | Not authorized              |
| `404`  | Database not found          |
| `500`  | Internal server error       |

---

## Create a database

Creates a new database associated with a pre-configured S3 bucket and prefix.

```
POST /database
```

### Request body

| Field                             | Type    | Required | Description                                                                                                                                                                                                       |
| --------------------------------- | ------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `databaseId`                      | string  | Yes      | Unique database identifier (4-256 chars, alphanumeric plus `-` and `_`). Cannot be `GLOBAL` or a reserved S3 keyword (`pipeline(s)`, `preview(s)`, `temp-upload(s)`, `workspace(s)`), matched case-insensitively. |
| `description`                     | string  | Yes      | Description of the database (4-256 chars).                                                                                                                                                                        |
| `defaultBucketId`                 | string  | Yes      | UUID of a pre-configured S3 bucket and prefix combination.                                                                                                                                                        |
| `restrictMetadataOutsideSchemas`  | boolean | No       | When `true`, metadata must conform to an applied metadata schema. Defaults to `false`.                                                                                                                            |
| `restrictFileUploadsToExtensions` | string  | No       | Comma-separated list of allowed file extensions (e.g., `.jpg,.png,.pdf`). Use `.all` or leave blank to allow all. Defaults to empty.                                                                              |

### Request body example

```json
{
    "databaseId": "architecture-db",
    "description": "3D architectural models and floor plans",
    "defaultBucketId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "restrictMetadataOutsideSchemas": false,
    "restrictFileUploadsToExtensions": ""
}
```

### Response

```json
{
    "databaseId": "architecture-db",
    "message": "Database created successfully"
}
```

### Error responses

| Status | Description                                 |
| ------ | ------------------------------------------- |
| `400`  | Validation error or database already exists |
| `403`  | Not authorized                              |
| `500`  | Internal server error                       |

---

## Update a database

Updates database metadata.

```
PUT /database/{databaseId}
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |

At least one field must be provided. The `databaseId` cannot be changed after creation.

### Request body

| Field                             | Type    | Required | Description                                                                                                       |
| --------------------------------- | ------- | -------- | ----------------------------------------------------------------------------------------------------------------- |
| `description`                     | string  | No       | Updated description (4-256 chars).                                                                                |
| `defaultBucketId`                 | string  | No       | UUID of a pre-configured S3 bucket and prefix combination. Must reference an existing bucket.                     |
| `restrictMetadataOutsideSchemas`  | boolean | No       | Toggle metadata schema enforcement.                                                                               |
| `restrictFileUploadsToExtensions` | string  | No       | Comma-separated list of allowed file extensions (e.g., `.jpg,.png,.pdf`). Use `.all` or leave blank to allow all. |

### Request body example

```json
{
    "description": "Updated 3D architectural models",
    "restrictFileUploadsToExtensions": ".e57,.las,.laz,.ply"
}
```

### Response

```json
{
    "success": true,
    "message": "Database architecture-db updated successfully",
    "databaseId": "architecture-db",
    "operation": "update",
    "timestamp": "2026-03-16T14:20:00"
}
```

---

## Delete a database

Soft-deletes a database.

```
DELETE /database/{databaseId}
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |

:::warning[Dependency check]
A database cannot be deleted if it contains active assets, pipelines, or workflows. Remove all dependent resources before deleting the database.
:::

### Response

```json
{
    "message": "Database deleted"
}
```

### Error responses

| Status | Description                                         |
| ------ | --------------------------------------------------- |
| `400`  | Database has active assets, pipelines, or workflows |
| `403`  | Not authorized                                      |
| `404`  | Database not found                                  |
| `500`  | Internal server error                               |

---

## Related resources

-   [Assets API](assets.md) -- Manage assets within databases
-   [Pipelines API](pipelines.md) -- Define pipelines scoped to databases
-   [Workflows API](workflows.md) -- Create workflows within databases
-   [Authorization API](auth.md) -- Configure database-level access permissions
