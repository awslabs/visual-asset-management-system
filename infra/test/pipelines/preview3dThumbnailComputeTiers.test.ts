/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The 3D preview thumbnail pipeline sizes its compute to the input: one Fargate job definition, and
 * two Batch states behind a Choice on the `computeTier` constructPipeline returns, each overriding
 * vCPU and memory with its tier's configured size, setting `VAMS_COMPUTE_TIER`, and bounding its own
 * attempt. The state machine timeout covers the longer tier and the registered pipeline's
 * `taskTimeout` covers the state machine, so neither orchestration gives up on a job Batch still allows
 * to run.
 *
 * Asserted on the synthesized ASL and job definition, because the size a job runs at is the one AWS
 * Batch receives.
 */

import * as fs from "fs";
import * as path from "path";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import {
    Preview3dThumbnailConstruct,
    PREVIEW_3D_THUMBNAIL_LARGE_TIER_TIMEOUT,
    PREVIEW_3D_THUMBNAIL_STANDARD_TIER_TIMEOUT,
    PREVIEW_3D_THUMBNAIL_STATE_MACHINE_TIMEOUT,
} from "../../lib/nestedStacks/pipelines/preview/3dThumbnail/constructs/preview3dThumbnail-construct";
import { makePipelineHarness } from "../support/pipelineConstructHarness";
import { lambdaEnvironmentsByHandler, parseAsl, singleStateMachine } from "../support/asl";

const BUNDLE_PIPELINE_JSON = path.resolve(
    __dirname,
    "../../../backendPipelines/preview/3dThumbnail/vamsSchema/pipeline.json"
);
const STANDARD_STATE = "Preview3dThumbnailBatchJob";
const LARGE_STATE = "Preview3dThumbnailLargeBatchJob";

/** Synthesize the construct with `mutate` applied to the thumbnail's config block. */
function synth(
    id: string,
    mutate?: (thumbnail: Config.Config["app"]["pipelines"]["usePreview3dThumbnail"]) => void
): Template {
    const h = makePipelineHarness(id, (c) => {
        c.app.pipelines.usePreview3dThumbnail.enabled = true;
        c.app.pipelines.usePreview3dThumbnail.autoRegisterWithVAMS = false;
        mutate?.(c.app.pipelines.usePreview3dThumbnail);
    });
    const nested = new Preview3dThumbnailConstruct(h.stack, "Preview3dThumbnailPipeline", {
        config: h.config,
        storageResources: h.storage,
        vpc: h.vpc,
        pipelineSubnets: h.subnets,
        pipelineSecurityGroups: h.securityGroups,
        lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
    });
    return Template.fromStack(nested);
}

const resourceRequirements = (state: any): Record<string, string> =>
    Object.fromEntries(
        (state.Parameters.ContainerOverrides.ResourceRequirements as any[]).map((r) => [
            r.Type,
            r.Value,
        ])
    );

const environment = (state: any): Record<string, string> =>
    Object.fromEntries(
        (state.Parameters.ContainerOverrides.Environment as any[]).map((e) => [
            e.Name,
            e.Value ?? e["Value.$"],
        ])
    );

const singleJobDefinition = (template: Template): any => {
    const jobDefinitions = Object.values(template.findResources("AWS::Batch::JobDefinition"));
    expect(jobDefinitions).toHaveLength(1);
    return (jobDefinitions[0] as any).Properties;
};

