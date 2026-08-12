# Files

This page documents the file operation endpoints in the VAMS API. These endpoints manage individual files within assets, including listing, moving, copying, archiving, uploading, and streaming.

For asset-level operations, see [Assets](assets.md). For file metadata, see [Metadata](metadata.md).

---

## Concepts

-   **File**: An individual object stored in S3 within an asset's directory structure. Files can be organized in folders.
-   **File Version**: S3 object versions tracked through bucket versioning. VAMS also tracks file versions within asset version snapshots.
-   **Primary File Type**: A designation that marks a file as the primary representative of a particular type within an asset (e.g., the primary `.ifc` file).
-   **Preview File**: A generated preview image (`.previewFile.gif`, `.previewFile.jpg`, `.previewFile.png`) associated with a specific file.
-   **Archive**: Soft-deletion of a file using S3 delete markers. Archived files can be unarchived.

---

## Endpoints

### List Files

`GET /database/{databaseId}/assets/{assetId}/listFiles`

Returns a list of all files in the specified asset, including file metadata, sizes, and archive status.

**Request Parameters:**

| Parameter       | Location | Type    | Required | Description                                  |
| --------------- | -------- | ------- | -------- | -------------------------------------------- |
| `databaseId`    | path     | string  | Yes      | Database identifier.                         |
| `assetId`       | path     | string  | Yes      | Asset identifier.                            |
| `maxItems`      | query    | integer | No       | Maximum number of files to return.           |
| `pageSize`      | query    | integer | No       | Page size for pagination.                    |
| `startingToken` | query    | string  | No       | Continuation token from a previous response. |

**Response:**

```json
{
    "items": [
        {
            "fileName": "building.ifc",
            "key": "/models/building.ifc",
            "relativePath": "/models/building.ifc",
            "isFolder": false,
            "size": 15728640,
            "dateCreatedCurrentVersion": "2024-06-15T10:30:00Z",
            "versionId": "abc123",
            "etag": "d41d8cd98f00b204e9800998ecf8427e",
            "storageClass": "STANDARD",
            "isArchived": false,
            "primaryType": "primary",
            "previewFile": "/models/building.ifc.previewFile.png",
            "changeSource": "upload",
            "changeUserId": "user@example.com"
        },
        {
            "fileName": "textures",
            "key": "/textures/",
            "relativePath": "/textures/",
            "isFolder": true,
            "size": 0,
            "dateCreatedCurrentVersion": "2024-06-15T10:30:00Z",
            "isArchived": false
        }
    ],
    "NextToken": "eyJ..."
}
```

**Error Responses:**

| Status | Description                                 |
| ------ | ------------------------------------------- |
| `400`  | Invalid parameters.                         |
| `403`  | Not authorized to list files in this asset. |
| `404`  | Database or asset not found.                |
| `500`  | Internal server error.                      |

---

### Get File Info

`GET /database/{databaseId}/assets/{assetId}/fileInfo`

Retrieves detailed information about a specific file, including S3 metadata, version history, and archive status.

**Request Parameters:**

| Parameter         | Location | Type    | Required | Description                                                             |
| ----------------- | -------- | ------- | -------- | ----------------------------------------------------------------------- |
| `databaseId`      | path     | string  | Yes      | Database identifier.                                                    |
| `assetId`         | path     | string  | Yes      | Asset identifier.                                                       |
| `filePath`        | query    | string  | Yes      | The relative file path (e.g., `/models/building.ifc`).                  |
| `includeVersions` | query    | boolean | No       | When `true`, include the file's version history in the `versions` list. |

**Response:**

```json
{
    "fileName": "building.ifc",
    "key": "/models/building.ifc",
    "relativePath": "/models/building.ifc",
    "isFolder": false,
    "size": 15728640,
    "contentType": "application/octet-stream",
    "lastModified": "2024-06-15T10:30:00Z",
    "etag": "d41d8cd98f00b204e9800998ecf8427e",
    "storageClass": "STANDARD",
    "isArchived": false,
    "primaryType": "primary",
    "previewFile": "/models/building.ifc.previewFile.png",
    "changeSource": "upload",
    "changeUserId": "user@example.com",
    "versions": [
        {
            "versionId": "abc123",
            "lastModified": "2024-06-15T10:30:00Z",
            "size": 15728640,
            "isLatest": true
        }
    ]
}
```

