/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Locks in the task-token callback grant on the 3D Preview Thumbnail vamsExecute lambda. The
 * workflow task waits on that callback, so a failure with no SendTaskFailure leaves the parent
 * task RUNNING for the full taskTimeout instead of failing in seconds.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../config/config";
import * as Service from "../lib/helper/service-helper";
import * as s3AssetBuckets from "../lib/helper/s3AssetBuckets";
import { buildVamsExecutePreview3dThumbnailPipelineFunction } from "../lib/nestedStacks/pipelines/preview/3dThumbnail/lambdaBuilder/preview3dThumbnailFunctions";
import commercialTemplate from "../config/config.template.commercial.json";

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
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

let template: Template;

beforeAll(() => {
    const config = createMockConfig();
    Service.SetConfig(config);

    const app = new cdk.App();
    const stack = new cdk.Stack(app, "Preview3dThumbnailTestStack", {
        env: { account: ACCOUNT, region: REGION },
    });

    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const kmsKey = new kms.Key(stack, "Key");
    const assetAuxiliaryBucket = new s3.Bucket(stack, "AuxBucket");

    const assetBucket = new s3.Bucket(stack, "AssetBucket");
    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(assetBucket, "/", "db");

    const openPipelineLambdaFunction = lambda.Function.fromFunctionName(
        stack,
        "OpenPipeline",
        "openPipeline"
    );

    buildVamsExecutePreview3dThumbnailPipelineFunction(
        stack,
        lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        assetAuxiliaryBucket,
        openPipelineLambdaFunction,
        config,
        vpc,
        vpc.privateSubnets,
        kmsKey
    );

    template = Template.fromStack(stack);
});

const actionsOf = (statement: any): string[] =>
    Array.isArray(statement.Action) ? statement.Action : [statement.Action];

describe("3D Preview Thumbnail vamsExecute lambda", () => {
    test("can resolve the workflow callback token in both directions", () => {
        const policies = template.findResources("AWS::IAM::Policy");
        const actions = Object.entries(policies)
            .filter(([logicalId]) => logicalId.includes("vamsExecutePreview3dThumbnailPipeline"))
            .flatMap(([, policy]) => (policy as any).Properties.PolicyDocument.Statement)
            .flatMap(actionsOf);

        expect(actions).toContain("states:SendTaskFailure");
        expect(actions).toContain("states:SendTaskSuccess");
    });

    test("the callback grant is scoped to the deployment account and region", () => {
        const policies = template.findResources("AWS::IAM::Policy");
        const statement = Object.entries(policies)
            .filter(([logicalId]) => logicalId.includes("vamsExecutePreview3dThumbnailPipeline"))
            .flatMap(([, policy]) => (policy as any).Properties.PolicyDocument.Statement)
            .find((entry: any) => actionsOf(entry).includes("states:SendTaskFailure"));

        expect(statement.Resource).toEqual(`arn:aws:states:${REGION}:${ACCOUNT}:*`);
    });
});
