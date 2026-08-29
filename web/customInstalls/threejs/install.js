/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");
const { checkViewerEnabled } = require("../utility/checkViewerEnabled");

// Configurations
const viewerId = "threejs-viewer";
const npmPackageDir = "./customInstalls/threejs";
const npmRepoSourceDestDir = "./customInstalls/threejs/node_modules";
const bundleSourceDir = "./customInstalls/threejs/dist";
const destinationDir = "./public/viewers/threejs";

// Function to cleanup previous builds
const previousCleanUp = async () => {
    try {
        await fs.rmSync(npmRepoSourceDestDir, { recursive: true, force: true });
        await fs.rmSync(bundleSourceDir, { recursive: true, force: true });
        await fs.rmSync(destinationDir, { recursive: true, force: true });
        console.log("ThreeJS: Previous build cleanup complete");
    } catch (err) {
        console.error("ThreeJS: Previous build cleanup error:", err);
    }
};

// Function to run NPM install
const npmInstall = async () => {
    try {
        console.log("ThreeJS: Installing dependencies...");
        await execSync("npm install", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("ThreeJS: NPM install complete");
    } catch (err) {
        console.error("ThreeJS: NPM install error:", err);
        throw err;
    }
};

// Function to build the bundle using webpack
const buildBundle = async () => {
    try {
        console.log("ThreeJS: Building bundle with webpack...");
        await execSync("npx webpack", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("ThreeJS: Bundle build complete");
    } catch (err) {
        console.error("ThreeJS: Bundle build error:", err);
        throw err;
    }
};

// Function to copy bundled files to destination directory
const copyBundledFiles = async () => {
    try {
        console.log("ThreeJS: Copying bundled files to destination...");

        // Create destination directory
        await fs.mkdir(destinationDir, { recursive: true });

        // Copy the main bundle file
        const bundleFile = path.join(bundleSourceDir, "threejs.min.js");
        if (await fs.pathExists(bundleFile)) {
            await fs.copy(bundleFile, path.join(destinationDir, "threejs.min.js"));
            console.log("ThreeJS: Copied main bundle file");
        } else {
            throw new Error("Bundle file not found: " + bundleFile);
        }

        // Copy WASM files if they exist (for OCCT support)
        const distFiles = await fs.readdir(bundleSourceDir);
        const wasmFiles = distFiles.filter((file) => file.endsWith(".wasm"));

        if (wasmFiles.length > 0) {
            console.log(
                `ThreeJS: Found ${wasmFiles.length} WASM file(s) - copying for OCCT support`
            );
            for (const wasmFile of wasmFiles) {
                await fs.copy(
                    path.join(bundleSourceDir, wasmFile),
                    path.join(destinationDir, wasmFile)
                );
                console.log(`ThreeJS: Copied ${wasmFile}`);
            }
        } else {
            console.log(
                "ThreeJS: No WASM files found (OCCT not installed - CAD formats will be unavailable)"
            );
        }

        console.log("ThreeJS: Files copied to destination directory");
        console.log("ThreeJS: Bundle location: " + destinationDir);
    } catch (err) {
        console.error("ThreeJS: File copy error:", err);
        throw err;
    }
};

// Main function
const main = async () => {
    try {
        console.log("=".repeat(60));
        console.log("ThreeJS Installation");
        console.log("=".repeat(60));

        // Always cleanup previous builds first
        await previousCleanUp();

        // Check if viewer is enabled in config
        if (!checkViewerEnabled(viewerId)) {
            console.log(`ThreeJS: Viewer "${viewerId}" is disabled in viewerConfig.json`);
            console.log("ThreeJS: Skipping installation");
            console.log("=".repeat(60));
            return;
        }

        await npmInstall();
        await buildBundle();
        await copyBundledFiles();

        console.log("=".repeat(60));
        console.log("ThreeJS: Installation complete!");
        console.log("=".repeat(60));
    } catch (err) {
        console.error("=".repeat(60));
        console.error("ThreeJS: Installation failed!");
        console.error(err);
        console.error("=".repeat(60));
        process.exit(1);
    }
};

// Run the main function
main();
