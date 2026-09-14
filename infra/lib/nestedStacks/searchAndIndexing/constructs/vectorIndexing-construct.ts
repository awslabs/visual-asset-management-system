/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as eventsTargets from "aws-cdk-lib/aws-events-targets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as destinations from "aws-cdk-lib/aws-lambda-destinations";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { SqsSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../config/config";
import {
    SYSTEM_GENAI_METADATA_WORKFLOW_ID,
    SYSTEM_WORKFLOW_DATABASE_ID,
} from "../../../../common/systemPipelines";
import { IAMArn } from "../../../helper/service-helper";
import {
    buildSystemWorkflowLauncherFunction,
    buildVectorIndexerFunction,
    buildVectorReindexerFunction,
} from "../../../lambdaBuilder/vectorSearchFunctions";
import { storageResources } from "../../storage/storageBuilder-nestedStack";

export interface VectorIndexingConstructProps {
    config: Config.Config;
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    vpc?: ec2.IVpc;
    subnets?: ec2.ISubnet[];
    /** Name of the execute-workflow Lambda built in ApiBuilder2; only the name crosses the stack boundary. */
    executeWorkflowV2FunctionName: string;
}

/** The orchestration-bus event a pipeline publishes when a file version's embedding document is ready. */
export const VECTOR_EMBEDDING_READY_DETAIL_TYPE = "vector.embedding.ready";

// Deliveries a message gets before its source queue moves it to the dead-letter queue. A failure the
// consumer cannot attribute to one record reports the WHOLE batch, and a timeout reports none of it, so
// every record in a redelivered batch advances its receive count together; three deliveries spaced by
// the 960 s visibility timeout is ~45 minutes before a persistent fault dead-letters a batch.
const QUEUE_MAX_RECEIVE_COUNT = 3;
// The consuming functions time out at 900 s.
const CONSUMER_VISIBILITY_TIMEOUT = cdk.Duration.seconds(960);
const DLQ_RETENTION = cdk.Duration.days(14);
// Concurrent indexer invocations the mapping allows: one asset's item collection is one DynamoDB
// partition (3,000 RCU/s, 1,000 WCU/s), and the asset- and file-wide rules re-enter this queue with
// continuation messages.
const INDEXER_MAX_CONCURRENCY = 10;

interface SqsConsumerSettings {
    batchSize: number;
    maxBatchingWindow?: cdk.Duration;
    reportBatchItemFailures: boolean;
    maxConcurrency?: number;
}

/**
 * Vector indexing: the single writer of the vector embeddings table and its two feeds, the reindexer (with
 * the dead-letter queue of its asynchronous continuations), and the paced launcher of the system GenAI
 * workflow. Present only when `app.vectorSearch.enabled`.
 */
export class VectorIndexingConstruct extends Construct {
    public readonly vectorIndexerQueue: sqs.Queue;
    public readonly systemWorkflowLaunchQueue: sqs.Queue;
    public readonly vectorIndexerFunction: lambda.Function;
    public readonly vectorReindexerFunction: lambda.Function;
    public readonly systemWorkflowLauncherFunction: lambda.Function;

