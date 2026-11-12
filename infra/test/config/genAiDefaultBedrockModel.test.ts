/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Amazon Bedrock model id VAMS ships as the default for GenAI 3D Metadata Labeling. Bedrock refuses
 * a model whose lifecycle status is LEGACY to an account that has not called it for 30 days, while the
 * model's inference profile still lists as ACTIVE, and refuses every account once the model reaches end
 * of life, so a deployment on such a default synthesizes cleanly and fails every run. Each default site
 * (the commercial template, config.json and the ConfigBuilder presets) is checked against those models,
 * and the commercial sites against GENAI_DEFAULT_BEDROCK_MODEL_ID.
 */

import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import configJson from "../../config/config.json";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";

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

const foundationModel = (modelId: string): string =>
    modelId.replace(/^(global|us-gov|us|eu|apac)\./, "");

const labelingModelId = (config: any): string =>
    config.app.pipelines.useGenAiMetadata3dLabeling?.bedrockModelId ?? "";

describe("GenAI 3D Metadata Labeling default Amazon Bedrock model", () => {
    test("no shipped default names a model Amazon Bedrock lists as LEGACY or end of life", () => {
        const sites: [string, string][] = [
            ["config.template.commercial.json", labelingModelId(commercialTemplate)],
            ["config.json", labelingModelId(configJson)],
            ["ConfigBuilder commercial preset", labelingModelId(makeDefaultConfig("commercial"))],
            ["ConfigBuilder govcloud preset", labelingModelId(makeDefaultConfig("govcloud"))],
            ["ConfigBuilder eusovereign preset", labelingModelId(makeDefaultConfig("eusovereign"))],
            ["GENAI_DEFAULT_BEDROCK_MODEL_ID", Config.GENAI_DEFAULT_BEDROCK_MODEL_ID ?? ""],
        ];
        const retired = sites.filter(
            ([, modelId]) =>
                modelId !== "" && RETIRED_BEDROCK_MODELS.includes(foundationModel(modelId))
        );
        expect(retired).toEqual([]);
    });

    test("the commercial Labeling default is GENAI_DEFAULT_BEDROCK_MODEL_ID everywhere", () => {
        expect(Config.GENAI_DEFAULT_BEDROCK_MODEL_ID).toMatch(/^global\.anthropic\./);
        const commercialSites: [string, any][] = [
            ["config.template.commercial.json", commercialTemplate],
            ["config.json", configJson],
            ["ConfigBuilder commercial preset", makeDefaultConfig("commercial")],
        ];
        for (const [site, config] of commercialSites) {
            expect(`${site}: ${labelingModelId(config)}`).toBe(
                `${site}: ${Config.GENAI_DEFAULT_BEDROCK_MODEL_ID}`
            );
        }
    });
});
