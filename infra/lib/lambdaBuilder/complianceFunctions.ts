/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as events from "aws-cdk-lib/aws-events";
import * as eventsTargets from "aws-cdk-lib/aws-events-targets";
import * as destinations from "aws-cdk-lib/aws-lambda-destinations";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as sns from "aws-cdk-lib/aws-sns";
import * as kms from "aws-cdk-lib/aws-kms";
import { SqsSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import { NagSuppressions } from "cdk-nag";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as Service from "../helper/service-helper";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    suppressCdkNagLambda,
    suppressCdkNagErrorsByGrantReadWrite,
    grantReadPermissionsToAllAssetBuckets,
} from "../helper/security";

/**
 * Detail type of the workflow-completion event the Step Functions end-state and error handlers
 * publish to the orchestration bus. The compliance callback subscribes to it by this value.
 */
export const WORKFLOW_EXECUTION_COMPLETED_DETAIL_TYPE = "workflow.execution.completed";

/**
 * Grants publish on the per-asset subscription topics the compliance notifications helper
 * publishes to. The topics are named AssetTopic<assetId> and created at runtime, so the exact
 * ARN is not known at synthesis; the account and Region are, and the wildcard is over the asset
 * id only.
 */
function grantPublishToAssetTopics(fun: lambda.Function, config: Config.Config): void {
    const assetTopicWildcardArn = cdk.Fn.sub(
        `arn:${Service.Partition()}:sns:${config.env.region}:${config.env.account}:AssetTopic*`
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["sns:Publish"],
            resources: [assetTopicWildcardArn],
        })
    );
}

/**
 * Tables the evaluation engine (common/compliance/evaluationEngine.py) reads while evaluating an
 * asset, shared by every Lambda that runs it: asset metadata and links, the metadata schema a rule
 * references, the workflow, pipeline and pipeline-template rows a pipeline rule launches with, the
 * asset-version file records a version-coverage rule compares the current files against, and the
 * asset buckets (the current-file listing of file-structure and version-coverage rules, a pipeline
 * rule's input selection). The compliance tables the engine writes are granted per builder, since
 * the read/write split differs between them.
 */
function grantEvaluationEngineReads(fun: lambda.Function, storageResources: storageResources) {
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
    // Metadata rules of the fileAttributes and links scopes.
    storageResources.dynamo.fileAttributeStorageTable.grantReadData(fun);
    storageResources.dynamo.assetLinksMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileVersionsStorageTable.grantReadData(fun);
    storageResources.dynamo.metadataSchemaStorageTableV2.grantReadData(fun);
    storageResources.dynamo.workflowStorageTableV2.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTableV2.grantReadData(fun);
    storageResources.dynamo.pipelineTemplatesStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    grantReadPermissionsToAllAssetBuckets(fun);
}

export function buildComplianceSchemaServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "complianceSchemaService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX).
        environment: {},
    });
    storageResources.dynamo.complianceSchemaStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadData(fun);
    // Schema create, new-version and delete each record a compliance audit entry.
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildComplianceSchemaBindingServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "complianceSchemaBindingService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX).
        environment: {},
    });
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    // A database binding is recorded on the database row itself.
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildComplianceEvaluateServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    executeWorkflowFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "complianceEvaluateService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX). Pipeline rules launch a
            // workflow through the V2 execute Lambda.
            EXECUTE_WORKFLOW_FUNCTION_NAME: executeWorkflowFunction.functionName,
        },
    });
    storageResources.dynamo.complianceSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceEvaluationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceCascadeStorageTable.grantReadWriteData(fun);
    // An evaluation without a schema falls back to the database binding, and a sweep finds the
    // databases bound to its schema, both on the database rows.
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    grantEvaluationEngineReads(fun, storageResources);
    executeWorkflowFunction.grantInvoke(fun);
    grantPublishToAssetTopics(fun, config);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildComplianceQuarantineServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "complianceQuarantineService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX).
        environment: {},
    });
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    // An exception is scoped to the bound schema's current internalVersion, and revoking one
    // restores the state of the asset's last evaluation.
    storageResources.dynamo.complianceSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.complianceEvaluationStorageTable.grantReadData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    // A listing filtered by a database id that names no database answers 404 (keys-only reads).
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    // Revoking an exception can re-quarantine the asset, which notifies its subscribers.
    grantPublishToAssetTopics(fun, config);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

