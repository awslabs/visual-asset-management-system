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
 * A file name or relative path resolves relative to `infra/config/`; an absolute path is used as given.
 * An absent, null or blank value selects `config.json`.
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

/** The command-line option that names the configuration file for `scripts/assertDeployQuotas.ts`. */
export const CONFIG_FILE_ARG = "--config-file";

/**
 * Value of `--config-file <name>` or `--config-file=<name>` in `args`, or `undefined` when the option is
 * absent. Throws when the option is given without a value, so a mistyped command fails instead of
 * checking `config.json`.
 */
export function configFileFromArgs(args: readonly string[]): string | undefined {
    for (let i = 0; i < args.length; i++) {
        const arg = args[i];
        let value: string | undefined;
        if (arg === CONFIG_FILE_ARG) {
            value = args[i + 1];
        } else if (arg.startsWith(`${CONFIG_FILE_ARG}=`)) {
            value = arg.slice(CONFIG_FILE_ARG.length + 1);
        } else {
            continue;
        }
        if (value === undefined || value.trim() === "" || value.startsWith("--")) {
            throw new Error(
                `${CONFIG_FILE_ARG} needs a file name or path, for example ` +
                    `\`${CONFIG_FILE_ARG} ${DEFAULT_CONFIG_FILE_NAME}\`.`
            );
        }
        return value;
    }
    return undefined;
}
