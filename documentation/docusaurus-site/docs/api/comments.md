# Comments API

The Comments API attaches free-text comments to a specific version of an asset. Comments support asset review workflows: a reviewer records an observation against the version they inspected, and the comment stays bound to that version as the asset evolves.

:::info[Authorization]
All endpoints require a valid JWT token in the `Authorization` header. Comment endpoints enforce Casbin authorization against the **owning asset** using the `asset` object type — there is no separate comment object type. A caller who can read an asset can read its comments, and a caller who can write an asset can add comments to it.
:::

---

## Concepts

-   **Comment identity**: A comment is identified by the asset it belongs to plus a composite `assetVersionId:commentId` key. Both halves are supplied by the caller as a single colon-joined path parameter.
-   **Ownership**: The user who creates a comment becomes its owner. Only the owner can edit or delete it, regardless of their asset permissions.
-   **Soft delete**: A deleted comment is moved to a parallel `#deleted` partition rather than removed. It stops appearing in the asset's comment listings.
-   **Version scope**: Every comment is attached to an asset version. Listing by version returns the comments recorded against that version alone.

### Comment fields

| Field                      | Type   | Description                                                            |
| -------------------------- | ------ | ---------------------------------------------------------------------- |
| `assetId`                  | string | Asset the comment belongs to.                                          |
| `assetVersionId:commentId` | string | Composite identifier: the asset version and the comment ID, colon-joined. |
| `commentBody`              | string | Comment text.                                                          |
| `commentOwnerID`           | string | User ID of the comment's creator.                                      |
| `commentOwnerUsername`     | string | Display identity of the creator.                                       |
| `dateCreated`              | string | ISO 8601 creation timestamp.                                           |
| `dateEdited`               | string | ISO 8601 timestamp of the last edit. Present only on an edited comment. |

---

## Endpoints

### List comments for an asset

`GET /comments/assets/{assetId}`

Retrieves every comment on an asset, across all of its versions, newest first.

**Request Parameters:**

| Parameter       | Location | Type    | Required | Description                             |
| --------------- | -------- | ------- | -------- | --------------------------------------- |
| `assetId`       | path     | string  | Yes      | Asset identifier.                       |
| `maxItems`      | query    | integer | No       | Maximum number of comments to return.   |
| `pageSize`      | query    | integer | No       | Number of comments per page.            |
| `startingToken` | query    | string  | No       | Pagination token.                       |

**Response:**

```json
{
    "message": [
        {
            "assetId": "asset-001",
            "assetVersionId:commentId": "2:c1a2b3c4-d5e6-7890-abcd-ef1234567890",
            "commentBody": "The north facade needs a higher-resolution texture.",
            "commentOwnerID": "user@example.com",
            "commentOwnerUsername": "user@example.com",
            "dateCreated": "2026-03-15T10:30:00.000000Z"
        }
    ]
}
```

`message` is the comment array itself. The response carries no continuation token, so `maxItems` bounds the whole result set for a call rather than opening a page sequence.

**Error Responses:**

| Status | Description                                                                  |
| ------ | ---------------------------------------------------------------------------- |
| `400`  | Invalid `assetId`, or an `assetId` that resolves to more than one live asset. |
| `403`  | Not authorized to read this asset.                                           |
| `404`  | Asset not found.                                                             |
| `405`  | Method not allowed.                                                          |
| `500`  | Internal server error.                                                       |

---

### List comments for an asset version

`GET /comments/assets/{assetId}/assetVersionId/{assetVersionId}`

Retrieves the comments recorded against one version of an asset, newest first.

**Request Parameters:**

| Parameter        | Location | Type    | Required | Description                           |
| ---------------- | -------- | ------- | -------- | ------------------------------------- |
| `assetId`        | path     | string  | Yes      | Asset identifier.                     |
| `assetVersionId` | path     | string  | Yes      | Asset version identifier.             |
| `maxItems`       | query    | integer | No       | Maximum number of comments to return. |
| `pageSize`       | query    | integer | No       | Number of comments per page.          |
| `startingToken`  | query    | string  | No       | Pagination token.                     |

**Response:**

