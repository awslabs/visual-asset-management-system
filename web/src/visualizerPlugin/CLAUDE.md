# CLAUDE.md - VAMS Viewer Plugin System

Auto-loaded when working within `web/src/visualizerPlugin/`. See `web/CLAUDE.md` for the frontend-wide steering.

---

## Architecture

The 3D/media viewer system uses a plugin-based architecture:

-   **PluginRegistry** (`core/PluginRegistry.ts`) — Singleton that manages all viewer plugins
-   **viewerConfig.json** (`config/viewerConfig.json`) — JSON configuration for all plugins
-   **manifest.ts** (`viewers/manifest.ts`) — Vite static-analysis paths for dynamic imports
-   **StylesheetManager** (`core/StylesheetManager.ts`) — Per-plugin CSS lifecycle management
-   **types.ts** (`core/types.ts`) — Shared `ViewerPluginProps` and config interfaces

Viewer plugins live under `viewers/{Name}ViewerPlugin/` — each plugin ID below maps to a directory of that form (e.g. `potree-viewer` → `viewers/PotreeViewerPlugin/`). Per-viewer custom-install scripts live in `web/customInstalls/` (one dir per viewer, plus a shared `utility/` helper dir) and are executed by the `postinstall` chain in `web/package.json`.

---

## Current Viewers

| ID                                 | Name                           | Category | Extensions                                                                                                                               | Status                                              |
| ---------------------------------- | ------------------------------ | -------- | ---------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| `online3d-viewer`                  | Online 3D Viewer               | 3d       | .3dm, .amf, .bim, .off, .wrl                                                                                                             | enabled                                             |
| `potree-viewer`                    | Potree Viewer                  | 3d       | .e57, .las, .laz, .ply                                                                                                                   | enabled                                             |
| `image-viewer`                     | Image Viewer                   | media    | .png, .jpg, .jpeg, .svg, .gif                                                                                                            | enabled                                             |
| `html-viewer`                      | HTML Viewer                    | document | .html                                                                                                                                    | enabled                                             |
| `video-viewer`                     | Video Player                   | media    | .mp4, .webm, .mov, .avi, .mkv, .flv, .wmv, .m4v                                                                                          | enabled                                             |
| `audio-viewer`                     | Audio Player                   | media    | .mp3, .wav, .ogg, .aac, .flac, .m4a                                                                                                      | enabled                                             |
| `columnar-viewer`                  | Columnar Data Viewer           | data     | .fcs, .csv                                                                                                                               | enabled                                             |
| `pdf-viewer`                       | PDF Viewer                     | document | .pdf                                                                                                                                     | enabled                                             |
| `cesium-viewer`                    | Cesium 3D Tileset              | 3d       | .json                                                                                                                                    | enabled                                             |
| `text-viewer`                      | Text Viewer                    | document | .txt, .json, .xml, .html, .yaml, .md, .py, .js, .ts, .sql, etc.                                                                          | enabled                                             |
| `text-diff-viewer`                 | Text Diff Viewer               | document | .txt, .json, .xml, .yaml, .md, .py, .js, .ts, .sql, etc. (same list as `text-viewer`)                                                    | enabled (compare mode: 2 files, cross-asset)        |
| `gaussian-splat-viewer-babylonjs`  | BabylonJS Gaussian Splat       | 3d       | .ply, .spz                                                                                                                               | enabled                                             |
| `supersplat-viewer`                | SuperSplat Editor (PlayCanvas) | 3d       | .lcc, .ply, .sog, .splat                                                                                                                 | enabled (requires ALLOWUNSAFEEVAL, iframe-embedded) |
| `gaussian-splat-viewer-playcanvas` | PlayCanvas Gaussian Splat      | 3d       | .ply, .sog                                                                                                                               | enabled                                             |
| `vntana-viewer`                    | VNTANA 3D Viewer               | 3d       | .glb                                                                                                                                     | **disabled** (licensed)                             |
| `veerum-viewer`                    | VEERUM 3D Viewer               | 3d       | .e57, .las, .laz, .ply, .json                                                                                                            | **disabled** (licensed)                             |
| `needletools-usd-viewer`           | Needle USD Viewer              | 3d       | .usd, .usda, .usdc, .usdz                                                                                                                | enabled (requires ALLOWUNSAFEEVAL)                  |
| `threejs-viewer`                   | Three.js Viewer                | 3d       | .gltf, .glb, .obj, .fbx, .stl, .ply, .dae, .3ds, .3mf, .stp, .step, .iges, .igs, .brep                                                   | enabled                                             |
| `physna-viewer`                    | Physna Viewer                  | 3d       | .3ds, .asm, .catpart, .catproduct, .glb, .iam, .iges, .igs, .ipt, .jt, .obj, .par, .prt, .sldasm, .sldprt, .stl, .step, .stp, .x_b, .x_t | enabled (requires PHYSNA_ADDON)                     |
| `thatopenwebifc-viewer`            | ThatOpen IFC BIM Viewer        | 3d       | .ifc, .ifczip                                                                                                                            | enabled (requires ALLOWUNSAFEEVAL)                  |
| `preview-viewer`                   | Preview Viewer                 | preview  | \* (wildcard)                                                                                                                            | enabled                                             |