    constructor(scope: Construct, id: string, props: VectorIndexingConstructProps) {
        super(scope, id);
        const { config, storageResources, lambdaCommonBaseLayer, vpc, subnets } = props;
        const encryption = {
            encryption: storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: storageResources.encryption.kmsKey,
            enforceSSL: true,
        };

        // Queues are auto-named: a retained orphan from a failed teardown never collides with a redeploy.
        const vectorIndexerDlq = new sqs.Queue(this, "VectorIndexerDLQ", {
            retentionPeriod: DLQ_RETENTION,
            ...encryption,
        });
        this.vectorIndexerQueue = new sqs.Queue(this, "VectorIndexerQueue", {
            visibilityTimeout: CONSUMER_VISIBILITY_TIMEOUT,
            ...encryption,
            deadLetterQueue: { queue: vectorIndexerDlq, maxReceiveCount: QUEUE_MAX_RECEIVE_COUNT },
        });

        // S3 lifecycle records (bucket-sync envelope) and asset table stream records reach the indexer
        // through the two topics the OpenSearch indexers consume; the topics exist regardless of OpenSearch.
        storageResources.sns.fileIndexerSnsTopic.addSubscription(
            new SqsSubscription(this.vectorIndexerQueue)
        );
        storageResources.sns.assetIndexerSnsTopic.addSubscription(
            new SqsSubscription(this.vectorIndexerQueue)
        );

        // Embedding documents are announced on the orchestration bus by whichever pipeline produced them.
        const embeddingReadyRuleDlq = new sqs.Queue(this, "VectorEmbeddingReadyRuleDLQ", {
            retentionPeriod: DLQ_RETENTION,
            ...encryption,
        });
        const embeddingReadyRule = new events.Rule(this, "VectorEmbeddingReadyRule", {
            eventBus: storageResources.eventBridge.orchestrationBus,
            eventPattern: {
                source: events.Match.prefix(storageResources.eventBridge.eventSourcePrefix),
                detailType: [VECTOR_EMBEDDING_READY_DETAIL_TYPE],
            },
        });
        embeddingReadyRule.addTarget(
            new eventsTargets.SqsQueue(this.vectorIndexerQueue, {
                deadLetterQueue: embeddingReadyRuleDlq,
                retryAttempts: 3,
            })
        );

        const launchDlq = new sqs.Queue(this, "SystemWorkflowLaunchDLQ", {
            retentionPeriod: DLQ_RETENTION,
            ...encryption,
        });
        this.systemWorkflowLaunchQueue = new sqs.Queue(this, "SystemWorkflowLaunchQueue", {
            visibilityTimeout: CONSUMER_VISIBILITY_TIMEOUT,
            ...encryption,
            deadLetterQueue: { queue: launchDlq, maxReceiveCount: QUEUE_MAX_RECEIVE_COUNT },
        });

        this.vectorIndexerFunction = buildVectorIndexerFunction(
            this,
            storageResources,
            config,
            lambdaCommonBaseLayer,
            vpc,
            subnets,
            { VECTOR_INDEXER_QUEUE_URL: this.vectorIndexerQueue.queueUrl }
        );
        this.vectorReindexerFunction = buildVectorReindexerFunction(
            this,
            storageResources,
            config,
            lambdaCommonBaseLayer,
            vpc,
            subnets,
            {
                WORKFLOW_LAUNCH_QUEUE_URL: this.systemWorkflowLaunchQueue.queueUrl,
                GENAI_METADATA_WORKFLOW_ID: SYSTEM_GENAI_METADATA_WORKFLOW_ID,
                GENAI_METADATA_WORKFLOW_DATABASE_ID: SYSTEM_WORKFLOW_DATABASE_ID,
            }
        );
        this.systemWorkflowLauncherFunction = buildSystemWorkflowLauncherFunction(
            this,
            storageResources,
            config,
            lambdaCommonBaseLayer,
            vpc,
            subnets,
            {
                EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME: props.executeWorkflowV2FunctionName,
                GENAI_METADATA_WORKFLOW_ID: SYSTEM_GENAI_METADATA_WORKFLOW_ID,
                GENAI_METADATA_WORKFLOW_DATABASE_ID: SYSTEM_WORKFLOW_DATABASE_ID,
            }
        );

        // Asset- and file-wide rules that run out of time re-enqueue the rest of their work here.
        this.vectorIndexerQueue.grantSendMessages(this.vectorIndexerFunction);
        this.systemWorkflowLaunchQueue.grantSendMessages(this.vectorReindexerFunction);

        // The reindexer continues a long run by invoking itself asynchronously. An asynchronous
        // invocation Lambda could not complete (a timeout, a crash, exhausted retries after throttling)
        // is otherwise dropped, and the reindex ends part-way with nothing recording that it did; the
        // failed event lands here with its continuation state, from which the run can be resumed. The
        // two retries are Lambda's default for asynchronous events; a continuation re-run from the same
        // state repeats idempotent deletes and re-enqueues the same files, both of which are harmless.
        const reindexerAsyncDlq = new sqs.Queue(this, "VectorReindexerAsyncDLQ", {
            retentionPeriod: DLQ_RETENTION,
            ...encryption,
        });
        this.vectorReindexerFunction.configureAsyncInvoke({
            onFailure: new destinations.SqsDestination(reindexerAsyncDlq),
            retryAttempts: 2,
        });
        // The execute-workflow Lambda lives in ApiBuilder2; its ARN is composed from the name so no
        // function object crosses the nested-stack boundary.
        this.systemWorkflowLauncherFunction.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["lambda:InvokeFunction"],
                resources: [IAMArn(props.executeWorkflowV2FunctionName).lambda],
            })
        );

        this.addSqsConsumer(
            "VectorIndexerSqsEventSource",
            this.vectorIndexerQueue,
            this.vectorIndexerFunction,
            config,
            {
                batchSize: 10,
                maxBatchingWindow: cdk.Duration.seconds(3),
                reportBatchItemFailures: true,
                maxConcurrency: INDEXER_MAX_CONCURRENCY,
            }
        );
        this.addSqsConsumer(
            "SystemWorkflowLaunchSqsEventSource",
            this.systemWorkflowLaunchQueue,
            this.systemWorkflowLauncherFunction,
            config,
            {
                batchSize: 1,
                reportBatchItemFailures: true,
                maxConcurrency: config.app.vectorSearch.indexingConcurrency,
            }
        );

        NagSuppressions.addResourceSuppressions(
            [vectorIndexerDlq, embeddingReadyRuleDlq, launchDlq, reindexerAsyncDlq],
            [
                {
                    id: "AwsSolutions-SQS3",
                    reason: "This queue IS a dead-letter queue (of the vector indexer queue, of the embedding-ready rule target, of the system-workflow launch queue, or of the reindexer's asynchronous self-invocations). A DLQ is the terminal destination for records the consumer could not process, so a redrive policy of its own would only defer the same failure to a further queue.",
                },
            ],
            true
        );
    }

    /**
     * One event source mapping, built from one settings object on both partition branches. GovCloud and
     * EU Sovereign Lambda reject `Tags` on a mapping, so the restricted branch builds the L1 and deletes
     * the property CDK's tag propagation would otherwise set.
     */
    private addSqsConsumer(
        id: string,
        queue: sqs.Queue,
        fun: lambda.Function,
        config: Config.Config,
        settings: SqsConsumerSettings
    ): void {
        queue.grantConsumeMessages(fun);
        if (config.app.govCloud.enabled) {
            const esm = new lambda.EventSourceMapping(this, id, {
                eventSourceArn: queue.queueArn,
                target: fun,
                ...settings,
            });
            (esm.node.defaultChild as lambda.CfnEventSourceMapping).addPropertyDeletionOverride(
                "Tags"
            );
        } else {
            fun.addEventSource(new eventsources.SqsEventSource(queue, settings));
        }
    }
}
