/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Tiny get/set-by-path helpers for the nested config object. No dependencies
 * (no Lodash/Immer) — keeps the docs-site bundle lean and avoids the
 * no-barrel-imports concern.
 *
 * Paths are dotted strings. Numeric segments address array indices, e.g.
 * "app.authProvider.authorizerOptions.allowedIpRanges.0.1".
 */

import type { ConfigShape } from "./types";

/** Deep clone via structured JSON round-trip — config is plain JSON data. */
export function cloneConfig<T>(cfg: T): T {
    return JSON.parse(JSON.stringify(cfg));
}

function isIndex(segment: string): boolean {
    return /^\d+$/.test(segment);
}

/** Read a value at a dotted path; returns undefined if any segment is missing. */
export function getByPath(cfg: ConfigShape, path: string): any {
    const segments = path.split(".");
    let cursor: any = cfg;
    for (const segment of segments) {
        if (cursor == null) return undefined;
        cursor = cursor[segment];
    }
    return cursor;
}

/**
 * Return a new config with `value` set at `path`. Only the touched path is
 * cloned (structural sharing for the rest). Creates intermediate
 * objects/arrays as needed based on whether the next segment is numeric.
 */
export function setByPath(cfg: ConfigShape, path: string, value: unknown): ConfigShape {
    const segments = path.split(".");
    const root: any = Array.isArray(cfg) ? [...cfg] : { ...cfg };

    let cursor: any = root;
    for (let i = 0; i < segments.length - 1; i++) {
        const segment = segments[i];
        const nextSegment = segments[i + 1];
        const existing = cursor[segment];

        let copy: any;
        if (Array.isArray(existing)) {
            copy = [...existing];
        } else if (existing != null && typeof existing === "object") {
            copy = { ...existing };
        } else {
            // Build the missing container based on the next segment's shape.
            copy = isIndex(nextSegment) ? [] : {};
        }
        cursor[segment] = copy;
        cursor = copy;
    }

    cursor[segments[segments.length - 1]] = value;
    return root;
}
