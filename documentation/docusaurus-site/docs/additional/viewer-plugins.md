# Viewer Plugins

VAMS includes a plugin-based viewer architecture with 17 built-in viewer plugins for visualizing 3D models, point clouds, media files, documents, and data. This page provides a complete reference of all viewer plugins, their supported file extensions, and configuration options.

---

## Viewer Plugin Reference

### Complete Viewer Table

| ID | Name | Category | Supported Extensions | Enabled | Priority | Notes |
|---|---|---|---|---|---|---|
| `online3d-viewer` | Online 3D Viewer | 3d | `.3dm`, `.amf`, `.bim`, `.off`, `.wrl` | Yes | 2 | Multi-file support |
| `potree-viewer` | Potree Viewer | 3d | `.e57`, `.las`, `.laz`, `.ply` | Yes | 1 | Requires Potree preprocessing pipeline; shows latest version only |
| `image-viewer` | Image Viewer | media | `.png`, `.jpg`, `.jpeg`, `.svg`, `.gif` | Yes | 1 | Eager load strategy |
| `html-viewer` | HTML Viewer | document | `.html` | Yes | 1 | |
| `video-viewer` | Video Player | media | `.mp4`, `.webm`, `.mov`, `.avi`, `.mkv`, `.flv`, `.wmv`, `.m4v` | Yes | 1 | |
| `audio-viewer` | Audio Player | media | `.mp3`, `.wav`, `.ogg`, `.aac`, `.flac`, `.m4a` | Yes | 1 | |
| `columnar-viewer` | Columnar Data Viewer | data | `.rds`, `.fcs`, `.csv` | Yes | 2 | |
| `pdf-viewer` | PDF Viewer | document | `.pdf` | Yes | 1 | |
| `cesium-viewer` | Cesium 3D Tileset Viewer | 3d | `.json` | Yes | 2 | Requires `ALLOWUNSAFEEVAL` feature flag; shows latest version only |
| `text-viewer` | Text Viewer | document | `.txt`, `.json`, `.xml`, `.html`, `.htm`, `.yaml`, `.yml`, `.toml`, `.ini`, `.ipynb`, `.inf`, `.cfg`, `.md`, `.sh`, `.csv`, `.py`, `.log`, `.js`, `.ts`, `.sql`, `.ps1` | Yes | 1 | Syntax highlighting |
| `gaussian-splat-viewer-babylonjs` | Gaussian Splat Viewer (BabylonJS) | 3d | `.ply`, `.spz` | Yes | 1 | XR support |
| `gaussian-splat-viewer-playcanvas` | Gaussian Splat Viewer (PlayCanvas) | 3d | `.ply`, `.sog` | Yes | 2 | XR support, orbit camera |
| `vntana-viewer` | VNTANA 3D Viewer | 3d | `.glb` | Yes | 2 | Licensed viewer |
| `veerum-viewer` | VEERUM 3D Viewer | 3d | `.e57`, `.las`, `.laz`, `.ply`, `.json` | Yes | 2 | Licensed viewer; multi-file; requires Potree pipeline; shows latest version only |
| `needletools-usd-viewer` | Needle USD Viewer | 3d | `.usd`, `.usda`, `.usdc`, `.usdz` | Yes | 1 | Requires `ALLOWUNSAFEEVAL` feature flag; WASM-based |
| `threejs-viewer` | Three.js Viewer | 3d | `.gltf`, `.glb`, `.obj`, `.fbx`, `.stl`, `.ply`, `.dae`, `.3ds`, `.3mf`, `.stp`, `.step`, `.iges`, `.brep` | Yes | 1 | Multi-file; CAD formats require WASM |
| `preview-viewer` | Preview Viewer | preview | `*` (wildcard) | Yes | 10 | Displays generated preview images; internal use |

---

## Extension-to-Viewer Mapping

When a file is opened for viewing, VAMS determines which viewer(s) can handle it based on the file extension. If multiple viewers support the same extension, the viewer with the lowest priority number is used by default. Users can switch between available viewers using the viewer dropdown.

### 3D Model Extensions

| Extension | Available Viewers | Default Viewer |
|---|---|---|
| `.3dm` | Online 3D Viewer | Online 3D Viewer |
| `.3ds` | Three.js Viewer | Three.js Viewer |
| `.3mf` | Three.js Viewer | Three.js Viewer |
| `.amf` | Online 3D Viewer | Online 3D Viewer |
| `.bim` | Online 3D Viewer | Online 3D Viewer |
| `.brep` | Three.js Viewer | Three.js Viewer |
| `.dae` | Three.js Viewer | Three.js Viewer |
| `.fbx` | Three.js Viewer | Three.js Viewer |
| `.glb` | Three.js Viewer, VNTANA Viewer | Three.js Viewer |
| `.gltf` | Three.js Viewer | Three.js Viewer |
| `.iges` | Three.js Viewer | Three.js Viewer |
| `.obj` | Three.js Viewer | Three.js Viewer |
| `.off` | Online 3D Viewer | Online 3D Viewer |
| `.step` / `.stp` | Three.js Viewer | Three.js Viewer |
| `.stl` | Three.js Viewer | Three.js Viewer |
| `.wrl` | Online 3D Viewer | Online 3D Viewer |

### Point Cloud and Gaussian Splat Extensions

| Extension | Available Viewers | Default Viewer |
|---|---|---|
| `.e57` | Potree Viewer, VEERUM Viewer | Potree Viewer |
| `.las` | Potree Viewer, VEERUM Viewer | Potree Viewer |
| `.laz` | Potree Viewer, VEERUM Viewer | Potree Viewer |
| `.ply` | Potree Viewer, Three.js Viewer, BabylonJS Splat, PlayCanvas Splat, VEERUM Viewer | Potree Viewer |
| `.sog` | PlayCanvas Gaussian Splat Viewer | PlayCanvas Gaussian Splat Viewer |
| `.spz` | BabylonJS Gaussian Splat Viewer | BabylonJS Gaussian Splat Viewer |

