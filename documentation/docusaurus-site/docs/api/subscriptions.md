# Subscriptions and Databases API

This page documents the APIs for managing event subscriptions and databases. Subscriptions allow users to receive email notifications when specific events occur on assets. Databases are the top-level organizational containers in VAMS that hold assets, pipelines, and workflows.

:::info[Authorization]
All endpoints require a valid JWT token in the `Authorization` header. Subscription endpoints enforce Casbin authorization on the associated asset. Database endpoints enforce authorization using the `database` object type.
:::


---

## Subscriptions

Subscriptions enable email notifications for asset events. Currently, the supported event type is **Asset Version Change**, which notifies subscribers when a new version of an asset is created.

### List subscriptions

Retrieves all subscriptions the current user has access to.

```
GET /subscriptions
```

#### Query parameters

| Parameter       | Type   | Required | Default | Description                           |
|-----------------|--------|----------|---------|---------------------------------------|
| `maxItems`      | number | No       | `100`   | Maximum number of items to return     |
| `pageSize`      | number | No       | `100`   | Number of items per page              |
| `startingToken` | string | No       | `null`  | Pagination token from previous response |

#### Response

```json
{
  "message": {
    "Items": [
      {
        "eventName": "Asset Version Change",
        "entityName": "Asset",
        "entityId": "my-asset-id",
        "entityValue": "Building Model",
        "databaseId": "architecture-db",
        "subscribers": ["user1@example.com", "user2@example.com"]
      }
    ],
    "NextToken": null
  }
}
```

:::note
The `entityValue` field contains the asset name, and `databaseId` contains the asset's database. These are resolved at query time from the asset record.
:::


#### Error responses

| Status | Description                     |
|--------|---------------------------------|
| `403`  | Not authorized                  |
| `500`  | Internal server error           |

---

### Create a subscription

Creates a new subscription for an event on a specific entity, or adds subscribers to an existing subscription.

```
POST /subscriptions
```

#### Request body

| Field         | Type   | Required | Description                                                       |
|--------------|--------|----------|-------------------------------------------------------------------|
| `eventName`   | string | Yes      | Event type. Must be `Asset Version Change`.                       |
| `entityName`  | string | Yes      | Entity type. Must be `Asset`.                                     |
| `entityId`    | string | Yes      | Asset ID to subscribe to (3-63 chars, alphanumeric, hyphens, underscores) |
| `subscribers` | array  | Yes      | Array of user IDs to subscribe                                    |

#### Request body example

```json
{
  "eventName": "Asset Version Change",
  "entityName": "Asset",
  "entityId": "building-model",
  "subscribers": ["user1@example.com", "user2@example.com"]
}
```

:::note[Subscriber email resolution]
VAMS resolves each subscriber's email from their user profile. If a subscriber does not have a valid email in their profile, and their user ID is not in email format, the request is rejected.
:::


#### Response

```json
{
  "message": "success"
}
```

#### Error responses

| Status | Description                                                            |
|--------|------------------------------------------------------------------------|
| `400`  | Invalid fields, subscriber already exists, or subscriber has no valid email |
| `403`  | Not authorized to modify subscriptions for this asset                  |
| `500`  | Internal server error                                                  |

---

### Update a subscription

Updates the subscriber list for an existing subscription. Subscribers not in the new list are removed; new subscribers are added.

```
PUT /subscriptions
```

#### Request body

