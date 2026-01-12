# Cesium Viewer Custom Installation

## Overview

This directory contains the custom installation system for the Cesium viewer, which dynamically loads Cesium from the public folder instead of bundling it with the main application.

## Architecture

### Previous Approach

-   Cesium was installed as a direct dependency in `web/package.json`
-   Imported directly: `import * as Cesium from "cesium"`
-   Bundled into the main application at build time
-   Only asset files were copied to public folder

### New Approach (Dynamic Loading)

-   Cesium is installed only in this custom install directory
-   Pre-built Cesium.js bundle is copied to public folder
-   All Source assets (Workers, Assets, Widgets, etc.) are also copied
-   Dependency manager loads Cesium via script tag
-   Component accesses Cesium from window object via wrapper module
-   No runtime dependency on node_modules

## Files

### Installation Files

-   **`package.json`**: NPM package configuration with cesium dependency
-   **`cesiumInstall.js`**: Installation script that:
    -   Installs cesium package with `npm install`
    -   Copies pre-built `Cesium.js` from `node_modules/cesium/Build/Cesium/`
    -   Copies all Source files (Assets, Workers, Widgets, etc.)
    -   Places everything in `web/public/viewers/cesium/`

### Application Files

-   **`dependencies.ts`**: CesiumDependencyManager for dynamic loading

    -   Loads Cesium.js via script tag
    -   Loads widgets.css via StylesheetManager
    -   Sets CESIUM_BASE_URL for assets
    -   Accesses Cesium from `window.Cesium`

-   **`cesium.ts`**: Wrapper module that re-exports Cesium from window

    -   Provides typed access to Cesium classes
    -   Allows importing: `import * as Cesium from "./cesium"`
    -   Simplifies component code

-   **`CesiumViewerComponent.tsx`**: Updated to use dynamic loading
    -   Imports from wrapper module instead of 'cesium' package
    -   Loads Cesium via CesiumDependencyManager on mount
    -   Waits for Cesium to load before initializing viewer

## Installation Process

The installation happens automatically during `yarn install` in the main web directory via the postinstall script.

### Manual Installation

To manually rebuild:

```bash
cd web
node customInstalls/cesium/cesiumInstall.js
```

### Build Steps

1. **Cleanup**: Removes previous builds and node_modules
2. **Install**: Runs `npm install` to get cesium package
3. **Copy**: Copies pre-built Cesium.js and Source assets to public folder

## Output Files

After installation, the following structure is created in `web/public/viewers/cesium/`:

```
web/public/viewers/cesium/
├── Cesium.js              # Pre-built UMD bundle (~2MB minified)
├── Cesium.js.map          # Source map for debugging
├── Cesium.d.ts            # TypeScript definitions
├── Assets/                # Textures, models, etc.
├── ThirdParty/            # Third-party dependencies
└── Widgets/               # UI widgets and CSS
    └── widgets.css        # Cesium widget styles
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
const viewer = new Cesium.Viewer(container);
const tileset = await Cesium.Cesium3DTileset.fromUrl(url);
```

## Benefits

### Reduced Bundle Size

-   **Before**: Cesium bundled with main application (~50MB+ in node_modules)
-   **After**: Cesium loaded dynamically only when needed

### Improved Performance

-   Main application bundle is smaller
-   Cesium loads only when Cesium viewer is used
-   Better code splitting

### Consistent Pattern

-   Matches Potree and Vntana loading approaches
-   Uses same StylesheetManager and script loading utilities
-   Follows VAMS plugin architecture

### No Runtime Dependency

-   Cesium not needed in main `web/package.json`
-   Cleaner dependency tree
-   Smaller main node_modules

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
2. Check that Assets/, Workers/, and Widgets/ directories exist
3. Verify file paths in browser network tab

### TypeScript Errors

If you see TypeScript errors:

1. Ensure you're importing from `./cesium` wrapper, not `cesium` package
2. Use `any` types for complex Cesium objects if needed
3. The wrapper provides common Cesium exports

### Viewer Not Initializing

If the viewer doesn't initialize:

1. Check that `cesiumLoaded` state is true before creating viewer
2. Verify Cesium dependency manager loaded successfully
3. Check browser console for initialization errors

## Development

### Modifying the Installation

To change what's copied, edit `cesiumInstall.js`:

```javascript
// Copy additional files
await fs.copy(
    path.join(cesiumSourceDir, "SomeOtherDir"),
    path.join(destinationDir, "SomeOtherDir")
);
```

### Adding Cesium Exports

To add more Cesium exports to the wrapper, edit `cesium.ts`:

```typescript
export const NewCesiumClass = CesiumLib.NewCesiumClass;
```

### Testing Changes

After modifying the installation:

1. Run `node customInstalls/cesium/cesiumInstall.js`
2. Verify files in `web/public/viewers/cesium/`
3. Test in the application

## Version Updates

To update the Cesium version:

1. Update version in `package.json`:

    ```json
    {
        "dependencies": {
            "cesium": "1.119.0"
        }
    }
    ```

2. Rebuild:

    ```bash
    cd web
    node customInstalls/cesium/cesiumInstall.js
    ```

3. Test the updated viewer in the application

## Technical Details

### UMD Format

Cesium's pre-built bundle uses UMD (Universal Module Definition) format:

-   Works in browsers (exposes `window.Cesium`)
-   Compatible with AMD and CommonJS
-   Official Cesium build (tested and optimized)

### Asset Management

Cesium requires extensive assets:

-   **Workers**: Web workers for 3D tiles processing
-   **Assets**: Textures, models, shaders
-   **Widgets**: UI components and styles
-   **ThirdParty**: External dependencies

All are copied to maintain full Cesium functionality.

### CESIUM_BASE_URL

The `CESIUM_BASE_URL` global variable tells Cesium where to find its assets:

```javascript
window.CESIUM_BASE_URL = "/viewers/cesium/";
```

This must be set BEFORE loading Cesium.js.

## Notes

-   The pre-built Cesium.js is production-ready and minified
-   Source maps are included for debugging
-   All Cesium features are available (Ion, terrain, imagery, etc.)
-   The wrapper module simplifies imports and provides type safety