### USD Extensions

| Extension | Available Viewers | Default Viewer |
|---|---|---|
| `.usd` | Needle USD Viewer | Needle USD Viewer |
| `.usda` | Needle USD Viewer | Needle USD Viewer |
| `.usdc` | Needle USD Viewer | Needle USD Viewer |
| `.usdz` | Needle USD Viewer | Needle USD Viewer |

### Media Extensions

| Extension | Available Viewers | Default Viewer |
|---|---|---|
| `.png`, `.jpg`, `.jpeg`, `.svg`, `.gif` | Image Viewer | Image Viewer |
| `.mp4`, `.webm`, `.mov`, `.avi`, `.mkv`, `.flv`, `.wmv`, `.m4v` | Video Player | Video Player |
| `.mp3`, `.wav`, `.ogg`, `.aac`, `.flac`, `.m4a` | Audio Player | Audio Player |

### Document and Data Extensions

| Extension | Available Viewers | Default Viewer |
|---|---|---|
| `.pdf` | PDF Viewer | PDF Viewer |
| `.html` | HTML Viewer, Text Viewer | HTML Viewer |
| `.json` | Cesium Viewer, Text Viewer, VEERUM Viewer | Text Viewer |
| `.csv` | Columnar Data Viewer, Text Viewer | Text Viewer |
| `.txt`, `.xml`, `.yaml`, `.yml`, `.toml`, `.ini`, `.md`, `.py`, `.js`, `.ts`, `.sql`, `.sh`, `.ps1`, `.log`, `.cfg`, `.inf`, `.ipynb`, `.htm` | Text Viewer | Text Viewer |
| `.rds`, `.fcs` | Columnar Data Viewer | Columnar Data Viewer |

---

## Priority Resolution

When multiple viewers support the same file extension, the viewer with the **lowest priority number** is selected as the default. Users can switch to any other compatible viewer using the viewer selection dropdown in the file viewer UI.

| Priority | Meaning |
|---|---|
| 1 | Preferred viewer for the extension |
| 2 | Secondary viewer available via dropdown |
| 10 | Preview-only viewer (lowest preference) |

---

## Feature Flag Requirements

Certain viewers require specific feature flags to be enabled in the VAMS deployment configuration.

| Viewer | Required Feature Flag | Configuration Setting |
|---|---|---|
| Cesium 3D Tileset Viewer | `ALLOWUNSAFEEVAL` | `app.webUi.allowUnsafeEvalFeatures: true` |
| Needle USD Viewer | `ALLOWUNSAFEEVAL` | `app.webUi.allowUnsafeEvalFeatures: true` |
| Three.js Viewer (CAD formats only) | `ALLOWUNSAFEEVAL` | `app.webUi.allowUnsafeEvalFeatures: true` |

:::warning
Enabling `allowUnsafeEvalFeatures` adds `unsafe-eval` to the Content Security Policy `script-src` directive. This is required by CesiumJS and certain WASM-based loaders (OpenCascade for CAD, Needle for USD). Review this setting with your organization's security team before enabling.
:::


The Three.js Viewer works without `ALLOWUNSAFEEVAL` for standard mesh formats (.gltf, .glb, .obj, .fbx, .stl, .ply, .dae, .3ds, .3mf). The WASM requirement only applies to CAD formats (.stp, .step, .iges, .brep) which use the OpenCascade library.

---

## Viewer Categories

Viewers are organized into five categories:

| Category | Description | Viewers |
|---|---|---|
| `3d` | 3D model and point cloud visualization | Online 3D Viewer, Potree, Cesium, BabylonJS Splat, PlayCanvas Splat, VNTANA, VEERUM, Needle USD, Three.js |
| `media` | Image, video, and audio playback | Image Viewer, Video Player, Audio Player |
| `document` | Document rendering and text display | HTML Viewer, PDF Viewer, Text Viewer |
| `data` | Structured data viewing | Columnar Data Viewer |
| `preview` | Generated preview image display | Preview Viewer |

---

## Viewer Configuration

Viewer plugins are configured in `web/src/visualizerPlugin/config/viewerConfig.json`. Each viewer entry supports the following fields:

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique plugin identifier |
| `name` | string | Display name shown in the viewer dropdown |
| `description` | string | Tooltip description for the viewer |
| `componentPath` | string | Path for Vite dynamic import resolution |
| `supportedExtensions` | string[] | File extensions this viewer handles |
| `supportsMultiFile` | boolean | Whether the viewer can display multiple files simultaneously |
| `canFullscreen` | boolean | Whether fullscreen mode is supported |
| `priority` | number | Lower number = higher preference when multiple viewers match |
| `loadStrategy` | string | `"lazy"` (loaded on demand) or `"eager"` (loaded at startup) |
| `category` | string | Viewer category (`3d`, `media`, `document`, `data`, `preview`) |
| `enabled` | boolean | Whether the plugin is active |
| `featuresEnabledRestriction` | string[] | Feature flags required for this viewer to be available |
| `requiresPreprocessing` | boolean | Whether the viewer needs a pipeline to pre-process files |
| `customParameters` | object | Viewer-specific configuration (e.g., Cesium ion token, BabylonJS settings) |

---

## Creating Custom Viewers

For instructions on developing and registering custom viewer plugins, refer to the viewer plugin development guide at `web/src/visualizerPlugin/README.md` and the [FAQ](../troubleshooting/faq.md#how-do-i-add-a-custom-3d-viewer).
