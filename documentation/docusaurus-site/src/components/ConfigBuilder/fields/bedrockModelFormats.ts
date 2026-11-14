/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Amazon Bedrock model-id rules of the SYSTEM GenAI metadata pipeline, mirroring `infra/config/config.ts`.
 *
 * A hand-ported mirror rather than a shared import: the ConfigBuilder runs in the browser and cannot
 * reach the CDK sources or the backend's embedding model table. `validation.ts` mirrors `getConfig()`'s
 * rules by hand and the drift check (`infra/test/config/configBuilderSync.test.ts`) covers only
 * `schema.ts` and `defaults.ts`, so this file is pinned separately, by
 * `infra/test/config/configBuilderBedrockModelMirror.test.ts`: the prefix table, the id pattern and its
 * length, the embedding families (against `backend/backend/common/vectorsearch/embeddingModels.json`)
 * and the classification of a set of ids against `validateSystemGenAiModelId`.
 *
 * Keep in step with `BEDROCK_INFERENCE_PROFILE_PREFIXES`, `BEDROCK_MODEL_ID_PATTERN`,
 * `BEDROCK_MODEL_ID_MAX_LENGTH`, `validateSystemGenAiModelId` and `EMBEDDING_MODEL_FAMILIES` in
 * `infra/config/config.ts`.
 */

/** Cross-Region inference-profile prefixes and the partitions each is offered in. */
export const BEDROCK_INFERENCE_PROFILE_PREFIXES: Readonly<
    Record<string, { partitions: readonly string[]; warnIn?: readonly string[] }>
> = {
    global: { partitions: ["aws"] },
    us: { partitions: ["aws"], warnIn: ["aws-us-gov"] },
    "us-gov": { partitions: ["aws-us-gov"] },
    eu: { partitions: ["aws"] },
    apac: { partitions: ["aws"] },
    au: { partitions: ["aws"] },
    jp: { partitions: ["aws"] },
    in: { partitions: ["aws"] },
};

export const BEDROCK_INFERENCE_PROFILE_PREFIX = new RegExp(
    `^(${Object.keys(BEDROCK_INFERENCE_PROFILE_PREFIXES).join("|")})\\.`
);

export const BEDROCK_MODEL_ID_PATTERN = /^[a-z0-9-]+\.[a-z0-9][a-z0-9._-]*(:[a-z0-9]+)*$/;
export const BEDROCK_MODEL_ID_MAX_LENGTH = 128;

/** Bounds of `bedrockModels.inference.maxTokens`. */
export const SYSTEM_GENAI_MIN_MAX_TOKENS = 256;
export const SYSTEM_GENAI_MAX_MAX_TOKENS = 8192;

/**
 * Why `getConfig()` would refuse (or warn about) a model id in a partition: `arn` and `format` are
 * refused everywhere, `foreign-prefix` is a profile prefix the partition does not offer, and
 * `unverified-prefix` is the warning case. `undefined` when the id is accepted, or when the partition
 * is unknown and only the partition-specific outcome depends on it.
 */
export type ModelIdIssue = "arn" | "format" | "foreign-prefix" | "unverified-prefix" | undefined;

export function systemGenAiModelIdIssue(
    modelId: string,
    partition: string | undefined
): ModelIdIssue {
    if (modelId.startsWith("arn:")) return "arn";
    if (modelId.length > BEDROCK_MODEL_ID_MAX_LENGTH || !BEDROCK_MODEL_ID_PATTERN.test(modelId)) {
        return "format";
    }
    const prefix = BEDROCK_INFERENCE_PROFILE_PREFIX.exec(modelId)?.[1];
    if (prefix === undefined || partition === undefined) return undefined;
    const offered = BEDROCK_INFERENCE_PROFILE_PREFIXES[prefix];
    if (offered.warnIn?.includes(partition)) return "unverified-prefix";
    return offered.partitions.includes(partition) ? undefined : "foreign-prefix";
}

/** The embedding families the backend's adapter serves (its `embeddingModels.json`), in table order. */
export const EMBEDDING_MODEL_FAMILIES: ReadonlyArray<{
    family: string;
    displayName: string;
    exampleModelId: string;
    modelIdPattern: string;
    dimensions: readonly number[];
    maxInputChars: number;
}> = [
    {
        family: "titan-v2",
        displayName: "Amazon Titan Text Embeddings V2",
        exampleModelId: "amazon.titan-embed-text-v2:0",
        modelIdPattern: "titan-embed-text-v2",
        dimensions: [256, 512, 1024],
        maxInputChars: 30000,
    },
    {
        family: "nova-mme",
        displayName: "Amazon Nova Multimodal Embeddings",
        exampleModelId: "amazon.nova-2-multimodal-embeddings-v1:0",
        modelIdPattern: "nova-2-multimodal-embeddings",
        dimensions: [256, 384, 1024, 3072],
        maxInputChars: 8192,
    },
    {
        family: "cohere-v3",
        displayName: "Cohere Embed v3",
        exampleModelId: "cohere.embed-english-v3",
        modelIdPattern: "^(?=.*-v3)cohere\\.embed-",
        dimensions: [1024],
        maxInputChars: 2048,
    },
];

export function embeddingModelFamily(modelId: string) {
    return EMBEDDING_MODEL_FAMILIES.find((entry) => new RegExp(entry.modelIdPattern).test(modelId));
}

/** One line per family for help and error text: "Amazon Titan Text Embeddings V2 (amazon.titan-embed-text-v2:0): 256, 512 or 1024". */
export function embeddingFamilySummary(): string {
    return EMBEDDING_MODEL_FAMILIES.map((entry) => {
        const widths = entry.dimensions.map(String);
        const list =
            widths.length > 1
                ? `${widths.slice(0, -1).join(", ")} or ${widths[widths.length - 1]}`
                : widths[0];
        return `${entry.displayName} (${entry.exampleModelId}): ${list}`;
    }).join("; ");
}
