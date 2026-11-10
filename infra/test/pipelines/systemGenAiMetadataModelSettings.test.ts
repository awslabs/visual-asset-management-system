/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The SYSTEM GenAI metadata pipeline's model settings reach its functions: the environment the
 * analysis handler reads (BEDROCK_TEXT_MODEL_ID, BEDROCK_MAX_TOKENS, BEDROCK_TEMPERATURE,
 * ANALYSIS_MODEL_SUPPORTS_IMAGES) and the per-function `bedrock:InvokeModel` grants, each function
 * holding the models its own step calls and no other.
 *
 * The defaults must reproduce the requests the handler sent before the settings existed (2048 tokens,
 * temperature 0.2, images on), so the default synth is asserted value by value. A null temperature
 * must leave the variable unset, because the handler omits the parameter only when it is absent.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { SystemGenAiMetadataConstruct } from "../../lib/nestedStacks/pipelines/system/genAiMetadata/constructs/systemGenAiMetadata-construct";
import {
    BEDROCK_INFERENCE_PROFILE_PREFIX,
    analysisStepModelIds,
    bedrockFoundationModelId,
    isBedrockInferenceProfileId,
} from "../../lib/nestedStacks/pipelines/system/genAiMetadata/lambdaBuilder/systemGenAiMetadataFunctions";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";
const ANALYSIS = "global.anthropic.claude-haiku-4-5-20251001-v1:0";
const EMBEDDING = "amazon.titan-embed-text-v2:0";

const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = REGION;
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.useGlobalVpc.enabled = true;
    config.app.useGlobalVpc.useForAllLambdas = false;
    config.app.pipelines.useSystemGenAiMetadata.enabled = true;
    config.app.pipelines.useSystemGenAiMetadata.autoRegisterWithVAMS = true;
    config.app.pipelines.useSystemGenAiMetadata.bedrockModels.analysisModelId = ANALYSIS;
    config.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = {
        guardrailIdentifier: "",
        guardrailVersion: "",
        create: { enabled: false, promptAttackInputStrength: "LOW", piiFilter: "anonymize" },
    };
    config.app.vectorSearch.enabled = true;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

const synth = (id: string, mutate?: (c: Config.Config) => void): Template => {
    const config = createMockConfig();
    mutate?.(config);
    Service.SetConfig(config);

    const app = newTestApp();
    const stack = new cdk.Stack(app, id, { env: { account: ACCOUNT, region: REGION } });
    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const securityGroups = [new ec2.SecurityGroup(stack, "Sg", { vpc })];
    const assetBucket = new s3.Bucket(stack, "AssetBucket");
    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(assetBucket, "/", "db", undefined, undefined, true);
    const storage = {
        encryption: { kmsKey: new kms.Key(stack, "Key") },
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
        },
        eventBridge: { orchestrationBus: new events.EventBus(stack, "Bus") },
    } as unknown as storageResources;

    const construct = new SystemGenAiMetadataConstruct(stack, "SystemGenAiMetadata", {
        config,
        storageResources: storage,
        vpc,
        pipelineSubnets: vpc.isolatedSubnets.length ? vpc.isolatedSubnets : vpc.privateSubnets,
        pipelineSecurityGroups: securityGroups,
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        importGlobalPipelineWorkflowV2FunctionName: "vams-test-importFunction",
    });
    return Template.fromStack(construct);
};

const models = (c: Config.Config) => c.app.pipelines.useSystemGenAiMetadata.bedrockModels;

/** The environment of every pipeline function, keyed by its logical-id prefix. */
function environments(template: Template): Record<string, Record<string, string>> {
    const out: Record<string, Record<string, string>> = {};
    for (const [logicalId, fn] of Object.entries(
        template.findResources("AWS::Lambda::Function")
    ) as [string, any][]) {
        const match = /^SystemGenAiMetadata([A-Za-z0-9]+?)[0-9A-F]{8}$/.exec(logicalId);
        if (match) out[match[1]] = fn.Properties.Environment?.Variables ?? {};
    }
    return out;
}

/** The `bedrock:InvokeModel` statements of the policies whose logical id contains `fragment`. */
function invokeStatements(template: Template, fragment: string): any[] {
    return Object.entries(template.findResources("AWS::IAM::Policy"))
        .filter(([logicalId]) => logicalId.includes(fragment))
        .flatMap(([, policy]) => (policy as any).Properties.PolicyDocument.Statement)
        .filter((s: any) => ([] as string[]).concat(s.Action).includes("bedrock:InvokeModel"));
}

const resourcesOf = (statements: any[]) => statements.map((s) => s.Resource);

const profileGrant = (id: string) => [
    `arn:aws:bedrock:${REGION}:${ACCOUNT}:inference-profile/${id}`,
    `arn:aws:bedrock:*::foundation-model/${bedrockFoundationModelId(id)}`,
];
const plainGrant = (id: string) => [
    `arn:aws:bedrock:${REGION}::foundation-model/${id}`,
    `arn:aws:bedrock:::foundation-model/${id}`,
];

