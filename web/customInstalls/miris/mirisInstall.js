/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");
const { checkViewerEnabled } = require("../utility/checkViewerEnabled");

// Configurations
const viewerId = "miris-stream-viewer";
const npmPackageDir = "./customInstalls/miris";
const npmRepoSourceDestDir = "./customInstalls/miris/node_modules";
const bundleSourceDir = "./customInstalls/miris/dist";
const destinationDir = "./public/viewers/miris";
const bundleEntryFile = "miris-sdk.js";

// The SDK packages whose dist/ holds the WebAssembly the bundle loads at runtime. The
// @miris-inc/components dist carries copies for its prebuilt CDN build, which is not bundled.
const wasmSourceDirs = [
    path.join(npmRepoSourceDestDir, "@miris-inc/core/dist"),
    path.join(npmRepoSourceDestDir, "@miris-inc/three/dist"),
];

// Matches `new URL("<name>.wasm", import.meta.url)` in the emitted chunks.
const WASM_URL_PATTERN = /new URL\(\s*["']([^"'/]+\.wasm)["']\s*,\s*import\.meta\.url\s*\)/g;

// Function to cleanup previous builds
const previousCleanUp = async () => {
    try {
        await fs.rmSync(npmRepoSourceDestDir, { recursive: true, force: true });
        await fs.rmSync(bundleSourceDir, { recursive: true, force: true });
        await fs.rmSync(destinationDir, { recursive: true, force: true });
        console.log("Miris: Previous build cleanup complete");
    } catch (err) {
        console.error("Miris: Previous build cleanup error:", err);
    }
};

// Function to install the pinned SDK and three from package-lock.json
const npmInstall = async () => {
    try {
        console.log("Miris: Installing dependencies...");
        await execSync("npm ci --no-audit --no-fund", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("Miris: NPM install complete");
    } catch (err) {
        console.error("Miris: NPM install error:", err);
        throw err;
    }
};

// Function to build the bundle using webpack
const buildBundle = async () => {
    try {
        console.log("Miris: Building bundle with webpack...");
        await execSync("npm run build", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("Miris: Bundle build complete");
    } catch (err) {
        console.error("Miris: Bundle build error:", err);
        throw err;
    }
};

// Function to copy the bundle and the SDK's WebAssembly to the destination directory. Every chunk
// resolves its .wasm files against its own URL, so they are served from the same directory.
const copyBundledFiles = async () => {
    try {
        console.log("Miris: Copying bundled files to destination...");

        if (!(await fs.pathExists(path.join(bundleSourceDir, bundleEntryFile)))) {
            throw new Error(
                "Bundle file not found: " + path.join(bundleSourceDir, bundleEntryFile)
            );
        }

        await fs.mkdir(destinationDir, { recursive: true });
        await fs.copy(bundleSourceDir, destinationDir);
        console.log("Miris: Copied all bundle files from dist directory");

        for (const sourceDir of wasmSourceDirs) {
            for (const file of await fs.readdir(sourceDir)) {
                if (file.endsWith(".wasm")) {
                    await fs.copy(path.join(sourceDir, file), path.join(destinationDir, file));
                    console.log("Miris: Copied " + file);
                }
            }
        }

        console.log("Miris: Bundle location: " + destinationDir);
    } catch (err) {
        console.error("Miris: File copy error:", err);
        throw err;
    }
};

// Function to confirm every .wasm file a chunk references is present beside it
const verifyWasmFiles = async () => {
    const referenced = new Set();
    for (const file of await fs.readdir(destinationDir)) {
        if (!file.endsWith(".js")) continue;
        const source = await fs.readFile(path.join(destinationDir, file), "utf8");
        for (const match of source.matchAll(WASM_URL_PATTERN)) {
            referenced.add(match[1]);
        }
    }
    if (referenced.size === 0) {
        throw new Error("No WebAssembly references found in the bundle");
    }
    const missing = [...referenced].filter(
        (name) => !fs.existsSync(path.join(destinationDir, name))
    );
    if (missing.length > 0) {
        throw new Error("WebAssembly files missing from the bundle: " + missing.join(", "));
    }
    console.log("Miris: WebAssembly files present: " + [...referenced].sort().join(", "));
};

// Main function
const main = async () => {
    try {
        console.log("=".repeat(60));
        console.log("Miris Spatial Streaming SDK Bundle Installation");
        console.log("=".repeat(60));

        // Always cleanup previous builds first
        await previousCleanUp();

        // Check if viewer is enabled in config
        if (!checkViewerEnabled(viewerId)) {
            console.log(`Miris: Viewer "${viewerId}" is disabled in addonViewerConfig.json`);
            console.log("Miris: Skipping installation");
            console.log("=".repeat(60));
            return;
        }

        await npmInstall();
        await buildBundle();
        await copyBundledFiles();
        await verifyWasmFiles();

        console.log("=".repeat(60));
        console.log("Miris: Installation complete!");
        console.log("Bundle entry: " + path.join(destinationDir, bundleEntryFile));
        console.log("=".repeat(60));
    } catch (err) {
        console.error("=".repeat(60));
        console.error("Miris: Installation failed!");
        console.error(err);
        console.error("=".repeat(60));
        process.exit(1);
    }
};

// Run the main function
main();