> `supersplat-viewer` is an **iframe-embedded** viewer — it self-hosts a from-source SuperSplat build under `public/viewers/supersplat/` and loads files via a presigned URL `?load=` parameter. The build is WebGPU-only (no WebGL2 fallback). Under the production CSP its `<base>` element, inline script, and embedded `pc-icon` data-URI font are blocked without breaking the editor.

---

## Compare Mode

`PluginRegistry.getCompatibleViewers(exts, isMultiFile, isPreview, mode, compareContext)` takes a
`mode` of `"visualize"` (default, unchanged behavior) or `"compare"`. In compare mode the registry
surfaces **only** viewers that declare a `compareMode` block with `enabled: true`, whose
`[minFiles, maxFiles]` window admits the selected file count, and whose shape flags admit the
selection: N versions of one file requires `allowSameFileDifferentVersions`, N distinct files
requires `allowDifferentFiles`, and entries spanning assets require `allowCrossAsset`.

The classification lives in `core/compareShape.ts` (pure, unit-tested; the registry re-exports it):
`deriveCompareContext(files)` returns `{ fileCount, shape, crossAsset }`. **Identity is database +
asset + key, never the key alone** — `config.json` under assetA and `config.json` under assetB are two
different files (`shape: "different-files"`, `crossAsset: true`), not two versions of one file.

`DynamicViewer` renders compare mode when passed `mode="compare"`; it forwards the ordered files to
the viewer as `compareFiles` (index 0 = left/base) and sets `compareMode={true}`. Compare-capable
viewers read `compareFiles` instead of the single-file props.

### Cross-asset / multi-version contract

Each `compareFiles` entry is a fully resolved `{ databaseId, assetId, key, versionId? }`:

-   `DynamicViewer` fills a missing per-entry `databaseId`/`assetId` from its **top-level** props
    before classifying and before handing the list to the viewer. The visualize-path
    `effectiveAssetId` (which falls back to `files[0]`) is never used in compare mode — a second entry
    must not be re-homed under the first entry's asset.
-   A missing `versionId` means **latest**. Search-result selections arrive this way
    (`searchRowToFileInfo`); the version-list surfaces pin the left side to an S3 `versionId`.
-   A viewer fetches every entry under **its own** database/asset via `downloadAsset` — never a shared
    pair — and each asset is authorized independently (Casbin, per asset). A per-entry 401/403 (denied),
    410 (archived) or 404 must be rendered as **that entry's** state while the other entries still
    render; do not collapse the comparison into one error. `downloadAsset` returns
    `[false, message, status]` on failure so the status can be classified.
