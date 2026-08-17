# Tags and Tag Types API

The Tags and Tag Types API allows you to manage the categorization system for assets. Tag types define categories (such as "Department" or "Classification"), and tags are individual values within those categories. Tags provide a flexible way to organize and filter assets across databases.

:::info[Authorization]
All endpoints require a valid JWT token in the `Authorization` header. Tags use the `tag` object type for Casbin enforcement, and tag types use `tagType`.
:::

---

## Tag scope

Tags and tag types are either **global** (available in every database) or **scoped to a single database**. Scope is set by the `databaseId` field: omit it or use `GLOBAL` for a global tag, or provide a database ID to scope the tag to that database. A tag's scope is fixed once the tag is created.

Tag and tag type names are unique **per database**, not globally: the same name may exist independently in different databases (for example, a `Status` tag in one database and a separate `Status` tag in another). Across scopes the rule is asymmetric:

-   Creating a **GLOBAL** entry for a name a database already uses **succeeds**, and the response carries a `warnings` array noting that both entries will appear on asset forms until the database-specific one is removed.
-   Creating a **database-specific** entry for a name a GLOBAL entry already uses is **rejected** with `400`. A database may not shadow the shared vocabulary.

An asset resolves its tag names within its own database plus GLOBAL, so while a name exists in both scopes an asset in that database sees both entries.

### Listing by scope

`GET /tags` and `GET /tag-types` accept optional query parameters that filter the results by scope:

| Parameter    | Value          | Result                                                    |
| ------------ | -------------- | --------------------------------------------------------- |
| `databaseId` | a database ID  | Only the tags scoped to that database (global tags excluded) |
| `scope`      | `global`       | Global tags only                                          |
| `scope`      | `all`          | Every tag the caller is permitted to see                  |

With no parameter, the response contains the tags visible to the caller under their permissions.

### Creating a scoped tag

Include `databaseId` in the request body to scope a tag to a database; omit it (or set it to `GLOBAL`) for a global tag. The referenced database must exist, and a tag's tag type must live in the tag's own scope — a GLOBAL tag requires a GLOBAL tag type, and a database-scoped tag requires a tag type in that same database (a GLOBAL tag type is not accepted). The name must be free within the target scope: creation is rejected if the same database already uses the name, or if the name belongs to a GLOBAL tag and you are creating a database-specific one. Creating a GLOBAL tag for a name a database already uses succeeds and returns a `warnings` array. A database literally named `GLOBAL` (in any letter case) is reserved and cannot be used as a scope target.

---

## Tag types

Tag types define categories for tags (e.g., "Department", "Classification", "Priority"). Tags are always associated with a tag type. A tag type can be marked as **required**, meaning every asset should have a tag of that type.

A required tag type is enforced only while it has tags. Asset creation and asset updates evaluate the required tag types in the asset's own database plus `GLOBAL`, and skip any that have no tags in those scopes — an empty required tag type would otherwise reject every asset, because no tag exists that could satisfy it.

### List tag types

Retrieves all tag types with their associated tags.

```
GET /tag-types
```

#### Query parameters

| Parameter       | Type   | Required | Default | Description                                                                            |
| --------------- | ------ | -------- | ------- | -------------------------------------------------------------------------------------- |
| `maxItems`      | number | No       | `30000` | Maximum number of items to return                                                      |
| `pageSize`      | number | No       | `3000`  | Number of items per page                                                               |
| `startingToken` | string | No       | `null`  | Pagination token from previous response                                                |
| `databaseId`    | string | No       | `null`  | Return only the tag types scoped to this database (global tag types excluded)          |
| `scope`         | string | No       | `null`  | `global` returns only global tag types; `all` returns every tag type the caller may see |

