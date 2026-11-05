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

const PARITY_FAILURE =
    "The committed infra/config/config.json differs from infra/config/config.template.commercial.json. " +
    "The tracked config.json ships as a copy of the commercial template, so a change to one must be " +
    "made to the other in the same commit. If you committed deployment settings to config.json (for " +
    "example in a fork), keep them in infra/config/config.<name>.json instead (git ignores it by " +
    "default; add it with `git add -f`) and select it with `-c configFile=config.<name>.json`. " +
    "Differing settings:\n";

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

/** The failure message for two parsed configurations, or undefined when every leaf matches. */
function parityFailure(configJsonValue: unknown, templateValue: unknown): string | undefined {
    const actual = leaves(configJsonValue);
    const expected = leaves(templateValue);
    const differences = [
        ...actual.filter((l) => !expected.includes(l)).map((l) => `  config.json:  ${l}`),
        ...expected.filter((l) => !actual.includes(l)).map((l) => `  template:     ${l}`),
    ].sort();
    return differences.length > 0 ? PARITY_FAILURE + differences.join("\n") : undefined;
}

describe("the committed config.json matches the commercial template", () => {
    (skipReason ? test.skip : test)("every setting is the same", () => {
        const failure = parityFailure(
            JSON.parse(configJson as string),
            JSON.parse(template as string)
        );
        if (failure) throw new Error(failure);
    });

    test("a difference fails with the guidance and the differing settings", () => {
        const failure = parityFailure(
            { env: { region: "eu-west-1" } },
            { env: { region: "us-east-1" } }
        );
        expect(failure).toContain("config.<name>.json");
        expect(failure).toContain("-c configFile=");
        expect(failure).toContain('config.json:  env.region = "eu-west-1"');
        expect(failure).toContain('template:     env.region = "us-east-1"');
        expect(parityFailure({ a: 1 }, { a: 1 })).toBeUndefined();
    });

    test("the leaf listing names a differing setting", () => {
        const a = leaves({ app: { pipelines: { x: { bedrockModelId: "m1" } }, list: [] } });
        expect(a).toEqual(['app.pipelines.x.bedrockModelId = "m1"', "app.list = []"]);
    });
});
