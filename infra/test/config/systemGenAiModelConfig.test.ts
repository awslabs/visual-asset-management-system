/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The model settings of the SYSTEM GenAI metadata pipeline (`pipelines.useSystemGenAiMetadata.bedrockModels`):
 * the text model, the image flag, the inference settings, the id format and partition-prefix rules
 * every model id goes through, and the embedding family table.
 *
 * Every model id is both the `modelId` of a request and the resource of a function's
 * `bedrock:InvokeModel` grant, so an id VAMS cannot grant (an ARN, a malformed id, a prefix the
 * partition does not offer) is refused at synthesis rather than failing at the first invocation.
 * Each rejection asserts getConfig()'s MESSAGE and that the ConfigBuilder flags the same configuration
 * (`evaluateRules`), because the builder mirrors these rules by hand and a drift there tells an
 * operator a failing configuration is valid.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const MODELS = "app.pipelines.useSystemGenAiMetadata.bedrockModels";

/** The commercial template with the test identity, then `mutate`. */
function build(mutate: (c: any) => void): any {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    mutate(config);
    return config;
}

/** A thunk that runs getConfig() over `config` as config.json. */
function resolveConfig(config: any): () => Config.Config {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

const resolve = (mutate: (c: any) => void) => resolveConfig(build(mutate));

/** The ConfigBuilder rules of this area that a configuration trips, by severity. */
function builder(mutate: (c: any) => void): { errors: string[]; warnings: string[] } {
    const rules = evaluateRules(build(mutate)).filter(
        (rule) =>
            rule.id.startsWith("system-genai-") || rule.id.startsWith("vector-search-embedding")
    );
    return {
        errors: rules.filter((r) => r.severity === "error").map((r) => r.id),
        warnings: rules.filter((r) => r.severity === "warning").map((r) => r.id),
    };
}

/** Everything a GovCloud-partition config needs to reach the model rules (they run after these). */
function govCloud(c: any) {
    c.env.region = "us-gov-west-1";
    c.app.govCloud.enabled = true;
    c.app.useGlobalVpc.enabled = true;
    c.app.useCloudFront.enabled = false;
    c.app.useAlb.enabled = true;
    c.app.useAlb.domainHost = "vams.example.com";
    c.app.useAlb.certificateArn =
        "arn:aws-us-gov:acm:us-gov-west-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    c.app.useLocationService.enabled = false;
    c.app.openSearch.useServerless.enabled = false;
    c.app.openSearch.useServerless.nextGen = false;
    c.app.openSearch.useProvisioned.enabled = true;
    c.app.vectorSearch.enabled = false;
}

function euSovereign(c: any) {
    govCloud(c);
    c.env.region = "eusc-de-east-1";
    c.app.useAlb.certificateArn =
        "arn:aws-eusc:acm:eusc-de-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
}

const vectorSearchOff = (c: any) => {
    c.app.vectorSearch.enabled = false;
};

const models = (c: any) => c.app.pipelines.useSystemGenAiMetadata.bedrockModels;

let warn: jest.SpyInstance;
beforeEach(() => {
    warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
});
afterEach(() => {
    warn.mockRestore();
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});
const warnings = () => warn.mock.calls.map((call) => String(call[0])).join("\n");

describe("backfill of the model settings", () => {
    test("a block without the new fields gets the values today's requests carry", () => {
        const config = resolve((c) => {
            delete models(c).textModelId;
            delete models(c).analysisModelSupportsImages;
            delete models(c).inference;
        })();
        const resolved = config.app.pipelines.useSystemGenAiMetadata.bedrockModels;
        expect(resolved.textModelId).toBe("");
        expect(resolved.analysisModelSupportsImages).toBe(true);
        expect(resolved.inference).toEqual({ maxTokens: 2048, temperature: 0.2 });
        expect([
            Config.SYSTEM_GENAI_DEFAULT_MAX_TOKENS,
            Config.SYSTEM_GENAI_DEFAULT_TEMPERATURE,
        ]).toEqual([2048, 0.2]);
    });

    test("a partial inference block gets only the missing key", () => {
        const config = resolve((c) => {
            models(c).inference = { maxTokens: 4096 };
        })();
        expect(config.app.pipelines.useSystemGenAiMetadata.bedrockModels.inference).toEqual({
            maxTokens: 4096,
            temperature: 0.2,
        });
    });

    test("a null temperature is kept: it means send no temperature", () => {
        const config = resolve((c) => {
            models(c).inference.temperature = null;
        })();
        expect(
            config.app.pipelines.useSystemGenAiMetadata.bedrockModels.inference.temperature
        ).toBeNull();
    });

    test("a null text model (the ConfigBuilder's cleared field) resolves to empty", () => {
        const config = resolve((c) => {
            models(c).textModelId = null;
        })();
        expect(config.app.pipelines.useSystemGenAiMetadata.bedrockModels.textModelId).toBe("");
        expect(builder((c) => (models(c).textModelId = null)).errors).toEqual([]);
    });
});

describe("the model id format, for every model id", () => {
    test.each([
        ["analysisModelId", "analysis"],
        ["textModelId", "text"],
        ["embeddingModelId", "embedding"],
    ])("an ARN in %s is refused with the reason", (field, name) => {
        const mutate = (c: any) => {
            models(c)[field] =
                "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/a1b2c3d4e5f6";
        };
        expect(resolve(mutate)).toThrow(
            new RegExp(
                `bedrockModels\\.${field} is an ARN .*each function is granted the model it names`
            )
        );
        expect(builder(mutate).errors).toContain(`system-genai-${name}-model-arn`);
    });

    test.each([
        ["upper case", "Anthropic.Claude-Haiku-4-5:0"],
        ["no provider segment", "claude-haiku-4-5"],
        ["a space", "anthropic.claude haiku:0"],
        ["a slash", "anthropic/claude-haiku-4-5:0"],
        ["leading whitespace", " anthropic.claude-haiku-4-5:0"],
        ["over the length limit", `anthropic.${"x".repeat(130)}:0`],
    ])("an analysis model id with %s is refused", (_case, id) => {
        const mutate = (c: any) => {
            vectorSearchOff(c);
            models(c).analysisModelId = id;
        };
        expect(resolve(mutate)).toThrow(
            /bedrockModels\.analysisModelId is .*, which is not an Amazon Bedrock model id/
        );
        expect(builder(mutate).errors).toContain("system-genai-analysis-model-format");
    });

    test.each([
        "anthropic.claude-3-sonnet-20240229-v1:0:200k",
        "meta.llama3-2-90b-instruct-v1:0",
        "openai.gpt-oss-120b-1:0",
        "amazon.nova-2-lite-v1:0",
        "mistral.pixtral-large-2502-v1:0",
    ])("the real model id %s is accepted", (id) => {
        const mutate = (c: any) => {
            vectorSearchOff(c);
            models(c).analysisModelId = id;
        };
        expect(resolve(mutate)).not.toThrow();
        expect(builder(mutate).errors).toEqual([]);
    });

    test("the embedding model id is checked while vector search is off, since it is still granted", () => {
        const mutate = (c: any) => {
            vectorSearchOff(c);
            models(c).embeddingModelId = "arn:aws:bedrock:us-east-1::foundation-model/x";
        };
        expect(resolve(mutate)).toThrow(/bedrockModels\.embeddingModelId is an ARN/);
        expect(builder(mutate).errors).toContain("system-genai-embedding-model-arn");
    });

    test("no model id is checked while the pipeline is disabled", () => {
        const mutate = (c: any) => {
            vectorSearchOff(c);
            c.app.pipelines.useSystemGenAiMetadata.enabled = false;
            models(c).analysisModelId = "arn:aws:bedrock:us-east-1:1:x";
            models(c).textModelId = "Not An Id";
            models(c).embeddingModelId = "arn:aws:bedrock:us-east-1:1:y";
        };
        expect(resolve(mutate)).not.toThrow();
        expect(builder(mutate).errors).toEqual([]);
    });
});

describe("cross-Region inference-profile prefixes", () => {
    test.each(["eu.", "apac.", "au.", "jp.", "in."])(
        "a %s profile is accepted in the commercial partition",
        (prefix) => {
            const mutate = (c: any) => {
                vectorSearchOff(c);
                models(c).analysisModelId = `${prefix}anthropic.claude-haiku-4-5-20251001-v1:0`;
            };
            expect(resolve(mutate)).not.toThrow();
            expect(builder(mutate).errors).toEqual([]);
        }
    );

    test.each([
        ["GovCloud", govCloud, "eu."],
        ["GovCloud", govCloud, "au."],
        ["GovCloud", govCloud, "jp."],
        ["GovCloud", govCloud, "in."],
        ["GovCloud", govCloud, "apac."],
        ["the EU Sovereign Cloud", euSovereign, "eu."],
        ["the EU Sovereign Cloud", euSovereign, "global."],
    ])("in %s a %s profile is refused, naming the prefix", (_name, partition, prefix) => {
        const mutate = (c: any) => {
            partition(c);
            models(c).analysisModelId = `${prefix}anthropic.claude-haiku-4-5-20251001-v1:0`;
        };
        expect(resolve(mutate)).toThrow(
            new RegExp(
                `"${prefix.replace(
                    ".",
                    "\\."
                )}" cross-Region inference-profile prefix exists only in the commercial partition`
            )
        );
        expect(builder(mutate).errors).toContain("system-genai-analysis-model-prefix-partition");
    });

    test("the text model goes through the same prefix rules", () => {
        const refused = (c: any) => {
            govCloud(c);
            models(c).analysisModelId = "us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0";
            models(c).textModelId = "global.amazon.nova-2-lite-v1:0";
        };
        expect(resolve(refused)).toThrow(
            /bedrockModels\.textModelId is "global\.amazon\.nova-2-lite-v1:0", whose "global\." cross-Region inference-profile prefix exists only in the commercial partition/
        );
        expect(builder(refused).errors).toContain("system-genai-text-model-prefix-partition");

        const warned = (c: any) => {
            govCloud(c);
            models(c).analysisModelId = "us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0";
            models(c).textModelId = "us.amazon.nova-2-lite-v1:0";
        };
        expect(resolveConfig(build(warned))).not.toThrow();
        expect(warnings()).toContain(
            'bedrockModels.textModelId is "us.amazon.nova-2-lite-v1:0", whose "us." cross-Region'
        );
        expect(warnings()).toContain(
            "verify that this inference profile exists in your GovCloud account"
        );
        expect(builder(warned).warnings).toContain("system-genai-text-model-prefix-unverified");
        expect(builder(warned).errors).toEqual([]);
    });

    test("a profile id's prefix is matched only at the start", () => {
        // "jp" inside the model name is not a prefix: the id is an in-Region model id everywhere.
        const mutate = (c: any) => {
            govCloud(c);
            models(c).analysisModelId = "amazon.jp-model-v1:0";
        };
        expect(resolve(mutate)).not.toThrow();
    });
});

describe("textModelId", () => {
    test("empty is the shipped value and means the analysis model answers (control)", () => {
        expect(
            commercialTemplate.app.pipelines.useSystemGenAiMetadata.bedrockModels.textModelId
        ).toBe("");
        expect(resolve(() => undefined)).not.toThrow();
    });

    test("a model id is accepted", () => {
        const mutate = (c: any) => {
            models(c).textModelId = "amazon.nova-2-lite-v1:0";
        };
        expect(resolve(mutate)).not.toThrow();
        expect(builder(mutate).errors).toEqual([]);
    });

    test("is not checked while the GenAI layer is off", () => {
        const mutate = (c: any) => {
            c.app.pipelines.useSystemGenAiMetadata.useGenAiAnalysis = false;
            models(c).analysisModelId = "";
            models(c).textModelId = "arn:aws:bedrock:us-east-1:1:x";
        };
        expect(resolve(mutate)).not.toThrow();
        expect(builder(mutate).errors).toEqual([]);
    });

    test("a value that is not a string is refused", () => {
        const mutate = (c: any) => {
            models(c).textModelId = 7;
        };
        expect(resolve(mutate)).toThrow(/bedrockModels\.textModelId must be a model id string/);
        expect(builder(mutate).errors).toContain("system-genai-text-model-type");
    });

    test("an Anthropic text model gets the use-case form warning, naming the field", () => {
        resolve((c) => {
            models(c).analysisModelId = "amazon.nova-2-lite-v1:0";
            models(c).textModelId = "global.anthropic.claude-haiku-4-5-20251001-v1:0";
        })();
        expect(warnings()).toContain(
            "pipelines.useSystemGenAiMetadata.bedrockModels.textModelId names an Anthropic model"
        );
        expect(
            builder((c) => {
                models(c).analysisModelId = "amazon.nova-2-lite-v1:0";
                models(c).textModelId = "global.anthropic.claude-haiku-4-5-20251001-v1:0";
            }).warnings
        ).toContain("system-genai-anthropic-use-case-form");
    });
});

describe("inference settings", () => {
    const maxTokensMessage =
        /bedrockModels\.inference\.maxTokens must be an integer between 256 and 8192/;
    const temperatureMessage =
        /bedrockModels\.inference\.temperature must be a number between 0 and 1, or null to send no temperature/;

    test.each([[255], [8193], [1.5], ["2048"], [""]])("maxTokens %p is refused", (value) => {
        const mutate = (c: any) => {
            models(c).inference.maxTokens = value;
        };
        expect(resolve(mutate)).toThrow(maxTokensMessage);
        expect(builder(mutate).errors).toContain("system-genai-inference-max-tokens");
    });

    test.each([[256], [8192]])("maxTokens %p (a bound) is accepted", (value) => {
        const mutate = (c: any) => {
            models(c).inference.maxTokens = value;
        };
        expect(resolve(mutate)).not.toThrow();
        expect(builder(mutate).errors).toEqual([]);
    });

    test.each([[-0.1], [1.1], ["0.2"], [""]])("temperature %p is refused", (value) => {
        const mutate = (c: any) => {
            models(c).inference.temperature = value;
        };
        expect(resolve(mutate)).toThrow(temperatureMessage);
        expect(builder(mutate).errors).toContain("system-genai-inference-temperature");
    });

    test.each([[0], [1], [null]])("temperature %p is accepted", (value) => {
        const mutate = (c: any) => {
            models(c).inference.temperature = value;
        };
        expect(resolve(mutate)).not.toThrow();
        expect(builder(mutate).errors).toEqual([]);
    });

    test("are checked with the GenAI layer off too, and not while the pipeline is disabled", () => {
        expect(
            resolve((c) => {
                c.app.pipelines.useSystemGenAiMetadata.useGenAiAnalysis = false;
                models(c).inference.maxTokens = 0;
            })
        ).toThrow(maxTokensMessage);
        const disabled = (c: any) => {
            vectorSearchOff(c);
            c.app.pipelines.useSystemGenAiMetadata.enabled = false;
            models(c).inference = { maxTokens: 0, temperature: 5 };
            models(c).analysisModelSupportsImages = "no";
        };
        expect(resolve(disabled)).not.toThrow();
        expect(builder(disabled).errors).toEqual([]);
    });

    test("analysisModelSupportsImages must be a boolean", () => {
        const mutate = (c: any) => {
            models(c).analysisModelSupportsImages = "false";
        };
        expect(resolve(mutate)).toThrow(
            /bedrockModels\.analysisModelSupportsImages must be true or false/
        );
        expect(builder(mutate).errors).toContain("system-genai-analysis-model-supports-images");
        const off = (c: any) => {
            models(c).analysisModelSupportsImages = false;
        };
        expect(resolve(off)).not.toThrow();
        expect(builder(off).errors).toEqual([]);
    });
});

describe("the embedding family and width, with vector search on", () => {
    test("a model outside the families is refused, listing them", () => {
        const mutate = (c: any) => {
            models(c).embeddingModelId = "cohere.embed-v4:0";
        };
        expect(resolve(mutate)).toThrow(
            /embeddingModelId is "cohere\.embed-v4:0", which is not a model of a supported embedding family: Amazon Titan Text Embeddings V2 \(for example amazon\.titan-embed-text-v2:0\), Amazon Nova Multimodal Embeddings/
        );
        expect(builder(mutate).errors).toContain("vector-search-embedding-model-family");
    });

    test.each([
        ["amazon.titan-embed-text-v2:0", 3072, "256, 512, 1024"],
        ["amazon.titan-embed-text-v2:0", 1, "256, 512, 1024"],
        ["cohere.embed-multilingual-v3", 512, "1024"],
        ["amazon.nova-2-multimodal-embeddings-v1:0", 512, "256, 384, 1024, 3072"],
    ])("%s at %p dimensions is refused, naming the widths", (id, width, supported) => {
        const mutate = (c: any) => {
            models(c).embeddingModelId = id;
            models(c).embeddingDimensions = width;
        };
        expect(resolve(mutate)).toThrow(
            new RegExp(
                `embeddingDimensions is ${width}, which .* does not produce\\. Supported widths: ${supported}\\.`
            )
        );
        expect(builder(mutate).errors).toContain("vector-search-embedding-dimensions-family");
    });

    test.each([
        ["amazon.titan-embed-text-v2:0", 512],
        ["amazon.nova-2-multimodal-embeddings-v1:0", 3072],
        ["cohere.embed-english-v3", 1024],
    ])("%s at %p dimensions is accepted", (id, width) => {
        const mutate = (c: any) => {
            models(c).embeddingModelId = id;
            models(c).embeddingDimensions = width;
        };
        expect(resolve(mutate)).not.toThrow();
        expect(builder(mutate).errors).toEqual([]);
    });

    test("the family is not checked while vector search is off", () => {
        const mutate = (c: any) => {
            vectorSearchOff(c);
            models(c).embeddingModelId = "cohere.embed-v4:0";
            models(c).embeddingDimensions = 1536;
        };
        expect(resolve(mutate)).not.toThrow();
        expect(builder(mutate).errors).toEqual([]);
    });
});