Same shape as [List comments for an asset](#list-comments-for-an-asset), restricted to the named version.

**Error Responses:**

| Status | Description                                                                  |
| ------ | ---------------------------------------------------------------------------- |
| `400`  | Invalid `assetId`, or an `assetId` that resolves to more than one live asset. |
| `403`  | Not authorized to read this asset.                                           |
| `404`  | Asset not found.                                                             |
| `500`  | Internal server error.                                                       |

---

### Get a comment

`GET /comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}`

Retrieves a single comment.

**Request Parameters:**

| Parameter                  | Location | Type   | Required | Description                                                        |
| -------------------------- | -------- | ------ | -------- | ------------------------------------------------------------------ |
| `assetId`                  | path     | string | Yes      | Asset identifier.                                                  |
| `assetVersionId:commentId` | path     | string | Yes      | Asset version and comment ID, colon-joined (for example `2:c1a2b3c4`). |

**Response:**

```json
{
    "message": {
        "assetId": "asset-001",
        "assetVersionId:commentId": "2:c1a2b3c4-d5e6-7890-abcd-ef1234567890",
        "commentBody": "The north facade needs a higher-resolution texture.",
        "commentOwnerID": "user@example.com",
        "commentOwnerUsername": "user@example.com",
        "dateCreated": "2026-03-15T10:30:00.000000Z"
    }
}
```

A comment that does not exist returns `200` with an empty `message` object rather than a `404`.

**Error Responses:**

| Status | Description                                                                  |
| ------ | ---------------------------------------------------------------------------- |
| `400`  | Invalid `assetId` or comment ID, or a malformed composite path parameter.     |
| `403`  | Not authorized to read this asset.                                           |
| `404`  | Asset not found.                                                             |
| `500`  | Internal server error.                                                       |

---

### Add a comment

`POST /comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}`

Adds a comment to a version of an asset. The caller supplies the comment ID as the second half of the composite path parameter, and becomes the comment's owner.

**Request Parameters:**

| Parameter                  | Location | Type   | Required | Description                                        |
| -------------------------- | -------- | ------ | -------- | -------------------------------------------------- |
| `assetId`                  | path     | string | Yes      | Asset identifier.                                  |
| `assetVersionId:commentId` | path     | string | Yes      | Asset version and the new comment's ID, colon-joined. |

**Request Body:**

| Field         | Type   | Required | Description                              |
| ------------- | ------ | -------- | ---------------------------------------- |
| `commentBody` | string | Yes      | Comment text, up to 16,384 characters.   |

```json
{
    "commentBody": "The north facade needs a higher-resolution texture."
}
```

**Response:**

```json
{
    "message": "Succeeded"
}
```

**Error Responses:**

| Status | Description                                                                                          |
| ------ | ---------------------------------------------------------------------------------------------------- |
| `400`  | Missing or invalid path parameters, a missing or over-length `commentBody`, a comment that already exists, or an `assetId` that resolves to more than one live asset. |
| `403`  | Not authorized to write this asset.                                                                  |
| `404`  | Asset not found.                                                                                     |
| `500`  | Internal server error.                                                                               |

---

### Edit a comment

`PUT /comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}`

Replaces a comment's text and records a `dateEdited` timestamp. Only the comment's owner can edit it.

**Request Parameters:**

| Parameter                  | Location | Type   | Required | Description                                        |
| -------------------------- | -------- | ------ | -------- | -------------------------------------------------- |
| `assetId`                  | path     | string | Yes      | Asset identifier.                                  |
| `assetVersionId:commentId` | path     | string | Yes      | Asset version and comment ID, colon-joined.        |

**Request Body:**

| Field         | Type   | Required | Description                                       |
| ------------- | ------ | -------- | ------------------------------------------------- |
| `commentBody` | string | Yes      | Replacement comment text, up to 16,384 characters. |

**Response:**

```json
{
    "message": "Succeeded"
}
```

**Error Responses:**

| Status | Description                                                                     |
| ------ | ------------------------------------------------------------------------------- |
| `400`  | Missing or invalid path parameters, or a missing or over-length `commentBody`.    |
| `403`  | Not authorized to write this asset, or the caller does not own the comment.      |
| `404`  | Asset not found, or the comment does not exist.                                 |
| `500`  | Internal server error.                                                          |

---

### Delete a comment

`DELETE /comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}`

Soft-deletes a comment. Only the comment's owner can delete it.

**Request Parameters:**

| Parameter                  | Location | Type   | Required | Description                                 |
| -------------------------- | -------- | ------ | -------- | ------------------------------------------- |
| `assetId`                  | path     | string | Yes      | Asset identifier.                           |
| `assetVersionId:commentId` | path     | string | Yes      | Asset version and comment ID, colon-joined. |

**Response:**

```json
{
    "message": "Succeeded"
}
```

**Error Responses:**

| Status | Description                                                                |
| ------ | -------------------------------------------------------------------------- |
| `400`  | Missing path parameters, or a malformed composite path parameter.           |
| `403`  | Not authorized to delete on this asset, or the caller does not own the comment. |
| `404`  | Asset not found, or the comment does not exist.                            |
| `500`  | Internal server error.                                                     |

---

## Related resources

-   [Assets API](assets.md) -- Manage the assets that comments are attached to
-   [Asset Versions API](asset-versions.md) -- Create and inspect the versions comments are bound to
-   [Authorization API](auth.md) -- Configure the asset permissions that gate comment access
