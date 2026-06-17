# Add-on API Reference

This page documents every VAMS REST API surface contributed by an optional add-on. Each add-on's endpoints live under `/addon/{addonName}/...` and are only deployed when the corresponding add-on is enabled in `infra/config/config.json`.

Add-on endpoints enforce the same VAMS two-tier Casbin authorization as the core API: API-tier (can the role reach the route) and object-tier (can the role access the specific data entity). Both tiers must allow for a call to succeed.

---

## Physna add-on

The Physna add-on surfaces endpoints that gate Physna-hosted functionality behind VAMS authorization. All routes below are only deployed when `app.addons.usePhysnaSync.enabled` is `true`.

See the [Physna Integration](../developer/physna-integration.md) developer guide for broader context on the add-on, its sync behavior, and its configuration.

### GET `/addon/physna/viewer`

Returns a JSON envelope describing whether the Physna viewer can currently be rendered for a given asset file, and — when it can — the values the frontend needs to embed Physna's hosted viewer in an `<iframe>` directly.

:::info
The `viewerToken` field in the `ready` response is short-lived and scoped by Physna. It is returned to the browser so the iframe can load Physna's hosted viewer directly, which avoids the Lambda response-size limits and URL-rewriting complexity of proxying the full viewer payload. VAMS's object-tier authorization runs on every call to this endpoint — a user who cannot see the asset cannot obtain a viewer token for it.
:::

#### Query parameters

| Parameter      | Type   | Required | Description                                                     |
| -------------- | ------ | -------- | --------------------------------------------------------------- |
| `databaseId`   | string | Yes      | VAMS database ID the asset belongs to.                          |
| `assetId`      | string | Yes      | VAMS asset ID.                                                  |
| `relativePath` | string | Yes      | Relative path of the file within the asset, beginning with `/`. |

#### Responses

All responses have `Content-Type: application/json`. The body always includes `status` and `message`; additional fields are populated for specific statuses.

| `status`               | HTTP  | Additional fields                                           | Meaning                                                                   |
| ---------------------- | ----- | ----------------------------------------------------------- | ------------------------------------------------------------------------- |
| `ready`                | `200` | `physnaAssetId`, `tenantId`, `viewerToken`, `physnaApiBase` | Asset is ready to render. Use the fields to build the Physna iframe URL.  |
| `indexing`             | `200` | `physnaState`                                               | Physna is still indexing the asset. Poll again shortly.                   |
| `not_synced`           | `200` | —                                                           | File has not appeared in Physna yet. Poll again shortly.                  |
| `failed`               | `200` | `physnaState`                                               | Physna reported a permanent failure state; the viewer cannot render.      |
| `unsupported`          | `400` | —                                                           | File extension is not in the Physna-supported list.                       |
| `not_found`            | `404` | —                                                           | Asset does not exist in VAMS.                                             |
| `forbidden`            | `403` | —                                                           | Caller lacks API- or object-level access.                                 |
| `invalid_request`      | `400` | —                                                           | Query parameters failed validation.                                       |
| `method_not_allowed`   | `405` | —                                                           | Non-`GET` request.                                                        |
| `upstream_unavailable` | `502` | —                                                           | Physna API could not be reached, or the viewer token could not be minted. |
| `internal_error`       | `500` | —                                                           | Unexpected server error.                                                  |

#### Example request

```
GET /addon/physna/viewer?databaseId=engineering&assetId=pump-housing-v2&relativePath=%2Fpump.stp
```

#### Example `ready` response

```json
{
    "status": "ready",
    "message": "Physna viewer is ready.",
    "tenantId": "bc336bab-e3e4-4c50-bd00-9d6cbbd9f194",
    "physnaAssetId": "8d65af7c-5412-404b-a494-fbd2cdb62442",
    "viewerToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "physnaApiBase": "https://app-api.physna.com/v3"
}
```

The frontend uses these fields to build the iframe URL:

```
${physnaApiBase}/tenants/${tenantId}/viewer/asset
  ?assetId=${physnaAssetId}
  &token=${viewerToken}
  &theme=dark
  &parentOrigin=${window.location.origin}
```

#### Example `indexing` response

```json
{
    "status": "indexing",
    "message": "Physna is still indexing this file. Please check back shortly.",
    "physnaState": "indexing"
}
```

#### Authorization

-   **API tier:** The caller's role must include access to `/addon/physna/viewer` via a permission constraint with `objectType: "api"` or `objectType: "web"`.
-   **Object tier:** The caller must have `GET` permission on the specific `databaseId` / `assetId` combination (`objectType: "asset"`).

Both tiers must allow for the endpoint to succeed. Failures return `status: "forbidden"` with HTTP `403`.