The `versions` list is present only when `includeVersions` is `true`.

**Error Responses:**

| Status | Description                                         |
| ------ | --------------------------------------------------- |
| `400`  | Invalid parameters or missing `filePath` parameter. |
| `403`  | Not authorized to view this file.                   |
| `404`  | File not found.                                     |
| `500`  | Internal server error.                              |

---

### Move/Rename File

`POST /database/{databaseId}/assets/{assetId}/moveFile`

Moves or renames a file within the asset. This copies the file to the new location and deletes the original.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Request Body:**

```json
{
    "sourcePath": "/models/old-name.ifc",
    "destinationPath": "/models/new-name.ifc"
}
```

| Field             | Type   | Required | Description                 |
| ----------------- | ------ | -------- | --------------------------- |
| `sourcePath`      | string | Yes      | Current relative file path. |
| `destinationPath` | string | Yes      | New relative file path.     |

**Response:**

```json
{
    "success": true,
    "message": "File moved successfully",
    "affectedFiles": ["/models/new-name.ifc"]
}
```

**Error Responses:**

| Status | Description                                                               |
| ------ | ------------------------------------------------------------------------- |
| `400`  | Invalid parameters, source file not found, or destination already exists. |
| `403`  | Not authorized to modify files in this asset.                             |
| `500`  | Internal server error.                                                    |

---

### Copy File

`POST /database/{databaseId}/assets/{assetId}/copyFile`

Copies a file within the same asset or to a different asset. Supports cross-database copying when `destinationDatabaseId` is provided.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description                 |
| ------------ | -------- | ------ | -------- | --------------------------- |
| `databaseId` | path     | string | Yes      | Source database identifier. |
| `assetId`    | path     | string | Yes      | Source asset identifier.    |

**Request Body:**

```json
{
    "sourcePath": "/models/building.ifc",
    "destinationPath": "/models/building-copy.ifc",
    "destinationAssetId": "asset-002",
    "destinationDatabaseId": "other-database"
}
```

| Field                   | Type   | Required | Description                                 |
| ----------------------- | ------ | -------- | ------------------------------------------- |
| `sourcePath`            | string | Yes      | Source file relative path.                  |
| `destinationPath`       | string | Yes      | Destination file relative path.             |
| `destinationAssetId`    | string | No       | Target asset ID (defaults to same asset).   |
| `destinationDatabaseId` | string | No       | Target database ID for cross-database copy. |

**Response:**

```json
{
    "success": true,
    "message": "File copied successfully",
    "affectedFiles": ["/models/building-copy.ifc"]
}
```

**Error Responses:**

| Status | Description                                  |
| ------ | -------------------------------------------- |
| `400`  | Invalid parameters or source file not found. |
| `403`  | Not authorized to copy files.                |
| `500`  | Internal server error.                       |

---

### Delete File

`DELETE /database/{databaseId}/assets/{assetId}/deleteFile`

Permanently deletes a file from the asset. This removes all versions of the file from S3.

