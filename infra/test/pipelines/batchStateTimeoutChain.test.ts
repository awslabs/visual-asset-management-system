/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A Batch state bounds the time its job waits in the AWS Batch queue only when the state itself
 * carries a Step Functions TimeoutSeconds. The job definition's attempt duration starts when the
 * attempt starts, so a job that waits RUNNABLE is bounded by the execution timeout alone, and an
 * execution-level States.Timeout skips every Catch: PipelineEndTask never runs and the VAMS run
 * waits for its own task timeout. Each pipeline here nests
 *
 *   attempt duration < Batch state TimeoutSeconds < state machine TimeoutSeconds < bundle taskTimeout
 *
 * and routes the Batch state's Catch (which handles States.Timeout) to PipelineEndTask.
 *
 * `tasks.BatchSubmitJob` cannot express the state bound: its `taskTimeout` renders as the SubmitJob
 * attempt override and the state's TimeoutSeconds is forced undefined. The control below shows that,
 * and `StateTimeoutBatchSubmitJob` is the subclass that adds it.
 */

import * as fs from "fs";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import { Template } from "aws-cdk-lib/assertions";
import { ImageTo3dConstruct } from "../../lib/nestedStacks/pipelines/genAi/imageTo3d/constructs/imageTo3d-construct";
import { StateTimeoutBatchSubmitJob } from "../../lib/nestedStacks/pipelines/constructs/stateTimeoutBatchSubmitJob";
import {
    PcPotreeViewerConstruct,
    PC_POTREE_BATCH_STATE_TIMEOUT,
    PC_POTREE_STATE_MACHINE_TIMEOUT,
} from "../../lib/nestedStacks/pipelines/preview/pcPotreeViewer/constructs/pcPotreeViewer-construct";
import {
    CoordinateTransformConstruct,
    COORDINATE_TRANSFORM_BATCH_TIMEOUT,
    COORDINATE_TRANSFORM_STATE_MACHINE_TIMEOUT,
} from "../../lib/nestedStacks/pipelines/conversion/coordinateTransform/constructs/coordinateTransform-construct";
import { makePipelineHarness, ACCOUNT, REGION } from "../support/pipelineConstructHarness";
import { parseAsl, singleStateMachine } from "../support/asl";
import { expectBatchStateTimeoutCleanup } from "../support/batchStateTimeoutCleanup";
import { newTestApp } from "../support/testApp";

const HF_TOKEN = "hf_test_token_placeholder"; // pragma: allowlist secret

const bundleTaskTimeoutSeconds = (bundleDir: string): number => {
    const bundle = JSON.parse(
        fs.readFileSync(
            path.resolve(
                __dirname,
                "../../../backendPipelines",
                bundleDir,
                "vamsSchema/pipeline.json"
            ),
            "utf-8"
        )
    );
    return Number(bundle.executionConfig.taskTimeout);
};

const renderAlone = (build: (scope: cdk.Stack) => sfn.TaskStateBase): any => {
    const stack = new cdk.Stack(newTestApp(), "BatchStateTimeoutRenderStack", {
        env: { account: ACCOUNT, region: REGION },
    });
    const task = build(stack);
    return stack.resolve(task.toStateJson());
};

const submitJobProps = {
    jobName: "job",
    jobDefinitionArn: `arn:aws:batch:${REGION}:${ACCOUNT}:job-definition/job`,
    jobQueueArn: `arn:aws:batch:${REGION}:${ACCOUNT}:job-queue/queue`,
    integrationPattern: sfn.IntegrationPattern.RUN_JOB,
};

describe("StateTimeoutBatchSubmitJob", () => {
    test("renders the state's TimeoutSeconds and no SubmitJob attempt override", () => {
        const rendered = renderAlone(
            (scope) =>
                new StateTimeoutBatchSubmitJob(scope, "Job", {
                    ...submitJobProps,
                    stateTimeout: cdk.Duration.minutes(90),
                })
        );
        expect(rendered.TimeoutSeconds).toBe(5400);
        expect(rendered.Parameters.Timeout).toBeUndefined();
        expect(JSON.stringify(rendered.Resource)).toContain("batch:submitJob.sync");
    });

    test("[control] a plain BatchSubmitJob renders no state bound, even with taskTimeout", () => {
        const rendered = renderAlone(
            (scope) =>
                new tasks.BatchSubmitJob(scope, "Job", {
                    ...submitJobProps,
                    taskTimeout: sfn.Timeout.duration(cdk.Duration.hours(1)),
                })
        );
        expect(rendered.TimeoutSeconds).toBeUndefined();
        expect(rendered.Parameters.Timeout.AttemptDurationSeconds).toBe(3600);
    });

    test("refuses a zero bound", () => {
        expect(() =>
            renderAlone(
                (scope) =>
                    new StateTimeoutBatchSubmitJob(scope, "Job", {
                        ...submitJobProps,
                        stateTimeout: cdk.Duration.seconds(0),
                    })
            )
        ).toThrow(/positive/);
    });
});

