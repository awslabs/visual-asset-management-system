/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Video SOP/BOM sub-state-machine routes every failure to PipelineEndTask and submits its Batch job
 * as a `.sync` task whose container holds the inner task token.
 *
 * PipelineEndTask is the only state that reports on the parent workflow's waitForCallback token, so a
 * state that fails without a Catch leaves the parent task RUNNING for its whole 30600 s taskTimeout.
 * ConstructPipelineTask is the FIRST state and its handler's own `abort_external_workflow` covers only the
 * failures it raises itself — not the function timeout, an out-of-memory kill or an import error — which
 * is why `States.ALL` on the state is asserted from the ASL rather than from the Python.
 *
 * The Batch task's shape is asserted too: `jobName` and `command` come from constructPipeline's output,
 * and `TASK_TOKEN` is the `$$.Task.Token` the container completes the `.sync` task with. A wrong path in
 * any of these fails at the first execution, not at synth.
 *
 * EndStatesChoice fails the execution on `$.error` AND on a callback payload that does not carry the
 * container's reporter marker. pipelineEnd fails the external token on a missing marker but returns the
 * event without `$.error`, so without the second arm the sub-execution would record SUCCEEDED for a run
 * the parent workflow was told had failed.
 */

import * as fs from "fs";
import * as path from "path";
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
import { VideoSopBomConstruct } from "../../lib/nestedStacks/pipelines/genAi/videoSopBom/constructs/videoSopBom-construct";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

/** The marker the container puts in its SendTaskSuccess payload; pipelineEnd requires the same string. */
const REPORTER_MARKER = "video_sop_bom_pipeline";

const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = REGION;
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.useGlobalVpc.enabled = true;
    config.app.useGlobalVpc.useForAllLambdas = false;
    config.app.useKmsCmkEncryption.enabled = true;
    config.app.pipelines.useGenAiVideoSopBom.enabled = true;
    // Sources the container image from an ECR repository instead of a local Docker build.
    config.app.pipelines.useGenAiVideoSopBom.useCodeBuild = true;
    config.app.pipelines.useGenAiVideoSopBom.autoRegisterWithVAMS = false;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

/** The synthesized Amazon States Language definition of the pipeline's state machine. */
let definition: any;

beforeAll(() => {
    const config = createMockConfig();
    Service.SetConfig(config);

    const app = newTestApp();
    const stack = new cdk.Stack(app, "VideoSopBomCatchTestStack", {
        env: { account: ACCOUNT, region: REGION },
    });

    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const securityGroups = [new ec2.SecurityGroup(stack, "Sg", { vpc })];
    const kmsKey = new kms.Key(stack, "Key");
    const assetAuxiliaryBucket = new s3.Bucket(stack, "AuxBucket");

    const assetBucket = new s3.Bucket(stack, "AssetBucket");
    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(assetBucket, "/", "db", undefined, undefined, true);

    const storage = {
        encryption: { kmsKey },
        s3: {
            assetAuxiliaryBucket,
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
        },
        eventBridge: { orchestrationBus: new events.EventBus(stack, "Bus") },
    } as unknown as storageResources;

    new VideoSopBomConstruct(stack, "VideoSopBomPipeline", {
        config,
        vpc,
        pipelineSubnets: vpc.privateSubnets,
        pipelineSecurityGroups: securityGroups,
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        assetAuxiliaryBucket,
        storageResources: storage,
        kmsKey,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
    });

    const template = Template.fromStack(stack);
    const machines = Object.values(
        template.findResources("AWS::StepFunctions::StateMachine")
    ) as any[];
    expect(machines).toHaveLength(1);

    // Lambda and Batch ARNs are unresolved intrinsics inside the Fn::Join, so each non-string part
    // becomes a placeholder. State names, Catch blocks, JSON paths and Next targets are literal text.
    definition = JSON.parse(
        machines[0].Properties.DefinitionString["Fn::Join"][1]
            .map((part: any) => (typeof part === "string" ? part : "PLACEHOLDER"))
            .join("")
    );
});

/** The state names reachable from `stateName` by following Next, in order. */
const chainFrom = (stateName: string): string[] => {
    const visited: string[] = [];
    let current: string | undefined = stateName;
    while (current && !visited.includes(current)) {
        visited.push(current);
        current = definition.States[current]?.Next;
    }
    return visited;
};

