/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");
const { checkViewerEnabled } = require("../utility/checkViewerEnabled");

// Configurations
const viewerId = "cesium-viewer";
const npmPackageDir = "./customInstalls/cesium";
const npmRepoSourceDestDir = "./customInstalls/cesium/node_modules";
const enginePackageDir = "./customInstalls/cesium/node_modules/@cesium/engine";
const destinationDir = "./public/viewers/cesium";

// Function to cleanup previous builds
const previousCleanUp = async () => {
    try {
        await fs.rmSync(npmRepoSourceDestDir, { recursive: true, force: true });
        await fs.rmSync(destinationDir, { recursive: true, force: true });
        console.log("Cesium: Previous build cleanup complete");
    } catch (err) {
        console.error("Cesium: Previous build cleanup error:", err);
    }
};

// Function to run NPM install
const npmInstall = async () => {
    try {
        console.log("Cesium: Installing dependencies...");
        await execSync("npm install", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("Cesium: NPM install complete");
    } catch (err) {
        console.error("Cesium: NPM install error:", err);
        throw err;
    }
};

// Best-effort: apply safe (non-breaking) npm audit fixes before building/packaging.
// Non-fatal — `npm audit fix` exits non-zero when unfixable vulnerabilities remain
// (those need --force/manual review, which we do NOT apply) and a registry hiccup
// must not break the viewer install.
const auditFix = async () => {
    console.log("Cesium: Running npm audit fix (safe fixes only)...");
    try {
        await execSync("npm audit fix", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("Cesium: npm audit fix complete");
    } catch (err) {
        console.warn(
            "Cesium: npm audit fix reported unresolved/unfixable vulnerabilities (continuing)."
        );
    }
};

// Function to bundle @cesium/engine into a browser global (window.Cesium)
const bundleEngine = async () => {
    try {
        console.log("Cesium: Bundling @cesium/engine...");

        await fs.mkdir(destinationDir, { recursive: true });

        // Bundle the engine's ESM entry point into a classic-script IIFE that
        // assigns the module exports to the global `Cesium` (window.Cesium)
        const entryPoint = path.resolve(enginePackageDir, "index.js");
        const outFile = path.resolve(destinationDir, "Cesium.js");
        await execSync(
            `npx esbuild "${entryPoint}" --bundle --format=iife --global-name=Cesium ` +
                `--target=es2020 --charset=utf8 --minify --outfile="${outFile}"`,
            { cwd: npmPackageDir, stdio: "inherit" }
        );

        console.log("Cesium: Created browser-compatible Cesium.js bundle from @cesium/engine");
    } catch (err) {
        console.error("Cesium: Bundle error:", err);
        throw err;
    }
};

// Function to copy static runtime files (workers, wasm, assets, widget CSS)
const copyFiles = async () => {
    try {
        console.log("Cesium: Copying static files to destination...");

        // Web workers (draco decoding, KTX2 transcoding, geometry, etc.)
        await fs.copy(
            path.join(enginePackageDir, "Build/Workers"),
            path.join(destinationDir, "Workers")
        );

        // Third-party workers (zip)
        await fs.copy(
            path.join(enginePackageDir, "Build/ThirdParty"),
            path.join(destinationDir, "ThirdParty")
        );

        // WASM binaries resolved at runtime relative to CESIUM_BASE_URL
        const thirdPartySourceDir = path.join(enginePackageDir, "Source/ThirdParty");
        const wasmFiles = (await fs.readdir(thirdPartySourceDir)).filter((f) =>
            f.endsWith(".wasm")
        );
        for (const wasmFile of wasmFiles) {
            await fs.copy(
                path.join(thirdPartySourceDir, wasmFile),
                path.join(destinationDir, "ThirdParty", wasmFile)
            );
        }

        // Static assets (IAU data, approximate terrain heights, textures)
        await fs.copy(
            path.join(enginePackageDir, "Source/Assets"),
            path.join(destinationDir, "Assets")
        );

        // CesiumWidget stylesheet
        await fs.copy(
            path.join(enginePackageDir, "Source/Widget"),
            path.join(destinationDir, "Widget"),
            { filter: (src) => !src.endsWith(".js") }
        );

        console.log("Cesium: Static files copied to destination directory");
        console.log("Cesium: Bundle location: " + destinationDir);
    } catch (err) {
        console.error("Cesium: File copy error:", err);
        throw err;
    }
};

// Main function
const main = async () => {
    try {
        console.log("=".repeat(60));
        console.log("Cesium Viewer Installation");
        console.log("=".repeat(60));

        // Always cleanup previous builds first
        await previousCleanUp();

        // Check if viewer is enabled in config
        if (!checkViewerEnabled(viewerId)) {
            console.log(`Cesium: Viewer "${viewerId}" is disabled in viewerConfig.json`);
            console.log("Cesium: Skipping installation");
            console.log("=".repeat(60));
            return;
        }

        await npmInstall();
        await auditFix();
        await bundleEngine();
        await copyFiles();

        console.log("=".repeat(60));
        console.log("Cesium: Installation complete!");
        console.log("Files:");
        console.log("  - " + path.join(destinationDir, "Cesium.js"));
        console.log("  - " + path.join(destinationDir, "Workers, ThirdParty, Assets, Widget"));
        console.log("=".repeat(60));
    } catch (err) {
        console.error("=".repeat(60));
        console.error("Cesium: Installation failed!");
        console.error(err);
        console.error("=".repeat(60));
        process.exit(1);
    }
};

// Run the main function
main();