describe("SYSTEM GenAI metadata model settings", () => {
    const defaults = synth("Defaults");

    test("the default synth carries today's request values to the analysis handler", () => {
        const env = environments(defaults);
        expect(env.GenerateMetadata).toMatchObject({
            BEDROCK_ANALYSIS_MODEL_ID: ANALYSIS,
            BEDROCK_TEXT_MODEL_ID: "",
            BEDROCK_MAX_TOKENS: "2048",
            BEDROCK_TEMPERATURE: "0.2",
            ANALYSIS_MODEL_SUPPORTS_IMAGES: "true",
        });
        // The video segment child describes windows from their frames with the analysis model and the
        // same inference settings; it never calls the text model, and with a text-only analysis model the
        // media branch writes no window plan, so neither setting is handed to it.
        expect(env.SegmentAnalyze).toMatchObject({
            BEDROCK_ANALYSIS_MODEL_ID: ANALYSIS,
            BEDROCK_MAX_TOKENS: "2048",
            BEDROCK_TEMPERATURE: "0.2",
        });
        expect("BEDROCK_TEXT_MODEL_ID" in env.SegmentAnalyze).toBe(false);
        expect("ANALYSIS_MODEL_SUPPORTS_IMAGES" in env.SegmentAnalyze).toBe(false);
        // The render decision is made in constructPipeline.
        expect(env.ConstructPipeline.ANALYSIS_MODEL_SUPPORTS_IMAGES).toBe("true");
        // The settings are the analysis steps': no other function is handed them.
        for (const name of ["ConstructPipeline", "GenerateEmbedding"]) {
            expect(env[name].BEDROCK_TEXT_MODEL_ID).toBeUndefined();
            expect(env[name].BEDROCK_MAX_TOKENS).toBeUndefined();
            expect(env[name].BEDROCK_TEMPERATURE).toBeUndefined();
        }
    });

    test("with no text model the analysis function holds the analysis model only", () => {
        expect(resourcesOf(invokeStatements(defaults, "GenerateMetadata"))).toEqual([
            profileGrant(ANALYSIS),
        ]);
    });

    test("a distinct text model is granted to the whole-file analysis function only", () => {
        const text = "amazon.nova-2-lite-v1:0";
        const template = synth("TextModel", (c) => {
            models(c).textModelId = text;
        });
        expect(environments(template).GenerateMetadata.BEDROCK_TEXT_MODEL_ID).toBe(text);
        expect("BEDROCK_TEXT_MODEL_ID" in environments(template).SegmentAnalyze).toBe(false);
        expect(resourcesOf(invokeStatements(template, "GenerateMetadata"))).toEqual([
            profileGrant(ANALYSIS),
            plainGrant(text),
        ]);
        // The segment child: the analysis model, then the embedding model; every window request
        // carries frames, so the text model is never called.
        expect(resourcesOf(invokeStatements(template, "SegmentAnalyze"))).toEqual([
            profileGrant(ANALYSIS),
            plainGrant(EMBEDDING),
        ]);
        // The embedding function holds the embedding model and nothing else.
        expect(resourcesOf(invokeStatements(template, "GenerateEmbedding"))).toEqual([
            plainGrant(EMBEDDING),
        ]);
    });

    test("the segment child is granted no analysis model with the GenAI layer off", () => {
        const template = synth("SegmentGenAiOff", (c) => {
            c.app.pipelines.useSystemGenAiMetadata.useGenAiAnalysis = false;
            models(c).analysisModelId = "";
            models(c).textModelId = "amazon.nova-2-lite-v1:0";
        });
        expect(resourcesOf(invokeStatements(template, "SegmentAnalyze"))).toEqual([
            plainGrant(EMBEDDING),
        ]);
        expect("BEDROCK_TEXT_MODEL_ID" in environments(template).SegmentAnalyze).toBe(false);
    });

    test("a text model equal to the analysis model is one grant", () => {
        const template = synth("SameTextModel", (c) => {
            models(c).textModelId = ANALYSIS;
        });
        expect(resourcesOf(invokeStatements(template, "GenerateMetadata"))).toEqual([
            profileGrant(ANALYSIS),
        ]);
    });

    test.each(["jp.", "au.", "in."])(
        "a %s text profile is granted as an inference profile with its model in every Region",
        (prefix) => {
            const text = `${prefix}anthropic.claude-haiku-4-5-20251001-v1:0`;
            const template = synth(`TextProfile${prefix.replace(".", "")}`, (c) => {
                models(c).analysisModelId = "amazon.nova-2-lite-v1:0";
                models(c).textModelId = text;
            });
            expect(resourcesOf(invokeStatements(template, "GenerateMetadata"))).toEqual([
                plainGrant("amazon.nova-2-lite-v1:0"),
                [
                    `arn:aws:bedrock:${REGION}:${ACCOUNT}:inference-profile/${text}`,
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
                ],
            ]);
            // The Region wildcard is justified on the function's policy because the text model is a
            // profile, though the analysis model is not.
            const policies = Object.entries(template.findResources("AWS::IAM::Policy")).filter(
                ([logicalId]) => logicalId.includes("GenerateMetadata")
            ) as [string, any][];
            const rules: any[] = policies[0][1].Metadata?.cdk_nag?.rules_to_suppress ?? [];
            expect(
                rules.filter(
                    (r) =>
                        r.id === "AwsSolutions-IAM5" &&
                        String(r.reason).includes("cross-Region inference profile")
                )
            ).toHaveLength(1);
        }
    );

    test("a null temperature leaves BEDROCK_TEMPERATURE unset, so the request carries none", () => {
        const template = synth("NoTemperature", (c) => {
            models(c).inference.temperature = null;
        });
        for (const name of ["GenerateMetadata", "SegmentAnalyze"]) {
            const env = environments(template)[name];
            expect(env.BEDROCK_TEMPERATURE).toBeUndefined();
            expect("BEDROCK_TEMPERATURE" in env).toBe(false);
            expect(env.BEDROCK_MAX_TOKENS).toBe("2048");
        }
    });

    test("configured values are passed as written", () => {
        const template = synth("Tuned", (c) => {
            models(c).inference = { maxTokens: 4096, temperature: 0 };
        });
        for (const name of ["GenerateMetadata", "SegmentAnalyze"]) {
            expect(environments(template)[name]).toMatchObject({
                BEDROCK_MAX_TOKENS: "4096",
                BEDROCK_TEMPERATURE: "0",
            });
        }
    });

    test("a text-only analysis model is signalled to the render decision and the analysis", () => {
        const template = synth("TextOnly", (c) => {
            models(c).analysisModelSupportsImages = false;
        });
        const env = environments(template);
        for (const name of ["ConstructPipeline", "GenerateMetadata"]) {
            expect(env[name].ANALYSIS_MODEL_SUPPORTS_IMAGES).toBe("false");
        }
        // constructPipeline carries the flag on the state; the media branch then plans no windows.
        expect("ANALYSIS_MODEL_SUPPORTS_IMAGES" in env.SegmentAnalyze).toBe(false);
    });

    test("with the GenAI layer off a configured text model is neither passed nor granted", () => {
        const template = synth("GenAiOff", (c) => {
            c.app.pipelines.useSystemGenAiMetadata.useGenAiAnalysis = false;
            models(c).analysisModelId = "";
            models(c).textModelId = "amazon.nova-2-lite-v1:0";
        });
        expect(environments(template).GenerateMetadata.BEDROCK_TEXT_MODEL_ID).toBe("");
        expect(invokeStatements(template, "GenerateMetadata")).toHaveLength(0);
        expect(JSON.stringify(template.toJSON())).not.toContain("nova-2-lite");
    });
});

