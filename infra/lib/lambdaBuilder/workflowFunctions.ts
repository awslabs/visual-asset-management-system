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
import { Service, IAMArn, Partition } from "../helper/service-helper";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import * as events from "aws-cdk-lib/aws-events";
import * as eventsTargets from "aws-cdk-lib/aws-events-targets";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import * as sqs from "aws-cdk-lib/aws-sqs";
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
import { NagSuppressions } from "cdk-nag";

// Workflow state machines and auto-provisioned pipeline Lambdas are named at runtime by the
// backend with a fixed lowercase 'vams-' prefix that does not embed config.name, so both the
// config-name pattern and this prefix are granted.
const BACKEND_GENERATED_NAME_PATTERN = "vams-*";

// Use-case pipeline sub-state-machines are named by CDK from their construct ids
// (e.g. 'CosmosReasonPipelineCosmosReasonreason2BStateMachineB865922C-YV2FQMz87VHM',
// 'PcPotreeViewerProcessingStateMachine92DD7A15-90NUO0WAz4Ct'), so they carry neither the config
// name nor the 'vams-' prefix. Aborting a workflow execution has to stop the registered sub-execution
// as well, or the parent is marked ABORTED while the pipeline state machine — and the AWS Batch job
// it is waiting on — keeps running. IAM offers no name pattern that covers these, so the abort and
// log-read scope for sub-executions is account/region-wide on Step Functions executions.
const PIPELINE_SUB_STATE_MACHINE_PATTERN = "*";

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
        // Table names resolve from SSM (VAMS_RESOURCE_PARAM_PREFIX). The log group each execution
        // was launched against is read from that execution's own record (executionLogGroupArn), so
        // no log group ARN is set here; the read scope is granted on the role policy below.
        environment: {},
    });
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadWriteData(fun); // write for lazy status reconciliation + abort + permanent-delete
    // Permanent-delete removes an execution's rows across every sub-table, so the execution
    // sub-tables are read/WRITE.
    storageResources.dynamo.workflowExecutionInputsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadWriteData(fun); // write to mark pipeline rows ABORTED + delete
    storageResources.dynamo.workflowExecutionConfigurationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionInputFilesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionInputMetadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionInputConfigurationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionOutputFilesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionOutputMetadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionOutputResultsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionLogsStorageTable.grantReadWriteData(fun);
    // Definition tables (V2) are read only to cross-fetch human-readable workflow/pipeline
    // names + descriptions for the detail view.
    storageResources.dynamo.workflowStorageTableV2.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetFileVersionHistoryStorageTable.grantReadData(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            // DescribeExecution/StopExecution + GetExecutionHistory (the whole-execution log view
            // renders the Step Functions execution history) act on executions; DescribeStateMachine
            // resolves a registered sub-SFN's CloudWatch log group from its state-machine ARN.
            actions: [
                "states:DescribeExecution",
                "states:StopExecution",
                "states:GetExecutionHistory",
                "states:DescribeStateMachine",
            ],
            resources: [
                IAMArn("*" + config.name + "*").statemachine,
                IAMArn("*" + config.name + "*").statemachineExecution,
                IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachine,
                IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachineExecution,
                // Registered pipeline sub-executions (CDK-generated names — see the constant above).
                IAMArn(PIPELINE_SUB_STATE_MACHINE_PATTERN).statemachine,
                IAMArn(PIPELINE_SUB_STATE_MACHINE_PATTERN).statemachineExecution,
            ],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            // Terminate a Batch job a pipeline registered as an abortable sub-process. Needed only
            // for jobs a pipeline submits ITSELF: a job submitted through the Step Functions `.sync`
            // Batch integration is stopped by StopExecution on its state machine (granted above).
            // AWS Batch generates job ids with no deployment-specific prefix to scope on, so the
            // resource is a wildcard; the abort path only ever passes an id read from a registration
            // row on the execution being aborted.
            actions: ["batch:TerminateJob"],
            resources: ["*"],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"],
            // Read scope covers the whole-execution and per-pipeline-step log sources the logs API
            // reads. IAM resource matching is CASE-SENSITIVE, so each real prefix is listed exactly:
            //   (1) the shared workflow SFN log group — granted by its actual ARN (name is
            //       '/aws/vendedlogs/vamsPipelineWorkflows<hash>', lowercase 'vams');
            //   (2) config-name-based groups (the audit/log groups that embed the config name);
            //   (3) pipeline state-machine groups — BOTH '/aws/vendedlogs/VAMSStateMachine-*' and
            //       '/aws/vendedlogs/VAMSstateMachine-*' are used across pipelines (case varies);
            //   (4) pipeline container groups '/aws/vendedlogs/Pipelines/*'.
            // Scoped to these prefixes (not the whole /aws/vendedlogs/* namespace) so it cannot read
            // unrelated apps' vended log groups. Each is suffixed with ':*' for stream-level reads.
            resources: [
                workflowsLogGroup.logGroupArn,
                workflowsLogGroup.logGroupArn + ":*",
                IAMArn("*" + config.name + "*").loggroup,
                IAMArn("*" + config.name + "*").loggroup + ":*",
                IAMArn("/aws/vendedlogs/VAMSStateMachine-*").loggroup,
                IAMArn("/aws/vendedlogs/VAMSStateMachine-*").loggroup + ":*",
                IAMArn("/aws/vendedlogs/VAMSstateMachine-*").loggroup,
                IAMArn("/aws/vendedlogs/VAMSstateMachine-*").loggroup + ":*",
                IAMArn("/aws/vendedlogs/Pipelines/*").loggroup,
                IAMArn("/aws/vendedlogs/Pipelines/*").loggroup + ":*",
            ],
        })
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    return fun;
}

