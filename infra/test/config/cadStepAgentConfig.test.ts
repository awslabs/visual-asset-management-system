/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * getConfig() rules for `app.pipelines.useGenAiCadStepAgent`. The T1 synth harness builds its config
 * without getConfig(), so these rules are exercised here through the same fs-mock pattern as
 * configPartitionValidation.test.ts.
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

const serveConfig = (configJson: unknown) => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (path: string, ...rest: unknown[]) => {
            if (typeof path === "string" && path.endsWith("config.json")) {
                return JSON.stringify(configJson);
            }
            return realReadFileSync(path, ...rest);
        }
    );
};

const templateFor = (base: unknown, region: string) => {
    const config = JSON.parse(JSON.stringify(base));
    config.env.region = region;
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    if (config.app.useAlb?.enabled) {
        config.app.useAlb.domainHost = "vams.example.com";
        config.app.useAlb.certificateArn =
            "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    }
    return config;
};

const loadConfig = (base: unknown, region: string, mutate?: (c: any) => void) => {
    const config = templateFor(base, region);
    mutate?.(config);
    serveConfig(config);
    return () => Config.getConfig(newTestApp());
};

const enableCad =
    (runtime: "agentcore" | "fargate", extra?: (cad: any, c: any) => void) => (c: any) => {
        c.app.useGlobalVpc.enabled = true;
        c.app.pipelines.useGenAiCadStepAgent.enabled = true;
        c.app.pipelines.useGenAiCadStepAgent.runtime = runtime;
        c.app.pipelines.useGenAiCadStepAgent.useCodeBuild = true;
        extra?.(c.app.pipelines.useGenAiCadStepAgent, c);
    };

describe("useGenAiCadStepAgent getConfig() rules", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
    });

    test("[control] the shipped commercial defaults are accepted with the pipeline enabled", () => {
        expect(loadConfig(commercialTemplate, "us-east-1", enableCad("agentcore"))).not.toThrow();
        expect(loadConfig(commercialTemplate, "us-east-1", enableCad("fargate"))).not.toThrow();
    });

    test("the pipeline requires a VPC", () => {
        expect(
            loadConfig(commercialTemplate, "us-east-1", (c) => {
                enableCad("agentcore")(c);
                c.app.useGlobalVpc.enabled = false;
            })
        ).toThrow(/pipelines\.useGenAiCadStepAgent/);
    });

    test("an unknown runtime is refused", () => {
        expect(
            loadConfig(
                commercialTemplate,
                "us-east-1",
                enableCad("agentcore", (cad) => (cad.runtime = "ecs"))
            )
        ).toThrow(/runtime must be "agentcore" or "fargate"/);
    });

    test("agentcore requires CodeBuild", () => {
        expect(
            loadConfig(
                commercialTemplate,
                "us-east-1",
                enableCad("agentcore", (cad) => (cad.useCodeBuild = false))
            )
        ).toThrow(/requires useCodeBuild/);
        expect(
            loadConfig(
                commercialTemplate,
                "us-east-1",
                enableCad("fargate", (cad) => (cad.useCodeBuild = false))
            )
        ).not.toThrow();
    });

    test("agentcore is refused outside the commercial partition; fargate is accepted there", () => {
        const govModel = (cad: any) =>
            (cad.bedrockModelId = "anthropic.claude-3-5-sonnet-20241022-v2:0");
        expect(
            loadConfig(govcloudTemplate, "us-gov-west-1", enableCad("agentcore", govModel))
        ).toThrow(/offered only in the commercial/);
        expect(
            loadConfig(govcloudTemplate, "us-gov-west-1", enableCad("fargate", govModel))
        ).not.toThrow();
    });

    test("an empty or commercial-prefixed Bedrock model id is refused where it cannot exist", () => {
        expect(
            loadConfig(
                commercialTemplate,
                "us-east-1",
                enableCad("fargate", (cad) => (cad.bedrockModelId = ""))
            )
        ).toThrow(/bedrockModelId is empty/);
        expect(
            loadConfig(
                govcloudTemplate,
                "us-gov-west-1",
                enableCad("fargate", (cad) => {
                    cad.bedrockModelId = "global.anthropic.claude-sonnet-4-5-20250929-v1:0";
                })
            )
        ).toThrow(/exists only in the commercial partition/);
    });

    test("the OpenAI provider needs a Secrets Manager ARN and a model id together", () => {
        expect(
            loadConfig(
                commercialTemplate,
                "us-east-1",
                enableCad("agentcore", (cad) => {
                    cad.openAi = { modelId: "gpt-x", apiKeySecretArn: "" };
                })
            )
        ).toThrow(/apiKeySecretArn must be an AWS Secrets Manager secret ARN/);
        expect(
            loadConfig(
                commercialTemplate,
                "us-east-1",
                enableCad("agentcore", (cad) => {
                    cad.openAi = { modelId: "gpt-x", apiKeySecretArn: "not-an-arn" };
                })
            )
        ).toThrow(/apiKeySecretArn must be an AWS Secrets Manager secret ARN/);
        expect(
            loadConfig(
                commercialTemplate,
                "us-east-1",
                enableCad("agentcore", (cad) => {
                    cad.openAi = {
                        modelId: "",
                        apiKeySecretArn: "arn:aws:secretsmanager:us-east-1:123456789012:secret:x",
                    };
                })
            )
        ).toThrow(/openAi\.modelId must be set/);
        expect(
            loadConfig(
                commercialTemplate,
                "us-east-1",
                enableCad("agentcore", (cad) => {
                    cad.openAi = {
                        modelId: "gpt-x",
                        apiKeySecretArn: "arn:aws:secretsmanager:us-east-1:123456789012:secret:x",
                    };
                })
            )
        ).not.toThrow();
    });

    test.each([
        ["agentCore.warmSessionSlots", (cad: any) => (cad.agentCore.warmSessionSlots = 21)],
        [
            "agentCore.idleRuntimeSessionTimeoutSeconds",
            (cad: any) => (cad.agentCore.idleRuntimeSessionTimeoutSeconds = 10),
        ],
        ["agentCore.maxLifetimeSeconds", (cad: any) => (cad.agentCore.maxLifetimeSeconds = 30000)],
        ["maxRunSeconds", (cad: any) => (cad.maxRunSeconds = 60)],
    ])("%s out of range is refused", (field, mutate) => {
        expect(loadConfig(commercialTemplate, "us-east-1", enableCad("agentcore", mutate))).toThrow(
            new RegExp(field.replace(/\./g, "\\.") + " must be an integer between")
        );
    });

    test("the idle timeout cannot exceed the lifetime, and the run budget cannot exceed it on agentcore", () => {
        expect(
            loadConfig(
                commercialTemplate,
                "us-east-1",
                enableCad("agentcore", (cad) => {
                    cad.agentCore.idleRuntimeSessionTimeoutSeconds = 7200;
                    cad.agentCore.maxLifetimeSeconds = 3600;
                })
            )
        ).toThrow(/cannot exceed maxLifetimeSeconds/);
        expect(
            loadConfig(
                commercialTemplate,
                "us-east-1",
                enableCad("agentcore", (cad) => {
                    cad.maxRunSeconds = 7200;
                    cad.agentCore.maxLifetimeSeconds = 3600;
                    cad.agentCore.idleRuntimeSessionTimeoutSeconds = 900;
                })
            )
        ).toThrow(/cannot exceed agentCore\.maxLifetimeSeconds/);
        // The same pair is fine on fargate, whose container is not bounded by a runtime session.
        expect(
            loadConfig(
                commercialTemplate,
                "us-east-1",
                enableCad("fargate", (cad) => {
                    cad.maxRunSeconds = 7200;
                    cad.agentCore.maxLifetimeSeconds = 3600;
                    cad.agentCore.idleRuntimeSessionTimeoutSeconds = 900;
                })
            )
        ).not.toThrow();
    });

    test("an older config.json without the block is backfilled with the defaults", () => {
        const config = loadConfig(commercialTemplate, "us-east-1", (c) => {
            delete c.app.pipelines.useGenAiCadStepAgent;
        })();
        expect(config.app.pipelines.useGenAiCadStepAgent).toEqual({
            enabled: false,
            runtime: "agentcore",
            useCodeBuild: true,
            autoRegisterWithVAMS: false,
            autoRegisterAutoTriggerOnFileUpload: false,
            bedrockModelId: "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
            openAi: { modelId: "", apiKeySecretArn: "" },
            allowInternetResearch: true,
            agentCore: {
                warmSessionSlots: 0,
                idleRuntimeSessionTimeoutSeconds: 900,
                maxLifetimeSeconds: 28800,
            },
            maxRunSeconds: 3600,
        });
    });

    test("a partial block keeps the operator's values and fills the rest", () => {
        const config = loadConfig(commercialTemplate, "us-east-1", (c) => {
            c.app.pipelines.useGenAiCadStepAgent = { enabled: false, runtime: "fargate" };
        })();
        const cad = config.app.pipelines.useGenAiCadStepAgent;
        expect(cad.runtime).toBe("fargate");
        expect(cad.autoRegisterWithVAMS).toBe(true);
        expect(cad.agentCore.warmSessionSlots).toBe(0);
        expect(cad.maxRunSeconds).toBe(3600);
    });
});
