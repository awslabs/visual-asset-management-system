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
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
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
 * asset, shared by every Lambda that runs it. The compliance tables the engine writes are granted
 * per builder, since the read/write split differs between them.
 */
function grantEvaluationEngineReads(fun: lambda.Function, storageResources: storageResources) {
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.metadataSchemaStorageTableV2.grantReadData(fun);
    storageResources.dynamo.workflowStorageTableV2.grantReadData(fun);
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
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTableV2.grantReadData(fun);
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
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildComplianceCascadeServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
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
        environment: {},
    });
    // Approving a cascade re-evaluates the downstream assets through the evaluation engine.
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
    storageResources.dynamo.pipelineStorageTableV2.grantReadData(fun);
    grantEvaluationEngineReads(fun, storageResources);
    executeWorkflowFunction.grantInvoke(fun);
    grantPublishToAssetTopics(fun, config);

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
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTableV2.grantReadData(fun);
    storageResources.dynamo.workflowStorageTableV2.grantReadData(fun);
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionOutputFilesStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionOutputMetadataStorageTable.grantReadData(fun);
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
    completionRule.addTarget(new eventsTargets.LambdaFunction(fun));

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
