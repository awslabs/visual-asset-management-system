/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as batch from "aws-cdk-lib/aws-batch";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as kms from "aws-cdk-lib/aws-kms";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as cdk from "aws-cdk-lib";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import * as Config from "../../../../../../config/config";
import * as ServiceHelper from "../../../../../helper/service-helper";
import {
    globalLambdaEnvironmentsAndPermissions,
    grantReadPermissionsToAllAssetBuckets,
    grantReadWritePermissionsToAllAssetBuckets,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    suppressCdkNagErrorsByGrantReadWrite,
} from "../../../../../helper/security";
import path = require("path");

const LAMBDA_PYTHON_RUNTIME = Config.LAMBDA_PYTHON_RUNTIME;

export function buildConstructPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "constructPipeline";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(
                __dirname,
                "../../../../../../../backendPipelines/conversion/coordinateTransform/lambda"
            )
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
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

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildOpenPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    pipelineStateMachine: sfn.StateMachine,
    allowedPipelineInputExtensions: string,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "openPipeline";
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(
                __dirname,
                "../../../../../../../backendPipelines/conversion/coordinateTransform/lambda"
            )
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
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
            STATE_MACHINE_ARN: pipelineStateMachine.stateMachineArn,
            ALLOWED_INPUT_FILEEXTENSIONS: allowedPipelineInputExtensions,
        },
    });

    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantRead(fun);
    pipelineStateMachine.grantStartExecution(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );

    return fun;
}

export function buildPipelineEndFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "pipelineEnd";
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(
                __dirname,
                "../../../../../../../backendPipelines/conversion/coordinateTransform/lambda"
            )
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
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

    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantRead(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );

    return fun;
}

export function buildVamsExecuteCoordinateTransformFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    openPipelineLambdaFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "vamsExecuteCoordinateTransformPipeline";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(
                __dirname,
                "../../../../../../../backendPipelines/conversion/coordinateTransform/lambda"
            )
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
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
            OPEN_PIPELINE_FUNCTION_NAME: openPipelineLambdaFunction.functionName,
        },
    });

    openPipelineLambdaFunction.grantInvoke(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildExecuteBatchJobFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    batchJobQueue: batch.JobQueue,
    batchJobDefinition: batch.IJobDefinition,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;
    const name = "executeBatchJob";

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(
                __dirname,
                "../../../../../../../backendPipelines/conversion/coordinateTransform/lambda"
            )
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
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
            BATCH_JOB_QUEUE: batchJobQueue.jobQueueName,
            BATCH_JOB_DEFINITION: batchJobDefinition.jobDefinitionName,
        },
    });

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["batch:SubmitJob", "batch:DescribeJobs"],
            resources: [
                `arn:${ServiceHelper.Partition()}:batch:${region}:${account}:job-queue/${
                    batchJobQueue.jobQueueName
                }`,
                `arn:${ServiceHelper.Partition()}:batch:${region}:${account}:job-definition/${
                    batchJobDefinition.jobDefinitionName
                }*`,
            ],
        })
    );

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
