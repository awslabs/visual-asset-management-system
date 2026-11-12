/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as path from "path";
import * as sns from "aws-cdk-lib/aws-sns";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";
import { Duration, Names } from "aws-cdk-lib";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as events from "aws-cdk-lib/aws-events";
import * as logs from "aws-cdk-lib/aws-logs";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import { NagSuppressions } from "cdk-nag";
import {
    generateUniqueNameHash,
    suppressCdkNagErrorsByGrantReadWrite,
    grantReadWritePermissionsToAllAssetBuckets,
    grantReadPermissionsToAllAssetBuckets,
} from "../helper/security";
import * as s3AssetBuckets from "../helper/s3AssetBuckets";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import { IAMArn } from "../../lib/helper/service-helper";
import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import {
    LARGE_OBJECT_COPY_JOBS_EXECUTION_INDEX,
    PREPARED_DOWNLOADS_PREFIX,
    ZIP_DOWNLOAD_ASSET_INDEX,
    ZIP_DOWNLOAD_USER_ASSET_INDEX,
    storageResources,
} from "../nestedStacks/storage/storageBuilder-nestedStack";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    suppressCdkNagLambda,
    setupSecurityAndLoggingEnvironmentAndPermissions,
} from "../helper/security";

/**
 * Auxiliary-bucket prefix the asset export service stages an over-size export payload under
 * before handing back a presigned URL for it. Mirrors EXPORT_STAGING_PREFIX in
 * backend/backend/handlers/assets/assetExportService.py — the export function's S3 grant is
 * scoped to this prefix, so the two values move together.
 */
export const ASSET_EXPORT_STAGING_PREFIX = "assetExports/";

export function buildCreateAssetFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    // Per-asset subscription topics are named AssetTopic<assetId> and created at runtime, so the
    // exact ARN is not known at synthesis. The account and Region ARE known, and wildcarding them
    // made this a publish grant against any account's topics of that name.
    const assetTopicWildcardArn = cdk.Fn.sub(
        `arn:${Service.Partition()}:sns:${config.env.region}:${config.env.account}:AssetTopic*`
    );
    const name = "createAsset";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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

    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(fun); //update asset counts on a DB
    storageResources.dynamo.tagStorageTable.grantReadData(fun);
    storageResources.dynamo.tagTypeStorageTable.grantReadData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetHistoryStorageTable.grantReadWriteData(fun);

    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["sns:CreateTopic"],
            resources: [assetTopicWildcardArn],
        })
    );

    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildAssetService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    sendEmailFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const assetTopicWildcardArn = cdk.Fn.sub(
        `arn:${Service.Partition()}:sns:${config.env.region}:${config.env.account}:AssetTopic*`
    );
    const name = "assetService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            SEND_EMAIL_FUNCTION_NAME: sendEmailFunction.functionName,
        },
    });

    storageResources.dynamo.assetFileVersionHistoryStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetHistoryStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.tagStorageTable.grantReadData(fun);
    storageResources.dynamo.tagTypeStorageTable.grantReadData(fun);
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetUploadsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadWriteData(fun);
    storageResources.dynamo.assetLinksMetadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fileAttributeStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetFileVersionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetFileMetadataVersionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.commentStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.subscriptionsStorageTable.grantReadWriteData(fun);
    sendEmailFunction.grantInvoke(fun);

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["sns:CreateTopic", "sns:DeleteTopic"],
            resources: [assetTopicWildcardArn],
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

export function buildAssetFiles(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    sendEmailFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "assetFiles";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            SEND_EMAIL_FUNCTION_NAME: sendEmailFunction.functionName,
        },
    });

    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadWriteData(fun);
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);
    storageResources.dynamo.assetFileVersionsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetFileVersionHistoryStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fileAttributeStorageTable.grantReadWriteData(fun);
    sendEmailFunction.grantInvoke(fun);

    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildUploadFileFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    sendEmailFunction: lambda.Function,
    largeFileProcessingQueue: sqs.IQueue,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "uploadFile";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            SEND_EMAIL_FUNCTION_NAME: sendEmailFunction.functionName,
            PRESIGNED_URL_TIMEOUT_SECONDS:
                config.app.authProvider.presignedUrlTimeoutSeconds.toString(),
            LARGE_FILE_PROCESSING_QUEUE_URL: largeFileProcessingQueue.queueUrl,
        },
    });

    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);
    storageResources.dynamo.assetStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetUploadsStorageTable.grantReadWriteData(fun);
    sendEmailFunction.grantInvoke(fun);

    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

    suppressCdkNagErrorsByGrantReadWrite(scope);
    return fun;
}

export function buildStreamAuxiliaryPreviewAssetFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "streamAuxiliaryPreviewAsset";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            PRESIGNED_URL_TIMEOUT_SECONDS:
                config.app.authProvider.presignedUrlTimeoutSeconds.toString(),
        },
    });
    storageResources.s3.assetAuxiliaryBucket.grantRead(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    // The Region-routing S3 client resolves a bucket name's Region through the buckets table.
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

    suppressCdkNagErrorsByGrantReadWrite(scope);
    return fun;
}

export function buildDownloadAssetFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
) {
    const name = "downloadAsset";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            PRESIGNED_URL_TIMEOUT_SECONDS:
                config.app.authProvider.presignedUrlTimeoutSeconds.toString(),
        },
    });

    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileVersionsStorageTable.grantReadData(fun);

    grantReadPermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildAssetVersionsFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    sendEmailFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "assetVersions";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            SEND_EMAIL_FUNCTION_NAME: sendEmailFunction.functionName,
        },
    });

    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetFileVersionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetFileMetadataVersionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fileAttributeStorageTable.grantReadWriteData(fun);
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(fun);
    sendEmailFunction.grantInvoke(fun);

    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildStreamAssetFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "streamAsset";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            PRESIGNED_URL_TIMEOUT_SECONDS:
                config.app.authProvider.presignedUrlTimeoutSeconds.toString(),
        },
    });

    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileVersionsStorageTable.grantReadData(fun);

    grantReadPermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

    suppressCdkNagErrorsByGrantReadWrite(scope);
    return fun;
}

export function buildSqsUploadFileLargeFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    sendEmailFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "sqsUploadFileLarge";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            SEND_EMAIL_FUNCTION_NAME: sendEmailFunction.functionName,
        },
    });

    // Grant same permissions as uploadFile Lambda
    storageResources.dynamo.assetStorageTable.grantReadWriteData(fun);
    // The Region-routing S3 client resolves a bucket name's Region through the buckets table
    // when a queued message predates the bucketRegion field.
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    sendEmailFunction.grantInvoke(fun);

    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

    suppressCdkNagErrorsByGrantReadWrite(scope);
    return fun;
}

export function buildIngestAssetFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    uploadFileLambdaFunction: lambda.Function,
    createAssetLambdaFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "ingestAsset";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            CREATE_ASSET_LAMBDA_FUNCTION_NAME: createAssetLambdaFunction.functionName,
            FILE_UPLOAD_LAMBDA_FUNCTION_NAME: uploadFileLambdaFunction.functionName,
        },
    });

    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(fun);
    uploadFileLambdaFunction.grantInvoke(fun);
    createAssetLambdaFunction.grantInvoke(fun);

    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildAssetExportService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    assetLinksFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "assetExportService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            ASSET_LINKS_FUNCTION_NAME: assetLinksFunction.functionName,
            PRESIGNED_URL_TIMEOUT_SECONDS:
                config.app.authProvider.presignedUrlTimeoutSeconds.toString(),
        },
    });

    // Grant read permissions to all required tables
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileVersionsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.fileAttributeStorageTable.grantReadData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetLinksMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);

    // Grant invoke permission for asset links lambda
    assetLinksFunction.grantInvoke(fun);

    // Grant read permissions to all asset buckets for file listing and presigned URLs
    grantReadPermissionsToAllAssetBuckets(fun);

    // An export payload above the inline response size is written to the auxiliary bucket under
    // the staging prefix and returned as a presigned GET. A query-string presigned URL is
    // authorized as the signing role, so the same object needs s3:GetObject alongside
    // s3:PutObject. KMS Decrypt/GenerateDataKey for a CMK-encrypted bucket come from
    // kmsKeyLambdaPermissionAddToResourcePolicy below.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["s3:PutObject", "s3:GetObject"],
            resources: [
                `${storageResources.s3.assetAuxiliaryBucket.bucketArn}/${ASSET_EXPORT_STAGING_PREFIX}*`,
            ],
        })
    );

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildAssetHistoryFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "assetHistory";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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

    storageResources.dynamo.assetHistoryStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