/**
 * Cascade executor. Invoked asynchronously by the cascade service once a cascade row is in the
 * `executing` state; it evaluates the trigger asset's downstream descendants through the evaluation
 * engine, which launches a workflow execution for each pipeline rule, and records per-node progress
 * and the terminal state on the cascade row. No API route.
 */
export function buildComplianceCascadeExecutorFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    executeWorkflowFunction: lambda.Function
): lambda.Function {
    const name = "complianceCascadeExecutor";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX). Pipeline rules launch a
            // workflow through the V2 execute Lambda.
            EXECUTE_WORKFLOW_FUNCTION_NAME: executeWorkflowFunction.functionName,
        },
    });
    executeWorkflowFunction.grantInvoke(fun);
    storageResources.dynamo.complianceCascadeStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceEvaluationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceSchemaStorageTable.grantReadData(fun);
    grantEvaluationEngineReads(fun, storageResources);
    grantPublishToAssetTopics(fun, config);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

/**
 * Cascade API. Creating a cascade without approval, or approving a pending one, writes the row
 * in the `executing` state and hands it to the cascade executor with an asynchronous invoke; the
 * request returns 202 and clients poll GET /compliance/cascades/{cascadeId} for the terminal state.
 */
export function buildComplianceCascadeServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    cascadeExecutorFunction: lambda.Function
): lambda.Function {
    const name = "complianceCascadeService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX).
        environment: {
            COMPLIANCE_CASCADE_EXECUTOR_FUNCTION_NAME: cascadeExecutorFunction.functionName,
        },
    });
    cascadeExecutorFunction.grantInvoke(fun);
    storageResources.dynamo.complianceCascadeStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    // The trigger asset's existence is checked on the asset row before a cascade is created.
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    // An audit entry records the asset's name at the time of the event, read from its compliance
    // record when the caller does not pass it.
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadData(fun);
    // Approving or starting a cascade records the databases the caller may evaluate, read from the
    // database ids.
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildComplianceAuditServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "complianceAuditService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX).
        environment: {},
    });
    storageResources.dynamo.complianceAuditStorageTable.grantReadData(fun);
    // The entries of an archived asset are listed only for a caller allowed on its stored database
    // id, so the service reads whether an asset is archived (its key under `<databaseId>#deleted`).
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    // A listing filtered by a database id that names no database answers 404 (keys-only reads).
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

/**
 * Settings of the compliance trigger and the queue it consumes, exported so the synth tests assert
 * the values the builder uses.
 *
 * - The function timeout bounds one batch: at most `COMPLIANCE_TRIGGER_BATCH_SIZE` messages,
 *   de-duplicated per asset, so at most that many evaluations, each a few DynamoDB reads and writes
 *   and, for a pipeline rule, one workflow launch.
 * - The visibility timeout is six times the function timeout, the AWS guidance for an SQS event
 *   source, so a batch still running (or retried by Lambda after a throttle) is not redelivered to a
 *   second invocation meanwhile.
 * - Three receives before the dead-letter queue: an infrastructure error fails only its own record
 *   (`reportBatchItemFailures`), but an invocation that fails as a whole (a timeout, an error the
 *   handler cannot attribute to one record) redelivers the whole batch, and an upload completion
 *   held back to settle spends one receive, so a record's receives also count its batch's failures.
 *   A record still failing after three deliveries is an operator's to redrive within the 14-day
 *   retention.
 * - `maxConcurrency` caps the concurrent trigger invocations this queue drives, so a burst of
 *   changes (a bulk upload, a large binding) queues here instead of drawing the account's Lambda
 *   concurrency away from the API handlers; at 5 invocations of up to 10 messages each, a
 *   1000-asset burst drains in minutes, which an asynchronous compliance check tolerates.
 */
