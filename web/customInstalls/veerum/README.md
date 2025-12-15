# Veerum Viewer Bundle Installation

## Overview

This directory contains the build system for creating a bundled version of the Veerum viewer that can be dynamically loaded from the public folder. The Veerum viewer provides advanced 3D visualization capabilities for point clouds and 3D tilesets.

## Purchase Veerum License

VEERUM viewer and package is a paid license that can be purchased at [veerum.com](https://veerum.com).

## Architecture

### Bundled Approach

- Veerum viewer is installed only in this custom install directory
- Webpack bundles the viewer and all dependencies (Three.js, lodash, rxjs, etc.) into a single UMD file
- React and ReactDOM are externalized to use the host application's versions
- Bundle files are copied to public folder for dynamic loading
- Dependency manager loads files via script tags and provides React 17 compatibility polyfills
- No runtime dependency on node_modules

## Prerequisites

### 1. Retrieve Credentials

Receive credentials from VEERUM after purchasing license.

### 2. Setup AWS Credentials

Configure your AWS credentials by running:

```bash
aws configure
```

Follow the prompts to add your AWS Access Key and Secret Access Key. Use the defaults for the remaining options.

### 3. Add VEERUM Repository

Add the VEERUM repository to your npm registry:

```bash
aws codeartifact login --tool npm --repository veerum-viewer --domain veerum-viewer --domain-owner {VEERUM_DOMAIN_OWNER_ID} --region us-west-2 --namespace "@veerum"
```

Replace `{VEERUM_DOMAIN_OWNER_ID}` with the domain owner ID provided by VEERUM.

### 4. Install the Package

Install the @veerum/viewer package in this directory:

```bash
cd web/customInstalls/veerum
npm install
```

## Installation Process

### Enable Viewer in VAMS

Enable the VEERUM viewer for VAMS web install:

1. Open `web/src/visualizerPlugin/config/viewerConfig.json`
2. Find the `veerum-viewer` entry
3. Set `"enabled": true`

### Automatic Installation

The installation happens automatically during `yarn install` in the main web directory via the postinstall script.

### Manual Installation

To manually rebuild the bundle:

```bash
cd web
node customInstalls/veerum/veerumInstall.js
```

Or from the project root:

```bash
node web/customInstalls/veerum/veerumInstall.js
```

## Files

### Build Configuration

- **`webpack.config.js`**: Webpack configuration for bundling
  - Creates UMD bundle exposing `window.VeerumViewerModule`
  - Externalizes React and ReactDOM to use host app versions
  - Bundles all other dependencies (Three.js, lodash, rxjs, etc.)
  - Minifies JavaScript with Terser
  - Generates source maps for debugging
  - Copies assets and textures from the Veerum package

- **`package.json`**: NPM package configuration
  - Dependencies: `@veerum/viewer` (the viewer package)
  - DevDependencies: webpack, babel, and related plugins
  - Build script: `npm run build` (runs webpack)

- **`veerumInstall.js`**: Installation script
  - Cleans previous builds
  - Checks if viewer is enabled in viewerConfig.json
  - Verifies @veerum/viewer is installed in node_modules
  - Builds bundle with webpack
  - Copies bundled files, assets, and textures to `web/public/viewers/veerum/`

### Build Steps

1. **Cleanup**: Removes previous builds from dist and public directories
2. **Check Enabled**: Verifies viewer is enabled in viewerConfig.json (skips if disabled)
3. **Verify Package**: Checks that @veerum/viewer exists in node_modules
4. **Bundle**: Runs webpack to create the bundled files
5. **Copy**: Copies bundle files, assets, and textures to `web/public/viewers/veerum/`

## Output Files

After installation, the following files are created in `web/public/viewers/veerum/`:

```
web/public/viewers/veerum/
├── veerum-viewer.bundle.js          # Main bundled JavaScript (~1.76MB)
├── veerum-viewer.bundle.js.map      # Source map for debugging
├── assets/                          # Worker files and other assets
│   └── PointsExtractionWorker-*.js.map
└── textures/                        # Skybox textures
    └── skybox2/
        ├── nx.jpg, px.jpg, py.jpg, pz.jpg, ny.jpg, nz.jpg
        ├── nx_rot.jpg, px_rot.jpg, py_rot.jpg
        └── README.TXT
```

## Usage in Application

The bundled viewer is loaded dynamically by `VeerumDependencyManager`:

```typescript
// Load the viewer
await VeerumDependencyManager.loadVeerum();

// Access the viewer module (available on window after loading)
const veerumModule = window.VeerumViewerModule;

// Use viewer components
const { VeerumViewer, PointCloudModel, TileModel } = veerumModule;
```

The dependency manager:

1. Dynamically imports React and ReactDOM from the host application
2. Exposes them globally on window object
3. Creates React 18 API polyfills for React 17 compatibility:
   - `ReactDOM.createRoot` polyfill
   - `React['jsx-runtime']` polyfill
4. Loads JavaScript bundle via script tag injection
5. Accesses viewer from `window.VeerumViewerModule` (UMD export)

## Supported File Types

The Veerum viewer supports:

- **Point Clouds**: .e57, .las, .laz
  - Uses `PointCloudModel` class
  - Requires Potree preprocessing (auxiliary preview files)
  - Loads from: `auxiliaryPreviewAssets/stream/{fileKey}/preview/PotreeViewer/metadata.json`

- **3D Tilesets**: .json
  - Uses `TileModel` class
  - Streams directly from asset files
  - Loads from: `download/stream/{encodedFileKey}`

- **Multi-File Support**: Can load multiple files of mixed types in a single viewer session

## React Compatibility

The Veerum viewer is built with React 18, but VAMS uses React 17. The dependency manager provides compatibility through:

### React 18 API Polyfills

```typescript
// createRoot polyfill (React 18 → React 17)
ReactDOM.createRoot = function(container) {
    return {
        render: (element) => ReactDOM.render(element, container),
        unmount: () => ReactDOM.unmountComponentAtNode(container)
    };
};

// jsx-runtime polyfill
React['jsx-runtime'] = {
    jsx: React.createElement,
    jsxs: React.createElement,
    Fragment: React.Fragment
};
```

### Webpack Externals

The webpack configuration externalizes React dependencies:

```javascript
externals: {
    react: "React",
    "react-dom": "ReactDOM",
    "react-dom/client": {
        root: ["ReactDOM", "client"]
    },
    "react/jsx-runtime": {
        root: ["React", "jsx-runtime"]
    }
}
```

This ensures the Veerum bundle uses the host application's React instead of bundling its own copy.

## Benefits

### Reduced File Count

- Only 4 main files plus assets/textures copied to public folder
- Much smaller footprint than copying entire node_modules

### Improved Performance

- Single HTTP request for main bundle
- Minified and optimized bundle (~1.76MB)
- Better browser caching
- Source maps for debugging

### Consistent Pattern

- Matches other viewer loading approaches
- Uses same script loading utilities
- Follows VAMS plugin architecture

### No Runtime Dependency

- Veerum not needed in main `web/package.json`
- Cleaner dependency tree
- Smaller node_modules in main project

## Troubleshooting

### Bundle Build Fails

If webpack fails to build:

1. Check that all devDependencies are installed: `npm install`
2. Verify `@veerum/viewer` is installed in node_modules
3. Check webpack.config.js for syntax errors
4. Ensure you have proper AWS CodeArtifact credentials configured

### Viewer Not Loading

If the viewer fails to load in the application:

1. Verify bundle files exist in `web/public/viewers/veerum/`
2. Check browser console for script loading errors
3. Verify `window.VeerumViewerModule` is defined after bundle loads
4. Check that React and ReactDOM are available globally
5. Verify viewer is enabled in `viewerConfig.json`

### React Version Errors

If you see React-related errors:

1. Check that createRoot polyfill is being created
2. Verify jsx-runtime is exposed on React object
3. Check browser console for "[veerum-viewer]" log messages
4. Ensure React 17 is installed in the main web application

### Point Cloud Loading Fails

If point clouds don't load:

1. Verify the point cloud has been preprocessed with the Potree pipeline
2. Check that auxiliary preview files exist at: `{assetKey}/preview/PotreeViewer/metadata.json`
3. Verify authorization headers are being sent correctly
4. Check browser network tab for 401/403 errors

### Tileset Loading Fails

If 3D tilesets don't load:

1. Verify the .json file is a valid 3D tileset definition
2. Check that the tileset URL is properly encoded
3. Verify authorization headers are being sent
4. Check browser console for CORS or network errors

## Development

### Modifying the Bundle

To change what's included in the bundle, edit `webpack.config.js`:

```javascript
module.exports = {
    entry: "./node_modules/@veerum/viewer/dist/lib/index.js",
    externals: {
        react: "React",
        "react-dom": "ReactDOM",
        // ... other externals
    },
    // ... other config
};
```

### Debugging

Source maps are generated automatically. To debug:

1. Open browser DevTools
2. Find `veerum-viewer.bundle.js` in Sources
3. Source maps will show original source files from @veerum/viewer

### Testing Changes

After modifying the build:

1. Run `node customInstalls/veerum/veerumInstall.js` from web directory
2. Verify files in `web/public/viewers/veerum/`
3. Check bundle size (should be ~1.76MB)
4. Test in the application with both point cloud and tileset files

## Version Updates

To update the Veerum viewer version:

1. Update version in `package.json`:

   ```json
   {
       "dependencies": {
           "@veerum/viewer": "^2.0.13"
       }
   }
   ```

2. Reinstall the package:

   ```bash
   cd web/customInstalls/veerum
   npm install
   ```

3. Rebuild the bundle:

   ```bash
   cd web
   node customInstalls/veerum/veerumInstall.js
   ```

4. Test the updated viewer in the application

## Technical Details

### UMD Format

The bundle uses UMD (Universal Module Definition) format:

- Works in browsers (exposes global variable)
- Compatible with AMD and CommonJS
- Exposes `window.VeerumViewerModule`

### Bundle Contents

The bundle includes:

- `@veerum/viewer` core library
- Three.js (3D rendering engine)
- lodash (utility functions)
- rxjs (reactive programming)
- 3d-tiles-renderer (tileset support)
- @tweenjs/tween.js (animations)
- bowser (browser detection)
- file-saver (file downloads)
- popmotion (animations)
- tinycolor2 (color utilities)
- uuid (unique identifiers)
- All transitive dependencies

### Externalized Dependencies

These are NOT bundled (provided by host app):

- React (^17.0.2 in host app, ^18.3.1 in Veerum)
- ReactDOM (^17.0.2 in host app, ^18.3.1 in Veerum)
- react-dom/client (polyfilled for React 17)
- react/jsx-runtime (polyfilled for React 17)

### Webpack Configuration

Key webpack settings:

- **Mode**: Production (minification enabled)
- **Entry**: `./node_modules/@veerum/viewer/dist/lib/index.js`
- **Output**: UMD library format as `VeerumViewerModule`
- **Externals**: React and ReactDOM (with subpath externals)
- **Optimization**: TerserPlugin for minification
- **Source Maps**: Generated for debugging
- **Performance**: Hints disabled (bundle is intentionally large)
- **Babel**: Transpiles ES modules for broader compatibility

### Model Classes

The Veerum viewer provides two main model classes:

#### PointCloudModel

For point cloud files (.e57, .las, .laz, .ply):

```typescript
new PointCloudModel(
    id: string,           // Unique identifier
    url: string,          // URL to metadata.json
    headers?: Headers     // Authorization headers
)
```

#### TileModel

For 3D tileset files (.json):

```typescript
new TileModel(
    id: string,           // Unique identifier
    url: string,          // URL to tileset.json
    type?: string,        // '3DTILES'
    headers?: Headers     // Authorization headers
)
```

## Integration with VAMS

### Viewer Component

Located at: `web/src/visualizerPlugin/viewers/VeerumViewerPlugin/VeerumViewerComponent.tsx`

Features:
- Multi-file support (can load multiple point clouds and/or tilesets)
- Automatic file type detection based on extension
- Proper URL construction for each file type
- JWT authorization via Headers object
- React 17 compatibility via polyfills
- Error handling and loading states

### Dependency Manager

Located at: `web/src/visualizerPlugin/viewers/VeerumViewerPlugin/dependencies.ts`

Responsibilities:
- Load React and ReactDOM from host application
- Create React 18 API polyfills for React 17
- Load Veerum bundle from public folder
- Expose VeerumViewerModule on window object
- Manage cleanup and state

### Configuration

Located at: `web/src/visualizerPlugin/config/viewerConfig.json`

```json
{
    "id": "veerum-viewer",
    "name": "VEERUM 3D Viewer",
    "supportedExtensions": [".e57", ".las", ".laz", ".ply", ".json"],
    "supportsMultiFile": true,
    "priority": 2,
    "enabled": true
}
```

## VAMS Web Install / Build

After completing the prerequisites and enabling the viewer:

1. Run `yarn install` in the VAMS `/web` directory
2. The postinstall script will automatically run `veerumInstall.js`
3. Continue with subsequent build commands

## Future Improvements

Potential enhancements:

1. Add support for additional 3D formats (GLB, GLTF)
2. Implement advanced camera controls and measurement tools
3. Add point cloud styling and filtering options
4. Create separate dev/prod builds with different optimization levels
5. Add bundle integrity checks
6. Implement progressive loading for large datasets
7. Add support for custom shaders and materials

## Support

For issues related to:
- **Veerum viewer functionality**: Contact VEERUM support
- **VAMS integration**: Check VAMS documentation or create an issue
- **Bundle build issues**: Review webpack.config.js and ensure all dependencies are installed
- **React compatibility**: Check browser console for polyfill-related messages

## License

The Veerum viewer is a commercial product. See your VEERUM license agreement for terms and conditions.

VAMS integration code is licensed under Apache-2.0. See the LICENSE file in the project root.
