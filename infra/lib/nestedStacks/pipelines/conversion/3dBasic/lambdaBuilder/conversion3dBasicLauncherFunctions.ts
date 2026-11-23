/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as events from "aws-cdk-lib/aws-events";
import * as logs from "aws-cdk-lib/aws-logs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as Config from "../../../../../../config/config";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../../config/config";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    grantReadPermissionsToAllAssetBuckets,
    suppressCdkNagErrorsByGrantReadWrite,
    suppressCdkNagLambda,
} from "../../../../../helper/security";
import { vendedBatchJobLogGroupEnvironment } from "../../../../../helper/batchJobLogGroup";
import * as ServiceHelper from "../../../../../helper/service-helper";

/**
 * The launcher Lambdas of the 3D Basic Conversion Batch tier (backendPipelines/conversion/3dBasic/lambda):
 * the execute function the workflow calls, openPipeline (starts the state machine), selectJobSize and
 * constructPipeline (the tier selector and the job's request), and pipelineEnd (reports the run to the
 * workflow). They exist only in a deployment with app.pipelines.useConversion3dBasic.batchTier.enabled.
 * The converter Lambda itself is built by conversion3dBasicFunctions.ts and is unchanged.
 */

/** The extensions the pipeline's vamsSchema input filter allows; openPipeline checks the same list. */
export const CONVERSION_3D_BASIC_INPUT_FILE_EXTENSIONS =
    ".stl,.obj,.ply,.gltf,.glb,.xyz,.usd,.usda,.usdc,.usdz,.dae,.3mf,.off,.fbx,.step,.stp";

/** Lambda state names the launcher registers log sources under; the state machine uses these construct ids. */
export const CONVERSION_3D_BASIC_LAMBDA_STATE_ID = "ConversionLambdaAttempt";
export const CONVERSION_3D_BASIC_BATCH_STATE_ID = "ConversionBatchJob";

const LAUNCHER_CODE_DIR = "../../../../../../../backendPipelines/conversion/3dBasic/lambda";

interface LauncherNetwork {
    config: Config.Config;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    securityGroups: ec2.ISecurityGroup[];
}

/** The properties every launcher function shares; each builder adds its own `handler`. */
function launcherProps(
    layer: LayerVersion,
    network: LauncherNetwork,
    environment?: { [key: string]: string }
): Omit<lambda.FunctionProps, "handler"> {
    const useVpc =
        network.config.app.useGlobalVpc.enabled && network.config.app.useGlobalVpc.useForAllLambdas;
    return {
        code: lambda.Code.fromAsset(path.join(__dirname, LAUNCHER_CODE_DIR)),
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [layer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc: useVpc ? network.vpc : undefined,
        vpcSubnets: useVpc ? { subnets: network.subnets } : undefined,
        securityGroups: useVpc ? network.securityGroups : undefined,
        environment,
    };
}

export function buildVamsExecuteConversion3dBasicLauncherFunction(
    scope: Construct,
    layer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    openPipeline: lambda.IFunction,
    network: LauncherNetwork,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "vamsExecuteConversion3dBasicPipeline";
    const fun = new lambda.Function(scope, name, {
        ...launcherProps(layer, network, {
            OPEN_PIPELINE_FUNCTION_NAME: openPipeline.functionName,
            // The Regions the isolated-subnet Batch compute reaches Amazon S3 in through a
            // cross-Region interface endpoint; an input in any other Region is rejected at launch.
            CROSS_REGION_S3_ENDPOINT_REGIONS: Config.crossRegionS3EndpointRegions(
                network.config
            ).join(","),
        }),
        handler: `${name}.lambda_handler`,
    });
    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantRead(fun);
    openPipeline.grantInvoke(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, network.config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    // The workflow task waits on a callback token, so a failure here must reach Step Functions.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [
                `arn:${ServiceHelper.Partition()}:states:${network.config.env.region}:${
                    network.config.env.account
                }:*`,
            ],
        })
    );
    suppressCdkNagLambda(fun);
    return fun;
}

export interface OpenPipelineLogSources {
    /** The converter Lambda's log group (the Lambda attempt's source). */
    converterLogGroupName: string;
    converterLogGroupArn: string;
}

