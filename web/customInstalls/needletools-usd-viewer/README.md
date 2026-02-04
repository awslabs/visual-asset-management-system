# Needle USD Viewer for VAMS

This directory contains the Needle USD viewer plugin for VAMS (Visual Asset Management System), providing support for viewing Universal Scene Description (USD) files.

## Overview

The Needle USD viewer is a WebAssembly-based viewer that provides high-quality rendering of USD files including:

-   **USD Formats**: .usd, .usda (ASCII), .usdc (binary), .usdz (packaged)
-   **Features**: Material editing, transform controls, animation playback, multi-file support
-   **Rendering**: Real-time 3D rendering with PBR materials and lighting

## ⚠️ CRITICAL: WebAssembly Requirements

**The Needle USD viewer requires WebAssembly (WASM) with SharedArrayBuffer support.** This has specific deployment requirements that must be met for the viewer to function.

### Required HTTP Headers

Your web server **MUST** serve the following HTTP headers for the viewer to work:

```
Cross-Origin-Embedder-Policy: credentialless
Cross-Origin-Opener-Policy: same-origin
```

These headers enable the `SharedArrayBuffer` API which is required by the WASM module for threading support.

### Browser Compatibility

| Browser | Support Status | Notes                                          |
| ------- | -------------- | ---------------------------------------------- |
| Chrome  | ✅ Supported   | Recommended browser                            |
| Firefox | ✅ Supported   | Recommended browser                            |
| Edge    | ✅ Supported   | Recommended browser                            |
| Safari  | ⚠️ Limited     | Does not support `credentialless` policy value |

**Safari Limitation**: Safari does not support the `Cross-Origin-Embedder-Policy: credentialless` value. Users on Safari will see an error message directing them to use Chrome, Firefox, or Edge instead.

### Deployment Configuration

The required headers are configured differently depending on your deployment:

#### CloudFront Distributions

Headers are set via CloudFront response headers policies in the CDK infrastructure.

#### Application Load Balancer (ALB)

A service worker script in `index.html` intercepts requests and adds the required headers.

**Before deploying**, verify your infrastructure includes the proper header configuration. Without these headers, users will see an error message instead of the viewer.

### Error Messages

If the required headers are not set or the browser doesn't support SharedArrayBuffer, users will see:

```
WebAssembly (WASM) Support Not Available

The Needle USD viewer requires WebAssembly with SharedArrayBuffer support,
which is not currently available. This may be due to:

• Missing or incorrect web server headers (Cross-Origin-Embedder-Policy and
  Cross-Origin-Opener-Policy)
• Browser restrictions or unsupported browser version
• Safari browser limitations (does not support required 'credentialless' policy)

Please contact your system administrator to enable WASM support or try a
different browser (Chrome, Firefox, or Edge recommended).
```

## Installation

The viewer is automatically installed when you run `yarn install` in the web directory, provided it is enabled in the viewer configuration.

The installation process:

1. Downloads the Needle USD viewer bundle from npm
2. Copies viewer files to `web/public/viewers/needletools_usd_viewer/`
3. Makes the viewer available to VAMS

## File Structure

```
needletools-usd-viewer/
├── usdViewerInstall.js    # Installation script
├── README.md              # This file
└── source/                # Needle USD viewer source files
```

## Features

### USD File Support

-   **Text Format (.usda)**: Human-readable ASCII USD files
-   **Binary Format (.usdc)**: Compressed binary USD files
-   **Packaged Format (.usdz)**: ZIP-archived USD with dependencies
-   **Generic (.usd)**: Auto-detects format

### Dependency Resolution

The viewer automatically:

-   Parses USD files for referenced dependencies (textures, materials, sub-layers)
-   Downloads dependencies on-demand from VAMS storage
-   Handles relative paths (../, ./) correctly
-   Supports deep dependency trees

### Material Library

-   View and edit all materials in the scene
-   Create custom materials
-   Assign materials to objects
-   Reset materials to original state
-   Duplicate and modify materials

### Transform Controls

-   Move, rotate, and scale objects
-   Reset transforms to original state
-   World and local coordinate systems
-   Precise numeric input

### Animation Support

-   Play/pause USD animations
-   Timeline scrubbing
-   Frame-by-frame control

## Troubleshooting

### Viewer Shows Infinite Loading Spinner

**Cause**: SharedArrayBuffer is not available (missing headers or unsupported browser)

**Solution**:

1. Verify HTTP headers are configured correctly in your deployment
2. Check browser console for specific error messages
3. Try a different browser (Chrome, Firefox, or Edge)
4. Contact your system administrator

### Files Load But Textures Are Missing

**Cause**: Dependency files couldn't be downloaded or resolved

**Solution**:

1. Check that all referenced files exist in VAMS storage
2. Verify file paths in USD files are correct
3. Check browser console for 404 errors on specific files

### Safari Browser Issues

**Cause**: Safari doesn't support the required `credentialless` policy

**Solution**: Use Chrome, Firefox, or Edge instead. This is a Safari limitation that cannot be worked around.

### Performance Issues

**Cause**: Large USD files with many dependencies

**Solution**:

1. Optimize USD files by reducing polygon count
2. Use compressed texture formats
3. Limit the number of materials and objects
4. Consider splitting large scenes into multiple files

## Technical Details

### WASM Module

-   **Engine**: Pixar USD (Universal Scene Description)
-   **Renderer**: Three.js with Hydra render delegate
-   **Threading**: Multi-threaded via SharedArrayBuffer
-   **Memory**: Dynamic allocation based on scene complexity

### File Loading

-   **Streaming**: Files downloaded via VAMS streaming API
-   **Caching**: In-memory caching of downloaded dependencies
-   **Parallel**: Up to 5 concurrent dependency downloads

### Security

-   All files served from VAMS storage (no external CDNs)
-   Authorization headers required for all downloads
-   CORS headers properly configured

## Support

For issues or questions:

-   Check VAMS documentation
-   Review Needle USD viewer documentation
-   Contact your system administrator for deployment issues

## License

-   **Needle USD Viewer**: Check vendor license
-   **VAMS**: Apache-2.0 License