Same structure as [Create a subscription](#create-a-subscription). The full subscriber list replaces the existing one.

#### Request body example

```json
{
  "eventName": "Asset Version Change",
  "entityName": "Asset",
  "entityId": "building-model",
  "subscribers": ["user1@example.com", "user3@example.com"]
}
```

#### Response

```json
{
  "message": "success"
}
```

#### Error responses

| Status | Description                                            |
|--------|--------------------------------------------------------|
| `400`  | Subscription does not exist or invalid subscriber email |
| `403`  | Not authorized                                         |
| `500`  | Internal server error                                  |

---

### Delete a subscription

Deletes a subscription and removes the associated SNS topic from the asset.

```
DELETE /subscriptions
```

#### Request body

| Field         | Type   | Required | Description                     |
|--------------|--------|----------|---------------------------------|
| `eventName`   | string | Yes      | Event type                      |
| `entityName`  | string | Yes      | Entity type                     |
| `entityId`    | string | Yes      | Asset ID                        |
| `subscribers` | array  | Yes      | Array of subscriber user IDs    |

#### Request body example

```json
{
  "eventName": "Asset Version Change",
  "entityName": "Asset",
  "entityId": "building-model",
  "subscribers": ["user1@example.com"]
}
```

#### Response

```json
{
  "message": "success"
}
```

#### Error responses

| Status | Description                     |
|--------|---------------------------------|
| `400`  | Subscription not found          |
| `403`  | Not authorized                  |
| `500`  | Internal server error           |

---

### Check subscription status

Checks whether a specific user is subscribed to a specific asset's version change events.

```
POST /check-subscription
```

#### Request body

| Field    | Type   | Required | Description              |
|---------|--------|----------|--------------------------|
| `userId` | string | Yes      | User ID to check         |
| `assetId`| string | Yes      | Asset ID to check        |

#### Request body example

```json
{
  "userId": "user@example.com",
  "assetId": "building-model"
}
```

#### Response (subscribed)

```json
{
  "message": "success"
}
```

#### Response (not subscribed)

```json
{
  "message": "Subscription doesn't exists."
}
```

---

### Unsubscribe

Removes a user's subscription from an asset.

```
DELETE /unsubscribe
```

#### Request body

| Field         | Type   | Required | Description                     |
|--------------|--------|----------|---------------------------------|
| `eventName`   | string | Yes      | Event type                      |
| `entityName`  | string | Yes      | Entity type                     |
| `entityId`    | string | Yes      | Asset ID                        |
| `subscribers` | array  | Yes      | Array of subscriber user IDs to remove |

#### Request body example

```json
{
  "eventName": "Asset Version Change",
  "entityName": "Asset",
  "entityId": "building-model",
  "subscribers": ["user@example.com"]
}
```

#### Response

```json
{
  "message": "success"
}
```

---

## Databases

Databases are the top-level organizational containers in VAMS. Each database has an associated S3 bucket for asset storage. Assets, pipelines, and workflows are all scoped to a database.

### List all databases

Retrieves all databases.

```
GET /database
```

#### Query parameters

| Parameter       | Type   | Required | Default | Description                           |
|-----------------|--------|----------|---------|---------------------------------------|
| `maxItems`      | number | No       | `100`   | Maximum number of items to return     |
| `pageSize`      | number | No       | `100`   | Number of items per page              |
| `startingToken` | string | No       | `null`  | Pagination token from previous response |
| `showDeleted`   | string | No       | `false` | Include soft-deleted databases        |

#### Response

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

#### Error responses

| Status | Description                     |
|--------|---------------------------------|
| `403`  | Not authorized                  |
| `500`  | Internal server error           |

---

### Get a database

Retrieves a single database by its identifier.

```
GET /database/{databaseId}
```

#### Path parameters

| Parameter    | Type   | Required | Description              |
|-------------|--------|----------|--------------------------|
| `databaseId` | string | Yes      | Database identifier      |

#### Response

Returns a single database object in the same format as the items in the list response.

#### Error responses

| Status | Description                     |
|--------|---------------------------------|
| `400`  | Invalid `databaseId` format     |
| `403`  | Not authorized                  |
| `404`  | Database not found              |
| `500`  | Internal server error           |

---

### Create a database

Creates a new database and its associated S3 storage bucket.

```
POST /database
```

#### Request body

| Field           | Type   | Required | Description                                                |
|----------------|--------|----------|------------------------------------------------------------|
| `databaseId`    | string | Yes      | Unique database identifier (3-63 chars)                    |
| `databaseName`  | string | Yes      | Human-readable database name                               |
| `description`   | string | No       | Description of the database                                |

#### Request body example

```json
{
  "databaseId": "architecture-db",
  "databaseName": "Architecture Database",
  "description": "3D architectural models and floor plans"
}
```

#### Response

```json
{
  "message": "Succeeded"
}
```

#### Error responses

| Status | Description                     |
|--------|---------------------------------|
| `400`  | Validation error or database already exists |
| `403`  | Not authorized                  |
| `500`  | Internal server error           |

---

### Update a database

Updates database metadata.

```
PUT /database/{databaseId}
```

#### Path parameters

| Parameter    | Type   | Required | Description              |
|-------------|--------|----------|--------------------------|
| `databaseId` | string | Yes      | Database identifier      |

#### Request body

| Field          | Type   | Required | Description                     |
|---------------|--------|----------|---------------------------------|
| `databaseName` | string | No       | Updated database name           |
| `description`  | string | No       | Updated description             |

#### Request body example

```json
{
  "databaseName": "Architecture Database (v2)",
  "description": "Updated 3D architectural models"
}
```

#### Response

```json
{
  "message": "Succeeded"
}
```

---

### Delete a database

Soft-deletes a database.

```
DELETE /database/{databaseId}
```

#### Path parameters

| Parameter    | Type   | Required | Description              |
|-------------|--------|----------|--------------------------|
| `databaseId` | string | Yes      | Database identifier      |

:::warning[Dependency check]
A database cannot be deleted if it contains active assets, pipelines, or workflows. Remove all dependent resources before deleting the database.
:::


#### Response

```json
{
  "message": "Database deleted"
}
```

#### Error responses

| Status | Description                                                        |
|--------|--------------------------------------------------------------------|
| `400`  | Database has active assets, pipelines, or workflows                |
| `403`  | Not authorized                                                     |
| `404`  | Database not found                                                 |
| `500`  | Internal server error                                              |

---

## Related resources

- [Assets API](assets.md) -- Manage assets within databases
- [Pipelines API](pipelines.md) -- Define pipelines scoped to databases
- [Workflows API](workflows.md) -- Create workflows within databases
- [Authorization API](auth.md) -- Configure database-level access permissions
