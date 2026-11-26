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

| Parameter       | Type   | Required | Default | Description                                       |
| --------------- | ------ | -------- | ------- | ------------------------------------------------- |
| `maxItems`      | number | No       | `10000` | Maximum number of items to return (up to `30000`) |
| `pageSize`      | number | No       | `10000` | Number of items per page (up to `10000`)          |
| `startingToken` | string | No       | `null`  | Pagination token from previous response           |
| `showDeleted`   | string | No       | `false` | Include soft-deleted databases                    |

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
            "restrictFileUploadsToExtensions": "",
            "complianceSchemaName": "survey-compliance",
            "complianceAutoEval": true
        }
    ],
    "NextToken": null
}
```

`complianceSchemaName` and `complianceAutoEval` describe the database's compliance schema binding as written by `PUT /compliance/bind/{databaseId}` (see [Compliance API — Bind a schema to a database](compliance.md#bind-a-schema-to-a-database)); both are `null` on a database with no binding, so a client can tell "unbound" from "automatic evaluation off". They are read through the database routes only — the binding is changed through the compliance routes.

`assetCount` counts the database's active (non-archived) assets and can exceed the assets the caller can list or open. Search totals count only records the caller can open (see [Search API — Counts and permissions](search.md#counts-and-permissions)).

### Error responses

| Status | Description                   |
| ------ | ----------------------------- |
| `400`  | Invalid pagination parameters |
| `403`  | Not authorized                |
| `500`  | Internal server error         |

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

Returns a single database object in the same format as the items in the list response, including `complianceSchemaName` and `complianceAutoEval` (`null` when the database has no compliance schema binding). `assetCount` counts the database's active (non-archived) assets and can exceed the assets the caller can list or open. Search totals count only records the caller can open (see [Search API — Counts and permissions](search.md#counts-and-permissions)).

### Error responses

| Status | Description                 |
| ------ | --------------------------- |
| `400`  | Invalid `databaseId` format |
| `403`  | Not authorized              |
| `404`  | Database not found          |
| `500`  | Internal server error       |

---

## Bucket configurations

A database is created against a **bucket configuration** — a registered pairing of an Amazon S3 bucket and a base prefix. Bucket configurations are registered at deployment time from the `app.assetBuckets` deployment configuration, covering both the bucket the deployment creates and any external buckets it is given, and cannot be created, changed, or removed through the API. This endpoint lists them so you can obtain the `defaultBucketId` a database requires. A registration whose entry was removed from `app.assetBuckets` is kept as retired and is not listed, and neither is a bucket registered at run time that is pending or disabled. Only the configurations your role may read through a `bucket` permission constraint are listed.

### List bucket configurations

```
GET /buckets
```

#### Query parameters

| Parameter       | Type   | Required | Default | Description                                             |
| --------------- | ------ | -------- | ------- | ------------------------------------------------------- |
| `maxItems`      | number | No       | `3000`  | Maximum number of items to return (up to `30000`)       |
| `pageSize`      | number | No       | `3000`  | Number of items per page (up to `10000`)                |
| `startingToken` | string | No       | `null`  | Pagination token from a previous response's `NextToken` |

#### Response

| Field              | Type    | Description                                                                                                                                                                                       |
| ------------------ | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bucketId`         | string  | Identifier of the bucket configuration. This is the value to supply as `defaultBucketId` when creating or updating a database.                                                                    |
| `bucketName`       | string  | Name of the Amazon S3 bucket used for asset storage.                                                                                                                                              |
| `baseAssetsPrefix` | string  | Base prefix within the bucket under which assets are stored. Empty when assets are stored at the bucket root.                                                                                     |
| `isDefault`        | boolean | Whether this is the VAMS default asset bucket, which holds pipeline template data and execution-time run input and output under the `pipelines/` prefix. Exactly one bucket is default.           |
| `bucketRegion`     | string  | The AWS Region the bucket is in; presigned URLs and Amazon S3 requests for the bucket are signed for it. `null` on a row written without a Region, which is the deployment Region.                |
| `bucketAccountId`  | string  | The 12-digit AWS account that owns the bucket when it is not the deployment account; `null` otherwise.                                                                                            |
| `isAccelerated`    | boolean | Whether S3 Transfer Acceleration is on for the bucket. Uploads and downloads for an accelerated bucket use its accelerate endpoint. `false` when the registration does not say.                   |
| `source`           | string  | Where the configuration comes from: `static` (the deployment configuration). `runtime` is reserved for buckets registered at run time.                                                            |
| `status`           | string  | The state of the configuration. Only active configurations are listed, so the value is `active`; a retired one, and a bucket registered at run time that is `pending` or `disabled`, is left out. |

```json
{
    "Items": [
        {
            "bucketId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "bucketName": "vams-assets-abc123",
            "baseAssetsPrefix": "assets/",
            "isDefault": true,
            "bucketRegion": "us-west-2",
            "bucketAccountId": null,
            "isAccelerated": true,
            "source": "static",
            "status": "active"
        },
        {
            "bucketId": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
            "bucketName": "east-coast-scans",
            "baseAssetsPrefix": "",
            "isDefault": false,
            "bucketRegion": "us-east-1",
            "bucketAccountId": "222222222222",
            "isAccelerated": false,
            "source": "static",
            "status": "active"
        }
    ],
    "NextToken": null
}
```

`NextToken` is `null` on the last page.

