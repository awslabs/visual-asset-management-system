# OCCT Optional Implementation for ThreeJS Viewer

## Overview

OCCT (Open CASCADE Technology) support for CAD file formats (STEP, IGES, BREP) is implemented as an **optional feature** that is **disabled by default** due to LGPL licensing restrictions.

## Security & Architecture

### ✅ Secure Implementation

**No Remote Dependencies:**

-   WASM file is **bundled locally** during build process
-   Served from `/viewers/threejs/occt-import-js.wasm` (local deployment)
-   **No CDN fetching** at runtime
-   No external network dependencies

**Build-Time Bundling:**

1. OCCT library installed via npm (optional)
2. Webpack bundles OCCT into `threejs.min.js`
3. CopyWebpackPlugin copies WASM file to dist
4. Install script copies WASM to `public/viewers/threejs/`
5. Runtime loads WASM from local path

### 🔒 License Compliance

**Default State: DISABLED**

-   OCCT not included by default
-   Apache-2.0 license maintained
-   No LGPL dependencies in default build

**When Enabled:**

-   Administrator must explicitly add to `optionalDependencies`
-   Clear LGPL warning in package.json
-   Documentation emphasizes license implications

### ☁️ Deployment Requirements

**⚠️ CRITICAL: CloudFront Required for OCCT**

OCCT support **only works with CloudFront deployments** and will **fail with ALB-only deployments**.

**Why CloudFront is Required:**

-   WASM files require specific HTTP headers
-   CloudFront distributions are configured with proper WASM headers
-   ALB web deployments do not set WASM-specific headers
-   Without proper headers, browsers will refuse to load WASM modules

**Before Enabling OCCT:**

1. Verify your VAMS deployment uses CloudFront for web content
2. Confirm CloudFront is configured to serve static assets from S3
3. Do NOT enable OCCT if using ALB-only deployment

**Deployment Check:**

-   ✅ CloudFront + S3: OCCT will work
-   ❌ ALB only: OCCT will fail to load WASM

## Implementation Details

### 1. Package Configuration (`package.json`)

```json
{
    "optionalDependencies": {
        // Add this line to enable OCCT:
        // "occt-import-js": "^0.0.12"
    },
    "devDependencies": {
        "copy-webpack-plugin": "^11.0.0" // For copying WASM files
    }
}
```

### 2. Webpack Configuration (`webpack.config.js`)

```javascript
// Detects if OCCT is installed
const occtInstalled = fs.existsSync(path.resolve(__dirname, "node_modules/occt-import-js"));

// Conditionally adds CopyWebpackPlugin
if (occtInstalled) {
    plugins.push(
        new CopyPlugin({
            patterns: [
                {
                    from: "node_modules/occt-import-js/dist/*.wasm",
                    to: "[name][ext]",
                    noErrorOnMissing: true,
                },
            ],
        })
    );
}
```

### 3. Install Script (`install.js`)

```javascript
// Copies WASM files from dist to public
const wasmFiles = distFiles.filter((file) => file.endsWith(".wasm"));

if (wasmFiles.length > 0) {
    // Copy each WASM file to public/viewers/threejs/
    for (const wasmFile of wasmFiles) {
        await fs.copy(path.join(bundleSourceDir, wasmFile), path.join(destinationDir, wasmFile));
    }
}
```

### 4. Bundle Entry Point (`threejsInstall.js`)

```javascript
// Optional: Import OCCT if available
let occtimportjs;
try {
    occtimportjs = require("occt-import-js");
} catch (e) {
    // OCCT not installed - this is expected by default
    console.log("OCCT-import-js not available (optional dependency)");
}

export default {
    // ... other exports
    occtimportjs, // Will be undefined if not installed
};
```

### 5. Runtime Loader (`occtLoader.ts`)

```typescript
// Use local WASM file (not CDN)
const wasmUrl = "/viewers/threejs/occt-import-js.wasm";

// Initialize with local WASM
const occt = await occtimportjs({
    locateFile: (name: string) => wasmUrl,
});
```

### 6. File Loader (`fileLoaders.ts`)

```typescript
// Check for OCCT availability
const bundle = (window as any).THREEBundle;
if (!bundle || !bundle.occtimportjs) {
    throw new Error(
        `CAD format support not enabled. The ${extension.toUpperCase()} format requires the OCCT library. ` +
            `Please contact your administrator to enable CAD format support for this viewer.`
    );
}
```

## Runtime Behavior

### With OCCT Installed

1. Webpack bundles OCCT library
2. WASM file copied to `public/viewers/threejs/`
3. `THREEBundle.occtimportjs` available at runtime
4. CAD files load normally
5. WASM loaded from local path

### Without OCCT Installed (Default)

1. Webpack skips OCCT bundling
2. No WASM files copied
3. `THREEBundle.occtimportjs` is undefined
4. CAD files show user-friendly error
5. No network requests for WASM

## Error Messages

**User-Facing Error (when OCCT not installed):**

```
CAD format support not enabled. The [FORMAT] format requires the OCCT library.
Please contact your administrator to enable CAD format support for this viewer.
```

**Console Logs:**

-   Build time: "OCCT not installed - CAD format support will be unavailable"
-   Install time: "ThreeJS: No WASM files found (OCCT not installed - CAD formats will be unavailable)"

## Verification Steps

### Check if OCCT is Enabled

1. **During build:**

    ```
    OCCT detected - will copy WASM files to bundle
    ```

2. **During install:**

    ```
    ThreeJS: Found 1 WASM file(s) - copying for OCCT support
    ThreeJS: Copied occt-import-js.wasm
    ```

3. **In browser console:**

    ```javascript
    window.THREEBundle.occtimportjs !== undefined;
    ```

4. **Check file exists:**
    ```
    public/viewers/threejs/occt-import-js.wasm
    ```

## Testing

### Test Without OCCT

1. Ensure `optionalDependencies` is empty in package.json
2. Build and install
3. Try to load .stp file
4. Should see error message (not console error)

### Test With OCCT

1. Add `occt-import-js` to `optionalDependencies`
2. Run `yarn install` and `npm run build` in web directory
3. Run install script
4. Verify WASM file exists in public/viewers/threejs/
5. Try to load .stp file
6. Should load successfully

## Maintenance

**To disable OCCT:**

1. Remove from `optionalDependencies`
2. Run `yarn install` in web directory
3. Run `npm run build` in web directory
4. Run install script
5. WASM file will not be copied

**To update OCCT version:**

1. Update version in `optionalDependencies`
2. Run `yarn install` in web directory
3. Run `npm run build` in web directory
4. Run install script
