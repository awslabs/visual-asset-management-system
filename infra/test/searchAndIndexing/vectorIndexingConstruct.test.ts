/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The vector indexing construct's wiring, asserted on the emitted template in both partitions.
 *
 * Two source queues (auto-named, so an orphan never blocks a redeploy), one DLQ each plus a rule-target
 * DLQ and the reindexer's asynchronous-invocation DLQ, an EventBridge rule for `vector.embedding.ready`,
 * two SNS subscriptions on the same indexer queue,
 * and two event source mappings whose GovCloud L1 branch strips `Tags` while carrying exactly the settings
 * of the commercial `addEventSource()` branch — the indexer's capped at 10 concurrent invocations, the
 * launcher's at the configured indexing concurrency. The indexer addresses its own queue for continuation
 * messages and may send to it. The launcher's InvokeFunction grant names the execute-workflow Lambda by a
 * partition-aware ARN composed from its NAME. Every grant is read from inline and managed policies alike.
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
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
import { VectorIndexingConstruct } from "../../lib/nestedStacks/searchAndIndexing/constructs/vectorIndexing-construct";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";
import { batchSizeOffenders } from "../support/sqsEventSourceBounds";

const ACCOUNT = "123456789012";
const EXECUTE_NAME = "vams-test-executeWorkflow-ABC123";

const mockConfig = (govCloud: boolean): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = govCloud ? "us-gov-west-1" : "us-east-1";
    config.env.partition = govCloud ? "aws-us-gov" : "aws";
    config.env.coreStackName = "vams-test";
    config.app.baseStackName = "vams-test";
    config.app.govCloud.enabled = govCloud;
    config.app.vectorSearch = {
        enabled: true,
        embeddingModelId: "amazon.titan-embed-text-v2:0",
        embeddingDimensions: 1024,
        indexingConcurrency: 7,
    };
    (config as any).vectorIndexName = "vec-amazon-titan-embed-text-v2-0-1024";
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test/resourceNames";
    return config;
};

const TABLE_FIELDS = [
    "vectorEmbeddingsStorageTable",
    "assetStorageTable",
    "databaseStorageTable",
    "s3AssetBucketsStorageTable",
    "workflowStorageTableV2",
    "pipelineStorageTableV2",
    "workflowTriggersStorageTable",
    "authEntitiesStorageTable",
    "constraintsStorageTable",
    "userRolesStorageTable",
    "rolesStorageTable",
];

