/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Amazon Bedrock guardrail the SYSTEM GenAI metadata pipeline creates, synthesized on a plain stack.
 * Pins the guardrail and its published version, the PROMPT_ATTACK filter at the configured strength
 * (output NONE), the PII policy per `piiFilter`, the deployment-key encryption, the deterministic name,
 * and the wiring into both analysis functions: the environment pair reads the created guardrail's id and
 * version, and `bedrock:ApplyGuardrail` is granted on its ARN and on nothing else. The bring-your-own
 * branch (creation off, identifier set) still emits no guardrail and grants the composed literal ARN,
 * and configuration validation rejects both at once.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as fs from "fs";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { SystemGenAiMetadataConstruct } from "../../lib/nestedStacks/pipelines/system/genAiMetadata/constructs/systemGenAiMetadata-construct";
import {
    SYSTEM_GENAI_GUARDRAIL_BLOCKED_INPUT_MESSAGE,
    SYSTEM_GENAI_GUARDRAIL_BLOCKED_OUTPUT_MESSAGE,
    SYSTEM_GENAI_GUARDRAIL_NAME_PREFIX,
    SYSTEM_GENAI_GUARDRAIL_PII_ENTITIES,
} from "../../lib/nestedStacks/pipelines/system/genAiMetadata/constructs/systemGenAiGuardrail-construct";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

type Guardrail = Config.Config["app"]["pipelines"]["useSystemGenAiMetadata"]["bedrockGuardrail"];

const createdGuardrail = (overrides: Partial<Guardrail["create"]> = {}): Guardrail => ({
    guardrailIdentifier: "",
    guardrailVersion: "",
    create: {
        enabled: true,
        promptAttackInputStrength: "LOW",
        piiFilter: "anonymize",
        ...overrides,
    },
});

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
    config.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId =
        "global.anthropic.claude-haiku-4-5-20251001-v1:0";
    config.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = createdGuardrail();
    config.app.vectorSearch.enabled = true;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

