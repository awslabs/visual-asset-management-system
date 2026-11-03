/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The tracked `infra/config/config.json` ships as a copy of `config.template.commercial.json`.
 *
 * A setting added or changed in the template alone left `config.json` deploying a different model id
 * and missing four pipeline settings, so a deployment from the default file differed from the one the
 * documentation describes. Compared as the COMMITTED blobs (see `support/committedFile.ts`): an
 * operator edits the working `config.json` for a real deployment, and that must not fail `npm test`.
 */

import { readCommittedFile } from "../support/committedFile";

const committedConfig = readCommittedFile("infra/config/config.json");
const committedTemplate = readCommittedFile("infra/config/config.template.commercial.json");
const configJson = committedConfig.content;
const template = committedTemplate.content;
const skipReason = committedConfig.skipReason ?? committedTemplate.skipReason;
if (skipReason) console.warn(`config.json parity: ${skipReason}`);

/** Every leaf as `path = value`, so a failure names the setting that differs. */
function leaves(value: unknown, prefix = ""): string[] {
    if (value !== null && typeof value === "object") {
        const entries = Array.isArray(value)
            ? value.map((v, i) => [String(i), v] as const)
            : Object.entries(value as Record<string, unknown>);
        if (entries.length === 0) return [`${prefix} = ${JSON.stringify(value)}`];
        return entries.flatMap(([k, v]) => leaves(v, prefix ? `${prefix}.${k}` : k));
    }
    return [`${prefix} = ${JSON.stringify(value)}`];
}

describe("the committed config.json matches the commercial template", () => {
    (skipReason ? test.skip : test)("every setting is the same", () => {
        const actual = leaves(JSON.parse(configJson as string)).sort();
        const expected = leaves(JSON.parse(template as string)).sort();
        expect(actual).toEqual(expected);
    });

    test("the leaf listing names a differing setting", () => {
        const a = leaves({ app: { pipelines: { x: { bedrockModelId: "m1" } }, list: [] } });
        expect(a).toEqual(['app.pipelines.x.bedrockModelId = "m1"', "app.list = []"]);
    });
});
