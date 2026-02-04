# ThreeJS Viewer for VAMS

This directory contains the build configuration for the ThreeJS dynamic viewer plugin for VAMS (Visual Asset Management System).

## Overview

The ThreeJS viewer provides support for viewing 3D models in various formats including:

-   GLTF/GLB (GLTF 2.0)
-   OBJ (Wavefront OBJ)
-   FBX (Autodesk FBX)
-   STL (Stereolithography)
-   PLY (Polygon File Format)
-   COLLADA (.dae)
-   3DS (3D Studio)
-   3MF (3D Manufacturing Format)
-   **CAD Formats** (optional, requires OCCT): STEP (.stp, .step), IGES (.iges), BREP (.brep)

## ⚠️ Browser Requirements for CAD Files

**CAD file formats (STEP, IGES, BREP) require WebAssembly (WASM) with SharedArrayBuffer support.** If you plan to enable OCCT support for CAD files, your deployment must meet specific requirements.

### Required HTTP Headers (CAD Files Only)

For CAD file viewing to work, your web server **MUST** serve the following HTTP headers:

```
Cross-Origin-Embedder-Policy: credentialless
Cross-Origin-Opener-Policy: same-origin
```

These headers enable the `SharedArrayBuffer` API which is required by the OCCT WASM module.

**Note**: These headers are only required if you enable OCCT support. Standard 3D formats (GLTF, OBJ, FBX, etc.) work without these headers.

### Browser Compatibility (CAD Files)

| Browser | CAD Support      | Notes                                    |
| ------- | ---------------- | ---------------------------------------- |
| Chrome  | ✅ Supported     | Recommended for CAD files                |
| Firefox | ✅ Supported     | Recommended for CAD files                |
| Edge    | ✅ Supported     | Recommended for CAD files                |
| Safari  | ❌ Not Supported | Does not support `credentialless` policy |

**Safari Limitation**: Safari does not support the `Cross-Origin-Embedder-Policy: credentialless` value required for CAD file viewing. Users on Safari will see an error message directing them to use Chrome, Firefox, or Edge for CAD files.

### Error Messages (CAD Files)

If a user tries to view a CAD file without proper browser support, they will see:

```
CAD Format Support Not Available

This CAD file format requires WebAssembly with SharedArrayBuffer support,
which is not currently available. This may be due to:

• Missing or incorrect web server headers
• Browser restrictions or unsupported browser version
• Safari browser limitations

Please contact your system administrator to enable WASM support for CAD files.
```

## Installation

The viewer is automatically installed when you run `yarn install` in the web directory, provided it is enabled in the viewer configuration.

## Build Process

1. **Install dependencies**: `npm install` in this directory or `yarn install` in the web directory
2. **Build bundle**: `npx webpack` to create the minified bundle
3. **Output**: Bundle is created at `../../public/viewers/threejs.min.js`

## Enabling OCCT Support (Optional)

By default, OCCT support for CAD file formats (STEP, IGES, BREP) is **disabled** due to LGPL licensing restrictions.

### ⚠️ IMPORTANT: License & Deployment Considerations

**occt-import-js uses the LGPL (Lesser General Public License)**, which is more restrictive than the Apache-2.0 license used by VAMS. Before enabling OCCT support:

1. **Understand LGPL implications**: LGPL requires that modifications to the LGPL library itself must be released under LGPL
2. **Consult your legal team**: Ensure LGPL is acceptable for your deployment
3. **Document the decision**: Keep records of why OCCT support was enabled

**⚠️ DEPLOYMENT REQUIREMENT: Special Web Headers**

OCCT support **requires special headers** and will **not work with if not being set due to any restrictions**. This is because:

-   WASM files require specific HTTP headers ()
-   These headers are configured for CloudFront distributions and/or to run a web service worker script in index.html for ALB deployments
-   Headers Needed: "Cross-Origin-Embedder-Policy: credentialless" and "Cross-Origin-Opener-Policy: same-origin"

**Before enabling OCCT, verify your deployment has these headers set properly during web page load.**

### Steps to Enable OCCT Support

1. **Edit package.json** in this directory:

    ```json
    "optionalDependencies": {
      "occt-import-js": "^0.0.12"
    }
    ```

2. **Reinstall dependencies**:

    ```bash
    cd web/customInstalls/threejs
    npm install
    ```

3. **Rebuild the bundle**:

    ```bash
    npm run build
    ```

4. **Run the install script** (from web directory):

    ```bash
    node customInstalls/threejs/install.js
    ```

5. **Verify**: The bundle will now include OCCT support with local WASM files:
    - OCCT library bundled in `threejs.min.js`
    - WASM file copied to `public/viewers/threejs/occt-import-js.wasm`
    - Supports: .stp, .step (STEP), .iges (IGES), .brep (BREP)

**Security Note**: The WASM file is served locally from your deployment, not fetched from external CDNs.

### Using OCCT in the Viewer

When OCCT is enabled, the viewer will automatically detect and use it for CAD file formats. If OCCT is not enabled and a user tries to view a STEP/IGES/BREP file, they will see an error message:

> "STEP/BREP/IGES file viewing is not available for the viewer until the library is enabled and packaged with the ThreeJS dynamic viewer. See VAMS admin documentation to enable."

## File Structure

```
threejs/
├── package.json          # Dependencies and build scripts
├── webpack.config.js     # Webpack bundling configuration
├── threejsInstall.js     # Main entry point for the bundle
├── install.js            # Installation script (called by yarn postinstall)
└── README.md            # This file
```

## Troubleshooting

### Build Fails

-   Ensure Node.js and npm are installed
-   Try deleting `node_modules` and running `npm install` again
-   Check that webpack is installed: `npm list webpack`

### Bundle Not Found

-   Verify the build completed successfully
-   Check that `../../public/viewers/threejs.min.js` exists
-   Review the install.js output for errors

### OCCT Not Working

-   Verify occt-import-js is installed: `npm list occt-import-js`
-   Check browser console for OCCT-related errors
-   Ensure the bundle was rebuilt after enabling OCCT

## Development

To modify the viewer:

1. Make changes to `threejsInstall.js`
2. Rebuild: `npx webpack`
3. Test in VAMS by viewing a 3D file

## License

-   **ThreeJS**: MIT License
-   **VAMS**: Apache-2.0 License
-   **occt-import-js** (optional): LGPL License ⚠️

## Support

For issues or questions:

-   Check VAMS documentation
-   Review ThreeJS documentation: https://threejs.org/docs/
-   For OCCT issues: https://github.com/kovacsv/occt-import-js
