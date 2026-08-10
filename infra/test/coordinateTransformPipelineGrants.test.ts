/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Locks in three properties of the Coordinate Transform pipeline that fail silently at runtime.
 *
 *   1. The container roles must carry the shared-key KMS grant. Every object the Batch job reads
 *      and writes lives in a CMK-encrypted bucket whenever useKmsCmkEncryption is on, so without
 *      it each job dies on its first S3 call with an AccessDenied logged inside the container.
 *   2. The vamsExecute lambda must be able to send a task-token failure. The workflow task waits
 *      on that callback, so a rejected input with no SendTaskFailure leaves the parent task
 *      RUNNING for the full taskTimeout instead of failing in seconds.
 *   3. The state machine timeout must ENVELOPE the batch-job task timeout. An execution-level
 *      States.Timeout is not routed through the task's Catch, so it skips pipelineEnd — the only
 *      state that releases the external task token.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../config/config";
import * as Service from "../lib/helper/service-helper";
import * as s3AssetBuckets from "../lib/helper/s3AssetBuckets";
import { storageResources } from "../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { CoordinateTransformConstruct } from "../lib/nestedStacks/pipelines/conversion/coordinateTransform/constructs/coordinateTransform-construct";
import commercialTemplate from "../config/config.template.commercial.json";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";
const EXTERNAL_KEY_ARN = `arn:aws:kms:${REGION}:210987654321:key/external-asset-bucket-key`;

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

let template: Template;
let sharedKeyLogicalId: string;

beforeAll(() => {
    const config = createMockConfig();
    Service.SetConfig(config);

    const app = new cdk.App();
    const stack = new cdk.Stack(app, "CoordTransformTestStack", {
        env: { account: ACCOUNT, region: REGION },
    });

    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const securityGroups = [new ec2.SecurityGroup(stack, "Sg", { vpc })];
    const kmsKey = new kms.Key(stack, "Key");
    const assetAuxiliaryBucket = new s3.Bucket(stack, "AuxBucket");

    // One asset bucket encrypted with an external customer managed key.
    const assetBucket = new s3.Bucket(stack, "AssetBucket");
    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(assetBucket, "/", "db", undefined, EXTERNAL_KEY_ARN, true);

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

    template = Template.fromStack(stack);
    sharedKeyLogicalId = stack.resolve(kmsKey.keyArn)["Fn::GetAtt"][0];
});

const actionsOf = (statement: any): string[] =>
    Array.isArray(statement.Action) ? statement.Action : [statement.Action];

/** Resources may be plain ARNs or unresolved intrinsics, so they stay untyped. */
const resourcesOf = (statement: any): any[] =>
    Array.isArray(statement.Resource) ? statement.Resource : [statement.Resource];

/** Inline-policy statements of the single role whose logical id contains the fragment. */
const inlineStatements = (roleIdFragment: string): any[] => {
    const roles = Object.entries(template.findResources("AWS::IAM::Role")).filter(([logicalId]) =>
        logicalId.includes(roleIdFragment)
    );
    expect(roles).toHaveLength(1);
    return ((roles[0][1] as any).Properties.Policies as any[]).flatMap(
        (policy) => policy.PolicyDocument.Statement
    );
};

/** True when a statement allows kms:Decrypt on the shared key. */
const grantsSharedKeyDecrypt = (statement: any): boolean =>
    actionsOf(statement).includes("kms:Decrypt") &&
    resourcesOf(statement).some(
        (resource) =>
            resource &&
            resource["Fn::GetAtt"] &&
            resource["Fn::GetAtt"][0] === sharedKeyLogicalId &&
            resource["Fn::GetAtt"][1] === "Arn"
    );

describe.each([["CoordTransformContainerJobRole"], ["CoordTransformContainerExecutionRole"]])(
    "%s",
    (roleIdFragment) => {
        test("can use the shared KMS key for both the input and the output bucket policy", () => {
            const statements = inlineStatements(roleIdFragment);

            // One grant per bucket policy: reading inputs and writing outputs are separate calls.
            expect(statements.filter(grantsSharedKeyDecrypt)).toHaveLength(2);
        });
    }
);

describe("Coordinate Transform container job role", () => {
    test("can use the customer managed key of an external asset bucket", () => {
        const policies = template.findResources("AWS::IAM::Policy");
        const statements = Object.entries(policies)
            .filter(([logicalId]) => logicalId.includes("CoordTransformContainerJobRole"))
            .flatMap(([, policy]) => (policy as any).Properties.PolicyDocument.Statement);

        const externalKeyGrant = statements.find((statement) =>
            resourcesOf(statement).includes(EXTERNAL_KEY_ARN)
        );
        expect(externalKeyGrant).toBeDefined();
        expect(actionsOf(externalKeyGrant)).toContain("kms:Decrypt");
    });
});

describe("Coordinate Transform vamsExecute lambda", () => {
    test("can resolve the workflow callback token in both directions", () => {
        const policies = template.findResources("AWS::IAM::Policy");
        const actions = Object.entries(policies)
            .filter(([logicalId]) => logicalId.includes("vamsExecuteCoordinateTransformPipeline"))
            .flatMap(([, policy]) => (policy as any).Properties.PolicyDocument.Statement)
            .flatMap(actionsOf);

        expect(actions).toContain("states:SendTaskFailure");
        expect(actions).toContain("states:SendTaskSuccess");
    });
});

describe("Coordinate Transform processing state machine", () => {
    test("execution timeout envelopes the batch-job task timeout", () => {
        const machines = Object.values(
            template.findResources("AWS::StepFunctions::StateMachine")
        ) as any[];
        expect(machines).toHaveLength(1);

        const definition = JSON.parse(
            machines[0].Properties.DefinitionString["Fn::Join"][1]
                .map((part: any) => (typeof part === "string" ? part : "PLACEHOLDER"))
                .join("")
        );

        const executionTimeout = definition.TimeoutSeconds;
        const taskTimeout = definition.States.CoordTransformBatchJob.TimeoutSeconds;
        expect(taskTimeout).toBeGreaterThan(0);

        // Strictly greater: an execution-level States.Timeout bypasses the task's Catch, so it
        // never reaches pipelineEnd and the external task token is never released.
        expect(executionTimeout).toBeGreaterThan(taskTimeout);
    });
});
