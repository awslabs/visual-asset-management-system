# BabylonJS Dynamic Loading Implementation

This directory contains the installation and bundling configuration for the BabylonJS Gaussian Splat Viewer plugin, which has been converted to use dynamic loading instead of direct NPM package imports.

## Overview

The BabylonJS plugin now follows the a dynamic loading pattern for the viewer, loading the library from the public directory at runtime instead of bundling it with the main application.

## Implementation Details

### 1. Installation Script (`babylonjsInstall.js`)

-   Runs `npm install` in the `customInstalls/babylonjs` directory to install BabylonJS packages
-   Builds a webpack bundle from local node_modules
-   Copies all files from the `dist` directory to `public/viewers/babylonjs/` (includes main bundle and chunk files)
-   Cleans up local node_modules and dist directories after copying

### 2. Webpack Configuration (`webpack.config.js`)

-   Bundles `@babylonjs/core` and `@babylonjs/loaders` into a single UMD file
-   Includes Gaussian Splatting mesh support via side-effect import
-   Exposes `BABYLON` globally on window object
-   Minified for production (6.9 MB)

### 3. Webpack Entry File (`src/index.js`)

-   Imports @babylonjs/core
-   Imports @babylonjs/loaders (side-effect)
-   Imports Gaussian Splatting mesh support (side-effect)
-   Exports BABYLON as UMD module

### 4. Dependency Management (`dependencies.ts`)

-   Dynamically loads `babylonjs.bundle.js` via script tag injection
-   Waits for the global `BABYLON` object to be available on `window`
-   Tracks active engine instances for proper cleanup
-   Provides cleanup methods to remove scripts and global objects
-   Uses `window.BABYLON = undefined` instead of delete (strict mode compatible)

### 5. Viewer Component (`BabylonJSGaussianSplatViewerComponent.tsx`)

-   Uses `BabylonJSGaussianSplatDependencyManager.loadBabylonJS()` to load the library
-   Accesses the library via the returned BABYLON object
-   Creates Engine, Scene, and Camera using the dynamically loaded library
-   Gaussian Splatting functionality is included in the bundle

### 6. Package Configuration

-   Created separate `package.json` in `customInstalls/babylonjs/` with BabylonJS dependencies
-   Removed all BabylonJS packages from main `web/package.json`:
    -   `@babylonjs/core`
    -   `@babylonjs/loaders`
    -   `babylonjs`
    -   `babylonjs-loaders`
    -   `@spz-loader/babylonjs`
-   Install script manages its own local node_modules for bundling

## File Structure

```
web/
├── customInstalls/babylonjs/
│   ├── babylonjsInstall.js         # Installation script
│   ├── package.json                 # Local package.json with BabylonJS dependencies
│   ├── webpack.config.js            # Webpack bundling configuration
│   ├── src/
│   │   └── index.js                 # Webpack entry point
│   ├── node_modules/                # Local node_modules (created during install, cleaned after)
│   ├── dist/                        # Webpack output (created during build, cleaned after)
│   └── README.md                    # This file
├── public/viewers/babylonjs/
│   ├── babylonjs.bundle.js         # Main bundle (6.9 MB)
│   ├── 119.babylonjs.bundle.js     # Webpack chunk file
│   ├── 232.babylonjs.bundle.js     # Webpack chunk file
│   └── 287.babylonjs.bundle.js     # Webpack chunk file
└── src/visualizerPlugin/viewers/BabylonJSGaussianSplatViewerPlugin/
    ├── dependencies.ts              # Dynamic loading manager
    └── BabylonJSGaussianSplatViewerComponent.tsx  # Viewer component using window.BABYLON
```

## Benefits

1. **Reduced Bundle Size**: The 6.9 MB BabylonJS library is no longer bundled with the main application
2. **Faster Initial Load**: Library is only loaded when the Gaussian Splat viewer is actually used
3. **Better Memory Management**: Library can be unloaded when not needed
4. **Consistent Pattern**: Follows the same approach as other viewer plugins
5. **Easier Updates**: Library can be updated independently of the main application
6. **Gaussian Splatting Included**: All necessary functionality bundled together

## Bundle Contents

The webpack bundle includes:

-   **@babylonjs/core** - Complete BabylonJS engine
-   **@babylonjs/loaders** - All model loaders (glTF, OBJ, STL, etc.)
-   **Gaussian Splatting Mesh** - Support for .ply and .splat files
-   **UMD Format** - Works in browser with global BABYLON object

## Usage

The dynamic loading is handled automatically by the viewer component. No changes are needed to use the viewer - it will load the library on demand when initialized.

## Development

To update the BabylonJS library:

1. Update the version in `customInstalls/babylonjs/package.json`
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
    - "Loading BabylonJS for BabylonJS Gaussian Splat viewer..."
    - "BabylonJS loaded successfully for BabylonJS Gaussian Splat viewer"
6. Verify the viewer initializes and models load correctly

## Troubleshooting

**Build Warnings**: The webpack build will show size warnings (6.61 MiB exceeds 244 KiB limit). This is expected and acceptable for BabylonJS, as the library is large but only loaded on-demand.

**Cleanup Errors**: If you see errors about deleting window.BABYLON, ensure the dependencies.ts uses `window.BABYLON = undefined` instead of `delete window.BABYLON`.

**Import Errors**: If you see "Cannot find module '@babylonjs/core'", ensure:

-   The package is removed from main web/package.json
-   The bundle file exists in public/viewers/babylonjs/
-   The viewer component uses the dependency manager, not direct imports
