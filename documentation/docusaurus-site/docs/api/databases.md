# Databases API

The Databases API allows you to create, retrieve, update, and delete databases. Databases are the top-level organizational containers in VAMS that hold assets, pipelines, and workflows. Each database has an associated Amazon S3 bucket for asset storage.

:::info[Authorization]
All endpoints require a valid JWT token in the `Authorization` header. Database endpoints enforce Casbin authorization using the `database` object type.
:::

:::note[Free-text whitespace]
Surrounding whitespace is removed from a submitted `description` before the length constraint is applied and before the value is stored, so a subsequent read returns the trimmed value. A padded value whose trimmed length falls below the documented minimum is rejected with `400`. Interior whitespace is preserved.
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

## Bucket configurations

A database is created against a **bucket configuration** — a registered pairing of an Amazon S3 bucket and a base prefix. Bucket configurations are registered at deployment time from the `app.assetBuckets` deployment configuration, covering both the bucket the deployment creates and any external buckets it is given, and cannot be created, changed, or removed through the API. This endpoint lists them so you can obtain the `defaultBucketId` a database requires.

### List bucket configurations

```
GET /buckets
```

#### Query parameters

| Parameter       | Type   | Required | Default | Description                                              |
| --------------- | ------ | -------- | ------- | -------------------------------------------------------- |
| `maxItems`      | number | No       | `30000` | Maximum number of items to return                        |
| `pageSize`      | number | No       | `3000`  | Number of items per page                                 |
| `startingToken` | string | No       | `null`  | Pagination token from a previous response's `NextToken`   |

#### Response

| Field              | Type    | Description                                                                                                                                                                          |
| ------------------ | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `bucketId`         | string  | Identifier of the bucket configuration. This is the value to supply as `defaultBucketId` when creating or updating a database.                                                        |
| `bucketName`       | string  | Name of the Amazon S3 bucket used for asset storage.                                                                                                                                 |
| `baseAssetsPrefix` | string  | Base prefix within the bucket under which assets are stored. Empty when assets are stored at the bucket root.                                                                         |
| `isDefault`        | boolean | Whether this is the VAMS default asset bucket, which holds pipeline template data and execution-time run input and output under the `pipelines/` prefix. Exactly one bucket is default. |

```json
{
    "Items": [
        {
            "bucketId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "bucketName": "vams-assets-abc123",
            "baseAssetsPrefix": "assets/",
            "isDefault": true
        }
    ],
    "NextToken": null
}
```

`NextToken` is `null` on the last page.

:::note[Authorization is route-level only]
`bucket` is not a constraint object type, so no per-bucket constraint can be authored and this listing applies no entity-level filter. A role that is granted the `/buckets` route receives every registered bucket configuration, which is why the shipped permission templates grant it to administrator roles alone.
:::

#### Error responses

| Status | Description                      |
| ------ | -------------------------------- |
| `400`  | Invalid pagination token         |
| `403`  | Not authorized                   |
| `500`  | Internal server error            |

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
| `defaultBucketId`                 | string  | Yes      | UUID of a pre-configured S3 bucket and prefix combination. Obtain it from [List bucket configurations](#list-bucket-configurations).                                                                               |
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
| `defaultBucketId`                 | string  | No       | UUID of a pre-configured S3 bucket and prefix combination, from [List bucket configurations](#list-bucket-configurations). Must reference an existing bucket. |
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
