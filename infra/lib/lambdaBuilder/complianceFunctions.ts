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
 * references, the workflow, pipeline and pipeline-template rows a pipeline rule launches with, and
 * the asset buckets. The compliance tables the engine writes are granted per builder, since the
 * read/write split differs between them.
 */
function grantEvaluationEngineReads(fun: lambda.Function, storageResources: storageResources) {
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
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
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

/**
 * Asset-event trigger. Subscribed to the asset and file indexer SNS topics; an asset or file
 * change on a database or asset bound to a compliance schema starts an evaluation, which may
 * launch a workflow for pipeline rules and open a cascade for downstream assets.
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
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceEvaluationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceCascadeStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    grantEvaluationEngineReads(fun, storageResources);
    executeWorkflowFunction.grantInvoke(fun);
    grantPublishToAssetTopics(fun, config);
    // Reads the changed object's metadata (head_object across asset buckets) to skip files a
    // workflow execution wrote, so a pipeline rule's own output does not re-trigger the evaluation.
    grantReadPermissionsToAllAssetBuckets(fun);

    fun.addEventSource(new eventsources.SnsEventSource(storageResources.sns.assetIndexerSnsTopic));
    fun.addEventSource(new eventsources.SnsEventSource(storageResources.sns.fileIndexerSnsTopic));

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