export const COMPLIANCE_TRIGGER_TIMEOUT = Duration.minutes(5);
export const COMPLIANCE_TRIGGER_QUEUE_VISIBILITY_TIMEOUT = Duration.minutes(30);
export const COMPLIANCE_TRIGGER_QUEUE_MAX_RECEIVE_COUNT = 3;
export const COMPLIANCE_TRIGGER_BATCH_SIZE = 10;
export const COMPLIANCE_TRIGGER_BATCHING_WINDOW = Duration.seconds(5);
export const COMPLIANCE_TRIGGER_MAX_CONCURRENCY = 5;

/**
 * Message-body filter policies of the trigger queue's two topic subscriptions. Both topics carry
 * the whole DynamoDB stream record (`snsQueuing`) or the file indexer's S3 message (`sqsBucketSync`)
 * as the SNS message body, and publish no message attributes, so the policies filter on the body.
 *
 * - Asset indexer topic: DynamoDB stream records only (the asset, asset-file-metadata, asset-link
 *   and asset-link-metadata tables), told apart by their top-level `eventSource`.
 * - File indexer topic: the S3 file-event messages and the file-attribute table's records. The
 *   asset-file-metadata table's records are published to both topics; they are taken from the
 *   asset indexer topic only, so each reaches the queue once. The file topic's policy drops a
 *   record whose `dynamodb.Keys.metadataKey.S` exists: SNS applies `exists` to leaf values only,
 *   and in a stream record `Keys.metadataKey` is the attribute-value object `{"S": ...}`, so a
 *   check on `Keys.metadataKey` itself never finds a leaf and admits every record. The file
 *   attribute table keys on `attributeKey` and an S3 message has no `dynamodb`, so both pass.
 *
 * test/platform/complianceTriggerQueue.test.ts runs both synthesized policies over a sample of
 * every message kind each topic carries (test/platform/fixtures/indexerTopicMessages.json).
 */
export function complianceTriggerAssetTopicFilter(): { [key: string]: sns.FilterOrPolicy } {
    return {
        eventSource: sns.FilterOrPolicy.filter(
            sns.SubscriptionFilter.stringFilter({ allowlist: ["aws:dynamodb"] })
        ),
    };
}

export function complianceTriggerFileTopicFilter(): { [key: string]: sns.FilterOrPolicy } {
    return {
        dynamodb: sns.FilterOrPolicy.policy({
            Keys: sns.FilterOrPolicy.policy({
                metadataKey: sns.FilterOrPolicy.policy({
                    S: sns.FilterOrPolicy.filter(sns.SubscriptionFilter.notExistsFilter()),
                }),
            }),
        }),
    };
}

/**
 * The compliance trigger's queue (`ComplianceTriggerQueue`, with `ComplianceTriggerDLQ`) and its two
 * filtered topic subscriptions. Built in the storage stack, beside the indexer topics, not with the
 * trigger in ApiBuilder2: CloudFormation updates the storage stack before ApiBuilder, whose request
 * Lambdas no longer record metadata and link changes themselves, and ApiBuilder2 only after it. With
 * the queue and its subscriptions created first, every indexer message from the storage update on
 * waits in the queue until the trigger's event source starts reading it, so no change made while
 * the stacks update goes unrecorded (it is stamped with its own instant when it is read).
 */