describe("Video SOP/BOM sub-state-machine", () => {
    test("has exactly the eight states the pipeline defines, starting at ConstructPipelineTask", () => {
        // The positive control: every assertion below reads definition.States by name, and a renamed
        // state would make each of them read undefined rather than fail.
        expect(Object.keys(definition.States).sort()).toEqual([
            "ConstructPipelineTask",
            "EndStatesChoice",
            "HandleBatchError",
            "HandleConstructPipelineError",
            "PipelineEndTask",
            "PipelineFailed",
            "PipelineSuccess",
            "VideoSopBomBatchJob",
        ]);
        expect(definition.StartAt).toEqual("ConstructPipelineTask");
        expect(definition.States.ConstructPipelineTask.Next).toEqual("VideoSopBomBatchJob");
        // outputPath "$.Payload": the handler's return value REPLACES the state, which is why
        // constructPipeline re-emits batchJobName and externalSfnTaskToken at its top level.
        expect(definition.States.ConstructPipelineTask.OutputPath).toEqual("$.Payload");
        expect(definition.States.ConstructPipelineTask.ResultPath).toBeUndefined();
    });

    test("ConstructPipelineTask catches every failure and reaches PipelineEndTask beside the payload", () => {
        const catches = definition.States.ConstructPipelineTask.Catch;
        expect(catches).toBeDefined();
        const catchAll = catches.find((entry: any) => entry.ErrorEquals.includes("States.ALL"));
        expect(catchAll).toBeDefined();
        expect(chainFrom(catchAll.Next)).toContain("PipelineEndTask");
        // pipelineEnd reads externalSfnTaskToken from the state and decides success vs failure on the
        // presence of `error`; a ResultPath of "$" would overwrite the state with the error object.
        expect(catchAll.ResultPath).toEqual("$.error");
    });

    test("VideoSopBomBatchJob is a .sync Batch submission fed from the constructPipeline output", () => {
        const task = definition.States.VideoSopBomBatchJob;
        expect(task.Type).toEqual("Task");
        expect(task.Resource).toMatch(/:states:::batch:submitJob\.sync$/);
        expect(task.Parameters["JobName.$"]).toEqual("$.batchJobName");
        expect(task.Parameters.ContainerOverrides["Command.$"]).toEqual("$.definitionCommand");
        // The callback output lands beside the state, so externalSfnTaskToken survives for pipelineEnd.
        expect(task.ResultPath).toEqual("$.batchResult");
        expect(task.Next).toEqual("PipelineEndTask");
    });

    test("the container receives the inner task token and the Region, and nothing else", () => {
        const env = definition.States.VideoSopBomBatchJob.Parameters.ContainerOverrides
            .Environment as any[];
        expect(env.map((entry) => entry.Name).sort()).toEqual(["AWS_REGION", "TASK_TOKEN"]);
        // The container completes the .sync task on this token; the external token is never in the job.
        expect(JSON.stringify(env.find((entry) => entry.Name === "TASK_TOKEN"))).toContain(
            "$$.Task.Token"
        );
    });

    test("the Batch task keeps its own error route to PipelineEndTask", () => {
        const catchAll = definition.States.VideoSopBomBatchJob.Catch.find((entry: any) =>
            entry.ErrorEquals.includes("States.ALL")
        );
        expect(catchAll).toBeDefined();
        expect(catchAll.ResultPath).toEqual("$.error");
        expect(chainFrom(catchAll.Next)).toContain("PipelineEndTask");
    });

    test("PipelineEndTask feeds the Choice, which fails on $.error or a missing reporter marker", () => {
        expect(definition.States.PipelineEndTask.Next).toEqual("EndStatesChoice");
        // inputPath "$" hands pipelineEnd the whole state (externalSfnTaskToken, batchResult, error);
        // outputPath "$.Payload" makes the handler's return value the state the Choice reads, which
        // is why pipelineEnd returns its input unchanged — a resultPath here would nest the return
        // under a key and the Choice's `$.error` would read a different document.
        expect(definition.States.PipelineEndTask.InputPath).toEqual("$");
        expect(definition.States.PipelineEndTask.OutputPath).toEqual("$.Payload");
        expect(definition.States.PipelineEndTask.ResultPath).toBeUndefined();
        const choice = definition.States.EndStatesChoice;
        expect(choice.Choices).toHaveLength(1);
        expect(choice.Choices[0].Next).toEqual("PipelineFailed");
        // One Or over three arms. The StringEquals arm is guarded by its own IsPresent: a comparison
        // against a path that does not exist is a States.Runtime error, which no Catch can intercept.
        const arms = choice.Choices[0].Or as any[];
        expect(arms).toHaveLength(3);
        expect(arms).toContainEqual({ Variable: "$.error", IsPresent: true });
        expect(arms).toContainEqual({ Variable: "$.batchResult.reporter", IsPresent: false });
        expect(arms).toContainEqual({
            And: [
                { Variable: "$.batchResult.reporter", IsPresent: true },
                {
                    Not: {
                        Variable: "$.batchResult.reporter",
                        StringEquals: REPORTER_MARKER,
                    },
                },
            ],
        });
        expect(choice.Default).toEqual("PipelineSuccess");
        expect(definition.States.PipelineFailed.Type).toEqual("Fail");
        expect(definition.States.PipelineSuccess.Type).toEqual("Succeed");
    });

    test("[control] the reporter marker the Choice compares against is the one pipelineEnd requires", () => {
        // The Choice and the handler each hold the literal; a drift between them would pass every
        // state-machine assertion above and mis-route real runs.
        const handler = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../../backendPipelines/genAi/videoSopBom/lambda/pipelineEnd.py"
            ),
            "utf-8"
        );
        expect(handler).toContain(`REPORTER_MARKER = "${REPORTER_MARKER}"`);
    });
});
