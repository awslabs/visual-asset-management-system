/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Add-on lists the backend declares, read from its Python source so an infra test needs no
 * interpreter.
 */

import * as fs from "fs";
import * as path from "path";

export const ADDON_CONSTANTS_PY = path.resolve(
    __dirname,
    "../../../../backend/backend/common/addonConstants.py"
);

/**
 * `ADDON_CONSTRAINT_OBJECT_TYPE_FEATURES` (`common/addonConstants.py`): each add-on permission
 * object type mapped to the feature switch the backend offers it under.
 */
export function addonObjectTypeFeatures(): Record<string, string> {
    const source = fs.readFileSync(ADDON_CONSTANTS_PY, "utf8");
    const start = source.search(/^ADDON_CONSTRAINT_OBJECT_TYPE_FEATURES\b[^=\n]*=\s*\{/m);
    if (start < 0) {
        throw new Error(`ADDON_CONSTRAINT_OBJECT_TYPE_FEATURES not found in ${ADDON_CONSTANTS_PY}`);
    }
    // The dict closes on a line of its own; its entries are at four spaces of indent.
    const body = source.slice(start).split(/^\}/m)[0];
    const entries = [...body.matchAll(/^ {4}["']([A-Za-z]+)["']\s*:\s*["']([A-Z0-9_]+)["']/gm)];
    if (entries.length === 0) {
        throw new Error(`no entry parsed from ADDON_CONSTRAINT_OBJECT_TYPE_FEATURES`);
    }
    return Object.fromEntries(entries.map((m) => [m[1], m[2]]));
}
