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
 *
 * A cross-Region inference profile (`us.`, `eu.`, `apac.`, `us-gov.`, `global.`) routes each request to
 * the foundation model in one of several Regions, and Amazon Bedrock authorizes the foundation-model
 * ARN of the Region it routed to. So a prefixed id grants the configured model in any Region, and a
 * plain model id stays pinned to the deployment Region. The Region wildcard is an AwsSolutions-IAM5
 * finding, so the last block runs cdk-nag and asserts it is suppressed, not left as an error.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Annotations, Match, Template } from "aws-cdk-lib/assertions";
import { AwsSolutionsChecks } from "cdk-nag";
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

const createMockConfig = (bedrockModelId: string): Config.Config => {
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
    config.app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId = bedrockModelId;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

const buildPipeline = (
    stackName: string,
    bedrockModelId: string
): { stack: cdk.Stack; pipeline: Metadata3dLabelingConstruct } => {
    const config = createMockConfig(bedrockModelId);
    Service.SetConfig(config);

    const app = newTestApp();
    const stack = new cdk.Stack(app, stackName, {
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
    return { stack, pipeline };
};

const synthPipeline = (bedrockModelId: string = CONFIGURED_MODEL_ID): Template =>
    Template.fromStack(
        buildPipeline(
            `Metadata3dLabelingBedrockGrantStack${bedrockModelId.replace(/[^A-Za-z0-9]/g, "")}`,
            bedrockModelId
        ).pipeline
    );

/** Every statement in the template that grants a bedrock: action. */
const bedrockStatementsOf = (template: Template): any[] =>
    Object.values(template.findResources("AWS::IAM::Policy"))
        .flatMap((policy: any) => policy.Properties.PolicyDocument.Statement)
        .filter((statement: any) =>
            actionsOf(statement).some((action) => action.startsWith("bedrock:"))
        );

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
        bedrockStatements = bedrockStatementsOf(template);
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
                // Any Region: a `us.` profile routes to us-east-1, us-east-2 or us-west-2.
                `arn:aws:bedrock:*::foundation-model/${FOUNDATION_MODEL_ID}`,
                `arn:aws:bedrock:::foundation-model/${FOUNDATION_MODEL_ID}`,
                `arn:aws:bedrock:${REGION}:${ACCOUNT}:inference-profile/${CONFIGURED_MODEL_ID}`,
            ].sort()
        );
    });

    test("the foundation-model wildcard is the Region only, never the model", () => {
        const resources = bedrockStatements.flatMap(resourcesOf);
        expect(resources.filter((resource) => resource.includes("foundation-model/*"))).toEqual([]);
        expect(resources.filter((resource) => resource === "*")).toEqual([]);
    });

    test("no Bedrock resource is an inference-profile wildcard", () => {
        const wildcards = bedrockStatements
            .flatMap(resourcesOf)
            .filter((resource) => resource.includes("inference-profile/*"));
        expect(wildcards).toEqual([]);
    });
});

describe.each([
    ["us.", "us.anthropic.claude-3-7-sonnet-20250219-v1:0"],
    ["eu.", "eu.anthropic.claude-3-7-sonnet-20250219-v1:0"],
    ["apac.", "apac.anthropic.claude-3-7-sonnet-20250219-v1:0"],
    ["us-gov.", "us-gov.anthropic.claude-3-7-sonnet-20250219-v1:0"],
    ["global.", "global.anthropic.claude-3-7-sonnet-20250219-v1:0"],
])("metadataGenerationPipeline Bedrock grant, %s inference profile", (_prefix, modelId) => {
    test("grants the foundation model in every Region the profile can route to", () => {
        const resources = bedrockStatementsOf(synthPipeline(modelId)).flatMap(resourcesOf);
        expect(resources).toContain(`arn:aws:bedrock:*::foundation-model/${FOUNDATION_MODEL_ID}`);
        expect(resources).not.toContain(
            `arn:aws:bedrock:${REGION}::foundation-model/${FOUNDATION_MODEL_ID}`
        );
        expect(resources).toContain(
            `arn:aws:bedrock:${REGION}:${ACCOUNT}:inference-profile/${modelId}`
        );
    });
});

describe("metadataGenerationPipeline Bedrock grant, plain model id", () => {
    test("stays pinned to the deployment Region", () => {
        const resources = bedrockStatementsOf(synthPipeline(FOUNDATION_MODEL_ID)).flatMap(
            resourcesOf
        );
        expect(resources).toContain(
            `arn:aws:bedrock:${REGION}::foundation-model/${FOUNDATION_MODEL_ID}`
        );
        expect(resources.filter((resource) => resource.includes(":bedrock:*:"))).toEqual([]);
    });
});

describe("metadataGenerationPipeline Bedrock grant under cdk-nag", () => {
    const BEDROCK_REGION_WILDCARD = String.raw`Resource::arn:aws:bedrock:\*::foundation-model/`;
    let errors: string[];
    let suppressed: string[];

    beforeAll(() => {
        const { stack } = buildPipeline(
            "Metadata3dLabelingBedrockGrantNagStack",
            CONFIGURED_MODEL_ID
        );
        cdk.Aspects.of(stack).add(new AwsSolutionsChecks({ logIgnores: true, reports: false }));
        // A nested stack is not an artifact of its own, so its annotations are read from the
        // enclosing stack.
        const annotations = Annotations.fromStack(stack);
        const messages = (found: ReturnType<typeof annotations.findError>): string[] =>
            found.map((m) => String(m.entry.data));
        errors = messages(
            annotations.findError(
                "*",
                Match.stringLikeRegexp(String.raw`^AwsSolutions-IAM5\[${BEDROCK_REGION_WILDCARD}`)
            )
        );
        suppressed = messages(
            annotations.findInfo(
                "*",
                Match.stringLikeRegexp(
                    String.raw`^CdkNagSuppression\[${BEDROCK_REGION_WILDCARD}.*AwsSolutions-IAM5 was triggered but suppressed`
                )
            )
        );
    });

    test("[control] the Region wildcard is an IAM5 finding that the suppression matched", () => {
        // Without this the absence assertion below would hold for a grant with no wildcard at all.
        expect(suppressed.length).toBeGreaterThan(0);
    });

    test("the Region wildcard is not left as an unsuppressed error", () => {
        expect(errors).toEqual([]);
    });
});