/** Synthesizes the construct; `withKey` false leaves `storageResources.encryption.kmsKey` undefined. */
const synth = (id: string, mutate?: (c: Config.Config) => void, withKey = true): Template => {
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
        encryption: { kmsKey: withKey ? new kms.Key(stack, "Key") : undefined },
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

/** A property value assembled through Ref / Fn::GetAtt / Fn::Join, flattened to a string. */
const flatten = (value: any): string => {
    if (value === undefined || value === null) return "";
    if (typeof value !== "object") return String(value);
    if (Array.isArray(value)) return value.map(flatten).join("");
    if (value.Ref) return `Ref(${value.Ref})`;
    if (value["Fn::GetAtt"]) return `GetAtt(${value["Fn::GetAtt"].join(".")})`;
    if (value["Fn::Join"]) {
        const [sep, parts] = value["Fn::Join"];
        return (parts as any[]).map(flatten).join(sep);
    }
    return Object.values(value).map(flatten).join("");
};

const only = <T>(items: T[]): T => {
    expect(items).toHaveLength(1);
    return items[0];
};

const guardrailOf = (t: Template): [string, any] =>
    only(Object.entries(t.findResources("AWS::Bedrock::Guardrail")));
const versionOf = (t: Template): [string, any] =>
    only(Object.entries(t.findResources("AWS::Bedrock::GuardrailVersion")));

const functionByHandler = (t: Template, handler: string): any =>
    only(
        Object.values(t.findResources("AWS::Lambda::Function")).filter(
            (f: any) => f.Properties.Handler === handler
        )
    );
const functionByIdPrefix = (t: Template, prefix: string): any =>
    only(
        Object.entries(t.findResources("AWS::Lambda::Function"))
            .filter(([id]) => id.startsWith(prefix))
            .map(([, f]) => f)
    );
const analysisEnvironments = (t: Template): Record<string, string>[] => [
    functionByHandler(t, "generateMetadata.lambda_handler").Properties.Environment.Variables,
    functionByIdPrefix(t, "SystemGenAiMetadataSegmentAnalyze").Properties.Environment.Variables,
];

const allStatements = (t: Template): any[] =>
    Object.values(t.findResources("AWS::IAM::Policy")).flatMap(
        (p: any) => p.Properties.PolicyDocument.Statement
    );
const statementsOf = (t: Template, roleIdFragment: string): any[] =>
    Object.entries(t.findResources("AWS::IAM::Policy"))
        .filter(([logicalId]) => logicalId.includes(roleIdFragment))
        .flatMap(([, policy]) => (policy as any).Properties.PolicyDocument.Statement);
const applyGuardrail = (statements: any[]): any[] =>
    statements.filter((s) =>
        (Array.isArray(s.Action) ? s.Action : [s.Action]).includes("bedrock:ApplyGuardrail")
    );

describe("the created guardrail", () => {
    const created = synth("Created");
    const [guardrailId, guardrail] = guardrailOf(created);
    const [versionId, version] = versionOf(created);

    test("is one guardrail with one published version that follows it", () => {
        expect(version.Properties.GuardrailIdentifier).toEqual({
            "Fn::GetAtt": [guardrailId, "GuardrailId"],
        });
        expect(versionId).toMatch(/^SystemGenAiMetadataGuardrail/);
    });

    test("carries a PROMPT_ATTACK filter on the prompt at the configured strength and none on the response", () => {
        expect(guardrail.Properties.ContentPolicyConfig.FiltersConfig).toEqual([
            { Type: "PROMPT_ATTACK", InputStrength: "LOW", OutputStrength: "NONE" },
        ]);
        const medium = guardrailOf(
            synth("CreatedMedium", (c) => {
                c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = createdGuardrail({
                    promptAttackInputStrength: "MEDIUM",
                });
            })
        )[1];
        expect(medium.Properties.ContentPolicyConfig.FiltersConfig).toEqual([
            { Type: "PROMPT_ATTACK", InputStrength: "MEDIUM", OutputStrength: "NONE" },
        ]);
    });

    test("applies the PII action to the prompt and the response: anonymizes every listed entity by default, blocks them on `block`, and adds no PII policy on `off`", () => {
        const entities = (t: Template) =>
            guardrailOf(t)[1].Properties.SensitiveInformationPolicyConfig?.PiiEntitiesConfig;
        // The legacy Action alone reaches the model response only; InputAction with InputEnabled is what
        // masks or blocks the PII an uploaded file carries before the model reads the prompt.
        const entity = (type: string, action: "ANONYMIZE" | "BLOCK") => ({
            Type: type,
            Action: action,
            InputAction: action,
            InputEnabled: true,
            OutputAction: action,
            OutputEnabled: true,
        });
        expect(entities(created)).toEqual(
            SYSTEM_GENAI_GUARDRAIL_PII_ENTITIES.map((type) => entity(type, "ANONYMIZE"))
        );
        expect(SYSTEM_GENAI_GUARDRAIL_PII_ENTITIES).toEqual(
            expect.arrayContaining([
                "EMAIL",
                "PHONE",
                "NAME",
                "ADDRESS",
                "US_SOCIAL_SECURITY_NUMBER",
                "CREDIT_DEBIT_CARD_NUMBER",
                "AWS_ACCESS_KEY",
                "AWS_SECRET_KEY",
                "PASSWORD",
            ])
        );
        expect(SYSTEM_GENAI_GUARDRAIL_PII_ENTITIES).toHaveLength(9);

        const blocking = synth("CreatedBlock", (c) => {
            c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = createdGuardrail({
                piiFilter: "block",
            });
        });
        expect(entities(blocking)).toEqual(
            SYSTEM_GENAI_GUARDRAIL_PII_ENTITIES.map((type) => entity(type, "BLOCK"))
        );

        const off = synth("CreatedOff", (c) => {
            c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = createdGuardrail({
                piiFilter: "off",
            });
        });
        expect(guardrailOf(off)[1].Properties.SensitiveInformationPolicyConfig).toBeUndefined();
        // The prompt-attack filter is never dropped with the PII policy.
        expect(guardrailOf(off)[1].Properties.ContentPolicyConfig.FiltersConfig).toHaveLength(1);
    });

    test("carries the fixed blocked-prompt and blocked-response messages", () => {
        expect(guardrail.Properties.BlockedInputMessaging).toBe(
            SYSTEM_GENAI_GUARDRAIL_BLOCKED_INPUT_MESSAGE
        );
        expect(guardrail.Properties.BlockedOutputsMessaging).toBe(
            SYSTEM_GENAI_GUARDRAIL_BLOCKED_OUTPUT_MESSAGE
        );
        for (const message of [
            SYSTEM_GENAI_GUARDRAIL_BLOCKED_INPUT_MESSAGE,
            SYSTEM_GENAI_GUARDRAIL_BLOCKED_OUTPUT_MESSAGE,
        ]) {
            expect(message.length).toBeGreaterThan(0);
            expect(message.length).toBeLessThanOrEqual(500);
        }
    });

    test("is encrypted with the deployment key when one exists, and names no key otherwise", () => {
        // The key lives in the parent stack, so the nested template receives its ARN as a parameter.
        expect(flatten(guardrail.Properties.KmsKeyArn)).toMatch(/Key.*Arn/);
        const noKey = synth("CreatedNoKey", undefined, false);
        expect(guardrailOf(noKey)[1].Properties.KmsKeyArn).toBeUndefined();
    });

    test("has a deterministic, deployment-scoped name within the Bedrock name contract", () => {
        const name = guardrail.Properties.Name;
        expect(name).toMatch(new RegExp(`^${SYSTEM_GENAI_GUARDRAIL_NAME_PREFIX}[0-9a-f]{10}$`));
        expect(name.length).toBeLessThanOrEqual(50);
        expect(name).toMatch(/^[0-9a-zA-Z-_]+$/);
        // The same configuration synthesized again in this process names the same guardrail: the
        // hash is over literals, not a Token whose allocation counter keeps running between synths.
        const again = synth("CreatedAgain");
        expect(guardrailOf(again)[1].Properties.Name).toBe(name);
        // Another deployment in the same account names a different one.
        const other = synth("CreatedOther", (c) => {
            c.env.coreStackName = "vams-other-us-east-1";
        });
        expect(guardrailOf(other)[1].Properties.Name).not.toBe(name);
    });

    test("publishes a new version when the policy changes: the version's description carries the policy digest", () => {
        const description = version.Properties.Description;
        expect(description).toMatch(/^VAMS SYSTEM GenAI metadata guardrail policy [0-9a-f]{10}$/);
        const again = synth("VersionAgain");
        expect(versionOf(again)[1].Properties.Description).toBe(description);
        const stronger = synth("VersionStronger", (c) => {
            c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = createdGuardrail({
                promptAttackInputStrength: "HIGH",
            });
        });
        expect(versionOf(stronger)[1].Properties.Description).not.toBe(description);
    });

    test("both analysis functions read the created guardrail's id and published version", () => {
        for (const env of analysisEnvironments(created)) {
            expect(flatten(env.BEDROCK_GUARDRAIL_IDENTIFIER)).toBe(
                `GetAtt(${guardrailId}.GuardrailId)`
            );
            expect(flatten(env.BEDROCK_GUARDRAIL_VERSION)).toBe(`GetAtt(${versionId}.Version)`);
        }
    });

    test("bedrock:ApplyGuardrail is granted on the created guardrail's ARN to both analysis functions and to no one else", () => {
        for (const fragment of ["GenerateMetadata", "SegmentAnalyze"]) {
            const grant = only(applyGuardrail(statementsOf(created, fragment)));
            expect(grant.Effect).toBe("Allow");
            expect(grant.Resource).toEqual({ "Fn::GetAtt": [guardrailId, "GuardrailArn"] });
        }
        expect(applyGuardrail(allStatements(created))).toHaveLength(2);
    });
});

describe("the bring-your-own branch (creation off, identifier set)", () => {
    const own = synth("Own", (c) => {
        c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = {
            guardrailIdentifier: "kb4v3hkqvi6f",
            guardrailVersion: "1",
            create: { enabled: false, promptAttackInputStrength: "LOW", piiFilter: "anonymize" },
        };
    });

    test("emits no guardrail resource", () => {
        expect(own.findResources("AWS::Bedrock::Guardrail")).toEqual({});
        expect(own.findResources("AWS::Bedrock::GuardrailVersion")).toEqual({});
    });

    test("the environment pair is the configured literals", () => {
        for (const env of analysisEnvironments(own)) {
            expect(env.BEDROCK_GUARDRAIL_IDENTIFIER).toBe("kb4v3hkqvi6f");
            expect(env.BEDROCK_GUARDRAIL_VERSION).toBe("1");
        }
    });

    test("bedrock:ApplyGuardrail is granted on the composed guardrail ARN, once per analysis function", () => {
        const grants = applyGuardrail(allStatements(own));
        expect(grants).toHaveLength(2);
        for (const grant of grants) {
            expect(grant.Resource).toBe(
                `arn:aws:bedrock:${REGION}:${ACCOUNT}:guardrail/kb4v3hkqvi6f`
            );
        }
    });
});

describe("no guardrail (creation off, no identifier)", () => {
    const none = synth("None", (c) => {
        c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = createdGuardrail({
            enabled: false,
        });
    });

    test("emits no guardrail, grants nothing, and leaves the pair empty for the handlers' warning", () => {
        expect(none.findResources("AWS::Bedrock::Guardrail")).toEqual({});
        expect(applyGuardrail(allStatements(none))).toEqual([]);
        for (const env of analysisEnvironments(none)) {
            expect(env.BEDROCK_GUARDRAIL_IDENTIFIER).toBe("");
            expect(env.BEDROCK_GUARDRAIL_VERSION).toBe("");
        }
    });
});

describe("getConfig() and the create block", () => {
    /** Builds a config.json from the commercial template, applies `mutate`, and calls getConfig(). */
    const resolve = (mutate: (c: any) => void): (() => Config.Config) => {
        const config = JSON.parse(JSON.stringify(commercialTemplate));
        config.env.region = REGION;
        config.env.account = ACCOUNT;
        config.app.baseStackName = "vamstest";
        mutate(config);
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(
            (p: string, ...rest: unknown[]) => {
                if (typeof p === "string" && p.endsWith("config.json")) {
                    return JSON.stringify(config);
                }
                return realReadFileSync(p, ...rest);
            }
        );
        return () => Config.getConfig(newTestApp());
    };
    const guardrailBlock = (c: any) => c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail;

    // getConfig() prints version lines and unrelated configuration warnings for the template.
    let quiet: jest.SpyInstance[] = [];
    beforeAll(() => {
        quiet = [
            jest.spyOn(console, "log").mockImplementation(() => undefined),
            jest.spyOn(console, "warn").mockImplementation(() => undefined),
        ];
    });
    afterAll(() => quiet.forEach((spy) => spy.mockRestore()));

    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
    });

    test("the commercial template creates the guardrail at LOW with PII anonymized", () => {
        const config = resolve(() => undefined)();
        expect(guardrailBlock(config)).toEqual(createdGuardrail());
    });

    test("an absent create block defaults to creation, LOW and anonymize", () => {
        const config = resolve((c) => {
            delete guardrailBlock(c).create;
        })();
        expect(guardrailBlock(config).create).toEqual({
            enabled: true,
            promptAttackInputStrength: "LOW",
            piiFilter: "anonymize",
        });
    });

    test("an absent create block beside an operator-owned guardrail keeps that guardrail (creation off)", () => {
        const config = resolve((c) => {
            c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = {
                guardrailIdentifier: "kb4v3hkqvi6f",
                guardrailVersion: "1",
            };
        })();
        expect(guardrailBlock(config).create.enabled).toBe(false);
        expect(guardrailBlock(config).guardrailIdentifier).toBe("kb4v3hkqvi6f");
    });

    test("creation on together with an operator-owned guardrail is rejected", () => {
        expect(
            resolve((c) => {
                guardrailBlock(c).guardrailIdentifier = "kb4v3hkqvi6f";
                guardrailBlock(c).guardrailVersion = "1";
                guardrailBlock(c).create.enabled = true;
            })
        ).toThrow(
            /create\.enabled is true while guardrailIdentifier names an operator-owned guardrail/
        );
    });

    test("the rejection holds on a disabled pipeline too", () => {
        expect(
            resolve((c) => {
                c.app.pipelines.useSystemGenAiMetadata.enabled = false;
                c.app.vectorSearch.enabled = false;
                guardrailBlock(c).guardrailIdentifier = "kb4v3hkqvi6f";
                guardrailBlock(c).guardrailVersion = "1";
            })
        ).toThrow(/create\.enabled is true while guardrailIdentifier/);
    });

    test.each(["low", "NONE", "MAX", ""])("rejects promptAttackInputStrength %j", (strength) => {
        expect(
            resolve((c) => {
                guardrailBlock(c).create.promptAttackInputStrength = strength;
            })
        ).toThrow(/create\.promptAttackInputStrength must be one of \["LOW","MEDIUM","HIGH"\]/);
    });

    test.each(["LOW", "MEDIUM", "HIGH"])("accepts promptAttackInputStrength %s", (strength) => {
        expect(
            resolve((c) => {
                guardrailBlock(c).create.promptAttackInputStrength = strength;
            })
        ).not.toThrow();
    });

    test.each(["mask", "ANONYMIZE", "none", true])("rejects piiFilter %j", (filter) => {
        expect(
            resolve((c) => {
                guardrailBlock(c).create.piiFilter = filter;
            })
        ).toThrow(/create\.piiFilter must be one of \["off","anonymize","block"\]/);
    });

    test.each(["off", "anonymize", "block"])("accepts piiFilter %s", (filter) => {
        expect(
            resolve((c) => {
                guardrailBlock(c).create.piiFilter = filter;
            })
        ).not.toThrow();
    });
});
