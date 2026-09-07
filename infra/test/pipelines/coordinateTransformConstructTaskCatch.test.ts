/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * ConstructPipelineTask must route its failures to PipelineEndTask.
 *
 * PipelineEndTask is the only state that reports on the parent workflow's waitForCallback token
 * (`pipelineEnd.py` sends task success or task failure). ConstructPipelineTask is the FIRST state, so a
 * failure there with no Catch ends the sub-execution before PipelineEndTask runs and the parent task
 * stays RUNNING for its whole 14400s taskTimeout.
 *
 * The handler's own `abort_external_workflow` covers only the failures the handler itself raises. It
 * cannot fire when the handler does not run: the 5-minute function timeout, an out-of-memory kill, a
 * `Runtime.ImportModuleError`, or an invoke fault that exhausts LambdaInvoke's service-exception
 * retries. `States.ALL` on the state covers those, which is why both routes exist and why this asserts
 * the ASL rather than the Python.
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
import { CoordinateTransformConstruct } from "../../lib/nestedStacks/pipelines/conversion/coordinateTransform/constructs/coordinateTransform-construct";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

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
    config.app.pipelines.useConversionCoordinateTransform.enabled = true;
    // Sources the container image from an ECR repository instead of a local Docker build.
    config.app.pipelines.useConversionCoordinateTransform.useCodeBuild = true;
    config.app.pipelines.useConversionCoordinateTransform.autoRegisterWithVAMS = false;
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
    const stack = new cdk.Stack(app, "CoordTransformCatchTestStack", {
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

    new CoordinateTransformConstruct(stack, "CoordinateTransformPipeline", {
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

    // Lambda ARNs are unresolved intrinsics in the Fn::Join, so each non-string part becomes a
    // placeholder. State names, Catch blocks and Next targets are all literal text.
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

describe("Coordinate Transform sub-state-machine", () => {
    test("has the states this file reasons about", () => {
        // The positive control. Every assertion below reads definition.States by name, and a renamed
        // state would make each of them read undefined rather than fail.
        expect(Object.keys(definition.States)).toEqual(
            expect.arrayContaining(["ConstructPipelineTask", "PipelineEndTask"])
        );
        expect(definition.StartAt).toEqual("ConstructPipelineTask");
    });

    test("ConstructPipelineTask catches every failure and reaches PipelineEndTask", () => {
        const catches = definition.States.ConstructPipelineTask.Catch;
        expect(catches).toBeDefined();

        const catchAll = catches.find((entry: any) => entry.ErrorEquals.includes("States.ALL"));
        expect(catchAll).toBeDefined();

        // A Lambda-level fault raises an error name the handler never sees (Lambda.Unknown,
        // States.Timeout), so a narrower ErrorEquals would leave the token unreported for exactly the
        // causes the handler cannot cover.
        expect(chainFrom(catchAll.Next)).toContain("PipelineEndTask");
    });

    test("the caught error lands beside the payload rather than replacing it", () => {
        const catchAll = definition.States.ConstructPipelineTask.Catch.find((entry: any) =>
            entry.ErrorEquals.includes("States.ALL")
        );

        // pipelineEnd reads externalSfnTaskToken from the state and decides success vs failure on the
        // presence of `error`. A ResultPath of "$" would overwrite the state with the error object,
        // leaving no token to report on.
        expect(catchAll.ResultPath).toEqual("$.error");
    });

    test("the batch job keeps its own error route to PipelineEndTask", () => {
        // The pre-existing Catch, asserted so a change that moves error handling around cannot drop it
        // while this file's new assertions still pass.
        const catchAll = definition.States.CoordTransformBatchJob.Catch.find((entry: any) =>
            entry.ErrorEquals.includes("States.ALL")
        );
        expect(catchAll).toBeDefined();
        expect(chainFrom(catchAll.Next)).toContain("PipelineEndTask");
    });
});
