# File Viewers

VAMS includes a plugin-based viewer system that enables browser-based visualization of 3D models, point clouds, media files, documents, and data -- without requiring desktop software or specialized licenses. This page is the single source of truth for all built-in viewers and their supported file extensions.

## What are file viewers

File viewers are browser-based rendering components that display asset files directly in the VAMS web interface. When you open a file from the file manager, VAMS automatically selects the best viewer based on the file extension and viewer priority. If multiple viewers support the same extension, you can switch between them using a dropdown in the viewer UI.

The viewer system is built on a **plugin architecture**. Each viewer is an independent module with its own dependencies, configuration, and supported file types. New viewers can be added without modifying any core system code -- only a configuration entry and the viewer component are required.

### How viewer selection works

1. VAMS reads the file extension (e.g., `.glb`, `.e57`, `.pdf`).
2. All enabled viewers that support that extension are identified.
3. The viewer with the **lowest priority number** is selected as the default.
4. If multiple viewers match, a dropdown allows you to switch between them.
5. If no viewer matches the extension but a preview image exists, the **Preview Viewer** displays the thumbnail.

---

## Viewer categories

Viewers are organized into five categories based on the type of content they render.

### 3D

Interactive 3D model, point cloud, CAD, USD, and Gaussian splat viewing. Nine viewers cover a wide range of spatial data formats, from mesh files (glTF, OBJ, FBX) to point clouds (E57, LAS) to Universal Scene Description (USD). Some 3D viewers support multi-file mode, allowing multiple files to be loaded into a single scene.

### Media

Image, video, and audio playback. The Image Viewer supports zoom and pan. The Video Player and Audio Player provide standard browser-native playback controls.

### Document

Rendering of PDF, HTML, and text-based files. The Text Viewer provides syntax highlighting for over 20 programming and configuration file types.

### Data

Tabular display of columnar data formats such as CSV, RDS, and FCS files.

### Preview

A fallback viewer that displays generated preview thumbnails for files that have no dedicated viewer. The Preview Viewer has the lowest priority (10) and matches all file extensions as a wildcard.

---

## Master viewer table

This table is the definitive reference for all 17 built-in viewer plugins.

