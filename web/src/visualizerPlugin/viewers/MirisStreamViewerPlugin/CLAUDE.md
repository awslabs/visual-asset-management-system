# CLAUDE.md -- Miris Spatial Streaming viewer

> Auto-loaded when Claude Code works within `web/src/visualizerPlugin/viewers/MirisStreamViewerPlugin/`. Every rule in `web/CLAUDE.md` and `web/src/visualizerPlugin/CLAUDE.md` applies here as well; this file adds what is specific to the `miris-stream-viewer` add-on viewer. The backend side is `backend/backend/handlers/addon/miris/CLAUDE.md`, the CDK side `infra/lib/nestedStacks/addon/miris/CLAUDE.md`. The Kiro mirror is `.kiro/steering/ADDON_MIRIS_SPATIAL_STREAMING.md`.

---

## Registration and install

The entry is in `config/addonViewerConfig.json` + `viewers/addonManifest.ts` (`.usd`, `.usda`, `.usdc`, `.usdz`; `featuresEnabledRestriction` `MIRIS_ADDON` and `ALLOWUNSAFEEVAL`) and ships enabled. `web/customInstalls/miris/mirisInstall.js` (in `postinstall`) bundles the SDK only when the deployment configuration (`VAMS_CONFIG_PATH`, resolved as CDK resolves `-c configFile`, else `infra/config/config.json`) enables `app.addons.useMirisStreaming` (`customInstalls/miris/mirisAddonEnabled.js`, tested by `mirisInstallGate.test.ts`) and the entry is enabled (`customInstalls/utility/checkViewerEnabled.js`).

The SDK (`@miris-inc/components`, `@miris-inc/core`, `@miris-inc/three`) and its `three` are not web dependencies: the install bundles them with webpack into an ES module and copies it with the SDK's `.wasm` files to `public/viewers/miris/`, where `dependencies.ts` imports it at runtime. The exact pins live in `customInstalls/miris/package.json`; upgrade the `@miris-inc/*` packages together with `three`, which stays inside the SDK's peer range (`>=0.182.0 <0.186.0`).

## Behavior

The viewer streams the Miris asset recorded in a USD file's `miris_*` metadata. It reads the file's state from `GET /addon/miris/asset-status` and, only for a streamable file on a browser that grants a WebGPU adapter (the SDK renders only with WebGPU; `MirisStreamDependencyManager.isDeviceSupported()` runs before the key request), gets a viewer key from `POST /addon/miris/viewer-key` (held in memory for that open) and imports the SDK bundle from `/viewers/miris/miris-sdk.js`, bounded at 60 s. The entry has no `dependencyManagerMethod`, so the registry never loads the SDK up front, and `autoSelect` opens it ahead of the Needle USD viewer.

For a file with no recorded stream it shows "not yet streamed" guidance (the file manager's **Automation** action and the **Miris Spatial Streaming Upload** workflow) and a **Stream with Miris** button, gated on the `MIRIS_UPLOAD` feature switch, `can("POST", "/workflows/{workflowDatabaseId}/{workflowId}/execute")` and the USD extensions. `startMirisUpload` (`services/addons/mirisAPIService.ts`) starts a manual run of the GLOBAL `miris-upload` workflow on the status response's `filePath` with no template. When the user may also list the asset's workflow executions, `findRunningMirisUpload` first looks for an unfinished `miris-upload` run and shows an in-progress notice with a **View execution** link in place of the button; a failed lookup offers the button.

The viewer follows a found or started run until it ends: every 30 s through its execution details (`getMirisUploadRun`) or else the execution list, and every 90 s through the stream status alone; a source that fails twice in a row gives way to the next. The execution list is read for the followed run itself (`findRunningMirisUpload` with its `executionId`), so it reports that run's status and `executionError` as the details do. Success re-reads the status, failure shows the recorded `executionError` and offers the button again, and a `SKIPPED` run gets a neutral notice. A run the source still cannot see after 90 minutes is reported as unknown, and the button is offered again.

Only the camera carries `controls`: with `controls` on both the camera and the stream, one drag rotates twice. The canvas is transparent, so the viewer supplies its own backdrop.

Tests: `MirisStreamViewerComponent.test.tsx`, `dependencies.test.ts`, `streamStatus.test.ts`, `services/addons/mirisAPIService.test.ts`.
