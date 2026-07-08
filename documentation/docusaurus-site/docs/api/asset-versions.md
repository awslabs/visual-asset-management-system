# Asset Versions API

The Asset Versions API provides version management for assets, including creating version snapshots, updating version metadata, archiving versions, and reverting to previous versions. Each version captures the state of an asset's files at a point in time.

:::info[Authorization]
All endpoints require a valid JWT token in the `Authorization` header. Asset version operations are subject to two-tier Casbin authorization on the parent asset.
:::

---

## List asset versions

Retrieves all versions for an asset.

```
GET /database/{databaseId}/assets/{assetId}/getVersions
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `assetId`    | string | Yes      | Asset identifier    |

### Response

```json
{
    "versions": [
        {
            "Version": "1",
            "DateModified": "2026-03-15T10:30:00Z",
            "Comment": "Initial version",
            "description": "",
            "createdBy": "user@example.com",
            "isCurrent": true,
            "fileCount": 12,
            "versionAlias": "v1.0",
            "isArchived": false,
            "assetId": "my-asset",
            "databaseId": "my-database"
        }
    ],
    "NextToken": null
}
```

---

## Get a specific asset version

Retrieves details for a specific asset version.

```
GET /database/{databaseId}/assets/{assetId}/getVersion/{assetVersionId}
```

### Path parameters

| Parameter        | Type   | Required | Description         |
| ---------------- | ------ | -------- | ------------------- |
| `databaseId`     | string | Yes      | Database identifier |
| `assetId`        | string | Yes      | Asset identifier    |
| `assetVersionId` | string | Yes      | Version identifier  |

### Response

Returns a single version object with full details including file listings.

---

## Create an asset version

Creates a new version snapshot of the asset's current state.

```
POST /database/{databaseId}/assets/{assetId}/createVersion
```

### Path parameters

| Parameter    | Type   | Required | Description         |
| ------------ | ------ | -------- | ------------------- |
| `databaseId` | string | Yes      | Database identifier |
| `assetId`    | string | Yes      | Asset identifier    |

### Request body

| Field            | Type    | Required | Description                                                                                          |
| ---------------- | ------- | -------- | ---------------------------------------------------------------------------------------------------- |
| `comment`        | string  | Yes      | Comment for the version (1-256 characters).                                                          |
| `useLatestFiles` | boolean | No       | When `true`, snapshot the latest files in the asset's S3 bucket. Defaults to `false`.                |
| `files`          | array   | No       | Explicit list of files and their S3 versions to include. Required unless `useLatestFiles` is `true`. |
| `versionAlias`   | string  | No       | Human-readable version alias (up to 64 characters).                                                  |

Provide either `useLatestFiles` set to `true` or a non-empty `files` list; the two are mutually exclusive. Each entry in `files` is an object with `relativeKey`, `versionId` (S3 version ID), and an optional `isArchived` flag.

### Request body example

```json
{
    "comment": "Updated building model with revised floor 3",
    "useLatestFiles": true,
    "versionAlias": "v1.1"
}
```

### Response

```json
{
    "success": true,
    "message": "Asset version created successfully",
    "assetId": "my-asset",
    "assetVersionId": "v-abc123def",
    "operation": "create",
    "timestamp": "2026-03-15T10:30:00Z"
}
```

---

## Update an asset version

Updates the alias or comment on an existing asset version.

```
PUT /database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}
```

### Path parameters

| Parameter        | Type   | Required | Description         |
| ---------------- | ------ | -------- | ------------------- |
| `databaseId`     | string | Yes      | Database identifier |
| `assetId`        | string | Yes      | Asset identifier    |
| `assetVersionId` | string | Yes      | Version identifier  |

### Request body

| Field          | Type   | Required | Description                     |
| -------------- | ------ | -------- | ------------------------------- |
| `versionAlias` | string | No       | Human-readable version alias    |
| `comment`      | string | No       | Updated comment for the version |

### Request body example

```json
{
    "versionAlias": "v2.0-release",
    "comment": "Production-ready version"
}
```

### Response

```json
{
    "success": true,
    "message": "Asset version updated successfully",
    "assetId": "my-asset",
    "assetVersionId": "v-abc123",
    "operation": "update",
    "timestamp": "2026-03-15T10:30:00Z"
}
```

---

## Archive an asset version

Archives an asset version, making it read-only.

```
POST /database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/archive
```

### Path parameters

| Parameter        | Type   | Required | Description         |
| ---------------- | ------ | -------- | ------------------- |
| `databaseId`     | string | Yes      | Database identifier |
| `assetId`        | string | Yes      | Asset identifier    |
| `assetVersionId` | string | Yes      | Version identifier  |

### Response

```json
{
    "success": true,
    "message": "Asset version archived successfully",
    "assetId": "my-asset",
    "assetVersionId": "v-abc123",
    "operation": "archive",
    "timestamp": "2026-03-15T10:30:00Z"
}
```

### Error responses

| Status | Description                                                                                                 |
| ------ | ----------------------------------------------------------------------------------------------------------- |
| `400`  | Invalid parameters, or an attempt to archive the current version. Set a different version as current first. |
| `403`  | Not authorized                                                                                              |
| `500`  | Internal server error                                                                                       |

---

## Unarchive an asset version

Restores a previously archived asset version.

```
POST /database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/unarchive
```

### Path parameters

Same as [Archive an asset version](#archive-an-asset-version).

### Response

```json
{
    "success": true,
    "message": "Asset version unarchived successfully",
    "assetId": "my-asset",
    "assetVersionId": "v-abc123",
    "operation": "unarchive",
    "timestamp": "2026-03-15T10:30:00Z"
}
```

---

## Revert to an asset version

Reverts the asset to the state captured in a specific version.

```
POST /database/{databaseId}/assets/{assetId}/revertAssetVersion/{assetVersionId}
```

### Path parameters

| Parameter        | Type   | Required | Description                     |
| ---------------- | ------ | -------- | ------------------------------- |
| `databaseId`     | string | Yes      | Database identifier             |
| `assetId`        | string | Yes      | Asset identifier                |
| `assetVersionId` | string | Yes      | Version identifier to revert to |

### Request body

| Field            | Type    | Required | Description                                                                        |
| ---------------- | ------- | -------- | ---------------------------------------------------------------------------------- |
| `comment`        | string  | Yes      | Comment for the new version created by the revert (1-256 characters).              |
| `revertMetadata` | boolean | No       | When `true`, also revert the asset's metadata and attributes. Defaults to `false`. |

### Request body example

```json
{
    "comment": "Reverting to floor plan revision 2",
    "revertMetadata": false
}
```

### Response

```json
{
    "success": true,
    "message": "Asset version reverted successfully",
    "assetId": "my-asset",
    "assetVersionId": "v-abc123",
    "operation": "revert",
    "timestamp": "2026-03-15T10:30:00Z"
}
```

### Error responses

| Status | Description                             |
| ------ | --------------------------------------- |
| `400`  | Invalid parameters or version not found |
| `403`  | Not authorized                          |
| `500`  | Internal server error                   |

---

## Related resources

-   [Assets API](assets.md) -- Manage the assets that versions belong to
-   [Files API](files.md) -- Manage files within asset versions
-   [Subscriptions API](subscriptions.md) -- Subscribe to asset version change notifications
-   [Workflows API](workflows.md) -- Execute workflows that process assets and create outputs
