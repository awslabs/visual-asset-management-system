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
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    kmsKeyPolicyStatementGenerator,
    generateUniqueNameHash,
} from "../helper/security";
import * as logs from "aws-cdk-lib/aws-logs";
import * as cdk from "aws-cdk-lib";

export function buildWorkflowService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "workflowService";
    const workflowService = new lambda.Function(scope, name, {
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
            WORKFLOW_STORAGE_TABLE_NAME: storageResources.dynamo.workflowStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });
    storageResources.dynamo.databaseStorageTable.grantReadData(workflowService);
    storageResources.dynamo.workflowStorageTable.grantReadWriteData(workflowService);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(workflowService);
    storageResources.dynamo.userRolesStorageTable.grantReadData(workflowService);
    storageResources.dynamo.rolesStorageTable.grantReadData(workflowService);
    kmsKeyLambdaPermissionAddToResourcePolicy(workflowService, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(workflowService, config);
    workflowService.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "states:DeleteStateMachine",
                "states:DescribeStateMachine",
                "states:UpdateStateMachine",
            ],
            resources: [IAMArn("*vams*").statemachine],
        })
    );
    return workflowService;
}

export function buildListWorkflowExecutionsFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "listExecutions";
    const listAllWorkflowsFunction = new lambda.Function(scope, name, {
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
            WORKFLOW_EXECUTION_STORAGE_TABLE_NAME:
                storageResources.dynamo.workflowExecutionsStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });
    storageResources.dynamo.workflowExecutionsStorageTable.grantReadWriteData(
        listAllWorkflowsFunction
    ); //Needs write permission to update execution status after a SFN fetch
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(listAllWorkflowsFunction);
    storageResources.dynamo.userRolesStorageTable.grantReadData(listAllWorkflowsFunction);
    storageResources.dynamo.assetStorageTable.grantReadData(listAllWorkflowsFunction);
    storageResources.dynamo.rolesStorageTable.grantReadData(listAllWorkflowsFunction);
    listAllWorkflowsFunction.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:DescribeExecution"],
            resources: [IAMArn("*vams*").statemachine, IAMArn("*vams*").statemachineExecution],
        })
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(
        listAllWorkflowsFunction,
        storageResources.encryption.kmsKey
    );
    globalLambdaEnvironmentsAndPermissions(listAllWorkflowsFunction, config);

    return listAllWorkflowsFunction;
}

export function buildCreateWorkflowFunction(
    scope: Construct,
    lambdaCommonServiceSDKLayer: LayerVersion,
    storageResources: storageResources,
    processWorkflowExecutionOutputFunction: lambda.Function,
    stackName: string,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const logGroupWorkflows = new logs.LogGroup(scope, "vamsPipelineWorkflows", {
        logGroupName:
            "/aws/vendedlogs/vamsPipelineWorkflows" + //important to have 'vams' in the name as resource access looks for this
            generateUniqueNameHash(
                config.env.coreStackName,
                config.env.account,
                "vamsPipelineWorkflows",
                10
            ),
        retention: logs.RetentionDays.TEN_YEARS,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const role = buildWorkflowRole(
        scope,
        storageResources.s3.assetBucket,
        processWorkflowExecutionOutputFunction,
        storageResources.encryption.kmsKey
    );
    const name = "createWorkflow";
    const createWorkflowFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.workflows.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonServiceSDKLayer],
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
            WORKFLOW_STORAGE_TABLE_NAME: storageResources.dynamo.workflowStorageTable.tableName,
            PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME:
                processWorkflowExecutionOutputFunction.functionName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            VAMS_STACK_NAME: stackName,
            LAMBDA_ROLE_ARN: role.roleArn,
            LOG_GROUP_ARN: logGroupWorkflows.logGroupArn,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });
    storageResources.dynamo.workflowStorageTable.grantReadWriteData(createWorkflowFunction);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(createWorkflowFunction);
    storageResources.dynamo.userRolesStorageTable.grantReadData(createWorkflowFunction);
    storageResources.dynamo.rolesStorageTable.grantReadData(createWorkflowFunction);
    createWorkflowFunction.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "states:CreateStateMachine",
                "states:DescribeStateMachine",
                "states:UpdateStateMachine",
            ],
            resources: [IAMArn("*vams*").statemachine],
        })
    );
    createWorkflowFunction.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["iam:PassRole"],
            resources: [IAMArn("*vams*").role],
        })
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(
        createWorkflowFunction,
        storageResources.encryption.kmsKey
    );
    globalLambdaEnvironmentsAndPermissions(createWorkflowFunction, config);
    suppressCdkNagErrorsByGrantReadWrite(createWorkflowFunction);
    return createWorkflowFunction;
}

