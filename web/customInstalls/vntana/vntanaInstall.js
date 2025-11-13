/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");

// Configurations
const npmPackageDir = "./customInstalls/vntana";
const npmRepoSourceDestDir = "./customInstalls/vntana/node_modules";
const bundleSourceDir = "./customInstalls/vntana/dist";
const destinationDir = "./public/viewers/vntana";

// Function to cleanup previous builds
const previousCleanUp = async () => {
    try {
        await fs.rmSync(npmRepoSourceDestDir, { recursive: true, force: true });
        await fs.rmSync(bundleSourceDir, { recursive: true, force: true });
        await fs.rmSync(destinationDir, { recursive: true, force: true });
        console.log("Vntana: Previous build cleanup complete");
    } catch (err) {
        console.error("Vntana: Previous build cleanup error:", err);
    }
};

// Function to run NPM install
const npmInstall = async () => {
    try {
        console.log("Vntana: Installing dependencies...");
        await execSync("npm install", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("Vntana: NPM install complete");
    } catch (err) {
        console.error("Vntana: NPM install error:", err);
        throw err;
    }
};

// Function to build the bundle using webpack
const buildBundle = async () => {
    try {
        console.log("Vntana: Building bundle with webpack...");
        await execSync("npm run build", { cwd: npmPackageDir, stdio: "inherit" });
        console.log("Vntana: Bundle build complete");
    } catch (err) {
        console.error("Vntana: Bundle build error:", err);
        throw err;
    }
};

// Function to copy bundled files to destination directory
const copyBundledFiles = async () => {
    try {
        console.log("Vntana: Copying bundled files to destination...");

        // Create destination directory
        await fs.mkdir(destinationDir, { recursive: true });

        // Copy all files from the dist directory (includes main bundle and any chunks)
        if (await fs.pathExists(bundleSourceDir)) {
            await fs.copy(bundleSourceDir, destinationDir);
            console.log("Vntana: Copied all bundle files from dist directory");
        } else {
            throw new Error("Bundle directory not found: " + bundleSourceDir);
        }

        // Copy the CSS file directly from the source package
        const cssSource = path.join(npmRepoSourceDestDir, "@vntana/viewer/styles/viewer.css");
        const cssDest = path.join(destinationDir, "vntana-viewer.css");

        if (await fs.pathExists(cssSource)) {
            await fs.copy(cssSource, cssDest);
            console.log("Vntana: Copied vntana-viewer.css from source package");
        } else {
            console.warn("Vntana: CSS file not found in source package: " + cssSource);
        }

        console.log("Vntana: Files copied to destination directory");
        console.log("Vntana: Bundle location: " + destinationDir);
    } catch (err) {
        console.error("Vntana: File copy error:", err);
        throw err;
    }
};

// Main function
const main = async () => {
    try {
        console.log("=".repeat(60));
        console.log("Vntana Viewer Bundle Installation");
        console.log("=".repeat(60));

        await previousCleanUp();
        await npmInstall();
        await buildBundle();
        await copyBundledFiles();

        console.log("=".repeat(60));
        console.log("Vntana: Installation complete!");
        console.log("Bundle files:");
        console.log("  - " + path.join(destinationDir, "vntana-viewer.bundle.js"));
        console.log("  - " + path.join(destinationDir, "vntana-viewer.css"));
        console.log("=".repeat(60));
    } catch (err) {
        console.error("=".repeat(60));
        console.error("Vntana: Installation failed!");
        console.error(err);
        console.error("=".repeat(60));
        process.exit(1);
    }
};

// Run the main function
main();
