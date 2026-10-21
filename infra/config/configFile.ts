/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { isAbsolute, join } from "path";

/** The configuration file read when CDK context `configFile` is not set. */
export const DEFAULT_CONFIG_FILE_NAME = "config.json";

/** The CDK context key that names the configuration file (`-c configFile=<name>`). */
export const CONFIG_FILE_CONTEXT_KEY = "configFile";

/**
 * Path of the deployment configuration file.
 *
 * A file name or relative path resolves in `infra/config/`; an absolute path is used as given. An
 * absent, null or blank value selects `config.json`.
 *
 * Kept free of CDK imports so `scripts/assertDeployQuotas.ts` resolves the same file without loading the
 * CDK app.
 */
export function resolveConfigFilePath(configFile?: unknown): string {
    if (configFile !== undefined && configFile !== null && typeof configFile !== "string") {
        throw new Error(
            `Configuration Error: CDK context \`${CONFIG_FILE_CONTEXT_KEY}\` must be a file name or ` +
                `path, for example \`-c ${CONFIG_FILE_CONTEXT_KEY}=${DEFAULT_CONFIG_FILE_NAME}\`.`
        );
    }
    const name =
        typeof configFile === "string" && configFile.trim() !== ""
            ? configFile.trim()
            : DEFAULT_CONFIG_FILE_NAME;
    return isAbsolute(name) ? name : join(__dirname, name);
}
