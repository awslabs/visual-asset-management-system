/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../../config/config";
import * as Config from "../../../../../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";
import { kmsKeyLambdaPermissionAddToResourcePolicy } from "../../../../../helper/security";
import * as ServiceHelper from "../../../../../helper/service-helper";

export function buildVamsExecuteRapidPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetBucket: s3.IBucket,
    assetAuxiliaryBucket: s3.IBucket,
    openPipelineLambdaFunction: lambda.IFunction,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "vamsExecuteRapidPipeline";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(__dirname, `../../../../../../../backendPipelines/multi/rapidPipeline/lambda`)
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
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
            OPEN_PIPELINE_FUNCTION_NAME: openPipelineLambdaFunction.functionName,
        },
    });

    assetBucket.grantRead(fun);
    assetAuxiliaryBucket.grantRead(fun);
    openPipelineLambdaFunction.grantInvoke(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);

    return fun;
}

export function buildOpenPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetBucket: s3.IBucket,
    assetAuxiliaryBucket: s3.IBucket,
    pipelineStateMachine: sfn.StateMachine,
    allowedPipelineInputExtensions: string,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "openPipeline";
    const vpcSubnets = vpc.selectSubnets({
        subnets: subnets,
    });

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(__dirname, `../../../../../../../backendPipelines/multi/rapidPipeline/lambda`)
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
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
            STATE_MACHINE_ARN: pipelineStateMachine.stateMachineArn,
            ALLOWED_INPUT_FILEEXTENSIONS: allowedPipelineInputExtensions,
        },
    });

    assetBucket.grantRead(fun);
    assetAuxiliaryBucket.grantRead(fun);
    pipelineStateMachine.grantStartExecution(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);

    return fun;
}

export function buildConstructPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    assetAuxiliaryBucket: s3.IBucket,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "constructPipeline";
    const vpcSubnets = vpc.selectSubnets({
        subnets: subnets,
    });

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(__dirname, `../../../../../../../backendPipelines/multi/rapidPipeline/lambda`)
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        securityGroups:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? pipelineSecurityGroups
                : undefined,
    });

    // Giving Lambda function permission to upload RapidPipeline config file to auxiliary bucket
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["s3:PutObject"],
            effect: iam.Effect.ALLOW,
            resources: [assetAuxiliaryBucket.bucketArn + "/*"],
        })
    );

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);

    return fun;
}

export function buildPipelineEndFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetBucket: s3.IBucket,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "pipelineEnd";
    const vpcSubnets = vpc.selectSubnets({
        subnets: subnets,
    });

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(__dirname, `../../../../../../../backendPipelines/multi/rapidPipeline/lambda`)
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        securityGroups:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? pipelineSecurityGroups
                : undefined,
        environment: {},
    });

    assetBucket.grantRead(fun);
    assetAuxiliaryBucket.grantRead(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);

    const stateTaskPolicy = new iam.PolicyStatement({
        actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
        resources: [
            `arn:${ServiceHelper.Partition()}:states:${config.env.region}:${config.env.account}:*`,
        ],
    });
    fun.addToRolePolicy(stateTaskPolicy);

    return fun;
}
