# Viewer Plugins

VAMS includes a plugin-based viewer architecture with 25 built-in viewer plugins for visualizing 3D models, point clouds, media files, documents, and data. This page provides a configuration reference for viewer plugins.

For the complete list of viewers, supported extensions, and extension-to-viewer mapping, see [File Viewers](../concepts/viewers.md).

---

## Viewer Configuration

Viewer plugins are configured in `web/src/visualizerPlugin/config/viewerConfig.json` (core viewers) and `web/src/visualizerPlugin/config/addonViewerConfig.json` (add-on viewers: the Physna viewer, and the Miris Spatial Streaming viewer and the licensed VNTANA and VEERUM viewers, which ship disabled). A disabled add-on viewer is enabled by setting `"enabled": true` on its entry in `addonViewerConfig.json` before running `npm install` in `web/`. Each viewer entry supports the following fields:

| Field                        | Type     | Description                                                                                                                |
| ---------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------- |
| `id`                         | string   | Unique plugin identifier                                                                                                   |
| `name`                       | string   | Display name shown in the viewer dropdown                                                                                  |
| `description`                | string   | Tooltip description for the viewer                                                                                         |
| `componentPath`              | string   | Path for Vite dynamic import resolution                                                                                    |
| `supportedExtensions`        | string[] | File extensions this viewer handles                                                                                        |
| `supportsMultiFile`          | boolean  | Whether the viewer can display multiple files simultaneously                                                               |
| `canFullscreen`              | boolean  | Whether fullscreen mode is supported                                                                                       |
| `priority`                   | number   | Lower number = listed first when multiple viewers match                                                                    |
| `autoSelect`                 | boolean  | Opens the viewer automatically in visualize mode when it is the sole match at the lowest priority                          |
| `loadStrategy`               | string   | `"lazy"` or `"eager"`. Not read; every viewer loads on first use.                                                          |
| `category`                   | string   | Viewer category (`3d`, `media`, `document`, `data`, `preview`)                                                             |
| `enabled`                    | boolean  | Whether the plugin is active                                                                                               |
| `featuresEnabledRestriction` | string[] | Feature flags required for this viewer to be available                                                                     |
| `requiresPreprocessing`      | boolean  | Whether the viewer needs a pipeline to pre-process files                                                                   |
| `customParameters`           | object   | Viewer-specific configuration (e.g., Cesium ion token, BabylonJS settings, the Robotics Data Viewer's `maxWholeFileBytes`) |

### TIFF/TGA Image Viewer parameters

The `tiff-tga-viewer` entry (`.tga`, `.tif`, `.tiff`) takes two `customParameters`. They are build-time values in `viewerConfig.json`, not deployment configuration, and a viewer entry in `customViewerConfig.json` cannot replace a shipped one.

| Parameter         | Default     | Description                                                                                                                 |
| ----------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------- |
| `maxDecodeBytes`  | `268435456` | Largest file (256 MiB) the viewer downloads to decode. Checked from the file size and `Content-Length` before the download. |
| `maxDecodePixels` | `100000000` | Most pixels (100 megapixels) decoded for one image or TIFF page. Checked from the header before decoding.                   |

Above a cap, or when the file cannot be decoded (a truncated file, BigTIFF, TIFF stored in separate colour planes, old-style JPEG TIFF), the viewer shows the file's generated preview with a notice when one exists, and a plain message otherwise. Decoding runs in a same-origin web worker and falls back to the page when the worker cannot start; see [Viewer Plugin Development](../developer/viewer-plugins.md#decoding-in-a-web-worker-optional).

---

## Creating Custom Viewers

For instructions on developing and registering custom viewer plugins, refer to the viewer plugin development guide at `web/src/visualizerPlugin/README.md` and the [FAQ](../troubleshooting/faq.md#how-do-i-add-a-custom-3d-viewer).
