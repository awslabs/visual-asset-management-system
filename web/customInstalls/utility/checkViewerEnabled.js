/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const fs = require("fs");
const path = require("path");

/** The viewer configuration directory, relative to this file (web/customInstalls/utility/). */
const CONFIG_DIR = path.join(__dirname, "../../src/visualizerPlugin/config");

/**
 * The configuration files PluginRegistry merges (src/visualizerPlugin/core/viewerSources.ts), in its
 * order: core, then add-on (the optional add-ons' and the licensed viewers), then a fork's. The core
 * file is required; the other two are read when present.
 */
const CONFIG_FILES = [
    { name: "viewerConfig.json", path: path.join(CONFIG_DIR, "viewerConfig.json"), required: true },
    {
        name: "addonViewerConfig.json",
        path: path.join(CONFIG_DIR, "addonViewerConfig.json"),
        required: false,
    },
    {
        name: "customViewerConfig.json",
        path: path.join(CONFIG_DIR, "customViewerConfig.json"),
        required: false,
    },
];

/**
 * Checks if a viewer is enabled in the viewer configuration: viewerConfig.json (core),
 * addonViewerConfig.json (add-on and licensed viewers) and customViewerConfig.json (fork).
 * The first file that declares the id decides, as in the browser, where a custom entry that repeats
 * a shipped id is skipped.
 * @param {string} viewerId - The ID of the viewer to check (e.g., "online3d-viewer", "potree-viewer")
 * @param {Array<{name: string, path: string, required: boolean}>} [configFiles] - The files to read,
 *     in order (tests only; defaults to CONFIG_FILES)
 * @returns {boolean} - Returns true if viewer is enabled, false otherwise (including all error cases)
 */
function checkViewerEnabled(viewerId, configFiles = CONFIG_FILES) {
    try {
        let viewer;
        for (const file of configFiles) {
            // Check if file exists
            if (!fs.existsSync(file.path)) {
                if (file.required) {
                    console.warn("checkViewerEnabled: %s not found at %s", file.name, file.path);
                    return false;
                }
                continue;
            }

            // Read and parse the JSON file
            const config = JSON.parse(fs.readFileSync(file.path, "utf8"));

            // Validate config structure
            if (!config || !Array.isArray(config.viewers)) {
                console.warn(
                    "checkViewerEnabled: Invalid config structure in %s - missing viewers array",
                    file.name
                );
                return false;
            }

            // Find the viewer by ID
            viewer = config.viewers.find((v) => v.id === viewerId);
            if (viewer) {
                break;
            }
        }

        if (!viewer) {
            console.warn('checkViewerEnabled: Viewer with ID "%s" not found in config', viewerId);
            return false;
        }

        // Check if enabled property exists and is true
        if (typeof viewer.enabled !== "boolean") {
            console.warn(
                'checkViewerEnabled: Viewer "%s" missing or invalid "enabled" property',
                viewerId
            );
            return false;
        }

        return viewer.enabled;
    } catch (error) {
        // Catch any errors (JSON parse errors, file read errors, etc.)
        console.warn('checkViewerEnabled: Error checking viewer "%s": %s', viewerId, error.message);
        return false;
    }
}

module.exports = { checkViewerEnabled, CONFIG_FILES };
