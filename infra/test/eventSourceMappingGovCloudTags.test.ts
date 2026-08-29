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
import { newTestApp } from "./support/testApp";
import { batchSizeOffenders } from "./support/sqsEventSourceBounds";

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
    const app = newTestApp();
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

/** Logical id an `Fn::GetAtt`-shaped value points at, or undefined for any other shape. */
const getAttTarget = (value: any): string | undefined =>
    value && typeof value === "object" && Array.isArray(value["Fn::GetAtt"])
        ? String(value["Fn::GetAtt"][0])
        : undefined;

/** Logical id a `Ref`-shaped value points at, or undefined for any other shape. */
const refTarget = (value: any): string | undefined =>
    value && typeof value === "object" && typeof value.Ref === "string" ? value.Ref : undefined;

describe("workflowTriggerDispatch SQS event source mapping", () => {
    test("omits Tags when govCloud is enabled", () => {
        const mappings = eventSourceMappings(synthDispatchFunction(true));

        // Guards the assertion below: an empty list would make "no Tags" vacuously true.
        expect(mappings).toHaveLength(1);
        expect(mappings[0].Properties).not.toHaveProperty("Tags");
        // The mapping must still be wired up, not merely untagged.
        expect(mappings[0].Properties.EventSourceArn).toBeDefined();
        expect(mappings[0].Properties.FunctionName).toBeDefined();
        // Batch size is bounded from ABOVE rather than pinned: a smaller batch is a strictly safer
        // change, while a larger one widens the whole-batch failure this mapping has (it declares no
        // FunctionResponseTypes, so one failing record redelivers the batch and re-fires the triggers
        // of every record in it). See support/sqsEventSourceBounds.ts. The batching window is left
        // unasserted there for a reason worth not rediscovering: CDK's own 300 s cap guarantees any
        // upper bound that could be written for it, and a bound nothing can fail asserts nothing.
        expect(
            batchSizeOffenders([
                { at: "govCloud dispatch mapping", properties: mappings[0].Properties },
            ])
        ).toEqual([]);
    });

    test("keeps the commercial mapping intact, proving the govCloud assertion is load-bearing", () => {
        const template = synthDispatchFunction(false);
        const mappings = eventSourceMappings(template);

        expect(mappings).toHaveLength(1);
        // Positive control: CDK really does propagate the stack tags onto this mapping, so the
        // govCloud test above is asserting the absence of something that would otherwise be there.
        expect(mappings[0].Properties).toHaveProperty("Tags");

        // Asserted through the REFERENCE rather than with a defined-ness check on EventSourceArn /
        // FunctionName. On this branch the mapping comes from `addEventSource(new SqsEventSource(q))`,
        // which gives CDK no way to omit either property -- so `toBeDefined()` on them is an
        // assertion no implementation of this branch could fail. Consuming the wrong queue is a
        // mistake it CAN make, and the dispatch buffer's dead-letter queue sits right beside the
        // source queue in the same template.
        const queues = template.findResources("AWS::SQS::Queue");
        const sourceQueueId = getAttTarget(mappings[0].Properties.EventSourceArn);
        expect(Object.keys(queues).filter((id) => id === sourceQueueId)).toHaveLength(1);
        // The SOURCE queue, not its DLQ: only the queue that redrives carries a RedrivePolicy, so
        // this distinguishes the two rather than merely finding "some queue".
        expect(queues[sourceQueueId as string].Properties).toHaveProperty("RedrivePolicy");

        const functions = template.findResources("AWS::Lambda::Function");
        const targetFunctionId = refTarget(mappings[0].Properties.FunctionName);
        expect(Object.keys(functions).filter((id) => id === targetFunctionId)).toHaveLength(1);
        // The dispatch Lambda, not the executeWorkflowV2 function this builder is handed: both are
        // Lambdas in this template, so an unqualified "it is a function" check would pass either way.
        expect(functions[targetFunctionId as string].Properties.Handler).toBe(
            "handlers.workflows.sfn.workflowTriggerDispatch.lambda_handler"
        );

        // Same bound on the branch that builds the mapping the other way, because the two branches
        // configure it separately and only the emitted template shows they agree.
        expect(
            batchSizeOffenders([
                { at: "commercial dispatch mapping", properties: mappings[0].Properties },
            ])
        ).toEqual([]);
    });

    test("both branches configure the mapping identically apart from Tags", () => {
        // The two branches restate batchSize and maxBatchingWindow separately, so they can drift
        // silently: a change made to one is invisible in the other partition until deploy. Compared
        // as whole property sets rather than field by field, so a property added to only one branch
        // is caught as well.
        const govCloud = eventSourceMappings(synthDispatchFunction(true))[0].Properties;
        const commercial = { ...eventSourceMappings(synthDispatchFunction(false))[0].Properties };
        delete commercial.Tags;

        // Anti-vacuity: an empty pair of property sets would satisfy the comparison below.
        expect(Object.keys(govCloud).sort()).toEqual(
            expect.arrayContaining(["BatchSize", "EventSourceArn", "FunctionName"])
        );
        expect(commercial).toEqual(govCloud);
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