/**
 * Asset-less multi-file execute handler. Reads the V2 workflow/pipeline/template/tag-schema tables +
 * the buckets table (default run bucket + per-input asset buckets), writes the V2 execution records,
 * and starts the workflow state machine. Run I/O lives in the default asset bucket; input files are
 * read from their own asset buckets and output write-back targets the output asset bucket, so it
 * needs read/write across all asset buckets.
 */
export function buildExecuteWorkflowV2Function(
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
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            METADATA_SERVICE_LAMBDA_FUNCTION_NAME: metadataServiceFunction.functionName,
            WORKFLOW_EXECUTION_LOG_GROUP_ARN: workflowsLogGroup.logGroupArn,
            ORCHESTRATION_BUS_ARN: storageResources.eventBridge.orchestrationBus.eventBusArn,
            ORCHESTRATION_EVENT_SOURCE_PREFIX: storageResources.eventBridge.eventSourcePrefix,
            // Block launching an execution whose referenced pipeline is DeadlineCloud when the type
            // is disabled (covers a pipeline created while enabled, then the deployment disabling it).
            DEADLINE_CLOUD_EXECUTION_TYPE_ENABLED: config.app.pipelines
                .deadlineCloudExecutionTypeEnabled
                ? "true"
                : "false",
        },
    });

    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.workflowStorageTableV2.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTableV2.grantReadData(fun);
    storageResources.dynamo.pipelineTemplatesStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineTemplateTagSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadWriteData(fun);
    storageResources.dynamo.pipelineExecutionsStorageTable.grantReadWriteData(fun);
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
            // StopExecution compensates a launch whose execution records could not be written: the
            // state machine is already running, so it is stopped rather than left with no VAMS
            // records (invisible to the executions list and unreachable by the abort API).
            actions: [
                "states:StartExecution",
                "states:StopExecution",
                "states:DescribeStateMachine",
                "states:DescribeExecution",
            ],
            resources: [
                IAMArn("*" + config.name + "*").statemachine,
                IAMArn("*" + config.name + "*").statemachineExecution,
                IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachine,
                IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachineExecution,
            ],
        })
    );
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
            // Reads the shared workflow SFN log group to capture a run's logs on completion. Granted
            // by that group's actual ARN (its name embeds lowercase 'vams'); the config-name pattern
            // is kept as a superset for any other VAMS-named group this handler may read.
            resources: [
                workflowsLogGroup.logGroupArn,
                workflowsLogGroup.logGroupArn + ":*",
                IAMArn("*" + config.name + "*").loggroup,
                IAMArn("*" + config.name + "*").loggroup + ":*",
            ],
        })
    );

    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);
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
            // values are set here: the orchestration bus ARN + event source prefix written into
            // each next pipeline's manifest.
            ORCHESTRATION_BUS_ARN: storageResources.eventBridge.orchestrationBus.eventBusArn,
            ORCHESTRATION_EVENT_SOURCE_PREFIX: storageResources.eventBridge.eventSourcePrefix,
        },
    });
    // The main execution row belongs to the launch, interim-status and end-state handlers; this
    // lambda advances only the per-pipeline rows, so the main table is read only.
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadData(fun);
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
    suppressCdkNagErrorsByGrantReadWrite(fun);
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
            // Pulls the failed run's logs from the shared workflow SFN log group; granted by that
            // group's actual ARN, plus the config-name pattern as a superset for other VAMS groups.
            resources: [
                workflowsLogGroup.logGroupArn,
                workflowsLogGroup.logGroupArn + ":*",
                IAMArn("*" + config.name + "*").loggroup,
                IAMArn("*" + config.name + "*").loggroup + ":*",
            ],
        })
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    // Scoped IAM5 suppression for this function's wildcard log-group read resource
    // (IAMArn("*"+config.name+"*").loggroup), so it does not depend on a stack-wide blanket.
    suppressCdkNagErrorsByGrantReadWrite(fun);
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