// ============================================================================================== //
// Large object copy engine
// ============================================================================================== //

/**
 * The large object copy engine (this section): one Standard Step Functions state machine per deployment that copies
 * a large object into its asset as a multipart copy made of short Lambda tasks, and the grants of the
 * functions that start, wait on or stop its jobs.
 *
 *   Prepare -> Map(chunks, one at a time) { child execution: Map(batches) { CopyBatch } }
 *           -> Complete -> NotifySucceeded
 *   Catch (any state) -> Fail -> NotifyFailed -> Refused (succeeds) | CopyFailed (fails)
 *
 * The batches run in child executions of a second state machine (LargeObjectCopyParts-*) so that no
 * execution's history can reach the Step Functions quota (see "History budget" below). A copy
 * execution that ends outside its states is finalized by a status change rule (reconcile).
 *
 * Starters: fileIngestion (workflow outputs over 1 GiB) and the large-file queue consumer (API uploads
 * over 5 GiB, backend common/largeObjectCopy.py API_UPLOAD_ENGINE_THRESHOLD_BYTES). Waiters: the workflow output step (task
 * token). Stoppers: the abort route and the workflow error handler.
 *
 * The state machine has a fixed name so the starters in ApiBuilder can be given its ARN without a
 * cross-stack reference: it is built in ApiBuilder2 (next to the output step it reports to), and
 * ApiBuilder2 already depends on ApiBuilder. The name does not start with "vams-", the prefix of the
 * runtime-created workflow state machines, so the workflow status change rule does not match it.
 * Backend: backend/backend/common/largeObjectCopy.py, backend/backend/handlers/assets/largeObjectCopy.py.
 */

/** Backend-generated workflow state machine names (workflowAsl._generate_state_machine_name). */
const WORKFLOW_STATE_MACHINE_NAME_PATTERN = "vams-*";
/** The asset buckets table's index by bucket name (storageBuilder-nestedStack.ts). */
export const S3_ASSET_BUCKETS_NAME_INDEX = "bucketNameGSI";

/** Fixed name of the copy state machine (<= 80 characters). */
export function largeObjectCopyStateMachineName(config: Config.Config): string {
    return `LargeObjectCopy-${config.env.coreStackName}`.substring(0, 80);
}

/** ARN of the copy state machine, from its fixed name (no cross-stack reference). */
export function largeObjectCopyStateMachineArn(config: Config.Config): string {
    return IAMArn(largeObjectCopyStateMachineName(config)).statemachine;
}

/** ARN pattern of the copy state machine's executions. */
export function largeObjectCopyExecutionArnPattern(config: Config.Config): string {
    return IAMArn(largeObjectCopyStateMachineName(config) + ":*").statemachineExecution;
}

/**
 * Environment every function that starts, runs or waits on copy jobs reads: the state machine ARN
 * (a resource reference). The engine's sizes, batching and wait are fixed constants in backend
 * common/largeObjectCopy.py, not deployment configuration. The table name resolves from SSM.
 */
export function largeObjectCopyEnvironment(config: Config.Config): Record<string, string> {
    return {
        LARGE_OBJECT_COPY_STATE_MACHINE_ARN: largeObjectCopyStateMachineArn(config),
    };
}

function addEnvironment(fun: lambda.Function, environment: Record<string, string>) {
    for (const [key, value] of Object.entries(environment)) {
        fun.addEnvironment(key, value);
    }
}

function jobsTableStatement(storage: storageResources, actions: string[], withIndex: boolean) {
    const table = storage.dynamo.largeObjectCopyJobsStorageTable;
    const resources = [table.tableArn];
    if (withIndex) {
        resources.push(`${table.tableArn}/index/${LARGE_OBJECT_COPY_JOBS_EXECUTION_INDEX}`);
    }
    return new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: actions.map((a) => `dynamodb:${a}`),
        resources,
    });
}

/**
 * Add each statement inline to a function's role (its default policy, which the function already
 * waits for). VAMS uses no customer managed policies.
 */
function addRoleStatements(fun: lambda.Function, statements: iam.PolicyStatement[]) {
    for (const statement of statements) {
        fun.addToRolePolicy(statement);
    }
}

/**
 * Let `caller`'s role invoke the copy engine through a resource-based permission on the engine (not
 * an identity statement on the caller's role, so the caller's inline policy does not grow). Same
 * account, so the engine's resource policy alone authorizes the call.
 */
function permitEngineInvoke(engine: lambda.IFunction, caller: lambda.Function, id: string) {
    const role = caller.role;
    if (!role) {
        throw new Error(`${caller.node.id} needs an execution role to invoke the copy engine`);
    }
    engine.addPermission(id, {
        principal: new iam.ArnPrincipal(role.roleArn),
        action: "lambda:InvokeFunction",
    });
}

/**
 * Grants of a STARTER (fileIngestion, the large-file queue consumer): StartExecution on the copy
 * state machine, the job row writes start_copy_job makes (conditional PutItem, UpdateItem), and the
 * job row read of a redelivered message (GetItem).
 */
export function grantLargeObjectCopyStart(
    fun: lambda.Function,
    storage: storageResources,
    config: Config.Config
) {
    addEnvironment(fun, largeObjectCopyEnvironment(config));
    addRoleStatements(fun, [
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:StartExecution"],
            resources: [largeObjectCopyStateMachineArn(config)],
        }),
        // GetItem: a redelivered queue message finds the job it already handed off.
        jobsTableStatement(storage, ["PutItem", "UpdateItem", "GetItem"], false),
    ]);
}

/**
 * Grants of the workflow OUTPUT STEP (ingest / await / finalize modes) and of the STOPPERS (abort
 * route, error handler): the batch and job rows, the execution index, StopExecution on copy
 * executions (cancel), and, for the output step, SendTaskSuccess on the workflow state machines (it
 * answers its own task token when the copies finished before the wait registered).
 */
export function grantLargeObjectCopyCoordination(
    fun: lambda.Function,
    storage: storageResources,
    config: Config.Config,
    options: {
        reconcileWith?: lambda.IFunction;
        handsOffTo?: lambda.IFunction;
    }
) {
    addEnvironment(fun, largeObjectCopyEnvironment(config));
    const statements = [
        jobsTableStatement(storage, ["GetItem", "UpdateItem", "Query"], true),
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:StopExecution"],
            resources: [largeObjectCopyExecutionArnPattern(config)],
        }),
        // After its abort the stopper starts the job's delayed sweep (the copy state machine's sweep
        // branch, backend loc.schedule_delayed_sweep): an abort's immediate answer is not final
        // (#1123 D5). Only that one state machine.
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:StartExecution"],
            resources: [largeObjectCopyStateMachineArn(config)],
        }),
    ];
    // A stopped copy execution runs no Catch state, so the stopper aborts its multipart upload in
    // the asset bucket (cancel_execution_copy_jobs). Only that action: it reads and writes no object.
    const assetObjectArns = s3AssetBuckets
        .getS3AssetBucketRecords()
        .map((record) => record.bucket.arnForObjects("*"));
    if (assetObjectArns.length > 0) {
        statements.push(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["s3:AbortMultipartUpload"],
                resources: assetObjectArns,
            })
        );
    }
    // The abort is signed for the asset bucket's Region. A job row carries destRegion; a row written
    // without it resolves the Region through the asset buckets table's bucketNameGSI (backend
    // common/s3.py _lookup_bucket_region), so every stopper may query that index (#1123 D6).
    statements.push(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:Query"],
            resources: [
                `${storage.dynamo.s3AssetBucketsStorageTable.tableArn}/index/${S3_ASSET_BUCKETS_NAME_INDEX}`,
            ],
        })
    );
    if (options.reconcileWith) {
        // The execution details read hands the copy engine an unfinished job whose execution ended
        // outside its states (a missed status change event): it describes the job's execution
        // (backend executionService._reconcile_ended_copies).
        statements.push(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["states:DescribeExecution"],
                resources: [largeObjectCopyExecutionArnPattern(config)],
            })
        );
    }
    const engine = options.reconcileWith ?? options.handsOffTo;
    if (engine) {
        // The cancel path hands each copy it stopped and finalized as landed, or left to the engine
        // to decide, to the engine's reconcile with an asynchronous invoke (backend
        // largeObjectCopy.request_reconcile), next to the stop's status change event. The invoke is
        // a resource-based permission on the engine.
        fun.addEnvironment("LARGE_OBJECT_COPY_FUNCTION_NAME", engine.functionName);
        permitEngineInvoke(engine, fun, `LargeObjectCopyInvokeBy${fun.node.id}`);
    }
    addRoleStatements(fun, statements);
}

