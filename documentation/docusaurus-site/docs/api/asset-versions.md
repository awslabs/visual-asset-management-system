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
    "message": {
        "versions": [
            {
                "assetVersionId": "v-abc123",
                "databaseId": "my-database",
                "assetId": "my-asset",
                "description": "Initial version",
                "versionAlias": "v1.0",
                "isArchived": false,
                "dateCreated": "2026-03-15T10:30:00Z",
                "createdBy": "user@example.com"
            }
        ]
    }
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

| Field         | Type   | Required | Description                     |
| ------------- | ------ | -------- | ------------------------------- |
| `description` | string | No       | Description for the new version |
| `comment`     | string | No       | Comment for the version         |

### Request body example

```json
{
    "description": "Added updated floor plan",
    "comment": "Updated building model with revised floor 3"
}
```

### Response

```json
{
    "message": {
        "assetVersionId": "v-abc123def",
        "message": "Asset version created successfully"
    }
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
    "message": "Asset version updated successfully"
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
    "message": "Asset version archived successfully"
}
```

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
    "message": "Asset version unarchived successfully"
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

### Response

```json
{
    "message": "Asset version reverted successfully"
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
