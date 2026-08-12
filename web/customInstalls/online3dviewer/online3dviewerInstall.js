/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");
const { checkViewerEnabled } = require("../utility/checkViewerEnabled");

// Configurations
const viewerId = "online3d-viewer";
const npmPackageDir = "./customInstalls/online3dviewer";
const npmRepoSourceDestDir = "./customInstalls/online3dviewer/node_modules";
const publicDestinationDir = "./public/viewers/online3dviewer";

// Online3DViewer loads four optional format libraries from a third-party CDN at RUNTIME, with each
// URL hard-coded in its shipped bundle (LoadExternalLibrary and the OCCT worker loader in
// source/engine/import/importerutils.js). VAMS must not fetch code from an external site at runtime:
// the CSP has no jsdelivr source, so each fetch is blocked and the importer fails with "Failed to
// load web-ifc." rather than a diagnosable error.
//
// Each library is therefore installed as a pinned dependency, copied next to the bundle, and its URL
// rewritten to that local path. Versions match the URLs the bundle requests exactly, so the local
// copy is the same code the CDN would have served.
const externalLibs = [
    {
        name: "web-ifc",
        cdnUrl: "https://cdn.jsdelivr.net/npm/web-ifc@0.0.68/web-ifc-api-iife.js",
        source: "web-ifc/web-ifc-api-iife.js",
        // The IIFE build loads its own WebAssembly binary from the same directory.
        extraFiles: ["web-ifc/web-ifc.wasm", "web-ifc/web-ifc-mt.wasm"],
    },
    {
        name: "rhino3dm",
        cdnUrl: "https://cdn.jsdelivr.net/npm/rhino3dm@8.17.0/rhino3dm.min.js",
        source: "rhino3dm/rhino3dm.min.js",
        extraFiles: ["rhino3dm/rhino3dm.wasm"],
    },
    {
        name: "occt-import-js",
        // A BASE URL rather than a single file: the bundle fetches the worker script from it, then
        // rewrites two paths inside that script to pull the JS and WASM from the same base. All three
        // files must therefore sit together under one served directory.
        cdnUrl: "https://cdn.jsdelivr.net/npm/occt-import-js@0.0.22/dist/",
        isBaseUrl: true,
        source: "occt-import-js/dist/occt-import-js-worker.js",
        extraFiles: [
            "occt-import-js/dist/occt-import-js.js",
            "occt-import-js/dist/occt-import-js.wasm",
        ],
    },
    {
        name: "draco3d",
        cdnUrl: "https://cdn.jsdelivr.net/npm/draco3d@1.5.7/draco_decoder_nodejs.min.js",
        // The npm package ships the decoder unminified; jsdelivr serves a ".min.js" it derives. The
        // file is functionally the same module, so the local copy keeps the name the bundle asks for.
        source: "draco3d/draco_decoder_nodejs.js",
        destName: "draco_decoder_nodejs.min.js",
        extraFiles: [],
    },
];

// Served path of the vendored libraries, matching where copyFiles() writes them.
const externalLibPublicPath = "/viewers/online3dviewer/externallibs";

// Function to cleanup previous build
const previousCleanUp = async () => {
    try {
        await fs.rmSync(npmRepoSourceDestDir, { recursive: true, force: true });
        await fs.rmSync(publicDestinationDir, { recursive: true, force: true });
        console.log("Online3DViewer: Previous build cleanup complete");
    } catch (err) {
        console.error("Online3DViewer: Previous build cleanup error:", err);
    }
};