/**
 * File-upload workflow trigger dispatcher. An asset file upload publishes an `asset.file.uploaded`
 * event to the orchestration bus; a standing rule (deployment event-source prefix + that detail-type)
 * targets a durable SQS buffer this lambda consumes. Per uploaded file it enumerates the fileUpload
 * trigger rows (WorkflowTriggersTable TriggersByBaseTypeGSI), matches inputFileFilters + database
 * scope, and invokes executeWorkflowV2 (as SYSTEM_USER) per firing trigger. Its own SQS buffer + DLQ
 * isolate the fan-out from the invoking upload request.
 */
export function buildWorkflowTriggerDispatchFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    executeWorkflowV2Function: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const kmsEncryptionKey = storageResources.encryption.kmsKey;
    // Durable buffer: a single upload action can fan out to many files; SQS gives batching + retry.
    const dispatchDlq = new sqs.Queue(scope, "WorkflowTriggerDispatchDLQ", {
        encryption: kmsEncryptionKey ? sqs.QueueEncryption.KMS : sqs.QueueEncryption.SQS_MANAGED,
        encryptionMasterKey: kmsEncryptionKey,
        enforceSSL: true,
    });
    const dispatchQueue = new sqs.Queue(scope, "WorkflowTriggerDispatchQueue", {
        visibilityTimeout: Duration.seconds(960), // > the 900s function timeout
        encryption: kmsEncryptionKey ? sqs.QueueEncryption.KMS : sqs.QueueEncryption.SQS_MANAGED,
        encryptionMasterKey: kmsEncryptionKey,
        enforceSSL: true,
        deadLetterQueue: { queue: dispatchDlq, maxReceiveCount: 3 },
    });
    dispatchQueue.grantSendMessages(Service("EVENTS").Principal);

    const name = "workflowTriggerDispatch";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        // EventBridge/SQS-invoked execution handler under handlers/workflows/sfn/.
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
            EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME: executeWorkflowV2Function.functionName,
        },
    });
    storageResources.dynamo.workflowTriggersStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    // Read the candidate workflow's systemConfig.allowWorkflowTriggerChaining, which decides whether
    // a file written by ANOTHER workflow may fire this workflow's trigger. Read from the workflow
    // record rather than mirrored onto the trigger row, so it cannot go stale; only consulted for a
    // workflow-written file, so an ordinary upload performs no extra reads.
    storageResources.dynamo.workflowStorageTableV2.grantReadData(fun);
    executeWorkflowV2Function.grantInvoke(fun);
    // Reads uploaded-object metadata to resolve the asset (head_object across asset buckets).
    grantReadPermissionsToAllAssetBuckets(fun);

    // Standing rule: route this deployment's asset.file.uploaded events to the dispatch buffer.
    const uploadRule = new events.Rule(scope, "WorkflowFileUploadTriggerRule", {
        eventBus: storageResources.eventBridge.orchestrationBus,
        eventPattern: {
            source: events.Match.prefix(storageResources.eventBridge.eventSourcePrefix),
            detailType: ["asset.file.uploaded"],
        },
    });
    uploadRule.addTarget(new eventsTargets.SqsQueue(dispatchQueue));

    // Setup event source mapping for the dispatch buffer with GovCloud support
    dispatchQueue.grantConsumeMessages(fun);
    if (config.app.govCloud.enabled) {
        const esmDispatch = new lambda.EventSourceMapping(
            scope,
            "WorkflowTriggerDispatchSqsEventSource",
            {
                eventSourceArn: dispatchQueue.queueArn,
                target: fun,
                batchSize: 10,
                maxBatchingWindow: Duration.seconds(3),
            }
        );
        const cfnEsmDispatch = esmDispatch.node.defaultChild as lambda.CfnEventSourceMapping;
        cfnEsmDispatch.addPropertyDeletionOverride("Tags");
    } else {
        fun.addEventSource(
            new eventsources.SqsEventSource(dispatchQueue, {
                batchSize: 10,
                maxBatchingWindow: Duration.seconds(3),
            })
        );
    }

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

export function buildDeadlineCloudJobCallbackFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "deadlineCloudJobCallback";
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
        environment: {
            // Orchestration bus + event source prefix for registering the Deadline job as
            // the pipeline execution's sub-process (same registration contract pipelines use).
            ORCHESTRATION_BUS_ARN: storageResources.eventBridge.orchestrationBus.eventBusArn,
            ORCHESTRATION_EVENT_SOURCE_PREFIX: storageResources.eventBridge.eventSourcePrefix,
        },
    });

    // Deadline Cloud publishes job status events to the account DEFAULT bus only. Two rules
    // route job endings to the callback: terminal combined task-run statuses, and lifecycle
    // failure states (a job that fails at CREATE/UPLOAD never reaches a task-run status, so
    // its task token would otherwise only resolve by timing out). The lambda additionally
    // ignores jobs without the reserved VamsTaskToken job parameter.
    // The handler re-raises GetJob/SendTask* failures so EventBridge retries delivery. Without a
    // dead-letter queue a persistently failing terminal event is discarded after those retries and
    // the workflow's task token is left to time out with no operator-visible signal.
    const kmsEncryptionKey = storageResources.encryption.kmsKey;
    const deadlineCallbackDlq = new sqs.Queue(scope, "DeadlineCloudJobCallbackDLQ", {
        encryption: kmsEncryptionKey ? sqs.QueueEncryption.KMS : sqs.QueueEncryption.SQS_MANAGED,
        encryptionMasterKey: kmsEncryptionKey,
        enforceSSL: true,
    });
    NagSuppressions.addResourceSuppressions(deadlineCallbackDlq, [
        {
            id: "AwsSolutions-SQS3",
            reason:
                "This queue is itself the dead-letter target for the Deadline Cloud job-status EventBridge rules, " +
                "so it does not take a further dead-letter queue. Its messages are the undeliverable terminal " +
                "job events an operator redrives after fixing the underlying Deadline Cloud access failure.",
        },
    ]);

    const deadlineJobStatusRule = new events.Rule(scope, "DeadlineCloudJobStatusRule", {
        eventPattern: {
            source: ["aws.deadline"],
            detailType: ["Job Run Status Change"],
            detail: {
                taskRunStatus: ["SUCCEEDED", "FAILED", "CANCELED", "NOT_COMPATIBLE"],
            },
        },
    });
    deadlineJobStatusRule.addTarget(
        new eventsTargets.LambdaFunction(fun, {
            deadLetterQueue: deadlineCallbackDlq,
            retryAttempts: 3,
        })
    );

    const deadlineJobLifecycleRule = new events.Rule(scope, "DeadlineCloudJobLifecycleRule", {
        eventPattern: {
            source: ["aws.deadline"],
            detailType: ["Job Lifecycle Status Change"],
            detail: {
                lifecycleStatus: ["CREATE_FAILED", "UPLOAD_FAILED"],
            },
        },
    });
    deadlineJobLifecycleRule.addTarget(
        new eventsTargets.LambdaFunction(fun, {
            deadLetterQueue: deadlineCallbackDlq,
            retryAttempts: 3,
        })
    );

    const deadlineArnBase = `arn:${Partition()}:deadline:${config.env.region}:${
        config.env.account
    }`;
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["deadline:GetJob"],
            resources: [
                `${deadlineArnBase}:farm/*/queue/*`,
                `${deadlineArnBase}:farm/*/queue/*/job/*`,
            ],
        })
    );
    // Task tokens are opaque (not resource-scoped), so SendTask* uses the same
    // account/region-wide states resource the pipeline callback lambdas use.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${Partition()}:states:${config.env.region}:${config.env.account}:*`],
        })
    );
    storageResources.eventBridge.orchestrationBus.grantPutEventsTo(fun);

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Deadline Cloud job submission targets operator-owned farms/queues whose IDs are stored in " +
                    "pipeline records rather than known at deploy time, so deadline:GetJob is scoped to this " +
                    "account/region's farms, queues and jobs by wildcard. Step Functions task tokens are opaque " +
                    "values that carry no resource ARN, so states:SendTaskSuccess/SendTaskFailure cannot be " +
                    "scoped below the account/region — the same scope the pipeline callback lambdas use.",
                appliesTo: [
                    {
                        regex: "/^Resource::arn:.*:deadline:.*$/g",
                    },
                    {
                        regex: "/^Resource::arn:.*:states:.*$/g",
                    },
                ],
            },
        ],
        true
    );
    suppressCdkNagErrorsByGrantReadWrite(fun);
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
                resources: [
                    IAMArn("*" + config.name + "*").statemachine,
                    IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachine,
                ],
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
                resources: [
                    IAMArn("*" + config.name + "*").lambda,
                    IAMArn(BACKEND_GENERATED_NAME_PATTERN).lambda,
                ],
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

    // Deadline Cloud CreateJob permission for DeadlineCloud pipeline types. Job submission
    // targets operator-owned farms/queues whose IDs live in pipeline records, so the resource
    // covers any farm/queue (and job under it) in this account/region rather than named ARNs.
    if (config.app.pipelines.deadlineCloudExecutionTypeEnabled) {
        const deadlineArnBase = `arn:${Partition()}:deadline:${config.env.region}:${
            config.env.account
        }`;
        runWorkflowPolicy.addStatements(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["deadline:CreateJob"],
                resources: [
                    `${deadlineArnBase}:farm/*/queue/*`,
                    `${deadlineArnBase}:farm/*/queue/*/job/*`,
                ],
            })
        );
    }

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

