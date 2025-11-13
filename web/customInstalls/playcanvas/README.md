# PlayCanvas Dynamic Loading Implementation

This directory contains the installation and bundling configuration for the PlayCanvas Gaussian Splat Viewer plugin, which has been converted to use dynamic loading instead of direct NPM package imports.

## Overview

The PlayCanvas plugin now follows the same dynamic loading pattern as Vntana, Cesium, Potree, Online3DViewer, and BabylonJS, loading the library from the public directory at runtime instead of bundling it with the main application.

## Implementation Details

### 1. Installation Script (`playcanvasInstall.js`)

-   Runs `npm install` in the `customInstalls/playcanvas` directory to install PlayCanvas package
-   Builds a webpack bundle from local node_modules
-   Copies all files from the `dist` directory to `public/viewers/playcanvas/`
-   Cleans up local node_modules and dist directories after copying

### 2. Webpack Configuration (`webpack.config.js`)

-   Bundles `playcanvas` into a single UMD file
-   Exposes `pc` globally on window object (PlayCanvas namespace)
-   Minified for production (2 MB)

### 3. Webpack Entry File (`src/index.js`)

-   Imports playcanvas
-   Exports pc as UMD module

### 4. Dependency Management (`dependencies.ts`)

-   Dynamically loads `playcanvas.bundle.js` via script tag injection
-   Waits for the global `pc` object to be available on `window`
-   Provides cleanup methods to remove scripts and global objects
-   Uses `window.pc = undefined` instead of delete (strict mode compatible)
-   Maintains load promise to prevent duplicate loading

### 5. Viewer Component (`PlayCanvasGaussianSplatViewerComponent.tsx`)

-   Already uses `PlayCanvasGaussianSplatDependencyManager.loadPlayCanvas()` correctly
-   No changes needed - component was already structured properly!
-   Creates Application, Camera, and GSplat entities using the dynamically loaded library

### 6. Package Configuration

-   Created separate `package.json` in `customInstalls/playcanvas/` with playcanvas dependency
-   Removed `playcanvas: 2.11.8` from main `web/package.json`
-   Install script manages its own local node_modules for bundling

## File Structure

```
web/
├── customInstalls/playcanvas/
│   ├── playcanvasInstall.js        # Installation script
│   ├── package.json                 # Local package.json with playcanvas dependency
│   ├── webpack.config.js            # Webpack bundling configuration
│   ├── src/
│   │   └── index.js                 # Webpack entry point
│   ├── node_modules/                # Local node_modules (created during install, cleaned after)
│   ├── dist/                        # Webpack output (created during build, cleaned after)
│   └── README.md                    # This file
├── public/viewers/playcanvas/
│   └── playcanvas.bundle.js        # Dynamically loaded library (2 MB)
└── src/visualizerPlugin/viewers/PlayCanvasGaussianSplatViewerPlugin/
    ├── dependencies.ts              # Dynamic loading manager
    └── PlayCanvasGaussianSplatViewerComponent.tsx  # Viewer component using window.pc
```

## Benefits

1. **Reduced Bundle Size**: The 2 MB PlayCanvas library is no longer bundled with the main application
2. **Faster Initial Load**: Library is only loaded when the Gaussian Splat viewer is actually used
3. **Better Memory Management**: Library can be unloaded when not needed
4. **Consistent Pattern**: Follows the same approach as other viewer plugins
5. **Easier Updates**: Library can be updated independently of the main application
6. **Gaussian Splatting Included**: All necessary functionality bundled together

## Bundle Contents

The webpack bundle includes:

-   **PlayCanvas Engine** - Complete PlayCanvas engine with all features
-   **Gaussian Splatting Support** - Built-in GSplat component support
-   **UMD Format** - Works in browser with global `pc` object

## Usage

The dynamic loading is handled automatically by the viewer component. No changes are needed to use the viewer - it will load the library on demand when initialized.

## Development

To update the PlayCanvas library:

1. Update the version in `customInstalls/playcanvas/package.json`
2. Run `npm install` or `yarn install` in the main web directory
3. The postinstall script will automatically:
    - Install the new version in the local customInstalls directory
    - Build a new webpack bundle
    - Copy the new bundle to the public directory
    - Clean up the local node_modules and dist directories

## Testing

After making changes:

1. Clear browser cache to ensure new files are loaded
2. Clear webpack cache: `Remove-Item -Recurse -Force web/node_modules/.cache`
3. Start the development server
4. Open the application and navigate to a Gaussian Splat model (.ply or .splat file)
5. Check browser console for successful library loading messages:
    - "[playcanvas-gaussian-splat-viewer] Loading PlayCanvas engine..."
    - "[playcanvas-gaussian-splat-viewer] PlayCanvas engine loaded successfully"
6. Verify the viewer initializes and models load correctly

## Troubleshooting

**Build Warnings**: The webpack build will show size warnings (1.98 MiB exceeds 244 KiB limit). This is expected and acceptable for PlayCanvas, as the library is large but only loaded on-demand.

**Cleanup Errors**: If you see errors about deleting window.pc, ensure the dependencies.ts uses `window.pc = undefined` instead of `delete window.pc`.

**Import Errors**: If you see "Cannot find module 'playcanvas'", ensure:

-   The package is removed from main web/package.json
-   The bundle file exists in public/viewers/playcanvas/
-   The viewer component uses the dependency manager (it already does!)

## Key Advantage

Unlike other viewer conversions, the PlayCanvas component was already using the dependency manager pattern correctly, so **no component changes were needed**! This made the conversion much simpler - we only needed to change the loading mechanism from dynamic `import()` to script tag loading.