/** Fixed name of the SSM parameter holding the copy engine function name. */
export function largeObjectCopyEngineFunctionNameParameterName(config: Config.Config): string {
    return `${config.resourceNamesSSMParamPrefix}/${RESOURCE_PARAM_KEYS.lambdaFunctions.largeObjectCopyEngine}`;
}

/**
 * The workflow OUTPUT STEP's copy work goes through the copy engine: the output step holds no grant
 * on the jobs table, the copy state machine or its executions, and invokes the engine synchronously
 * (backend loc.call_engine, the engine's "outputStep" action), which records the batch, registers
 * the waiting state's task token, reads the outcomes and cancels with its own role.
 *
 * The engine already references the output step (it finalizes a run whose definition does not
 * wait), so the output step cannot take the engine's name as an environment variable: that would be
 * a CloudFormation dependency cycle. The name is published under the deployment's resource-name
 * prefix (lambdaFunctions/largeObjectCopyEngine), which every VAMS function may already read
 * (globalLambdaEnvironmentsAndPermissions), so the output step's role gains no statement; the
 * invoke is a resource-based permission on the engine naming the output step's role.
 */
export function grantLargeObjectCopyViaEngine(
    scope: Construct,
    fun: lambda.Function,
    engine: lambda.IFunction,
    config: Config.Config
) {
    const parameterName = largeObjectCopyEngineFunctionNameParameterName(config);
    new ssm.StringParameter(scope, "LargeObjectCopyEngineFunctionNameParameter", {
        parameterName,
        stringValue: engine.functionName,
        description:
            "Name of the VAMS large object copy engine function (read by the workflow output step).",
    });
    fun.addEnvironment("LARGE_OBJECT_COPY_FUNCTION_NAME_PARAMETER", parameterName);
    permitEngineInvoke(engine, fun, "LargeObjectCopyInvokeByOutputStep");
}

export interface LargeObjectCopyEngine {
    stateMachine: sfn.StateMachine;
    engineFunction: lambda.Function;
}

/** Build the copy-engine Lambda and the copy state machine (in ApiBuilder2). */
export function buildLargeObjectCopyEngine(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storage: storageResources,
    sendEmailFunction: lambda.IFunction,
    processWorkflowExecutionOutputFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): LargeObjectCopyEngine {
    const inVpc = config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas;
    // Reconcile requests that could not be processed: status change events the rule could not
    // deliver, and asynchronous reconcile invocations (the rule's, the details read's and the cancel
    // path's) that still failed after Lambda's retries.
    const reconcileDlq = new sqs.Queue(scope, LARGE_OBJECT_COPY_RECONCILE_DLQ_ID, {
        encryption: storage.encryption.kmsKey
            ? sqs.QueueEncryption.KMS
            : sqs.QueueEncryption.SQS_MANAGED,
        encryptionMasterKey: storage.encryption.kmsKey,
        enforceSSL: true,
        retentionPeriod: Duration.days(14),
    });
    NagSuppressions.addResourceSuppressions(reconcileDlq, [
        {
            id: "AwsSolutions-SQS3",
            reason:
                "This queue is itself the dead-letter target of the copy engine's reconcile requests, " +
                "so it takes no further dead-letter queue. A request left here names an ended copy " +
                "execution; an alarm on the queue signals it, and the operator re-invokes the engine's " +
                "reconcile with that execution ARN.",
        },
    ]);
    // A request in the reconcile DLQ is an ended copy whose job, or whose post-landing work, may be
    // unfinished: a cancelled stop, or an execution that ended outside its states. Both the status
    // change event and the cancel path's direct hand-over dead-letter here, and nothing retries a
    // request once it is here, so the alarm is the signal to act. The queue is a dead-letter target
    // with no source queue, so an SQS redrive cannot replay it; the remedy is to invoke the engine's
    // reconcile again (safe to repeat). No alarm action: VAMS has no notification topic, like the
    // other dead-letter queue alarms.
    new cloudwatch.Alarm(scope, LARGE_OBJECT_COPY_RECONCILE_DLQ_ALARM_ID, {
        alarmName: `${config.name}-${config.app.baseStackName}-largeObjectCopyReconcileDLQ`,
        alarmDescription:
            "Large object copy reconcile requests the copy engine could not process are in this " +
            "dead-letter queue. Each message names an ended copy execution (executionArn, or " +
            "detail.executionArn for a status change event). After fixing the cause, invoke the copy " +
            'engine function with {"action": "reconcile", "executionArn": "<that ARN>"} (safe to ' +
            "repeat), then delete the message.",
        metric: reconcileDlq.metricApproximateNumberOfMessagesVisible({
            period: Duration.minutes(5),
            statistic: cloudwatch.Stats.MAXIMUM,
        }),
        threshold: 0,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    // Checkov: like every VAMS function, the function is unreserved (CKV_AWS_115), the VPC (CKV_AWS_117) follows app.useGlobalVpc, and the environment holds no
    // secret, so it keeps Lambda's default encryption (CKV_AWS_173). CKV_AWS_116 is met (reconcileDlq).
    const fun = new lambda.Function(scope, "largeObjectCopy", {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.largeObjectCopy.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        // Each task is sized for about 5 minutes of copying; the 15-minute ceiling is headroom, and a
        // task that hits it is retried and resumes at the next missing part.
        timeout: Duration.minutes(15),
        // Server-side part copies hold no object data in memory. Only an in-VPC copy between
        // Regions streams parts through the function (two at a time, each up to a planned part).
        memorySize: inVpc ? Config.LAMBDA_MEMORY_SIZE : LARGE_OBJECT_COPY_SERVER_SIDE_MEMORY_MB,
        // Only asynchronous invocations (reconcile) use it; the state machines invoke synchronously.
        deadLetterQueue: reconcileDlq,
        vpc: inVpc ? vpc : undefined,
        vpcSubnets: inVpc ? { subnets: subnets } : undefined,
        environment: {
            SEND_EMAIL_FUNCTION_NAME: sendEmailFunction.functionName,
            PROCESS_WORKFLOW_OUTPUT_LAMBDA_FUNCTION_NAME:
                processWorkflowExecutionOutputFunction.functionName,
            ...largeObjectCopyEnvironment(config),
        },
    });

    // Job and batch rows (TransactWriteItems counts a job on its batch), the execution index
    // (finalize-side reads), the asset row (asset type after a file lands), the buckets table (the
    // Region-routing S3 client), the main execution row (display-only progress counter), the upload
    // record (an API upload's outcome).
    fun.addToRolePolicy(jobsTableStatement(storage, ["GetItem", "UpdateItem", "Query"], true));
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:GetItem", "dynamodb:UpdateItem"],
            resources: [storage.dynamo.assetStorageTable.tableArn],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:UpdateItem"],
            resources: [storage.dynamo.workflowExecutionsStorageTableV2.tableArn],
        })
    );
    // An API upload's copy records its file's outcome on the upload record.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:UpdateItem"],
            resources: [storage.dynamo.assetUploadsStorageTable.tableArn],
        })
    );
    storage.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    // Report a finished batch: answer the waiting workflow's task token, or finalize a run whose
    // definition does not wait.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:SendTaskSuccess"],
            resources: [IAMArn(WORKFLOW_STATE_MACHINE_NAME_PATTERN).statemachine],
        })
    );
    processWorkflowExecutionOutputFunction.grantInvoke(fun);
    sendEmailFunction.grantInvoke(fun);
    // Reconcile: read an ended copy execution's status and input (its job).
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:DescribeExecution"],
            resources: [largeObjectCopyExecutionArnPattern(config)],
        })
    );
    // The output step's cancel (a run that ended while its output step started copies) runs here:
    // stop the run's copy executions and hand each stop to this engine's own reconcile (an
    // asynchronous invoke, allowed by a resource-based permission on this function).
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:StopExecution"],
            resources: [largeObjectCopyExecutionArnPattern(config)],
        })
    );
    permitEngineInvoke(fun, fun, "LargeObjectCopyInvokeBySelf");
    // The failure cleanup and the reconcile of a cancelled stop start the job's delayed sweep, an
    // execution of the copy state machine's sweep branch (#1123 D5). The ARN is built from the fixed
    // name, so the engine does not reference the state machine that invokes it.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:StartExecution"],
            resources: [largeObjectCopyStateMachineArn(config)],
        })
    );

    // Read staged sources, write the destination multipart upload, delete the staged object.
    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storage.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storage);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    const stateMachine = buildCopyStateMachine(scope, fun, storage, config);
    buildCopyStatusChangeRule(scope, fun, config, reconcileDlq);
    return { stateMachine, engineFunction: fun };
}