-   A viewer that opts into `allowCrossAsset` should offer a per-entry **version picker**
    (`fetchFileVersions` from `AssetVersionService`, one list per db+asset+key) and re-fetch only the
    entry whose version changed.

Compare mode is reached from three surfaces, all hosted by `FileViewerModal` (which has a
Visualize/Compare toggle): the search results multi-select "Compare Selected" action (rows may span
assets — each row carries its own db/asset), and the version-comparison "Compare" actions in
`AssetVersionComparison.tsx` and `FileVersionsList.tsx` (diffing versions of the same file). The first
compare viewer is `text-diff-viewer` (`TextDiffViewerPlugin`), which diffs two text files via
`react-diff-viewer-continued` (dynamically imported by its `dependencies.ts` so it stays out of the
base bundle), declares `allowCrossAsset`, keeps per-side state (content, error, version list), and
renders a readable side beside a denied/archived one.

### `compareMode` config fields

| Field                            | Type     | Description                                                                             |
| -------------------------------- | -------- | --------------------------------------------------------------------------------------- |
| `enabled`                        | boolean  | Offer this viewer in compare mode                                                       |
| `minFiles` / `maxFiles`          | number   | Inclusive file-count window                                                             |
| `allowSameFileDifferentVersions` | boolean  | Admit N versions of one file (same db + asset + key)                                    |
| `allowDifferentFiles`            | boolean  | Admit N distinct files                                                                  |
| `allowCrossAsset`                | boolean? | Admit entries spanning assets/databases; viewer must handle per-entry 403 (default: no) |

---

## Adding a New Viewer Plugin

**Step 1:** Create the viewer directory:

```
viewers/MyViewerPlugin/
  MyViewerComponent.tsx     # The React component
  dependencies.ts           # Optional: dependency loader
  MyViewer.module.css       # Optional: scoped styles
```

**Step 2:** Create the component implementing `ViewerPluginProps`:

```tsx
/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef } from "react";
import { ViewerPluginProps } from "../../core/types";

const MyViewerComponent: React.FC<ViewerPluginProps> = ({
    asset,
    files,
    databaseId,
    onFullscreen,
    viewerConfig,
}) => {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        // Initialize viewer
        return () => {
            // Cleanup on unmount
        };
    }, []);

    return <div ref={containerRef} style={{ width: "100%", height: "100%" }} />;
};

export default MyViewerComponent;
```

**Step 3:** Add to `viewers/manifest.ts`:

```typescript
export const VIEWER_COMPONENTS = {
    // ... existing entries
    "./viewers/MyViewerPlugin/MyViewerComponent": "./MyViewerPlugin/MyViewerComponent",
};
```

**Step 4:** Add to `config/viewerConfig.json`:

```json
{
    "id": "my-viewer",
    "name": "My Viewer",
    "description": "Description of the viewer",
    "componentPath": "./viewers/MyViewerPlugin/MyViewerComponent",
    "supportedExtensions": [".xyz"],
    "supportsMultiFile": false,
    "canFullscreen": true,
    "priority": 1,
    "dependencies": [],
    "loadStrategy": "lazy",
    "category": "3d",
    "enabled": true
}
```

**Step 5:** If the viewer has external dependencies, create a custom install script in `web/customInstalls/myviewer/` and add it to the `postinstall` chain in `web/package.json`.

---

## Plugin Config Fields

