# Assets

This page documents the asset management endpoints in the VAMS API. Assets are the core entities in VAMS, representing 3D models, point clouds, CAD files, and other visual content stored within databases.

For general API information, see the [API Overview](overview.md). For file-level operations within assets, see [Files](files.md). For asset metadata, see [Metadata](metadata.md).

---

## Concepts

-   **Asset**: A logical container for one or more files within a database. Assets have metadata, tags, version history, and storage locations.
-   **Database**: A logical grouping of assets. Each database has an associated S3 bucket for storage.
-   **Asset Version**: A point-in-time snapshot of an asset's files. Versions are created manually or when files are uploaded.
-   **Archive**: Soft-deletion of an asset. Archived assets can be unarchived. Permanent deletion removes all data.

---

## Endpoints

### List Assets in Database

`GET /database/{databaseId}/assets`

Returns a paginated list of all assets in the specified database. By default, archived assets are excluded.

**Request Parameters:**

| Parameter       | Location | Type    | Required | Description                                                                                     |
| --------------- | -------- | ------- | -------- | ----------------------------------------------------------------------------------------------- |
| `databaseId`    | path     | string  | Yes      | Database identifier. Pattern: `^[-_a-zA-Z0-9]{3,63}$`                                           |
| `showDeleted`   | query    | boolean | No       | When `true`, returns archived (soft-deleted) assets instead of active assets. Default: `false`. |
| `maxItems`      | query    | integer | No       | Maximum number of assets to return. Default: `100`.                                             |
| `pageSize`      | query    | integer | No       | Page size for pagination. Default: `100`.                                                       |
| `startingToken` | query    | string  | No       | Continuation token from a previous response.                                                    |

**Response:**

```json
{
    "Items": [
        {
            "databaseId": "my-database",
            "assetId": "asset-001",
            "assetName": "Building Model",
            "assetType": "ifc",
            "description": "Main building 3D model",
            "isDistributable": true,
            "tags": ["architecture", "building"],
            "currentVersionId": "v1",
            "assetLocation": {
                "Bucket": "vams-asset-bucket",
                "Key": "my-database/asset-001"
            },
            "previewLocation": {
                "Bucket": "vams-asset-bucket",
                "Key": "my-database/asset-001/preview.jpg"
            },
            "currentVersion": {
                "Version": "v1",
                "DateModified": "2024-06-15T10:30:00Z",
                "Comment": "Initial upload",
                "description": "",
                "createdBy": "user@example.com"
            },
            "dateCreated": "2024-06-15T10:30:00Z",
            "dateModified": "2024-06-15T10:30:00Z"
        }
    ],
    "NextToken": "eyJ..."
}
```

**Error Responses:**

| Status | Description            |
| ------ | ---------------------- |
| `404`  | Database not found.    |
| `500`  | Internal server error. |

---

### List All Assets

`GET /assets`

Returns a paginated list of all assets across all databases that the user has permission to access.

**Request Parameters:**

| Parameter       | Location | Type    | Required | Description                                         |
| --------------- | -------- | ------- | -------- | --------------------------------------------------- |
| `maxItems`      | query    | integer | No       | Maximum number of assets to return. Default: `100`. |
| `pageSize`      | query    | integer | No       | Page size for pagination. Default: `100`.           |
| `startingToken` | query    | string  | No       | Continuation token from a previous response.        |

**Response:**

```json
{
    "Items": [
        {
            "databaseId": "my-database",
            "assetId": "asset-001",
            "assetName": "Building Model",
            "assetType": "ifc",
            "description": "Main building 3D model",
            "isDistributable": true,
            "tags": ["architecture"]
        }
    ],
    "NextToken": "eyJ..."
}
```

**Error Responses:**

| Status | Description            |
| ------ | ---------------------- |
| `500`  | Internal server error. |

---

### Create Asset

`POST /assets`