/** The copy execution statuses that mean it may have ended outside its states. */
export const LARGE_OBJECT_COPY_RECONCILE_STATUSES = ["FAILED", "TIMED_OUT", "ABORTED"];

/**
 * Reconcile a copy execution that ended outside its states (H2): the history quota, a runtime
 * error, a timeout, or a stop in the console or the API runs no Catch, Fail or notify state. A rule
 * on the default event bus matches this deployment's copy state machine only (its exact ARN) and
 * invokes the engine, which finalizes the job once (backend largeObjectCopy.reconcile) and leaves a
 * job the cancel path stopped. The same pattern as the workflow status change rule
 * (buildWorkflowStatusChangeRule in workflowFunctions.ts), which matches only vams-* workflow state
 * machines. A missed event is
 * picked up by the execution details read, which checks a run's unfinished copies.
 *
 * As with the workflow status change rule (#1122 D2), the rule is created only after the engine's
 * resource policy and the dead-letter queue's policy, so it never goes live unable to deliver. Both
 * policies name the rule by an ARN built from its fixed name rather than by a reference to the rule
 * (a reference would make that dependency a cycle).
 */
function buildCopyStatusChangeRule(
    scope: Construct,
    fun: lambda.Function,
    config: Config.Config,
    dlq: sqs.Queue
): events.Rule {
    const rule = new events.Rule(scope, LARGE_OBJECT_COPY_STATUS_RULE_ID, {
        description: "Finalize VAMS large object copies whose execution ended outside its states",
        eventPattern: {
            source: ["aws.states"],
            detailType: ["Step Functions Execution Status Change"],
            detail: {
                status: LARGE_OBJECT_COPY_RECONCILE_STATUSES,
                stateMachineArn: [largeObjectCopyStateMachineArn(config)],
            },
        },
    });
    const cfnRule = rule.node.defaultChild as events.CfnRule;
    cfnRule.name = largeObjectCopyStatusRuleName(rule);
    const ruleArn = IAMArn(cfnRule.name).eventRule;

    // What the aws-events-targets LambdaFunction target adds (the engine's resource policy for this
    // rule, the queue policy statement, the target), with the rule named by its built ARN.
    const permission = new lambda.CfnPermission(scope, LARGE_OBJECT_COPY_STATUS_PERMISSION_ID, {
        action: "lambda:InvokeFunction",
        functionName: fun.functionArn,
        principal: Service.Service("EVENTS").PrincipalString,
        sourceArn: ruleArn,
    });
    dlq.addToResourcePolicy(
        new iam.PolicyStatement({
            sid: "AllowLargeObjectCopyStatusChangeRule",
            principals: [Service.Service("EVENTS").Principal],
            effect: iam.Effect.ALLOW,
            actions: ["sqs:SendMessage"],
            resources: [dlq.queueArn],
            conditions: { ArnEquals: { "aws:SourceArn": ruleArn } },
        })
    );
    // The queue's policy construct (created with the queue for enforceSSL, holding the statement above).
    const queuePolicy = dlq.node.findChild("Policy") as sqs.QueuePolicy;

    rule.addTarget({
        bind: (): events.RuleTargetConfig => ({
            arn: fun.functionArn,
            deadLetterConfig: { arn: dlq.queueArn },
            retryPolicy: {
                maximumRetryAttempts: 3,
                maximumEventAgeInSeconds: Duration.hours(2).toSeconds(),
            },
        }),
    });
    cfnRule.addDependency(permission);
    cfnRule.node.addDependency(queuePolicy);
    return rule;
}

/**
 * The copy status rule's name: unique per deployment (the top-level stack name and the construct
 * path, with a hash suffix) and within EventBridge's 64-character limit. Fixed rather than generated
 * by CloudFormation so its ARN is known without a reference to the rule.
 */
export function largeObjectCopyStatusRuleName(rule: Construct): string {
    return Names.uniqueResourceName(rule, {
        maxLength: 64,
        separator: "-",
        allowedSpecialCharacters: "-",
    });
}

/** Engine memory when every part is copied server side (UploadPartCopy holds no object data). */
export const LARGE_OBJECT_COPY_SERVER_SIDE_MEMORY_MB = 1024;

// Raised by the engine for a copy no retry can complete (backend common/largeObjectCopy.py).
const NON_RETRYABLE_ERRORS = ["CopyRefused", "SourceChanged"];

/**
 * Lambda throttling (the account's unreserved concurrency is exhausted) is retried
 * separately from faults, with long jittered backoff, so a throttled copy waits for capacity instead
 * of failing and discarding the parts it has copied. The delays double from 10 s to a 300 s cap; with
 * full jitter each delay is uniform between 0 and that value, so 99 attempts wait about 4 hours on
 * average and at most about 7.9 hours. Exported for the tests; the workflow definitions use the same
 * values (backend workflowAslBuilder THROTTLE_RETRIER).
 */
export const THROTTLE_RETRY = {
    errors: ["Lambda.TooManyRequestsException"],
    intervalSeconds: 10,
    backoffRate: 2,
    maxAttempts: 99,
    maxDelaySeconds: 300,
};

/** Fault retrier of an engine task (anything but a named refusal or throttling). */
export const FAULT_RETRY = {
    intervalSeconds: 5,
    backoffRate: 2,
    maxAttempts: 6,
    maxDelaySeconds: 120,
};

function engineTask(
    scope: Construct,
    id: string,
    fun: lambda.IFunction,
    payload: Record<string, unknown>,
    options: {
        resultSelector?: Record<string, unknown>;
        resultPath?: string;
        faultRetry?: typeof FAULT_RETRY;
    }
): tasks.LambdaInvoke {
    const task = new tasks.LambdaInvoke(scope, id, {
        lambdaFunction: fun,
        payload: sfn.TaskInput.fromObject(payload),
        resultSelector: options.resultSelector,
        resultPath: options.resultPath ?? sfn.JsonPath.DISCARD,
        retryOnServiceExceptions: false,
        taskTimeout: sfn.Timeout.duration(Duration.minutes(15)),
    });
    // A named refusal is final; throttling waits for capacity; anything else (a Lambda fault, a
    // task timeout, a failed S3 call) is retried, and a retried copy resumes where it stopped.
    // Step Functions uses the first retrier whose errors match, so States.ALL comes last.
    task.addRetry({ errors: NON_RETRYABLE_ERRORS, maxAttempts: 0 });
    task.addRetry({
        errors: THROTTLE_RETRY.errors,
        interval: Duration.seconds(THROTTLE_RETRY.intervalSeconds),
        backoffRate: THROTTLE_RETRY.backoffRate,
        maxAttempts: THROTTLE_RETRY.maxAttempts,
        maxDelay: Duration.seconds(THROTTLE_RETRY.maxDelaySeconds),
        jitterStrategy: sfn.JitterType.FULL,
    });
    const fault = options.faultRetry ?? FAULT_RETRY;
    task.addRetry({
        errors: [sfn.Errors.ALL],
        interval: Duration.seconds(fault.intervalSeconds),
        backoffRate: fault.backoffRate,
        maxAttempts: fault.maxAttempts,
        maxDelay: Duration.seconds(fault.maxDelaySeconds),
        jitterStrategy: sfn.JitterType.FULL,
    });
    return task;
}

