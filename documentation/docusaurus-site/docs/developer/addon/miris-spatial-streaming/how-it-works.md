---
sidebar_label: How it works
---

# How the Miris Add-on Works

## How the add-on works

The viewer asks VAMS at render time whether a USD file has a stream. Both API routes read the `miris_*` keys of the requested file server side, so the browser never supplies a Miris asset UUID and a caller can reach only a stream that the file itself references.

1. **Stream status.** The viewer calls `GET /addon/miris/asset-status` with the file's path. VAMS authorizes the caller, reads the file's stream metadata, reads the Miris asset with the Integration Key to check its [binding tags](#binding-tags), and reports the stream's state and whether the stream is stale (see [Stale streams](#stale-streams)).
2. **Viewer key.** When the state allows streaming, the viewer calls `POST /addon/miris/viewer-key`. VAMS repeats the authorization, applies the [asset distribution rule](#which-assets-can-be-streamed-and-uploaded) and the binding check, and returns a viewer key minted for the file's Miris asset (see [Viewer keys](#viewer-keys)), which the viewer holds in memory for that open only.
3. **Streaming.** The viewer loads the Miris web SDK and streams the asset directly from Miris. VAMS never proxies or stores the streamed geometry.

Both routes enforce Tier 1 on the route and Tier 2 `GET` on the asset before any metadata is read; the viewer key route checks a read even though it is a `POST`. See the [Add-on API reference](../../../api/addon.md#miris-spatial-streaming-add-on) for the request and response shapes.

### Binding tags

A Miris asset is bound to a VAMS asset by two tags on the Miris asset: `vams-asset-{assetId}` and `vams-database-{databaseId}`. Both are required, because an asset ID is unique only within its database. The routes accept a Miris asset only when it carries both tags of the VAMS asset being viewed.

A Miris asset that lacks either tag, that the Integration Key cannot find, or that Miris reports `deleted` is `notBound`: the viewer key route returns `403` with `state: notBound` and no key, and the viewer shows an error that names both tags. The upload pipeline always adds both tags. For a Miris asset linked by hand, add `vams-asset-<assetId>` and `vams-database-<databaseId>` in the Miris Portal or with `miris asset update --add-tags`.

### Which assets can be streamed and uploaded

The add-on applies the rule the core download and stream routes apply before they serve an asset's files (`common/downloadEligibility.py` and `common/compliance/quarantineGuard.py`). The viewer key route returns a key, and the upload pipeline uploads, only for an asset that is distributable and, while `app.compliance.quarantineBlocksDownload` is `true`, is not quarantined without a granted exception. The key streams only the file's Miris asset, so the rule decides which assets are streamed. The rule is checked after both authorization tiers, so a caller who may not read the asset receives the authorization error and learns nothing about the asset's state.

-   **Viewer key route.** Returns `400` with the reason, "Asset not distributable" or "Asset is quarantined and cannot be downloaded", and no key. The viewer shows the reason.
-   **Upload pipeline.** The run fails at `vamsExecute`, before anything is claimed or sent to Miris, with the reason as the workflow's failure cause. The viewer and the execution details show it.

The rule is checked when a key is requested and when a run starts, not afterward. A run checks it once, at `vamsExecute`, so an asset that is quarantined or stops being distributable while its run waits in the job queue is still uploaded. Nothing in VAMS removes an asset that is already in Miris: a later change to the VAMS asset leaves its Miris assets in place, and a viewer key that covers them can still stream them. When an asset is quarantined or made non-distributable, delete its Miris assets in the Miris Portal or with `miris asset delete`. The current one is the `miris_asset_uuid` in the file's [stream metadata](#stream-metadata); earlier uploads of the same asset carry the `vams-asset-{assetId}` [binding tag](#binding-tags).

The status route still reports the stream recorded on the file, and returns no key.

## Viewer plugin

| Plugin ID             | Extensions                        | Priority | Feature flags                       |
| --------------------- | --------------------------------- | -------- | ----------------------------------- |
| `miris-stream-viewer` | `.usd`, `.usda`, `.usdc`, `.usdz` | 0        | `MIRIS_ADDON` and `ALLOWUNSAFEEVAL` |

The entry in `web/src/visualizerPlugin/config/addonViewerConfig.json` ships enabled, is offered only when the deployment publishes `MIRIS_ADDON` and `ALLOWUNSAFEEVAL` (see [Configuration](./enable.md#configuration)), and sets `autoSelect: true`, so the viewer opens USD files automatically in visualize mode while the Needle USD Viewer and the Three.js Viewer stay in the viewer selector.

| Status `state`                    | What the viewer shows                                                                                                                                                                                                                                                 |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `notStreamed`                     | A "Not yet streamed with Miris" note that points to the file manager's **Automation** action and the **Miris Spatial Streaming Upload** workflow, and a **Stream with Miris** button (see [Starting an upload from the viewer](#starting-an-upload-from-the-viewer)). |
| `processing`, `preview`, or other | A "Miris is preparing this asset" overlay with the current state and the note "This can take a few hours." The viewer checks the status again every 90 seconds and loads the stream once the state leaves this group.                                                 |
| `streamable` or `unknown`         | Requests a viewer key and streams the asset, labeled with `miris_display_name`. `unknown` means the state could not be established; the viewer still attempts the stream.                                                                                             |
| `notBound`                        | An error that names both [binding tags](#binding-tags).                                                                                                                                                                                                               |
| `invalidUuid`                     | An error stating that `miris_asset_uuid` is not a valid UUID.                                                                                                                                                                                                         |
| `error` or `failed`               | An error stating that Miris processing failed.                                                                                                                                                                                                                        |

Each error offers a **Retry** button. A failed viewer key request shows a `400` refusal with the API's reason (see [Which assets can be streamed and uploaded](#which-assets-can-be-streamed-and-uploaded)), `403` as a permission error, `503` as "Miris streaming is not configured for this deployment", `404` as the "Not yet streamed with Miris" note, a request the AWS WAF web ACL blocked with the blocking message, and anything else as an access error.

When the status reports `stale: true`, the viewer streams the recorded asset under a banner that says the asset has changed since it was streamed: the file, or a sublayer or texture it uses, differs from the one in the stream (see [Stale streams](#stale-streams)). When `stale` is null, the viewer compares the recorded root version (`miris_source_version_id`) with the version being viewed instead. The routes read the file's current metadata, so a historical asset version streams the Miris asset recorded now.

### Starting an upload from the viewer

**Stream with Miris** runs the GLOBAL `miris-upload` workflow on the viewed file as a manual run with the pipeline's default template, so it always uploads (see [Duplicate uploads and re-runs](./upload-pipeline.md#duplicate-uploads-and-re-runs)). The button appears only when the deployment publishes `MIRIS_UPLOAD`, the file has a USD extension, and the user's role may call `POST /workflows/{workflowDatabaseId}/{workflowId}/execute`. When the role may also list the asset's workflow executions, an unfinished `miris-upload` run replaces the button with the note "A Miris upload for this asset is running." and a **View execution** link. After the button starts a run, the viewer shows the same note and follows that run until it ends, reading the execution's status every 30 seconds. When the run succeeds, the viewer reads the stream status again and streams the asset or shows the preparing overlay. When it fails, the viewer shows the failure reason the workflow recorded and offers the button again; when the workflow skipped it because another upload was running, the viewer says so. A role that may not read the execution's details is followed through the asset's execution list, which reports the run's failure reason or skip in the same way, or else through the stream status every 90 seconds, and a source whose read fails twice in a row gives way to the next. When the source still cannot see the run after 90 minutes (the list does not show it, or the file records no stream), the viewer reports the result as unknown and offers the button again.

## Viewer keys

Each viewer open mints a key for the file's one Miris asset with the Integration Key, after the binding check. The key expires `viewerKeyTtlMinutes` (1 to 60, default 15) after it is minted, and `expiresAt` in the response is that expiry. No shared viewer key is deployed or stored.

VAMS records each key's Miris key ID in the add-on's viewer key table, with a `ttl` one hour after the key's expiry. When Amazon DynamoDB TTL removes the row, the cleanup Lambda function deletes the key from the Miris workspace; TTL deletion is lazy, so an expired key can remain there for a while. A cleanup batch that still fails after its retries goes to a dead-letter queue. When a minted key cannot be recorded, VAMS deletes it from Miris and the request fails with `500`.

Expiry and revocation apply when a stream starts; an open stream is not ended. The key is visible in the browser, so a short lifetime limits the exposure of a copied key.

Each key is written to the `AUTHOTHER` audit log as `mirisViewerKeyMint`, without the key material, with `userId`, `databaseId`, `assetId`, `filePath`, `mirisAssetUuid`, `mirisViewerKeyId` and `expiresAt`. A successful status request records `mirisAssetStatusGet` in the actions audit log. See [Audit Logging](../../audit-logging.md).

## Stream metadata

The Miris stream for a USD file is described by five keys in that file's metadata:

| Key                         | Type     | Meaning                                                                                                                                                                    |
| --------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `miris_asset_uuid`          | `string` | Miris asset to stream. The only key the viewer requires; a missing or empty value means the file is not streamed.                                                          |
| `miris_source_version_id`   | `string` | Amazon S3 version ID of the USD file the stream was made from. Empty when the bucket is unversioned. Drives the different-version banner.                                  |
| `miris_uploaded_at`         | `date`   | When the Miris upload completed (ISO 8601 UTC, for example `2026-10-14T09:30:00Z`).                                                                                        |
| `miris_display_name`        | `string` | Label shown over the stream in the viewer. The upload pipeline sets it to the name of the Miris asset.                                                                     |
| `miris_dependency_manifest` | `string` | SHA-256 ID of the [dependency manifest](./upload-pipeline.md#what-the-pipeline-writes) of the files the stream was built from. Optional; hand-edited metadata may omit it. |

The keys are indexed for search like any other metadata, so with Amazon OpenSearch Service, streamed files can be found by searching on `MD_.miris_asset_uuid`.

When the add-on is enabled, deployment seeds a GLOBAL `fileMetadata` schema named **Miris stream** (ID `miris-stream-file-schema`) that defines the five keys as optional fields for `.usd`, `.usda`, `.usdc`, and `.usdz` files. Later deployments refresh it only while no user has edited it, and disabling the add-on or deleting the stack does not remove it. In a database that restricts metadata to schema-defined fields (see [Schema enforcement](../../../concepts/metadata-and-schemas.md#schema-enforcement)), it is an applicable schema for USD files, so users cannot add keys that no schema defines to USD files there.

### Stale streams

Miris bakes every file of the dependency tree into the stream: the root layer, its sublayers and references, and the textures and other assets they use. The status route reports `stale: true` when any file listed in the file's [dependency manifest](./upload-pipeline.md#what-the-pipeline-writes) has been removed from the asset or changed since the upload, so an edited texture or lookdev layer marks the stream stale even though the root file is unchanged. It compares the manifest's ETags with one listing of the asset, and reports `false` when every file matches. `stale` is null when the file records no `miris_dependency_manifest` (hand-edited metadata), or when the manifest or the listing cannot be read in time; only `miris_source_version_id` then says whether the stream matches the root file. A stale stream keeps streaming; run the **Miris Spatial Streaming Upload** workflow to upload the current files.

### Editing the stream metadata

Users with `PUT` permission on the asset can edit the keys through the **Miris stream** fields in the file metadata editor, the API, or the CLI. To stream a USD file from an asset already on Miris, set `miris_asset_uuid` to that asset's UUID and add the [binding tags](#binding-tags) to the Miris asset; leaving `miris_source_version_id` empty streams without the different-version banner. Clearing `miris_asset_uuid` returns the file to "Not yet streamed".

Moving or renaming the file carries the keys to the new path, and deleting the file deletes them; neither changes the asset on Miris. Running the upload workflow again overwrites the keys; see [What the pipeline writes](./upload-pipeline.md#what-the-pipeline-writes).

## Troubleshooting

-   **"Not yet streamed with Miris"**: the file has no `miris_asset_uuid`. Choose **Stream with Miris**, run the **Miris Spatial Streaming Upload** workflow on the file, or set the stream metadata by hand.
-   **"The Miris asset recorded for this file is not linked to this VAMS asset"**: add the [binding tags](#binding-tags) to the Miris asset, or run the upload workflow again. The error also appears when the Miris asset was deleted or is not in the Integration Key's workspace.
-   **"Miris streaming is not configured for this deployment"**: the Integration Key secret ARN or the Miris API base URL is missing from the function environment. Check the add-on configuration and redeploy.
-   **"The Miris viewer failed to load"**: the SDK did not load within 60 seconds. Check the browser's network panel and console. A `404` or HTML response for `/viewers/miris/miris-sdk.js` means the web application was built without the SDK bundle: confirm that the configuration the web application was built with enables `app.addons.useMirisStreaming` (see [Enable](./enable.md#configuration)) and that `postinstall` reports "Miris: Installation complete!", then rebuild and redeploy the web application.
-   **"Timed out waiting for Miris to start streaming"**: no first frame arrived within 60 seconds. Check for an expired viewer key, a key from another Miris workspace, or a blocked `*.miris.com` connection.
-   **The "preparing" overlay never clears**: the asset stays at `preview`. Promote it in the Miris Portal.
-   **The status stays `unknown`**: check the `MirisGetAssetStatusFunction` logs, the secret ARN, `mirisApiBaseUrl`, and the function's network path to `*.miris.com`.
