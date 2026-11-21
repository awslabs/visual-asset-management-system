# Miris Spatial Streaming

[Miris](https://miris.com/) is a partner ISV whose Spatial Streaming service delivers 3D geometry progressively: an asset loads at a lower fidelity first and sharpens to full quality within seconds. VAMS integrates with Miris through an optional [add-on](addons-overview.md) that streams USD files from Miris inside the VAMS file viewer, without downloading the full geometry to the browser.

-   **Streaming** (`app.addons.useMirisStreaming`) deploys the Miris viewer plugin for USD files, the two API routes it calls, the GLOBAL **Miris stream** file metadata schema, and the Content Security Policy sources the Miris web SDK needs. It holds the Miris Integration Key and API base URL that both parts use.
-   **The [Miris Upload pipeline](../pipelines/miris-upload.md)** (`app.addons.useMirisStreaming.upload`) uploads a VAMS asset's root USD file to Miris and records the Miris asset as [stream metadata](#stream-metadata) on that file. It publishes the `MIRIS_UPLOAD` feature switch.

A USD asset is uploaded to Miris, automatically on file upload or on demand; Miris prepares it for streaming; the Miris asset UUID is written to the metadata of the asset's root USD file; and the viewer streams the asset when a user opens that file. The stream metadata can also be set by hand to point a USD file at an asset that is already on Miris.

:::info
The add-on is off by default. It deploys only to the commercial `aws` partition and requires `app.webUi.allowUnsafeEvalFeatures` (see [Requirements and limitations](#requirements-and-limitations)).
:::

---

## How the add-on works

The viewer asks VAMS at render time whether a USD file has a stream. Both API routes read the `miris_*` keys of the requested file server side, so the browser never supplies a Miris asset UUID and a caller can reach only a stream that the file itself references.

1. **Stream status.** The viewer calls `GET /addon/miris/asset-status` with the file's path. VAMS authorizes the caller, reads the file's stream metadata, reads the Miris asset with the Integration Key to check its [binding tags](#binding-tags), and reports the stream's state and whether the stream is stale (see [Stale streams](#stale-streams)).
2. **Viewer key.** When the state allows streaming, the viewer calls `POST /addon/miris/viewer-key`. VAMS repeats the authorization, applies the [asset distribution rule](#which-assets-can-be-streamed-and-uploaded) and the binding check, and returns a viewer key (see [Viewer key modes](#viewer-key-modes)), which the viewer holds in memory for that open only.
3. **Streaming.** The viewer loads the Miris web SDK and streams the asset directly from Miris. VAMS never proxies or stores the streamed geometry.

Both routes enforce Tier 1 on the route and Tier 2 `GET` on the asset before any metadata is read; the viewer key route checks a read even though it is a `POST`. See the [Add-on API reference](../api/addon.md#miris-spatial-streaming-add-on) for the request and response shapes.

### Binding tags

A Miris asset is bound to a VAMS asset by two tags on the Miris asset: `vams-asset-{assetId}` and `vams-database-{databaseId}`. Both are required, because an asset ID is unique only within its database. In both viewer key modes the routes accept a Miris asset only when it carries both tags of the VAMS asset being viewed.

A Miris asset that lacks either tag, that the Integration Key cannot find, or that Miris reports `deleted` is `notBound`: the viewer key route returns `403` with `state: notBound` and no key, and the viewer shows an error that names both tags. The upload pipeline always adds both tags. For a Miris asset linked by hand, add `vams-asset-<assetId>` and `vams-database-<databaseId>` in the Miris Portal or with `miris asset update --add-tags`.

### Which assets can be streamed and uploaded

The add-on applies the rule the core download and stream routes apply before they serve an asset's files (`common/downloadEligibility.py` and `common/compliance/quarantineGuard.py`). An asset can be streamed and uploaded to Miris when it is distributable and, while `app.compliance.quarantineBlocksDownload` is `true`, it is not quarantined without a granted exception. The rule is checked after both authorization tiers, so a caller who may not read the asset receives the authorization error and learns nothing about the asset's state.

-   **Viewer key route.** Returns `400` with the reason, "Asset not distributable" or "Asset is quarantined and cannot be downloaded", and no key. The viewer shows the reason.
-   **Upload pipeline.** The run fails at `vamsExecute`, before anything is claimed or sent to Miris, with the reason as the workflow's failure cause. The viewer and the execution details show it.

The status route still reports the stream recorded on the file, and returns no key.

---

## Viewer plugin

| Plugin ID             | Extensions                        | Priority | Feature flags                       |
| --------------------- | --------------------------------- | -------- | ----------------------------------- |
| `miris-stream-viewer` | `.usd`, `.usda`, `.usdc`, `.usdz` | 0        | `MIRIS_ADDON` and `ALLOWUNSAFEEVAL` |

The entry in `web/src/visualizerPlugin/config/addonViewerConfig.json` ships with `"enabled": false` (see [Configuration](#configuration)) and sets `autoSelect: true`, so the viewer opens USD files automatically in visualize mode while the Needle USD Viewer and the Three.js Viewer stay in the viewer selector.

| Status `state`                    | What the viewer shows                                                                                                                                                                                                                                                                             |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `notStreamed`                     | A "Not yet streamed with Miris" note that points to the file manager's **Automation** action and the **Miris Spatial Streaming Upload** workflow, and a **Stream with Miris** button (see [Starting an upload from the viewer](#starting-an-upload-from-the-viewer)).                             |
| `processing`, `preview`, or other | A "Miris is preparing this asset" overlay with the current state and the note "This can take a few hours." The viewer checks the status again every 90 seconds and loads the stream once the state leaves this group.                                                                             |
| `streamable` or `unknown`         | Requests a viewer key and streams the asset, labeled with `miris_display_name`. `unknown` means the state could not be established; the viewer still attempts the stream. In `static` mode, a deployment viewer key whose Miris tag scope does not cover the asset is also reported as `unknown`. |
| `notBound`                        | An error that names both [binding tags](#binding-tags).                                                                                                                                                                                                                                           |
| `invalidUuid`                     | An error stating that `miris_asset_uuid` is not a valid UUID.                                                                                                                                                                                                                                     |
| `error` or `failed`               | An error stating that Miris processing failed.                                                                                                                                                                                                                                                    |

Each error offers a **Retry** button. A failed viewer key request shows a `400` refusal with the API's reason (see [Which assets can be streamed and uploaded](#which-assets-can-be-streamed-and-uploaded)), `403` as a permission error, `503` as "Miris streaming is not configured for this deployment", `404` as the "Not yet streamed with Miris" note, a request the AWS WAF web ACL blocked with the blocking message, and anything else as an access error.

When the status reports `stale: true`, the viewer streams the recorded asset under a banner that says the asset has changed since it was streamed: the file, or a sublayer or texture it uses, differs from the one in the stream (see [Stale streams](#stale-streams)). When `stale` is null, the viewer compares the recorded root version (`miris_source_version_id`) with the version being viewed instead. The routes read the file's current metadata, so a historical asset version streams the Miris asset recorded now.

### Starting an upload from the viewer

**Stream with Miris** runs the GLOBAL `miris-upload` workflow on the viewed file as a manual run with the pipeline's default template, so it always uploads (see [Duplicate uploads and re-runs](../pipelines/miris-upload.md#duplicate-uploads-and-re-runs)). The button appears only when the deployment publishes `MIRIS_UPLOAD`, the file has a USD extension, and the user's role may call `POST /workflows/{workflowDatabaseId}/{workflowId}/execute`. When the role may also list the asset's workflow executions, an unfinished `miris-upload` run replaces the button with the note "A Miris upload for this asset is running." and a **View execution** link. After the button starts a run, the viewer shows the same note and follows that run until it ends, reading the execution's status every 30 seconds. When the run succeeds, the viewer reads the stream status again and streams the asset or shows the preparing overlay. When it fails, the viewer shows the failure reason the workflow recorded and offers the button again; when the workflow skipped it because another upload was running, the viewer says so. A role that may not read the execution's details is followed through the asset's execution list, which reports the run's failure reason or skip in the same way, or else through the stream status every 90 seconds, and a source whose read fails twice in a row gives way to the next. When the source still cannot see the run after 90 minutes (the list does not show it, or the file records no stream), the viewer reports the result as unknown and offers the button again.

### Web SDK

The viewer renders with the Miris web SDK (`@miris-inc/components`, `@miris-inc/three`, `@miris-inc/core`, and `three`), which is not a dependency of the web application. The custom install script `web/customInstalls/miris/mirisInstall.js`, run by the web application's `postinstall`, acts only when the `miris-stream-viewer` entry is enabled: it bundles the SDK into `web/public/viewers/miris/`, and the viewer imports `/viewers/miris/miris-sdk.js` from the same origin. The SDK packages and `three` are pinned in `web/customInstalls/miris/package.json` and are upgraded together.

The SDK renders only with WebGPU. Before it requests a viewer key, the viewer asks the browser for a WebGPU adapter, as the SDK does; a browser that grants none shows "Miris streaming needs WebGPU" and no key is requested. The viewer loads the SDK only after it has a viewer key. It waits 60 seconds for the SDK to load and 60 seconds for the first frame, counting the first-frame wait only while the browser tab is visible.

With the add-on enabled, the Content Security Policy gains `https://*.miris.com` and `wss://*.miris.com` in `connect-src` and `blob:` in `script-src`. The SDK also needs `'unsafe-eval'`, which only `app.webUi.allowUnsafeEvalFeatures` adds. See [Security Architecture](../architecture/security.md#conditional-csp-sources).

---

## Viewer key modes

`app.addons.useMirisStreaming.viewerKeyMode` selects how the viewer key route delivers keys. Both modes use the Integration Key for the binding check.

| Mode                | What `POST /addon/miris/viewer-key` returns                                                                                | `expiresAt` |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------- | ----------- |
| `perView` (default) | A key minted for the file's one Miris asset, which expires `viewerKeyTtlMinutes` (1 to 60, default 15) after it is minted. | The expiry  |
| `static`            | The deployment-wide `viewerKey`. No key is created and nothing is stored.                                                  | `null`      |

### Static mode

The deployment's viewer key is set in `config.json` (`viewerKey`). VAMS stores it in an AWS Secrets Manager secret that it creates empty and fills at deployment time, and the status and viewer key Lambda functions read it from there, so it is in neither the CloudFormation template nor a Lambda environment (see [Third-Party Credentials Supplied in Configuration](../architecture/security.md#third-party-credentials-supplied-in-configuration)). It is not part of `/api/secure-config`. To change the key, set the new value and deploy. A user receives it only from the viewer key route, after both authorization tiers and the binding check pass. Create the key with an expiry (`miris viewerkey create -e`) and rotate it before it expires (`miris viewerkey rotate`): a key without an expiry stays valid until it is revoked.

:::warning[The static key is deployment-wide]
Anyone who can read one bound asset receives the deployment's viewer key, and that key streams every Miris asset it allows. Scope the static key in Miris with tags and an expiry, or use `perView` mode where stream access must follow VAMS permissions asset by asset.
:::

### Per-view mode

No shared viewer key is deployed. Each viewer open mints a key and records its Miris key ID in the add-on's viewer key table, with a `ttl` one hour after the key's expiry. When Amazon DynamoDB TTL removes the row, the cleanup Lambda function deletes the key from the Miris workspace; TTL deletion is lazy, so an expired key can remain there for a while. A cleanup batch that still fails after its retries goes to a dead-letter queue. When a minted key cannot be recorded, VAMS deletes it from Miris and the request fails with `500`.

Expiry and revocation apply when a stream starts; an open stream is not ended. In both modes the key is visible in the browser, so a short lifetime limits the exposure of a copied key.

Each key delivery is written to the `AUTHOTHER` audit log, without the key material: `mirisStaticViewerKeyIssue` in `static` mode and `mirisViewerKeyMint` in `perView` mode, both with `userId`, `databaseId`, `assetId`, `filePath`, and `mirisAssetUuid`, and the mint record also with `mirisViewerKeyId` and `expiresAt`. A successful status request records `mirisAssetStatusGet` in the actions audit log. See [Audit Logging](audit-logging.md).

### Switching modes

-   **To `perView`**: set `viewerKeyMode` to `"perView"`, remove `viewerKey`, and deploy. Then revoke the old static viewer key in the Miris Portal or with `miris viewerkey revoke`, because a browser that received it can stream with it until it expires.
-   **Back to `static`**: set `viewerKeyMode` to `"static"` and `viewerKey`, and deploy. The cleanup function and its queue are removed; the viewer key table is retained. Keys minted before the switch expire within `viewerKeyTtlMinutes` and stay in the Miris workspace until deleted there.

---

## Stream metadata

The Miris stream for a USD file is described by five keys in that file's metadata:

| Key                         | Type     | Meaning                                                                                                                                                                            |
| --------------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `miris_asset_uuid`          | `string` | Miris asset to stream. The only key the viewer requires; a missing or empty value means the file is not streamed.                                                                  |
| `miris_source_version_id`   | `string` | Amazon S3 version ID of the USD file the stream was made from. Empty when the bucket is unversioned. Drives the different-version banner.                                          |
| `miris_uploaded_at`         | `date`   | When the Miris upload completed (ISO 8601 UTC, for example `2026-10-14T09:30:00Z`).                                                                                                |
| `miris_display_name`        | `string` | Label shown over the stream in the viewer. The upload pipeline sets it to the name of the Miris asset.                                                                             |
| `miris_dependency_manifest` | `string` | SHA-256 ID of the [dependency manifest](../pipelines/miris-upload.md#what-the-pipeline-writes) of the files the stream was built from. Optional; hand-edited metadata may omit it. |

The keys are indexed for search like any other metadata, so with Amazon OpenSearch Service, streamed files can be found by searching on `MD_.miris_asset_uuid`.

When the add-on is enabled, deployment seeds a GLOBAL `fileMetadata` schema named **Miris stream** (ID `miris-stream-file-schema`) that defines the five keys as optional fields for `.usd`, `.usda`, `.usdc`, and `.usdz` files. Later deployments refresh it only while no user has edited it, and disabling the add-on or deleting the stack does not remove it. In a database that restricts metadata to schema-defined fields (see [Schema enforcement](../concepts/metadata-and-schemas.md#schema-enforcement)), it is an applicable schema for USD files, so users cannot add keys that no schema defines to USD files there.

### Stale streams

Miris bakes every file of the dependency tree into the stream: the root layer, its sublayers and references, and the textures and other assets they use. The status route reports `stale: true` when any file listed in the file's [dependency manifest](../pipelines/miris-upload.md#what-the-pipeline-writes) has been removed from the asset or changed since the upload, so an edited texture or lookdev layer marks the stream stale even though the root file is unchanged. It compares the manifest's ETags with one listing of the asset, and reports `false` when every file matches. `stale` is null when the file records no `miris_dependency_manifest` (hand-edited metadata), or when the manifest or the listing cannot be read in time; only `miris_source_version_id` then says whether the stream matches the root file. A stale stream keeps streaming; run the **Miris Spatial Streaming Upload** workflow to upload the current files.

### Editing the stream metadata

Users with `PUT` permission on the asset can edit the keys through the **Miris stream** fields in the file metadata editor, the API, or the CLI. To stream a USD file from an asset already on Miris, set `miris_asset_uuid` to that asset's UUID and add the [binding tags](#binding-tags) to the Miris asset; leaving `miris_source_version_id` empty streams without the different-version banner. Clearing `miris_asset_uuid` returns the file to "Not yet streamed".

Moving or renaming the file carries the keys to the new path, and deleting the file deletes them; neither changes the asset on Miris. Running the upload workflow again overwrites the keys; see [What the pipeline writes](../pipelines/miris-upload.md#what-the-pipeline-writes).

---

## Configuration

Enable the add-on by setting `app.addons.useMirisStreaming.enabled` to `true`, with `app.webUi.allowUnsafeEvalFeatures` set to `true`, and the ARN of the secret that holds the Miris Integration Key. The default `perView` mode needs no viewer key; `static` mode also needs `viewerKey`. The [Configuration Reference](../deployment/configuration-reference.md#miris-spatial-streaming-appaddonsusemirisstreaming) lists every field, default, and validation rule.

```json
{
    "app": {
        "webUi": {
            "allowUnsafeEvalFeatures": true
        },
        "addons": {
            "useMirisStreaming": {
                "enabled": true,
                "apiKeySecretArn": "arn:aws:secretsmanager:us-east-1:111122223333:secret:miris-integration-key-AbCdEf"
            }
        }
    }
}
```

Enabling the add-on sets the `MIRIS_ADDON` feature switch; enabling `upload` also sets `MIRIS_UPLOAD`. The viewer plugin ships disabled in the web application, so that a deployment without the add-on neither installs nor bundles the Miris web SDK. To enable it:

1. Set `"enabled": true` on the `miris-stream-viewer` entry in `web/src/visualizerPlugin/config/addonViewerConfig.json`.
2. Run `npm install` (or `npm ci`) in `web/`. Its `postinstall` bundles the [web SDK](#web-sdk). To rebuild only the SDK bundle, run `node customInstalls/miris/mirisInstall.js` from `web/`.
3. Build the web application (`npm run build` in `web/`) and deploy it with the add-on configuration above.

The viewer is offered only when the entry is enabled and both `MIRIS_ADDON` and `ALLOWUNSAFEEVAL` are present.

### Integration Key

The Integration Key authenticates the add-on's Lambda functions and the upload pipeline to the Miris API. It carries the permissions of the person who created it and resolves to that person's default Miris workspace, which must be the workspace that holds the streamed assets.

-   Create the key for a Miris service account that is a member of only the workspace VAMS uses.
-   Store it in AWS Secrets Manager as a plain-string secret in the deployment's account. VAMS grants `secretsmanager:GetSecretValue` on exactly that ARN and no AWS KMS permission, so a secret encrypted with a customer managed key other than the VAMS key needs a key policy that lets the reading roles call `kms:Decrypt`.
-   The functions cache the key for up to an hour per execution environment, and read it again when Miris answers `401` or `403`. A refusal after that read is logged as `miris_auth_failed` and counted in the `MirisAuthFailures` metric (see [Monitoring](#monitoring)).
-   A Miris integration key belongs to the person who created it; Miris has no key owned by an organization. When that person's account is removed or moves to another default workspace, status checks, viewer keys and uploads fail until a new key is stored.

The functions never follow a redirect from Miris, so a `mirisApiBaseUrl` that redirects makes every call fail. Miris calls are bounded to fit within the API Gateway integration timeout; when time runs out, the status route reports `unknown` and the viewer key route answers `504`.

### Monitoring

The add-on creates two Amazon CloudWatch alarms, named `<configuration name>-<app.baseStackName>-<region>-…`, with no alarm action. Subscribe an Amazon SNS topic or another action to them.

| Alarm                        | Goes into alarm when                                                                                                                                                                                                                                                                                                |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `…-mirisAuthFailures`        | Miris refuses the Integration Key after a re-read, in any 5-minute period (metric `MirisAuthFailures` in namespace `VAMS/<configuration name>-<app.baseStackName>-<region>/Miris`).                                                                                                                                 |
| `…-mirisViewerKeyCleanupDLQ` | `perView` mode only: a cleanup batch is in the dead-letter queue. The queue holds only the stream's shard and sequence range; the key IDs are in the cleanup function's log ("Could not delete Miris viewer key"). The keys have already expired, so deleting them in Miris after fixing the cause is housekeeping. |

An upload run the Integration Key fails is reported on the run itself, as a failed execution.

### Permissions

The status route needs Tier 1 `GET` on `/addon/miris/asset-status` and the viewer key route Tier 1 `POST` on `/addon/miris/viewer-key`. Both need Tier 2 `GET` on the asset. The Database Admin, Database User, Database Read-Only, and Global Read-Only permission templates grant both routes, and the seeded `basicReadOnly` role gains them when the add-on is enabled. A custom role, or one imported from an earlier copy of a template, needs constraints that allow both routes. See [Permissions Model](../concepts/permissions-model.md#add-on-routes).

### AWS resources

The add-on deploys `MirisBuilderNestedStack` (`infra/lib/nestedStacks/addon/miris/mirisBuilder-nestedStack.ts`) inside the add-on builder stack. It always creates the `MirisGetAssetStatusFunction` and `MirisMintViewerKeyFunction` Lambda functions and the custom resource that writes the **Miris stream** schema. In `perView` mode it also creates the `MirisViewerKeyStorageTable` Amazon DynamoDB table (retained), its `addon/dynamoTables/mirisViewerKeyStorage` SSM parameter, the `MirisViewerKeyCleanupFunction` Lambda function, and the `MirisViewerKeyCleanupDLQ` Amazon SQS queue. See [AWS Resources](../architecture/aws-resources.md) for removal policies and naming.

---

## Requirements and limitations

-   **Commercial partition only.** Configuration validation rejects the add-on in any partition other than `aws`. Every browser that streams, and the add-on's Lambda functions, must reach `*.miris.com`.
-   **`app.webUi.allowUnsafeEvalFeatures` must be `true`.** The Miris web SDK runs in the VAMS web application's own origin and compiles code at runtime. The setting adds `'unsafe-eval'` to `script-src` for the whole web application, not only the viewer. Review this with your security team (see [Security](../architecture/security.md)).
-   **Not with `app.useGlobalVpc.useForAllLambdas`.** Configuration validation rejects the add-on with that setting, which would place the add-on's Lambda functions in the VPC's isolated subnets, where they have no route to the Miris API. See [Network Architecture](../architecture/networking.md).
-   **Browser support.** A browser with WebGPU, for example Chrome or Edge 113 or later, or Safari 26 or later, over HTTPS, with WebSocket support.
-   **Processing time.** After upload, an asset can take a few hours to become streamable.

---

## Troubleshooting

-   **"Not yet streamed with Miris"**: the file has no `miris_asset_uuid`. Choose **Stream with Miris**, run the **Miris Spatial Streaming Upload** workflow on the file, or set the stream metadata by hand.
-   **"The Miris asset recorded for this file is not linked to this VAMS asset"**: add the [binding tags](#binding-tags) to the Miris asset, or run the upload workflow again. The error also appears when the Miris asset was deleted or is not in the Integration Key's workspace.
-   **"Miris streaming is not configured for this deployment"**: a setting the viewer key mode needs is missing from the function environment. Check the add-on configuration and redeploy.
-   **"The Miris viewer failed to load"**: the SDK did not load within 60 seconds. Check the browser's network panel and console. A `404` or HTML response for `/viewers/miris/miris-sdk.js` means the web application was built without the SDK bundle: confirm the `miris-stream-viewer` entry is enabled and that `postinstall` reports "Miris: Installation complete!", then rebuild and redeploy the web application.
-   **"Timed out waiting for Miris to start streaming"**: no first frame arrived within 60 seconds. Check for an invalid or expired viewer key, a key from another Miris workspace, a `static` viewer key whose tag scope does not cover the asset, or a blocked `*.miris.com` connection.
-   **The "preparing" overlay never clears**: the asset stays at `preview`. Promote it in the Miris Portal.
-   **The status stays `unknown`**: check the `MirisGetAssetStatusFunction` logs, the secret ARN, `mirisApiBaseUrl`, the static viewer key's tag scope, and the function's network path to `*.miris.com`.
