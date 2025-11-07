/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");

// Configurations
const npmPackageDir = "./customInstalls/online3dviewer";
const npmRepoSourceDestDir = "./customInstalls/online3dviewer/node_modules";
const publicDestinationDir = "./public/viewers/online3dviewer";

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

        console.log("Online3DViewer: Files copied to destination directory");
        console.log("Online3DViewer: Bundle location: " + publicDestinationDir);
    } catch (err) {
        console.error("Online3DViewer: File copy error:", err);
        throw err;
    }
};

// Main function
const main = async () => {
    try {
        console.log("=".repeat(60));
        console.log("Online3DViewer Installation");
        console.log("=".repeat(60));

        await previousCleanUp();
        await npmInstall();
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
