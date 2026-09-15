/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");
const { checkViewerEnabled } = require("../utility/checkViewerEnabled");

// Configurations
const viewerId = "gaussian-splat-viewer-babylonjs";
const npmPackageDir = "./customInstalls/babylonjs";
const npmRepoSourceDestDir = "./customInstalls/babylonjs/node_modules";
const bundleSourceDir = "./customInstalls/babylonjs/dist";
const destinationDir = "./public/viewers/babylonjs";

// Function to cleanup previous builds
const previousCleanUp = async () => {
    try {
        await fs.rmSync(npmRepoSourceDestDir, { recursive: true, force: true });
        await fs.rmSync(bundleSourceDir, { recursive: true, force: true });
        await fs.rmSync(destinationDir, { recursive: true, force: true });
        console.log("BabylonJS: Previous build cleanup complete");
    } catch (err) {
        console.error("BabylonJS: Previous build cleanup error:", err);
    }
};

// Function to run NPM install
const npmInstall = async () => {
    try {
        console.log("BabylonJS: Installing dependencies...");
        await execSync("npm install", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("BabylonJS: NPM install complete");
    } catch (err) {
        console.error("BabylonJS: NPM install error:", err);
        throw err;
    }
};

// Function to build the bundle using webpack
const buildBundle = async () => {
    try {
        console.log("BabylonJS: Building bundle with webpack...");
        await execSync("npx webpack", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("BabylonJS: Bundle build complete");
    } catch (err) {
        console.error("BabylonJS: Bundle build error:", err);
        throw err;
    }
};

// Function to copy bundled files to destination directory
const copyBundledFiles = async () => {
    try {
        console.log("BabylonJS: Copying bundled files to destination...");

        // Create destination directory
        await fs.mkdir(destinationDir, { recursive: true });

        // Copy all files from dist directory (includes main bundle and any chunks)
        if (await fs.pathExists(bundleSourceDir)) {
            await fs.copy(bundleSourceDir, destinationDir);
            console.log("BabylonJS: Copied all bundle files from dist directory");
        } else {
            throw new Error("Bundle directory not found: " + bundleSourceDir);
        }

        console.log("BabylonJS: Files copied to destination directory");
        console.log("BabylonJS: Bundle location: " + destinationDir);
    } catch (err) {
        console.error("BabylonJS: File copy error:", err);
        throw err;
    }
};

// Main function
const main = async () => {
    try {
        console.log("=".repeat(60));
        console.log("BabylonJS Installation");
        console.log("=".repeat(60));

        // Always cleanup previous builds first
        await previousCleanUp();

        // Check if viewer is enabled in config
        if (!checkViewerEnabled(viewerId)) {
            console.log(`BabylonJS: Viewer "${viewerId}" is disabled in viewerConfig.json`);
            console.log("BabylonJS: Skipping installation");
            console.log("=".repeat(60));
            return;
        }

        await npmInstall();
        await buildBundle();
        await copyBundledFiles();

        console.log("=".repeat(60));
        console.log("BabylonJS: Installation complete!");
        console.log("=".repeat(60));
    } catch (err) {
        console.error("=".repeat(60));
        console.error("BabylonJS: Installation failed!");
        console.error(err);
        console.error("=".repeat(60));
        process.exit(1);
    }
};

// Run the main function
main();