/**
 * Fault retrier of the last notify attempt: a notify that failed its normal retries (DynamoDB or
 * Step Functions unavailable) tries again, about 1.4 hours on average and at most about 2.8 hours
 * (full jitter), so a landed file still finalizes its run long before the run's maximum wait.
 */
export const NOTIFY_LAST_ATTEMPT_RETRY = {
    intervalSeconds: 30,
    backoffRate: 2,
    maxAttempts: 20,
    maxDelaySeconds: 600,
};

/*
 * History budget (H2). A Standard execution fails, outside its own states, once its history reaches
 * HISTORY_EVENT_QUOTA events. Throttling retries land in the history of the execution that runs the
 * task, so the part batches run in child executions of at most BATCHES_PER_CHUNK batches each, and
 * no execution of the copy can approach the quota however long its batches are throttled:
 *
 * - a Task state adds at most 5 events (entered, scheduled, started, succeeded or failed, exited)
 *   plus 3 per retried attempt (scheduled, started, failed); a Map iteration adds 2 around it;
 * - a CopyBatch iteration in a child execution: 2 + 5 + 3 x (0 + 99 + 6) = 322 events at most
 *   (CopyRefused retries 0 times, throttling 99, faults 6);
 * - a child execution: 50 x 322 = 16,100, plus its own start, Map and end events: about 16,110, 64%
 *   of the quota;
 * - the job's own execution: at most MAX_BATCHES / BATCHES_PER_CHUNK = 20 chunk iterations of
 *   2 + 6 (startExecution.sync adds a submitted event) + 3 x 6 retries = 26 events (520), plus
 *   Prepare, Complete, Fail and the four notify tasks, each run once with every retrier exhausted
 *   (at most 5 + 3 x (99 + 20) = 362 each, about 2,550): about 3,100, 12% of the quota.
 *
 * infra/test/workflows/largeObjectCopyEngine.test.ts recomputes these bounds from the synthesized
 * definitions. A copy execution that still ends outside its states (stopped in the console, a
 * runtime error) is finalized by the status change rule below (backend largeObjectCopy.reconcile).
 */

/** Events a Standard execution's history may hold (Step Functions quota, not adjustable). */
export const HISTORY_EVENT_QUOTA = 25000;
/** Most batches a job is planned into (backend common/largeObjectCopy.py MAX_BATCHES). */
export const MAX_BATCHES = 1000;
/** Batches each child execution of a copy runs (see the history budget above). */
export const BATCHES_PER_CHUNK = 50;

/** Copy tasks one copy runs at once (the copy-parts Map MaxConcurrency). */
export const MAX_CONCURRENT_BATCHES = 10;
/** Upper bound of the events one Task state adds, from its retriers' MaxAttempts. */
export function worstCaseTaskEvents(retriers: { MaxAttempts?: number }[], submitted = false) {
    const attempts = retriers.reduce((sum, r) => sum + (r.MaxAttempts ?? 3), 0);
    return 5 + (submitted ? 1 : 0) + 3 * attempts;
}
/** Events a Map iteration adds around its item processor. */
export const MAP_ITERATION_EVENTS = 2;

/** Fixed name of the child state machine that copies one chunk of batches (<= 80 characters). */
export function largeObjectCopyPartsStateMachineName(config: Config.Config): string {
    return `LargeObjectCopyParts-${config.env.coreStackName}`.substring(0, 80);
}

/** Retrier of the chunk task: only a failed start; a failed child execution is final. */
export const CHUNK_START_RETRY = {
    errors: ["StepFunctions.ExecutionLimitExceeded", "StepFunctions.ThrottlingException"],
    intervalSeconds: 5,
    backoffRate: 2,
    maxAttempts: 6,
    maxDelaySeconds: 120,
};

/**
 * Waits of the delayed sweep branch (#1123 D5): the first pass about 5 minutes after the stop, the
 * last one 11 minutes later (about 16 minutes after the stop), a minute past the engine's 15-minute
 * task timeout. Same values as backend common/largeObjectCopy.py SWEEP_FIRST_WAIT_SECONDS /
 * SWEEP_SECOND_WAIT_SECONDS.
 */
export const LARGE_OBJECT_COPY_SWEEP_WAIT_SECONDS = { first: 300, second: 660 };

/** Construct ids, for the tests and the resource list in documentation. */
export const LARGE_OBJECT_COPY_STATUS_RULE_ID = "LargeObjectCopyStatusChangeRule";
export const LARGE_OBJECT_COPY_STATUS_PERMISSION_ID = "LargeObjectCopyStatusChangeInvokePermission";
export const LARGE_OBJECT_COPY_RECONCILE_DLQ_ID = "LargeObjectCopyReconcileDLQ";
export const LARGE_OBJECT_COPY_RECONCILE_DLQ_ALARM_ID = "LargeObjectCopyReconcileDLQAlarm";

function stateMachineLogGroup(
    scope: Construct,
    id: string,
    prefix: string,
    storage: storageResources,
    config: Config.Config
): logs.LogGroup {
    return new logs.LogGroup(scope, id, {
        logGroupName:
            prefix + generateUniqueNameHash(config.env.coreStackName, config.env.account, id, 10),
        // The deployment key when one is configured, as every VAMS log group (CKV_AWS_158 on a
        // deployment without a key matches the other log groups of the stack).
        encryptionKey: storage.encryption.kmsKey,
        retention: logs.RetentionDays.ONE_YEAR,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
}

/** The child state machine: copy the batches of one chunk, at most MAX_CONCURRENT_BATCHES at once. */
function buildCopyPartsStateMachine(
    scope: Construct,
    fun: lambda.IFunction,
    storage: storageResources,
    config: Config.Config
): sfn.StateMachine {
    const copyBatch = engineTask(
        scope,
        "LargeObjectCopyBatch",
        fun,
        {
            action: "copyBatch",
            job: sfn.JsonPath.objectAt("$.job"),
            batch: sfn.JsonPath.objectAt("$.batch"),
        },
        {}
    );
    const copyBatches = new sfn.Map(scope, "LargeObjectCopyBatches", {
        itemsPath: "$.batches",
        // Fixed; mirrors backend common/largeObjectCopy.py MAX_CONCURRENT_BATCHES (a test pins both).
        maxConcurrency: MAX_CONCURRENT_BATCHES,
        itemSelector: {
            job: sfn.JsonPath.objectAt("$.job"),
            batch: sfn.JsonPath.objectAt("$$.Map.Item.Value"),
        },
        resultPath: sfn.JsonPath.DISCARD,
    });
    copyBatches.itemProcessor(copyBatch);
    const machine = new sfn.StateMachine(scope, "LargeObjectCopyPartsStateMachine", {
        stateMachineName: largeObjectCopyPartsStateMachineName(config),
        stateMachineType: sfn.StateMachineType.STANDARD,
        definitionBody: sfn.DefinitionBody.fromChainable(copyBatches),
        logs: {
            destination: stateMachineLogGroup(
                scope,
                "LargeObjectCopyPartsStateMachineLogGroup",
                "/aws/vendedlogs/VAMSStateMachine-LargeObjectCopyParts",
                storage,
                config
            ),
            includeExecutionData: false,
            level: sfn.LogLevel.ALL,
        },
        tracingEnabled: true,
    });
    NagSuppressions.addResourceSuppressions(
        machine,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "Invokes the copy-engine function and its versions; writes its own log group.",
            },
        ],
        true
    );
    return machine;
}