/**
 * Global pipeline + workflow import custom-resource lambda. Registers a built-in (or externally
 * self-registered) pipeline + workflow into the pipeline/workflow tables from a vamsSchema bundle. It
 * upserts via SYSTEM_USER cross-calls to the four service functions, so it needs their names as env +
 * invoke permission on them, plus read on the artefacts bucket where the schema files are uploaded.
 * Also invocable directly (external self-registration) — no API route.
 */
export function buildImportGlobalPipelineWorkflowFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    pipelineServiceV2Function: lambda.Function,
    pipelineTemplateServiceFunction: lambda.Function,
    workflowServiceV2Function: lambda.Function,
    workflowTriggerServiceFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
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
            PIPELINE_SERVICE_V2_FUNCTION_NAME: pipelineServiceV2Function.functionName,
            PIPELINE_TEMPLATE_SERVICE_FUNCTION_NAME: pipelineTemplateServiceFunction.functionName,
            WORKFLOW_SERVICE_V2_FUNCTION_NAME: workflowServiceV2Function.functionName,
            WORKFLOW_TRIGGER_SERVICE_FUNCTION_NAME: workflowTriggerServiceFunction.functionName,
        },
    });

    pipelineServiceV2Function.grantInvoke(fun);
    pipelineTemplateServiceFunction.grantInvoke(fun);
    workflowServiceV2Function.grantInvoke(fun);
    workflowTriggerServiceFunction.grantInvoke(fun);
    storageResources.s3.artefactsBucket.grantRead(fun);

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    return fun;
}