const synth = (govCloud: boolean): Template => {
    const config = mockConfig(govCloud);
    Service.SetConfig(config);
    const app = newTestApp();
    const stack = new cdk.Stack(app, "VectorStack", {
        env: { account: ACCOUNT, region: config.env.region },
    });
    // core-stack.ts tags the stack and CDK propagates the tags onto taggable L1s, the mapping created by
    // addEventSource() included; without the tags there is nothing for the GovCloud override to remove.
    cdk.Tags.of(stack).add("SolutionName", "AWSVisualAssetManagementSystem");
    cdk.Tags.of(stack).add("vams:stackname", stack.stackName);

    const table = (id: string) =>
        new dynamodb.Table(stack, id, {
            partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
        });
    const dynamo: Record<string, dynamodb.Table> = {};
    for (const field of TABLE_FIELDS) dynamo[field] = table(field);
    const logGroup = (id: string) => new logs.LogGroup(stack, id);
    const resources = {
        encryption: { kmsKey: new kms.Key(stack, "Key") },
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
            accessLogsBucket: new s3.Bucket(stack, "AccessLogsBucket"),
        },
        sns: {
            eventEmailSubscriptionTopic: new sns.Topic(stack, "EmailTopic"),
            fileIndexerSnsTopic: new sns.Topic(stack, "FileIndexerTopic"),
            assetIndexerSnsTopic: new sns.Topic(stack, "AssetIndexerTopic"),
            databaseIndexerSnsTopic: new sns.Topic(stack, "DatabaseIndexerTopic"),
        },
        eventBridge: {
            orchestrationBus: new events.EventBus(stack, "OrchestrationBus"),
            orchestrationBusAuditLogGroup: logGroup("BusAudit"),
            eventSourcePrefix: "vams.vams-test",
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
    const layer = lambda.LayerVersion.fromLayerVersionArn(
        stack,
        "CommonLayer",
        `arn:${config.env.partition}:lambda:${config.env.region}:${ACCOUNT}:layer:vams-common:1`
    ) as lambda.LayerVersion;

    new VectorIndexingConstruct(stack, "VectorIndexing", {
        config,
        storageResources: resources,
        lambdaCommonBaseLayer: layer,
        executeWorkflowV2FunctionName: EXECUTE_NAME,
    });
    return Template.fromStack(stack);
};

/**
 * Resources of `type` whose logical id starts with `prefix`. The construct is instantiated as
 * `VectorIndexing`, so every child's logical id is `VectorIndexing<ChildId><8HEX>`: a bare child id such
 * as `VectorIndexerQueue` matches nothing, and `byPrefix(...)[0]` would throw at collection.
 */
const byPrefix = (template: Template, type: string, prefix: string): [string, any][] =>
    Object.entries(template.findResources(type)).filter(([id]) => id.startsWith(prefix)) as [
        string,
        any
    ][];

const functionByHandler = (template: Template, module: string): [string, any] => {
    const entries = Object.entries(template.findResources("AWS::Lambda::Function")).filter(
        ([, r]: [string, any]) =>
            r.Properties.Handler === `handlers.osVectorSearch.${module}.lambda_handler`
    );
    expect(entries).toHaveLength(1);
    return entries[0] as [string, any];
};

const mappingFor = (template: Template, queueId: string): any => {
    const mappings = Object.values(
        template.findResources("AWS::Lambda::EventSourceMapping")
    ).filter((m: any) => JSON.stringify(m.Properties.EventSourceArn).includes(queueId));
    expect(mappings).toHaveLength(1);
    return mappings[0];
};

const statementsForFunction = (template: Template, fn: any): any[] => {
    const roleId: string = fn.Properties.Role["Fn::GetAtt"][0];
    const policies = [
        ...Object.values(template.findResources("AWS::IAM::Policy")),
        ...Object.values(template.findResources("AWS::IAM::ManagedPolicy")),
    ].filter((p: any) => JSON.stringify(p.Properties.Roles ?? []).includes(`"Ref":"${roleId}"`));
    return policies.flatMap((p: any) => {
        const st = p.Properties.PolicyDocument.Statement;
        return Array.isArray(st) ? st : [st];
    });
};

describe.each([false, true])("VectorIndexingConstruct (govCloud=%s)", (govCloud) => {
    const template = synth(govCloud);
    const [indexerQueueId, indexerQueue] = byPrefix(
        template,
        "AWS::SQS::Queue",
        "VectorIndexingVectorIndexerQueue"
    )[0];
    const [launchQueueId, launchQueue] = byPrefix(
        template,
        "AWS::SQS::Queue",
        "VectorIndexingSystemWorkflowLaunchQueue"
    )[0];

    test("emits two auto-named source queues and four dead-letter queues", () => {
        expect(
            byPrefix(template, "AWS::SQS::Queue", "VectorIndexingVectorIndexerQueue")
        ).toHaveLength(1);
        expect(
            byPrefix(template, "AWS::SQS::Queue", "VectorIndexingVectorIndexerDLQ")
        ).toHaveLength(1);
        expect(
            byPrefix(template, "AWS::SQS::Queue", "VectorIndexingVectorEmbeddingReadyRuleDLQ")
        ).toHaveLength(1);
        expect(
            byPrefix(template, "AWS::SQS::Queue", "VectorIndexingSystemWorkflowLaunchQueue")
        ).toHaveLength(1);
        expect(
            byPrefix(template, "AWS::SQS::Queue", "VectorIndexingSystemWorkflowLaunchDLQ")
        ).toHaveLength(1);
        expect(
            byPrefix(template, "AWS::SQS::Queue", "VectorIndexingVectorReindexerAsyncDLQ")
        ).toHaveLength(1);
        expect(Object.keys(template.findResources("AWS::SQS::Queue"))).toHaveLength(6);
        for (const [, q] of Object.entries(template.findResources("AWS::SQS::Queue")) as [
            string,
            any
        ][]) {
            expect(q.Properties).not.toHaveProperty("QueueName");
            expect(q.Properties).toHaveProperty("KmsMasterKeyId");
        }
        for (const q of [indexerQueue, launchQueue]) {
            expect(q.Properties.VisibilityTimeout).toBe(960);
            expect(q.Properties.RedrivePolicy.maxReceiveCount).toBe(3);
        }
        const dlqIds = new Set(
            [indexerQueue, launchQueue].map(
                (q) => q.Properties.RedrivePolicy.deadLetterTargetArn["Fn::GetAtt"][0]
            )
        );
        expect(dlqIds.size).toBe(2);
        for (const dlqId of dlqIds) {
            expect(
                template.findResources("AWS::SQS::Queue")[dlqId].Properties.MessageRetentionPeriod
            ).toBe(14 * 24 * 60 * 60);
        }
    });

    test("every queue denies non-TLS access", () => {
        for (const [id] of Object.entries(template.findResources("AWS::SQS::Queue"))) {
            const policies = Object.values(template.findResources("AWS::SQS::QueuePolicy")).filter(
                (p: any) => JSON.stringify(p.Properties.Queues).includes(id)
            );
            expect(
                policies.some((p: any) =>
                    JSON.stringify(p.Properties.PolicyDocument).includes("aws:SecureTransport")
                )
            ).toBe(true);
        }
    });

    test("the embedding-ready rule targets the indexer queue with a rule-owned DLQ and three retries", () => {
        const [, rule] = byPrefix(
            template,
            "AWS::Events::Rule",
            "VectorIndexingVectorEmbeddingReadyRule"
        )[0];
        expect(rule.Properties.EventPattern).toEqual({
            source: [{ prefix: "vams.vams-test" }],
            "detail-type": ["vector.embedding.ready"],
        });
        expect(rule.Properties.EventBusName).toEqual({
            Ref: expect.stringMatching(/^OrchestrationBus/),
        });
        const [target] = rule.Properties.Targets;
        expect(target.Arn["Fn::GetAtt"][0]).toBe(indexerQueueId);
        expect(target.DeadLetterConfig.Arn["Fn::GetAtt"][0]).toMatch(
            /^VectorIndexingVectorEmbeddingReadyRuleDLQ/
        );
        expect(target.RetryPolicy.MaximumRetryAttempts).toBe(3);
    });

    test("both indexer topics subscribe the indexer queue", () => {
        const subscriptions = Object.values(
            template.findResources("AWS::SNS::Subscription")
        ).filter(
            (s: any) =>
                s.Properties.Protocol === "sqs" &&
                JSON.stringify(s.Properties.Endpoint).includes(indexerQueueId)
        );
        const topics = subscriptions.map((s: any) => s.Properties.TopicArn.Ref).sort();
        expect(topics).toEqual([
            expect.stringMatching(/^AssetIndexerTopic/),
            expect.stringMatching(/^FileIndexerTopic/),
        ]);
    });

    test("the indexer mapping reports batch item failures at batch size 10 with MaximumConcurrency 10; the launcher at 1 with the configured MaximumConcurrency", () => {
        const indexerMapping = mappingFor(template, indexerQueueId);
        expect(indexerMapping.Properties.FunctionResponseTypes).toEqual([
            "ReportBatchItemFailures",
        ]);
        expect(indexerMapping.Properties.BatchSize).toBe(10);
        expect(indexerMapping.Properties.MaximumBatchingWindowInSeconds).toBe(3);
        // One asset's item collection is one DynamoDB partition, and continuation messages re-enter
        // this queue, so the indexer's concurrency is capped independently of the launcher's.
        expect(indexerMapping.Properties.ScalingConfig).toEqual({ MaximumConcurrency: 10 });
        expect(indexerMapping.Properties.FunctionName.Ref).toBe(
            functionByHandler(template, "vectorIndexer")[0]
        );

        const launchMapping = mappingFor(template, launchQueueId);
        expect(launchMapping.Properties.FunctionResponseTypes).toEqual(["ReportBatchItemFailures"]);
        expect(launchMapping.Properties.BatchSize).toBe(1);
        expect(launchMapping.Properties.ScalingConfig).toEqual({ MaximumConcurrency: 7 });
        expect(launchMapping.Properties.FunctionName.Ref).toBe(
            functionByHandler(template, "systemWorkflowLauncher")[0]
        );

        expect(
            batchSizeOffenders([
                { at: `govCloud=${govCloud} indexer`, properties: indexerMapping.Properties },
                { at: `govCloud=${govCloud} launcher`, properties: launchMapping.Properties },
            ])
        ).toEqual([]);
    });

    test("Tags are stripped from the mappings in the restricted partition and present in commercial", () => {
        for (const mapping of [
            mappingFor(template, indexerQueueId),
            mappingFor(template, launchQueueId),
        ]) {
            if (govCloud) expect(mapping.Properties).not.toHaveProperty("Tags");
            else expect(mapping.Properties).toHaveProperty("Tags");
        }
    });

    test("the launcher may invoke exactly the named execute-workflow function, in this partition", () => {
        const [, launcher] = functionByHandler(template, "systemWorkflowLauncher");
        const invoke = statementsForFunction(template, launcher).filter((s) =>
            JSON.stringify(s.Action).includes("lambda:InvokeFunction")
        );
        expect(invoke).toHaveLength(1);
        const partition = govCloud ? "aws-us-gov" : "aws";
        const region = govCloud ? "us-gov-west-1" : "us-east-1";
        expect(invoke[0].Resource).toBe(
            `arn:${partition}:lambda:${region}:${ACCOUNT}:function:${EXECUTE_NAME}`
        );
    });

    test("the reindexer may send to the launch queue and its own async DLQ, and both consumers may receive from their own queue", () => {
        const [, reindexer] = functionByHandler(template, "vectorReindexer");
        const send = statementsForFunction(template, reindexer).filter((s) =>
            JSON.stringify(s.Action).includes("sqs:SendMessage")
        );
        // Two exact queues: the launch queue it enqueues to, and the DLQ Lambda delivers its failed
        // asynchronous self-invocations to (the destination grant is on the function's own role).
        expect(send).toHaveLength(2);
        const sendTargets = send.map((s) => JSON.stringify(s.Resource)).join();
        expect(sendTargets).toContain(launchQueueId);
        expect(sendTargets).toContain("VectorIndexingVectorReindexerAsyncDLQ");
        expect(sendTargets).not.toContain(indexerQueueId);
        for (const [module, queueId] of [
            ["vectorIndexer", indexerQueueId],
            ["systemWorkflowLauncher", launchQueueId],
        ]) {
            const [, fn] = functionByHandler(template, module);
            const receive = statementsForFunction(template, fn).filter((s) =>
                JSON.stringify(s.Action).includes("sqs:ReceiveMessage")
            );
            expect(receive).toHaveLength(1);
            expect(JSON.stringify(receive[0].Resource)).toContain(queueId);
        }
    });

    test("the indexer addresses its own queue for continuation messages and may send to it", () => {
        const [, indexer] = functionByHandler(template, "vectorIndexer");
        expect(indexer.Properties.Environment.Variables.VECTOR_INDEXER_QUEUE_URL).toEqual({
            Ref: indexerQueueId,
        });
        // Read from inline and managed policies alike; a grant CDK spilled into a managed policy is a grant.
        const send = statementsForFunction(template, indexer).filter((s) =>
            JSON.stringify(s.Action).includes("sqs:SendMessage")
        );
        expect(send).toHaveLength(1);
        expect(JSON.stringify(send[0].Resource)).toContain(indexerQueueId);
        expect(JSON.stringify(send[0].Resource)).not.toContain(launchQueueId);
    });

    test("the reindexer's asynchronous self-invocations have an on-failure destination: its CMK-encrypted DLQ, after two retries", () => {
        const [reindexerId] = functionByHandler(template, "vectorReindexer");
        const [, dlq] = byPrefix(
            template,
            "AWS::SQS::Queue",
            "VectorIndexingVectorReindexerAsyncDLQ"
        )[0];
        expect(dlq.Properties.KmsMasterKeyId).toBeDefined();
        expect(dlq.Properties.MessageRetentionPeriod).toBe(14 * 24 * 60 * 60);
        expect(dlq.Properties).not.toHaveProperty("RedrivePolicy");
        const configs = Object.values(
            template.findResources("AWS::Lambda::EventInvokeConfig")
        ).filter((c: any) => c.Properties.FunctionName.Ref === reindexerId);
        expect(configs).toHaveLength(1);
        const [config] = configs as any[];
        expect(config.Properties.Qualifier).toBe("$LATEST");
        expect(config.Properties.MaximumRetryAttempts).toBe(2);
        expect(config.Properties.DestinationConfig.OnFailure.Destination["Fn::GetAtt"][0]).toMatch(
            /^VectorIndexingVectorReindexerAsyncDLQ/
        );
        expect(config.Properties.DestinationConfig.OnSuccess).toBeUndefined();
        // Nothing else has an asynchronous invoke configuration: the other functions are queue consumers.
        expect(Object.keys(template.findResources("AWS::Lambda::EventInvokeConfig"))).toHaveLength(
            1
        );
    });

    test("the four dead-letter queues carry the SQS3 suppression and the source queues do not", () => {
        for (const prefix of [
            "VectorIndexingVectorIndexerDLQ",
            "VectorIndexingVectorEmbeddingReadyRuleDLQ",
            "VectorIndexingSystemWorkflowLaunchDLQ",
            "VectorIndexingVectorReindexerAsyncDLQ",
        ]) {
            const [, dlq] = byPrefix(template, "AWS::SQS::Queue", prefix)[0];
            const rules = dlq.Metadata?.cdk_nag?.rules_to_suppress ?? [];
            expect(rules.map((r: any) => r.id)).toContain("AwsSolutions-SQS3");
        }
        for (const q of [indexerQueue, launchQueue]) {
            expect(JSON.stringify(q.Metadata ?? {})).not.toContain("AwsSolutions-SQS3");
        }
    });
});

test("both branches configure each mapping identically apart from Tags", () => {
    const commercial = synth(false);
    const govCloud = synth(true);
    for (const prefix of [
        "VectorIndexingVectorIndexerQueue",
        "VectorIndexingSystemWorkflowLaunchQueue",
    ]) {
        const c = {
            ...mappingFor(commercial, byPrefix(commercial, "AWS::SQS::Queue", prefix)[0][0])
                .Properties,
        };
        const g = mappingFor(
            govCloud,
            byPrefix(govCloud, "AWS::SQS::Queue", prefix)[0][0]
        ).Properties;
        delete c.Tags;
        expect(Object.keys(g).sort()).toEqual(
            expect.arrayContaining(["BatchSize", "EventSourceArn", "FunctionName"])
        );
        expect(c).toEqual(g);
    }
});