/** The Batch container's log source, which constructPipeline registers for the state the run takes. */
export interface BatchContainerLogSource {
    orchestrationBus: events.IEventBus;
    /** The Batch container's vended group and the job definition whose streams it holds. */
    batchLogGroup: logs.ILogGroup;
    batchJobDefinitionName: string;
}

export function buildOpenConversion3dBasicPipelineFunction(
    scope: Construct,
    layer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    pipelineStateMachine: sfn.StateMachine,
    network: LauncherNetwork,
    orchestrationBus: events.IEventBus,
    stateMachineLogGroup: logs.ILogGroup,
    logSources: OpenPipelineLogSources,
    maxMemoryGiB: number,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "openPipeline";
    const fun = new lambda.Function(scope, name, {
        ...launcherProps(layer, network, {
            STATE_MACHINE_ARN: pipelineStateMachine.stateMachineArn,
            ALLOWED_INPUT_FILEEXTENSIONS: CONVERSION_3D_BASIC_INPUT_FILE_EXTENSIONS,
            MAX_MEMORY_GIB: String(maxMemoryGiB),
            ORCHESTRATION_BUS_NAME: orchestrationBus.eventBusName,
            STATE_MACHINE_LOG_GROUP_NAME: stateMachineLogGroup.logGroupName,
            STATE_MACHINE_LOG_GROUP_ARN: stateMachineLogGroup.logGroupArn,
            CONVERTER_LOG_GROUP_NAME: logSources.converterLogGroupName,
            CONVERTER_LOG_GROUP_ARN: logSources.converterLogGroupArn,
        }),
        handler: `${name}.lambda_handler`,
    });
    // Read on the asset buckets covers the run's input configuration, which may force a tier.
    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantRead(fun);
    pipelineStateMachine.grantStartExecution(fun);
    orchestrationBus.grantPutEventsTo(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, network.config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [
                `arn:${ServiceHelper.Partition()}:states:${network.config.env.region}:${
                    network.config.env.account
                }:*`,
            ],
        })
    );
    suppressCdkNagLambda(fun);
    return fun;
}

/** The tier selector: picks the smallest permitted job size, or fails with the message that names the limit. */
export function buildSelectJobSizeFunction(
    scope: Construct,
    layer: LayerVersion,
    network: LauncherNetwork,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "selectJobSize";
    const fun = new lambda.Function(scope, name, {
        ...launcherProps(layer, network),
        handler: `${name}.lambda_handler`,
    });
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, network.config);
    suppressCdkNagLambda(fun);
    return fun;
}

/**
 * Builds the Batch task's request (VAMS_CONVERSION_EVENT) from the state machine's input, and registers
 * the Batch container's log source under the Batch state the run is routed to. The construct adds
 * BATCH_STATE_NAMES (memory to state name) once the states exist.
 */
export function buildConstructConversionPipelineFunction(
    scope: Construct,
    layer: LayerVersion,
    network: LauncherNetwork,
    logSource: BatchContainerLogSource,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "constructPipeline";
    const fun = new lambda.Function(scope, name, {
        ...launcherProps(layer, network, {
            ORCHESTRATION_BUS_NAME: logSource.orchestrationBus.eventBusName,
            // Streams are `<jobDefinitionName>/default/<task-id>`.
            ...vendedBatchJobLogGroupEnvironment(logSource.batchLogGroup),
            BATCH_JOB_DEFINITION_NAME: logSource.batchJobDefinitionName,
        }),
        handler: `${name}.lambda_handler`,
    });
    logSource.orchestrationBus.grantPutEventsTo(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, network.config);
    suppressCdkNagLambda(fun);
    return fun;
}

/** The last state: reports the run's success or failure to the workflow's waiting task. */
export function buildConversionPipelineEndFunction(
    scope: Construct,
    layer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    network: LauncherNetwork,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "pipelineEnd";
    const fun = new lambda.Function(scope, name, {
        ...launcherProps(layer, network, {}),
        handler: `${name}.lambda_handler`,
    });
    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantRead(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, network.config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [
                `arn:${ServiceHelper.Partition()}:states:${network.config.env.region}:${
                    network.config.env.account
                }:*`,
            ],
        })
    );
    suppressCdkNagLambda(fun);
    return fun;
}
