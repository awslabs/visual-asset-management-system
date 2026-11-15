/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Isaac Lab timeout chain (issue #1324). Four bounds apply to one training run, each enforced by
 * a different clock:
 *
 *   training bundle taskTimeout "28800"           (the parent workflow's callback wait, from the step's start)
 *   ExecuteBatchJobState TimeoutSeconds 28800      (internal state machine, from entering the task)
 *   job definition AttemptDurationSeconds 28800    (AWS Batch, from the attempt's start)
 *   internal state machine TimeoutSeconds 32400    (envelopes the task, so its Catch stays reachable)
 *
 * The workflow promises 8 hours, and the two workflow clocks start before the attempt (they include the
 * RUNNABLE wait and the image pull), so the job definition's bound must be at least the training
 * taskTimeout: lower, and AWS Batch stops a run the workflow still allows. It was 21600 (6 hours), so a
 * long run was stopped and then reported, 45 minutes later, as a heartbeat timeout. Evaluation shares
 * the job definition and has the shorter bundle taskTimeout "7200".
 */

import * as fs from "fs";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { IsaacLabTrainingConstruct } from "../../lib/nestedStacks/pipelines/simulation/isaacLabTraining/constructs/isaacLabTraining-construct";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

const SCHEMA_ROOT = path.resolve(
    __dirname,
    "../../../backendPipelines/simulation/isaacLabTraining/vamsSchema"
);

const bundleTaskTimeout = (mode: "training" | "evaluation"): number => {
    const bundle = JSON.parse(
        fs.readFileSync(path.join(SCHEMA_ROOT, mode, "pipeline.json"), "utf-8")
    );
    return Number(bundle.executionConfig.taskTimeout);
};

const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = REGION;
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.useGlobalVpc.enabled = true;
    config.app.useGlobalVpc.useForAllLambdas = false;
    config.app.pipelines.useIsaacLabTraining.enabled = true;
    config.app.pipelines.useIsaacLabTraining.acceptNvidiaEula = true;
    config.app.pipelines.useIsaacLabTraining.autoRegisterWithVAMS = false;
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
    const stack = new cdk.Stack(app, "IsaacLabTimeoutTestStack", {
        env: { account: ACCOUNT, region: REGION },
    });

    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const kmsKey = new kms.Key(stack, "Key");

    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(new s3.Bucket(stack, "AssetBucket"), "/", "db");

    const storage = {
        encryption: { kmsKey },
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
        },
        eventBridge: { orchestrationBus: new events.EventBus(stack, "Bus") },
    } as unknown as storageResources;

    new IsaacLabTrainingConstruct(stack, "IsaacLabTrainingPipeline", {
        config,
        vpc,
        pipelineSubnets: vpc.privateSubnets,
        pipelineSubnetsIsolated: vpc.isolatedSubnets,
        pipelineSecurityGroups: [new ec2.SecurityGroup(stack, "Sg", { vpc })],
        storageResources: storage,
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflowV2",
        // Sources the container image from ECR so synth does not run a local Docker build.
        codeBuildImage: {
            repository: ecr.Repository.fromRepositoryName(stack, "Repo", "isaaclab"),
            tag: "0123456789abcdef0123456789abcdef01234567", // pragma: allowlist secret
        },
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

/** Attempt bound of the one Isaac Lab job definition in the template. */
const attemptSeconds = (): number => {
    const jobDefinitions = Object.values(
        template.findResources("AWS::Batch::JobDefinition")
    ) as any[];
    expect(jobDefinitions).toHaveLength(1);
    return jobDefinitions[0].Properties.Timeout.AttemptDurationSeconds;
};

describe("Isaac Lab timeout chain", () => {
    test("the bundles allow 8 hours for training and 2 for evaluation", () => {
        expect(bundleTaskTimeout("training")).toBe(28800);
        expect(bundleTaskTimeout("evaluation")).toBe(7200);
    });

    test("the job definition bounds one attempt at 8 hours", () => {
        expect(attemptSeconds()).toBe(28800);
    });

    test("AWS Batch never stops a run the workflow still allows", () => {
        // The guard for #1324: a bound below either workflow clock cuts the run short.
        expect(attemptSeconds()).toBeGreaterThanOrEqual(bundleTaskTimeout("training"));
        expect(attemptSeconds()).toBeGreaterThanOrEqual(bundleTaskTimeout("evaluation"));
        const task = definition.States.ExecuteBatchJobState;
        expect(task).toBeDefined();
        expect(task.TimeoutSeconds).toBe(28800);
        expect(attemptSeconds()).toBeGreaterThanOrEqual(task.TimeoutSeconds);
    });

    test("the state machine envelopes the Batch task so its Catch stays reachable", () => {
        const task = definition.States.ExecuteBatchJobState;
        expect(definition.TimeoutSeconds).toBe(32400);
        expect(definition.TimeoutSeconds).toBeGreaterThan(task.TimeoutSeconds);
    });
});
