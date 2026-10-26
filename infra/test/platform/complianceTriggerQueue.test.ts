/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The compliance trigger consumes the asset and file indexer topics through its own SQS queue:
 * KMS-encrypted with the deployment key and TLS-only like the indexer queues, a dead-letter queue
 * with a bounded receive count, a visibility timeout of at least six times the function timeout, a
 * message-body filter policy on each topic subscription, and a batched, concurrency-capped event
 * source that reads `batchItemFailures`. The only send access to the queue is each topic's, and the
 * trigger's role consumes it without a wildcard resource.
 *
 * Built from `buildComplianceTriggerQueue` and `buildComplianceTriggerFunction` against a stand-in
 * storage stack, so it runs in seconds; `complianceTriggerQueueNoOpenSearch.test.ts` asserts the same wiring survives a full
 * synth with no OpenSearch.
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sns from "aws-cdk-lib/aws-sns";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import {
    buildComplianceTriggerFunction,
    buildComplianceTriggerQueue,
    COMPLIANCE_TRIGGER_BATCH_SIZE,
    COMPLIANCE_TRIGGER_MAX_CONCURRENCY,
    COMPLIANCE_TRIGGER_QUEUE_MAX_RECEIVE_COUNT,
    COMPLIANCE_TRIGGER_QUEUE_VISIBILITY_TIMEOUT,
    COMPLIANCE_TRIGGER_TIMEOUT,
} from "../../lib/lambdaBuilder/complianceFunctions";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";
import { batchSizeOffenders } from "../support/sqsEventSourceBounds";
import { snsMessageBodyPolicyAdmits } from "../support/snsFilterPolicy";
import topicMessages from "./fixtures/indexerTopicMessages.json";

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

/** A storage stand-in whose `dynamo` hands out a table for whatever name the builder reads. */
const buildStorageResources = (stack: cdk.Stack, kmsKey?: kms.Key): storageResources => {
    const tables: Record<string, dynamodb.Table> = {};
    const dynamo = new Proxy(tables, {
        get: (target, prop: string) => {
            if (!(prop in target)) {
                target[prop] = new dynamodb.Table(stack, `T${prop}`, {
                    partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
                });
            }
            return target[prop];
        },
    });
    const logGroup = (id: string) => new logs.LogGroup(stack, id);
    const topics = {
        assetIndexerSnsTopic: new sns.Topic(stack, "AssetIndexerSnsTopic"),
        fileIndexerSnsTopic: new sns.Topic(stack, "FileIndexerSnsTopic"),
    };
    return {
        encryption: { kmsKey },
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
            accessLogsBucket: new s3.Bucket(stack, "AccessLogsBucket"),
        },
        sns: topics,
        // As the storage stack builds it: the queue and its subscriptions beside the topics.
        sqs: { complianceTriggerQueue: buildComplianceTriggerQueue(stack, topics, kmsKey) },
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
        dynamo,
    } as unknown as storageResources;
};

const synthTrigger = (opts: { govCloud?: boolean; cmk?: boolean } = {}): Template => {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "TestStack", {
        env: { account: "123456789012", region: opts.govCloud ? "us-gov-west-1" : "us-east-1" },
    });
    const config = mockConfig(!!opts.govCloud);
    Service.SetConfig(config);
    cdk.Tags.of(stack).add("vams:stackname", stack.stackName);
    const kmsKey = opts.cmk ? new kms.Key(stack, "DeploymentKey") : undefined;
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
    buildComplianceTriggerFunction(
        stack,
        layer,
        buildStorageResources(stack, kmsKey),
        executeWorkflowV2,
        config,
        undefined as unknown as ec2.IVpc,
        []
    );
    return Template.fromStack(stack);
};

const resources = (template: Template, type: string) =>
    Object.entries(template.findResources(type)).map(([id, r]) => ({ id, ...(r as any) }));

const triggerFunctionId = (template: Template) => {
    const ids = resources(template, "AWS::Lambda::Function")
        .filter(
            (f) => f.Properties.Handler === "handlers.compliance.complianceTrigger.lambda_handler"
        )
        .map((f) => f.id);
    expect(ids).toHaveLength(1);
    return ids[0];
};

