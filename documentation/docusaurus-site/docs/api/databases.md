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
    "message": {
        "Items": [
            {
                "databaseId": "architecture-db",
                "databaseName": "Architecture Database",
                "description": "3D architectural models and floor plans",
                "assetCount": 42,
                "dateCreated": "\"March 15 2026 - 10:30:00\"",
                "dateUpdated": "\"March 16 2026 - 14:20:00\""
            }
        ],
        "NextToken": null
    }
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

Creates a new database and its associated S3 storage bucket.

```
POST /database
```

### Request body

| Field          | Type   | Required | Description                             |
| -------------- | ------ | -------- | --------------------------------------- |
| `databaseId`   | string | Yes      | Unique database identifier (3-63 chars) |
| `databaseName` | string | Yes      | Human-readable database name            |
| `description`  | string | No       | Description of the database             |

### Request body example

```json
{
    "databaseId": "architecture-db",
    "databaseName": "Architecture Database",
    "description": "3D architectural models and floor plans"
}
```

### Response

```json
{
    "message": "Succeeded"
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

### Request body

| Field          | Type   | Required | Description           |
| -------------- | ------ | -------- | --------------------- |
| `databaseName` | string | No       | Updated database name |
| `description`  | string | No       | Updated description   |

### Request body example

```json
{
    "databaseName": "Architecture Database (v2)",
    "description": "Updated 3D architectural models"
}
```

### Response

```json
{
    "message": "Succeeded"
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
