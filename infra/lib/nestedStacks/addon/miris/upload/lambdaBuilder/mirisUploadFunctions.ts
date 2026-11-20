/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../../../config/config";
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import * as ServiceHelper from "../../../../../helper/service-helper";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import { vendedBatchJobLogGroupEnvironment } from "../../../../../helper/batchJobLogGroup";
import {
    globalLambdaEnvironmentsAndPermissions,
    grantExternalAssetBucketKmsKeys,
    grantReadPermissionsToAllAssetBuckets,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    suppressCdkNagErrorsByGrantReadWrite,
    suppressCdkNagLambda,
} from "../../../../../helper/security";
import path = require("path");

const LAMBDA_PYTHON_RUNTIME = Config.LAMBDA_PYTHON_RUNTIME;

const LAMBDA_SOURCE = path.join(
    __dirname,
    "../../../../../../../backendPipelines/addon/conversion/mirisUpload/lambda"
);

/**
 * Auxiliary-bucket prefix holding the dedup claim objects. Mirrors CLAIM_PREFIX in
 * backendPipelines/addon/conversion/mirisUpload/lambda/mirisClaim.py.
 */
export const MIRIS_CLAIM_PREFIX = "locks/miris-upload";

/**
 * Auxiliary-bucket prefix of the dependency manifests the container writes. Mirrors MANIFEST_PREFIX in
 * backendPipelines/addon/conversion/mirisUpload/container/dependency_manifest.py.
 */
export const MIRIS_DEPENDENCY_MANIFEST_PREFIX = "pipelines/miris-upload/manifests";

/** IAM5 `appliesTo` pattern for a grant on the manifest prefix of the auxiliary bucket, and nothing wider. */
export const MIRIS_DEPENDENCY_MANIFEST_PREFIX_OBJECT_REGEX =
    "/^Resource::<[A-Za-z0-9]*AssetAuxiliaryBucket[A-Za-z0-9]*\\.Arn>/pipelines/miris-upload/manifests/\\*$/g";

/** IAM5 `appliesTo` pattern for a grant on the claim prefix of the auxiliary bucket, and nothing wider. */
export const MIRIS_CLAIM_PREFIX_OBJECT_REGEX =
    "/^Resource::<[A-Za-z0-9]*AssetAuxiliaryBucket[A-Za-z0-9]*\\.Arn>/locks/miris-upload/\\*$/g";

/**
 * Object name of the container's ambiguous_root report under a run's results path. Mirrors
 * AMBIGUOUS_ROOT_REPORT_NAME in backendPipelines/addon/conversion/mirisUpload/lambda/openPipeline.py.
 */
export const MIRIS_AMBIGUOUS_ROOT_REPORT_NAME = "ambiguous_root.json";

/**
 * IAM5 `appliesTo` patterns for the end Lambda's read of that report: a bucket this deployment
 * creates (a GetAtt) or an imported external bucket (its literal ARN), under the `pipelines/`
 * run-output area of its VAMS prefix, and only objects of that name.
 */
export const MIRIS_AMBIGUOUS_ROOT_REPORT_REGEXES = [
    "/^Resource::<AssetBucket[0-9A-F]{8}\\.Arn>/pipelines/\\*/ambiguous_root\\.json$/g",
    "/^Resource::arn:(<AWS::Partition>|aws[a-z-]*):s3:::[a-z0-9][a-z0-9.-]*/([^*?/]+/)*pipelines/\\*/ambiguous_root\\.json$/g",
];

/** The Batch job definition name and the vended group its container writes to. */
export interface OpenPipelineBatchLogProps {
    jobDefinitionName: string;
    logGroup: logs.ILogGroup;
}

/** Suppresses the IAM5 finding for a function's grant on the claim prefix. */
function suppressCdkNagClaimPrefixWildcard(fun: lambda.Function) {
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Claim objects are keyed per asset and content fingerprint, known only at run time, under the " +
                    "fixed locks/miris-upload/ prefix of this deployment's auxiliary bucket.",
                appliesTo: [{ regex: MIRIS_CLAIM_PREFIX_OBJECT_REGEX }],
            },
        ],
        true
    );
}

/**
 * Lets a function release a claim: read its owner back, then delete it. Object actions on the claim
 * prefix only; the auxiliary bucket's KMS key is granted by kmsKeyLambdaPermissionAddToResourcePolicy.
 */
function grantClaimRelease(fun: lambda.Function, storageResources: storageResources) {
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["s3:GetObject", "s3:DeleteObject"],
            resources: [
                `${storageResources.s3.assetAuxiliaryBucket.bucketArn}/${MIRIS_CLAIM_PREFIX}/*`,
            ],
        })
    );
    suppressCdkNagClaimPrefixWildcard(fun);
}

function commonProps(
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    lambdaCommonBaseLayer: LayerVersion
) {
    return {
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
    };
}

export function buildMirisUploadGateFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "mirisUploadGate";
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_SOURCE),
        handler: `${name}.lambda_handler`,
        ...commonProps(config, vpc, subnets, lambdaCommonBaseLayer),
        environment: {
            ALLOWED_INPUT_FILEEXTENSIONS:
                config.app.addons.useMirisStreaming.upload.triggerExtensions,
        },
    });

    // The claim key fingerprints the keys and ETags of the asset's USD files, read from a listing.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["s3:ListBucket"],
            resources: s3AssetBuckets
                .getS3AssetBucketRecords()
                .map((record) => record.bucket.bucketArn),
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:SendTaskSuccess"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );
    // Writes the claim; the read checks the owner of an existing claim, telling a retry of its own
    // put from another execution's. Object actions on the claim prefix only; the auxiliary bucket's
    // KMS key is granted by kmsKeyLambdaPermissionAddToResourcePolicy.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["s3:PutObject", "s3:GetObject"],
            resources: [
                `${storageResources.s3.assetAuxiliaryBucket.bucketArn}/${MIRIS_CLAIM_PREFIX}/*`,
            ],
        })
    );
    suppressCdkNagClaimPrefixWildcard(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);

    return fun;
}

export function buildVamsExecuteMirisUploadFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    openPipelineFunction: lambda.Function,
    gateFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "vamsExecuteMirisUpload";
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_SOURCE),
        handler: `${name}.lambda_handler`,
        ...commonProps(config, vpc, subnets, lambdaCommonBaseLayer),
        environment: {
            OPEN_PIPELINE_FUNCTION_NAME: openPipelineFunction.functionName,
            MIRIS_UPLOAD_GATE_FUNCTION_NAME: gateFunction.functionName,
            // The asset name the Miris asset is created with, read from the asset record.
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
        },
    });

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["lambda:InvokeFunction"],
            resources: [openPipelineFunction.functionArn, gateFunction.functionArn],
        })
    );
    // Reads the per-pipeline manifest envelope and the input configuration from the asset bucket.
    grantReadPermissionsToAllAssetBuckets(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    // Releases the claim the gate wrote when the run cannot be handed off to the pipeline.
    grantClaimRelease(fun, storageResources);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);

    // The workflow task waits on a callback token, so a failure in this lambda must be reported
    // back to Step Functions instead of leaving the task pending until its timeout.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );
    return fun;
}

export function buildOpenPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    pipelineStateMachine: sfn.StateMachine,
    stateMachineLogGroup: logs.ILogGroup,
    batchLogs: OpenPipelineBatchLogProps,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "openPipeline";
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;
    const orchestrationBus = storageResources.eventBridge.orchestrationBus;

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_SOURCE),
        handler: `${name}.lambda_handler`,
        ...commonProps(config, vpc, subnets, lambdaCommonBaseLayer),
        environment: {
            STATE_MACHINE_ARN: pipelineStateMachine.stateMachineArn,
            ALLOWED_INPUT_FILEEXTENSIONS:
                config.app.addons.useMirisStreaming.upload.triggerExtensions,
            // Orchestration bus + sub-SFN log group for sub-process registration
            ORCHESTRATION_BUS_NAME: orchestrationBus.eventBusName,
            STATE_MACHINE_LOG_GROUP_NAME: stateMachineLogGroup.logGroupName,
            STATE_MACHINE_LOG_GROUP_ARN: stateMachineLogGroup.logGroupArn,
            // The container's vended log group and job definition name, registered as the Batch
            // state's log source (streams are `<jobDefinitionName>/default/<task-id>`).
            ...vendedBatchJobLogGroupEnvironment(batchLogs.logGroup),
            BATCH_JOB_DEFINITION_NAME: batchLogs.jobDefinitionName,
        },
    });

    pipelineStateMachine.grantStartExecution(fun);
    orchestrationBus.grantPutEventsTo(fun);
    // Releases the claim the gate wrote when the input is rejected or the state machine cannot start.
    grantClaimRelease(fun, storageResources);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );

    return fun;
}

export function buildConstructPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "constructPipeline";
    const upload = config.app.addons.useMirisStreaming.upload;
    const miris = config.app.addons.useMirisStreaming;

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_SOURCE),
        handler: `${name}.lambda_handler`,
        ...commonProps(config, vpc, subnets, lambdaCommonBaseLayer),
        environment: {
            MIRIS_API_BASE_URL: miris.mirisApiBaseUrl,
            MIRIS_API_KEY_SECRET_ARN: miris.apiKeySecretArn,
            MIRIS_UPLOAD_TASK_TIMEOUT_SECONDS: String(upload.taskTimeoutSeconds),
            MIRIS_UPLOAD_MAX_ASSET_SIZE_BYTES: String(upload.maxAssetSizeBytes),
            ALLOWED_INPUT_FILEEXTENSIONS: upload.triggerExtensions,
        },
    });

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);

    return fun;
}

export function buildPipelineEndFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "pipelineEnd";
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_SOURCE),
        handler: `${name}.lambda_handler`,
        ...commonProps(config, vpc, subnets, lambdaCommonBaseLayer),
        environment: {},
    });

    // A run that does not succeed releases its claim here, so the same content can be retried.
    grantClaimRelease(fun, storageResources);
    // A successful run lists the asset's claims to retire those on other content; the listing is
    // limited to the claim prefix.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["s3:ListBucket"],
            resources: [storageResources.s3.assetAuxiliaryBucket.bucketArn],
            conditions: { StringLike: { "s3:prefix": [`${MIRIS_CLAIM_PREFIX}/*`] } },
        })
    );
    // An ambiguous_root failure names the tied roots the container wrote to the run's results path.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["s3:GetObject"],
            resources: s3AssetBuckets.getS3AssetBucketRecords().map((record) => {
                const prefix = (record.prefix || "/").replace(/^\/+/, "");
                const objectPrefix = prefix === "" || prefix.endsWith("/") ? prefix : prefix + "/";
                return `${record.bucket.bucketArn}/${objectPrefix}pipelines/*/${MIRIS_AMBIGUOUS_ROOT_REPORT_NAME}`;
            }),
        })
    );
    grantExternalAssetBucketKmsKeys(fun);
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "The ambiguous_root report sits under a run's results folder, whose pipeline, job and " +
                    "execution segments are known only at run time; the read is limited to objects of that " +
                    "one name under each asset bucket's pipelines/ run-output area.",
                appliesTo: MIRIS_AMBIGUOUS_ROOT_REPORT_REGEXES.map((regex) => ({ regex })),
            },
        ],
        true
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );

    return fun;
}
