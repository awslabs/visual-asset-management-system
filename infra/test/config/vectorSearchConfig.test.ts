/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `getConfig()` backfill for `app.vectorSearch` and `app.pipelines.useSystemGenAiMetadata`, and the
 * vector index name derived from the embedding model.
 *
 * Backfill runs before validation, so a config.json written before these sections existed resolves to a
 * deployable shape without turning anything on by itself: the pipeline block defaults to disabled, and
 * `vectorSearch.enabled` follows that pipeline outside the restricted partitions. Every case asserts the
 * resolved VALUE, because "does not throw" would pass while a default was wrong.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** Builds a config.json from `base`, applies `mutate`, and returns a thunk that calls getConfig(). */
function resolve(base: unknown, mutate: (c: any) => void): () => Config.Config {
    const config = JSON.parse(JSON.stringify(base));
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    if (config.app.useAlb?.enabled) {
        config.app.useAlb.domainHost = "vams.example.com";
        config.app.useAlb.certificateArn =
            "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    }
    mutate(config);
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

const commercial = (mutate: (c: any) => void = () => undefined) =>
    resolve(commercialTemplate, (c) => {
        c.env.region = "us-east-1";
        mutate(c);
    });

const govcloud = (mutate: (c: any) => void = () => undefined) =>
    resolve(govcloudTemplate, (c) => {
        // The REGION, not the partition field: getConfig() derives the partition from the region.
        c.env.region = "us-gov-west-1";
        mutate(c);
    });

let warn: jest.SpyInstance;
beforeEach(() => {
    warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
});
afterEach(() => {
    warn.mockRestore();
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

const warnings = () => warn.mock.calls.map((call) => String(call[0])).join("\n");

const SPEC_PIPELINE_DEFAULTS = {
    enabled: false,
    bedrockAnalysisModelId: "",
    autoRegisterWithVAMS: true,
    autoRegisterAutoTriggerOnFileUpload: true,
    useFargateRenderer: false,
    lambdaLimits: { maxInputFileSizeMb: 2048, maxPointCloudPoints: 20000000 },
    bedrockGuardrail: {
        guardrailIdentifier: "",
        guardrailVersion: "",
        create: { enabled: true, promptAttackInputStrength: "LOW", piiFilter: "anonymize" },
    },
};

describe("the vector index name", () => {
    test("slugModelId lowercases and collapses every non-alphanumeric run to one hyphen", () => {
        expect(Config.slugModelId("amazon.titan-embed-text-v2:0")).toBe(
            "amazon-titan-embed-text-v2-0"
        );
        expect(Config.slugModelId("Global.Anthropic.Claude-Haiku-4-5:0")).toBe(
            "global-anthropic-claude-haiku-4-5-0"
        );
        expect(Config.slugModelId("::abc::")).toBe("abc");
    });

    test("deriveVectorIndexName is vec-<slug>-<dims>", () => {
        expect(Config.deriveVectorIndexName("amazon.titan-embed-text-v2:0", 1024)).toBe(
            "vec-amazon-titan-embed-text-v2-0-1024"
        );
        expect(Config.deriveVectorIndexName("cohere.embed-v4:0", 1536)).toBe(
            "vec-cohere-embed-v4-0-1536"
        );
    });

    test("the default model's index name is the cross-language parity anchor", () => {
        // The backend pins the same literals in Python: `slug_model_id("amazon.titan-embed-text-v2:0")`
        // is "amazon-titan-embed-text-v2-0" (test_embeddings.py) and the vector-store contract tests
        // use INDEX = "vec-amazon-titan-embed-text-v2-0-1024". The two slug implementations are separate
        // code, so this literal is what keeps the index the CDK creates and the one the backend
        // queries the same name; change it on one side only and one of the two suites fails.
        const PARITY_ANCHOR = "vec-amazon-titan-embed-text-v2-0-1024";
        expect(Config.slugModelId("amazon.titan-embed-text-v2:0")).toBe(
            "amazon-titan-embed-text-v2-0"
        );
        expect(`vec-${Config.slugModelId("amazon.titan-embed-text-v2:0")}-1024`).toBe(
            PARITY_ANCHOR
        );
        expect(
            Config.deriveVectorIndexName(
                Config.VECTOR_SEARCH_DEFAULT_EMBEDDING_MODEL_ID,
                Config.VECTOR_SEARCH_DEFAULT_EMBEDDING_DIMENSIONS
            )
        ).toBe(PARITY_ANCHOR);
    });

    test("getConfig() derives Config.vectorIndexName from the shipped commercial values", () => {
        const config = commercial()();
        expect(config.vectorIndexName).toBe("vec-amazon-titan-embed-text-v2-0-1024");
    });
});

describe("app.vectorSearch backfill", () => {
    test("the shipped commercial template resolves to enabled with the Titan V2 defaults", () => {
        const config = commercial()();
        expect(config.app.vectorSearch).toEqual({
            enabled: true,
            embeddingModelId: Config.VECTOR_SEARCH_DEFAULT_EMBEDDING_MODEL_ID,
            embeddingDimensions: Config.VECTOR_SEARCH_DEFAULT_EMBEDDING_DIMENSIONS,
            indexingConcurrency: Config.VECTOR_SEARCH_DEFAULT_INDEXING_CONCURRENCY,
        });
    });

    test("an absent block follows the enabled pipeline in the commercial partition", () => {
        const config = commercial((c) => {
            delete c.app.vectorSearch;
        })();
        expect(config.app.vectorSearch).toEqual({
            enabled: true,
            embeddingModelId: "amazon.titan-embed-text-v2:0",
            embeddingDimensions: 1024,
            indexingConcurrency: 5,
        });
        expect(warnings()).not.toContain("app.vectorSearch is not set");
    });

    test("an absent block resolves to disabled when the pipeline is disabled, and says how to enable", () => {
        const config = commercial((c) => {
            delete c.app.vectorSearch;
            c.app.pipelines.useSystemGenAiMetadata.enabled = false;
        })();
        expect(config.app.vectorSearch.enabled).toBe(false);
        expect(warnings()).toContain("Configuration Warning: app.vectorSearch is not set");
        expect(warnings()).toContain("app.pipelines.useSystemGenAiMetadata");
        expect(warnings()).toContain('"embeddingModelId": "amazon.titan-embed-text-v2:0"');
    });

    test("an absent block resolves to disabled under the restricted-partition flag, without the warning", () => {
        const config = govcloud((c) => {
            delete c.app.vectorSearch;
            c.app.pipelines.useSystemGenAiMetadata.enabled = true;
        })();
        expect(config.app.vectorSearch.enabled).toBe(false);
        expect(warnings()).not.toContain("app.vectorSearch is not set");
    });

    test("a null enabled (the ConfigBuilder serializer's unset) is backfilled like an absent one", () => {
        const config = commercial((c) => {
            c.app.vectorSearch.enabled = null;
        })();
        expect(config.app.vectorSearch.enabled).toBe(true);
    });

    test("a present block keeps its values and fills only the missing fields", () => {
        const config = commercial((c) => {
            c.app.vectorSearch = { enabled: true, embeddingDimensions: 512 };
        })();
        expect(config.app.vectorSearch).toEqual({
            enabled: true,
            embeddingModelId: "amazon.titan-embed-text-v2:0",
            embeddingDimensions: 512,
            indexingConcurrency: 5,
        });
        expect(config.vectorIndexName).toBe("vec-amazon-titan-embed-text-v2-0-512");
    });
});

describe("app.pipelines.useSystemGenAiMetadata backfill", () => {
    test("an absent block is filled with the disabled defaults", () => {
        const config = commercial((c) => {
            delete c.app.pipelines.useSystemGenAiMetadata;
            // vectorSearch requires the pipeline once validation lands; keep the case about backfill.
            c.app.vectorSearch.enabled = false;
        })();
        expect(config.app.pipelines.useSystemGenAiMetadata).toEqual(SPEC_PIPELINE_DEFAULTS);
    });

    test("a present block with a partial lambdaLimits gets only the missing sub-key filled", () => {
        const config = commercial((c) => {
            c.app.pipelines.useSystemGenAiMetadata.lambdaLimits = { maxInputFileSizeMb: 512 };
        })();
        expect(config.app.pipelines.useSystemGenAiMetadata.lambdaLimits).toEqual({
            maxInputFileSizeMb: 512,
            maxPointCloudPoints: 20000000,
        });
    });

    test("a present block with no lambdaLimits gets both defaults", () => {
        const config = commercial((c) => {
            delete c.app.pipelines.useSystemGenAiMetadata.lambdaLimits;
        })();
        expect(config.app.pipelines.useSystemGenAiMetadata.lambdaLimits).toEqual(
            SPEC_PIPELINE_DEFAULTS.lambdaLimits
        );
    });

    test("a present block missing the register flags is registered with the trigger disarmed", () => {
        // The same fallback every trigger-bearing pipeline block gets from defaultAutoRegisterFlags.
        const config = commercial((c) => {
            delete c.app.pipelines.useSystemGenAiMetadata.autoRegisterWithVAMS;
            delete c.app.pipelines.useSystemGenAiMetadata.autoRegisterAutoTriggerOnFileUpload;
            c.app.vectorSearch.enabled = false;
        })();
        expect(config.app.pipelines.useSystemGenAiMetadata.autoRegisterWithVAMS).toBe(true);
        expect(
            config.app.pipelines.useSystemGenAiMetadata.autoRegisterAutoTriggerOnFileUpload
        ).toBe(false);
    });

    test("an armed trigger on an unregistered pipeline is warned about like the other pipelines", () => {
        commercial((c) => {
            c.app.pipelines.useSystemGenAiMetadata.autoRegisterWithVAMS = false;
            c.app.vectorSearch.enabled = false;
        })();
        expect(warnings()).toContain(
            "pipelines.useSystemGenAiMetadata.autoRegisterAutoTriggerOnFileUpload is true but"
        );
    });
});