function buildCopyStateMachine(
    scope: Construct,
    fun: lambda.IFunction,
    storage: storageResources,
    config: Config.Config
): sfn.StateMachine {
    const job = sfn.JsonPath.objectAt("$.job");
    const prepare = engineTask(
        scope,
        "LargeObjectCopyPrepare",
        fun,
        { action: "prepare", job },
        {
            resultSelector: {
                job: sfn.JsonPath.objectAt("$.Payload.job"),
                chunks: sfn.JsonPath.listAt("$.Payload.chunks"),
            },
            resultPath: "$",
        }
    );
    const partsMachine = buildCopyPartsStateMachine(scope, fun, storage, config);
    const copyChunk = new tasks.StepFunctionsStartExecution(scope, "LargeObjectCopyChunk", {
        stateMachine: partsMachine,
        integrationPattern: sfn.IntegrationPattern.RUN_JOB,
        input: sfn.TaskInput.fromObject({
            job: sfn.JsonPath.objectAt("$.job"),
            batches: sfn.JsonPath.listAt("$.batches"),
        }),
        associateWithParent: true,
        resultPath: sfn.JsonPath.DISCARD,
    });
    copyChunk.addRetry({
        errors: CHUNK_START_RETRY.errors,
        interval: Duration.seconds(CHUNK_START_RETRY.intervalSeconds),
        backoffRate: CHUNK_START_RETRY.backoffRate,
        maxAttempts: CHUNK_START_RETRY.maxAttempts,
        maxDelay: Duration.seconds(CHUNK_START_RETRY.maxDelaySeconds),
        jitterStrategy: sfn.JitterType.FULL,
    });
    // One chunk at a time: the child runs MAX_CONCURRENT_BATCHES batches at once, so the copy's
    // concurrency stays MAX_CONCURRENT_BATCHES.
    const copyChunks = new sfn.Map(scope, "LargeObjectCopyChunks", {
        itemsPath: "$.chunks",
        maxConcurrency: 1,
        itemSelector: {
            job: sfn.JsonPath.objectAt("$.job"),
            batches: sfn.JsonPath.listAt("$$.Map.Item.Value.batches"),
        },
        resultPath: sfn.JsonPath.DISCARD,
    });
    copyChunks.itemProcessor(copyChunk);
    const complete = engineTask(
        scope,
        "LargeObjectCopyComplete",
        fun,
        { action: "complete", job },
        { resultSelector: { job: sfn.JsonPath.objectAt("$.Payload.job") }, resultPath: "$" }
    );
    const notifySucceeded = engineTask(
        scope,
        "LargeObjectCopyNotifySucceeded",
        fun,
        { action: "notify", job, status: "SUCCEEDED" },
        {}
    );
    const fail = engineTask(
        scope,
        "LargeObjectCopyFail",
        fun,
        { action: "fail", job, error: sfn.JsonPath.objectAt("$.error") },
        {
            resultSelector: { refused: sfn.JsonPath.objectAt("$.Payload.refused") },
            resultPath: "$.outcome",
        }
    );
    const notifyFailed = engineTask(
        scope,
        "LargeObjectCopyNotifyFailed",
        fun,
        { action: "notify", job, status: "FAILED" },
        {}
    );
    const failed = new sfn.Fail(scope, "LargeObjectCopyFailed", {
        error: "LargeObjectCopyFailed",
        cause: "The copy did not complete; the job row records the reason.",
    });
    const notifyLost = new sfn.Fail(scope, "LargeObjectCopyNotifyLost", {
        error: "LargeObjectCopyNotifyFailed",
        cause: "The copy's outcome could not be reported to its workflow execution.",
    });
    // A notify that exhausted its retries gets a last, longer attempt (one per outcome, so each
    // keeps the status it reports), so a landed file still counts on its batch and finalizes the
    // run instead of the run waiting out its maximum wait. The notify action counts a job exactly
    // once, so a repeated attempt never double counts.
    const notifySucceededLastAttempt = engineTask(
        scope,
        "LargeObjectCopyNotifySucceededLastAttempt",
        fun,
        { action: "notify", job, status: "SUCCEEDED" },
        { faultRetry: NOTIFY_LAST_ATTEMPT_RETRY }
    );
    const notifyFailedLastAttempt = engineTask(
        scope,
        "LargeObjectCopyNotifyFailedLastAttempt",
        fun,
        { action: "notify", job, status: "FAILED" },
        { faultRetry: NOTIFY_LAST_ATTEMPT_RETRY }
    );
    notifySucceeded.addCatch(notifySucceededLastAttempt, {
        errors: [sfn.Errors.ALL],
        resultPath: "$.notifyError",
    });
    notifyFailed.addCatch(notifyFailedLastAttempt, {
        errors: [sfn.Errors.ALL],
        resultPath: "$.notifyError",
    });
    notifySucceededLastAttempt.addCatch(notifyLost, {
        errors: [sfn.Errors.ALL],
        resultPath: "$.notifyError",
    });
    notifyFailedLastAttempt.addCatch(notifyLost, {
        errors: [sfn.Errors.ALL],
        resultPath: "$.notifyError",
    });
    // A refused file (blocked type, over 5 TiB, source changed, cancelled) ends in its own state, so
    // the failed-copy alarm counts only copies that broke. The job row and the upload status or the
    // workflow execution still record the refusal as a failed file.
    const refused = new sfn.Succeed(scope, "LargeObjectCopyRefused", {
        comment: "The file was refused; the job row records the reason.",
    });
    const refusedOrFailed = new sfn.Choice(scope, "LargeObjectCopyRefusedOrFailed")
        .when(
            sfn.Condition.and(
                sfn.Condition.isPresent("$.outcome.refused"),
                sfn.Condition.booleanEquals("$.outcome.refused", true)
            ),
            refused
        )
        .otherwise(failed);
    notifyFailedLastAttempt.next(refusedOrFailed);
    // The failure path always reports, so a waiting workflow is told even when its cleanup fails.
    fail.addCatch(notifyFailed, { errors: [sfn.Errors.ALL], resultPath: "$.cleanupError" });
    const failurePath = fail.next(notifyFailed).next(refusedOrFailed);
    for (const state of [prepare, copyChunks, complete]) {
        state.addCatch(failurePath, { errors: [sfn.Errors.ALL], resultPath: "$.error" });
    }

    // The delayed sweep (#1123 D5): an execution whose input carries `sweep` (started by the cancel
    // path and the engine's failure cleanup, backend loc.schedule_delayed_sweep, once per job) waits,
    // sweeps the job's recorded upload, waits again until a minute past the longest a copy task alive
    // at the stop can run (the engine's 15-minute timeout), and sweeps a last time. An abort's immediate answer is not
    // final: a part copy accepted before the stop can land minutes later. A failed first pass still
    // runs the second. Bounded history: two waits and two engine tasks (see the history budget).
    const sweepFirstWait = new sfn.Wait(scope, "LargeObjectCopySweepFirstWait", {
        time: sfn.WaitTime.duration(Duration.seconds(LARGE_OBJECT_COPY_SWEEP_WAIT_SECONDS.first)),
    });
    const sweepFirst = engineTask(
        scope,
        "LargeObjectCopySweepFirst",
        fun,
        { action: "sweep", sweep: sfn.JsonPath.objectAt("$.sweep"), final: false },
        {}
    );
    const sweepSecondWait = new sfn.Wait(scope, "LargeObjectCopySweepSecondWait", {
        time: sfn.WaitTime.duration(Duration.seconds(LARGE_OBJECT_COPY_SWEEP_WAIT_SECONDS.second)),
    });
    const sweepFinal = engineTask(
        scope,
        "LargeObjectCopySweepFinal",
        fun,
        { action: "sweep", sweep: sfn.JsonPath.objectAt("$.sweep"), final: true },
        {}
    );
    const swept = new sfn.Succeed(scope, "LargeObjectCopySwept", {
        comment: "The delayed sweep of a stopped or failed copy ended.",
    });
    sweepFirst.addCatch(sweepSecondWait, { errors: [sfn.Errors.ALL], resultPath: "$.sweepError" });
    const sweepPath = sweepFirstWait
        .next(sweepFirst)
        .next(sweepSecondWait)
        .next(sweepFinal)
        .next(swept);
    const copyPath = prepare.next(copyChunks).next(complete).next(notifySucceeded);
    const start = new sfn.Choice(scope, "LargeObjectCopyCopyOrSweep")
        .when(sfn.Condition.isPresent("$.sweep"), sweepPath)
        .otherwise(copyPath);

    const logGroup = stateMachineLogGroup(
        scope,
        "LargeObjectCopyStateMachineLogGroup",
        "/aws/vendedlogs/VAMSStateMachine-LargeObjectCopy",
        storage,
        config
    );

    const stateMachine = new sfn.StateMachine(scope, "LargeObjectCopyStateMachine", {
        stateMachineName: largeObjectCopyStateMachineName(config),
        stateMachineType: sfn.StateMachineType.STANDARD,
        definitionBody: sfn.DefinitionBody.fromChainable(start),
        logs: {
            destination: logGroup,
            // Execution data carries bucket and key names only; level ALL satisfies AwsSolutions-SF1.
            includeExecutionData: false,
            level: sfn.LogLevel.ALL,
        },
        tracingEnabled: true,
    });
    NagSuppressions.addResourceSuppressions(
        stateMachine,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Invokes the copy-engine function and its versions; writes its own log group; " +
                    "describes and stops the executions of its child state machine; manages the " +
                    "Step Functions-owned rule startExecution.sync uses to wait on them.",
            },
        ],
        true
    );

    // A failed copy execution is a copy that broke (its job row and the upload or execution record
    // name the file), an outcome that could not be reported, or an execution that ended outside its
    // states. Refused files end in LargeObjectCopyRefused and do not count. No alarm action: VAMS has
    // no notification topic, so operators attach their own.
    new cloudwatch.Alarm(scope, "LargeObjectCopyExecutionsFailedAlarm", {
        alarmName: `${config.name}-${config.app.baseStackName}-largeObjectCopyExecutionsFailed`,
        alarmDescription:
            "A large object copy did not complete or could not report its outcome. The copy job " +
            "row, the upload status or the workflow execution names the file. A failed delayed " +
            "sweep execution (name starts with locsweep-) counts too: the partial upload of a " +
            "stopped copy may still be listed.",
        metric: stateMachine.metricFailed({
            period: Duration.minutes(5),
            statistic: cloudwatch.Stats.SUM,
        }),
        threshold: 0,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    return stateMachine;
}

// ---------------------------------------------------------------------------
// Download as Zip (app.zipDownloads)
// ---------------------------------------------------------------------------

/**
 * Download as Zip (app.zipDownloads): one function, the zip download service
 * (backend handlers/assets/zipDownloadService.py), serves the six `/download/zip` routes and the
 * passes of the delayed sweep. Its handler dispatches on the event envelope: an API Gateway event (it
 * always has `requestContext`) takes the API path with the authorizer and Casbin checks; a sweep pass
 * is a direct invoke no route can reach. No terminal callback reaches it: a job follows its
 * zipArchive run by the stored execution id, and the service's read-time reconcile finalizes it
 * from the run's execution row on the next read.
 *
 * The function deploys whatever `app.zipDownloads.enabled` says, so a deployment that turns the
 * feature off keeps its job table readable and its routes answering (404 "not enabled"). With the
 * feature on, `grantZipDownloadLaunch` adds the two cross-calls the service makes (launch and abort),
 * and `buildZipDownloadSweep` adds the sweep state machine that invokes it.
 */

/** The zipArchive system workflow every zip download job runs (database GLOBAL). */
export const ZIP_ARCHIVE_WORKFLOW_DATABASE_ID = "GLOBAL";
export const ZIP_ARCHIVE_WORKFLOW_ID = "utility-zip-archive";

/** Lifetime of the presigned URL the zip download service returns for a finished archive. */
export const ZIP_DOWNLOAD_PRESIGNED_URL_TIMEOUT_SECONDS = 900;

/**
 * The seven routes the zip download service serves (backend common/apiRoutes.py ZIP_DOWNLOAD_ROUTES;
 * test/api/apiRouteBackendCdkParity.test.ts holds the two lists equal).
 */
export const ZIP_DOWNLOAD_ROUTES: ReadonlyArray<{
    routePath: string;
    method: apigateway.HttpMethod;
}> = [
    {
        routePath: "/database/{databaseId}/assets/{assetId}/download/zip",
        method: apigateway.HttpMethod.POST,
    },
    {
        routePath: "/database/{databaseId}/assets/{assetId}/download/zip",
        method: apigateway.HttpMethod.GET,
    },
    { routePath: "/download/zip", method: apigateway.HttpMethod.GET },
    {
        routePath: "/database/{databaseId}/assets/{assetId}/download/zip-all-users",
        method: apigateway.HttpMethod.GET,
    },
    {
        routePath: "/database/{databaseId}/assets/{assetId}/download/zip/{requestId}",
        method: apigateway.HttpMethod.GET,
    },
    {
        routePath: "/database/{databaseId}/assets/{assetId}/download/zip/{requestId}",
        method: apigateway.HttpMethod.DELETE,
    },
    {
        routePath: "/database/{databaseId}/assets/{assetId}/download/zip/{requestId}/url",
        method: apigateway.HttpMethod.POST,
    },
];

/** The zip download service's environment from app.zipDownloads (names read by zipDownloadService.py). */
export function zipDownloadServiceEnvironment(config: Config.Config): Record<string, string> {
    const zip = config.app.zipDownloads;
    return {
        ZIPDOWNLOADS_ENABLED: zip.enabled ? "true" : "false",
        ZIP_DOWNLOAD_MAX_FILES_PER_REQUEST: zip.maxFilesPerRequest.toString(),
        ZIP_DOWNLOAD_MAX_TOTAL_BYTES_PER_REQUEST: zip.maxTotalBytesPerRequest.toString(),
        ZIP_DOWNLOAD_MAX_ACTIVE_PER_USER: zip.maxActiveRequestsPerUser.toString(),
        ZIP_ARCHIVE_WORKFLOW_DATABASE_ID: ZIP_ARCHIVE_WORKFLOW_DATABASE_ID,
        ZIP_ARCHIVE_WORKFLOW_ID: ZIP_ARCHIVE_WORKFLOW_ID,
        // 15 minutes, or the deployment's presigned URL lifetime when that is shorter.
        PRESIGNED_URL_TIMEOUT_SECONDS: Math.min(
            ZIP_DOWNLOAD_PRESIGNED_URL_TIMEOUT_SECONDS,
            config.app.authProvider.presignedUrlTimeoutSeconds
        ).toString(),
    };
}

/** `<auxiliary bucket ARN>/vams.preparedDownloads/*`: every object a zip download job owns. */
function preparedDownloadsObjectArn(storageResources: storageResources): string {
    return `${storageResources.s3.assetAuxiliaryBucket.bucketArn}/${PREPARED_DOWNLOADS_PREFIX}*`;
}

function inVpc(config: Config.Config): boolean {
    return config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas;
}

/**
 * The zip download service: create, list (asset and global), get, cancel/delete and presigned URL,
 * one function for all seven routes, plus the delayed sweep's passes (abort a stopped job's recorded
 * uploads). Its own job table and its auxiliary-bucket prefix are granted as exact statements (the
 * actions the handler calls, the read-time reconcile's and the sweep's included), the asset data read the way downloadAsset
 * reads it, and the execution row read by GetItem only (read-time reconcile).
 */
export function buildZipDownloadServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "zipDownloadService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc: inVpc(config) ? vpc : undefined,
        vpcSubnets: inVpc(config) ? { subnets: subnets } : undefined,
        environment: zipDownloadServiceEnvironment(config),
    });

    const zipTable = storageResources.dynamo.zipDownloadRequestsStorageTable;
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:DeleteItem",
                "dynamodb:Query",
            ],
            resources: [
                zipTable.tableArn,
                `${zipTable.tableArn}/index/${ZIP_DOWNLOAD_USER_ASSET_INDEX}`,
                `${zipTable.tableArn}/index/${ZIP_DOWNLOAD_ASSET_INDEX}`,
            ],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:GetItem"],
            resources: [storageResources.dynamo.workflowExecutionsStorageTableV2.tableArn],
        })
    );
    // The asset, its bucket record, and (for an asset-version pin) its version snapshot, as the
    // download function reads them.
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileVersionsStorageTable.grantReadData(fun);

    // The job's own objects: manifest write, archive head and presign, cancel/delete cleanup, and the
    // read-time reconcile's working-object cleanup (common/zipDownloads.delete_working_objects,
    // confined by the handler to one job's keys).
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "s3:PutObject",
                "s3:GetObject",
                "s3:DeleteObject",
                "s3:DeleteObjectVersion",
                "s3:AbortMultipartUpload",
                // ListParts confirms an aborted upload is gone (#1125 D16, abort_until_gone).
                "s3:ListMultipartUploadParts",
            ],
            resources: [preparedDownloadsObjectArn(storageResources)],
        })
    );
    // s3:ListBucket also makes S3 answer a HeadObject on a missing archive key with 404, not 403: the
    // read-time reconcile and the delayed sweep decide by the landed archive (#1125 D21).
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["s3:ListBucket", "s3:ListBucketVersions"],
            resources: [storageResources.s3.assetAuxiliaryBucket.bucketArn],
            conditions: { StringLike: { "s3:prefix": [`${PREPARED_DOWNLOADS_PREFIX}*`] } },
        })
    );
    // ListMultipartUploads takes no s3:prefix condition key; the handler confines every listing to
    // one job's prefix.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["s3:ListBucketMultipartUploads"],
            resources: [storageResources.s3.assetAuxiliaryBucket.bucketArn],
        })
    );
    // Selection checks (HeadObject, ListObjectVersions) on the asset buckets.
    grantReadPermissionsToAllAssetBuckets(fun);

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    // Scoped to this function (not the stack): applied to the stack, it would restamp the suppression
    // metadata on every construct the stack already holds, changing their templates.
    suppressCdkNagErrorsByGrantReadWrite(fun);

    return fun;
}

