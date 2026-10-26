/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * VAMS-authored webpack wrapper for the robotics viewer build, copied to <lichtblick>/web/ by
 * web/customInstalls/robotics/roboticsViewerInstall.js. Builds the upstream web app with the VAMS
 * entrypoint (web/src-vams/entrypoint.tsx) under the VAMS sub-path, without production source
 * maps, and with fonts emitted as files instead of data: URIs, which the VAMS CSP
 * (font-src 'self') blocks.
 */

import path from "path";
import type { Configuration } from "webpack";

import { mainConfig } from "@lichtblick/suite-web/src/webpackConfigs";

import packageJson from "../package.json";

// MUST match PUBLIC_PATH in roboticsViewerInstall.js and the hosting sub-path.
const PUBLIC_PATH = "/viewers/robotics/";

type Rule = { test?: unknown; type?: string; oneOf?: Rule[]; rules?: Rule[] };

/** Turns every rule that inlines .woff/.woff2 fonts as data: URIs into one that emits files. */
function fontsAsFiles(rules: Rule[] | undefined): void {
    for (const rule of rules ?? []) {
        if (
            rule.test instanceof RegExp &&
            (rule.test.test("font.woff2") || rule.test.test("font.woff")) &&
            rule.type === "asset/inline"
        ) {
            rule.type = "asset/resource";
        }
        fontsAsFiles(rule.oneOf);
        fontsAsFiles(rule.rules);
    }
}

const base = mainConfig({
    outputPath: path.resolve(__dirname, ".webpack-vams"),
    contextPath: path.resolve(__dirname, "src-vams"),
    entrypoint: "./entrypoint.tsx",
    prodSourceMap: false,
    version: packageJson.version,
    publicPath: PUBLIC_PATH,
});

export default (env: unknown, argv: Parameters<typeof base>[1]): Configuration => {
    const config = base(env, argv);
    fontsAsFiles(config.module?.rules as Rule[] | undefined);
    return config;
};
