/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A stack carrying the shared resources every pipeline construct takes as props, so a per-pipeline
 * assertion synthesizes one construct rather than the whole app. Imports (layer, ECR image, EFS)
 * stand in for the resources the pipeline builder stack would create.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "./testApp";

export const ACCOUNT = "123456789012";
export const REGION = "us-east-1";

/** Commercial-template config with a fixed synth environment; registration is off unless a test turns it on. */
export const createMockConfig = (): Config.Config => {
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

export interface PipelineHarness {
    stack: cdk.Stack;
    config: Config.Config;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    securityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: lambda.LayerVersion;
    storage: storageResources;
    kmsKey: kms.Key;
    assetAuxiliaryBucket: s3.Bucket;
    modelCacheBucket: s3.Bucket;
    /** Imported so the GPU constructs do not stand up a real EFS mount target set. */
    efsFileSystem: any;
    efsSecurityGroup: ec2.SecurityGroup;
    /** Imported image so the CodeBuild-backed constructs skip a local Docker build. */
    codeBuildImage: { repository: ecr.IRepository; tag: string };
}

export const makePipelineHarness = (
    id: string,
    mutate?: (c: Config.Config) => void
): PipelineHarness => {
    const config = createMockConfig();
    mutate?.(config);
    Service.SetConfig(config);

    const app = newTestApp();
    const stack = new cdk.Stack(app, id, { env: { account: ACCOUNT, region: REGION } });

    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const securityGroups = [new ec2.SecurityGroup(stack, "Sg", { vpc })];

    // Module-level registry with no reset of its own: a second synth in the same process otherwise
    // collides on the construct id it derives from the previous stack name.
    const assetBucket = new s3.Bucket(stack, "AssetBucket");
    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(assetBucket, "/", "db", undefined, undefined, true);

    const kmsKey = new kms.Key(stack, "Key");
    const assetAuxiliaryBucket = new s3.Bucket(stack, "AuxBucket");
    const storage = {
        encryption: { kmsKey },
        s3: {
            assetAuxiliaryBucket,
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
        },
        eventBridge: { orchestrationBus: new events.EventBus(stack, "Bus") },
    } as unknown as storageResources;

    return {
        stack,
        config,
        vpc,
        subnets: vpc.privateSubnets,
        securityGroups,
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        storage,
        kmsKey,
        assetAuxiliaryBucket,
        modelCacheBucket: new s3.Bucket(stack, "ModelCacheBucket"),
        efsFileSystem: { fileSystemId: "fs-0123456789abcdef0" },
        efsSecurityGroup: new ec2.SecurityGroup(stack, "EfsSg", { vpc }),
        codeBuildImage: {
            repository: ecr.Repository.fromRepositoryName(stack, "Repo", "vams-test-repo"),
            tag: "0123456789abcdef0123456789abcdef01234567",
        },
    };
};
