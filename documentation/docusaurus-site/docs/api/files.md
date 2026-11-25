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

| Parameter         | Location | Type    | Required | Description                                                                                                                     |
| ----------------- | -------- | ------- | -------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `databaseId`      | path     | string  | Yes      | Database identifier.                                                                                                            |
| `assetId`         | path     | string  | Yes      | Asset identifier.                                                                                                               |
| `maxItems`        | query    | integer | No       | Maximum number of files to return. Default: `10000`. No maximum.                                                                |
| `pageSize`        | query    | integer | No       | Page size for pagination. Default: `100`, or `1500` when `basic` is `true`. No maximum.                                         |
| `startingToken`   | query    | string  | No       | Continuation token from a previous response.                                                                                    |
| `includeArchived` | query    | boolean | No       | Include archived files. Default: `false`. Archived files are returned on the first page only, beside that page's current files. |

**Response:**

```json
{
    "items": [
        {
            "fileName": "building.ifc",
            "key": "asset-001/models/building.ifc",
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
            "changeUserId": "user@example.com",
            "changeWorkflowId": null,
            "changeWorkflowExecutionId": null
        },
        {
            "fileName": "textures",
            "key": "asset-001/textures/",
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

`key` is the full Amazon S3 object key, which starts with the asset's base key. `relativePath` is the file's path under that base key, which is also what File Info returns.

An asset created from one Amazon S3 object (a `bucketExistingKey` without a trailing `/`) lists that object at `/{file name}` and its previews at `/{file name}.previewFile.{ext}`, and every file route takes that path or the object's `key`. Its other files are in its companion folder `{key}/` and keep the path after `{key}`. While the companion folder holds a file of the object's own name (`{key}/{file name}`), that file keeps the path `/{file name}`, and the object keeps the path `/` that no file route reaches.

`changeSource` and `changeUserId` describe how the current version of each file was created. When `changeSource` is `workflowExecution`, `changeWorkflowId` and `changeWorkflowExecutionId` name the workflow and execution that wrote it; they are `null` for every other change source, and in `basic` mode, which performs no per-object read.

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
    "key": "asset-001/models/building.ifc",
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
    "changeWorkflowId": null,
    "changeWorkflowExecutionId": null,
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

The `versions` list is present only when `includeVersions` is `true`. `changeWorkflowId` and `changeWorkflowExecutionId` name the workflow and execution that wrote the current version when `changeSource` is `workflowExecution`, and are `null` otherwise; each entry in `versions` carries the same pair for that version.

**Error Responses:**

| Status | Description                                                          |
| ------ | -------------------------------------------------------------------- |
| `400`  | Invalid parameters, missing `filePath` parameter, or file not found. |
| `403`  | Not authorized to view this file.                                    |
| `404`  | Asset not found.                                                     |
| `500`  | Internal server error.                                               |

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

| Status | Description                                                                                                                                                                                                                                                                                                    |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, source file not found, destination already exists, or the destination file name has a blocked extension. The file of an asset created from one Amazon S3 object cannot be moved or renamed: `The file of a single-file asset cannot be moved or renamed. Copy it to another path instead.` |
| `403`  | Not authorized to modify files in this asset.                                                                                                                                                                                                                                                                  |
| `500`  | Internal server error.                                                                                                                                                                                                                                                                                         |

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

| Status | Description                                                                                      |
| ------ | ------------------------------------------------------------------------------------------------ |
| `400`  | Invalid parameters, source file not found, or the destination file name has a blocked extension. |
| `403`  | Not authorized to copy files.                                                                    |
| `500`  | Internal server error.                                                                           |

---

### Delete File

`DELETE /database/{databaseId}/assets/{assetId}/deleteFile`

Permanently deletes a file from the asset. This removes all versions of the file from S3, and every version of the auxiliary data derived from it, such as previews and viewer files. It also removes the file's metadata, attributes and version records, so the asset's versions no longer list the file.

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

| Status | Description                                                                                                                                                                                                                                                                                                                                                    |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, `confirmPermanentDelete` not set to `true`, or file not found. With `isPrefix` set to `true`, a path that names the file of an asset created from one Amazon S3 object, or one of its previews, is refused: `A prefix operation cannot name the file of a single-file asset or its previews. Use the operation without the prefix option.` |
| `403`  | Not authorized to delete files in this asset.                                                                                                                                                                                                                                                                                                                  |
| `500`  | Internal server error.                                                                                                                                                                                                                                                                                                                                         |

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

| Status | Description                                                                                                                                                                                                                                                                                                                            |
| ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, or file not found or already archived. With `isPrefix` set to `true`, a path that names the file of an asset created from one Amazon S3 object, or one of its previews, is refused: `A prefix operation cannot name the file of a single-file asset or its previews. Use the operation without the prefix option.` |
| `403`  | Not authorized to archive files in this asset.                                                                                                                                                                                                                                                                                         |
| `500`  | Internal server error.                                                                                                                                                                                                                                                                                                                 |

---

### Unarchive File

`POST /database/{databaseId}/assets/{assetId}/unarchiveFile`

Restores a previously archived file by copying its most recent content version forward as the new current version. The new version is recorded with change source `fileUnarchive`. Archived preview files of the file are restored the same way.

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

| Status | Description                                            |
| ------ | ------------------------------------------------------ |
| `400`  | Invalid parameters, or file not found or not archived. |
| `403`  | Not authorized to unarchive files in this asset.       |
| `500`  | Internal server error.                                 |

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

| Status | Description                                       |
| ------ | ------------------------------------------------- |
| `400`  | Invalid parameters, or file or version not found. |
| `403`  | Not authorized to revert file versions.           |
| `500`  | Internal server error.                            |

---

### Set Primary File Type

`PUT /database/{databaseId}/assets/{assetId}/setPrimaryFile`

Designates a file as the primary representative of its file type within the asset. Only one file per type can be primary.

The file is written as a new Amazon S3 version with the same content, recorded with change source `fileMetadataUpdate` and, in `changeAssetFileVersionFrom`, the version it was copied from. That version does not start `fileUpload` workflow triggers.

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

| Status | Description                                        |
| ------ | -------------------------------------------------- |
| `400`  | Invalid parameters, or file not found or archived. |
| `403`  | Not authorized to modify file attributes.          |
| `500`  | Internal server error.                             |

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

A file's key, which is its asset's location followed by its `relativeKey`, may be at most 998 bytes of UTF-8 in a bucket registered at its root, and the length of the bucket's base prefix less than that otherwise. The upload is staged under a longer key that must fit the 1,024-byte Amazon S3 limit. A longer key is refused with `400` (`VAMS General Error: File key is too long to upload: a file key, including its asset location, may be at most N bytes`, where N is the bound for the asset's bucket), and no multipart upload is created for any file in the request. See [Amazon S3 quotas](../additional/quotas.md#amazon-s3).

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

| Status | Description                                                                                                                          |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| `400`  | Invalid parameters, asset not found, blocked file extension or MIME type, or a file key over the upload bound described above.       |
| `403`  | Not authorized to upload files to this asset.                                                                                        |
| `429`  | Rate limit exceeded: at most 20 upload initializations per user per minute. The `Retry-After` header gives the seconds to wait (60). |
| `500`  | Internal server error.                                                                                                               |

:::info[Blocked File Types]
For security, certain file extensions are blocked: `.jar`, `.java`, `.com`, `.php`, `.reg`, `.pif`, `.bak`, `.dll`, `.exe`, `.nat`, `.cmd`, `.lnk`, `.docm`, `.vbs`, `.bat`. Corresponding MIME types are also blocked. The destination file name of a move, rename or copy gets the same extension check.
:::

:::info[Preview File Extensions]
An `assetPreview` file, and any file-level preview companion (a `relativeKey` containing `.previewFile.`) inside an `assetFile` upload, must carry one of `.png`, `.jpg`, `.jpeg`, `.svg`, or `.gif`. For a companion the extension is everything after the `.previewFile.` marker, so `model.gltf.previewFile.p.png` is read as `.p.png` and refused. One offending file rejects the entire request with `400`; no multipart upload is created for any file in it.
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

Each entry in `files` is an object with `relativeKey`, `uploadIdS3`, and a `parts` array of `{ "PartNumber", "ETag" }` objects. An empty `parts` array completes a file initialized as `zero-byte`. For any other file it cancels the file: the multipart upload is aborted, nothing is stored, and the file's result has `success: false`.

A file over 1 GiB is processed asynchronously: its result has `success: true` and `largeFileAsynchronousHandling: true`, it counts toward `overallSuccess`, and it appears in the asset once the copy finishes.

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

| Status | Description                                            |
| ------ | ------------------------------------------------------ |
| `400`  | Invalid upload ID, upload not found, or missing parts. |
| `403`  | Not authorized to upload files to this asset.          |
| `500`  | Internal server error.                                 |

---

## Stream Endpoints

Both stream endpoints support two file-delivery modes, selected by the `ALWAYS_REDIRECT_TO_PRESIGNED` toggle in the corresponding Lambda handler (`streamAsset` and `streamAuxiliaryPreviewAsset`):

-   **Presigned-redirect mode** (`ALWAYS_REDIRECT_TO_PRESIGNED = True`): every request returns a `307 Temporary Redirect` whose `Location` is a short-lived Amazon S3 presigned URL. The client follows the redirect and fetches the bytes directly from the asset (or auxiliary) S3 bucket, which is CORS-enabled and supports native HTTP `Range` requests. This removes the 6&nbsp;MB Lambda response limit and the base64 encoding overhead, and offloads byte transfer from the Lambda to S3.
-   **Inline mode** (`ALWAYS_REDIRECT_TO_PRESIGNED = False`): files at or under approximately 4.4&nbsp;MB are returned inline as the response body (base64-encoded for binary content), and only larger files fall back to the `307` presigned redirect.

:::warning[Send no `Authorization` header to the presigned URL]
The redirect target carries its own authorization in the query string, and Amazon S3 rejects a request presenting two authorization mechanisms. Standard clients strip the header automatically on a cross-host redirect; a client that re-adds its VAMS token or API key to the redirected request receives `400 Bad Request` from Amazon S3.
:::

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

| Parameter             | Location | Type   | Required | Description                                                         |
| --------------------- | -------- | ------ | -------- | ------------------------------------------------------------------- |
| `databaseId`          | path     | string | Yes      | Database identifier.                                                |
| `assetId`             | path     | string | Yes      | Asset identifier.                                                   |
| `{proxy+}`            | path     | string | Yes      | The relative file path within the asset.                            |
| `versionId`           | query    | string | No       | Amazon S3 version ID of a specific file version.                    |
| `assetVersionId`      | query    | string | No       | VAMS asset version ID; the file version recorded in it is resolved. |
| `assetVersionIdAlias` | query    | string | No       | Alias of a VAMS asset version, resolved the same way.               |

Supply at most one of `versionId`, `assetVersionId` and `assetVersionIdAlias`. Supplying more than one
returns `400` with a message naming the three. Supplying none streams the current version.

**Response:**

In presigned-redirect mode, returns `307 Temporary Redirect` with a `Location` header pointing at an S3 presigned URL; the client follows it to retrieve the raw file content (with S3 serving `206 Partial Content` for range requests). In inline mode, files at or under ~4.4&nbsp;MB return the raw file content directly with appropriate `Content-Type` and `Content-Length` headers (`206 Partial Content` for range requests), and larger files return the `307` redirect.

**Error Responses:**

| Status | Description                                                                   |
| ------ | ----------------------------------------------------------------------------- |
| `403`  | Not authorized to stream this file, or the asset is not marked distributable. |
| `404`  | File not found.                                                               |
| `500`  | Internal server error.                                                        |

---

### Stream Auxiliary Preview Asset

`GET /database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}`

Streams auxiliary preview files (e.g., Potree octree data, generated viewer files) from the auxiliary S3 bucket. These files are non-versioned and typically generated by processing pipelines. Delivery follows the same presigned-redirect / inline modes described under [Stream Endpoints](#stream-endpoints).

`HEAD /database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}`

Returns file metadata without the file body.

**Request Parameters:**

| Parameter    | Location | Type   | Required | Description                                                    |
| ------------ | -------- | ------ | -------- | -------------------------------------------------------------- |
| `databaseId` | path     | string | Yes      | Database identifier.                                           |
| `assetId`    | path     | string | Yes      | Asset identifier.                                              |
| `{proxy+}`   | path     | string | Yes      | The viewer data path `{file}/preview/{data path}` (see below). |

A file's viewer data lives in its own `preview/` folder, so the path is the file, then `/preview/`, then the data path a pipeline wrote (for example `scans/site.laz/preview/PotreeViewer/metadata.json`). The file part names a file the asset holds in either form, for folder and single-file assets:

-   its path in the asset with its leading `/`, the file list's `relativePath` (`/scans/site.laz`, so the request path has two slashes after `stream`);
-   its full S3 key, the `key` the file list returns (`vams/a1/scans/site.laz` for an asset in a bucket registered under the base prefix `vams/`), the form the web viewers send for a file the list showed.

A leading `/` marks the path in the asset: `/a1/cloud.laz` in an asset stored at `a1/` is the file `a1/a1/cloud.laz` of a subfolder named like the asset, never `a1/cloud.laz`. A path without a leading `/` that starts with the key of a file the asset holds, followed by `/preview/`, is read as the full key, and any other path as the path in the asset. In an asset created from one Amazon S3 object, this route reads the object's own path `/{file name}` as the file `{key}/{file name}` of its companion folder, so name the object's viewer data by the object's key. URL-encode each segment and keep the slashes, so a viewer that reads the files beside `metadata.json` can resolve them. A path that names no file of this asset is refused with `400` and the `message` `The file path does not name a file in this asset.`

**Response:**

In presigned-redirect mode, returns `307 Temporary Redirect` with a `Location` header pointing at an S3 presigned URL that the client follows to retrieve the file. In inline mode, files at or under ~4.4&nbsp;MB return the raw file content directly with appropriate headers, and larger files return the `307` redirect.

**Error Responses:**

| Status | Description                                                                                                                                         |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters or unallowed file type, or the path names no file of this asset (`message` `The file path does not name a file in this asset.`). |
| `403`  | Not authorized to stream this file, or the asset is not marked distributable.                                                                       |
| `404`  | File not found.                                                                                                                                     |
| `500`  | Internal server error.                                                                                                                              |

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

| Status | Description                                              |
| ------ | -------------------------------------------------------- |
| `400`  | Invalid parameters, or asset or asset preview not found. |
| `403`  | Not authorized.                                          |
| `500`  | Internal server error.                                   |

---

### Delete Auxiliary Preview Files

`DELETE /database/{databaseId}/assets/{assetId}/deleteAuxiliaryPreviewAssetFiles`

Permanently deletes auxiliary preview files (e.g., Potree viewer data) from the auxiliary bucket for the specified asset, with every stored version.

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

| Status | Description                                                                                 |
| ------ | ------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, asset not found, or no auxiliary preview files found for the file path. |
| `403`  | Not authorized.                                                                             |
| `500`  | Internal server error.                                                                      |
