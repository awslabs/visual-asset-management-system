/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as events from "aws-cdk-lib/aws-events";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
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
    kmsKeyLambdaPermissionAddToResourcePolicy,
    suppressCdkNagErrorsByGrantReadWrite,
    suppressCdkNagLambda,
} from "../../../../../helper/security";
import path = require("path");

const LAMBDA_PYTHON_RUNTIME = Config.LAMBDA_PYTHON_RUNTIME;

const LAMBDA_CODE_PATH = path.join(
    __dirname,
    "../../../../../../../backendPipelines/genAi/videoSopBom/lambda"
);

/**
 * The Bedrock model id the pipeline invokes, with the commercial default standing in when the config
 * value is blank. The restricted-partition templates ship the id empty with the pipeline disabled,
 * `getConfig()` backfills an absent id to the empty string and refuses to enable the pipeline with one,
 * so the fallback is only ever reached by a synth that bypasses `getConfig()` — it keeps the construct
 * synthesizing in every template.
 */
export function resolveVideoSopBomBedrockModelId(config: Config.Config): string {
    const configured = config.app.pipelines.useGenAiVideoSopBom?.bedrockModelId;
    if (configured) {
        return configured;
    }
    return "global.anthropic.claude-sonnet-5";
}

export function buildConstructPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;
    const name = "constructPipeline";
    const limits = config.app.pipelines.useGenAiVideoSopBom.limits;
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_CODE_PATH),
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
        // Every cap the definition document carries, as strings the handler parses with int(); the
        // handler has no defaults, so a missing value fails at import rather than at the first run.
        environment: {
            VIDEO_SOP_BOM_MAX_VIDEO_FILES: String(limits.maxVideoFiles),
            VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB: String(
                Config.VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB
            ),
            VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB: String(
                Config.VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB
            ),
            VIDEO_SOP_BOM_MAX_TOTAL_DURATION_MINUTES: String(limits.maxTotalDurationMinutes),
            VIDEO_SOP_BOM_MAX_KEY_FRAMES_CEILING: String(
                Config.VIDEO_SOP_BOM_MAX_KEY_FRAMES_CEILING
            ),
            BEDROCK_MODEL_ID: resolveVideoSopBomBedrockModelId(config),
            KMS_KEY_ARN: kmsKey ? kmsKey.keyArn : "",
        },
    });

    // Reads the manifest envelope and the shared metadata file from the asset bucket, and writes the
    // rendered definition document to the auxiliary bucket for the container to fetch.
    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantPut(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);

    // constructPipeline is the first sub-state-machine state: a rejected configuration is reported on the
    // external VAMS workflow token before any Batch job exists.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );

    return fun;
}

export function buildOpenPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    pipelineStateMachine: sfn.StateMachine,
    allowedPipelineInputExtensions: string,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    orchestrationBus: events.IEventBus,
    stateMachineLogGroup: logs.ILogGroup
): lambda.Function {
    const name = "openPipeline";
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_CODE_PATH),
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
            // Orchestration bus + sub-SFN log group for the sub-execution registration
            ORCHESTRATION_BUS_NAME: orchestrationBus.eventBusName,
            STATE_MACHINE_LOG_GROUP_NAME: stateMachineLogGroup.logGroupName,
            STATE_MACHINE_LOG_GROUP_ARN: stateMachineLogGroup.logGroupArn,
        },
    });

    // openPipeline receives S3 locations from vamsExecute and reads nothing itself: it starts the
    // sub-state-machine and registers the execution on the orchestration bus.
    pipelineStateMachine.grantStartExecution(fun);
    orchestrationBus.grantPutEventsTo(fun);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);

    // The legacy extension gate reports the external token for a refused file type.
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
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "pipelineEnd";
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_CODE_PATH),
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

    // pipelineEnd reads only the state machine's event; its one AWS call is the token release.
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);

    // pipelineEnd releases the external VAMS workflow token with the container's outcome.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );

    return fun;
}

export function buildVamsExecuteVideoSopBomFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    openPipelineLambdaFunction: lambda.Function,
    allowedPipelineInputExtensions: string,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "vamsExecuteVideoSopBomPipeline";
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;
    const limits = config.app.pipelines.useGenAiVideoSopBom.limits;

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_CODE_PATH),
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
        // The pre-invoke gates: count, extension and byte caps are checked here before openPipeline is
        // invoked, so a refused selection never starts a job.
        environment: {
            OPEN_PIPELINE_FUNCTION_NAME: openPipelineLambdaFunction.functionName,
            ALLOWED_INPUT_FILEEXTENSIONS: allowedPipelineInputExtensions,
            VIDEO_SOP_BOM_MAX_VIDEO_FILES: String(limits.maxVideoFiles),
            VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB: String(
                Config.VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB
            ),
            VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB: String(
                Config.VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB
            ),
        },
    });

    // Reads the manifest envelope and HeadObjects every input (with its version id) in any registered
    // asset bucket, including buckets under an external customer managed key.
    grantReadPermissionsToAllAssetBuckets(fun);
    openPipelineLambdaFunction.grantInvoke(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);

    // The workflow task waits on a callback token, so a rejection here is reported to Step Functions
    // instead of leaving the task pending until its timeout.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );

    return fun;
}
