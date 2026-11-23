/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Isaac Lab timeout chain (issues #1324, #1669). Each mode's run is bounded by four clocks:
 *
 *   bundle taskTimeout                       (the parent workflow's callback wait, from the step's start)
 *   ExecuteBatchJobState TimeoutSecondsPath  (internal state machine, from entering the task)
 *   Batch attempt duration                   (AWS Batch, from the attempt's start; per-run `timeout`)
 *   internal state machine TimeoutSeconds    (envelopes the task, so its Catch stays reachable)
 *
 *   training   28800 / 28800 / 28800  (8 hours)
 *   evaluation 32400 / 32400 / 32400  (9 hours)
 *   state machine 36000 (10 hours), job definition 32400 (the largest mode's value)
 *
 * The workflow clocks start before the attempt (they include the RUNNABLE wait and the image pull), so
 * the bound Batch applies must be at least the mode's taskTimeout: lower, and AWS Batch stops a run
 * the workflow still allows (it was 21600 once, reported 45 minutes later as a heartbeat timeout).
 * openPipeline emits each mode's seconds; the state machine and the submit call read them.
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

const MODES = { training: 28800, evaluation: 32400 } as const;

describe("Isaac Lab timeout chain", () => {
    test("the bundles allow 8 hours for training and 9 for evaluation", () => {
        expect(bundleTaskTimeout("training")).toBe(MODES.training);
        expect(bundleTaskTimeout("evaluation")).toBe(MODES.evaluation);
    });

    test("the batch task takes its limit from the run's own mode", () => {
        const task = definition.States.ExecuteBatchJobState;
        expect(task).toBeDefined();
        expect(task.TimeoutSecondsPath).toBe("$.taskTimeoutSeconds");
        expect(task.TimeoutSeconds).toBeUndefined();
        // The value reaches the task through the Pass state and the Lambda payload.
        const pass = definition.States.PrepareExecutionState;
        expect(pass.Parameters["taskTimeoutSeconds.$"]).toBe(
            "$.openResult.Payload.taskTimeoutSeconds"
        );
        expect(task.Parameters["Payload"]["taskTimeoutSeconds.$"]).toBe("$.taskTimeoutSeconds");
    });

    test("openPipeline's per-mode seconds equal each bundle's taskTimeout", () => {
        const source = fs.readFileSync(
            path.join(SCHEMA_ROOT, "..", "lambda", "openPipeline.py"),
            "utf-8"
        );
        const match = source.match(/TASK_TIMEOUT_SECONDS = \{"train": (\d+), "evaluate": (\d+)\}/);
        expect(match).not.toBeNull();
        expect(Number(match![1])).toBe(bundleTaskTimeout("training"));
        expect(Number(match![2])).toBe(bundleTaskTimeout("evaluation"));
    });

    test("the job definition bounds one attempt at the largest mode's 9 hours", () => {
        expect(attemptSeconds()).toBe(MODES.evaluation);
    });

    test("AWS Batch never stops a run the workflow still allows", () => {
        // The guard for #1324: a bound below either workflow clock cuts the run short.
        for (const mode of ["training", "evaluation"] as const) {
            expect(attemptSeconds()).toBeGreaterThanOrEqual(bundleTaskTimeout(mode));
        }
    });

    test("the state machine envelopes the longest task so its Catch stays reachable", () => {
        expect(definition.TimeoutSeconds).toBe(36000);
        expect(definition.TimeoutSeconds).toBeGreaterThan(MODES.evaluation);
        expect(definition.TimeoutSeconds).toBeGreaterThan(MODES.training);
    });
});
