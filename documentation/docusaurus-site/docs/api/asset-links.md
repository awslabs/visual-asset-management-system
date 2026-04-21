# Asset Links API

The Asset Links API allows you to define and manage relationships between assets. Two relationship types are supported: **parentChild** for hierarchical relationships and **related** for peer associations. Asset links support optional aliases and tags for additional context.

:::info[Authorization]
All endpoints require a valid JWT token in the `Authorization` header. Asset link operations enforce Casbin permissions on both linked asset objects.
:::

---

## Create an asset link

Creates a new relationship between two assets.

```
POST /asset-links
```

### Request body

| Field                 | Type   | Required | Description                                                                |
| --------------------- | ------ | -------- | -------------------------------------------------------------------------- |
| `fromAssetId`         | string | Yes      | Source asset ID                                                            |
| `fromAssetDatabaseId` | string | Yes      | Database ID of the source asset                                            |
| `toAssetId`           | string | Yes      | Target asset ID                                                            |
| `toAssetDatabaseId`   | string | Yes      | Database ID of the target asset                                            |
| `relationshipType`    | string | Yes      | `parentChild` or `related`                                                 |
| `assetLinkAliasId`    | string | No       | Alias identifier for the link (only valid for `parentChild` relationships) |
| `tags`                | array  | No       | Array of tag strings associated with this link                             |

### Request body example

```json
{
    "fromAssetId": "building-model",
    "fromAssetDatabaseId": "architecture-db",
    "toAssetId": "floor-plan-3",
    "toAssetDatabaseId": "architecture-db",
    "relationshipType": "parentChild",
    "assetLinkAliasId": "floor-3",
    "tags": ["structural", "active"]
}
```

### Response

```json
{
    "message": "Asset link created successfully",
    "assetLinkId": "link-abc123"
}
```

### Error responses

| Status | Description                                           |
| ------ | ----------------------------------------------------- |
| `400`  | Validation error, duplicate link, or assets not found |
| `403`  | Not authorized (requires permission on both assets)   |
| `500`  | Internal server error                                 |

---

## Get asset links for an asset

Retrieves all relationships for a specific asset, organized by relationship type.

```
GET /database/{databaseId}/assets/{assetId}/asset-links
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `assetId`    | string | Yes      | Asset identifier    |

### Query parameters

| Parameter       | Type    | Required | Default | Description                                                 |
| --------------- | ------- | -------- | ------- | ----------------------------------------------------------- |
| `childTreeView` | boolean | No       | `false` | When `true`, returns children as a recursive tree structure |

### Response (flat view)

```json
{
    "related": [
        {
            "assetId": "related-asset-1",
            "assetName": "Related Model",
            "databaseId": "architecture-db",
            "assetLinkId": "link-def456",
            "assetLinkAliasId": null
        }
    ],
    "parents": [
        {
            "assetId": "parent-building",
            "assetName": "Main Building",
            "databaseId": "architecture-db",
            "assetLinkId": "link-ghi789",
            "assetLinkAliasId": "wing-a"
        }
    ],
    "children": [
        {
            "assetId": "child-component",
            "assetName": "HVAC System",
            "databaseId": "architecture-db",
            "assetLinkId": "link-jkl012",
            "assetLinkAliasId": "hvac"
        }
    ],
    "unauthorizedCounts": {
        "related": 0,
        "parents": 0,
        "children": 1
    }
}
```

:::note[Unauthorized counts]
The `unauthorizedCounts` field shows how many linked assets the current user does not have permission to view. This allows the UI to indicate that additional relationships exist without exposing unauthorized data.
:::

### Response (tree view)

When `childTreeView=true`, the `children` field contains a recursive tree structure:

```json
{
    "children": [
        {
            "assetId": "floor-1",
            "assetName": "First Floor",
            "databaseId": "architecture-db",
            "assetLinkId": "link-abc",
            "assetLinkAliasId": "floor-1",
            "children": [
                {
                    "assetId": "room-101",
                    "assetName": "Conference Room A",
                    "databaseId": "architecture-db",
                    "assetLinkId": "link-def",
                    "assetLinkAliasId": null,
                    "children": []
                }
            ]
        }
    ]
}
```

---

## Get a single asset link

Retrieves details for a specific asset link.

```
GET /asset-links/single/{assetLinkId}
```

### Path parameters

| Parameter     | Type   | Required | Description           |
| ------------- | ------ | -------- | --------------------- |
| `assetLinkId` | string | Yes      | Asset link identifier |

### Response

```json
{
    "assetLink": {
        "assetLinkId": "link-abc123",
        "fromAssetId": "building-model",
        "fromAssetDatabaseId": "architecture-db",
        "toAssetId": "floor-plan-3",
        "toAssetDatabaseId": "architecture-db",
        "relationshipType": "parentChild",
        "assetLinkAliasId": "floor-3",
        "tags": ["structural", "active"]
    }
}
```

---

## Update an asset link

Updates the tags or alias of an existing asset link.

```
PUT /asset-links/{assetLinkId}
```

### Path parameters

| Parameter     | Type   | Required | Description           |
| ------------- | ------ | -------- | --------------------- |
| `assetLinkId` | string | Yes      | Asset link identifier |

### Request body

| Field              | Type   | Required | Description                                                                 |
| ------------------ | ------ | -------- | --------------------------------------------------------------------------- |
| `tags`             | array  | Yes      | Updated array of tag strings                                                |
| `assetLinkAliasId` | string | No       | Updated alias (only for `parentChild` links; set to empty string to remove) |

### Request body example

```json
{
    "tags": ["structural", "active", "reviewed"],
    "assetLinkAliasId": "floor-3-updated"
}
```

### Response

```json
{
    "message": "Asset link updated successfully"
}
```

### Error responses

| Status | Description                                                    |
| ------ | -------------------------------------------------------------- |
| `400`  | Validation error or alias conflict                             |
| `403`  | Not authorized (requires PUT permission on both linked assets) |
| `404`  | Asset link not found                                           |
| `500`  | Internal server error                                          |

---

## Delete an asset link

Deletes an asset link and all associated metadata.

```
DELETE /asset-links/{assetLinkId}
```

### Path parameters

| Parameter     | Type   | Required | Description                     |
| ------------- | ------ | -------- | ------------------------------- |
| `assetLinkId` | string | Yes      | Asset link identifier to delete |

:::note[Path parameter name]
In the API route, this parameter is named `relationId`, but it represents the asset link ID.
:::

### Response

```json
{
    "message": "Asset link deleted successfully"
}
```

### Error responses

| Status | Description                                                       |
| ------ | ----------------------------------------------------------------- |
| `400`  | Invalid parameters or linked assets no longer exist               |
| `403`  | Not authorized (requires DELETE permission on both linked assets) |
| `404`  | Asset link not found                                              |
| `500`  | Internal server error                                             |

---

## Related resources

-   [Assets API](assets.md) -- Manage the assets that links are associated with
-   [Tags API](tags.md) -- Manage tags that can be applied to asset links
-   [Authorization API](auth.md) -- Configure permissions for asset link operations
-   [Metadata API](metadata.md) -- Manage metadata on asset links
