/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Amazon Bedrock's cross-Region inference-profile prefixes are listed in one table (#1526).
 *
 * The GenAI pipelines each carried their own short list of prefixes, in the grant that removes the
 * prefix and in the partition check, and every list missed some (`au.`, `jp.`, `in.`): a model id
 * behind a missing prefix was granted as a foundation model that does not exist. Every grant and check
 * now reads `BEDROCK_INFERENCE_PROFILE_PREFIXES` / `BEDROCK_INFERENCE_PROFILE_PREFIX` in
 * `infra/config/config.ts`, which the ConfigBuilder mirrors in `fields/bedrockModelFormats.ts`
 * (pinned by `configBuilderBedrockModelMirror.test.ts`). This scan fails on a new inline list in the
 * CDK app, its tests or the ConfigBuilder: a regex alternation of two or more prefixes, a
 * `startsWith("<prefix>.")` test, or an array of two or more `"<prefix>."` strings.
 */

import * as fs from "fs";
import * as path from "path";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const ROOTS = [
    path.join(REPO_ROOT, "infra", "lib"),
    path.join(REPO_ROOT, "infra", "config"),
    path.join(REPO_ROOT, "infra", "bin"),
    path.join(REPO_ROOT, "infra", "test"),
    path.join(REPO_ROOT, "documentation", "docusaurus-site", "src", "components", "ConfigBuilder"),
];
const SELF = path.resolve(__filename);

const P = "(?:global|us-gov|us|eu|apac|au|jp|in)";
const PATTERNS: [string, RegExp][] = [
    ["a regex alternation of profile prefixes", new RegExp(`\\(\\??:?${P}(?:\\|${P})+\\)`)],
    ["a startsWith() test of a profile prefix", new RegExp(`startsWith\\(\\s*["'\`]${P}\\.["'\`]`)],
    [
        "an array of profile prefix strings",
        new RegExp(`\\[\\s*["'\`]${P}\\.["'\`]\\s*,\\s*["'\`]${P}\\.["'\`]`),
    ],
];

function sources(dir: string): string[] {
    if (!fs.existsSync(dir)) return [];
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            return entry.name === "node_modules" || entry.name === "cdk.out" ? [] : sources(full);
        }
        return /\.(ts|tsx|js)$/.test(entry.name) && !entry.name.endsWith(".d.ts") ? [full] : [];
    });
}

describe("one Bedrock inference-profile prefix table", () => {
    const files = ROOTS.flatMap(sources).filter((file) => path.resolve(file) !== SELF);

    test("[control] the scan reads the grant constructs, config.ts and the ConfigBuilder", () => {
        const names = files.map((file) => path.basename(file));
        for (const expected of [
            "cadStepAgent-construct.ts",
            "videoSopBom-construct.ts",
            "imageTo3d-construct.ts",
            "config.ts",
            "validation.ts",
        ]) {
            expect(names).toContain(expected);
        }
    });

    test("[control] each pattern recognises the lists it is meant to catch", () => {
        expect(PATTERNS[0][1].test("/^(global|us-gov|us|eu|apac)\\./")).toBe(true);
        expect(PATTERNS[1][1].test('id.startsWith("global.")')).toBe(true);
        expect(PATTERNS[2][1].test('["global.", "us."]')).toBe(true);
        expect(PATTERNS.some(([, re]) => re.test('region.startsWith("us-")'))).toBe(false);
    });

    test("no grant, check or test carries its own prefix list", () => {
        const offenders: string[] = [];
        for (const file of files) {
            const lines = fs.readFileSync(file, "utf8").split("\n");
            lines.forEach((line, index) => {
                // A test's case list (`test.each([...])`) chooses inputs; it is not a list a grant or
                // a check reads.
                if (/\.each\(/.test(line)) return;
                for (const [what, re] of PATTERNS) {
                    if (re.test(line)) {
                        offenders.push(`${path.relative(REPO_ROOT, file)}:${index + 1} (${what})`);
                    }
                }
            });
        }
        expect(offenders).toEqual([]);
    });
});
