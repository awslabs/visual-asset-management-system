/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const { execSync } = require("child_process");
const fs = require("fs-extra");
const path = require("path");

// Configurations
const npmPackageDir = "./customInstalls/cesium";
const npmRepoSourceDestDir = "./customInstalls/cesium/node_modules";
const cesiumBuildDir = "./customInstalls/cesium/node_modules/cesium/Build/Cesium";
const cesiumSourceDir = "./customInstalls/cesium/node_modules/cesium/Source";
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

// Function to copy files to destination directory
const copyFiles = async () => {
    try {
        console.log("Cesium: Copying files to destination...");

        // Create destination directory
        await fs.mkdir(destinationDir, { recursive: true });

        // Copy all Source files (for assets, workers, etc.)
        // Exclude Cesium.js to avoid overwriting our bundle
        if (await fs.pathExists(cesiumSourceDir)) {
            await fs.copy(cesiumSourceDir, destinationDir, {
                filter: (src) => {
                    // Exclude the Cesium.js file from Source directory
                    const basename = path.basename(src);
                    return basename !== "Cesium.js";
                },
            });
            console.log(
                "Cesium: Copied Source directory (assets, workers, etc., excluding Cesium.js)"
            );
        } else {
            console.warn("Cesium: Source directory not found");
        }

        // Wrap the CommonJS bundle to work in browser
        if (await fs.pathExists(cesiumBuildDir)) {
            const cesiumJsSource = path.join(cesiumBuildDir, "index.cjs");
            const cesiumJsDest = path.join(destinationDir, "Cesium.js");

            if (await fs.pathExists(cesiumJsSource)) {
                // Read the CommonJS bundle
                const cesiumCode = await fs.readFile(cesiumJsSource, "utf8");

                // Wrap it to work in browser
                const wrappedCode = `(function() {
    var module = { exports: {} };
    var exports = module.exports;
    
${cesiumCode}
    
    window.Cesium = module.exports;
})();`;

                // Write the wrapped bundle
                await fs.writeFile(cesiumJsDest, wrappedCode, "utf8");
                console.log("Cesium: Created browser-compatible Cesium.js bundle from index.cjs");
            } else {
                console.warn("Cesium: index.cjs not found in Build directory");
            }
        } else {
            console.warn("Cesium: Build directory not found");
        }

        console.log("Cesium: Files copied to destination directory");
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

        await previousCleanUp();
        await npmInstall();
        await copyFiles();

        console.log("=".repeat(60));
        console.log("Cesium: Installation complete!");
        console.log("Files:");
        console.log("  - " + path.join(destinationDir, "Cesium.js"));
        console.log("  - " + path.join(destinationDir, "Source assets"));
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
