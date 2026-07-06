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

Soft-deletes an asset by archiving it. Archived assets can be restored using the [Unarchive Asset](#unarchive-asset) endpoint. The asset's files in S3 are archived using delete markers on the versioned bucket, and each archived file is recorded with `assetArchive` provenance in the file version history so a later unarchive can selectively restore them.

:::info[Reversible Operation]
Archiving is a soft-delete. The asset data is preserved and can be restored. Unarchiving restores the asset record only by default; restoring the archived files is a separate opt-in (`unarchiveFiles`). For permanent deletion, use the [Delete Asset](#delete-asset) endpoint.
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

Restores a previously archived asset record, making it active again. The asset's files remain archived by default. Setting `unarchiveFiles` to `true` also restores the files that the asset archive operation archived (matched by `assetArchive` provenance in the file version history); files archived individually before the asset archive always remain archived and can be restored with the [Unarchive File](files.md#unarchive-file) endpoint. Assets archived before provenance tracking have no restorable file set, so no files are restored for them.

**Request Parameters:**

| Parameter          | Location | Type    | Required | Description                                                           |
| ------------------ | -------- | ------- | -------- | --------------------------------------------------------------------- |
| `databaseId`       | path     | string  | Yes      | Database identifier.                                                  |
| `assetId`          | path     | string  | Yes      | Asset identifier.                                                     |
| `confirmUnarchive` | body     | boolean | Yes      | Must be `true`.                                                       |
| `reason`           | body     | string  | No       | Reason for unarchiving.                                               |
| `unarchiveFiles`   | body     | boolean | No       | Also restore files archived by the asset archive. Default is `false`. |

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

Generates presigned S3 URLs for downloading files from an asset. The URLs are time-limited and provide direct access to the files in S3. A request can target a single file (`key`) or multiple files of the same asset in one call (`keys`, up to 1,500 per request).

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Request Body (single file):**

```json
{
    "downloadType": "assetFile",
    "key": "/models/building.ifc",
    "versionId": "abc123"
}
```

**Request Body (bulk, latest versions):**

```json
{
    "downloadType": "assetFile",
    "keys": ["/models/building.ifc", "/textures/wall.png"]
}
```

**Request Body (bulk, per-file versions):**

```json
{
    "downloadType": "assetFile",
    "keys": [{ "key": "/models/building.ifc", "versionId": "abc123" }, "/textures/wall.png"]
}
```

| Field                 | Type               | Required | Description                                                                                                                                                              |
| --------------------- | ------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `key`                 | string             | No       | Relative file path within the asset. If omitted, the asset's primary file is used. Mutually exclusive with `keys`.                                                       |
| `keys`                | (string\|object)[] | No       | Files to generate URLs for in bulk (max 1,500 per request; `assetFile` only). Each entry is a path string (latest) or `{key, versionId}`. Mutually exclusive with `key`. |
| `versionId`           | string             | No       | S3 version ID for a single `key`. Cannot be combined with `keys` (put versions on individual keys instead).                                                              |
| `assetVersionId`      | string             | No       | VAMS asset version ID. Resolves the S3 version from the version snapshot for all requested file(s).                                                                      |
| `assetVersionIdAlias` | string             | No       | Named version alias. Resolves to an asset version ID, then to the S3 version.                                                                                            |

:::warning[Version Resolution and Exclusivity]
Version resolution is applied per file: `assetVersionId`/`assetVersionIdAlias` pins **all** files to that asset version snapshot; otherwise a per-file `versionId` (or the single `versionId` for a single `key`) selects that S3 version; with no version specified the **latest** file version is returned. Only one of `versionId`, `assetVersionId`, or `assetVersionIdAlias` can be specified at the request level. Per-file `versionId`s in `keys` cannot be combined with `assetVersionId`/`assetVersionIdAlias`. Version parameters are not allowed for asset preview downloads, and `key`/`keys` are mutually exclusive.
:::

**Response (single file):**

```json
{
    "downloadUrl": "https://vams-asset-bucket.s3.amazonaws.com/...?X-Amz-...",
    "expiresIn": 86400,
    "downloadType": "assetFile",
    "versionId": "abc123",
    "files": null,
    "message": "Download URL generated successfully"
}
```

**Response (bulk):**

```json
{
    "downloadUrl": "https://vams-asset-bucket.s3.amazonaws.com/...?X-Amz-...",
    "expiresIn": 86400,
    "downloadType": "assetFile",
    "files": [
        {
            "key": "/models/building.ifc",
            "downloadUrl": "https://vams-asset-bucket.s3.amazonaws.com/...?X-Amz-...",
            "versionId": "abc123",
            "success": true,
            "error": null
        },
        {
            "key": "/textures/missing.png",
            "downloadUrl": null,
            "versionId": null,
            "success": false,
            "error": "File not found in S3"
        }
    ],
    "message": "Generated 1 of 2 download URLs. Warning: 1 file path(s) do not exist or are not downloadable and were skipped."
}
```

Bulk requests return one entry per requested key. File paths that do not exist or are not downloadable are skipped (reported with `success: false` and an `error` reason, plus a warning in `message`); the request fails with `400` only when no URL can be generated at all. The top-level `downloadUrl` carries the first successful URL for compatibility with single-URL consumers.

**Error Responses:**

| Status | Description                                                                                                                                                                |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, multiple version parameters specified, `key`/`keys` combined, over 1,500 keys, no URLs generatable, or version parameters used with preview downloads. |
| `401`  | Asset is not distributable.                                                                                                                                                |
| `403`  | Not authorized to download this asset.                                                                                                                                     |
| `404`  | Database, asset, version, or file not found.                                                                                                                               |
| `500`  | Internal server error.                                                                                                                                                     |

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

---

### Get Asset History

`GET /database/{databaseId}/assets/{assetId}/assetHistory`

Returns the lifecycle history records for an asset, newest first. Each record captures one lifecycle operation (create, edit, archive, unarchive, or permanent delete) with the acting user, the origin of the change, and an open-schema snapshot of the asset fields as they stood after the operation.

History records persist across permanent deletion. If an asset is permanently deleted and later recreated with the same asset ID, the prior history (including the `permanentDelete` record) is returned again for that ID. When no asset record exists (live or archived) for the ID, the endpoint returns `404`.

**Request Parameters:**

| Parameter       | Location | Type    | Required | Description                                                |
| --------------- | -------- | ------- | -------- | ---------------------------------------------------------- |
| `databaseId`    | path     | string  | Yes      | Database identifier.                                       |
| `assetId`       | path     | string  | Yes      | Asset identifier.                                          |
| `pageSize`      | query    | integer | No       | Maximum records per page (1-1000, default 100).            |
| `startingToken` | query    | string  | No       | Continuation token from a previous response's `NextToken`. |

**Change Sources:**

| Value             | Operation                                        |
| ----------------- | ------------------------------------------------ |
| `create`          | Asset created through the VAMS API.              |
| `createDirect`    | Asset auto-created by S3 bucket-sync ingestion.  |
| `edit`            | Asset fields updated.                            |
| `archive`         | Asset archived.                                  |
| `unarchive`       | Asset unarchived through the VAMS API.           |
| `unarchiveDirect` | Asset auto-restored by S3 bucket-sync ingestion. |
| `permanentDelete` | Asset permanently deleted.                       |

**Response:**

```json
{
    "message": "Success",
    "Items": [
        {
            "historyRecordId": "2026-07-05T14:23:01.123456Z#a1b2c3d4",
            "databaseId": "my-database",
            "assetId": "my-asset",
            "recordDate": "2026-07-05T14:23:01.123456Z",
            "changeSource": "edit",
            "changeUserId": "user@example.com",
            "assetSnapshot": {
                "assetName": "My Asset",
                "description": "Updated description",
                "isDistributable": true,
                "tags": ["tag1"],
                "bucketId": "xbucket1",
                "assetLocationKey": "my-asset/"
            }
        },
        {
            "historyRecordId": "2026-07-01T09:00:00Z#migrated",
            "databaseId": "my-database",
            "assetId": "my-asset",
            "recordDate": "2026-07-01T09:00:00Z",
            "changeSource": "create",
            "changeUserId": "SYSTEM_USER",
            "assetSnapshot": { "assetName": "My Asset" },
            "migratedRecord": true
        }
    ],
    "NextToken": "eyJkYXRhYmFzZUlkOmFzc2V0SWQiOiAi..."
}
```

The `assetSnapshot` object is open-schema: snapshot fields may grow over time, and consumers should render whatever keys are present. Archive and unarchive records include `archivedReason`/`unarchivedReason` in the snapshot when a reason was provided. Records with `migratedRecord: true` were backfilled by the deployment data migration from inferred data.

**Error Responses:**

| Status | Description                                  |
| ------ | -------------------------------------------- |
| `400`  | Invalid parameters or pagination token.      |
| `403`  | Not authorized to view this asset's history. |
| `404`  | Asset not found.                             |
| `500`  | Internal server error.                       |
