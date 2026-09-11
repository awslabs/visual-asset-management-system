/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Serializes the builder's config state into a strict, deploy-ready
 * config.json string.
 *
 * Contract (mirrors how infra/config/config.ts treats values):
 *  - 4-space indentation, matching the templates.
 *  - Never emit the literal string "UNDEFINED" (config.ts treats it as unset).
 *    Any such value is normalized to null defensively.
 *  - Computed env fields (`partition`, `coreStackName`) are never present in
 *    builder state (the templates omit them; CDK derives them at deploy), so
 *    no special handling is needed here.
 *
 * The builder state starts as a clone of a template preset, so with no edits
 * the output is semantically identical to the corresponding template file.
 */

import type { ConfigShape } from "./types";
import { cloneConfig } from "./pathUtils";

/** Recursively replace any "UNDEFINED" string with null. */
function normalize(value: any): any {
    if (value === "UNDEFINED") return null;
    if (Array.isArray(value)) return value.map(normalize);
    if (value != null && typeof value === "object") {
        const out: Record<string, any> = {};
        for (const key of Object.keys(value)) {
            out[key] = normalize(value[key]);
        }
        return out;
    }
    return value;
}

export function toConfigJson(config: ConfigShape): string {
    const cleaned = normalize(cloneConfig(config));
    return JSON.stringify(cleaned, null, 4) + "\n";
}
