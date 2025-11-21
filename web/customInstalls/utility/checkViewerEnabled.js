/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const fs = require("fs");
const path = require("path");

/**
 * Checks if a viewer is enabled in the viewerConfig.json file
 * @param {string} viewerId - The ID of the viewer to check (e.g., "online3d-viewer", "potree-viewer")
 * @returns {boolean} - Returns true if viewer is enabled, false otherwise (including all error cases)
 */
function checkViewerEnabled(viewerId) {
    try {
        // Path to viewerConfig.json relative to the web directory
        const configPath = path.join(
            __dirname,
            "../../src/visualizerPlugin/config/viewerConfig.json"
        );

        // Check if file exists
        if (!fs.existsSync(configPath)) {
            console.warn(`checkViewerEnabled: viewerConfig.json not found at ${configPath}`);
            return false;
        }

        // Read and parse the JSON file
        const configContent = fs.readFileSync(configPath, "utf8");
        const config = JSON.parse(configContent);

        // Validate config structure
        if (!config || !Array.isArray(config.viewers)) {
            console.warn("checkViewerEnabled: Invalid config structure - missing viewers array");
            return false;
        }

        // Find the viewer by ID
        const viewer = config.viewers.find((v) => v.id === viewerId);

        if (!viewer) {
            console.warn(`checkViewerEnabled: Viewer with ID "${viewerId}" not found in config`);
            return false;
        }

        // Check if enabled property exists and is true
        if (typeof viewer.enabled !== "boolean") {
            console.warn(
                `checkViewerEnabled: Viewer "${viewerId}" missing or invalid "enabled" property`
            );
            return false;
        }

        return viewer.enabled;
    } catch (error) {
        // Catch any errors (JSON parse errors, file read errors, etc.)
        console.warn(`checkViewerEnabled: Error checking viewer "${viewerId}":`, error.message);
        return false;
    }
}

module.exports = { checkViewerEnabled };