/** Every state whose Resource is a `.sync` Batch submit, by name. */
const batchStates = (definition: any): [string, any][] =>
    Object.entries(definition.States as Record<string, any>).flatMap(([name, state]) => {
        if (state.Type === "Choice") return [];
        const resource = JSON.stringify(state.Resource ?? "");
        return resource.includes("batch:submitJob.sync") ? [[name, state]] : [];
    });

/** The longest attempt duration among the template's job definitions. */
const maxAttemptSeconds = (template: Template): number =>
    Math.max(
        ...(Object.values(template.findResources("AWS::Batch::JobDefinition")) as any[]).map(
            (j) => j.Properties.Timeout.AttemptDurationSeconds
        )
    );

describe("Image to 3D timeout chain", () => {
    let template: Template;
    let definition: any;

    beforeAll(() => {
        const h = makePipelineHarness("ImageTo3dStateTimeoutStack", (c) => {
            c.app.pipelines.useImageTo3d.enabled = true;
            c.app.pipelines.useImageTo3d.huggingFaceToken = HF_TOKEN;
            c.app.pipelines.useImageTo3d.autoRegisterWithVAMS = false;
        });
        new ImageTo3dConstruct(h.stack, "ImageTo3dPipeline", {
            config: h.config,
            storageResources: h.storage,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
            modelCacheBucket: h.modelCacheBucket,
            efsFileSystem: h.efsFileSystem,
            efsSecurityGroup: h.efsSecurityGroup,
            codeBuildImages: {
                stable3dgen: {
                    repository: new ecr.Repository(h.stack, "ImageTo3dStable3dgenRepo"),
                    imageUri: "123456789012.dkr.ecr.us-east-1.amazonaws.com/stable3dgen:latest",
                    codeBuildProjectName: "imageto3d-stable3dgen",
                },
            },
        });
        template = Template.fromStack(h.stack);
        definition = parseAsl(singleStateMachine(template));
    });

    test("every model's Batch state is bounded between the attempt and the state machine", () => {
        const states = batchStates(definition);
        expect(states.length).toBeGreaterThan(0);
        expect(maxAttemptSeconds(template)).toBe(7200);
        expect(definition.TimeoutSeconds).toBe(10800);
        for (const [name, state] of states) {
            expect([name, state.TimeoutSeconds]).toEqual([name, 9900]);
            expect(state.TimeoutSeconds).toBeGreaterThan(maxAttemptSeconds(template));
            // Room for ConstructPipelineTask and PipelineEndTask, 5-minute Lambdas each.
            expect(definition.TimeoutSeconds - state.TimeoutSeconds).toBeGreaterThanOrEqual(600);
            expect(state.Parameters.Timeout).toBeUndefined();
        }
    });

    test("a Batch state timeout is caught and reaches PipelineEndTask", () => {
        for (const [, state] of batchStates(definition)) {
            const catchers = state.Catch as any[];
            expect(catchers.some((c) => c.ErrorEquals.includes("States.ALL"))).toBe(true);
            const handler = definition.States[catchers[0].Next];
            expect(handler.Next).toBe("PipelineEndTask");
        }
    });

    test("the bundle taskTimeout envelopes the state machine", () => {
        expect(bundleTaskTimeoutSeconds("genAi/imageTo3d")).toBeGreaterThan(
            definition.TimeoutSeconds
        );
    });

    test("pipelineEnd can end the run's job on either queue after a state timeout", () => {
        expectBatchStateTimeoutCleanup(template);
    });
});

