/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");
const { checkViewerEnabled } = require("../utility/checkViewerEnabled");

// Configurations
const viewerId = "veerum-viewer";
const npmPackageDir = "./customInstalls/veerum";
const npmRepoSourceDestDir = "./customInstalls/veerum/node_modules";
const bundleSourceDir = "./customInstalls/veerum/dist";
const destinationDir = "./public/viewers/veerum";

// Function to check if node_modules exists
const checkNodeModules = async () => {
    const nodeModulesPath = path.join(npmRepoSourceDestDir, "@veerum");
    if (!(await fs.pathExists(nodeModulesPath))) {
        throw new Error(
            "Veerum package not found in node_modules. Please ensure @veerum/viewer is installed."
        );
    }
    console.log("Veerum: Package found in node_modules");
};

// Function to cleanup previous builds
const previousCleanUp = async () => {
    try {
        await fs.rmSync(bundleSourceDir, { recursive: true, force: true });
        await fs.rmSync(destinationDir, { recursive: true, force: true });
        console.log("Veerum: Previous build cleanup complete");
    } catch (err) {
        console.error("Veerum: Previous build cleanup error:", err);
    }
};

// Function to build the bundle using webpack
const buildBundle = async () => {
    try {
        console.log("Veerum: Building bundle with webpack...");
        await execSync("npm run build", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("Veerum: Bundle build complete");
    } catch (err) {
        console.error("Veerum: Bundle build error:", err);
        throw err;
    }
};

// Function to copy bundled files to destination directory
const copyBundledFiles = async () => {
    try {
        console.log("Veerum: Copying bundled files to destination...");

        // Create destination directory
        await fs.mkdir(destinationDir, { recursive: true });

        // Copy all files from the dist directory (includes main bundle and any chunks)
        if (await fs.pathExists(bundleSourceDir)) {
            await fs.copy(bundleSourceDir, destinationDir);
            console.log("Veerum: Copied all bundle files from dist directory");
        } else {
            throw new Error("Bundle directory not found: " + bundleSourceDir);
        }

        // Copy assets and textures from the source package if they exist
        const assetsSource = path.join(npmRepoSourceDestDir, "@veerum/viewer/dist/lib/assets");
        const texturesSource = path.join(npmRepoSourceDestDir, "@veerum/viewer/dist/lib/textures");

        if (await fs.pathExists(assetsSource)) {
            const assetsDest = path.join(destinationDir, "assets");
            await fs.copy(assetsSource, assetsDest);
            console.log("Veerum: Copied assets from source package");
        }

        if (await fs.pathExists(texturesSource)) {
            const texturesDest = path.join(destinationDir, "textures");
            await fs.copy(texturesSource, texturesDest);
            console.log("Veerum: Copied textures from source package");
        }

        console.log("Veerum: Files copied to destination directory");
        console.log("Veerum: Bundle location: " + destinationDir);
    } catch (err) {
        console.error("Veerum: File copy error:", err);
        throw err;
    }
};

// Main function
const main = async () => {
    try {
        console.log("=".repeat(60));
        console.log("Veerum Viewer Bundle Installation");
        console.log("=".repeat(60));

        // Always cleanup previous builds first
        await previousCleanUp();

        // Check if viewer is enabled in config
        if (!checkViewerEnabled(viewerId)) {
            console.log(`Veerum: Viewer "${viewerId}" is disabled in viewerConfig.json`);
            console.log("Veerum: Skipping installation");
            console.log("=".repeat(60));
            return;
        }

        // Check if node_modules exists (package should be pre-installed)
        await checkNodeModules();

        await buildBundle();
        await copyBundledFiles();

        console.log("=".repeat(60));
        console.log("Veerum: Installation complete!");
        console.log("Bundle files:");
        console.log("  - " + path.join(destinationDir, "veerum-viewer.bundle.js"));
        if (await fs.pathExists(path.join(destinationDir, "assets"))) {
            console.log("  - " + path.join(destinationDir, "assets/"));
        }
        if (await fs.pathExists(path.join(destinationDir, "textures"))) {
            console.log("  - " + path.join(destinationDir, "textures/"));
        }
        console.log("=".repeat(60));
    } catch (err) {
        console.error("=".repeat(60));
        console.error("Veerum: Installation failed!");
        console.error(err);
        console.error("=".repeat(60));
        process.exit(1);
    }
};

// Run the main function
main();
