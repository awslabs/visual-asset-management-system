# Cesium Viewer Custom Installation

## Overview

This directory contains the custom installation system for the Cesium viewer, which dynamically loads the CesiumJS rendering engine (`@cesium/engine`) from the public folder instead of bundling it with the main application.

## Architecture

The viewer uses the widget-less `@cesium/engine` package rather than the full `cesium` distribution. The engine provides the complete rendering core (Scene, Camera, Globe, 3D Tiles, imagery, terrain) plus the base `CesiumWidget`, without the `@cesium/widgets` UI layer. Viewer UI controls (home, scene mode, fullscreen, picked-feature info) are implemented as VAMS custom React components in `CesiumViewerComponent.tsx`. This keeps the runtime free of dynamic JavaScript code generation, allowing it to run free from a `unsafe-eval` Content Security Policy. However, a nnown content-type limitations under a strict CSP: KTX2/Basis compressed textures and `.spz` Gaussian splats still require `unsafe-eval` (Emscripten embind).

-   Cesium engine is installed only in this custom install directory
-   `@cesium/engine` is bundled with esbuild into a browser global (`window.Cesium`)
-   Static runtime files (Workers, Assets, ThirdParty wasm, Widget CSS) are copied to the public folder
-   Dependency manager loads the bundle via script tag
-   Component accesses Cesium from window object via wrapper module
-   No runtime dependency on node_modules

## Files

### Installation Files

-   **`package.json`**: NPM package configuration with `@cesium/engine` dependency and esbuild
-   **`cesiumInstall.js`**: Installation script that:
    -   Installs the engine package with `npm install`
    -   Bundles `@cesium/engine` into `Cesium.js` (IIFE, global name `Cesium`) with esbuild
    -   Copies Workers, ThirdParty (including wasm binaries), Assets, and Widget CSS
    -   Places everything in `web/public/viewers/cesium/`

### Application Files

-   **`dependencies.ts`**: CesiumDependencyManager for dynamic loading

    -   Loads Cesium.js via script tag
    -   Loads Widget/CesiumWidget.css via StylesheetManager
    -   Sets CESIUM_BASE_URL for assets
    -   Accesses Cesium from `window.Cesium`

-   **`cesium.ts`**: Wrapper module that re-exports Cesium from window

    -   Provides typed access to Cesium classes
    -   Allows importing: `import * as Cesium from "./cesium"`
    -   Simplifies component code

-   **`CesiumViewerComponent.tsx`**: Uses dynamic loading and custom UI
    -   Imports from wrapper module instead of a cesium package
    -   Loads Cesium via CesiumDependencyManager on mount
    -   Creates a `CesiumWidget` and renders VAMS custom scene controls

## Installation Process

The installation happens automatically during `npm install` in the main web directory via the postinstall script.

### Manual Installation

To manually rebuild:

```bash
cd web
node customInstalls/cesium/cesiumInstall.js
```

### Build Steps

1. **Cleanup**: Removes previous builds and node_modules
2. **Install**: Runs `npm install` to get the `@cesium/engine` package
3. **Bundle**: Runs esbuild to produce the browser-global `Cesium.js`
4. **Copy**: Copies Workers, ThirdParty, Assets, and Widget CSS to public folder

## Output Files

After installation, the following structure is created in `web/public/viewers/cesium/`:

```
web/public/viewers/cesium/
├── Cesium.js              # esbuild IIFE bundle of @cesium/engine (~4MB minified)
├── Assets/                # Textures, IAU data, approximate terrain heights
├── ThirdParty/            # Zip worker + wasm binaries (draco, basis, splats, zip)
├── Workers/               # Web workers (draco decoding, KTX2 transcoding, geometry)
└── Widget/
    └── CesiumWidget.css   # Base widget styles
```

## Usage in Application

### Loading Cesium

```typescript
// In component
useEffect(() => {
    const loadCesiumLib = async () => {
        await CesiumDependencyManager.loadCesium();
        // Cesium is now available on window.Cesium
    };
    loadCesiumLib();
}, []);
```

### Using Cesium

```typescript
// Import from wrapper
import * as Cesium from "./cesium";

// Use Cesium classes
const widget = new Cesium.CesiumWidget(container);
const tileset = await Cesium.Cesium3DTileset.fromUrl(url);
```

## Benefits

### Content Security Policy Compatibility

