/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Amazon Bedrock model ids VAMS ships as defaults for its GenAI pipelines. Bedrock refuses a model
 * whose lifecycle status is LEGACY to an account that has not called it for 30 days, while the model's
 * inference profile still lists as ACTIVE, and refuses every account once the model reaches end of
 * life, so a deployment on such a default synthesizes cleanly and fails every run. Each default site
 * (getConfig(), the commercial template, config.json and the ConfigBuilder presets) is checked against
 * those models, and the CAD STEP agent default against GENAI_DEFAULT_BEDROCK_MODEL_ID.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import configJson from "../../config/config.json";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { newTestApp } from "../support/testApp";

/**
 * Foundation models Amazon Bedrock lists with lifecycle status LEGACY, or answers "has reached the end
 * of its life" for (`aws bedrock get-foundation-model --model-identifier <id> --query
 * modelDetails.modelLifecycle`). Add a model here when Bedrock moves it to LEGACY.
 */
const RETIRED_BEDROCK_MODELS = [
    "anthropic.claude-3-sonnet-20240229-v1:0",
    "anthropic.claude-3-haiku-20240307-v1:0",
    "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "anthropic.claude-3-7-sonnet-20250219-v1:0",
    "anthropic.claude-sonnet-4-20250514-v1:0",
    "anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-opus-4-1-20250805-v1:0",
];

/** The pipelines whose model id defaults to GENAI_DEFAULT_BEDROCK_MODEL_ID in the commercial partition. */
const DEFAULTED_PIPELINES = ["useGenAiCadStepAgent"] as const;
/** Every pipeline block that carries a `bedrockModelId`. */
const MODEL_PIPELINES = [...DEFAULTED_PIPELINES, "useGenAiVideoSopBom"] as const;

const foundationModel = (modelId: string): string =>
    modelId.replace(/^(global|us-gov|us|eu|apac)\./, "");

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** getConfig() over the commercial template with the CAD STEP agent block removed. */
const cadDefaultFromGetConfig = (): string => {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    delete config.app.pipelines.useGenAiCadStepAgent;
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (path: string, ...rest: unknown[]) => {
            if (typeof path === "string" && path.endsWith("config.json")) {
                return JSON.stringify(config);
            }
            return realReadFileSync(path, ...rest);
        }
    );
    return Config.getConfig(newTestApp()).app.pipelines.useGenAiCadStepAgent.bedrockModelId;
};

const shippedModelIds = (): [string, string][] => {
    const sites: [string, any][] = [
        ["config.template.commercial.json", commercialTemplate],
        ["config.json", configJson],
        ["ConfigBuilder commercial preset", makeDefaultConfig("commercial")],
        ["ConfigBuilder govcloud preset", makeDefaultConfig("govcloud")],
        ["ConfigBuilder eusovereign preset", makeDefaultConfig("eusovereign")],
    ];
    const ids: [string, string][] = [];
    for (const [site, config] of sites) {
        for (const pipeline of MODEL_PIPELINES) {
            const modelId = config.app.pipelines[pipeline]?.bedrockModelId ?? "";
            if (modelId !== "") ids.push([`${site} ${pipeline}`, modelId]);
        }
    }
    return ids;
};

describe("GenAI pipeline default Amazon Bedrock models", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
    });

    test("no shipped default names a model Amazon Bedrock lists as LEGACY or end of life", () => {
        const sites: [string, string][] = [
            ...shippedModelIds(),
            ["getConfig() useGenAiCadStepAgent default", cadDefaultFromGetConfig()],
            ["GENAI_DEFAULT_BEDROCK_MODEL_ID", Config.GENAI_DEFAULT_BEDROCK_MODEL_ID ?? ""],
        ];
        const retired = sites.filter(([, modelId]) =>
            RETIRED_BEDROCK_MODELS.includes(foundationModel(modelId))
        );
        expect(retired).toEqual([]);
    });

    test("the CAD STEP agent default is GENAI_DEFAULT_BEDROCK_MODEL_ID everywhere", () => {
        expect(Config.GENAI_DEFAULT_BEDROCK_MODEL_ID).toMatch(/^global\.anthropic\./);
        const commercialSites: [string, any][] = [
            ["config.template.commercial.json", commercialTemplate],
            ["config.json", configJson],
            ["ConfigBuilder commercial preset", makeDefaultConfig("commercial")],
        ];
        for (const [site, config] of commercialSites) {
            for (const pipeline of DEFAULTED_PIPELINES) {
                expect(
                    `${site} ${pipeline}: ${config.app.pipelines[pipeline].bedrockModelId}`
                ).toBe(`${site} ${pipeline}: ${Config.GENAI_DEFAULT_BEDROCK_MODEL_ID}`);
            }
        }
        expect(cadDefaultFromGetConfig()).toBe(Config.GENAI_DEFAULT_BEDROCK_MODEL_ID);
    });
});