/**
 * Workflow V2 service (CRUD + enable/disable/archive + save-validation). Reads/writes the V2
 * workflow + triggers tables (triggers listed on the details view) and reads the V2 pipeline table
 * (referenced-pipeline authorization + save-consistency checks + GLOBAL/scope string checks).
 *
 * Create/update generate the workflow ASL from the referenced pipelines and (re)deploy the Step
 * Functions state machine (common.workflows.workflowAsl.deploy_state_machine), so it builds a
 * dedicated SFN execution role (buildWorkflowRole) and receives the execution-overhaul lambda names
 * + log group + partition as env, plus states:Create/Describe/UpdateStateMachine + iam:PassRole.
 */
export function buildWorkflowServiceV2Function(
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
    // SFN execution role the deployed state machines assume (buildWorkflowRole).
    const role = buildWorkflowRole(
        scope,
        processWorkflowExecutionOutputFunction,
        config,
        storageResources.encryption.kmsKey
    );
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
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            // Execution-overhaul lambda names embedded in the generated ASL (interim states between
            // pipelines; error-handler catch state; end-state process-output), plus the SFN role +
            // shared workflow log group + partition the deploy uses. Read lazily by workflowAsl.
            PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME:
                processWorkflowExecutionOutputFunction.functionName,
            INTERIM_PIPELINE_TRACKING_LAMBDA_FUNCTION_NAME:
                interimPipelineTrackingFunction.functionName,
            HANDLE_EXECUTION_ERROR_LAMBDA_FUNCTION_NAME: handleExecutionErrorFunction.functionName,
            VAMS_STACK_NAME: stackName,
            LAMBDA_ROLE_ARN: role.roleArn,
            LOG_GROUP_ARN: workflowsLogGroup.logGroupArn,
            AWS_PARTITION: config.env.partition,
        },
    });
    storageResources.dynamo.workflowStorageTableV2.grantReadWriteData(fun);
    storageResources.dynamo.workflowTriggersStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTableV2.grantReadData(fun);
    // Read the workflow-executions table (+ its by-workflow GSI) to compute each workflow's
    // executionCount on the list response via a bounded COUNT query.
    storageResources.dynamo.workflowExecutionsStorageTableV2.grantReadData(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "states:CreateStateMachine",
                "states:DescribeStateMachine",
                "states:UpdateStateMachine",
            ],
            resources: [
                IAMArn("*" + config.name + "*").statemachine,
                IAMArn(BACKEND_GENERATED_NAME_PATTERN).statemachine,
            ],
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

/**
 * Workflow trigger service (fileUpload trigger CRUD). Reads the workflow table (parent-object
 * Casbin) and reads/writes the triggers table.
 */
export function buildWorkflowTriggerServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "workflowTriggerService";
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
        environment: {},
    });
    storageResources.dynamo.workflowStorageTableV2.grantReadData(fun);
    storageResources.dynamo.workflowTriggersStorageTable.grantReadWriteData(fun);
    // Setting a fileUpload trigger validates any default template it names: a headless (auto-)
    // triggered run cannot supply tag values, so a chosen default template must not have a required
    // tag without a default. That check reads the template's tag schema (TagSchemaByTemplateGSI),
    // and rehydrates an S3-offloaded schema from the default asset bucket.
    storageResources.dynamo.pipelineTemplateTagSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    // A large tag schema is offloaded to the default asset bucket; the headless-template check
    // rehydrates it, so grant read on the asset buckets (best-effort — skipped if unreadable).
    grantReadPermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}