Creates a new asset in the specified database. This endpoint creates the asset record in DynamoDB. File uploads are handled separately through the [upload endpoints](files.md#upload-file).

**Request Body:**

```json
{
    "databaseId": "my-database",
    "assetName": "New Building Model",
    "description": "A detailed 3D model of the new building",
    "isDistributable": true,
    "assetType": "ifc",
    "tags": ["architecture", "new-building"]
}
```

| Field             | Type          | Required | Description                                    |
| ----------------- | ------------- | -------- | ---------------------------------------------- |
| `databaseId`      | string        | Yes      | Target database identifier.                    |
| `assetName`       | string        | Yes      | Display name for the asset (1-256 characters). |
| `description`     | string        | Yes      | Asset description (4-256 characters).          |
| `isDistributable` | boolean       | Yes      | Whether the asset can be downloaded.           |
| `assetType`       | string        | No       | File type classification.                      |
| `tags`            | array[string] | No       | Tags for categorization.                       |

**Response:**

```json
{
    "message": "Asset created successfully",
    "assetId": "xd130a6d6-abcd-1234-efgh-567890abcdef"
}
```

**Error Responses:**

| Status | Description                                       |
| ------ | ------------------------------------------------- |
| `400`  | Invalid parameters or validation error.           |
| `403`  | Not authorized to create assets in this database. |
| `404`  | Database not found.                               |
| `500`  | Internal server error.                            |

---

### Get Asset

`GET /database/{databaseId}/assets/{assetId}`

Retrieves detailed information about a specific asset, including version information, storage locations, and preview data.

**Request Parameters:**

| Parameter     | Location | Type    | Required | Description                                                   |
| ------------- | -------- | ------- | -------- | ------------------------------------------------------------- |
| `databaseId`  | path     | string  | Yes      | Database identifier.                                          |
| `assetId`     | path     | string  | Yes      | Asset identifier.                                             |
| `showDeleted` | query    | boolean | No       | When `true`, also searches archived assets. Default: `false`. |

**Response:**

```json
{
    "databaseId": "my-database",
    "assetId": "asset-001",
    "assetName": "Building Model",
    "assetType": "ifc",
    "description": "Main building 3D model",
    "isDistributable": true,
    "tags": ["architecture", "building"],
    "bucketId": "bucket-001",
    "currentVersionId": "v1",
    "assetLocation": {
        "Bucket": "vams-asset-bucket",
        "Key": "my-database/asset-001"
    },
    "previewLocation": {
        "Bucket": "vams-asset-bucket",
        "Key": "my-database/asset-001/preview.jpg"
    },
    "currentVersion": {
        "Version": "v1",
        "DateModified": "2024-06-15T10:30:00Z",
        "Comment": "Initial upload",
        "description": "",
        "createdBy": "user@example.com"
    },
    "dateCreated": "2024-06-15T10:30:00Z",
    "dateModified": "2024-06-15T10:30:00Z"
}
```

**Error Responses:**

| Status | Description                        |
| ------ | ---------------------------------- |
| `403`  | Not authorized to view this asset. |
| `404`  | Database or asset not found.       |
| `500`  | Internal server error.             |

---

### Update Asset

`PUT /database/{databaseId}/assets/{assetId}`

Updates the editable fields of an existing asset. Only the provided fields are updated; omitted fields remain unchanged.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Request Body:**

```json
{
    "assetName": "Updated Building Model",
    "description": "Updated description for the building model",
    "isDistributable": false,
    "tags": ["architecture", "building", "updated"]
}
```

| Field             | Type          | Required | Description                            |
| ----------------- | ------------- | -------- | -------------------------------------- |
| `assetName`       | string        | No       | Updated asset name.                    |
| `description`     | string        | No       | Updated description.                   |
| `isDistributable` | boolean       | No       | Updated distributable flag.            |
| `tags`            | array[string] | No       | Updated tags (replaces existing tags). |

**Response:**

```json
{
    "message": "Asset updated successfully",
    "asset": {
        "databaseId": "my-database",
        "assetId": "asset-001",
        "assetName": "Updated Building Model",
        "description": "Updated description for the building model",
        "isDistributable": false,
        "tags": ["architecture", "building", "updated"]
    }
}
```

**Error Responses:**

| Status | Description                             |
| ------ | --------------------------------------- |
| `400`  | Invalid parameters or validation error. |
| `403`  | Not authorized to update this asset.    |
| `404`  | Asset not found.                        |
| `500`  | Internal server error.                  |

---

### Archive Asset

`DELETE /database/{databaseId}/assets/{assetId}/archiveAsset`

Soft-deletes an asset by archiving it. Archived assets can be restored using the [Unarchive Asset](#unarchive-asset) endpoint. The asset's files in S3 are archived using delete markers on the versioned bucket.

:::info[Reversible Operation]
Archiving is a soft-delete. The asset data is preserved and can be restored. For permanent deletion, use the [Delete Asset](#delete-asset) endpoint.
:::

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Response:**

```json
{
    "message": "Asset archived successfully"
}
```

**Error Responses:**

| Status | Description                           |
| ------ | ------------------------------------- |
| `403`  | Not authorized to archive this asset. |
| `404`  | Asset not found.                      |
| `500`  | Internal server error.                |

---

### Unarchive Asset

`PUT /database/{databaseId}/assets/{assetId}/unarchiveAsset`

Restores a previously archived asset, making it active again.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Response:**

```json
{
    "message": "Asset unarchived successfully"
}
```

**Error Responses:**

| Status | Description                             |
| ------ | --------------------------------------- |
| `403`  | Not authorized to unarchive this asset. |
| `404`  | Asset not found or not archived.        |
| `500`  | Internal server error.                  |

---

### Delete Asset

`DELETE /database/{databaseId}/assets/{assetId}/deleteAsset`

Permanently deletes an asset, including all associated files, metadata, versions, and auxiliary data.

:::danger[Irreversible Operation]
This operation permanently removes the asset and all its data. It cannot be undone. Consider using [Archive Asset](#archive-asset) for soft-deletion instead.
:::

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Response:**

```json
{
    "message": "Asset deleted successfully"
}
```

**Error Responses:**

| Status | Description                          |
| ------ | ------------------------------------ |
| `403`  | Not authorized to delete this asset. |
| `404`  | Asset not found.                     |
| `500`  | Internal server error.               |

---

### Download Asset

`POST /database/{databaseId}/assets/{assetId}/download`

Generates a presigned S3 URL for downloading a file from an asset. The URL is time-limited and provides direct access to the file in S3.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Request Body:**

```json
{
    "databaseId": "my-database",
    "assetId": "asset-001",
    "key": "/models/building.ifc",
    "versionId": "abc123"
}
```

| Field                 | Type   | Required | Description                                                                        |
| --------------------- | ------ | -------- | ---------------------------------------------------------------------------------- |
| `key`                 | string | No       | Relative file path within the asset. If omitted, the asset's primary file is used. |
| `versionId`           | string | No       | S3 version ID to download a specific version.                                      |
| `assetVersionId`      | string | No       | VAMS asset version ID. Resolves the S3 version from the version snapshot.          |
| `assetVersionIdAlias` | string | No       | Named version alias. Resolves to an asset version ID, then to the S3 version.      |

:::warning[Version Parameter Exclusivity]
Only one of `versionId`, `assetVersionId`, or `assetVersionIdAlias` can be specified. Providing more than one returns a `400` error. Version parameters are not allowed for asset preview downloads.
:::

**Response:**

```json
{
    "message": "https://vams-asset-bucket.s3.amazonaws.com/my-database/asset-001/models/building.ifc?X-Amz-..."
}
```

**Error Responses:**

| Status | Description                                                                                                   |
| ------ | ------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, multiple version parameters specified, or version parameters used with preview downloads. |
| `401`  | Asset is not distributable.                                                                                   |
| `403`  | Not authorized to download this asset.                                                                        |
| `404`  | Database, asset, version, or file not found.                                                                  |
| `500`  | Internal server error.                                                                                        |

---

### Export Asset

`POST /database/{databaseId}/assets/{assetId}/export`

Exports comprehensive asset data including the asset hierarchy (child relationships), metadata, files, versions, and relationships. Supports pagination for large asset trees and optional response compression.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description            |
| ------------ | -------- | ------ | -------- | ---------------------- |
| `databaseId` | path     | string | Yes      | Database identifier.   |
| `assetId`    | path     | string | Yes      | Root asset identifier. |

**Request Body:**

```json
{
    "generatePresignedUrls": false,
    "includeFolderFiles": false,
    "includeOnlyPrimaryTypeFiles": false,
    "includeFileMetadata": true,
    "includeAssetLinkMetadata": true,
    "includeAssetMetadata": true,
    "fetchAssetRelationships": true,
    "fetchEntireChildrenSubtrees": false,
    "includeParentRelationships": false,
    "includeArchivedFiles": false,
    "fileExtensions": [".pdf", ".jpg"],
    "maxAssets": 100,
    "startingToken": null
}
```

| Field                         | Type          | Default | Description                                                           |
| ----------------------------- | ------------- | ------- | --------------------------------------------------------------------- |
| `generatePresignedUrls`       | boolean       | `false` | Generate presigned S3 URLs for file downloads.                        |
| `includeFolderFiles`          | boolean       | `false` | Include folder markers in file listings.                              |
| `includeOnlyPrimaryTypeFiles` | boolean       | `false` | Include only files with `primaryType` metadata set.                   |
| `includeFileMetadata`         | boolean       | `true`  | Include file-specific metadata.                                       |
| `includeAssetLinkMetadata`    | boolean       | `true`  | Include asset link relationship metadata.                             |
| `includeAssetMetadata`        | boolean       | `true`  | Include asset-level metadata.                                         |
| `fetchAssetRelationships`     | boolean       | `true`  | Fetch asset relationships. When `false`, returns only the root asset. |
| `fetchEntireChildrenSubtrees` | boolean       | `false` | Fetch complete child tree hierarchy instead of one level.             |
| `includeParentRelationships`  | boolean       | `false` | Include parent relationships in the relationship data.                |
| `includeArchivedFiles`        | boolean       | `false` | Include archived files in export.                                     |
| `fileExtensions`              | array[string] | --      | Filter files to specified extensions only.                            |
| `maxAssets`                   | integer       | `100`   | Maximum assets per page (1-1000).                                     |
| `startingToken`               | string        | --      | Pagination token from a previous response.                            |

**Response:**

```json
{
    "assets": [
        {
            "is_root_lookup_asset": true,
            "databaseid": "my-database",
            "assetid": "asset-001",
            "assetname": "Building Model",
            "assettype": "ifc",
            "description": "Main building",
            "isdistributable": true,
            "tags": ["architecture"],
            "archived": false,
            "metadata": { ... },
            "files": [ ... ],
            "relationships": [ ... ]
        }
    ],
    "totalAssetsInTree": 5,
    "assetsInThisPage": 5,
    "NextToken": null
}
```

:::info[Response Compression]
Responses exceeding 100KB are automatically gzip-compressed. The `Content-Encoding: gzip` header indicates compression.
:::

**Error Responses:**

| Status | Description                          |
| ------ | ------------------------------------ |
| `400`  | Invalid parameters.                  |
| `403`  | Not authorized to export this asset. |
| `404`  | Asset not found.                     |
| `500`  | Internal server error.               |