See [Tag scope](#tag-scope) for how these parameters filter results.

#### Response

```json
{
    "message": {
        "Items": [
            {
                "tagTypeName": "Department",
                "description": "Organizational department",
                "required": "True",
                "tags": ["Engineering", "Marketing", "Operations"],
                "databaseId": "GLOBAL"
            },
            {
                "tagTypeName": "Classification",
                "description": "Data classification level",
                "required": "False",
                "tags": ["Public", "Internal", "Confidential"],
                "databaseId": "factory-db"
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

| Field         | Type   | Required | Description                                                                          |
| ------------- | ------ | -------- | ------------------------------------------------------------------------------------ |
| `tagTypeName` | string | Yes      | Tag type name, unique per database (1-256 chars)                                     |
| `description` | string | No       | Description of the tag type                                                          |
| `required`    | string | No       | Whether this tag type is required (`True`/`False`, default `False`)                  |
| `databaseId`  | string | No       | Scope of the tag type. Omit or use `GLOBAL` for a global tag type; a database ID scopes it to that database. Immutable after creation. |

#### Request body example

```json
{
    "tagTypeName": "Priority",
    "description": "Asset priority level",
    "required": "False",
    "databaseId": "factory-db"
}
```

#### Response

```json
{
    "success": true,
    "message": "Tag type created successfully",
    "tagTypeName": "Priority",
    "operation": "create",
    "timestamp": "2026-01-15T10:30:00.000Z"
}
```

A create that succeeds with an advisory carries a `warnings` array — currently returned when a GLOBAL tag type is created for a name a database already uses:

```json
{
    "success": true,
    "message": "Tag type 'Priority' created successfully",
    "tagTypeName": "Priority",
    "operation": "create",
    "timestamp": "2026-01-15T10:30:00.000Z",
    "warnings": [
        "This name is also used by a database-specific tag type. Asset forms will list both entries until the database-specific tag type is removed."
    ]
}
```

---

### Update a tag type

Updates an existing tag type.

```
PUT /tag-types
```

#### Request body

Same structure as [Create a tag type](#create-a-tag-type). The `tagTypeName` identifies which tag type to update. A tag type's scope (`databaseId`) is fixed at creation and cannot be changed by an update.

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

| Parameter       | Type   | Required | Default | Description                                                                       |
| --------------- | ------ | -------- | ------- | --------------------------------------------------------------------------------- |
| `maxItems`      | number | No       | `30000` | Maximum number of items to return                                                 |
| `pageSize`      | number | No       | `3000`  | Number of items per page                                                          |
| `startingToken` | string | No       | `null`  | Pagination token from previous response                                           |
| `databaseId`    | string | No       | `null`  | Return only the tags scoped to this database (global tags excluded)                |
| `scope`         | string | No       | `null`  | `global` returns only global tags; `all` returns every tag the caller may see     |

See [Tag scope](#tag-scope) for how these parameters filter results.

#### Response

```json
{
    "message": {
        "Items": [
            {
                "tagName": "Engineering",
                "tagTypeName": "Department [R]",
                "databaseId": "GLOBAL"
            },
            {
                "tagName": "Public",
                "tagTypeName": "Classification",
                "databaseId": "factory-db"
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

| Field         | Type   | Required | Description                                                                          |
| ------------- | ------ | -------- | ------------------------------------------------------------------------------------ |
| `tagName`     | string | Yes      | Tag name, unique per database (1-256 chars)                                          |
| `tagTypeName` | string | Yes      | Tag type this tag belongs to (must already exist)                                    |
| `databaseId`  | string | No       | Scope of the tag. Omit or use `GLOBAL` for a global tag; a database ID scopes it to that database. Immutable after creation. |

The referenced database must exist, and a tag's tag type must live in the tag's own scope — a GLOBAL tag requires a GLOBAL tag type, and a database-scoped tag requires a tag type in that same database (a GLOBAL tag type is not accepted).

#### Request body example

```json
{
    "tagName": "High Priority",
    "tagTypeName": "Priority",
    "databaseId": "factory-db"
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

Same structure as [Create a tag](#create-a-tag). The `tagName` identifies which tag to update. A tag's scope (`databaseId`) is fixed at creation and cannot be changed by an update.

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