const queues = (template: Template) => {
    const all = resources(template, "AWS::SQS::Queue");
    const source = all.filter((q) => q.Properties.RedrivePolicy);
    const dlq = all.filter((q) => !q.Properties.RedrivePolicy);
    expect(source).toHaveLength(1);
    expect(dlq).toHaveLength(1);
    return { source: source[0], dlq: dlq[0] };
};

const getAtt = (value: any): string | undefined =>
    value && Array.isArray(value["Fn::GetAtt"]) ? String(value["Fn::GetAtt"][0]) : undefined;

describe("compliance trigger queue", () => {
    const commercial = synthTrigger();

    test("the trigger has no direct SNS subscription left", () => {
        const lambdaSubs = resources(commercial, "AWS::SNS::Subscription").filter(
            (s) => s.Properties.Protocol === "lambda"
        );
        expect(lambdaSubs).toEqual([]);
    });

    test("both topics deliver to the trigger queue with a message-body filter policy", () => {
        const { source } = queues(commercial);
        const subs = resources(commercial, "AWS::SNS::Subscription");
        expect(subs).toHaveLength(2);
        for (const sub of subs) {
            expect(sub.Properties.Protocol).toBe("sqs");
            expect(getAtt(sub.Properties.Endpoint)).toBe(source.id);
            expect(sub.Properties.FilterPolicyScope).toBe("MessageBody");
        }
        const topicIds = resources(commercial, "AWS::SNS::Topic").reduce(
            (acc: Record<string, string>, t) => ({ ...acc, [t.id]: t.id }),
            {}
        );
        const byTopic = Object.fromEntries(
            subs.map((s) => [s.Properties.TopicArn.Ref, s.Properties.FilterPolicy])
        );
        const assetTopicId = Object.keys(topicIds).find((id) => id.startsWith("AssetIndexer"))!;
        const fileTopicId = Object.keys(topicIds).find((id) => id.startsWith("FileIndexer"))!;
        // Asset indexer topic: stream records only.
        expect(byTopic[assetTopicId]).toEqual({ eventSource: ["aws:dynamodb"] });
        // File indexer topic: everything but the asset-file-metadata records, which the asset
        // indexer topic already delivers. The check sits on the leaf `S`: SNS `exists` applies to
        // leaf values only, and `Keys.metadataKey` is an attribute-value object.
        expect(byTopic[fileTopicId]).toEqual({
            dynamodb: { Keys: { metadataKey: { S: [{ exists: false }] } } },
        });
    });

    describe("the synthesized filter policies over a sample of every message kind", () => {
        const subs = resources(commercial, "AWS::SNS::Subscription");
        const policyOf = (topicPrefix: string) => {
            const sub = subs.find((s) => String(s.Properties.TopicArn.Ref).startsWith(topicPrefix));
            expect(sub).toBeDefined();
            return sub!.Properties.FilterPolicy as Record<string, unknown>;
        };
        const policies: Record<string, Record<string, unknown>> = {
            assetIndexer: policyOf("AssetIndexer"),
            fileIndexer: policyOf("FileIndexer"),
        };
        const cases = topicMessages.samples.flatMap((sample) =>
            Object.entries(sample.topics).map(([topic, expected]) => ({
                name: sample.name,
                topic,
                expected,
                body: sample.body,
            }))
        );

        test.each(cases)("$topic topic: $name is $expected", ({ topic, expected, body }) => {
            expect(policies[topic]).toBeDefined();
            expect(snsMessageBodyPolicyAdmits(policies[topic], body)).toBe(expected === "admit");
        });

        test("every message reaches the trigger queue exactly once", () => {
            for (const sample of topicMessages.samples) {
                const admitted = Object.keys(sample.topics).filter((topic) =>
                    snsMessageBodyPolicyAdmits(policies[topic], sample.body)
                );
                expect({ name: sample.name, admitted: admitted.length }).toEqual({
                    name: sample.name,
                    admitted: 1,
                });
            }
        });

        test("the samples cover every table each topic relays and both S3 event kinds", () => {
            const tableOf = (body: any) =>
                typeof body.eventSourceARN === "string"
                    ? body.eventSourceARN.split(":table/")[1].split("/stream/")[0]
                    : "S3";
            const covered = (topic: string) =>
                new Set(
                    topicMessages.samples
                        .filter((sample) => topic in sample.topics)
                        .map((sample) => tableOf(sample.body))
                );
            // The tables buildAssetIndexerSnsQueuingFunction / buildFileIndexerSnsQueuingFunction
            // read the streams of, and the bucket sync's S3 messages on the file topic.
            expect(covered("assetIndexer")).toEqual(
                new Set([
                    "AssetStorageTable",
                    "AssetFileMetadataStorageTableV2",
                    "AssetLinksStorageTableV2.2",
                    "AssetLinksMetadataStorageTable",
                ])
            );
            expect(covered("fileIndexer")).toEqual(
                new Set(["AssetFileMetadataStorageTableV2", "FileAttributeStorageTableV2", "S3"])
            );
        });
    });

    test("the queue dead-letters after a bounded receive count", () => {
        const { source, dlq } = queues(commercial);
        expect(getAtt(source.Properties.RedrivePolicy.deadLetterTargetArn)).toBe(dlq.id);
        expect(source.Properties.RedrivePolicy.maxReceiveCount).toBe(
            COMPLIANCE_TRIGGER_QUEUE_MAX_RECEIVE_COUNT
        );
        expect(COMPLIANCE_TRIGGER_QUEUE_MAX_RECEIVE_COUNT).toBeGreaterThan(0);
        expect(COMPLIANCE_TRIGGER_QUEUE_MAX_RECEIVE_COUNT).toBeLessThanOrEqual(5);
        expect(dlq.Properties.MessageRetentionPeriod).toBe(14 * 24 * 3600);
    });

    test("the visibility timeout is at least six times the function timeout", () => {
        const { source } = queues(commercial);
        const fun =
            commercial.findResources("AWS::Lambda::Function")[triggerFunctionId(commercial)];
        expect(fun.Properties.Timeout).toBe(COMPLIANCE_TRIGGER_TIMEOUT.toSeconds());
        expect(source.Properties.VisibilityTimeout).toBe(
            COMPLIANCE_TRIGGER_QUEUE_VISIBILITY_TIMEOUT.toSeconds()
        );
        expect(source.Properties.VisibilityTimeout).toBeGreaterThanOrEqual(
            6 * fun.Properties.Timeout
        );
    });

    test("both queues are KMS-encrypted with the deployment key when one is configured", () => {
        const template = synthTrigger({ cmk: true });
        const keyId = resources(template, "AWS::KMS::Key")[0].id;
        const { source, dlq } = queues(template);
        for (const q of [source, dlq]) {
            expect(getAtt(q.Properties.KmsMasterKeyId)).toBe(keyId);
        }
    });

    test("both queues use SQS-managed encryption without a deployment key", () => {
        const { source, dlq } = queues(commercial);
        for (const q of [source, dlq]) {
            expect(q.Properties.SqsManagedSseEnabled).toBe(true);
        }
    });

    test("both queues refuse non-TLS access", () => {
        const { source, dlq } = queues(commercial);
        const policies = resources(commercial, "AWS::SQS::QueuePolicy");
        for (const q of [source, dlq]) {
            const policy = policies.find((p) =>
                JSON.stringify(p.Properties.Queues).includes(`"${q.id}"`)
            );
            expect(policy).toBeDefined();
            const denies = policy!.Properties.PolicyDocument.Statement.filter(
                (st: any) =>
                    st.Effect === "Deny" &&
                    JSON.stringify(st.Condition ?? {}).includes("aws:SecureTransport")
            );
            expect(denies).toHaveLength(1);
        }
    });

    test("only the two topics may send to the queue", () => {
        const { source } = queues(commercial);
        const policy = resources(commercial, "AWS::SQS::QueuePolicy").find((p) =>
            JSON.stringify(p.Properties.Queues).includes(`"${source.id}"`)
        )!;
        const sends = policy.Properties.PolicyDocument.Statement.filter(
            (st: any) => st.Effect === "Allow"
        );
        expect(sends).toHaveLength(2);
        for (const st of sends) {
            expect(st.Action).toBe("sqs:SendMessage");
            expect(st.Principal).toEqual({ Service: "sns.amazonaws.com" });
            expect(Object.keys(st.Condition.ArnEquals)).toEqual(["aws:SourceArn"]);
        }
        const senders = sends.map((st: any) => st.Condition.ArnEquals["aws:SourceArn"].Ref).sort();
        expect(senders).toEqual(
            resources(commercial, "AWS::SNS::Topic")
                .map((t) => t.id)
                .sort()
        );
    });

    test("the trigger consumes the queue on the queue's own ARN, no wildcard", () => {
        const { source } = queues(commercial);
        // The role's statements can overflow its inline policy into managed policies.
        const policies = [
            ...resources(commercial, "AWS::IAM::Policy"),
            ...resources(commercial, "AWS::IAM::ManagedPolicy"),
        ].filter((p) => JSON.stringify(p.Properties.Roles).includes("complianceTrigger"));
        const sqsStatements = policies.flatMap((p) =>
            p.Properties.PolicyDocument.Statement.filter((st: any) =>
                [st.Action].flat().some((a: string) => a.startsWith("sqs:"))
            )
        );
        expect(sqsStatements).toHaveLength(1);
        const st = sqsStatements[0];
        expect([st.Action].flat().sort()).toEqual(
            [
                "sqs:ChangeMessageVisibility",
                "sqs:DeleteMessage",
                "sqs:GetQueueAttributes",
                "sqs:GetQueueUrl",
                "sqs:ReceiveMessage",
            ].sort()
        );
        expect(getAtt(st.Resource)).toBe(source.id);
    });

    test("the trigger knows its own queue's URL, to hold an upload completion back on it", () => {
        const { source } = queues(commercial);
        const trigger = resources(commercial, "AWS::Lambda::Function").find(
            (f) => f.id === triggerFunctionId(commercial)
        )!;
        expect(trigger.Properties.Environment.Variables.COMPLIANCE_TRIGGER_QUEUE_URL).toEqual({
            Ref: source.id,
        });
    });

    test("the trigger can stamp the asset row with UpdateItem and writes it no other way", () => {
        const statements = [
            ...resources(commercial, "AWS::IAM::Policy"),
            ...resources(commercial, "AWS::IAM::ManagedPolicy"),
        ]
            .filter((p) => JSON.stringify(p.Properties.Roles).includes("complianceTrigger"))
            .flatMap((p) => p.Properties.PolicyDocument.Statement)
            .filter((st: any) => JSON.stringify(st.Resource).includes("TassetStorageTable"));
        const actions = statements.flatMap((st: any) => [st.Action].flat());
        expect(actions).toContain("dynamodb:UpdateItem");
        expect(actions).toContain("dynamodb:GetItem");
        for (const write of [
            "dynamodb:PutItem",
            "dynamodb:DeleteItem",
            "dynamodb:BatchWriteItem",
        ]) {
            expect(actions).not.toContain(write);
        }
    });

    test.each([
        ["commercial", false],
        ["govCloud", true],
    ])(
        "the %s event source is batched, capped, reads batchItemFailures and consumes the queue",
        (_label, govCloud) => {
            const template = govCloud ? synthTrigger({ govCloud: true }) : commercial;
            const { source } = queues(template);
            const mappings = resources(template, "AWS::Lambda::EventSourceMapping");
            expect(mappings).toHaveLength(1);
            const props = mappings[0].Properties;
            expect(getAtt(props.EventSourceArn)).toBe(source.id);
            expect(props.FunctionName.Ref).toBe(triggerFunctionId(template));
            expect(props.BatchSize).toBe(COMPLIANCE_TRIGGER_BATCH_SIZE);
            expect(batchSizeOffenders([{ at: _label, properties: props }])).toEqual([]);
            expect(props.MaximumBatchingWindowInSeconds).toBeGreaterThan(0);
            expect(props.FunctionResponseTypes).toEqual(["ReportBatchItemFailures"]);
            expect(props.ScalingConfig).toEqual({
                MaximumConcurrency: COMPLIANCE_TRIGGER_MAX_CONCURRENCY,
            });
            // GovCloud and EU Sovereign reject Tags on a mapping; commercial keeps the stack tags.
            if (govCloud) expect(props).not.toHaveProperty("Tags");
            else expect(props).toHaveProperty("Tags");
        }
    );
});
