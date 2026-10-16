/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core aggregator files name no add-on.
 *
 * Every list VAMS aggregates (API routes, CSP sources, default-role routes, feature switches, web
 * routes and navigation, API service calls, viewer configuration and manifest) is composed from a
 * core part and an add-on part kept in a file of its own beside it. The core files are the ones
 * edited on every release and by forks, so an add-on entry placed back in one of them puts add-on
 * code on the path of every core change again.
 *
 * Asserted on the source text, across the backend, CDK and web trees, because the seam is a file
 * boundary rather than a synthesized behaviour.
 */

import * as fs from "fs";
import * as path from "path";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");

/** The core aggregator files and, beside each, the add-on file its add-on entries live in. */
const CORE_AND_ADDON_FILES: Array<{ core: string; addon: string }> = [
    {
        core: "backend/backend/common/apiRoutes.py",
        addon: "backend/backend/common/addonApiRoutes.py",
    },
    {
        core: "infra/lib/core-stack.ts",
        addon: "infra/lib/nestedStacks/addon/addonBuilder-nestedStack.ts",
    },
    { core: "infra/lib/helper/security.ts", addon: "infra/lib/nestedStacks/addon/addonCsp.ts" },
    {
        core: "infra/lib/nestedStacks/auth/constructs/dynamodb-authdefaults-ro-construct.ts",
        addon: "infra/lib/nestedStacks/addon/addonDefaultRoutes.ts",
    },
    {
        core: "web/src/services/APIService.ts",
        addon: "web/src/services/addons/physnaAPIService.ts",
    },
    {
        core: "web/src/visualizerPlugin/config/viewerConfig.json",
        addon: "web/src/visualizerPlugin/config/addonViewerConfig.json",
    },
    {
        core: "web/src/visualizerPlugin/viewers/manifest.ts",
        addon: "web/src/visualizerPlugin/viewers/addonManifest.ts",
    },
];

/** Core aggregators with no add-on counterpart today; an add-on entry still belongs elsewhere. */
const CORE_ONLY_FILES = ["web/src/routes.tsx", "web/src/layout/Navigation.tsx"];

/** The add-ons VAMS ships (`infra/lib/nestedStacks/addon/<name>`). */
const ADDON_NAME = /physna|garnet/i;

const read = (relative: string): string => fs.readFileSync(path.join(REPO_ROOT, relative), "utf8");

describe("core aggregator files name no add-on", () => {
    const coreFiles = [...CORE_AND_ADDON_FILES.map((pair) => pair.core), ...CORE_ONLY_FILES];

    test.each(coreFiles)("%s", (relative) => {
        const offending = read(relative)
            .split("\n")
            .map((line, index) => ({ line: index + 1, text: line }))
            .filter(({ text }) => ADDON_NAME.test(text))
            .map(({ line, text }) => `${relative}:${line}: ${text.trim()}`);
        // Move the entry into the add-on file beside this one.
        expect(offending).toEqual([]);
    });

    test("[control] every add-on file exists and names an add-on", () => {
        // Without this the assertion above passes for a detector that matches nothing.
        for (const { addon } of CORE_AND_ADDON_FILES) {
            expect(ADDON_NAME.test(read(addon))).toBe(true);
        }
    });

    test("[control] every core file exists and is non-trivial", () => {
        for (const relative of coreFiles) {
            expect(read(relative).length).toBeGreaterThan(1000);
        }
    });
});