-   `@cesium/engine` contains no dynamic JavaScript code generation on its load and render paths
-   Runs under `script-src 'wasm-unsafe-eval'` (WebAssembly compilation only)
-   The full `cesium` distribution's widgets layer embeds Knockout.js, which compiles binding expressions with `new Function` and therefore requires the broader `unsafe-eval` directive; the engine-only build avoids this

### Reduced Bundle Size

-   Engine-only bundle (~4MB) is significantly smaller than the full distribution
-   Cesium loads only when the Cesium viewer is used

### Consistent Pattern

-   Matches Potree and Vntana loading approaches
-   Uses same StylesheetManager and script loading utilities
-   Follows VAMS plugin architecture

### No Runtime Dependency

-   Cesium not needed in main `web/package.json`
-   Cleaner dependency tree
-   Smaller main node_modules

## Known Content-Type Limitations

Two content types rely on Emscripten embind glue that generates function bindings with `new Function` and will not load under a `wasm-unsafe-eval`-only CSP:

-   **KTX2/Basis compressed textures** (`KHR_texture_basisu` in glTF/3D Tiles): the `transcodeKTX2` worker fails at module init
-   **SPZ Gaussian splats** (`.spz`): the spz-loader codec fails on first load

Standard 3D Tiles, glTF/glb (including Draco compression), imagery, and terrain are unaffected.

## Troubleshooting

### Bundle Not Loading

If Cesium.js fails to load:

1. Verify `Cesium.js` exists in `web/public/viewers/cesium/`
2. Check browser console for script loading errors
3. Verify `window.Cesium` is defined after bundle loads
4. Check network tab for 404 errors

### Assets Not Found

If Cesium can't find assets (Workers, textures, etc.):

1. Verify `CESIUM_BASE_URL` is set to `/viewers/cesium/`
2. Check that Assets/, Workers/, ThirdParty/, and Widget/ directories exist
3. Verify file paths in browser network tab

### TypeScript Errors

If you see TypeScript errors:

1. Ensure you're importing from `./cesium` wrapper, not a cesium package
2. Use `any` types for complex Cesium objects if needed
3. The wrapper provides common Cesium exports

### Viewer Not Initializing

If the viewer doesn't initialize:

1. Check that `cesiumLoaded` state is true before creating the widget
2. Verify Cesium dependency manager loaded successfully
3. Check browser console for initialization errors

## Development

### Modifying the Installation

To change what's copied, edit `cesiumInstall.js`:

```javascript
// Copy additional files
await fs.copy(
    path.join(enginePackageDir, "Source/SomeOtherDir"),
    path.join(destinationDir, "SomeOtherDir")
);
```

### Adding Cesium Exports

To add more Cesium exports to the wrapper, edit `cesium.ts`:

```typescript
export const NewCesiumClass = CesiumWrapper.NewCesiumClass;
```

### Testing Changes

After modifying the installation:

1. Run `node customInstalls/cesium/cesiumInstall.js`
2. Verify files in `web/public/viewers/cesium/`
3. Test in the application

## Version Updates

To update the Cesium engine version:

1. Update version in `package.json`:

    ```json
    {
        "dependencies": {
            "@cesium/engine": "26.1.0"
        }
    }
    ```

2. Rebuild:

    ```bash
    cd web
    node customInstalls/cesium/cesiumInstall.js
    ```

3. Test the updated viewer in the application
4. Re-verify CSP compatibility: `grep -c "new Function" public/viewers/cesium/Cesium.js` should only match the spz-loader embind glue (see Known Content-Type Limitations)

## Technical Details

### IIFE Bundle

The esbuild bundle wraps the engine's ES modules in an IIFE:

-   Exposes `window.Cesium` for classic script-tag loading
-   Tree-shaken and minified by esbuild
-   Workers remain separate static files fetched at runtime

### Asset Management

The Cesium engine requires these runtime assets:

-   **Workers**: Web workers for 3D tiles, draco decoding, KTX2 transcoding
-   **Assets**: Textures, IAU2006 orientation data, approximate terrain heights
-   **ThirdParty**: Zip worker and wasm binaries
-   **Widget**: Base CesiumWidget stylesheet

All are copied to maintain full engine functionality.

### CESIUM_BASE_URL

The `CESIUM_BASE_URL` global variable tells Cesium where to find its assets:

```javascript
window.CESIUM_BASE_URL = "/viewers/cesium/";
```

This must be set BEFORE loading Cesium.js.