:::note[Authorization]
The route itself is granted by an `api` constraint on `/buckets`, which the shipped permission templates grant to administrator roles alone. Each configuration is then listed only when the role may `GET` it through a `bucket` constraint (fields `bucketId`, `bucketName`, `bucketRegion`, `source`). A role without a `bucket` constraint gets an empty list. The seeded `admin` role and every shipped role template read every configuration (the `deny-tagged-assets.json` overlay adds no `bucket` constraint). A page can hold fewer items than its size; follow `NextToken` until it is `null`.
:::

#### Error responses

| Status | Description              |
| ------ | ------------------------ |
| `400`  | Invalid pagination token |
| `403`  | Not authorized           |
| `500`  | Internal server error    |

---

## Create a database

Creates a new database associated with a pre-configured S3 bucket and prefix.

```
POST /database
```

### Request body

| Field                             | Type    | Required | Description                                                                                                                                                                                                                                                                                               |
| --------------------------------- | ------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `databaseId`                      | string  | Yes      | Unique database identifier (4-63 chars, alphanumeric plus `-` and `_`). Cannot be `GLOBAL` or a reserved S3 keyword (`pipeline(s)`, `preview(s)`, `temp-upload(s)`, `workspace(s)`), matched case-insensitively.                                                                                          |
| `description`                     | string  | Yes      | Description of the database (4-256 chars).                                                                                                                                                                                                                                                                |
| `defaultBucketId`                 | string  | Yes      | UUID of a pre-configured S3 bucket and prefix combination. Obtain it from [List bucket configurations](#list-bucket-configurations). The role must be allowed to `GET` it through a `bucket` constraint; a retired registration is refused.                                                               |
| `restrictMetadataOutsideSchemas`  | boolean | No       | When `true`, user and API metadata writes may add only keys defined in applied schemas. Keys already stored on a record pass; pipeline write-backs are not restricted. Defaults to `false`.                                                                                                               |
| `restrictFileUploadsToExtensions` | string  | No       | Comma-separated list of allowed file extensions (e.g., `.jpg,.png,.pdf`). Each starts with a dot and can contain letters, digits, `_` and further dots (`.x_t`, `.tar.gz`); a file is allowed when its name ends with one, case-insensitively. Use `.all` or leave blank to allow all. Defaults to empty. |

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

An unknown bucket id, one that is not active (retired, or a bucket registered at run time that is pending or disabled) and one the role may not read all get the same `400` with the body `{"message": "Bucket ID not found or not available"}`, so the answer does not tell them apart. [Update a database](#update-a-database) answers a move to such a bucket the same way.

### Error responses

| Status | Description                                                                                         |
| ------ | --------------------------------------------------------------------------------------------------- |
| `400`  | Validation error, database already exists, or the bucket is unknown, retired or not readable to you |
| `403`  | Not authorized                                                                                      |
| `500`  | Internal server error                                                                               |

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

| Field                             | Type    | Required | Description                                                                                                                                                                                                                                                                                                                                              |
| --------------------------------- | ------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `description`                     | string  | No       | Updated description (4-256 chars).                                                                                                                                                                                                                                                                                                                       |
| `defaultBucketId`                 | string  | No       | UUID of a pre-configured S3 bucket and prefix combination, from [List bucket configurations](#list-bucket-configurations). Must reference an existing bucket configuration. Moving the database to another one needs `GET` on it through a `bucket` constraint, and a retired one is refused; the bucket the database already uses is not checked again. |
| `restrictMetadataOutsideSchemas`  | boolean | No       | When `true`, user and API metadata writes may add only keys defined in applied schemas. Keys already stored on a record pass; pipeline write-backs are not restricted.                                                                                                                                                                                   |
| `restrictFileUploadsToExtensions` | string  | No       | Comma-separated list of allowed file extensions (e.g., `.jpg,.png,.pdf`). Each starts with a dot and can contain letters, digits, `_` and further dots (`.x_t`, `.tar.gz`); a file is allowed when its name ends with one, case-insensitively. Use `.all` or leave blank to allow all.                                                                   |

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

A caller who may call this route but is not permitted to update the database itself receives `403`, and nothing is written.

### Error responses

| Status | Description                                                                                                                                                                                           |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Validation error, no updatable field supplied, database not found, or the bucket is unknown, not active or not readable to you (`{"message": "Bucket ID not found or not available"}`, as for create) |
| `403`  | Not authorized                                                                                                                                                                                        |
| `500`  | Internal server error                                                                                                                                                                                 |

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

The dependency check runs after the database is found and the caller is authorized to delete it, so a caller who is not authorized receives `403`, and a database that does not exist returns `404`, whatever the database contains.

### Response

```json
{
    "message": "Database deleted"
}
```

### Error responses

| Status | Description                                                                                                                                     |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Database has active assets, pipelines, or workflows, or an unexpected error stopped the delete (`VAMS General Error: Error deleting database.`) |
| `403`  | Not authorized                                                                                                                                  |
| `404`  | Database not found                                                                                                                              |
| `500`  | Internal server error                                                                                                                           |
| `503`  | Compliance is still building an index after an update; try again in a few minutes                                                               |

The `503` occurs only right after an update that adds the compliance evaluation table's `PipelineRuleWaitIndex`, while Amazon DynamoDB still builds that index: the delete changes nothing and works once the index is active (see [v2.6 to v2.7](../deployment/update-the-solution.md#v26-to-v27)).

A delete that fails after it has started leaves the database in place with its compliance data readable and changeable, as before the delete. Compliance cascades awaiting approval and pipeline rules waiting to start that the delete already closed stay closed.

---

## Related resources

-   [Assets API](assets.md) -- Manage assets within databases
-   [Pipelines API](pipelines.md) -- Define pipelines scoped to databases
-   [Workflows API](workflows.md) -- Create workflows within databases
-   [Authorization API](auth.md) -- Configure database-level access permissions
