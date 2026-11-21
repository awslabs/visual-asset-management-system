/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const fs = require("fs");
const path = require("path");

/** The deployment configuration CDK reads (infra/config/config.json), relative to this file. */
const DEFAULT_CONFIG_PATH = path.resolve(__dirname, "../../../infra/config/config.json");

/**
 * Whether the deployment configuration enables the Miris Spatial Streaming add-on
 * (`app.addons.useMirisStreaming.enabled` is `true`). Reads the file named by `VAMS_CONFIG_PATH`,
 * else infra/config/config.json. A missing or unreadable file counts as the add-on being off.
 * @param {string} [configPath] - The configuration file to read (tests only)
 * @returns {boolean}
 */
function mirisAddonEnabled(configPath = process.env.VAMS_CONFIG_PATH || DEFAULT_CONFIG_PATH) {
    try {
        const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
        return config?.app?.addons?.useMirisStreaming?.enabled === true;
    } catch (err) {
        console.warn("Miris: could not read the deployment configuration at %s", configPath);
        return false;
    }
}

module.exports = { mirisAddonEnabled, DEFAULT_CONFIG_PATH };