describe("an empty embedding model id", () => {
    test("is granted to no function (vector search off, as the EU Sovereign Cloud template ships)", () => {
        const template = synth("NoEmbeddingModel", (c) => {
            c.app.vectorSearch.enabled = false;
            models(c).embeddingModelId = "";
        });
        expect(invokeStatements(template, "GenerateEmbedding")).toHaveLength(0);
        // The segment child runs only on the vector search path, so it holds no model grant either.
        expect(invokeStatements(template, "SegmentAnalyze")).toHaveLength(0);
        const all = Object.values(template.findResources("AWS::IAM::Policy")).flatMap(
            (p: any) => p.Properties.PolicyDocument.Statement
        );
        expect(JSON.stringify(all)).not.toMatch(/foundation-model\/"/);
    });
});

describe("the grant helpers", () => {
    test("the profile prefix is the one config.ts validates against", () => {
        expect(BEDROCK_INFERENCE_PROFILE_PREFIX).toBe(Config.BEDROCK_INFERENCE_PROFILE_PREFIX);
    });

    test.each(Object.keys(Config.BEDROCK_INFERENCE_PROFILE_PREFIXES))(
        "a %s. id is a profile whose model is the id without the prefix",
        (prefix) => {
            const id = `${prefix}.anthropic.claude-haiku-4-5-20251001-v1:0`;
            expect(isBedrockInferenceProfileId(id)).toBe(true);
            expect(bedrockFoundationModelId(id)).toBe("anthropic.claude-haiku-4-5-20251001-v1:0");
        }
    );

    test("a prefix-like word inside a model id is not a prefix", () => {
        expect(isBedrockInferenceProfileId("amazon.jp-model-v1:0")).toBe(false);
        expect(bedrockFoundationModelId("amazon.jp-model-v1:0")).toBe("amazon.jp-model-v1:0");
    });

    test("analysisStepModelIds: the analysis model, then a distinct text model", () => {
        const config = createMockConfig();
        expect(analysisStepModelIds(config)).toEqual([ANALYSIS]);
        models(config).textModelId = "amazon.nova-2-lite-v1:0";
        expect(analysisStepModelIds(config)).toEqual([ANALYSIS, "amazon.nova-2-lite-v1:0"]);
        models(config).textModelId = ANALYSIS;
        expect(analysisStepModelIds(config)).toEqual([ANALYSIS]);
        config.app.pipelines.useSystemGenAiMetadata.useGenAiAnalysis = false;
        expect(analysisStepModelIds(config)).toEqual([]);
    });
});
