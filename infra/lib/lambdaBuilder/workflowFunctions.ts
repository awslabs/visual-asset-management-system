/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { suppressCdkNagErrorsByGrantReadWrite } from "../helper/security";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import { Service, IAMArn } from "../helper/service-helper";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import * as events from "aws-cdk-lib/aws-events";
import * as eventsTargets from "aws-cdk-lib/aws-events-targets";
import * as s3AssetBuckets from "../helper/s3AssetBuckets";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    suppressCdkNagLambda,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    kmsKeyPolicyStatementGenerator,
} from "../helper/security";
import {
    grantReadWritePermissionsToAllAssetBuckets,
    grantReadPermissionsToAllAssetBuckets,
    grantExternalAssetBucketKmsKeys,
} from "../helper/security";
import * as logs from "aws-cdk-lib/aws-logs";

export function buildWorkflowService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "workflowService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.workflows.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {},
    });
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.workflowStorageTable.grantReadWriteData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "states:DeleteStateMachine",
                "states:DescribeStateMachine",
                "states:UpdateStateMachine",
            ],
            resources: [IAMArn("*" + config.name + "*").statemachine],
        })
    );
    return fun;
}

export function buildExecutionServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    workflowsLogGroup: logs.LogGroup,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "executionService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.workflows.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX). The shared workflow
            // SFN log group ARN is not an SSM resource-name parameter: used to pull error
            // logs for executions that ended in a non-success terminal status (e.g. a direct
            // SFN abort) and for the full-search logs API.
            WORKFLOW_EXECUTION_LOG_GROUP_ARN: workflowsLogGroup.logGroupArn,
        },
    });
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadWriteData(fun); // write for lazy status reconciliation + abort
    storageResources.dynamo.workflowExecutionInputsStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadWriteData(fun); // write to mark pipeline rows ABORTED
    // Read-only sources for the details + logs APIs.
    storageResources.dynamo.workflowExecutionConfigurationStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionInputFilesStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionInputMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionInputConfigurationStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionOutputFilesStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionOutputMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionOutputResultsStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineExecutionLogsStorageTable.grantReadData(fun);
    storageResources.dynamo.workflowStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileVersionHistoryStorageTable.grantReadData(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:DescribeExecution", "states:StopExecution"],
            resources: [
                IAMArn("*" + config.name + "*").statemachine,
                IAMArn("*" + config.name + "*").statemachineExecution,
            ],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"],
            // Scoped to VAMS-named log groups (the workflow SFN log group contains 'vams').
            resources: [IAMArn("*" + config.name + "*").loggroup],
        })
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

    return fun;
}

export function buildCreateWorkflowFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    processWorkflowExecutionOutputFunction: lambda.Function,
    interimPipelineTrackingFunction: lambda.Function,
    handleExecutionErrorFunction: lambda.Function,
    workflowsLogGroup: logs.LogGroup,
    stackName: string,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const logGroupWorkflows = workflowsLogGroup;

    const role = buildWorkflowRole(
        scope,
        processWorkflowExecutionOutputFunction,
        config,
        storageResources.encryption.kmsKey
    );
    const name = "createWorkflow";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.workflows.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME:
                processWorkflowExecutionOutputFunction.functionName,
            // Interim pipeline-tracking + error-handler lambda names, embedded into the
            // generated ASL (interim states between pipelines; error-handler catch state).
            INTERIM_PIPELINE_TRACKING_LAMBDA_FUNCTION_NAME:
                interimPipelineTrackingFunction.functionName,
            HANDLE_EXECUTION_ERROR_LAMBDA_FUNCTION_NAME: handleExecutionErrorFunction.functionName,
            VAMS_STACK_NAME: stackName,
            LAMBDA_ROLE_ARN: role.roleArn,
            LOG_GROUP_ARN: logGroupWorkflows.logGroupArn,
            // Deployment partition for the Step Functions service-integration ARNs embedded in
            // the generated ASL (arn:{partition}:states:::...), so workflows are valid in
            // GovCloud/China/ISO partitions and not just commercial "aws".
            AWS_PARTITION: config.env.partition,
        },
    });
    storageResources.dynamo.workflowStorageTable.grantReadWriteData(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "states:CreateStateMachine",
                "states:DescribeStateMachine",
                "states:UpdateStateMachine",
            ],
            resources: [IAMArn("*" + config.name + "*").statemachine],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["iam:PassRole"],
            resources: [IAMArn("*" + config.name + "*").role],
        })
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