export function buildComplianceTriggerQueue(
    scope: Construct,
    topics: { assetIndexerSnsTopic: sns.ITopic; fileIndexerSnsTopic: sns.ITopic },
    kmsKey?: kms.IKey
): sqs.Queue {
    // Encryption mirrors the source queue: a message moved into a DLQ the trigger's role cannot
    // decrypt is unreadable dead letter.
    const triggerDlq = new sqs.Queue(scope, "ComplianceTriggerDLQ", {
        retentionPeriod: cdk.Duration.days(14),
        encryption: kmsKey ? sqs.QueueEncryption.KMS : sqs.QueueEncryption.SQS_MANAGED,
        encryptionMasterKey: kmsKey,
        enforceSSL: true,
    });
    NagSuppressions.addResourceSuppressions(triggerDlq, [
        {
            id: "AwsSolutions-SQS3",
            reason:
                "This queue IS the dead-letter queue of the compliance trigger queue. A DLQ is the " +
                "terminal destination for records the trigger could not process after its receive " +
                "limit, so giving it a redrive policy of its own would only defer the same failure " +
                "to a further queue.",
        },
    ]);

    const triggerQueue = new sqs.Queue(scope, "ComplianceTriggerQueue", {
        visibilityTimeout: COMPLIANCE_TRIGGER_QUEUE_VISIBILITY_TIMEOUT,
        encryption: kmsKey ? sqs.QueueEncryption.KMS : sqs.QueueEncryption.SQS_MANAGED,
        encryptionMasterKey: kmsKey,
        enforceSSL: true,
        deadLetterQueue: {
            queue: triggerDlq,
            maxReceiveCount: COMPLIANCE_TRIGGER_QUEUE_MAX_RECEIVE_COUNT,
        },
    });

    // One subscription per topic. Each adds a queue-policy statement letting only that topic send
    // (aws:SourceArn), so nothing else can write into the queue.
    topics.assetIndexerSnsTopic.addSubscription(
        new SqsSubscription(triggerQueue, {
            filterPolicyWithMessageBody: complianceTriggerAssetTopicFilter(),
        })
    );
    topics.fileIndexerSnsTopic.addSubscription(
        new SqsSubscription(triggerQueue, {
            filterPolicyWithMessageBody: complianceTriggerFileTopicFilter(),
        })
    );
    return triggerQueue;
}

/**
 * Asset-event trigger. Consumes the asset and file indexer SNS topics through its own SQS queue
 * (`ComplianceTriggerQueue`, one filtered subscription per topic, a dead-letter queue, batched and
 * concurrency-capped); an asset or file change on a database or asset bound to a compliance schema
 * starts an evaluation, which may launch a workflow for pipeline rules and open a cascade for
 * downstream assets. The topics are fed by the table-stream relays and the bucket sync that every
 * deployment builds, with or without OpenSearch, so compliance does not depend on search.
 */
export function buildComplianceTriggerFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    executeWorkflowFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "complianceTrigger";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: COMPLIANCE_TRIGGER_TIMEOUT,
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX). Pipeline rules launch a
            // workflow through the V2 execute Lambda.
            EXECUTE_WORKFLOW_FUNCTION_NAME: executeWorkflowFunction.functionName,
        },
    });
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceEvaluationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceCascadeStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    grantEvaluationEngineReads(fun, storageResources);
    // Records the metadata and link changes it reads from the indexer events on the asset row
    // (common/assetProvenance.record_indexed_change): one conditional UpdateItem, no other write.
    storageResources.dynamo.assetStorageTable.grant(fun, "dynamodb:UpdateItem");
    executeWorkflowFunction.grantInvoke(fun);
    grantPublishToAssetTopics(fun, config);
    // Reads the changed object's metadata (head_object across asset buckets) to skip files a
    // workflow execution wrote, so a pipeline rule's own output does not re-trigger the evaluation.
    grantReadPermissionsToAllAssetBuckets(fun);

    const triggerQueue = storageResources.sqs.complianceTriggerQueue;
    triggerQueue.grantConsumeMessages(fun);
    // The trigger holds an upload completion's record back on this queue until the completion has
    // settled (ChangeMessageVisibility, part of the consume grant), so consecutive completions of
    // one multi-request upload are evaluated once (complianceTrigger.UPLOAD_COMPLETION_SETTLE_SECONDS).
    fun.addEnvironment("COMPLIANCE_TRIGGER_QUEUE_URL", triggerQueue.queueUrl);

    // GovCloud and EU Sovereign Lambda reject Tags on an event source mapping, which the high-level
    // addEventSource() stamps from the stack tags; there the mapping is built directly and its Tags
    // removed (test/partition/eventSourceMappingGovCloudTags.test.ts).
    if (config.app.govCloud.enabled) {
        const esm = new lambda.EventSourceMapping(scope, "ComplianceTriggerSqsEventSource", {
            eventSourceArn: triggerQueue.queueArn,
            target: fun,
            batchSize: COMPLIANCE_TRIGGER_BATCH_SIZE,
            maxBatchingWindow: COMPLIANCE_TRIGGER_BATCHING_WINDOW,
            // Emits FunctionResponseTypes: ["ReportBatchItemFailures"]. Without it Lambda ignores
            // the handler's batchItemFailures and deletes the whole batch on a 200.
            reportBatchItemFailures: true,
            maxConcurrency: COMPLIANCE_TRIGGER_MAX_CONCURRENCY,
        });
        (esm.node.defaultChild as lambda.CfnEventSourceMapping).addPropertyDeletionOverride("Tags");
    } else {
        fun.addEventSource(
            new eventsources.SqsEventSource(triggerQueue, {
                batchSize: COMPLIANCE_TRIGGER_BATCH_SIZE,
                maxBatchingWindow: COMPLIANCE_TRIGGER_BATCHING_WINDOW,
                reportBatchItemFailures: true,
                maxConcurrency: COMPLIANCE_TRIGGER_MAX_CONCURRENCY,
            })
        );
    }

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

