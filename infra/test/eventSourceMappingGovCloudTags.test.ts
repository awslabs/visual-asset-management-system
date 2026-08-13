/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../config/config";
import * as Service from "../lib/helper/service-helper";
import { storageResources } from "../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { buildWorkflowTriggerDispatchFunction } from "../lib/lambdaBuilder/workflowFunctions";
import commercialTemplate from "../config/config.template.commercial.json";

/**
 * GovCloud and EU Sovereign Lambda reject Tags on AWS::Lambda::EventSourceMapping
 * ("Invalid request provided: Tags not supported in request"), which fails stack creation and
 * rolls back the whole core stack. CDK's high-level `fun.addEventSource()` stamps the stack tags
 * onto the mapping, so every event source mapping must be constructed directly and have Tags
 * deleted when govCloud.enabled is set. The EU Sovereign config template also sets
 * govCloud.enabled, so that one flag covers both partitions.
 *
 * The commercial case is asserted too: it is what proves the govCloud assertion is meaningful
 * rather than passing because the mapping is absent or untagged everywhere.
 */

const mockConfig = (govCloud: boolean): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = "123456789012";
    config.env.region = govCloud ? "us-gov-west-1" : "us-east-1";
    config.env.partition = govCloud ? "aws-us-gov" : "aws";
    config.env.coreStackName = "vams-test";
    config.app.baseStackName = "vams-test";
    config.app.govCloud.enabled = govCloud;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test/resourceNames";
    return config;
};

const buildStorageResources = (stack: cdk.Stack): storageResources => {
    const table = (id: string) =>
        new dynamodb.Table(stack, id, {
            partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
        });
    const logGroup = (id: string) => new logs.LogGroup(stack, id);
    return {
        encryption: {},
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
            accessLogsBucket: new s3.Bucket(stack, "AccessLogsBucket"),
        },
        eventBridge: {
            orchestrationBus: new events.EventBus(stack, "OrchestrationBus"),
            eventSourcePrefix: "vams.test",
        },
        cloudWatchAuditLogGroups: {
            authentication: logGroup("AuditAuthentication"),
            authorization: logGroup("AuditAuthorization"),
            fileUpload: logGroup("AuditFileUpload"),
            fileDownload: logGroup("AuditFileDownload"),
            fileDownloadStreamed: logGroup("AuditFileDownloadStreamed"),
            authOther: logGroup("AuditAuthOther"),
            authChanges: logGroup("AuditAuthChanges"),
            actions: logGroup("AuditActions"),
            errors: logGroup("AuditErrors"),
        },
        dynamo: {
            workflowTriggersStorageTable: table("WorkflowTriggersTable"),
            assetStorageTable: table("AssetTable"),
            s3AssetBucketsStorageTable: table("S3AssetBucketsTable"),
            workflowStorageTableV2: table("V2WorkflowTable"),
            authEntitiesStorageTable: table("AuthEntitiesTable"),
            constraintsStorageTable: table("ConstraintsTable"),
            userRolesStorageTable: table("UserRolesTable"),
            rolesStorageTable: table("RolesTable"),
        },
    } as unknown as storageResources;
};

const synthDispatchFunction = (govCloud: boolean): Template => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, "TestStack", {
        env: { account: "123456789012", region: govCloud ? "us-gov-west-1" : "us-east-1" },
    });
    const config = mockConfig(govCloud);
    Service.SetConfig(config);

    // core-stack.ts tags the stack ("vams:stackname", plus the config's common environment tags),
    // and CDK propagates those onto taggable L1s — including the event source mapping created by
    // addEventSource(). Without tagging the test stack the same way there is nothing for the
    // deletion override to remove, so the govCloud assertion would pass either way.
    cdk.Tags.of(stack).add("SolutionName", "AWSVisualAssetManagementSystem");
    cdk.Tags.of(stack).add("vams:stackname", stack.stackName);

    const layer = lambda.LayerVersion.fromLayerVersionArn(
        stack,
        "CommonLayer",
        `arn:${config.env.partition}:lambda:${config.env.region}:123456789012:layer:vams-common:1`
    ) as lambda.LayerVersion;
    const executeWorkflowV2 = new lambda.Function(stack, "executeWorkflowV2", {
        code: lambda.Code.fromInline("def lambda_handler(e, c): pass"),
        handler: "index.lambda_handler",
        runtime: lambda.Runtime.PYTHON_3_12,
    });

    buildWorkflowTriggerDispatchFunction(
        stack,
        layer,
        buildStorageResources(stack),
        executeWorkflowV2,
        config,
        undefined as unknown as ec2.IVpc,
        []
    );

    return Template.fromStack(stack);
};

const eventSourceMappings = (template: Template) =>
    Object.values(template.findResources("AWS::Lambda::EventSourceMapping"));

describe("workflowTriggerDispatch SQS event source mapping", () => {
    test("omits Tags when govCloud is enabled", () => {
        const mappings = eventSourceMappings(synthDispatchFunction(true));

        // Guards the assertion below: an empty list would make "no Tags" vacuously true.
        expect(mappings).toHaveLength(1);
        expect(mappings[0].Properties).not.toHaveProperty("Tags");
        // The mapping must still be wired up, not merely untagged.
        expect(mappings[0].Properties.EventSourceArn).toBeDefined();
        expect(mappings[0].Properties.FunctionName).toBeDefined();
        expect(mappings[0].Properties.BatchSize).toBe(10);
        expect(mappings[0].Properties.MaximumBatchingWindowInSeconds).toBe(3);
    });

    test("keeps the commercial mapping intact, proving the govCloud assertion is load-bearing", () => {
        const mappings = eventSourceMappings(synthDispatchFunction(false));

        expect(mappings).toHaveLength(1);
        // Positive control: CDK really does propagate the stack tags onto this mapping, so the
        // govCloud test above is asserting the absence of something that would otherwise be there.
        expect(mappings[0].Properties).toHaveProperty("Tags");
        expect(mappings[0].Properties.EventSourceArn).toBeDefined();
        expect(mappings[0].Properties.FunctionName).toBeDefined();
        expect(mappings[0].Properties.BatchSize).toBe(10);
        expect(mappings[0].Properties.MaximumBatchingWindowInSeconds).toBe(3);
    });

    test("grants the dispatch function SQS consume permissions in both partitions", () => {
        for (const govCloud of [true, false]) {
            const rendered = JSON.stringify(
                Object.values(synthDispatchFunction(govCloud).findResources("AWS::IAM::Policy"))
            );
            for (const action of [
                "sqs:ReceiveMessage",
                "sqs:DeleteMessage",
                "sqs:GetQueueAttributes",
            ]) {
                expect(rendered).toContain(action);
            }
        }
    });
});