export function buildExecuteWorkflowFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    metadataServiceFunction: lambda.IFunction,
    workflowsLogGroup: logs.LogGroup,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "executeWorkflow";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.workflows.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            // Table/bucket names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX).
            METADATA_SERVICE_LAMBDA_FUNCTION_NAME: metadataServiceFunction.functionName,
            WORKFLOW_EXECUTION_LOG_GROUP_ARN: workflowsLogGroup.logGroupArn,
            // Orchestration bus ARN + event source prefix written into each pipeline's
            // manifest.systemConfig so a pipeline can optionally register sub-process ARNs.
            ORCHESTRATION_BUS_ARN: storageResources.eventBridge.orchestrationBus.eventBusArn,
            ORCHESTRATION_EVENT_SOURCE_PREFIX: storageResources.eventBridge.eventSourcePrefix,
        },
    });

    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.workflowStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionInputFilesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionInputMetadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionInputConfigurationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.workflowExecutionInputsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.workflowExecutionConfigurationStorageTable.grantReadWriteData(fun);
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);
    metadataServiceFunction.grantInvoke(fun);

    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "states:StartExecution",
                "states:DescribeStateMachine",
                "states:DescribeExecution",
            ],
            resources: [
                IAMArn("*" + config.name + "*").statemachine,
                IAMArn("*" + config.name + "*").statemachineExecution,
            ],
        })
    );
    return fun;
}

export function buildSqsAutoExecuteWorkflowFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    executeWorkflowFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "sqsAutoExecuteWorkflow";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.workflows.${name}.lambda_handler`,
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
            EXECUTE_WORKFLOW_LAMBDA_FUNCTION_NAME: executeWorkflowFunction.functionName,
        },
    });

    // Grant DynamoDB permissions
    storageResources.dynamo.workflowStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);

    // Grant invoke permission to executeWorkflow Lambda
    executeWorkflowFunction.grantInvoke(fun);

    //grant asset bucket permissions
    grantReadPermissionsToAllAssetBuckets(fun);

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildProcessWorkflowExecutionOutputFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    fileUploadLambdaFunction: lambda.Function,
    metadataServiceFunction: lambda.IFunction,
    workflowsLogGroup: logs.LogGroup,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "processWorkflowExecutionOutput";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        // SFN-invoked execution handlers live under handlers/workflows/sfn/.
        handler: `handlers.workflows.sfn.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX).
            FILE_UPLOAD_LAMBDA_FUNCTION_NAME: fileUploadLambdaFunction.functionName,
            METADATA_SERVICE_LAMBDA_FUNCTION_NAME: metadataServiceFunction.functionName,
            WORKFLOW_EXECUTION_LOG_GROUP_ARN: workflowsLogGroup.logGroupArn,
        },
    });

    fileUploadLambdaFunction.grantInvoke(fun);
    metadataServiceFunction.grantInvoke(fun);

    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetUploadsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionOutputFilesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionOutputMetadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionOutputResultsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionLogsStorageTable.grantReadWriteData(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"],
            // Scoped to VAMS-named log groups (state machine + lambda logs contain 'vams').
            resources: [IAMArn("*" + config.name + "*").loggroup],
        })
    );

    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    return fun;
}

export function buildInterimPipelineTrackingFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    workflowsLogGroup: logs.LogGroup,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "interimPipelineTracking";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        // SFN-invoked execution handlers live under handlers/workflows/sfn/.
        handler: `handlers.workflows.sfn.${name}.lambda_handler`,
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
            // DynamoDB table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX). Only non-SSM
            // values are set here: the shared workflow SFN log group ARN and the orchestration
            // bus ARN + event source prefix written into each next pipeline's manifest.
            WORKFLOW_EXECUTION_LOG_GROUP_ARN: workflowsLogGroup.logGroupArn,
            ORCHESTRATION_BUS_ARN: storageResources.eventBridge.orchestrationBus.eventBusArn,
            ORCHESTRATION_EVENT_SOURCE_PREFIX: storageResources.eventBridge.eventSourcePrefix,
        },
    });
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionOutputFilesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.workflowExecutionInputsStorageTable.grantReadData(fun);
    // Reads original input files + output-files folder, and writes the next pipeline's
    // resolved input manifest into the asset bucket execution input folder.
    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    return fun;
}

export function buildHandleExecutionErrorFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    workflowsLogGroup: logs.LogGroup,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "handleExecutionError";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        // SFN-invoked execution handlers live under handlers/workflows/sfn/.
        handler: `handlers.workflows.sfn.${name}.lambda_handler`,
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
            // DynamoDB table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX). Only the
            // non-SSM shared workflow SFN log group ARN is set here (used to pull failed-run logs).
            WORKFLOW_EXECUTION_LOG_GROUP_ARN: workflowsLogGroup.logGroupArn,
        },
    });
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionLogsStorageTable.grantReadWriteData(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"],
            resources: [IAMArn("*" + config.name + "*").loggroup],
        })
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    return fun;
}

export function buildRegisterPipelineExecutionFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "registerPipelineExecution";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        // SFN-adjacent execution handler (EventBridge-invoked) under handlers/workflows/sfn/.
        handler: `handlers.workflows.sfn.${name}.lambda_handler`,
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
        // DynamoDB table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX), set by
        // globalLambdaEnvironmentsAndPermissions below; no per-table env vars are needed.
        environment: {},
    });
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadWriteData(fun);

    // Standing EventBridge rule: route this deployment's pipeline.execution.register events to
    // the registration lambda. One rule matches every execution/pipeline by the source prefix;
    // the lambda routes to the exact pipeline row by detail.pipelineExecutionId.
    const registerRule = new events.Rule(scope, "PipelineExecutionRegisterRule", {
        eventBus: storageResources.eventBridge.orchestrationBus,
        eventPattern: {
            source: events.Match.prefix(storageResources.eventBridge.eventSourcePrefix),
            detailType: ["pipeline.execution.register"],
        },
    });
    registerRule.addTarget(new eventsTargets.LambdaFunction(fun));

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    return fun;
}

