/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every GenAI pipeline's Amazon Bedrock model id is checked against the partition's cross-Region
 * inference-profile prefixes (#1526).
 *
 * The GenAI CAD STEP agent, Video SOP/BOM extraction and Image to 3D Blender agent checks refused only
 * `global.` and `us.` outside the commercial partition, so an `eu.`, `apac.`, `au.`, `jp.` or `in.`
 * id passed synthesis in AWS GovCloud (US) or the AWS European Sovereign Cloud and failed at its first
 * call, a `us-gov.` id passed in the commercial partition, and a `us.` id was refused in GovCloud,
 * where some model cards list it. They now apply the same rule as the SYSTEM GenAI metadata pipeline,
 * from `BEDROCK_INFERENCE_PROFILE_PREFIXES`, and so does the ConfigBuilder.
 *
 * The expected outcomes are written out here, prefix by prefix, rather than read from the table, so a
 * wrong table entry fails the test instead of moving the expectation with it.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import { newTestApp } from "../support/testApp";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

type Outcome = "accepted" | "warns" | "refused";

const MODEL = "anthropic.claude-sonnet-4-5-20250929-v1:0";
const PREFIXES = ["global", "us", "us-gov", "eu", "apac", "au", "jp", "in"] as const;

/** What each partition does with each prefix (and with a bare model id). */
const EXPECTED: Record<string, Record<(typeof PREFIXES)[number] | "bare", Outcome>> = {
    aws: {
        global: "accepted",
        us: "accepted",
        "us-gov": "refused",
        eu: "accepted",
        apac: "accepted",
        au: "accepted",
        jp: "accepted",
        in: "accepted",
        bare: "accepted",
    },
    "aws-us-gov": {
        global: "refused",
        us: "warns",
        "us-gov": "accepted",
        eu: "refused",
        apac: "refused",
        au: "refused",
        jp: "refused",
        in: "refused",
        bare: "accepted",
    },
    "aws-eusc": {
        global: "refused",
        us: "refused",
        "us-gov": "refused",
        eu: "refused",
        apac: "refused",
        au: "refused",
        jp: "refused",
        in: "refused",
        bare: "accepted",
    },
};

const PARTITIONS = [
    { partition: "aws", region: "us-east-1", template: commercialTemplate, profile: "commercial" },
    {
        partition: "aws-us-gov",
        region: "us-gov-west-1",
        template: govcloudTemplate,
        profile: "govcloud",
    },
    {
        partition: "aws-eusc",
        region: "eusc-de-east-1",
        template: eusovereignTemplate,
        profile: "eusovereign",
    },
] as const;

const CASES = PARTITIONS.flatMap((p) =>
    [...PREFIXES, "bare" as const].map(
        (prefix) =>
            [
                p.partition,
                prefix,
                prefix === "bare" ? MODEL : `${prefix}.${MODEL}`,
                EXPECTED[p.partition][prefix],
            ] as const
    )
);

const PREFIX_MESSAGE = /cross-Region inference-profile prefix exists only in the/;

/** The shared check's outcome for one id. */
function sharedOutcome(modelId: string, partition: string): Outcome {
    const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    try {
        (Config as any).validateBedrockProfilePrefix("field", modelId, partition);
        return warn.mock.calls.length > 0 ? "warns" : "accepted";
    } catch (error) {
        if (PREFIX_MESSAGE.test(String((error as Error).message))) return "refused";
        throw error;
    } finally {
        warn.mockRestore();
    }
}

describe("the shared profile-prefix check", () => {
    test.each(CASES)("in %s, a %s id (%s) is %s", (partition, _prefix, modelId, expected) => {
        expect(sharedOutcome(modelId, partition)).toBe(expected);
    });
});

/** Serve `configJson` for config.json; delegate every other path to the real fs. */
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

/** Each pipeline: how to enable it with a model id, and the model-id message's field path. */
const PIPELINES = [
    {
        name: "GenAI CAD STEP agent",
        field: "pipelines.useGenAiCadStepAgent.bedrockModelId",
        rulePrefix: "cad-step-agent-",
        enable: (c: any, modelId: string, partition: string) => {
            const cad = c.app.pipelines.useGenAiCadStepAgent;
            cad.enabled = true;
            // The AgentCore runtime exists only in the commercial partition.
            cad.runtime = partition === "aws" ? "agentcore" : "fargate";
            cad.useCodeBuild = partition === "aws";
            cad.bedrockModelId = modelId;
        },
    },
    {
        name: "Video SOP/BOM extraction",
        field: "pipelines.useGenAiVideoSopBom.bedrockModelId",
        rulePrefix: "video-sop-bom-",
        enable: (c: any, modelId: string) => {
            c.app.pipelines.useGenAiVideoSopBom.enabled = true;
            c.app.pipelines.useGenAiVideoSopBom.bedrockModelId = modelId;
        },
    },
    {
        name: "Image to 3D Blender agent",
        field: "useImageTo3d.models.blenderagent.bedrockModelId",
        rulePrefix: "imageto3d-blenderagent-",
        enable: (c: any, modelId: string) => {
            const imageTo3d = c.app.pipelines.useImageTo3d;
            imageTo3d.enabled = true;
            imageTo3d.huggingFaceToken = "hf_test_token";
            imageTo3d.models.blenderagent.enabled = true;
            imageTo3d.models.blenderagent.useStabilityImageEditing = false;
            imageTo3d.models.blenderagent.bedrockModelId = modelId;
        },
    },
] as const;

// The Video SOP/BOM pipeline is refused outright outside the commercial and GovCloud partitions,
// before its model id is read.
const VIDEO_PARTITION_MESSAGE = /pipelines\.useGenAiVideoSopBom is enabled while deploying to the/;

/** getConfig()'s outcome for `pipeline` with `modelId` in `partition`. */
function getConfigOutcome(
    pipeline: (typeof PIPELINES)[number],
    partition: string,
    modelId: string
): Outcome | "pipeline-refused" {
    const target = PARTITIONS.find((p) => p.partition === partition)!;
    const config = JSON.parse(JSON.stringify(target.template));
    config.env.region = target.region;
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    config.app.useGlobalVpc.enabled = true;
    if (config.app.useAlb?.enabled) {
        config.app.useAlb.domainHost = "vams.example.com";
        config.app.useAlb.certificateArn = `arn:${partition}:acm:${target.region}:123456789012:certificate/11111111-2222-3333-4444-555555555555`;
    }
    pipeline.enable(config, modelId, partition);
    serveConfig(config);
    const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    try {
        Config.getConfig(newTestApp());
        const warned = warn.mock.calls.some(
            (call) => String(call[0]).includes(pipeline.field) && String(call[0]).includes(modelId)
        );
        return warned ? "warns" : "accepted";
    } catch (error) {
        const message = String((error as Error).message);
        if (VIDEO_PARTITION_MESSAGE.test(message)) return "pipeline-refused";
        if (message.includes(pipeline.field) && PREFIX_MESSAGE.test(message)) return "refused";
        throw error;
    } finally {
        warn.mockRestore();
    }
}

describe.each(PIPELINES)("getConfig(): the $name model id", (pipeline) => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    test.each(CASES)("in %s, a %s id (%s) is %s", (partition, _prefix, modelId, expected) => {
        const outcome = getConfigOutcome(pipeline, partition, modelId);
        if (pipeline.rulePrefix === "video-sop-bom-" && partition === "aws-eusc") {
            expect(outcome).toBe("pipeline-refused");
        } else {
            expect(outcome).toBe(expected);
        }
    });
});

