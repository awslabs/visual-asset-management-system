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
| `columnar-viewer`                  | Columnar Data Viewer           | data     | .rds, .fcs, .csv                                                                                                                         | enabled                                             |
| `pdf-viewer`                       | PDF Viewer                     | document | .pdf                                                                                                                                     | enabled                                             |
| `cesium-viewer`                    | Cesium 3D Tileset              | 3d       | .json                                                                                                                                    | enabled                                             |
| `text-viewer`                      | Text Viewer                    | document | .txt, .json, .xml, .yaml, .md, .py, .js, .ts, .parquet (plaintext only), etc.                                                            | enabled                                             |
| `gaussian-splat-viewer-babylonjs`  | BabylonJS Gaussian Splat       | 3d       | .ply, .spz                                                                                                                               | enabled                                             |
| `supersplat-viewer`                | SuperSplat Editor (PlayCanvas) | 3d       | .lcc, .ply, .sog, .splat                                                                                                                 | enabled (requires ALLOWUNSAFEEVAL, iframe-embedded) |
| `gaussian-splat-viewer-playcanvas` | PlayCanvas Gaussian Splat      | 3d       | .ply, .sog                                                                                                                               | enabled                                             |
| `vntana-viewer`                    | VNTANA 3D Viewer               | 3d       | .glb                                                                                                                                     | **disabled** (licensed)                             |
| `veerum-viewer`                    | VEERUM 3D Viewer               | 3d       | .e57, .las, .laz, .ply, .json                                                                                                            | **disabled** (licensed)                             |
| `needletools-usd-viewer`           | Needle USD Viewer              | 3d       | .usd, .usda, .usdc, .usdz                                                                                                                | enabled                                             |
| `threejs-viewer`                   | Three.js Viewer                | 3d       | .gltf, .glb, .obj, .fbx, .stl, .ply, .dae, .3ds, .3mf, .stp, .step, .iges, .brep                                                         | enabled                                             |
| `physna-viewer`                    | Physna Viewer                  | 3d       | .3ds, .asm, .catpart, .catproduct, .glb, .iam, .iges, .igs, .ipt, .jt, .obj, .par, .prt, .sldasm, .sldprt, .stl, .step, .stp, .x_b, .x_t | enabled (requires PHYSNA_ADDON)                     |
| `thatopenwebifc-viewer`            | ThatOpen IFC BIM Viewer        | 3d       | .ifc, .ifczip                                                                                                                            | enabled (requires ALLOWUNSAFEEVAL)                  |
| `preview-viewer`                   | Preview Viewer                 | preview  | \* (wildcard)                                                                                                                            | enabled                                             |

> `supersplat-viewer` is an **iframe-embedded** viewer — it self-hosts a from-source SuperSplat build under `public/viewers/supersplat/` and loads files via a presigned URL `?load=` parameter.

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

---

## CSP / `unsafe-eval`

Some viewers require the `ALLOWUNSAFEEVAL` feature flag because their loaders (WASM or JIT) use `eval`:

-   Needle USD Viewer
-   SuperSplat Editor (also iframe-embedded)
-   Three.js CAD-format loaders
-   ThatOpen IFC BIM Viewer (web-ifc)

These viewers are gated at runtime via `featuresEnabledRestriction` in `viewerConfig.json` and by the CDK CSP configuration (`allowUnsafeEvalFeatures` in the config). When adding a viewer that needs `eval`, add the feature-flag gate and update the deployment configuration reference in `documentation/docusaurus-site/docs/deployment/configuration-reference.md`.
