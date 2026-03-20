# Files

This page documents the file operation endpoints in the VAMS API. These endpoints manage individual files within assets, including listing, moving, copying, archiving, uploading, and streaming.

For asset-level operations, see [Assets](assets.md). For file metadata, see [Metadata](metadata.md).

---

## Concepts

- **File**: An individual object stored in S3 within an asset's directory structure. Files can be organized in folders.
- **File Version**: S3 object versions tracked through bucket versioning. VAMS also tracks file versions within asset version snapshots.
- **Primary File Type**: A designation that marks a file as the primary representative of a particular type within an asset (e.g., the primary `.ifc` file).
- **Preview File**: A generated preview image (`.previewFile.gif`, `.previewFile.jpg`, `.previewFile.png`) associated with a specific file.
- **Archive**: Soft-deletion of a file using S3 delete markers. Archived files can be unarchived.

---

## Endpoints

### List Files

`GET /database/{databaseId}/assets/{assetId}/listFiles`

Returns a list of all files in the specified asset, including file metadata, sizes, and archive status.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |
| `maxItems` | query | integer | No | Maximum number of files to return. |
| `pageSize` | query | integer | No | Page size for pagination. |
| `startingToken` | query | string | No | Continuation token from a previous response. |

**Response:**

```json
{
    "files": [
        {
            "key": "/models/building.ifc",
            "size": 15728640,
            "lastModified": "2024-06-15T10:30:00Z",
            "etag": "\"d41d8cd98f00b204e9800998ecf8427e\"",
            "isArchived": false,
            "isFolder": false,
            "primaryType": "ifc",
            "hasPreview": true,
            "versionId": "abc123"
        },
        {
            "key": "/textures/",
            "size": 0,
            "lastModified": "2024-06-15T10:30:00Z",
            "isArchived": false,
            "isFolder": true
        }
    ],
    "NextToken": "eyJ..."
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized to list files in this asset. |
| `404` | Database or asset not found. |
| `500` | Internal server error. |

---

### Get File Info

`GET /database/{databaseId}/assets/{assetId}/fileInfo`

Retrieves detailed information about a specific file, including S3 metadata, version history, and archive status.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |
| `key` | query | string | Yes | The relative file path (e.g., `/models/building.ifc`). |

**Response:**

```json
{
    "key": "/models/building.ifc",
    "size": 15728640,
    "lastModified": "2024-06-15T10:30:00Z",
    "etag": "\"d41d8cd98f00b204e9800998ecf8427e\"",
    "contentType": "application/octet-stream",
    "isArchived": false,
    "versionId": "abc123",
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

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or missing `key` parameter. |
| `403` | Not authorized to view this file. |
| `404` | File not found. |
| `500` | Internal server error. |

---

### Move/Rename File

`POST /database/{databaseId}/assets/{assetId}/moveFile`

Moves or renames a file within the asset. This copies the file to the new location and deletes the original.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |

**Request Body:**

```json
{
    "sourcePath": "/models/old-name.ifc",
    "destinationPath": "/models/new-name.ifc"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `sourcePath` | string | Yes | Current relative file path. |
| `destinationPath` | string | Yes | New relative file path. |

**Response:**

```json
{
    "message": "File moved successfully",
    "sourcePath": "/models/old-name.ifc",
    "destinationPath": "/models/new-name.ifc"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters, source file not found, or destination already exists. |
| `403` | Not authorized to modify files in this asset. |
| `500` | Internal server error. |

---

### Copy File

`POST /database/{databaseId}/assets/{assetId}/copyFile`

Copies a file within the same asset or to a different asset. Supports cross-database copying when `destinationDatabaseId` is provided.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Source database identifier. |
| `assetId` | path | string | Yes | Source asset identifier. |

**Request Body:**

```json
{
    "sourcePath": "/models/building.ifc",
    "destinationPath": "/models/building-copy.ifc",
    "destinationAssetId": "asset-002",
    "destinationDatabaseId": "other-database"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `sourcePath` | string | Yes | Source file relative path. |
| `destinationPath` | string | Yes | Destination file relative path. |
| `destinationAssetId` | string | No | Target asset ID (defaults to same asset). |
| `destinationDatabaseId` | string | No | Target database ID for cross-database copy. |

**Response:**

```json
{
    "message": "File copied successfully",
    "sourcePath": "/models/building.ifc",
    "destinationPath": "/models/building-copy.ifc"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or source file not found. |
| `403` | Not authorized to copy files. |
| `500` | Internal server error. |

---

### Delete File

`DELETE /database/{databaseId}/assets/{assetId}/deleteFile`

Permanently deletes a file from the asset. This removes all versions of the file from S3.

:::danger[Irreversible Operation]
This permanently deletes the file and all its versions. Consider using [Archive File](#archive-file) for soft-deletion instead.
:::


**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |
| `key` | query | string | Yes | Relative file path to delete. |

**Response:**

```json
{
    "message": "File deleted successfully"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or missing `key`. |
| `403` | Not authorized to delete files in this asset. |
| `404` | File not found. |
| `500` | Internal server error. |

---

### Archive File

`DELETE /database/{databaseId}/assets/{assetId}/archiveFile`

Soft-deletes a file by creating an S3 delete marker. The file can be restored using [Unarchive File](#unarchive-file).

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |
| `key` | query | string | Yes | Relative file path to archive. |

**Response:**

```json
{
    "message": "File archived successfully"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized to archive files in this asset. |
| `404` | File not found. |
| `500` | Internal server error. |

---

### Unarchive File

`POST /database/{databaseId}/assets/{assetId}/unarchiveFile`

Restores a previously archived file by removing the S3 delete marker.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |

**Request Body:**

```json
{
    "key": "/models/building.ifc"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `key` | string | Yes | Relative file path to unarchive. |

**Response:**

```json
{
    "message": "File unarchived successfully"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized to unarchive files in this asset. |
| `404` | File not found or not archived. |
| `500` | Internal server error. |

---

### Create Folder

`POST /database/{databaseId}/assets/{assetId}/createFolder`

Creates a new folder (zero-byte S3 object with trailing slash) within the asset's directory structure.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |

**Request Body:**

```json
{
    "folderPath": "/new-folder/"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `folderPath` | string | Yes | The folder path to create (must end with `/`). |

**Response:**

```json
{
    "message": "Folder created successfully",
    "folderPath": "/new-folder/"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or folder already exists. |
| `403` | Not authorized to create folders in this asset. |
| `500` | Internal server error. |

---

### Revert File Version

`POST /database/{databaseId}/assets/{assetId}/revertFileVersion/{versionId}`

Reverts a file to a specific previous S3 version by copying the old version as the new current version.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |
| `versionId` | path | string | Yes | The S3 version ID to revert to. |

**Request Body:**

```json
{
    "key": "/models/building.ifc"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `key` | string | Yes | Relative file path to revert. |

**Response:**

```json
{
    "message": "File version reverted successfully",
    "key": "/models/building.ifc",
    "newVersionId": "def456"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters or version not found. |
| `403` | Not authorized to revert file versions. |
| `404` | File or version not found. |
| `500` | Internal server error. |

---

### Set Primary File Type

`PUT /database/{databaseId}/assets/{assetId}/setPrimaryFile`

Designates a file as the primary representative of its file type within the asset. Only one file per type can be primary.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |

**Request Body:**

```json
{
    "key": "/models/building.ifc",
    "primaryType": "ifc"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `key` | string | Yes | Relative file path. |
| `primaryType` | string | Yes | The file type designation. |

**Response:**

```json
{
    "message": "Primary file type set successfully",
    "key": "/models/building.ifc",
    "primaryType": "ifc"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters. |
| `403` | Not authorized to modify file attributes. |
| `404` | File not found. |
| `500` | Internal server error. |

---

## Upload Endpoints

### Upload File

`POST /uploads`

Initiates a file upload by returning presigned S3 URLs. For small files, a single presigned PUT URL is returned. For large files (multipart upload), the request is queued for asynchronous processing via SQS.

**Request Body:**

```json
{
    "databaseId": "my-database",
    "assetId": "asset-001",
    "key": "/models/building.ifc",
    "contentType": "application/octet-stream",
    "fileSize": 15728640,
    "numParts": 1
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `databaseId` | string | Yes | Target database identifier. |
| `assetId` | string | Yes | Target asset identifier. |
| `key` | string | Yes | Relative file path for the upload. |
| `contentType` | string | No | MIME type of the file. |
| `fileSize` | integer | No | File size in bytes. |
| `numParts` | integer | No | Number of multipart upload parts (for large files, max 10,000). |

**Response:**

```json
{
    "uploadId": "upload-12345",
    "presignedUrls": [
        "https://bucket.s3.amazonaws.com/...?X-Amz-..."
    ],
    "s3UploadId": "multipart-upload-id"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid parameters, blocked file extension, or blocked MIME type. |
| `403` | Not authorized to upload files to this asset. |
| `500` | Internal server error. |

:::info[Blocked File Types]
For security, certain file extensions are blocked: `.jar`, `.java`, `.com`, `.php`, `.reg`, `.pif`, `.bak`, `.dll`, `.exe`, `.nat`, `.cmd`, `.lnk`, `.docm`, `.vbs`, `.bat`. Corresponding MIME types are also blocked.
:::


---

### Complete Upload

`POST /uploads/{uploadId}/complete`

Completes a multipart file upload by signaling that all parts have been uploaded.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `uploadId` | path | string | Yes | The upload identifier from the initial upload request. |

**Request Body:**

```json
{
    "parts": [
        {
            "PartNumber": 1,
            "ETag": "\"d41d8cd98f00b204e9800998ecf8427e\""
        }
    ]
}
```

**Response:**

```json
{
    "message": "Upload completed successfully"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `400` | Invalid upload ID or missing parts. |
| `500` | Internal server error. |

---

## Stream Endpoints

### Stream Asset File

`GET /database/{databaseId}/assets/{assetId}/download/stream/{proxy+}`

Streams a file from an asset through the API Gateway. Supports HTTP range requests for partial content delivery, which enables seeking in video/audio files and progressive loading of large files.

`HEAD /database/{databaseId}/assets/{assetId}/download/stream/{proxy+}`

Returns file metadata (size, content type) without the file body.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |
| `{proxy+}` | path | string | Yes | The relative file path within the asset. |
| `v` | query | string | No | S3 version ID for a specific file version. |
| `avid` | query | string | No | VAMS asset version ID. |

**Response:**

Returns the raw file content with appropriate `Content-Type` and `Content-Length` headers. For range requests, returns `206 Partial Content`.

**Error Responses:**

| Status | Description |
|--------|-------------|
| `403` | Not authorized to stream this file. |
| `404` | File not found. |
| `500` | Internal server error. |

---

### Stream Auxiliary Preview Asset

`GET /database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}`

Streams auxiliary preview files (e.g., Potree octree data, generated viewer files) from the auxiliary S3 bucket. These files are non-versioned and typically generated by processing pipelines.

`HEAD /database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}`

Returns file metadata without the file body.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |
| `{proxy+}` | path | string | Yes | The relative file path within the auxiliary bucket. |

**Response:**

Returns the raw file content with appropriate headers.

**Error Responses:**

| Status | Description |
|--------|-------------|
| `403` | Not authorized to stream this file. |
| `404` | File not found. |
| `500` | Internal server error. |

---

## Preview Management

### Delete Asset Preview

`DELETE /database/{databaseId}/assets/{assetId}/deleteAssetPreview`

Deletes the asset-level preview image.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |

**Response:**

```json
{
    "message": "Asset preview deleted successfully"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `403` | Not authorized. |
| `404` | Asset or preview not found. |
| `500` | Internal server error. |

---

### Delete Auxiliary Preview Files

`DELETE /database/{databaseId}/assets/{assetId}/deleteAuxiliaryPreviewAssetFiles`

Deletes auxiliary preview files (e.g., Potree viewer data) from the auxiliary bucket for the specified asset.

**Request Parameters:**

| Parameter | Location | Type | Required | Description |
|-----------|----------|------|----------|-------------|
| `databaseId` | path | string | Yes | Database identifier. |
| `assetId` | path | string | Yes | Asset identifier. |

**Response:**

```json
{
    "message": "Auxiliary preview files deleted successfully"
}
```

**Error Responses:**

| Status | Description |
|--------|-------------|
| `403` | Not authorized. |
| `404` | Asset not found. |
| `500` | Internal server error. |