/** The ConfigBuilder's prefix-rule outcome for `pipeline`: an error refuses, a warning warns. */
function builderOutcome(
    pipeline: (typeof PIPELINES)[number],
    profile: "commercial" | "govcloud" | "eusovereign",
    region: string,
    partition: string,
    modelId: string
): Outcome {
    const cfg = makeDefaultConfig(profile) as any;
    cfg.env.region = region;
    cfg.app.useGlobalVpc.enabled = true;
    pipeline.enable(cfg, modelId, partition);
    const rules = evaluateRules(cfg).filter(
        (rule) => rule.id.startsWith(pipeline.rulePrefix) && /prefix/.test(rule.id)
    );
    if (rules.some((rule) => rule.severity === "error")) return "refused";
    if (rules.some((rule) => rule.severity === "warning")) return "warns";
    return "accepted";
}

describe.each(PIPELINES)("ConfigBuilder: the $name model id", (pipeline) => {
    test.each(CASES)("in %s, a %s id (%s) is %s", (partition, _prefix, modelId, expected) => {
        const target = PARTITIONS.find((p) => p.partition === partition)!;
        expect(builderOutcome(pipeline, target.profile, target.region, partition, modelId)).toBe(
            expected
        );
    });
});

describe("one prefix table", () => {
    test("[control] the table names every prefix this test expects, and no other", () => {
        expect(Object.keys(Config.BEDROCK_INFERENCE_PROFILE_PREFIXES).sort()).toEqual(
            [...PREFIXES].sort()
        );
    });
});