| Field                        | Type              | Description                                          |
| ---------------------------- | ----------------- | ---------------------------------------------------- |
| `id`                         | string            | Unique plugin identifier                             |
| `componentPath`              | string            | Path for manifest lookup                             |
| `dependencyManager`          | string?           | Path to dependency loader module                     |
| `dependencyManagerClass`     | string?           | Class name in dependency module                      |
| `dependencyManagerMethod`    | string?           | Load method name                                     |
| `dependencyCleanupMethod`    | string?           | Cleanup method name                                  |
| `supportedExtensions`        | string[]          | File extensions this viewer handles                  |
| `supportsMultiFile`          | boolean           | Can handle multiple files at once                    |
| `canFullscreen`              | boolean           | Supports fullscreen mode                             |
| `priority`                   | number            | Lower = preferred when multiple viewers match        |
| `loadStrategy`               | "lazy" \| "eager" | When to load the component                           |
| `category`                   | string            | Viewer category (3d, media, document, data, preview) |
| `featuresEnabledRestriction` | string[]?         | Required feature flags                               |
| `isPreviewViewer`            | boolean?          | True for the preview-only viewer                     |
| `enabled`                    | boolean           | Whether the plugin is active                         |
| `customParameters`           | object?           | Viewer-specific configuration                        |
| `compareMode`                | object?           | Compare-mode opt-in (see Compare Mode above)         |

---

## CSP / `unsafe-eval`

Some viewers require the `ALLOWUNSAFEEVAL` feature flag because their loaders (WASM or JIT) use `eval`. Three carry the gate in `viewerConfig.json` (`featuresEnabledRestriction: ["ALLOWUNSAFEEVAL"]`), so the registry does not offer them at all when the deployment has not enabled `allowUnsafeEvalFeatures`:

-   Needle USD Viewer
-   SuperSplat Editor (also iframe-embedded)
-   ThatOpen IFC BIM Viewer (web-ifc)

The **Three.js viewer is deliberately not gated**: its mesh formats (.glb, .obj, .stl, …) need no `eval`, and gating the whole plugin would remove them too. Only its OCCT CAD path (.stp/.step/.iges/.igs/.brep) has the heavier requirements, and `loadFile()` in `ThreeJSViewerPlugin/utils/fileLoaders.ts` reports them as a message on the file rather than hiding the viewer — it checks for `SharedArrayBuffer` (the COI headers) and for the OCCT bundle before loading.

`cesium-viewer` carried the gate until this release. The viewer now builds a `CesiumWidget` from the widget-less `@cesium/engine` (`CesiumViewerComponent.tsx`), which drops the `@cesium/widgets` Knockout layer whose `new Function` binding compiler was what required `unsafe-eval`; `'wasm-unsafe-eval'` in the base CSP covers what remains. See `web/customInstalls/cesium/README.md` for the build. KTX2/Basis textures and `.spz` splats are the known content types that still need the broader directive.

When adding a viewer that needs `eval`, add the feature-flag gate and update the deployment configuration reference in `documentation/docusaurus-site/docs/deployment/configuration-reference.md`.

---

## A Framed Viewer Must Not Receive a Signed URL in the Query String

A presigned Amazon S3 URL is a bearer credential: anyone holding it can read the object until it
expires. Putting one in an iframe's **query string** writes it into CloudFront or ALB access logs, which
are not treated as containing credentials and are retained for the log group's retention period.

`supersplat-viewer` currently does this (`SuperSplatViewerComponent.tsx` builds
`?load=<presigned>&filename=…`) and ships as a documented known issue for 2.6.0 — one object, expiring,
but logged. **Do not copy the pattern into a new viewer.** Use the **URL fragment** instead: browsers do
not transmit a fragment, so nothing reaches the server or its logs.

For a vendored build that reads `location.search` and cannot be changed at the call site, inject a shim
into its `index.html` from that viewer's `customInstalls/` script which moves the fragment into the query
string with `history.replaceState` before the bundle runs — `replaceState` issues no request, so the
value stays client-side. Patch the HTML entry rather than the minified bundle: the bundle is regenerated
from a pinned upstream tag on every `npm install`, so a regex against its internals breaks on the next
version bump while an injected `<script>` does not.

Note the encoding interaction if you move an existing viewer: SuperSplat decodes `load` **twice**, so its
value is deliberately double-encoded (see the comment in `SuperSplatViewerComponent.tsx`). Moving the
same string to a fragment must preserve that double encoding byte-for-byte, or the presigned signature
breaks and S3 returns 400.
