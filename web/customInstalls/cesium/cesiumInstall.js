/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const fs = require("fs-extra");

// Configurations
const sourceDir = "./node_modules/cesium/Source"; //Relative to base web directory where yarn/npm is run
const destinationDir = "./public/cesium"; //Relative to base web directory where yarn/npm is run

// Function to cleanup previous cesium build
const previousCleanUp = async () => {
    try {
        await fs.rmSync(destinationDir, { recursive: true, force: true });
        console.log("Cesium Previous Build Cleanup Complete");
    } catch (err) {
        console.error("Cesium Previous Build Cleanup error:", err);
    }
};

// Function to copy cesium source files to destination directory
const copyFiles = async () => {
    try {
        // Check if source directory exists
        if (!(await fs.pathExists(sourceDir))) {
            console.log("Cesium source directory not found, skipping cesium install");
            return;
        }

        // Create cesium directory and copy files
        await fs.mkdir(destinationDir, { recursive: true });
        await fs.copy(sourceDir, destinationDir);

        console.log("Cesium Build Files copied to destination directory");
    } catch (err) {
        console.error("Cesium Build File copy error:", err);
    }
};

// Main function
const main = async () => {
    await previousCleanUp();
    await copyFiles();
};

// Run the main function
main();
