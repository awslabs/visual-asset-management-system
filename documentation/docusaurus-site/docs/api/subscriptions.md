# Subscriptions API

The Subscriptions API allows you to manage email notification subscriptions for asset events. Users can subscribe to receive notifications when specific events occur on assets, such as when a new version is created.

:::info[Authorization]
All endpoints require a valid JWT token in the `Authorization` header. Subscription endpoints enforce Casbin authorization on the associated asset, evaluated for the database in the request.
:::

The same asset ID can exist in more than one database, so a subscription names its asset by `databaseId` and `entityId` together. Every request that names one subscription carries both.

---

## List subscriptions

Retrieves all subscriptions the current user has access to.

```
GET /subscriptions
```

### Query parameters

| Parameter       | Type   | Required | Default | Description                             |
| --------------- | ------ | -------- | ------- | --------------------------------------- |
| `maxItems`      | number | No       | `10000` | Maximum number of items to return       |
| `pageSize`      | number | No       | `10000` | Number of items per page                |
| `startingToken` | string | No       | `null`  | Pagination token from previous response |

### Response

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
`databaseId` is the database of the subscribed asset, stored with the subscription. The `entityValue` field contains the asset name, resolved at query time from the asset record.
:::

### Error responses

| Status | Description           |
| ------ | --------------------- |
| `403`  | Not authorized        |
| `500`  | Internal server error |

---

## Create a subscription

Creates a new subscription for an event on a specific entity, or adds subscribers to an existing subscription.

```
POST /subscriptions
```

### Request body

| Field         | Type   | Required | Description                                                                        |
| ------------- | ------ | -------- | ---------------------------------------------------------------------------------- |
| `eventName`   | string | Yes      | Event type. Must be `Asset Version Change`.                                        |
| `entityName`  | string | Yes      | Entity type. Must be `Asset`.                                                      |
| `databaseId`  | string | Yes      | Database ID of the asset to subscribe to                                           |
| `entityId`    | string | Yes      | Asset ID to subscribe to (any ID the asset API accepts, including dots and spaces) |
| `subscribers` | array  | Yes      | Array of user IDs to subscribe                                                     |

### Request body example

```json
{
    "eventName": "Asset Version Change",
    "entityName": "Asset",
    "databaseId": "architecture-db",
    "entityId": "building-model",
    "subscribers": ["user1@example.com", "user2@example.com"]
}
```

:::note[Subscriber email resolution]
VAMS resolves each subscriber's email from their user profile. If a subscriber does not have a valid email in their profile, and their user ID is not in email format, the request is rejected.
:::

### Response

```json
{
    "message": "success"
}
```

### Error responses

| Status | Description                                                                 |
| ------ | --------------------------------------------------------------------------- |
| `400`  | Invalid fields, subscriber already exists, or subscriber has no valid email |
| `403`  | Not authorized to modify subscriptions for this asset                       |
| `404`  | Asset not found                                                             |
| `500`  | Internal server error                                                       |

---

## Update a subscription

Updates the subscriber list for an existing subscription. Subscribers not in the new list are removed; new subscribers are added.

```
PUT /subscriptions
```

### Request body

Same structure as [Create a subscription](#create-a-subscription). The full subscriber list replaces the existing one.

### Request body example

```json
{
    "eventName": "Asset Version Change",
    "entityName": "Asset",
    "databaseId": "architecture-db",
    "entityId": "building-model",
    "subscribers": ["user1@example.com", "user3@example.com"]
}
```

### Response

```json
{
    "message": "success"
}
```

### Error responses

| Status | Description                                             |
| ------ | ------------------------------------------------------- |
| `400`  | Subscription does not exist or invalid subscriber email |
| `403`  | Not authorized                                          |
| `404`  | Asset not found                                         |
| `500`  | Internal server error                                   |

---

## Delete a subscription

Deletes a subscription and removes the associated SNS topic from the asset.

```
DELETE /subscriptions
```

### Request body

| Field         | Type   | Required | Description                  |
| ------------- | ------ | -------- | ---------------------------- |
| `eventName`   | string | Yes      | Event type                   |
| `entityName`  | string | Yes      | Entity type                  |
| `databaseId`  | string | Yes      | Database ID of the asset     |
| `entityId`    | string | Yes      | Asset ID                     |
| `subscribers` | array  | Yes      | Array of subscriber user IDs |

### Request body example

```json
{
    "eventName": "Asset Version Change",
    "entityName": "Asset",
    "databaseId": "architecture-db",
    "entityId": "building-model",
    "subscribers": ["user1@example.com"]
}
```

### Response

```json
{
    "message": "success"
}
```

### Error responses

| Status | Description            |
| ------ | ---------------------- |
| `400`  | Subscription not found |
| `403`  | Not authorized         |
| `404`  | Asset not found        |
| `500`  | Internal server error  |

---

## Check subscription status

Checks whether a specific user is subscribed to a specific asset's version change events.

```
POST /check-subscription
```

### Request body

| Field        | Type   | Required | Description                       |
| ------------ | ------ | -------- | --------------------------------- |
| `userId`     | string | Yes      | User ID to check                  |
| `databaseId` | string | Yes      | Database ID of the asset to check |
| `assetId`    | string | Yes      | Asset ID to check                 |

### Request body example

```json
{
    "userId": "user@example.com",
    "databaseId": "architecture-db",
    "assetId": "building-model"
}
```

### Response (subscribed)

```json
{
    "message": "success"
}
```

### Response (not subscribed)

```json
{
    "message": "Subscription doesn't exists."
}
```

### Error responses

| Status | Description                                            |
| ------ | ------------------------------------------------------ |
| `400`  | Missing or invalid `userId`, `databaseId` or `assetId` |
| `403`  | Not authorized to read this asset                      |
| `404`  | Asset not found                                        |
| `500`  | Internal server error                                  |

---

## Unsubscribe

Removes a user's subscription from an asset.

```
DELETE /unsubscribe
```

### Request body

| Field         | Type   | Required | Description                                                                        |
| ------------- | ------ | -------- | ---------------------------------------------------------------------------------- |
| `eventName`   | string | Yes      | Event type                                                                         |
| `entityName`  | string | Yes      | Entity type                                                                        |
| `databaseId`  | string | Yes      | Database ID of the asset                                                           |
| `entityId`    | string | Yes      | Asset ID                                                                           |
| `subscribers` | array  | Yes      | Array of subscriber user IDs. Only the first user ID in the array is unsubscribed. |

### Request body example

```json
{
    "eventName": "Asset Version Change",
    "entityName": "Asset",
    "databaseId": "architecture-db",
    "entityId": "building-model",
    "subscribers": ["user@example.com"]
}
```

### Response

```json
{
    "message": "success"
}
```

---

## Related resources

-   [Assets API](assets.md) -- Manage the assets that subscriptions monitor
-   [Asset Versions API](asset-versions.md) -- Version change events trigger subscription notifications
-   [Authorization API](auth.md) -- Configure permissions for subscription operations
