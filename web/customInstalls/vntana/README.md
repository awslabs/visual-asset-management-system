# Vntana Viewer Bundle Installation

## Overview

This directory contains the build system for creating a bundled version of the Vntana viewer that can be dynamically loaded from the public folder, similar to how Potree is loaded in the VAMS application.

## Architecture

### Previous Approach

-   Vntana viewer was installed as a direct dependency in `web/package.json`
-   Custom install script copied entire `node_modules` directory (~300+ files) to `web/public/viewers/vntana/`
-   Dependency manager used dynamic imports (`import("@vntana/viewer")`)
-   Required package to be in node_modules at runtime

### New Approach (Bundled)

-   Vntana viewer is installed only in this custom install directory
-   Webpack bundles the viewer and all dependencies into a single UMD file
-   Only 3 files are copied to public folder:
    -   `vntana-viewer.bundle.js` (~1MB minified)
    -   `vntana-viewer.bundle.js.map` (source map for debugging)
    -   `vntana-viewer.css` (viewer styles)
-   Dependency manager loads files via script tags (like Potree)
-   No runtime dependency on node_modules

## Files

### Build Configuration

-   **`webpack.config.js`**: Webpack configuration for bundling

    -   Creates UMD bundle exposing `window.VntanaViewer`
    -   Minifies JavaScript and CSS
    -   Generates source maps for debugging
    -   Bundles all dependencies (lit, lit-element, etc.)

-   **`package.json`**: NPM package configuration

    -   Dependencies: `@vntana/viewer` (the viewer package)
    -   DevDependencies: webpack and related plugins
    -   Build script: `npm run build` (runs webpack)

-   **`vntanaInstall.js`**: Installation script
    -   Cleans previous builds
    -   Installs dependencies with `npm install`
    -   Builds bundle with webpack
    -   Copies bundled files to `web/public/viewers/vntana/`

## Installation Process

The installation happens automatically during `yarn install` in the main web directory via the postinstall script.

### Manual Installation

To manually rebuild the bundle:

```bash
cd web
node customInstalls/vntana/vntanaInstall.js
```

### Build Steps

1. **Cleanup**: Removes previous builds and node_modules
2. **Install**: Runs `npm install` to get @vntana/viewer and webpack
3. **Bundle**: Runs webpack to create the bundled files
4. **Copy**: Copies bundle files to `web/public/viewers/vntana/`

## Output Files

After installation, the following files are created in `web/public/viewers/vntana/`:

```
web/public/viewers/vntana/
├── vntana-viewer.bundle.js      # Main bundled JavaScript (~1MB)
├── vntana-viewer.bundle.js.map  # Source map for debugging
└── vntana-viewer.css            # Viewer styles
```

## Usage in Application

The bundled viewer is loaded dynamically by `VntanaDependencyManager`:

```typescript
// Load the viewer
await VntanaDependencyManager.loadVntana();

// Access the viewer (available on window after loading)
const viewer = window.VntanaViewer;
```

The dependency manager:

1. Loads CSS via `StylesheetManager`
2. Loads JavaScript bundle via script tag injection
3. Accesses viewer from `window.VntanaViewer` (UMD export)
4. Verifies custom elements are registered

## Benefits

### Reduced File Count

-   **Before**: 300+ files copied to public folder
-   **After**: 3 files copied to public folder

### Improved Performance

-   Single HTTP request instead of multiple
-   Minified and optimized bundle
-   Better browser caching

### Consistent Pattern

-   Matches Potree's loading approach
-   Uses same StylesheetManager and script loading utilities
-   Follows VAMS plugin architecture

### No Runtime Dependency

-   Vntana not needed in main `web/package.json`
-   Cleaner dependency tree
-   Smaller node_modules in main project

## Troubleshooting

### Bundle Build Fails

If webpack fails to build:

1. Check that all devDependencies are installed
2. Verify `@vntana/viewer` is installed
3. Check webpack.config.js for syntax errors

### Viewer Not Loading

If the viewer fails to load in the application:

1. Verify bundle files exist in `web/public/viewers/vntana/`
2. Check browser console for script loading errors
3. Verify `window.VntanaViewer` is defined after bundle loads
4. Check that custom elements are registered

### CSS Not Applied

If styles are missing:

1. Verify `vntana-viewer.css` exists in public folder
2. Check that StylesheetManager loaded the CSS
3. Inspect browser DevTools to see if CSS is loaded

## Development

### Modifying the Bundle

To change what's included in the bundle, edit `webpack.config.js`:

```javascript
module.exports = {
    entry: "./node_modules/@vntana/viewer/dist/index.js",
    // ... other config
};
```

### Debugging

Source maps are generated automatically. To debug:

1. Open browser DevTools
2. Find `vntana-viewer.bundle.js` in Sources
3. Source maps will show original source files

### Testing Changes

After modifying the build:

1. Run `node customInstalls/vntana/vntanaInstall.js`
2. Verify files in `web/public/viewers/vntana/`
3. Test in the application

## Version Updates

To update the Vntana viewer version:

1. Update version in `package.json`:

    ```json
    {
        "dependencies": {
            "@vntana/viewer": "^2.3.0"
        }
    }
    ```

2. Rebuild the bundle:

    ```bash
    cd web
    node customInstalls/vntana/vntanaInstall.js
    ```

3. Test the updated viewer in the application

## Technical Details

### UMD Format

The bundle uses UMD (Universal Module Definition) format:

-   Works in browsers (exposes global variable)
-   Compatible with AMD and CommonJS
-   Exposes `window.VntanaViewer`

### Bundle Contents

The bundle includes:

-   `@vntana/viewer` core library
-   `lit` and `lit-element` (web components framework)
-   All transitive dependencies
-   Custom element definitions

### Webpack Configuration

Key webpack settings:

-   **Mode**: Production (minification enabled)
-   **Output**: UMD library format
-   **Optimization**: TerserPlugin for JS, CssMinimizerPlugin for CSS
-   **Source Maps**: Generated for debugging
-   **Performance**: Hints disabled (bundle is intentionally large)

## Future Improvements

Potential enhancements:

1. Add CSS extraction from JS bundle (if needed)
2. Implement code splitting for larger bundles
3. Add bundle size analysis
4. Create separate dev/prod builds
5. Add bundle integrity checks