// Function to run NPM install
const npmInstall = async () => {
    try {
        console.log("Online3DViewer: Installing dependencies...");
        await execSync("npm install", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("Online3DViewer: NPM install complete");
    } catch (err) {
        console.error("Online3DViewer: NPM install error:", err);
        throw err;
    }
};

// Best-effort: apply safe (non-breaking) npm audit fixes before building/packaging.
// Non-fatal — `npm audit fix` exits non-zero when unfixable vulnerabilities remain
// (those need --force/manual review, which we do NOT apply) and a registry hiccup
// must not break the viewer install.
const auditFix = async () => {
    console.log("Online3DViewer: Running npm audit fix (safe fixes only)...");
    try {
        await execSync("npm audit fix", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("Online3DViewer: npm audit fix complete");
    } catch (err) {
        console.warn(
            "Online3DViewer: npm audit fix reported unresolved/unfixable vulnerabilities (continuing)."
        );
    }
};

// Function to copy files to public directory for dynamic loading
const copyFiles = async () => {
    try {
        console.log("Online3DViewer: Copying files to destination...");

        // Create public destination directory
        await fs.mkdir(publicDestinationDir, { recursive: true });

        // Copy the pre-built minified library file
        const libSource = path.join(
            npmRepoSourceDestDir,
            "online-3d-viewer/build/engine/o3dv.min.js"
        );
        const libDest = path.join(publicDestinationDir, "o3dv.min.js");

        if (await fs.pathExists(libSource)) {
            await fs.copy(libSource, libDest);
            console.log("Online3DViewer: Library file copied to public directory");
        } else {
            throw new Error("Library file not found: " + libSource);
        }

        // Copy website assets (environment maps)
        const assetsSource = path.join(npmRepoSourceDestDir, "online-3d-viewer/website/assets");
        const assetsDest = path.join(publicDestinationDir, "assets");

        if (await fs.pathExists(assetsSource)) {
            await fs.copy(assetsSource, assetsDest);
            console.log("Online3DViewer: Assets copied to public directory");
        } else {
            console.warn("Online3DViewer: Assets directory not found: " + assetsSource);
        }

        await vendorExternalLibs(libDest);

        console.log("Online3DViewer: Files copied to destination directory");
        console.log("Online3DViewer: Bundle location: " + publicDestinationDir);
    } catch (err) {
        console.error("Online3DViewer: File copy error:", err);
        throw err;
    }
};

// Copy the runtime-loaded format libraries next to the bundle and repoint the bundle at them, so no
// external URL is fetched while the viewer is running.
const vendorExternalLibs = async (bundlePath) => {
    const libsDest = path.join(publicDestinationDir, "externallibs");
    await fs.mkdir(libsDest, { recursive: true });

    let bundle = await fs.readFile(bundlePath, "utf8");
    const rewritten = [];

    for (const lib of externalLibs) {
        const source = path.join(npmRepoSourceDestDir, lib.source);
        if (!(await fs.pathExists(source))) {
            // Fail loudly. Silently skipping would ship a bundle still pointing at the CDN, which the
            // CSP blocks — the viewer would fail only when a user opened one of these formats.
            throw new Error(
                `Online3DViewer: ${lib.name} not found at ${source}. It is a pinned dependency of ` +
                    `this installer; the runtime-loaded library cannot be vendored without it.`
            );
        }
        const destName = lib.destName || path.basename(lib.source);
        await fs.copy(source, path.join(libsDest, destName));

        // Companion files (WebAssembly binaries) the library itself fetches relative to its own URL.
        for (const extra of lib.extraFiles) {
            const extraSource = path.join(npmRepoSourceDestDir, extra);
            if (await fs.pathExists(extraSource)) {
                await fs.copy(extraSource, path.join(libsDest, path.basename(extra)));
            } else {
                console.warn(`Online3DViewer: optional companion file missing: ${extraSource}`);
            }
        }

        const localUrl = lib.isBaseUrl
            ? `${externalLibPublicPath}/`
            : `${externalLibPublicPath}/${destName}`;
        if (!bundle.includes(lib.cdnUrl)) {
            // The URL moved or the version changed upstream. Stop rather than ship a bundle that
            // still reaches out at runtime.
            throw new Error(
                `Online3DViewer: expected CDN URL not present in the bundle: ${lib.cdnUrl}. ` +
                    `Online3DViewer changed how it loads ${lib.name}; update externalLibs.`
            );
        }
        bundle = bundle.split(lib.cdnUrl).join(localUrl);
        rewritten.push(`${lib.name} -> ${localUrl}`);
    }

    // Nothing may remain that would be fetched from a third-party host at runtime.
    const remaining = bundle.match(/https:\/\/cdn\.jsdelivr\.net\/[^"')\s]+/g);
    if (remaining) {
        throw new Error(
            "Online3DViewer: external CDN references remain in the bundle: " +
                [...new Set(remaining)].join(", ")
        );
    }

    await fs.writeFile(bundlePath, bundle, "utf8");
    console.log("Online3DViewer: Vendored runtime libraries (no external fetch at run time):");
    for (const line of rewritten) {
        console.log("  - " + line);
    }
};

// Main function
const main = async () => {
    try {
        console.log("=".repeat(60));
        console.log("Online3DViewer Installation");
        console.log("=".repeat(60));

        // Always cleanup previous builds first
        await previousCleanUp();

        // Check if viewer is enabled in config
        if (!checkViewerEnabled(viewerId)) {
            console.log(`Online3DViewer: Viewer "${viewerId}" is disabled in viewerConfig.json`);
            console.log("Online3DViewer: Skipping installation");
            console.log("=".repeat(60));
            return;
        }

        await npmInstall();
        await auditFix();
        await copyFiles();

        console.log("=".repeat(60));
        console.log("Online3DViewer: Installation complete!");
        console.log("Files:");
        console.log("  - " + path.join(publicDestinationDir, "o3dv.min.js"));
        console.log("  - " + path.join(publicDestinationDir, "assets/"));
        console.log("=".repeat(60));
    } catch (err) {
        console.error("=".repeat(60));
        console.error("Online3DViewer: Installation failed!");
        console.error(err);
        console.error("=".repeat(60));
        process.exit(1);
    }
};

// Run the main function
main();