describe("3D preview thumbnail compute tiers (template defaults)", () => {
    let template: Template;
    let asl: any;

    beforeAll(() => {
        template = synth("Preview3dThumbnailTiersStack");
        asl = parseAsl(singleStateMachine(template));
    });

    test("constructPipeline hands off to a Choice on $.computeTier", () => {
        expect(asl.States.ConstructPipelineTask.Next).toBe("ComputeTierChoice");
        const choice = asl.States.ComputeTierChoice;
        expect(choice.Type).toBe("Choice");
        expect(choice.Choices).toEqual([
            { Variable: "$.computeTier", StringEquals: "large", Next: LARGE_STATE },
        ]);
        expect(choice.Default).toBe(STANDARD_STATE);
    });

    test("both tiers submit the one job definition to the one queue and end the same way", () => {
        const standard = asl.States[STANDARD_STATE];
        const large = asl.States[LARGE_STATE];
        for (const state of [standard, large]) {
            expect(state.Type).toBe("Task");
            expect(state.Resource).toMatch(/:states:::batch:submitJob\.sync$/);
            expect(state.Parameters["JobName.$"]).toBe("$.jobName");
            expect(state.Parameters.ContainerOverrides["Command.$"]).toBe("$.definition");
            expect(state.Next).toBe("PipelineEndTask");
            expect(state.Catch).toEqual([
                { ErrorEquals: ["States.ALL"], ResultPath: "$.error", Next: "HandleBatchError" },
            ]);
        }
        expect(standard.Parameters.JobDefinition).toEqual(large.Parameters.JobDefinition);
        expect(standard.Parameters.JobQueue).toEqual(large.Parameters.JobQueue);
        expect(Object.keys(template.findResources("AWS::Batch::JobQueue"))).toHaveLength(1);
        singleJobDefinition(template);
    });

    test("each tier overrides vCPU and memory with its configured Fargate size", () => {
        expect(resourceRequirements(asl.States[STANDARD_STATE])).toEqual({
            VCPU: "4",
            MEMORY: String(16 * 1024),
        });
        expect(resourceRequirements(asl.States[LARGE_STATE])).toEqual({
            VCPU: "16",
            MEMORY: String(120 * 1024),
        });
    });

    test("each tier tells the container its tier and keeps the task token and Region", () => {
        const standard = environment(asl.States[STANDARD_STATE]);
        const large = environment(asl.States[LARGE_STATE]);
        expect(standard.VAMS_COMPUTE_TIER).toBe("standard");
        expect(large.VAMS_COMPUTE_TIER).toBe("large");
        for (const env of [standard, large]) {
            expect(env.TASK_TOKEN).toBe("$$.Task.Token");
            expect(env.AWS_REGION).toBeDefined();
        }
    });

    test("each tier bounds its own attempt; the state machine and the job definition cover the longer", () => {
        const standardSeconds = PREVIEW_3D_THUMBNAIL_STANDARD_TIER_TIMEOUT.toSeconds();
        const largeSeconds = PREVIEW_3D_THUMBNAIL_LARGE_TIER_TIMEOUT.toSeconds();
        const machineSeconds = PREVIEW_3D_THUMBNAIL_STATE_MACHINE_TIMEOUT.toSeconds();
        expect([standardSeconds, largeSeconds, machineSeconds]).toEqual([3600, 10800, 14400]);

        expect(asl.States[STANDARD_STATE].Parameters.Timeout).toEqual({
            AttemptDurationSeconds: standardSeconds,
        });
        expect(asl.States[LARGE_STATE].Parameters.Timeout).toEqual({
            AttemptDurationSeconds: largeSeconds,
        });
        expect(asl.TimeoutSeconds).toBe(machineSeconds);
        expect(singleJobDefinition(template).Timeout).toEqual({
            AttemptDurationSeconds: largeSeconds,
        });
    });

    test("the registered pipeline's taskTimeout covers the state machine timeout", () => {
        // The parent workflow waits taskTimeout for the callback; shorter than the state machine, it
        // fails the step while a large-tier job is still allowed to run.
        const bundle = JSON.parse(fs.readFileSync(BUNDLE_PIPELINE_JSON, "utf-8"));
        expect(Number(bundle.executionConfig.taskTimeout)).toBeGreaterThanOrEqual(
            PREVIEW_3D_THUMBNAIL_STATE_MACHINE_TIMEOUT.toSeconds()
        );
    });

    test("the job definition registers the standard size with 200 GiB of ephemeral storage", () => {
        const container = singleJobDefinition(template).ContainerProperties;
        expect(container.EphemeralStorage).toEqual({ SizeInGiB: 200 });
        expect(
            Object.fromEntries(
                (container.ResourceRequirements as any[]).map((r) => [r.Type, r.Value])
            )
        ).toEqual({ VCPU: "4", MEMORY: String(16 * 1024) });
    });

    test("the Fargate compute environment admits a 16 vCPU job", () => {
        const environments = Object.values(
            template.findResources("AWS::Batch::ComputeEnvironment")
        ) as any[];
        expect(environments).toHaveLength(1);
        const resources = environments[0].Properties.ComputeResources;
        expect(resources.Type).toBe("FARGATE");
        expect(resources.MaxvCpus).toBeGreaterThanOrEqual(16);
    });

    test("openPipeline receives the large-tier threshold in bytes", () => {
        const envs = lambdaEnvironmentsByHandler(template, "openPipeline.lambda_handler");
        expect(envs).toHaveLength(1);
        expect(envs[0].LARGE_TIER_THRESHOLD_BYTES).toBe(String(2 * 1024 ** 3));
    });
});

describe("3D preview thumbnail compute tiers (configured sizes)", () => {
    let template: Template;
    let asl: any;

    beforeAll(() => {
        template = synth("Preview3dThumbnailTiersConfiguredStack", (thumbnail) => {
            thumbnail.computeTiers = {
                standard: { vcpu: 2, memoryGiB: 8 },
                large: { vcpu: 8, memoryGiB: 60 },
            };
            thumbnail.largeTierThresholdGiB = 0.5;
        });
        asl = parseAsl(singleStateMachine(template));
    });

    test("the overrides and the registered size follow the configuration", () => {
        expect(resourceRequirements(asl.States[STANDARD_STATE])).toEqual({
            VCPU: "2",
            MEMORY: String(8 * 1024),
        });
        expect(resourceRequirements(asl.States[LARGE_STATE])).toEqual({
            VCPU: "8",
            MEMORY: String(60 * 1024),
        });
        const container = singleJobDefinition(template).ContainerProperties;
        expect(
            Object.fromEntries(
                (container.ResourceRequirements as any[]).map((r) => [r.Type, r.Value])
            )
        ).toEqual({ VCPU: "2", MEMORY: String(8 * 1024) });
    });

    test("a fractional threshold reaches openPipeline as whole bytes", () => {
        const [env] = lambdaEnvironmentsByHandler(template, "openPipeline.lambda_handler");
        expect(env.LARGE_TIER_THRESHOLD_BYTES).toBe(String(512 * 1024 ** 2));
    });
});
