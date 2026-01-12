/*
 * Garnet Framework indexer Lambda functions for VAMS CDK infrastructure.
 *
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import {
    suppressCdkNagErrorsByGrantReadWrite,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    grantReadWritePermissionsToAllAssetBuckets,
    grantReadPermissionsToAllAssetBuckets,
} from "../../../../helper/security";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../config/config";
import * as Config from "../../../../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import * as ServiceHelper from "../../../../helper/service-helper";

export function buildGarnetDataIndexDatabaseFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "garnetDataIndexDatabase";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../../../../backend/backend`)),
        handler: `handlers.addon.garnetFramework.${name}.lambda_handler`,
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
            // Database tables
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            DATABASE_METADATA_STORAGE_TABLE_NAME:
                storageResources.dynamo.databaseMetadataStorageTable.tableName,
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,

            // Authentication tables
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            CONSTRAINTS_TABLE_NAME: storageResources.dynamo.constraintsStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,

            // Garnet configuration
            GARNET_INGESTION_QUEUE_URL:
                config.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl,
            GARNET_API_ENDPOINT: config.app.addons.useGarnetFramework.garnetApiEndpoint,
        },
    });

    // Grant DynamoDB permissions
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.constraintsStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);

    // Grant permission to send messages to external Garnet ingestion queue
    const garnetQueueArn = convertSqsUrlToArn(
        config.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl,
        ServiceHelper.Partition()
    );

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["sqs:SendMessage"],
            resources: [garnetQueueArn],
        })
    );

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildGarnetDataIndexAssetFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "garnetDataIndexAsset";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../../../../backend/backend`)),
        handler: `handlers.addon.garnetFramework.${name}.lambda_handler`,
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
            // Asset tables
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            ASSET_FILE_METADATA_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetFileMetadataStorageTable.tableName,
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            ASSET_LINKS_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.assetLinksStorageTableV2.tableName,
            ASSET_LINKS_METADATA_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetLinksMetadataStorageTable.tableName,
            ASSET_VERSIONS_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetVersionsStorageTable.tableName,

            // Authentication tables
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            CONSTRAINTS_TABLE_NAME: storageResources.dynamo.constraintsStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,

            // Garnet configuration
            GARNET_INGESTION_QUEUE_URL:
                config.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl,
            GARNET_API_ENDPOINT: config.app.addons.useGarnetFramework.garnetApiEndpoint,
        },
    });

    // Grant DynamoDB permissions
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetLinksMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadData(fun);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.constraintsStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);

    // Grant permission to send messages to external Garnet ingestion queue
    const garnetQueueArn = convertSqsUrlToArn(
        config.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl,
        ServiceHelper.Partition()
    );

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["sqs:SendMessage"],
            resources: [garnetQueueArn],
        })
    );

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildGarnetDataIndexFileFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "garnetDataIndexFile";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../../../../backend/backend`)),
        handler: `handlers.addon.garnetFramework.${name}.lambda_handler`,
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
            // File tables
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            ASSET_FILE_METADATA_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetFileMetadataStorageTable.tableName,
            FILE_ATTRIBUTE_STORAGE_TABLE_NAME:
                storageResources.dynamo.fileAttributeStorageTable.tableName,
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,

            // Authentication tables
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            CONSTRAINTS_TABLE_NAME: storageResources.dynamo.constraintsStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,

            // Garnet configuration
            GARNET_INGESTION_QUEUE_URL:
                config.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl,
            GARNET_API_ENDPOINT: config.app.addons.useGarnetFramework.garnetApiEndpoint,
        },
    });

    // Grant DynamoDB permissions
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.fileAttributeStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.constraintsStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);

    // Grant permission to send messages to external Garnet ingestion queue
    const garnetQueueArn = convertSqsUrlToArn(
        config.app.addons.useGarnetFramework.garnetIngestionQueueSqsUrl,
        ServiceHelper.Partition()
    );

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["sqs:SendMessage"],
            resources: [garnetQueueArn],
        })
    );

    // Apply security helpers
    grantReadPermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

/**
 * Convert SQS URL to ARN format for IAM permissions.
 *
 * Converts: https://sqs.region.amazonaws.com/account/queue-name
 * To: arn:partition:sqs:region:account:queue-name
 *
 * @param sqsUrl - The SQS queue URL
 * @param partition - The AWS partition (aws, aws-us-gov, aws-cn)
 * @returns The SQS queue ARN
 */
function convertSqsUrlToArn(sqsUrl: string, partition: string): string {
    const sqsUrlParts = sqsUrl.replace("https://", "").split("/");
    const sqsRegion = sqsUrlParts[0].split(".")[1]; // Extract region from sqs.region.amazonaws.com
    const sqsAccount = sqsUrlParts[1];
    const sqsQueueName = sqsUrlParts[2];
    return `arn:${partition}:sqs:${sqsRegion}:${sqsAccount}:${sqsQueueName}`;
}