describe("Point cloud Potree viewer timeout chain", () => {
    let template: Template;
    let definition: any;

    beforeAll(() => {
        const h = makePipelineHarness("PcPotreeViewerStateTimeoutStack", (c) => {
            c.app.pipelines.usePreviewPcPotreeViewer.enabled = true;
            c.app.pipelines.usePreviewPcPotreeViewer.autoRegisterWithVAMS = false;
        });
        const nested = new PcPotreeViewerConstruct(h.stack, "PcPotreeViewerPipeline", {
            config: h.config,
            storageResources: h.storage,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        });
        template = Template.fromStack(nested);
        definition = parseAsl(singleStateMachine(template));
    });

    test("both Batch states are bounded, with the attempts no longer than the bound", () => {
        const states = batchStates(definition);
        expect(states.map(([name]) => name).sort()).toEqual([
            "PdalConverterBatchJob",
            "PotreeConverterBatchJob",
        ]);
        expect(definition.TimeoutSeconds).toBe(PC_POTREE_STATE_MACHINE_TIMEOUT.toSeconds());
        for (const [name, state] of states) {
            expect([name, state.TimeoutSeconds]).toEqual([
                name,
                PC_POTREE_BATCH_STATE_TIMEOUT.toSeconds(),
            ]);
            expect(definition.TimeoutSeconds - state.TimeoutSeconds).toBeGreaterThanOrEqual(600);
            const handler = definition.States[(state.Catch as any[])[0].Next];
            expect(handler.Next).toBe("PipelineEndTask");
        }
        expect(maxAttemptSeconds(template)).toBeLessThanOrEqual(
            PC_POTREE_BATCH_STATE_TIMEOUT.toSeconds()
        );
    });

    test("the bundle taskTimeout envelopes the state machine", () => {
        // Strictly greater: the parent's wait starts before the execution does.
        expect(bundleTaskTimeoutSeconds("preview/pcPotreeViewer")).toBeGreaterThan(
            definition.TimeoutSeconds
        );
    });

    test("pipelineEnd can end the run's job on the PDAL or Potree queue after a state timeout", () => {
        expectBatchStateTimeoutCleanup(template);
    });
});

describe("Coordinate transform timeout chain", () => {
    let template: Template;
    let definition: any;

    beforeAll(() => {
        const h = makePipelineHarness("CoordTransformStateTimeoutStack", (c) => {
            c.app.pipelines.useConversionCoordinateTransform.enabled = true;
            // Sources the container image from an ECR repository instead of a local Docker build.
            c.app.pipelines.useConversionCoordinateTransform.useCodeBuild = true;
            c.app.pipelines.useConversionCoordinateTransform.autoRegisterWithVAMS = false;
        });
        new CoordinateTransformConstruct(h.stack, "CoordinateTransformPipeline", {
            config: h.config,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            assetAuxiliaryBucket: h.assetAuxiliaryBucket,
            storageResources: h.storage,
            kmsKey: h.kmsKey,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        });
        template = Template.fromStack(h.stack);
        definition = parseAsl(singleStateMachine(template));
    });

    test("the token wait sits inside the state machine, and the attempt is no longer than the wait", () => {
        const wait = definition.States.CoordTransformBatchJob.TimeoutSeconds;
        expect(wait).toBe(COORDINATE_TRANSFORM_BATCH_TIMEOUT.toSeconds());
        expect(definition.TimeoutSeconds).toBe(
            COORDINATE_TRANSFORM_STATE_MACHINE_TIMEOUT.toSeconds()
        );
        expect(definition.TimeoutSeconds - wait).toBeGreaterThanOrEqual(600);
        expect(maxAttemptSeconds(template)).toBeLessThanOrEqual(wait);
    });

    test("the bundle taskTimeout envelopes the state machine", () => {
        // Before, the 4-hour token wait equalled the 4-hour bundle wait that started first.
        expect(bundleTaskTimeoutSeconds("conversion/coordinateTransform")).toBeGreaterThan(
            definition.TimeoutSeconds
        );
    });

    test("the submitting token state does not retry, so one run submits one Batch job", () => {
        // executeBatchJob submits on every call; CDK's default Retry on Lambda service exceptions
        // would start a second container on the same task token.
        const state = definition.States.CoordTransformBatchJob;
        expect(state.Retry).toBeUndefined();
        expect(state.Catch.some((c: any) => c.ErrorEquals.includes("States.ALL"))).toBe(true);
    });

    test("pipelineEnd can end the job executeBatchJob submitted after the token wait times out", () => {
        expectBatchStateTimeoutCleanup(template);
    });
});
