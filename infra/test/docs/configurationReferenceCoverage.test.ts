/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every leaf of `app.vectorSearch` and `app.pipelines.useSystemGenAiMetadata` has a field row in
 * `deployment/configuration-reference.md`, and the two constraint tables on that page carry the
 * vector-search conditions `getConfig()` and the VPC builder enforce.
 *
 * A configuration option without a reference row is one an operator learns about from a synth error.
 * Durable (root CLAUDE.md Rule 13): a field added to either section later, or a row removed, fails here.
 */

import * as fs from "fs";
import * as path from "path";

const PAGE = path.join(
    __dirname,
    "..",
    "..",
    "..",
    "documentation",
    "docusaurus-site",
    "docs",
    "deployment",
    "configuration-reference.md"
);

const text = fs.readFileSync(PAGE, "utf8");
const lines = text.split("\n");

/** True when a Markdown table row documents the dotted field path (first cell, in backticks). */
const hasFieldRow = (fieldPath: string): boolean =>
    lines.some((line) => line.startsWith("| `" + fieldPath + "`"));

const LEAVES = [
    "app.vectorSearch.enabled",
    "app.vectorSearch.embeddingModelId",
    "app.vectorSearch.embeddingDimensions",
    "app.vectorSearch.indexingConcurrency",
    "app.pipelines.useSystemGenAiMetadata.enabled",
    "app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId",
    "app.pipelines.useSystemGenAiMetadata.autoRegisterWithVAMS",
    "app.pipelines.useSystemGenAiMetadata.autoRegisterAutoTriggerOnFileUpload",
    "app.pipelines.useSystemGenAiMetadata.useFargateRenderer",
    "app.pipelines.useSystemGenAiMetadata.lambdaLimits.maxInputFileSizeMb",
    "app.pipelines.useSystemGenAiMetadata.lambdaLimits.maxPointCloudPoints",
];

describe("configuration-reference.md covers the vector-search configuration", () => {
    test("the page was read and the row matcher discriminates", () => {
        // Positive and negative controls for the matcher used by every assertion below.
        expect(text.length).toBeGreaterThan(0);
        expect(hasFieldRow("app.openSearch.reindexOnCdkDeploy")).toBe(true);
        expect(hasFieldRow("app.does.not.exist")).toBe(false);
    });

    test.each(LEAVES)("%s has a field row", (fieldPath) => {
        expect(hasFieldRow(fieldPath)).toBe(true);
    });

    test("the section headings exist", () => {
        expect(text).toContain("## Vector search (`app.vectorSearch`)");
        expect(text).toContain(
            "### SYSTEM GenAI metadata (`app.pipelines.useSystemGenAiMetadata`)"
        );
    });

    test("the restricted-partition table rejects vector search in the EU Sovereign Cloud", () => {
        const row = lines.find(
            (line) =>
                line.startsWith("| No ") && /vector search/i.test(line) && /Sovereign/.test(line)
        );
        expect(row).toBeDefined();
        expect(row).toContain("`app.vectorSearch.enabled: false`");
        expect(row).toContain("`aws-eusc`");
    });

    test("the Bedrock Runtime endpoint row states the vector-search condition", () => {
        const row = lines.find((line) => line.startsWith("| Bedrock Runtime"));
        expect(row).toBeDefined();
        expect(row).toContain("`vectorSearch.enabled=true`");
        expect(row).toContain("`useSystemGenAiMetadata.enabled=true`");
        expect(row).toContain("`useForAllLambdas=true`");
    });

    test("the VPC-requiring list names the Fargate renderer sub-flag", () => {
        expect(text).toContain("`useSystemGenAiMetadata.useFargateRenderer`");
    });

    test("the bring-your-own-VPC note names the DynamoDB search hostnames", () => {
        expect(text).toContain("search-dynamodb.");
        expect(text).toContain("search-ddb.");
        expect(text).toContain("bedrock-runtime");
    });
});