export function buildWorkflowRole(
    scope: Construct,
    processWorkflowExecutionOutputFunction: lambda.Function,
    config: Config.Config,
    kmsKey?: kms.IKey
): iam.Role {
    const createWorkflowPolicy = new iam.PolicyDocument({
        statements: [
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["states:CreateStateMachine"],
                resources: [IAMArn("*" + config.name + "*").statemachine],
            }),
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["events:PutTargets", "events:PutRule", "events:DescribeRule"],
                resources: [IAMArn("*" + config.name + "*").stateMachineEvents],
            }),
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: [
                    "logs:CreateLogDelivery",
                    "logs:GetLogDelivery",
                    "logs:UpdateLogDelivery",
                    "logs:DeleteLogDelivery",
                    "logs:ListLogDeliveries",
                    "logs:PutResourcePolicy",
                    "logs:DescribeResourcePolicies",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:CreateLogGroup",
                    "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups",
                ],
                //"*"" Resource policy required as per AWS documentation as CloudWatch API doesn't support resource types
                //https://docs.aws.amazon.com/step-functions/latest/dg/cw-logs.html
                resources: ["*"],
            }),
        ],
    });

    //https://docs.aws.amazon.com/step-functions/latest/dg/stepfunctions-iam.html
    const runWorkflowPolicy = new iam.PolicyDocument({
        statements: [
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: [
                    "cloudwatch:PutMetricData",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:CreateLogGroup",
                    "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups",
                ],
                //"*"" Resource policy required as per AWS documentation as CloudWatch API doesn't support resource types
                //https://docs.aws.amazon.com/step-functions/latest/dg/cw-logs.html
                resources: ["*"],
            }),
            // Add permissions for all asset buckets from the global array
            ...s3AssetBuckets.getS3AssetBucketRecords().map((record) => {
                const prefix = record.prefix || "/";
                // Build the object-level resource as {bucketArn}/{prefix}*. Strip any
                // leading slash from the prefix so the '/' separator after the bucket
                // ARN is always present (root prefix yields {bucketArn}/*).
                const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";
                const objectPrefix = normalizedPrefix.replace(/^\/+/, "");

                return new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["s3:ListBucket", "s3:PutObject", "s3:GetObject"],
                    resources: [
                        record.bucket.bucketArn,
                        `${record.bucket.bucketArn}/${objectPrefix}*`,
                    ],
                });
            }),
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["lambda:InvokeFunction"],
                resources: [processWorkflowExecutionOutputFunction.functionArn],
            }),
            // For lambda pipelines created through lambda functions
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["lambda:InvokeFunction"],
                resources: [IAMArn("*" + config.name + "*").lambda],
            }),
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["iam:PassRole"],
                resources: [IAMArn("*" + config.name + "*").role],
            }),
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["iam:PassRole"],
                resources: [IAMArn("*" + config.name + "*").role],
            }),
            // SQS SendMessage permission for SQS pipeline types
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["sqs:SendMessage"],
                resources: [IAMArn("*" + config.name + "*").sqs],
            }),
            // EventBridge PutEvents permission for EventBridge pipeline types
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["events:PutEvents"],
                resources: [IAMArn("*" + config.name + "*").eventBus, IAMArn("default").eventBus],
            }),
        ],
    });

    //Add KMS key use if provided
    if (kmsKey) {
        runWorkflowPolicy.addStatements(kmsKeyPolicyStatementGenerator(kmsKey));
    }

    const role = new iam.Role(scope, "VAMSWorkflowIAMRole", {
        assumedBy: new iam.CompositePrincipal(
            Service("LAMBDA").Principal,
            Service("STATES").Principal
        ),
        description: "VAMS Workflow IAM Role.",
        inlinePolicies: {
            createWorkflowPolicy: createWorkflowPolicy,
            runWorkflowPolicy: runWorkflowPolicy,
        },
        managedPolicies: [
            iam.ManagedPolicy.fromAwsManagedPolicyName(
                "service-role/AWSLambdaVPCAccessExecutionRole"
            ),
        ],
    });

    // Grant access to any external asset bucket customer managed KMS keys so the
    // workflow role can read/write objects in cross-account encrypted buckets
    // (no-op when no external keys are configured)
    grantExternalAssetBucketKmsKeys(role);

    return role;
}

export function buildImportGlobalPipelineWorkflowFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    createPipelineFunction: lambda.Function,
    pipelineServiceFunction: lambda.Function,
    createWorkflowFunction: lambda.Function,
    workflowServiceFunction: lambda.Function
): lambda.Function {
    const name = "importGlobalPipelineWorkflow";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.workflows.${name}.lambda_handler`,
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
            // Service function names - set directly from function parameters
            CREATE_PIPELINE_FUNCTION_NAME: createPipelineFunction.functionName,
            PIPELINE_SERVICE_FUNCTION_NAME: pipelineServiceFunction.functionName,
            CREATE_WORKFLOW_FUNCTION_NAME: createWorkflowFunction.functionName,
            WORKFLOW_SERVICE_FUNCTION_NAME: workflowServiceFunction.functionName,
        },
    });

    // Grant invoke permissions to the service functions directly
    createPipelineFunction.grantInvoke(fun);
    pipelineServiceFunction.grantInvoke(fun);
    createWorkflowFunction.grantInvoke(fun);
    workflowServiceFunction.grantInvoke(fun);

    // Apply standard security helper functions
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