| Viewer Name                          | Category | Supported Extensions                                                                                                                                                    | Priority | Multi-File | Notes                                                                                                                                                               |
| ------------------------------------ | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Three.js Viewer**                  | 3D       | `.gltf`, `.glb`, `.obj`, `.fbx`, `.stl`, `.ply`, `.dae`, `.3ds`, `.3mf`, `.stp`, `.step`, `.iges`, `.brep`                                                              | 1        | Yes        | Primary mesh and CAD viewer. Scene graph, material editing, transform controls. CAD formats (`.stp`, `.step`, `.iges`, `.brep`) require WASM and `ALLOWUNSAFEEVAL`. |
| **Potree Viewer**                    | 3D       | `.e57`, `.las`, `.laz`, `.ply`                                                                                                                                          | 1        | No         | Octree-based point cloud streaming. Requires the Potree preprocessing pipeline. Shows latest version only.                                                          |
| **BabylonJS Gaussian Splat Viewer**  | 3D       | `.ply`, `.spz`                                                                                                                                                          | 1        | No         | Gaussian splat visualization with WebXR support.                                                                                                                    |
| **Needle USD Viewer (Experimental)** | 3D       | `.usd`, `.usda`, `.usdc`, `.usdz`                                                                                                                                       | 1        | No         | Universal Scene Description via WebAssembly. Requires `ALLOWUNSAFEEVAL`. Experimental -- may not display all USD files correctly or load all dependencies.          |
| **Online 3D Viewer**                 | 3D       | `.3dm`, `.amf`, `.bim`, `.off`, `.wrl`                                                                                                                                  | 2        | Yes        | Rhinoceros 3D, AMF, BIM, OFF, and VRML formats.                                                                                                                     |
| **Cesium 3D Tileset Viewer**         | 3D       | `.json`                                                                                                                                                                 | 2        | No         | 3D Tileset viewing with geospatial capabilities. Requires `ALLOWUNSAFEEVAL`. Shows latest version only.                                                             |
| **PlayCanvas Gaussian Splat Viewer** | 3D       | `.ply`, `.sog`                                                                                                                                                          | 2        | No         | Gaussian splat visualization with orbit camera and auto-focus.                                                                                                      |
| **VNTANA 3D Viewer**                 | 3D       | `.glb`                                                                                                                                                                  | 2        | No         | Licensed viewer for high-quality GLB rendering. See [Licensed viewers](#licensed-viewers).                                                                          |
| **VEERUM 3D Viewer**                 | 3D       | `.e57`, `.las`, `.laz`, `.ply`, `.json`                                                                                                                                 | 2        | Yes        | Licensed viewer for point clouds and 3D tilesets. Requires Potree pipeline. Shows latest version only. See [Licensed viewers](#licensed-viewers).                   |
| **Image Viewer**                     | Media    | `.png`, `.jpg`, `.jpeg`, `.svg`, `.gif`                                                                                                                                 | 1        | No         | Zoom and pan capabilities. Eager load strategy.                                                                                                                     |
| **Video Player**                     | Media    | `.mp4`, `.webm`, `.mov`, `.avi`, `.mkv`, `.flv`, `.wmv`, `.m4v`                                                                                                         | 1        | No         | Standard browser-native playback controls.                                                                                                                          |
| **Audio Player**                     | Media    | `.mp3`, `.wav`, `.ogg`, `.aac`, `.flac`, `.m4a`                                                                                                                         | 1        | No         | Standard browser-native playback controls.                                                                                                                          |
| **PDF Viewer**                       | Document | `.pdf`                                                                                                                                                                  | 1        | No         | Navigation, zoom, and page management controls.                                                                                                                     |
| **HTML Viewer**                      | Document | `.html`                                                                                                                                                                 | 1        | No         | Renders HTML presentations and documents.                                                                                                                           |
| **Text Viewer**                      | Document | `.txt`, `.json`, `.xml`, `.html`, `.htm`, `.yaml`, `.yml`, `.toml`, `.ini`, `.ipynb`, `.inf`, `.cfg`, `.md`, `.sh`, `.csv`, `.py`, `.log`, `.js`, `.ts`, `.sql`, `.ps1` | 1        | No         | Syntax highlighting for 20+ file types.                                                                                                                             |
| **Columnar Data Viewer**             | Data     | `.rds`, `.fcs`, `.csv`                                                                                                                                                  | 2        | No         | Tabular data display with column headers.                                                                                                                           |
| **Preview Viewer**                   | Preview  | `*` (all extensions)                                                                                                                                                    | 10       | No         | Displays generated preview thumbnails. Fallback viewer for files with no dedicated viewer.                                                                          |

---

## File extension to viewer mapping

This table provides a quick lookup from file extension to the viewer(s) that handle it. When multiple viewers are listed, the **default** column indicates which is selected automatically (lowest priority number). Users can switch to any other listed viewer using the viewer dropdown.

### 3D model extensions

| Extension        | Default Viewer   | Other Available Viewers |
| ---------------- | ---------------- | ----------------------- |
| `.3dm`           | Online 3D Viewer | --                      |
| `.3ds`           | Three.js Viewer  | --                      |
| `.3mf`           | Three.js Viewer  | --                      |
| `.amf`           | Online 3D Viewer | --                      |
| `.bim`           | Online 3D Viewer | --                      |
| `.brep`          | Three.js Viewer  | --                      |
| `.dae`           | Three.js Viewer  | --                      |
| `.fbx`           | Three.js Viewer  | --                      |
| `.glb`           | Three.js Viewer  | VNTANA 3D Viewer        |
| `.gltf`          | Three.js Viewer  | --                      |
| `.iges`          | Three.js Viewer  | --                      |
| `.obj`           | Three.js Viewer  | --                      |
| `.off`           | Online 3D Viewer | --                      |
| `.step` / `.stp` | Three.js Viewer  | --                      |
| `.stl`           | Three.js Viewer  | --                      |
| `.wrl`           | Online 3D Viewer | --                      |

### Point cloud and Gaussian splat extensions

| Extension | Default Viewer                                                  | Other Available Viewers                            |
| --------- | --------------------------------------------------------------- | -------------------------------------------------- |
| `.e57`    | Potree Viewer                                                   | VEERUM 3D Viewer                                   |
| `.las`    | Potree Viewer                                                   | VEERUM 3D Viewer                                   |
| `.laz`    | Potree Viewer                                                   | VEERUM 3D Viewer                                   |
| `.ply`    | Potree Viewer, BabylonJS Gaussian Splat Viewer, Three.js Viewer | PlayCanvas Gaussian Splat Viewer, VEERUM 3D Viewer |
| `.sog`    | PlayCanvas Gaussian Splat Viewer                                | --                                                 |
| `.spz`    | BabylonJS Gaussian Splat Viewer                                 | --                                                 |

:::info[PLY files and multiple viewers]
The `.ply` extension is used for both point cloud data and Gaussian splat data. Three viewers match at priority 1 (Potree, BabylonJS Gaussian Splat, Three.js). VAMS presents all compatible viewers and you can select the appropriate one for your data type.
:::

### USD extensions

| Extension | Default Viewer                   | Other Available Viewers |
| --------- | -------------------------------- | ----------------------- |
| `.usd`    | Needle USD Viewer (Experimental) | --                      |
| `.usda`   | Needle USD Viewer (Experimental) | --                      |
| `.usdc`   | Needle USD Viewer (Experimental) | --                      |
| `.usdz`   | Needle USD Viewer (Experimental) | --                      |

:::warning[Needle USD Viewer -- Experimental]
The Needle USD Viewer is experimental. It may not display all USD files correctly or load all file dependencies, particularly with compressed USDC files or complex scene hierarchies with external references. For production workflows requiring reliable USD viewing, consider using a desktop USD viewer such as NVIDIA Omniverse or Pixar's usdview.
:::

### Media extensions

| Extension        | Default Viewer | Other Available Viewers |
| ---------------- | -------------- | ----------------------- |
| `.aac`           | Audio Player   | --                      |
| `.avi`           | Video Player   | --                      |
| `.flac`          | Audio Player   | --                      |
| `.flv`           | Video Player   | --                      |
| `.gif`           | Image Viewer   | --                      |
| `.jpg` / `.jpeg` | Image Viewer   | --                      |
| `.m4a`           | Audio Player   | --                      |
| `.m4v`           | Video Player   | --                      |
| `.mkv`           | Video Player   | --                      |
| `.mov`           | Video Player   | --                      |
| `.mp3`           | Audio Player   | --                      |
| `.mp4`           | Video Player   | --                      |
| `.ogg`           | Audio Player   | --                      |
| `.png`           | Image Viewer   | --                      |
| `.svg`           | Image Viewer   | --                      |
| `.wav`           | Audio Player   | --                      |
| `.webm`          | Video Player   | --                      |
| `.wmv`           | Video Player   | --                      |

### Document and data extensions

| Extension        | Default Viewer       | Other Available Viewers                    |
| ---------------- | -------------------- | ------------------------------------------ |
| `.cfg`           | Text Viewer          | --                                         |
| `.csv`           | Text Viewer          | Columnar Data Viewer                       |
| `.fcs`           | Columnar Data Viewer | --                                         |
| `.htm`           | Text Viewer          | --                                         |
| `.html`          | HTML Viewer          | Text Viewer                                |
| `.inf`           | Text Viewer          | --                                         |
| `.ini`           | Text Viewer          | --                                         |
| `.ipynb`         | Text Viewer          | --                                         |
| `.js`            | Text Viewer          | --                                         |
| `.json`          | Text Viewer          | Cesium 3D Tileset Viewer, VEERUM 3D Viewer |
| `.log`           | Text Viewer          | --                                         |
| `.md`            | Text Viewer          | --                                         |
| `.pdf`           | PDF Viewer           | --                                         |
| `.ps1`           | Text Viewer          | --                                         |
| `.py`            | Text Viewer          | --                                         |
| `.rds`           | Columnar Data Viewer | --                                         |
| `.sh`            | Text Viewer          | --                                         |
| `.sql`           | Text Viewer          | --                                         |
| `.toml`          | Text Viewer          | --                                         |
| `.ts`            | Text Viewer          | --                                         |
| `.txt`           | Text Viewer          | --                                         |
| `.xml`           | Text Viewer          | --                                         |
| `.yaml` / `.yml` | Text Viewer          | --                                         |

---

## Viewer screenshots

### USD viewer (Needle Engine) -- Experimental

![VAMS file viewer page displaying a USDZ 3D model rendered by the Needle USD Viewer](/img/view_file_page_usdz_20260323_v2.5.png)

### VEERUM point cloud viewer

![VEERUM 3D Viewer rendering a point cloud dataset with multi-file support](/img/veerum_viewer_20260323_v2.5.png)

### VNTANA 3D viewer

![VNTANA 3D Viewer displaying a high-quality GLB model](/img/vntana_viewer__20260323_v2.5.png)

### Three.js 3D model viewer

![Three.js Viewer rendering a 3D model with scene controls](/img/model_view.png)

---

## WebAssembly requirements

Several viewers depend on WebAssembly (WASM) modules for rendering. WASM-based viewers require a Cross-Origin Isolation (COI) service worker to enable `SharedArrayBuffer` support in the browser. VAMS includes this service worker automatically in production deployments.

The following viewers use WASM:

| Viewer                             | WASM Library                        | Required Feature Flag |
| ---------------------------------- | ----------------------------------- | --------------------- |
| Needle USD Viewer (Experimental)   | `usd-wasm`                          | `ALLOWUNSAFEEVAL`     |
| Cesium 3D Tileset Viewer           | CesiumJS (WebGL shader compilation) | `ALLOWUNSAFEEVAL`     |
| Three.js Viewer (CAD formats only) | OpenCascade (`opencascade.js`)      | `ALLOWUNSAFEEVAL`     |

:::warning[Content Security Policy]
Enabling `ALLOWUNSAFEEVAL` adds `unsafe-eval` to the Content Security Policy `script-src` directive. This is required by CesiumJS for WebGL shader compilation and by WASM loaders for OpenCascade and Needle USD. Review this setting with your organization's security team before enabling. Set `app.webUi.allowUnsafeEvalFeatures` to `true` in the CDK `config.json` to enable.
:::

The Three.js Viewer works without `ALLOWUNSAFEEVAL` for standard mesh formats (`.gltf`, `.glb`, `.obj`, `.fbx`, `.stl`, `.ply`, `.dae`, `.3ds`, `.3mf`). The WASM requirement applies only to CAD formats (`.stp`, `.step`, `.iges`, `.brep`) which use the OpenCascade library.

---

## Licensed viewers

Two viewer plugins are integrations with commercial products and require separate licenses to use.

| Viewer               | Vendor                            | Formats                                 | License Required |
| -------------------- | --------------------------------- | --------------------------------------- | ---------------- |
| **VNTANA 3D Viewer** | [VNTANA](https://www.vntana.com/) | `.glb`                                  | Yes              |
| **VEERUM 3D Viewer** | [Veerum](https://veerum.com/)     | `.e57`, `.las`, `.laz`, `.ply`, `.json` | Yes              |

Both licensed viewers are enabled in the viewer configuration by default but require a valid license from the respective vendor to function. Without a license, these viewers will not render content. Contact the vendor directly for licensing details.

For more information on partner integrations, see [Partner Integrations](../additional/partner-integrations.md).

---

## Related pages

-   [Viewer Plugin Development](../developer/viewer-plugins.md) -- How to create custom viewer plugins
-   [Web Interface Overview](../user-guide/web-interface.md) -- Using viewers in the web UI
-   [Viewer Plugins Reference](../additional/viewer-plugins.md) -- Detailed configuration field reference
-   [Files and Versions](files-and-versions.md) -- File concepts and operations
