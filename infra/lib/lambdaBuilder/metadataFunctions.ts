/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Metadata Lambda functions for VAMS CDK infrastructure.
 * Centralized metadata service handling all entity types.
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import {
    suppressCdkNagErrorsByGrantReadWrite,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../helper/security";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import * as s3AssetBuckets from "../helper/s3AssetBuckets";

export function buildMetadataService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "metadataService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.metadata.${name}.lambda_handler`,
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
            ASSET_LINKS_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.assetLinksStorageTableV2.tableName,
            ASSET_LINKS_METADATA_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetLinksMetadataStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            DATABASE_METADATA_STORAGE_TABLE_NAME:
                storageResources.dynamo.databaseMetadataStorageTable.tableName,
            ASSET_FILE_METADATA_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetFileMetadataStorageTable.tableName,
            FILE_ATTRIBUTE_STORAGE_TABLE_NAME:
                storageResources.dynamo.fileAttributeStorageTable.tableName,
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            METADATA_SCHEMA_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.metadataSchemaStorageTableV2.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            CONSTRAINTS_TABLE_NAME: storageResources.dynamo.constraintsStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });

    // Grant DynamoDB permissions
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetLinksMetadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseMetadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fileAttributeStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.metadataSchemaStorageTableV2.grantReadData(fun);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.constraintsStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);

    // Grant S3 read permissions for file existence checks
    const assetBucketRecords = s3AssetBuckets.getS3AssetBucketRecords();
    for (const record of assetBucketRecords) {
        record.bucket.grantRead(fun);
    }

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
