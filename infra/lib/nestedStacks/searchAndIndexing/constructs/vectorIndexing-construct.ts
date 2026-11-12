/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
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
} from "../../../lambdaBuilder/osVectorSearchFunctions";
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
 * The reindexer (with the dead-letter queue of its asynchronous continuations) and the paced launcher of
 * the system GenAI workflow, present whenever `systemWorkflowReindexerEnabled(config)` holds (the system
 * GenAI metadata pipeline is on and registered), with vector search on or off: with it off, a reindex run
 * is the bulk way to analyze the files that predate the pipeline. With `app.vectorSearch.enabled` it also
 * holds vector indexing: the single writer of the vector embeddings table and its two feeds. Every child
 * keeps one construct id in both cases, so turning vector search on adds the indexer and changes no
 * logical id of the rest.
 */
export class VectorIndexingConstruct extends Construct {
    /** Vector search only. */
    public readonly vectorIndexerQueue?: sqs.Queue;
    public readonly systemWorkflowLaunchQueue: sqs.Queue;
    /** Vector search only. */
    public readonly vectorIndexerFunction?: lambda.Function;
    public readonly vectorReindexerFunction: lambda.Function;
    public readonly systemWorkflowLauncherFunction: lambda.Function;

    constructor(scope: Construct, id: string, props: VectorIndexingConstructProps) {
        super(scope, id);
        const { config, storageResources, lambdaCommonBaseLayer, vpc, subnets } = props;
        const vectorSearch = config.app.vectorSearch.enabled;
        const encryption = {
            encryption: storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: storageResources.encryption.kmsKey,
            enforceSSL: true,
        };

        // Queues are auto-named: a retained orphan from a failed teardown never collides with a redeploy.
        let vectorIndexerDlq: sqs.Queue | undefined;
        let embeddingReadyRuleDlq: sqs.Queue | undefined;
        if (vectorSearch) {
            vectorIndexerDlq = new sqs.Queue(this, "VectorIndexerDLQ", {
                retentionPeriod: DLQ_RETENTION,
                ...encryption,
            });
            this.vectorIndexerQueue = new sqs.Queue(this, "VectorIndexerQueue", {
                visibilityTimeout: CONSUMER_VISIBILITY_TIMEOUT,
                ...encryption,
                deadLetterQueue: {
                    queue: vectorIndexerDlq,
                    maxReceiveCount: QUEUE_MAX_RECEIVE_COUNT,
                },
            });

            // S3 lifecycle records (bucket-sync envelope) and asset table stream records reach the
            // indexer through the two topics the OpenSearch indexers consume; the topics exist
            // regardless of OpenSearch.
            storageResources.sns.fileIndexerSnsTopic.addSubscription(
                new SqsSubscription(this.vectorIndexerQueue)
            );
            storageResources.sns.assetIndexerSnsTopic.addSubscription(
                new SqsSubscription(this.vectorIndexerQueue)
            );

            // Embedding documents are announced on the orchestration bus by whichever pipeline
            // produced them.
            embeddingReadyRuleDlq = new sqs.Queue(this, "VectorEmbeddingReadyRuleDLQ", {
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
        }

        const launchDlq = new sqs.Queue(this, "SystemWorkflowLaunchDLQ", {
            retentionPeriod: DLQ_RETENTION,
            ...encryption,
        });
        this.systemWorkflowLaunchQueue = new sqs.Queue(this, "SystemWorkflowLaunchQueue", {
            visibilityTimeout: CONSUMER_VISIBILITY_TIMEOUT,
            ...encryption,
            deadLetterQueue: { queue: launchDlq, maxReceiveCount: QUEUE_MAX_RECEIVE_COUNT },
        });

        if (this.vectorIndexerQueue && vectorIndexerDlq) {
            this.vectorIndexerFunction = buildVectorIndexerFunction(
                this,
                storageResources,
                config,
                lambdaCommonBaseLayer,
                vpc,
                subnets,
                {
                    VECTOR_INDEXER_QUEUE_URL: this.vectorIndexerQueue.queueUrl,
                    VECTOR_INDEXER_DLQ_URL: vectorIndexerDlq.queueUrl,
                    WORKFLOW_LAUNCH_QUEUE_URL: this.systemWorkflowLaunchQueue.queueUrl,
                }
            );
        }
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

        this.systemWorkflowLaunchQueue.grantSendMessages(this.vectorReindexerFunction);
        if (this.vectorIndexerQueue && vectorIndexerDlq && this.vectorIndexerFunction) {
            // Asset- and file-wide rules that run out of time re-enqueue the rest of their work here, and
            // a rule past the continuation cap is parked in the queue's DLQ, from which a redrive resumes
            // it.
            this.vectorIndexerQueue.grantSendMessages(this.vectorIndexerFunction);
            vectorIndexerDlq.grantSendMessages(this.vectorIndexerFunction);
            // A restored file that was never embedded is queued onto the launch queue by the indexer.
            this.systemWorkflowLaunchQueue.grantSendMessages(this.vectorIndexerFunction);
        }

        // The reindexer continues a long run by invoking itself asynchronously. An asynchronous
        // invocation Lambda could not complete (a failure the continuation raises, a timeout, a crash, an
        // event that aged out while the function was throttled) is otherwise dropped, and the reindex ends
        // part-way with nothing recording that it did; the failed event lands here with its continuation
        // state, from which the run can be resumed. No retries: a retry re-runs the continuation from its
        // start state and sends again every file the failed attempt had already sent (each one analyzed
        // again), so a failure goes to the queue once and an operator resumes it.
        const reindexerAsyncDlq = new sqs.Queue(this, "VectorReindexerAsyncDLQ", {
            retentionPeriod: DLQ_RETENTION,
            ...encryption,
        });
        this.vectorReindexerFunction.configureAsyncInvoke({
            onFailure: new destinations.SqsDestination(reindexerAsyncDlq),
            retryAttempts: 0,
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

        if (this.vectorIndexerQueue && this.vectorIndexerFunction) {
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
        }
        // Every reindex run is paced by app.vectorSearch.indexingConcurrency, with vector search on or
        // off (its default applies when the block is absent).
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

        // A record in one of these DLQs is work that never completed: an embedding the indexer could not
        // write, a `vector.embedding.ready` event EventBridge could not enqueue (both vector search
        // only), a file the launcher never started the system GenAI workflow for, or a reindex
        // continuation Lambda could not complete, which stops the run part-way. Nothing self-heals, so
        // the alarm is the signal to redrive or resume. Named and configured as the file and asset
        // indexer DLQ alarms in the search stack; no alarm action, because VAMS has no notification
        // topic.
        const alarmedDlqs: [string, string, string, sqs.Queue][] = [];
        if (vectorIndexerDlq && embeddingReadyRuleDlq) {
            alarmedDlqs.push(
                [
                    "VectorIndexerDLQAlarm",
                    "vectorIndexerDLQ",
                    "Records that the VAMS vector indexer could not index, and asset- or file-wide rules " +
                        "that reached the indexer's 200-continuation cap, are in this dead-letter queue. " +
                        "Redrive it to the vector indexer queue after fixing any cause; a parked rule " +
                        "resumes where it stopped.",
                    vectorIndexerDlq,
                ],
                [
                    "VectorEmbeddingReadyRuleDLQAlarm",
                    "vectorEmbeddingReadyRuleDLQ",
                    "vector.embedding.ready events that Amazon EventBridge could not deliver to the VAMS " +
                        "vector indexer queue are in this dead-letter queue. A message move task cannot " +
                        "redrive it: re-run each named file's system GenAI execution, or the vector " +
                        "reindexer, after fixing the cause.",
                    embeddingReadyRuleDlq,
                ]
            );
        }
        alarmedDlqs.push(
            [
                "SystemWorkflowLaunchDLQAlarm",
                "systemWorkflowLaunchDLQ",
                "Launch messages that the VAMS system-workflow launcher could not start are in this " +
                    "dead-letter queue. Redrive it to the system-workflow launch queue after fixing the cause.",
                launchDlq,
            ],
            [
                "VectorReindexerAsyncDLQAlarm",
                "vectorReindexerAsyncDLQ",
                "Asynchronous invocations of the VAMS vector reindexer that failed are in this " +
                    "dead-letter queue: each is a reindex run that stopped part-way, on an error or at " +
                    "its 200-invocation cap. After fixing any cause, resume the run by invoking the " +
                    "reindexer asynchronously with the resumePayload in the message's error, or its " +
                    "requestPayload when the error has none.",
                reindexerAsyncDlq,
            ]
        );
        for (const [alarmId, nameSuffix, description, dlq] of alarmedDlqs) {
            new cloudwatch.Alarm(this, alarmId, {
                alarmName: `${config.name}-${config.app.baseStackName}-${nameSuffix}`,
                alarmDescription: description,
                metric: dlq.metricApproximateNumberOfMessagesVisible({
                    period: cdk.Duration.minutes(5),
                    statistic: cloudwatch.Stats.MAXIMUM,
                }),
                threshold: 0,
                comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                evaluationPeriods: 1,
                treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
            });
        }

        NagSuppressions.addResourceSuppressions(
            alarmedDlqs.map(([, , , dlq]) => dlq),
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
