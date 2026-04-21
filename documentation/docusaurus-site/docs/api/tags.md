# Tags and Tag Types API

The Tags and Tag Types API allows you to manage the categorization system for assets. Tag types define categories (such as "Department" or "Classification"), and tags are individual values within those categories. Tags provide a flexible way to organize and filter assets across databases.

:::info[Authorization]
All endpoints require a valid JWT token in the `Authorization` header. Tags use the `tag` object type for Casbin enforcement, and tag types use `tagType`.
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

| Parameter       | Type   | Required | Default | Description                             |
| --------------- | ------ | -------- | ------- | --------------------------------------- |
| `maxItems`      | number | No       | `30000` | Maximum number of items to return       |
| `pageSize`      | number | No       | `3000`  | Number of items per page                |
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

| Status | Description           |
| ------ | --------------------- |
| `403`  | Not authorized        |
| `500`  | Internal server error |

---

### Create a tag type

Creates a new tag type.

```
POST /tag-types
```

#### Request body

| Field         | Type   | Required | Description                                                         |
| ------------- | ------ | -------- | ------------------------------------------------------------------- |
| `tagTypeName` | string | Yes      | Unique name for the tag type (1-256 chars)                          |
| `description` | string | No       | Description of the tag type                                         |
| `required`    | string | No       | Whether this tag type is required (`True`/`False`, default `False`) |

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

| Parameter   | Type   | Required | Description             |
| ----------- | ------ | -------- | ----------------------- |
| `tagTypeId` | string | Yes      | Tag type name to delete |

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

| Status | Description                            |
| ------ | -------------------------------------- |
| `400`  | Tag type is in use by one or more tags |
| `403`  | Not authorized                         |
| `404`  | Tag type not found                     |
| `500`  | Internal server error                  |

---

## Tags

Tags are individual values within a tag type. For example, the tag type "Department" might have tags "Engineering", "Marketing", and "Operations".

### List tags

Retrieves all tags. Tags from required tag types have `[R]` appended to their `tagTypeName`.

```
GET /tags
```

#### Query parameters

| Parameter       | Type   | Required | Default | Description                             |
| --------------- | ------ | -------- | ------- | --------------------------------------- |
| `maxItems`      | number | No       | `30000` | Maximum number of items to return       |
| `pageSize`      | number | No       | `3000`  | Number of items per page                |
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

| Field         | Type   | Required | Description                                       |
| ------------- | ------ | -------- | ------------------------------------------------- |
| `tagName`     | string | Yes      | Unique tag name (1-256 chars)                     |
| `tagTypeName` | string | Yes      | Tag type this tag belongs to (must already exist) |

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

| Parameter | Type   | Required | Description        |
| --------- | ------ | -------- | ------------------ |
| `tagId`   | string | Yes      | Tag name to delete |

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

| Status | Description           |
| ------ | --------------------- |
| `400`  | Invalid tag name      |
| `403`  | Not authorized        |
| `404`  | Tag not found         |
| `500`  | Internal server error |

---

## Related resources

-   [Asset Links API](asset-links.md) -- Manage relationships between assets with optional tags
-   [Assets API](assets.md) -- Manage the assets that tags are associated with
-   [Authorization API](auth.md) -- Configure permissions for tag operations