export function buildRunWorkflowFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    metadataReadFunction: lambda.IFunction,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "executeWorkflow";
    const runWorkflowFunction = new lambda.Function(scope, name, {
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
            WORKFLOW_STORAGE_TABLE_NAME: storageResources.dynamo.workflowStorageTable.tableName,
            PIPELINE_STORAGE_TABLE_NAME: storageResources.dynamo.pipelineStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            WORKFLOW_EXECUTION_STORAGE_TABLE_NAME:
                storageResources.dynamo.workflowExecutionsStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            S3_ASSET_STORAGE_BUCKET: storageResources.s3.assetBucket.bucketName,
            S3_ASSETAUXILIARY_STORAGE_BUCKET: storageResources.s3.assetAuxiliaryBucket.bucketName,
            METADATA_READ_LAMBDA_FUNCTION_NAME: metadataReadFunction.functionName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });
    storageResources.dynamo.workflowStorageTable.grantReadData(runWorkflowFunction);
    storageResources.dynamo.pipelineStorageTable.grantReadData(runWorkflowFunction);
    storageResources.dynamo.assetStorageTable.grantReadData(runWorkflowFunction);
    storageResources.dynamo.workflowExecutionsStorageTable.grantReadWriteData(runWorkflowFunction);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(runWorkflowFunction);
    storageResources.dynamo.userRolesStorageTable.grantReadData(runWorkflowFunction);
    storageResources.s3.assetBucket.grantReadWrite(runWorkflowFunction);
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(runWorkflowFunction);
    storageResources.dynamo.rolesStorageTable.grantReadData(runWorkflowFunction);
    metadataReadFunction.grantInvoke(runWorkflowFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(
        runWorkflowFunction,
        storageResources.encryption.kmsKey
    );
    globalLambdaEnvironmentsAndPermissions(runWorkflowFunction, config);
    suppressCdkNagErrorsByGrantReadWrite(runWorkflowFunction);

    runWorkflowFunction.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "states:StartExecution",
                "states:DescribeStateMachine",
                "states:DescribeExecution",
            ],
            resources: [IAMArn("*vams*").statemachine, IAMArn("*vams*").statemachineExecution],
        })
    );
    return runWorkflowFunction;
}

export function buildProcessWorkflowExecutionOutputFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    fileUploadLambdaFunction: lambda.Function,
    readMetadataLambdaFunction: lambda.Function,
    createMetadataLambdaFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "processWorkflowExecutionOutput";
    const processWorkflowExecutionOutputFunction = new lambda.Function(scope, name, {
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
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            WORKFLOW_EXECUTION_STORAGE_TABLE_NAME:
                storageResources.dynamo.workflowExecutionsStorageTable.tableName,
            FILE_UPLOAD_LAMBDA_FUNCTION_NAME: fileUploadLambdaFunction.functionName,
            READ_METADATA_LAMBDA_FUNCTION_NAME: readMetadataLambdaFunction.functionName,
            CREATE_METADATA_LAMBDA_FUNCTION_NAME: createMetadataLambdaFunction.functionName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            S3_ASSET_STORAGE_BUCKET: storageResources.s3.assetBucket.bucketName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });
    fileUploadLambdaFunction.grantInvoke(processWorkflowExecutionOutputFunction);
    readMetadataLambdaFunction.grantInvoke(processWorkflowExecutionOutputFunction);
    createMetadataLambdaFunction.grantInvoke(processWorkflowExecutionOutputFunction);
    storageResources.s3.assetBucket.grantReadWrite(processWorkflowExecutionOutputFunction);
    storageResources.dynamo.rolesStorageTable.grantReadData(processWorkflowExecutionOutputFunction);
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(
        processWorkflowExecutionOutputFunction
    );
    storageResources.dynamo.assetStorageTable.grantReadData(processWorkflowExecutionOutputFunction);
    storageResources.dynamo.workflowExecutionsStorageTable.grantReadWriteData(
        processWorkflowExecutionOutputFunction
    );
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(
        processWorkflowExecutionOutputFunction
    );
    storageResources.dynamo.userRolesStorageTable.grantReadData(
        processWorkflowExecutionOutputFunction
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(
        processWorkflowExecutionOutputFunction,
        storageResources.encryption.kmsKey
    );
    globalLambdaEnvironmentsAndPermissions(processWorkflowExecutionOutputFunction, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    return processWorkflowExecutionOutputFunction;
}

export function buildWorkflowRole(
    scope: Construct,
    assetStorageBucket: s3.Bucket,
    processWorkflowExecutionOutputFunction: lambda.Function,
    kmsKey?: kms.IKey
): iam.Role {
    const createWorkflowPolicy = new iam.PolicyDocument({
        statements: [
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["states:CreateStateMachine"],
                resources: [IAMArn("*vams*").statemachine],
            }),
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["events:PutTargets", "events:PutRule", "events:DescribeRule"],
                resources: [IAMArn("*vams*").stateMachineEvents],
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
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["s3:ListBucket", "s3:PutObject", "s3:GetObject"],
                resources: [assetStorageBucket.bucketArn, assetStorageBucket.bucketArn + "/*"],
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
                resources: [IAMArn("*vams*").lambda],
            }),
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["iam:PassRole"],
                resources: [IAMArn("*VAMS*").role],
            }),
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["iam:PassRole"],
                resources: [IAMArn("*vams*").role],
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

    return role;
}
