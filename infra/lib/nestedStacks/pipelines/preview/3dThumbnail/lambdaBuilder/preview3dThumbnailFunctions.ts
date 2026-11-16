/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as events from "aws-cdk-lib/aws-events";
import * as logs from "aws-cdk-lib/aws-logs";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../../config/config";
import * as Config from "../../../../../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../../../../../helper/security";
import { suppressCdkNagLambda } from "../../../../../helper/security";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import { NagSuppressions } from "cdk-nag";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { suppressCdkNagErrorsByGrantReadWrite } from "../../../../../helper/security";
import { vendedBatchJobLogGroupEnvironment } from "../../../../../helper/batchJobLogGroup";
import {
    grantReadWritePermissionsToAllAssetBuckets,
    grantReadPermissionsToAllAssetBuckets,
} from "../../../../../helper/security";

export function buildVamsExecutePreview3dThumbnailPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    openPipelineLambdaFunction: lambda.IFunction,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "vamsExecutePreview3dThumbnailPipeline";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(__dirname, `../../../../../../../backendPipelines/preview/3dThumbnail/lambda`)
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
            // The Regions the isolated-subnet Batch compute reaches Amazon S3 in through a
            // cross-Region interface endpoint; an input in any other Region is rejected at launch.
            CROSS_REGION_S3_ENDPOINT_REGIONS: Config.crossRegionS3EndpointRegions(config).join(","),
        },
    });

    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantRead(fun);
    openPipelineLambdaFunction.grantInvoke(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    // The workflow task waits on a callback token, so a failure in this lambda must be reported
    // back to Step Functions instead of leaving the task pending until its timeout.
    const stateTaskPolicy = new iam.PolicyStatement({
        actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
        resources: [
            `arn:${ServiceHelper.Partition()}:states:${config.env.region}:${config.env.account}:*`,
        ],
    });
    fun.addToRolePolicy(stateTaskPolicy);

    suppressCdkNagLambda(fun);
    return fun;
}

/**
 * The Batch job definition whose container log stream prefix openPipeline registers, and the
 * VAMS-owned group that job definition writes its container output to.
 */
export interface OpenPipelineBatchLogProps {
    jobDefinitionName: string;
    logGroup: logs.ILogGroup;
}

/**
 * The result document a run records when it writes no preview (the Lambda for an image the web shows as
 * its own preview, the container for the other no-preview outcomes). It must equal NO_PREVIEW_RESULT_FILENAME
 * in backendPipelines/preview/3dThumbnail/lambda/openPipeline.py and container/preview_pipeline/media_preview.py;
 * a drift test reads both.
 */
export const NO_PREVIEW_RESULT_FILENAME = "preview-not-generated.json";

/**
 * Media previews settings for openPipeline: the image extensions the web shows as their own preview,
 * the size limit under which they are (strictly), and whether a smaller JPEG is still written for them.
 * Always set: one deployment serves both the 3D and the media workflow.
 */
