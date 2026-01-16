/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { suppressCdkNagErrorsByGrantReadWrite } from "../helper/security";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import { IAMArn, Service } from "../helper/service-helper";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import * as s3AssetBuckets from "../helper/s3AssetBuckets";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    kmsKeyPolicyStatementGenerator,
} from "../helper/security";
import { PropagatedTagSource } from "aws-cdk-lib/aws-ecs";

export function buildCreatePipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    enablePipelineFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "createPipeline";
    const newPipelineLambdaRole = createRoleToAttachToLambdaPipelines(
        scope,
        storageResources.encryption.kmsKey
    );
    const newPipelineSubnetIds = buildPipelineLambdaSubnetIds(scope, subnets, config);
    const newPipelineLambdaSecurityGroup = buildPipelineLambdaSecurityGroup(scope, vpc, config);
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.pipelines.${name}.lambda_handler`,
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
            PIPELINE_STORAGE_TABLE_NAME: storageResources.dynamo.pipelineStorageTable.tableName,
            WORKFLOW_STORAGE_TABLE_NAME: storageResources.dynamo.workflowStorageTable.tableName,
            ENABLE_PIPELINE_FUNCTION_NAME: enablePipelineFunction.functionName,
            ENABLE_PIPELINE_FUNCTION_ARN: enablePipelineFunction.functionArn,
            LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET: storageResources.s3.artefactsBucket.bucketName,
            LAMBDA_PIPELINE_SAMPLE_FUNCTION_KEY:
                "sample_lambda_pipeline/lambda_pipeline_deployment_package.zip",
            ROLE_TO_ATTACH_TO_LAMBDA_PIPELINE: newPipelineLambdaRole.roleArn,
            LAMBDA_PYTHON_VERSION: LAMBDA_PYTHON_RUNTIME.name,
            SUBNET_IDS: newPipelineSubnetIds, //Determines if we put the pipeline lambdas in a VPC or not
            SECURITYGROUP_IDS: newPipelineLambdaSecurityGroup
                ? newPipelineLambdaSecurityGroup.securityGroupId
                : "", //used if subnet IDs are passed in,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
        },
    });
    enablePipelineFunction.grantInvoke(fun);
    storageResources.s3.artefactsBucket.grantRead(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.workflowStorageTable.grantReadWriteData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["iam:PassRole"],
            resources: [newPipelineLambdaRole.roleArn],
        })
    );

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["lambda:CreateFunction", "lambda:UpdateFunctionConfiguration"],
            resources: [IAMArn("*" + config.name + "*").lambda],
        })
    );

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["ec2:DescribeSecurityGroups", "ec2:DescribeSubnets", "ec2:DescribeVpcs"],
            //Note: needs to be * resource as ec2:Describe* actions do not support resource-level permissions - https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/iam-policies-ec2-console.html
            resources: ["*"],
        })
    );

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

    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}

function createRoleToAttachToLambdaPipelines(scope: Construct, kmsKey?: kms.IKey) {
    const newPipelineLambdaRole = new iam.Role(scope, "lambdaPipelineRole", {
        assumedBy: Service("LAMBDA").Principal,
        inlinePolicies: {
            ReadWriteAssetBucketPolicy: new iam.PolicyDocument({
                statements: [
                    // Add permissions for all asset buckets from the global array
                    ...s3AssetBuckets.getS3AssetBucketRecords().map((record) => {
                        const prefix = record.prefix || "/";
                        // Ensure the prefix ends with a slash for proper path construction
                        const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";

                        return new iam.PolicyStatement({
                            effect: iam.Effect.ALLOW,
                            actions: [
                                "s3:PutObject",
                                "s3:GetObject",
                                "s3:ListBucket",
                                "s3:DeleteObject",
                                "s3:GetObjectVersion",
                            ],
                            resources: [
                                record.bucket.bucketArn,
                                `${record.bucket.bucketArn}${normalizedPrefix}*`,
                            ],
                        });
                    }),
                ],
            }),
        },
    });
    newPipelineLambdaRole.addManagedPolicy(
        iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSLambdaVPCAccessExecutionRole")
    );

    //Add KMS key use if provided
    if (kmsKey) {
        newPipelineLambdaRole.addToPolicy(kmsKeyPolicyStatementGenerator(kmsKey));
    }

    return newPipelineLambdaRole;
}

export function buildPipelineService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "pipelineService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.pipelines.${name}.lambda_handler`,
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
            PIPELINE_STORAGE_TABLE_NAME: storageResources.dynamo.pipelineStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
        },
    });
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTable.grantReadWriteData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);

    const deletePipelineResources = [IAMArn("*" + config.name + "*").lambda];

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["lambda:DeleteFunction"],
            resources: deletePipelineResources,
        })
    );
    return fun;
}

export function buildEnablePipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
) {
    const name = "enablePipeline";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.pipelines.${name}.lambda_handler`,
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
            PIPELINE_STORAGE_TABLE_NAME: storageResources.dynamo.pipelineStorageTable.tableName,
        },
    });
    storageResources.dynamo.pipelineStorageTable.grantReadWriteData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    return fun;
}

export function buildPipelineLambdaSecurityGroup(
    scope: Construct,
    vpc: ec2.IVpc,
    config: Config.Config
): ec2.ISecurityGroup | undefined {
    if (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas) {
        const pipelineLambdaSecurityGroup = new ec2.SecurityGroup(scope, "VPCeSecurityGroup", {
            vpc: vpc,
            allowAllOutbound: true,
            description: "VPC Endpoints Security Group",
        });

        return pipelineLambdaSecurityGroup;
    } else {
        return undefined;
    }
}

export function buildPipelineLambdaSubnetIds(
    scope: Construct,
    subnets: ec2.ISubnet[],
    config: Config.Config
): string {
    if (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas) {
        const subnetsArray: string[] = [];

        subnets.forEach((element) => {
            subnetsArray.push(element.subnetId);
        });
        return subnetsArray.join(",");
    } else {
        return "";
    }
}
