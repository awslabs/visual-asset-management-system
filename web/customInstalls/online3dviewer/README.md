# Online3DViewer Dynamic Loading Implementation

This directory contains the installation script for the Online3DViewer plugin, which has been converted to use dynamic loading instead of direct NPM package imports.

## Overview

The Online3DViewer plugin follows the adynamic loading pattern for viewers, loading the library from the public directory at runtime instead of bundling it with the main application.

## Implementation Details

### 1. Installation Script (`online3dviewerInstall.js`)

-   Runs `npm install` in the `customInstalls/online3dviewer` directory to install `online-3d-viewer` package
-   Copies the pre-built `o3dv.min.js` from local `node_modules/online-3d-viewer/build/engine/`
-   Copies environment map assets from local `node_modules/online-3d-viewer/website/assets/`
-   Places files in `public/viewers/online3dviewer/` for dynamic loading
-   Cleans up local node_modules after copying (keeps main project clean)

### 2. Dependency Management (`dependencies.ts`)

-   Dynamically loads `o3dv.min.js` via script tag injection
-   Waits for the global `OV` object to be available on `window`
-   Provides cleanup methods to remove scripts and global objects
-   Exposes `getOV()` method to access the library

### 3. Viewer Component (`ViewerCanvas.tsx`)

-   Uses `Online3dViewerDependencyManager.loadOnline3dViewer()` to load the library
-   Accesses the library via `window.OV` instead of direct imports
-   Creates `EmbeddedViewer` instances using the dynamically loaded library

### 4. Package Configuration

-   Created separate `package.json` in `customInstalls/online3dviewer/` with `online-3d-viewer` dependency
-   Removed `online-3d-viewer` from main `web/package.json` entirely
-   Install script manages its own local node_modules for copying files
-   Removed unnecessary del-cli commands from postinstall script

## File Structure

```
web/
├── customInstalls/online3dviewer/
│   ├── online3dviewerInstall.js    # Installation script
│   ├── package.json                 # Local package.json with online-3d-viewer dependency
│   ├── node_modules/                # Local node_modules (created during install, cleaned after)
│   └── README.md                    # This file
├── public/viewers/online3dviewer/
│   ├── o3dv.min.js                 # Dynamically loaded library (1MB)
│   └── assets/                      # Environment maps and assets
└── src/visualizerPlugin/viewers/Online3dViewerPlugin/
    ├── dependencies.ts              # Dynamic loading manager
    └── components/core/
        └── ViewerCanvas.tsx         # Viewer component using window.OV
```

## Benefits

1. **Reduced Bundle Size**: The 1MB Online3DViewer library is no longer bundled with the main application
2. **Faster Initial Load**: Library is only loaded when the viewer is actually used
3. **Better Memory Management**: Library can be unloaded when not needed
4. **Consistent Pattern**: Follows the same approach as other viewer plugins
5. **Easier Updates**: Library can be updated independently of the main application

## Usage

The dynamic loading is handled automatically by the viewer component. No changes are needed to use the viewer - it will load the library on demand when initialized.

## Development

To update the Online3DViewer library:

1. Update the version in `customInstalls/online3dviewer/package.json`
2. Run `npm install` or `yarn install` in the main web directory
3. The postinstall script will automatically:
    - Install the new version in the local customInstalls directory
    - Copy the new files to the public directory
    - Clean up the local node_modules

## Testing

After making changes:

1. Clear browser cache to ensure new files are loaded
2. Open the application and navigate to a 3D model that uses Online3DViewer
3. Check browser console for successful library loading messages
4. Verify the viewer initializes and models load correctly