:::danger[Irreversible Operation]
This permanently deletes the file and all its versions. Consider using [Archive File](#archive-file) for soft-deletion instead.
:::

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Request Body:**

```json
{
    "filePath": "/models/building.ifc",
    "isPrefix": false,
    "confirmPermanentDelete": true
}
```

| Field                    | Type    | Required | Description                                                                      |
| ------------------------ | ------- | -------- | -------------------------------------------------------------------------------- |
| `filePath`               | string  | Yes      | Relative file path to delete.                                                    |
| `isPrefix`               | boolean | No       | When `true`, delete all files under the path prefix. Defaults to `false`.        |
| `confirmPermanentDelete` | boolean | Yes      | Safety confirmation. Must be `true`; the operation errors when it is not `true`. |

**Response:**

```json
{
    "success": true,
    "message": "File deleted successfully",
    "affectedFiles": ["/models/building.ifc"]
}
```

**Error Responses:**

| Status | Description                                                       |
| ------ | ----------------------------------------------------------------- |
| `400`  | Invalid parameters or `confirmPermanentDelete` not set to `true`. |
| `403`  | Not authorized to delete files in this asset.                     |
| `404`  | File not found.                                                   |
| `500`  | Internal server error.                                            |

---

### Archive File

`DELETE /database/{databaseId}/assets/{assetId}/archiveFile`

Soft-deletes a file by creating an S3 delete marker. The file can be restored using [Unarchive File](#unarchive-file).

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Request Body:**

```json
{
    "filePath": "/models/building.ifc",
    "isPrefix": false
}
```

| Field      | Type    | Required | Description                                                                |
| ---------- | ------- | -------- | -------------------------------------------------------------------------- |
| `filePath` | string  | Yes      | Relative file path to archive.                                             |
| `isPrefix` | boolean | No       | When `true`, archive all files under the path prefix. Defaults to `false`. |

**Response:**

```json
{
    "success": true,
    "message": "File archived successfully",
    "affectedFiles": ["/models/building.ifc"]
}
```

**Error Responses:**

| Status | Description                                    |
| ------ | ---------------------------------------------- |
| `400`  | Invalid parameters.                            |
| `403`  | Not authorized to archive files in this asset. |
| `404`  | File not found.                                |
| `500`  | Internal server error.                         |

---

### Unarchive File

`POST /database/{databaseId}/assets/{assetId}/unarchiveFile`

Restores a previously archived file by removing the S3 delete marker.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Request Body:**

```json
{
    "filePath": "/models/building.ifc"
}
```

| Field      | Type   | Required | Description                      |
| ---------- | ------ | -------- | -------------------------------- |
| `filePath` | string | Yes      | Relative file path to unarchive. |

**Response:**

```json
{
    "success": true,
    "message": "File unarchived successfully",
    "affectedFiles": ["/models/building.ifc"]
}
```

**Error Responses:**

| Status | Description                                      |
| ------ | ------------------------------------------------ |
| `400`  | Invalid parameters.                              |
| `403`  | Not authorized to unarchive files in this asset. |
| `404`  | File not found or not archived.                  |
| `500`  | Internal server error.                           |

---

### Create Folder

`POST /database/{databaseId}/assets/{assetId}/createFolder`

Creates a new folder (zero-byte S3 object with trailing slash) within the asset's directory structure.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Request Body:**

```json
{
    "relativeKey": "/new-folder/"
}
```

| Field         | Type   | Required | Description                                    |
| ------------- | ------ | -------- | ---------------------------------------------- |
| `relativeKey` | string | Yes      | The folder path to create (must end with `/`). |

**Response:**

```json
{
    "message": "Folder created successfully",
    "relativeKey": "/new-folder/"
}
```

**Error Responses:**

| Status | Description                                     |
| ------ | ----------------------------------------------- |
| `400`  | Invalid parameters or folder already exists.    |
| `403`  | Not authorized to create folders in this asset. |
| `500`  | Internal server error.                          |

---

### Revert File Version

`POST /database/{databaseId}/assets/{assetId}/revertFileVersion/{versionId}`

Reverts a file to a specific previous S3 version by copying the old version as the new current version.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description                     |
| ------------ | -------- | ------ | -------- | ------------------------------- |
| `databaseId` | path     | string | Yes      | Database identifier.            |
| `assetId`    | path     | string | Yes      | Asset identifier.               |
| `versionId`  | path     | string | Yes      | The S3 version ID to revert to. |

**Request Body:**

```json
{
    "filePath": "/models/building.ifc"
}
```

| Field      | Type   | Required | Description                   |
| ---------- | ------ | -------- | ----------------------------- |
| `filePath` | string | Yes      | Relative file path to revert. |

**Response:**

```json
{
    "success": true,
    "message": "File version reverted successfully",
    "filePath": "/models/building.ifc",
    "revertedFromVersionId": "abc123",
    "newVersionId": "def456"
}
```

**Error Responses:**

| Status | Description                              |
| ------ | ---------------------------------------- |
| `400`  | Invalid parameters or version not found. |
| `403`  | Not authorized to revert file versions.  |
| `404`  | File or version not found.               |
| `500`  | Internal server error.                   |

---

### Set Primary File Type

`PUT /database/{databaseId}/assets/{assetId}/setPrimaryFile`

Designates a file as the primary representative of its file type within the asset. Only one file per type can be primary.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Request Body:**

```json
{
    "filePath": "/models/building.ifc",
    "primaryType": "primary"
}
```

| Field              | Type   | Required | Description                                                                                            |
| ------------------ | ------ | -------- | ------------------------------------------------------------------------------------------------------ |
| `filePath`         | string | Yes      | Relative file path.                                                                                    |
| `primaryType`      | string | Yes      | The primary type designation. One of `''`, `primary`, `lod1`, `lod2`, `lod3`, `lod4`, `lod5`, `other`. |
| `primaryTypeOther` | string | No       | Custom type label. Required when `primaryType` is `other`, and only allowed in that case.              |

**Response:**

```json
{
    "success": true,
    "message": "Primary file type set successfully",
    "filePath": "/models/building.ifc",
    "primaryType": "primary"
}
```

**Error Responses:**

| Status | Description                               |
| ------ | ----------------------------------------- |
| `400`  | Invalid parameters.                       |
| `403`  | Not authorized to modify file attributes. |
| `404`  | File not found.                           |
| `500`  | Internal server error.                    |

---

## Upload Endpoints

### Upload File

`POST /uploads`

Initiates a file upload by returning presigned S3 URLs. For small files, a single presigned PUT URL is returned. For large files (multipart upload), the request is queued for asynchronous processing via SQS.

**Request Body:**

```json
{
    "assetId": "asset-001",
    "databaseId": "my-database",
    "uploadType": "assetFile",
    "files": [
        {
            "relativeKey": "/models/building.ifc",
            "file_size": 15728640,
            "num_parts": 1
        }
    ]
}
```

| Field        | Type   | Required | Description                                                                                    |
| ------------ | ------ | -------- | ---------------------------------------------------------------------------------------------- |
| `assetId`    | string | Yes      | Target asset identifier.                                                                       |
| `databaseId` | string | Yes      | Target database identifier.                                                                    |
| `uploadType` | string | Yes      | Upload target. One of `assetFile` or `assetPreview` (`assetPreview` accepts exactly one file). |
| `files`      | array  | Yes      | Files to initialize the upload for.                                                            |

Each entry in `files` is an object:

| Field         | Type    | Required | Description                                                                           |
| ------------- | ------- | -------- | ------------------------------------------------------------------------------------- |
| `relativeKey` | string  | Yes      | Relative file path for the upload.                                                    |
| `file_size`   | integer | No       | File size in bytes. Either `file_size` or `num_parts` must be provided.               |
| `num_parts`   | integer | No       | Number of multipart upload parts. Either `file_size` or `num_parts` must be provided. |

**Response:**

```json
{
    "uploadId": "upload-12345",
    "files": [
        {
            "relativeKey": "/models/building.ifc",
            "uploadIdS3": "multipart-upload-id",
            "numParts": 1,
            "partUploadUrls": [
                {
                    "PartNumber": 1,
                    "UploadUrl": "https://bucket.s3.amazonaws.com/...?X-Amz-..."
                }
            ]
        }
    ],
    "message": "Upload initialized successfully"
}
```

**Error Responses:**

| Status | Description                                                       |
| ------ | ----------------------------------------------------------------- |
| `400`  | Invalid parameters, blocked file extension, or blocked MIME type. |
| `403`  | Not authorized to upload files to this asset.                     |
| `500`  | Internal server error.                                            |

:::info[Blocked File Types]
For security, certain file extensions are blocked: `.jar`, `.java`, `.com`, `.php`, `.reg`, `.pif`, `.bak`, `.dll`, `.exe`, `.nat`, `.cmd`, `.lnk`, `.docm`, `.vbs`, `.bat`. Corresponding MIME types are also blocked.
:::

---

### Complete Upload

`POST /uploads/{uploadId}/complete`

Completes a multipart file upload by signaling that all parts have been uploaded.

**Request Parameters:**

| Parameter  | Location | Type   | Required | Description                                            |
| ---------- | -------- | ------ | -------- | ------------------------------------------------------ |
| `uploadId` | path     | string | Yes      | The upload identifier from the initial upload request. |

**Request Body:**

```json
{
    "assetId": "asset-001",
    "databaseId": "my-database",
    "uploadType": "assetFile",
    "files": [
        {
            "relativeKey": "/models/building.ifc",
            "uploadIdS3": "multipart-upload-id",
            "parts": [
                {
                    "PartNumber": 1,
                    "ETag": "\"d41d8cd98f00b204e9800998ecf8427e\""
                }
            ]
        }
    ]
}
```

| Field        | Type   | Required | Description                                          |
| ------------ | ------ | -------- | ---------------------------------------------------- |
| `assetId`    | string | Yes      | Target asset identifier.                             |
| `databaseId` | string | Yes      | Target database identifier.                          |
| `uploadType` | string | Yes      | Upload target. One of `assetFile` or `assetPreview`. |
| `files`      | array  | Yes      | Completed files, each with its uploaded parts.       |

Each entry in `files` is an object with `relativeKey`, `uploadIdS3`, and a `parts` array of `{ "PartNumber", "ETag" }` objects.

**Response:**

```json
{
    "message": "Upload completed successfully",
    "uploadId": "upload-12345",
    "assetId": "asset-001",
    "assetType": "3d",
    "fileResults": [
        {
            "relativeKey": "/models/building.ifc",
            "uploadIdS3": "multipart-upload-id",
            "success": true
        }
    ],
    "overallSuccess": true,
    "largeFileAsynchronousHandling": false
}
```

**Error Responses:**

| Status | Description                         |
| ------ | ----------------------------------- |
| `400`  | Invalid upload ID or missing parts. |
| `500`  | Internal server error.              |

---

## Stream Endpoints

Both stream endpoints support two file-delivery modes, selected by the `ALWAYS_REDIRECT_TO_PRESIGNED` toggle in the corresponding Lambda handler (`streamAsset` and `streamAuxiliaryPreviewAsset`):

-   **Presigned-redirect mode** (`ALWAYS_REDIRECT_TO_PRESIGNED = True`): every request returns a `307 Temporary Redirect` whose `Location` is a short-lived Amazon S3 presigned URL. The client follows the redirect and fetches the bytes directly from the asset (or auxiliary) S3 bucket, which is CORS-enabled and supports native HTTP `Range` requests. This removes the 6&nbsp;MB Lambda response limit and the base64 encoding overhead, and offloads byte transfer from the Lambda to S3.
-   **Inline mode** (`ALWAYS_REDIRECT_TO_PRESIGNED = False`): files at or under approximately 4.4&nbsp;MB are returned inline as the response body (base64-encoded for binary content), and only larger files fall back to the `307` presigned redirect.

:::warning[Presigned-redirect mode is required on API Gateway REST APIs]
Under the API Gateway REST API, inline binary delivery requires the API-wide `binaryMediaTypes` to include `*/*`, which is incompatible with the CORS `OPTIONS` preflight (it breaks the preflight's MOCK integration). Because of this, `ALWAYS_REDIRECT_TO_PRESIGNED` must be `True` so that all files are delivered by presigned redirect. The inline path is retained for potential future use (for example, a different API front-end that does not have this constraint).

The trade-off of always redirecting is an extra request hop (the redirect to S3) on every file fetch. Clients that issue many small requests — such as octree or 3D tile streaming viewers fetching numerous metadata and tile files — incur the redirect cost per request and load more slowly than with inline delivery.

:::note[Distribution control]
Both stream endpoints require the asset's `isDistributable` flag to be `true`. When it is `false` they return `403` regardless of the caller's role permissions, as do the download endpoints. See [The isDistributable flag](../concepts/assets.md#the-isdistributable-flag).
:::
:::

### Stream Asset File

`GET /database/{databaseId}/assets/{assetId}/download/stream/{proxy+}`

Streams a file from an asset. Supports HTTP range requests for partial content delivery, which enables seeking in video/audio files and progressive loading of large files. The Range request is served by S3 on the redirected presigned URL (presigned-redirect mode) or by the API directly (inline mode).

`HEAD /database/{databaseId}/assets/{assetId}/download/stream/{proxy+}`

Returns file metadata (size, content type) without the file body.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description                                |
| ------------ | -------- | ------ | -------- | ------------------------------------------ |
| `databaseId` | path     | string | Yes      | Database identifier.                       |
| `assetId`    | path     | string | Yes      | Asset identifier.                          |
| `{proxy+}`   | path     | string | Yes      | The relative file path within the asset.   |
| `v`          | query    | string | No       | S3 version ID for a specific file version. |
| `avid`       | query    | string | No       | VAMS asset version ID.                     |

**Response:**

In presigned-redirect mode, returns `307 Temporary Redirect` with a `Location` header pointing at an S3 presigned URL; the client follows it to retrieve the raw file content (with S3 serving `206 Partial Content` for range requests). In inline mode, files at or under ~4.4&nbsp;MB return the raw file content directly with appropriate `Content-Type` and `Content-Length` headers (`206 Partial Content` for range requests), and larger files return the `307` redirect.

**Error Responses:**

| Status | Description                                                                    |
| ------ | ------------------------------------------------------------------------------ |
| `403`  | Not authorized to stream this file, or the asset is not marked distributable.  |
| `404`  | File not found.                                                                |
| `500`  | Internal server error.                                                         |

---

### Stream Auxiliary Preview Asset

`GET /database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}`

Streams auxiliary preview files (e.g., Potree octree data, generated viewer files) from the auxiliary S3 bucket. These files are non-versioned and typically generated by processing pipelines. Delivery follows the same presigned-redirect / inline modes described under [Stream Endpoints](#stream-endpoints).

`HEAD /database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}`

Returns file metadata without the file body.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description                                         |
| ------------ | -------- | ------ | -------- | --------------------------------------------------- |
| `databaseId` | path     | string | Yes      | Database identifier.                                |
| `assetId`    | path     | string | Yes      | Asset identifier.                                   |
| `{proxy+}`   | path     | string | Yes      | The relative file path within the auxiliary bucket. |

**Response:**

In presigned-redirect mode, returns `307 Temporary Redirect` with a `Location` header pointing at an S3 presigned URL that the client follows to retrieve the file. In inline mode, files at or under ~4.4&nbsp;MB return the raw file content directly with appropriate headers, and larger files return the `307` redirect.

**Error Responses:**

| Status | Description                                                                    |
| ------ | ------------------------------------------------------------------------------ |
| `403`  | Not authorized to stream this file, or the asset is not marked distributable.  |
| `404`  | File not found.                                                                |
| `500`  | Internal server error.                                                         |

---

## Preview Management

### Delete Asset Preview

`DELETE /database/{databaseId}/assets/{assetId}/deleteAssetPreview`

Deletes the asset-level preview image.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Response:**

```json
{
    "success": true,
    "message": "Asset preview deleted successfully",
    "assetId": "asset-001"
}
```

**Error Responses:**

| Status | Description                 |
| ------ | --------------------------- |
| `403`  | Not authorized.             |
| `404`  | Asset or preview not found. |
| `500`  | Internal server error.      |

---

### Delete Auxiliary Preview Files

`DELETE /database/{databaseId}/assets/{assetId}/deleteAuxiliaryPreviewAssetFiles`

Deletes auxiliary preview files (e.g., Potree viewer data) from the auxiliary bucket for the specified asset.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description          |
| ------------ | -------- | ------ | -------- | -------------------- |
| `databaseId` | path     | string | Yes      | Database identifier. |
| `assetId`    | path     | string | Yes      | Asset identifier.    |

**Request Body:**

```json
{
    "filePath": "/models/building.ifc"
}
```

| Field      | Type   | Required | Description                                                   |
| ---------- | ------ | -------- | ------------------------------------------------------------- |
| `filePath` | string | Yes      | Relative file path whose auxiliary preview files are deleted. |

**Response:**

```json
{
    "success": true,
    "message": "Auxiliary preview files deleted successfully",
    "filePath": "/models/building.ifc",
    "deletedCount": 12
}
```

**Error Responses:**

| Status | Description            |
| ------ | ---------------------- |
| `403`  | Not authorized.        |
| `404`  | Asset not found.       |
| `500`  | Internal server error. |
