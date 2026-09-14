/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The GenAI 3D metadata labeling handler may invoke exactly the model the operator configured.
 *
 * `metadataGenerationPipeline` invokes Amazon Bedrock through the id in
 * `pipelines.useGenAiMetadata3dLabeling.bedrockModelId`. That id is known at synthesis, so the grant
 * names it: the two foundation-model ARNs carry the id with any cross-Region profile prefix removed,
 * and the inference-profile ARN carries the configured id as-is. An `inference-profile/*` resource
 * would let the handler invoke every profile in the account, and it is asserted absent here.
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
import { Metadata3dLabelingConstruct } from "../../lib/nestedStacks/pipelines/genAi/metadata3dLabeling/constructs/metadata3dLabeling-construct";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

/** A cross-Region profile id, so the prefix-stripped and as-configured forms differ. */
const CONFIGURED_MODEL_ID = "us.anthropic.claude-3-7-sonnet-20250219-v1:0";
const FOUNDATION_MODEL_ID = "anthropic.claude-3-7-sonnet-20250219-v1:0";

const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = REGION;
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.useGlobalVpc.enabled = true;
    config.app.useGlobalVpc.useForAllLambdas = false;
    config.app.pipelines.useGenAiMetadata3dLabeling.enabled = true;
    config.app.pipelines.useGenAiMetadata3dLabeling.autoRegisterWithVAMS = false;
    config.app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId = CONFIGURED_MODEL_ID;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

const synthPipeline = (): Template => {
    const config = createMockConfig();
    Service.SetConfig(config);

    const app = newTestApp();
    const stack = new cdk.Stack(app, "Metadata3dLabelingBedrockGrantStack", {
        env: { account: ACCOUNT, region: REGION },
    });

    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const securityGroups = [new ec2.SecurityGroup(stack, "Sg", { vpc })];

    const assetBucket = new s3.Bucket(stack, "AssetBucket");
    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(assetBucket, "/", "db", undefined, undefined, true);

    const storage = {
        encryption: { kmsKey: new kms.Key(stack, "Key") },
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
        },
        eventBridge: { orchestrationBus: new events.EventBus(stack, "Bus") },
    } as unknown as storageResources;

    // The construct IS a NestedStack, so its resources are in its own template.
    const pipeline = new Metadata3dLabelingConstruct(stack, "Metadata3dLabelingPipeline", {
        config,
        storageResources: storage,
        vpc,
        pipelineSubnets: vpc.privateSubnets,
        pipelineSecurityGroups: securityGroups,
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
    });
    return Template.fromStack(pipeline);
};

const actionsOf = (statement: any): string[] =>
    Array.isArray(statement.Action) ? statement.Action : [statement.Action];

const resourcesOf = (statement: any): string[] =>
    Array.isArray(statement.Resource) ? statement.Resource : [statement.Resource];

describe("metadataGenerationPipeline Bedrock grant", () => {
    let template: Template;
    /** Every statement, across the template, that grants a bedrock: action. */
    let bedrockStatements: any[];

    beforeAll(() => {
        template = synthPipeline();
        bedrockStatements = Object.values(template.findResources("AWS::IAM::Policy"))
            .flatMap((policy: any) => policy.Properties.PolicyDocument.Statement)
            .filter((statement: any) =>
                actionsOf(statement).some((action) => action.startsWith("bedrock:"))
            );
    });

    test("[control] the handler is granted Bedrock invoke actions", () => {
        // Without this the absence assertion below would hold for a template granting nothing.
        expect(bedrockStatements).toHaveLength(1);
        expect(actionsOf(bedrockStatements[0]).sort()).toEqual([
            "bedrock:InvokeModel",
            "bedrock:InvokeModelWithResponseStream",
        ]);
    });

    test("names exactly the configured model: two foundation-model ARNs and its inference profile", () => {
        expect(resourcesOf(bedrockStatements[0]).sort()).toEqual(
            [
                `arn:aws:bedrock:${REGION}::foundation-model/${FOUNDATION_MODEL_ID}`,
                `arn:aws:bedrock:::foundation-model/${FOUNDATION_MODEL_ID}`,
                `arn:aws:bedrock:${REGION}:${ACCOUNT}:inference-profile/${CONFIGURED_MODEL_ID}`,
            ].sort()
        );
    });

    test("no Bedrock resource is an inference-profile wildcard", () => {
        const wildcards = bedrockStatements
            .flatMap(resourcesOf)
            .filter((resource) => resource.includes("inference-profile/*"));
        expect(wildcards).toEqual([]);
    });
});
