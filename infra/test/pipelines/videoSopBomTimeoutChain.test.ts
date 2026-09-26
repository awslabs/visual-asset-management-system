/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Video SOP/BOM timeout chain has three members, each enforced by a different service, and every
 * link must hold:
 *
 *   job definition AttemptDurationSeconds 21600 (AWS Batch, from the attempt's startedAt)
 *     < state machine TimeoutSeconds 28800 (Step Functions, from start_execution)
 *       < bundle taskTimeout "30600" (the parent workflow's callback wait)
 *
 * The 2 h gap between the first two absorbs a RUNNABLE wait behind the Fargate On-Demand vCPU quota:
 * the execution clock runs while the job is queued, the attempt clock does not. An execution-level
 * States.Timeout bypasses every Catch, so it skips PipelineEndTask and the external token is released
 * only by the parent's own taskTimeout with a generic cause.
 *
 * The Batch task itself carries NO Step Functions timeout. `tasks.BatchSubmitJob` renders a `taskTimeout`
 * prop as the SubmitJob `Timeout.AttemptDurationSeconds` parameter and forces the state's TimeoutSeconds
 * to undefined, so a value written there would silently become a second attempt bound rather than a task
 * clock. The state's `TimeoutSeconds` is therefore undefined BY CONSTRUCTION whatever props are given, and
 * asserting it proves nothing; the falsifiable half is `Parameters.Timeout`, and the control at the end of
 * this file renders a `BatchSubmitJob` WITH a `taskTimeout` to show that this is the path a regression
 * would populate.
 */

import * as fs from "fs";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
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

const BUNDLE_PIPELINE_JSON = path.resolve(
    __dirname,
    "../../../backendPipelines/genAi/videoSopBom/vamsSchema/pipeline.json"
);

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

let template: Template;
let definition: any;

beforeAll(() => {
    const config = createMockConfig();
    Service.SetConfig(config);

    const app = newTestApp();
    const stack = new cdk.Stack(app, "VideoSopBomTimeoutTestStack", {
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

    template = Template.fromStack(stack);
    const machines = Object.values(
        template.findResources("AWS::StepFunctions::StateMachine")
    ) as any[];
    expect(machines).toHaveLength(1);
    definition = JSON.parse(
        machines[0].Properties.DefinitionString["Fn::Join"][1]
            .map((part: any) => (typeof part === "string" ? part : "PLACEHOLDER"))
            .join("")
    );
});

/** Attempt bound of the one Fargate job definition in the template. */
const attemptSeconds = (): number => {
    const jobDefinitions = Object.values(
        template.findResources("AWS::Batch::JobDefinition")
    ) as any[];
    expect(jobDefinitions).toHaveLength(1);
    return jobDefinitions[0].Properties.Timeout.AttemptDurationSeconds;
};

describe("Video SOP/BOM timeout chain", () => {
    test("the job definition bounds one attempt at six hours", () => {
        expect(attemptSeconds()).toBe(21600);
    });

    test("the state machine timeout is eight hours and envelopes the attempt", () => {
        expect(definition.TimeoutSeconds).toBe(28800);
        // Strictly greater: the container sends the task-token callback, so the state machine must
        // outlive the job rather than being cut short by States.Timeout.
        expect(definition.TimeoutSeconds).toBeGreaterThan(attemptSeconds());
    });

    test("the bundle taskTimeout is the config constant and envelopes the state machine", () => {
        const bundle = JSON.parse(fs.readFileSync(BUNDLE_PIPELINE_JSON, "utf-8"));
        // A string in the bundle, as every shipped pipeline.json spells it.
        expect(bundle.executionConfig.taskTimeout).toBe("30600");
        expect(Number(bundle.executionConfig.taskTimeout)).toBe(
            Config.VIDEO_SOP_BOM_BUNDLE_TASK_TIMEOUT_SECONDS
        );
        expect(Number(bundle.executionConfig.taskTimeout)).toBeGreaterThan(
            definition.TimeoutSeconds
        );
        // No heartbeat anywhere in the chain: the container reports only its terminal outcome.
        expect(bundle.executionConfig.taskHeartbeatTimeout).toBe("");
    });

    test("the Batch task carries neither a Step Functions timeout nor a SubmitJob attempt override", () => {
        const task = definition.States.VideoSopBomBatchJob;
        expect(task).toBeDefined();
        // Undefined by construction (see the header); kept as documentation of the rendered shape, not
        // as the guard.
        expect(task.TimeoutSeconds).toBeUndefined();
        expect(task.TimeoutSecondsPath).toBeUndefined();
        expect(task.HeartbeatSeconds).toBeUndefined();
        // The guard. A `taskTimeout` prop would render here, as a second attempt bound competing with
        // the job definition's; the control below shows this is the path it lands on.
        expect(task.Parameters.Timeout).toBeUndefined();
    });

    test("[control] a taskTimeout prop lands at Parameters.Timeout, the path the negative above reads", () => {
        // A BatchSubmitJob built WITH the prop the pipeline must not carry, rendered on its own: the
        // attempt override appears under Parameters.Timeout and the state's TimeoutSeconds stays
        // undefined even now — which is what makes the two halves of the negative above mean what
        // they say.
        const scratch = new cdk.Stack(newTestApp(), "VideoSopBomTimeoutControlStack", {
            env: { account: ACCOUNT, region: REGION },
        });
        const control = new tasks.BatchSubmitJob(scratch, "ControlSubmitJob", {
            jobName: "control",
            jobDefinitionArn: `arn:aws:batch:${REGION}:${ACCOUNT}:job-definition/control`,
            jobQueueArn: `arn:aws:batch:${REGION}:${ACCOUNT}:job-queue/control`,
            integrationPattern: sfn.IntegrationPattern.RUN_JOB,
            taskTimeout: sfn.Timeout.duration(cdk.Duration.hours(1)),
        });
        const rendered = scratch.resolve(control.toStateJson()) as any;
        expect(rendered.Parameters.Timeout.AttemptDurationSeconds).toBe(3600);
        expect(rendered.TimeoutSeconds).toBeUndefined();
    });
});
