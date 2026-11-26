/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const fs = require("fs");
const path = require("path");

/** The directory CDK resolves its configuration file in (infra/config), relative to this file. */
const CONFIG_DIR = path.resolve(__dirname, "../../../infra/config");

/** The configuration file CDK reads when `-c configFile` names none. */
const DEFAULT_CONFIG_PATH = path.join(CONFIG_DIR, "config.json");

/**
 * The configuration file a `VAMS_CONFIG_PATH` value names, resolved as CDK resolves
 * `-c configFile` (`infra/config/configFile.ts`): a file name or relative path under infra/config,
 * an absolute path as given, and config.json when the value is empty.
 * @param {string|undefined} value - The value of VAMS_CONFIG_PATH
 * @returns {string}
 */
function resolveConfigPath(value) {
    const name = typeof value === "string" && value.trim() !== "" ? value.trim() : "config.json";
    // The deployer's own VAMS_CONFIG_PATH, resolved on their machine as CDK resolves -c configFile;
    // the install script never reads request input.
    // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
    return path.isAbsolute(name) ? name : path.join(CONFIG_DIR, name);
}

/**
 * Whether the deployment configuration enables the Miris Spatial Streaming add-on
 * (`app.addons.useMirisStreaming.enabled` is `true`). Reads the file `VAMS_CONFIG_PATH` names, set to
 * the value passed to CDK as `-c configFile`, else infra/config/config.json. A missing or unreadable
 * file counts as the add-on being off.
 * @param {string} [configPath] - The configuration file to read (tests only)
 * @returns {boolean}
 */
function mirisAddonEnabled(configPath = resolveConfigPath(process.env.VAMS_CONFIG_PATH)) {
    try {
        const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
        return config?.app?.addons?.useMirisStreaming?.enabled === true;
    } catch (err) {
        console.warn("Miris: could not read the deployment configuration at %s", configPath);
        return false;
    }
}

module.exports = { mirisAddonEnabled, resolveConfigPath, DEFAULT_CONFIG_PATH };