export interface OpenPipelineMediaProps {
    selfPreviewableFileExtensions: string;
    previewFileSizeLimitBytes: number;
    downscaleSelfPreviewableImages: boolean;
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
    orchestrationBus: events.IEventBus,
    stateMachineLogGroup: logs.ILogGroup,
    batchLogs: OpenPipelineBatchLogProps,
    largeTierThresholdBytes: number,
    kmsKey: kms.IKey | undefined,
    media: OpenPipelineMediaProps
): lambda.Function {
    const name = "openPipeline";

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(__dirname, `../../../../../../../backendPipelines/preview/3dThumbnail/lambda`)
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
            ORCHESTRATION_BUS_NAME: orchestrationBus.eventBusName,
            STATE_MACHINE_LOG_GROUP_NAME: stateMachineLogGroup.logGroupName,
            STATE_MACHINE_LOG_GROUP_ARN: stateMachineLogGroup.logGroupArn,
            // This pipeline's vended container log group + its job definition name, registered as
            // the Batch state's log source (streams are `<jobDefinitionName>/default/<task-id>`).
            ...vendedBatchJobLogGroupEnvironment(batchLogs.logGroup),
            BATCH_JOB_DEFINITION_NAME: batchLogs.jobDefinitionName,
            // Inputs at or above this size run on the large compute tier.
            LARGE_TIER_THRESHOLD_BYTES: String(largeTierThresholdBytes),
            // An image the web already shows as its own preview ends its run in this function.
            SELF_PREVIEWABLE_FILEEXTENSIONS: media.selfPreviewableFileExtensions,
            PREVIEW_FILE_SIZE_LIMIT_BYTES: String(media.previewFileSizeLimitBytes),
            DOWNSCALE_SELF_PREVIEWABLE_IMAGES: String(media.downscaleSelfPreviewableImages),
        },
    });

    // Read on the asset buckets also covers the HeadObject that sizes the input for its compute tier
    // and the run's input configuration, which may force a tier.
    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantRead(fun);
    // An image the web shows as its own preview ends its run here, and the Lambda records why as one
    // small result document in the execution's results folder. That folder is in the RUN bucket, which
    // is the default asset bucket (executeWorkflow.py resolves the manifest's outputs against it), at
    // <baseAssetsPrefix>pipelines/<pipeline>/<job>/output/<execution>/results/ -- not in the auxiliary
    // bucket. So the grant is s3:PutObject on exactly that document name, under that results path of
    // each asset bucket (the run bucket is one of them), and nothing else: no abort, legal hold,
    // retention, tagging or delete.
    const resultsDocumentKey = (prefix: string) =>
        `${prefix.replace(/^\/+/, "")}pipelines/*/output/*/results/${NO_PREVIEW_RESULT_FILENAME}`;
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["s3:PutObject"],
            resources: s3AssetBuckets.getS3AssetBucketRecords().map((record) => {
                const prefix = record.prefix || "/";
                const normalized = prefix.endsWith("/") ? prefix : prefix + "/";
                return `${record.bucket.bucketArn}/${resultsDocumentKey(normalized)}`;
            }),
        })
    );
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "s3:PutObject only, on one fixed document name (" +
                    NO_PREVIEW_RESULT_FILENAME +
                    ") in the results folder of an execution, " +
                    "<baseAssetsPrefix>pipelines/<pipeline>/<job>/output/<execution>/results/ of an asset bucket. " +
                    "The pipeline, job and execution segments are generated when a run starts, so they cannot be " +
                    "named at deploy time; the wildcards stand for those segments only. The Lambda cannot read, " +
                    "list, delete, tag or write any other object through this statement.",
                appliesTo: [
                    {
                        // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
                        regex: "/^Resource::.*\\/pipelines\\/\\*\\/output\\/\\*\\/results\\/preview-not-generated\\.json$/g",
                    },
                ],
            },
        ],
        true
    );
    pipelineStateMachine.grantStartExecution(fun);
    orchestrationBus.grantPutEventsTo(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    const stateTaskPolicy = new iam.PolicyStatement({
        actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
        resources: [
            `arn:${ServiceHelper.Partition()}:states:${config.env.region}:${config.env.account}:*`,
        ],
    });
    fun.addToRolePolicy(stateTaskPolicy);

    suppressCdkNagLambda(fun);
    return fun;
}

export function buildConstructPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "constructPipeline";

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(__dirname, `../../../../../../../backendPipelines/preview/3dThumbnail/lambda`)
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

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);

    suppressCdkNagLambda(fun);
    return fun;
}

export function buildPipelineEndFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "pipelineEnd";

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(__dirname, `../../../../../../../backendPipelines/preview/3dThumbnail/lambda`)
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

    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantRead(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    const stateTaskPolicy = new iam.PolicyStatement({
        actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
        resources: [
            `arn:${ServiceHelper.Partition()}:states:${config.env.region}:${config.env.account}:*`,
        ],
    });
    fun.addToRolePolicy(stateTaskPolicy);

    suppressCdkNagLambda(fun);
    return fun; //return
}
