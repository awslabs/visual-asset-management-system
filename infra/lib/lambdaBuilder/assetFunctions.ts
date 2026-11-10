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
import { Duration } from "aws-cdk-lib";
import {
    suppressCdkNagErrorsByGrantReadWrite,
    grantReadWritePermissionsToAllAssetBuckets,
    grantReadPermissionsToAllAssetBuckets,
} from "../helper/security";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import * as logs from "aws-cdk-lib/aws-logs";
import { NagSuppressions } from "cdk-nag";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import {
    PREPARED_DOWNLOADS_PREFIX,
    ZIP_DOWNLOAD_USER_ASSET_INDEX,
    storageResources,
} from "../nestedStacks/storage/storageBuilder-nestedStack";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    generateUniqueNameHash,
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
 * The six routes the zip download service serves (backend common/apiRoutes.py ZIP_DOWNLOAD_ROUTES;
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
        ZIP_DOWNLOAD_RETENTION_DAYS: zip.retentionDays.toString(),
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
 * one function for all six routes, plus the delayed sweep's passes (abort a stopped job's recorded
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
