# Tags, Tag Types, and Asset Links API

This page documents the APIs for managing tags, tag types, and asset links. Tags provide a flexible categorization system for assets, organized under tag types. Asset links define relationships between assets such as parent-child hierarchies and peer associations.

:::info[Authorization]
All endpoints require a valid JWT token in the `Authorization` header. Tags use the `tag` object type for Casbin enforcement, tag types use `tagType`, and asset links enforce permissions on the linked asset objects.
:::


---

## Tag types

Tag types define categories for tags (e.g., "Department", "Classification", "Priority"). Tags are always associated with a tag type. A tag type can be marked as **required**, meaning every asset should have a tag of that type.

### List tag types

Retrieves all tag types with their associated tags.

```
GET /tag-types
```

#### Query parameters

| Parameter       | Type   | Required | Default | Description                           |
|-----------------|--------|----------|---------|---------------------------------------|
| `maxItems`      | number | No       | `30000` | Maximum number of items to return     |
| `pageSize`      | number | No       | `3000`  | Number of items per page              |
| `startingToken` | string | No       | `null`  | Pagination token from previous response |

#### Response

```json
{
  "message": {
    "Items": [
      {
        "tagTypeName": "Department",
        "description": "Organizational department",
        "required": "True",
        "tags": ["Engineering", "Marketing", "Operations"]
      },
      {
        "tagTypeName": "Classification",
        "description": "Data classification level",
        "required": "False",
        "tags": ["Public", "Internal", "Confidential"]
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

### Create a tag type

Creates a new tag type.

```
POST /tag-types
```

#### Request body

| Field          | Type   | Required | Description                                           |
|---------------|--------|----------|-------------------------------------------------------|
| `tagTypeName`  | string | Yes      | Unique name for the tag type (1-256 chars)            |
| `description`  | string | No       | Description of the tag type                           |
| `required`     | string | No       | Whether this tag type is required (`True`/`False`, default `False`) |

#### Request body example

```json
{
  "tagTypeName": "Priority",
  "description": "Asset priority level",
  "required": "False"
}
```

#### Response

```json
{
  "message": "Tag type created successfully"
}
```

---

### Update a tag type

Updates an existing tag type.

```
PUT /tag-types
```

#### Request body

Same structure as [Create a tag type](#create-a-tag-type). The `tagTypeName` identifies which tag type to update.

---

### Delete a tag type

Deletes a tag type.

```
DELETE /tag-types/{tagTypeId}
```

#### Path parameters

| Parameter   | Type   | Required | Description              |
|------------|--------|----------|--------------------------|
| `tagTypeId` | string | Yes      | Tag type name to delete  |

:::warning[In-use check]
A tag type cannot be deleted if any tags are currently assigned to it. Remove all tags of this type before deleting the tag type.
:::


#### Response

```json
{
  "success": true,
  "message": "Tag type 'Priority' deleted successfully",
  "tagTypeName": "Priority",
  "operation": "delete",
  "timestamp": "2026-03-15T10:30:00"
}
```

#### Error responses

| Status | Description                                            |
|--------|--------------------------------------------------------|
| `400`  | Tag type is in use by one or more tags                 |
| `403`  | Not authorized                                         |
| `404`  | Tag type not found                                     |
| `500`  | Internal server error                                  |

---

## Tags

Tags are individual values within a tag type. For example, the tag type "Department" might have tags "Engineering", "Marketing", and "Operations".

### List tags

Retrieves all tags. Tags from required tag types have `[R]` appended to their `tagTypeName`.

```
GET /tags
```

#### Query parameters

| Parameter       | Type   | Required | Default | Description                           |
|-----------------|--------|----------|---------|---------------------------------------|
| `maxItems`      | number | No       | `30000` | Maximum number of items to return     |
| `pageSize`      | number | No       | `3000`  | Number of items per page              |
| `startingToken` | string | No       | `null`  | Pagination token from previous response |

#### Response

```json
{
  "message": {
    "Items": [
      {
        "tagName": "Engineering",
        "tagTypeName": "Department [R]"
      },
      {
        "tagName": "Public",
        "tagTypeName": "Classification"
      }
    ],
    "NextToken": null
  }
}
```

---

### Create a tag

Creates a new tag.

```
POST /tags
```

#### Request body

| Field         | Type   | Required | Description                                      |
|--------------|--------|----------|--------------------------------------------------|
| `tagName`     | string | Yes      | Unique tag name (1-256 chars)                    |
| `tagTypeName` | string | Yes      | Tag type this tag belongs to (must already exist)|

#### Request body example

```json
{
  "tagName": "High Priority",
  "tagTypeName": "Priority"
}
```

#### Response

```json
{
  "message": "Tag created successfully"
}
```

---

### Update a tag

Updates an existing tag.

```
PUT /tags
```

#### Request body

Same structure as [Create a tag](#create-a-tag). The `tagName` identifies which tag to update.

---

### Delete a tag

Deletes a tag.

```
DELETE /tags/{tagId}
```

#### Path parameters

| Parameter | Type   | Required | Description              |
|----------|--------|----------|--------------------------|
| `tagId`   | string | Yes      | Tag name to delete       |

#### Response

```json
{
  "success": true,
  "message": "Tag High Priority deleted successfully",
  "tagName": "High Priority",
  "operation": "delete",
  "timestamp": "2026-03-15T10:30:00"
}
```

#### Error responses

| Status | Description                     |
|--------|---------------------------------|
| `400`  | Invalid tag name                |
| `403`  | Not authorized                  |
| `404`  | Tag not found                   |
| `500`  | Internal server error           |

---

## Asset links

Asset links define relationships between assets. Two relationship types are supported:

- **`parentChild`** -- A hierarchical relationship where one asset is the parent and another is the child. Supports an optional `assetLinkAliasId` for named relationship instances.
- **`related`** -- A peer association between two assets with no hierarchy.

### Create an asset link

Creates a new relationship between two assets.

```
POST /asset-links
```

#### Request body

| Field                  | Type   | Required | Description                                                               |
|-----------------------|--------|----------|---------------------------------------------------------------------------|
| `fromAssetId`          | string | Yes      | Source asset ID                                                           |
| `fromAssetDatabaseId`  | string | Yes      | Database ID of the source asset                                           |
| `toAssetId`            | string | Yes      | Target asset ID                                                           |
| `toAssetDatabaseId`    | string | Yes      | Database ID of the target asset                                           |
| `relationshipType`     | string | Yes      | `parentChild` or `related`                                                |
| `assetLinkAliasId`     | string | No       | Alias identifier for the link (only valid for `parentChild` relationships)|
| `tags`                 | array  | No       | Array of tag strings associated with this link                            |

#### Request body example

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

#### Response

```json
{
  "message": "Asset link created successfully",
  "assetLinkId": "link-abc123"
}
```

#### Error responses

| Status | Description                                                        |
|--------|--------------------------------------------------------------------|
| `400`  | Validation error, duplicate link, or assets not found              |
| `403`  | Not authorized (requires permission on both assets)                |
| `500`  | Internal server error                                              |

---

### Get asset links for an asset

Retrieves all relationships for a specific asset, organized by relationship type.

```
GET /database/{databaseId}/assets/{assetId}/asset-links
```

#### Path parameters

| Parameter    | Type   | Required | Description              |
|-------------|--------|----------|--------------------------|
| `databaseId` | string | Yes      | Database identifier      |
| `assetId`    | string | Yes      | Asset identifier         |

#### Query parameters

| Parameter       | Type    | Required | Default | Description                                              |
|-----------------|---------|----------|---------|----------------------------------------------------------|
| `childTreeView` | boolean | No       | `false` | When `true`, returns children as a recursive tree structure |

#### Response (flat view)

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


#### Response (tree view)

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

### Get a single asset link

Retrieves details for a specific asset link.

```
GET /asset-links/single/{assetLinkId}
```

#### Path parameters

| Parameter     | Type   | Required | Description              |
|--------------|--------|----------|--------------------------|
| `assetLinkId` | string | Yes      | Asset link identifier    |

#### Response

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

### Update an asset link

Updates the tags or alias of an existing asset link.

```
PUT /asset-links/{assetLinkId}
```

#### Path parameters

| Parameter     | Type   | Required | Description              |
|--------------|--------|----------|--------------------------|
| `assetLinkId` | string | Yes      | Asset link identifier    |

#### Request body

| Field              | Type   | Required | Description                                                                |
|-------------------|--------|----------|----------------------------------------------------------------------------|
| `tags`             | array  | Yes      | Updated array of tag strings                                               |
| `assetLinkAliasId` | string | No       | Updated alias (only for `parentChild` links; set to empty string to remove)|

#### Request body example

```json
{
  "tags": ["structural", "active", "reviewed"],
  "assetLinkAliasId": "floor-3-updated"
}
```

#### Response

```json
{
  "message": "Asset link updated successfully"
}
```

#### Error responses

| Status | Description                                                        |
|--------|--------------------------------------------------------------------|
| `400`  | Validation error or alias conflict                                 |
| `403`  | Not authorized (requires PUT permission on both linked assets)     |
| `404`  | Asset link not found                                               |
| `500`  | Internal server error                                              |

---

### Delete an asset link

Deletes an asset link and all associated metadata.

```
DELETE /asset-links/{assetLinkId}
```

#### Path parameters

| Parameter     | Type   | Required | Description                     |
|--------------|--------|----------|---------------------------------|
| `assetLinkId` | string | Yes      | Asset link identifier to delete |

:::note[Path parameter name]
In the API route, this parameter is named `relationId`, but it represents the asset link ID.
:::


#### Response

```json
{
  "message": "Asset link deleted successfully"
}
```

#### Error responses

| Status | Description                                                        |
|--------|--------------------------------------------------------------------|
| `400`  | Invalid parameters or linked assets no longer exist                |
| `403`  | Not authorized (requires DELETE permission on both linked assets)  |
| `404`  | Asset link not found                                               |
| `500`  | Internal server error                                              |

---

## Related resources

- [Assets API](assets.md) -- Manage the assets that tags and links are associated with
- [Authorization API](auth.md) -- Configure permissions for tag and asset link operations
- [Metadata API](metadata.md) -- Manage metadata on assets and asset links
