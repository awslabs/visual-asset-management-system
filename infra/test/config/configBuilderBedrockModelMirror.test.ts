/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The ConfigBuilder's Bedrock model-id rules agree with the ones `getConfig()` applies.
 *
 * `documentation/docusaurus-site/src/components/ConfigBuilder/fields/bedrockModelFormats.ts` is a
 * hand-ported mirror (the builder runs in the browser and cannot import the CDK sources or the
 * backend's embedding table), and `configBuilderSync.test.ts` covers only `schema.ts` and
 * `defaults.ts`. A drifted mirror tells an operator that a configuration `cdk synth` refuses is valid,
 * or the reverse, so its tables are compared here and its classification is checked against
 * `validateSystemGenAiModelId` over ids in every partition.
 */

import * as Config from "../../config/config";
import * as Builder from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/fields/bedrockModelFormats";

/** What validateSystemGenAiModelId does with an id, in the builder's vocabulary. */
function configOutcome(modelId: string, partition: string): Builder.ModelIdIssue {
    const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    try {
        Config.validateSystemGenAiModelId("field", modelId, partition);
        return warn.mock.calls.length > 0 ? "unverified-prefix" : undefined;
    } catch (error) {
        const message = String((error as Error).message);
        if (message.includes("is an ARN")) return "arn";
        if (message.includes("is not an Amazon Bedrock model id")) return "format";
        if (message.includes("cross-Region inference-profile prefix exists only")) {
            return "foreign-prefix";
        }
        throw error;
    } finally {
        warn.mockRestore();
    }
}

const IDS = [
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
    "apac.anthropic.claude-3-5-sonnet-20240620-v1:0",
    "au.anthropic.claude-haiku-4-5-20251001-v1:0",
    "jp.anthropic.claude-haiku-4-5-20251001-v1:0",
    "in.anthropic.claude-haiku-4-5-20251001-v1:0",
    "anthropic.claude-3-sonnet-20240229-v1:0:200k",
    "amazon.titan-embed-text-v2:0",
    "cohere.embed-multilingual-v3",
    "amazon.jp-model-v1:0",
    "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/a1b2c3",
    "Anthropic.Claude-Haiku:0",
    "claude",
    "anthropic.claude haiku:0",
    `anthropic.${"x".repeat(130)}:0`,
];

const PARTITIONS = ["aws", "aws-us-gov", "aws-eusc"];

describe("ConfigBuilder Bedrock model-id mirror", () => {
    test("[control] the mirror's helpers are real functions", () => {
        expect(typeof Builder.systemGenAiModelIdIssue).toBe("function");
        expect(typeof Builder.embeddingModelFamily).toBe("function");
    });

    test("the profile prefix table, the id pattern and its length match config.ts", () => {
        expect(Builder.BEDROCK_INFERENCE_PROFILE_PREFIXES).toEqual(
            Config.BEDROCK_INFERENCE_PROFILE_PREFIXES
        );
        expect(Builder.BEDROCK_INFERENCE_PROFILE_PREFIX.source).toBe(
            Config.BEDROCK_INFERENCE_PROFILE_PREFIX.source
        );
        expect(Builder.BEDROCK_MODEL_ID_PATTERN.source).toBe(
            Config.BEDROCK_MODEL_ID_PATTERN.source
        );
        expect(Builder.BEDROCK_MODEL_ID_MAX_LENGTH).toBe(Config.BEDROCK_MODEL_ID_MAX_LENGTH);
    });

    test("the maxTokens bounds match config.ts", () => {
        expect([Builder.SYSTEM_GENAI_MIN_MAX_TOKENS, Builder.SYSTEM_GENAI_MAX_MAX_TOKENS]).toEqual([
            Config.SYSTEM_GENAI_MIN_MAX_TOKENS,
            Config.SYSTEM_GENAI_MAX_MAX_TOKENS,
        ]);
    });

    test("the embedding families match the backend's table", () => {
        expect(Builder.EMBEDDING_MODEL_FAMILIES).toEqual(Config.EMBEDDING_MODEL_FAMILIES);
    });

    test.each(PARTITIONS.flatMap((partition) => IDS.map((id) => [partition, id] as const)))(
        "in %s, %s is classified as getConfig() treats it",
        (partition, id) => {
            expect(Builder.systemGenAiModelIdIssue(id, partition)).toBe(
                configOutcome(id, partition)
            );
        }
    );

    test("an unknown partition leaves only the partition-specific outcome undecided", () => {
        expect(
            Builder.systemGenAiModelIdIssue("global.anthropic.x-v1:0", undefined)
        ).toBeUndefined();
        expect(Builder.systemGenAiModelIdIssue("arn:aws:bedrock:x", undefined)).toBe("arn");
        expect(Builder.systemGenAiModelIdIssue("Bad Id", undefined)).toBe("format");
    });
});