/**
 * With app.zipDownloads on: the service launches the zipArchive workflow through the execute function
 * and aborts a cancelled job's run through the execution service, both as SYSTEM_USER cross-calls.
 * The same execution service grant serves the run check of an idle job (a details read that records
 * a run ended outside VAMS whose status change event was lost; zipDownloadService._check_run_ended).
 * Each grant names the exact unqualified function ARN (no `:*`), like the re-run grant on the
 * execution service. Not called with the feature off, so neither name nor grant is deployed then.
 */
export function grantZipDownloadLaunch(
    zipDownloadService: lambda.Function,
    executeWorkflowV2: lambda.IFunction,
    executionService: lambda.IFunction
): void {
    zipDownloadService.addEnvironment(
        "EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME",
        executeWorkflowV2.functionName
    );
    zipDownloadService.addEnvironment(
        "EXECUTION_SERVICE_FUNCTION_NAME",
        executionService.functionName
    );
    zipDownloadService.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["lambda:InvokeFunction"],
            resources: [executeWorkflowV2.functionArn, executionService.functionArn],
        })
    );
}

/**
 * Waits of the zip download delayed sweep (#1125 D16, the #1123 D5 rule): the first pass about 5
 * minutes after the stop, the last one 10 minutes later (15 minutes after the stop). Unlike the copy
 * engine's sweep, no task timeout sets this bound: the container is the only writer of parts, uploads
 * no part after it sees the stop (stopRequestedAt, or AWS Batch's SIGTERM, with SIGKILL 30 s later)
 * and aborts again after its last part. The passes cover what S3 lists again after those aborts (2 to
 * 8 minutes later on demo21). The service's sweep branch only aborts, so its 15-minute function
 * timeout does not bound it either. Same values as backend common/zipDownloads.py
 * SWEEP_FIRST_WAIT_SECONDS / SWEEP_SECOND_WAIT_SECONDS.
 */