/**
 * Workflow-completion callback for pipeline rules. The Step Functions end-state and error
 * handlers publish a `workflow.execution.completed` event to the orchestration bus; a standing
 * rule on the deployment's event-source prefix routes it here. The callback resolves the
 * evaluation by executionId (ExecutionIdIndex), reads the pipeline's measurements from the V2
 * execution output records and the asset bucket, and records the verdict.
 */
export function buildComplianceWorkflowCallbackFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "complianceWorkflowCallback";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        // Table and bucket names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX); output locations
        // come from the V2 execution records.
        environment: {},
    });
    storageResources.dynamo.complianceEvaluationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    // Finalizing a verdict compares an active exception against the schema's current version.
    storageResources.dynamo.complianceSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTableV2.grantReadData(fun);
    storageResources.dynamo.workflowStorageTableV2.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionOutputResultsStorageTable.grantReadData(fun);
    grantReadPermissionsToAllAssetBuckets(fun);
    grantPublishToAssetTopics(fun, config);

    const completionRule = new events.Rule(scope, "ComplianceWorkflowCompletionRule", {
        eventBus: storageResources.eventBridge.orchestrationBus,
        eventPattern: {
            source: events.Match.prefix(storageResources.eventBridge.eventSourcePrefix),
            detailType: [WORKFLOW_EXECUTION_COMPLETED_DETAIL_TYPE],
        },
    });

    // The completion event is the only signal that resolves a pipeline-rule evaluation: the
    // evaluation sits in `pending_pipeline` until the callback records the verdict. Without a
    // dead-letter queue, EventBridge discards a persistently failing delivery after its own retries
    // and nothing records that it happened, leaving the evaluation pending with no trace. The queue
    // holds the undeliverable events for an operator to redrive. It receives both kinds of failure:
    // an event EventBridge could not hand to the function (the rule target's dead-letter queue) and
    // an invocation the function itself failed after Lambda's asynchronous retries (the function's
    // on-failure destination) — the callback raises for a completion it cannot resolve yet rather
    // than answering it as processed, so such a completion is retried and, if it keeps failing, kept.
    const completionDlq = new sqs.Queue(scope, "ComplianceWorkflowCompletionDLQ", {
        encryption: storageResources.encryption.kmsKey
            ? sqs.QueueEncryption.KMS
            : sqs.QueueEncryption.SQS_MANAGED,
        encryptionMasterKey: storageResources.encryption.kmsKey,
        enforceSSL: true,
    });
    NagSuppressions.addResourceSuppressions(completionDlq, [
        {
            id: "AwsSolutions-SQS3",
            reason:
                "This queue is itself the dead-letter target for the compliance workflow-completion " +
                "EventBridge rule and the callback function's asynchronous-invocation failure " +
                "destination, so it does not take a further dead-letter queue.",
        },
    ]);

    completionRule.addTarget(
        new eventsTargets.LambdaFunction(fun, {
            deadLetterQueue: completionDlq,
            retryAttempts: 3,
            maxEventAge: Duration.hours(1),
        })
    );
    fun.configureAsyncInvoke({
        retryAttempts: 2,
        maxEventAge: Duration.hours(1),
        onFailure: new destinations.SqsDestination(completionDlq),
    });

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