export const ZIP_DOWNLOAD_SWEEP_WAIT_SECONDS = { first: 300, second: 600 };

/** Fixed name of the zip download sweep state machine (<= 80 characters). */
export function zipDownloadSweepStateMachineName(config: Config.Config): string {
    return `ZipDownloadSweep-${config.env.coreStackName}`.substring(0, 80);
}

/** Its ARN, built from the fixed name (the functions it invokes cannot reference it: a cycle). */
export function zipDownloadSweepStateMachineArn(config: Config.Config): string {
    return Service.IAMArn(zipDownloadSweepStateMachineName(config)).statemachine;
}

/** Retrier of a sweep pass (a Lambda fault or a failed S3 check), then the pass is given up. */
export const ZIP_DOWNLOAD_SWEEP_RETRY = {
    intervalSeconds: 10,
    backoffRate: 2,
    maxAttempts: 5,
    maxDelaySeconds: 120,
};

/**
 * With app.zipDownloads on: the delayed sweep of a stopped zip download job (#1125 D16). An abort's
 * immediate answer is not final (on demo21 an upload whose abort had answered, and whose ListParts
 * then answered NoSuchUpload, was listed again minutes later with a part accepted before the stop),
 * so every stopper also starts this Standard state machine, once per job (backend
 * common/zipDownloads.schedule_delayed_sweep): Wait, a sweep pass, Wait, a last pass. Each pass is the
 * zip download service's `sweep` action (common/zipDownloads.sweep_recorded_uploads), which aborts only
 * the archive uploads the job row recorded at its exact archive key, unless its archive landed. A
 * failed first pass still runs the second. Bounded history: two waits and two tasks of at most
 * 5 + 3 x 5 events each.
 *
 * The service gets the ARN (built from the fixed name) and StartExecution on it; the state machine
 * invokes the service with the sweep payload. Not built with the feature off.
 */
export function buildZipDownloadSweep(
    scope: Construct,
    zipDownloadService: lambda.Function,
    storageResources: storageResources,
    config: Config.Config
): sfn.StateMachine {
    const pass = (id: string, final: boolean) => {
        const task = new tasks.LambdaInvoke(scope, id, {
            lambdaFunction: zipDownloadService,
            payload: sfn.TaskInput.fromObject({
                action: "sweep",
                zipSweep: sfn.JsonPath.objectAt("$.zipSweep"),
                final,
            }),
            resultPath: sfn.JsonPath.DISCARD,
            retryOnServiceExceptions: false,
            taskTimeout: sfn.Timeout.duration(Duration.minutes(5)),
        });
        task.addRetry({
            errors: [sfn.Errors.ALL],
            interval: Duration.seconds(ZIP_DOWNLOAD_SWEEP_RETRY.intervalSeconds),
            backoffRate: ZIP_DOWNLOAD_SWEEP_RETRY.backoffRate,
            maxAttempts: ZIP_DOWNLOAD_SWEEP_RETRY.maxAttempts,
            maxDelay: Duration.seconds(ZIP_DOWNLOAD_SWEEP_RETRY.maxDelaySeconds),
            jitterStrategy: sfn.JitterType.FULL,
        });
        return task;
    };
    const firstWait = new sfn.Wait(scope, "ZipDownloadSweepFirstWait", {
        time: sfn.WaitTime.duration(Duration.seconds(ZIP_DOWNLOAD_SWEEP_WAIT_SECONDS.first)),
    });
    const first = pass("ZipDownloadSweepFirst", false);
    const secondWait = new sfn.Wait(scope, "ZipDownloadSweepSecondWait", {
        time: sfn.WaitTime.duration(Duration.seconds(ZIP_DOWNLOAD_SWEEP_WAIT_SECONDS.second)),
    });
    const final = pass("ZipDownloadSweepFinal", true);
    first.addCatch(secondWait, { errors: [sfn.Errors.ALL], resultPath: "$.sweepError" });
    const swept = new sfn.Succeed(scope, "ZipDownloadSwept", {
        comment: "The delayed sweep of a stopped zip download ended.",
    });

    const logGroup = new logs.LogGroup(scope, "ZipDownloadSweepStateMachineLogGroup", {
        logGroupName:
            "/aws/vendedlogs/VAMSStateMachine-ZipDownloadSweep" +
            generateUniqueNameHash(
                config.env.coreStackName,
                config.env.account,
                "ZipDownloadSweepStateMachineLogGroup",
                10
            ),
        encryptionKey: storageResources.encryption.kmsKey,
        retention: logs.RetentionDays.ONE_YEAR,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const stateMachine = new sfn.StateMachine(scope, "ZipDownloadSweepStateMachine", {
        stateMachineName: zipDownloadSweepStateMachineName(config),
        stateMachineType: sfn.StateMachineType.STANDARD,
        definitionBody: sfn.DefinitionBody.fromChainable(
            firstWait.next(first).next(secondWait).next(final).next(swept)
        ),
        timeout: Duration.hours(2),
        logs: {
            destination: logGroup,
            // The input carries ids and an object key only; level ALL satisfies AwsSolutions-SF1.
            includeExecutionData: false,
            level: sfn.LogLevel.ALL,
        },
        tracingEnabled: true,
    });
    NagSuppressions.addResourceSuppressions(
        stateMachine,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "Invokes the zip download service function (its sweep action) and its versions; writes its own log group.",
            },
        ],
        true
    );

    const arn = zipDownloadSweepStateMachineArn(config);
    zipDownloadService.addEnvironment("ZIP_DOWNLOAD_SWEEP_STATE_MACHINE_ARN", arn);
    zipDownloadService.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["states:StartExecution"],
            resources: [arn],
        })
    );
    return stateMachine;
}
