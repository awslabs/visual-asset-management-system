/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as path from "path";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../helper/security";
import * as Config from "../../config/config";

// Combined function for GET and DELETE operations
export function buildAssetLinksService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    config: Config.Config,
    assetLinksStorageTableV2: dynamodb.Table,
    assetLinksMetadataStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    rolesStorageTable: dynamodb.Table,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "assetLinksService";
    const assetLinksService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assetLinks.${name}.lambda_handler`,
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
            ASSET_LINKS_STORAGE_TABLE_V2_NAME: assetLinksStorageTableV2.tableName,
            ASSET_LINKS_METADATA_STORAGE_TABLE_NAME: assetLinksMetadataStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: rolesStorageTable.tableName,
        },
    });
    assetLinksStorageTableV2.grantReadWriteData(assetLinksService);
    assetLinksMetadataStorageTable.grantReadWriteData(assetLinksService);
    assetStorageTable.grantReadWriteData(assetLinksService);
    authEntitiesStorageTable.grantReadData(assetLinksService);
    userRolesStorageTable.grantReadData(assetLinksService);
    rolesStorageTable.grantReadData(assetLinksService);
    kmsKeyLambdaPermissionAddToResourcePolicy(assetLinksService, kmsKey);
    globalLambdaEnvironmentsAndPermissions(assetLinksService, config);
    return assetLinksService;
}

// Separate function for POST operations (create asset link)
export function buildCreateAssetLinkFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    config: Config.Config,
    assetLinksStorageTableV2: dynamodb.Table,
    assetLinksMetadataStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    rolesStorageTable: dynamodb.Table,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "createAssetLink";
    const createAssetLinkService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assetLinks.${name}.lambda_handler`,
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
            ASSET_LINKS_STORAGE_TABLE_V2_NAME: assetLinksStorageTableV2.tableName,
            ASSET_LINKS_METADATA_STORAGE_TABLE_NAME: assetLinksMetadataStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: rolesStorageTable.tableName,
        },
    });
    assetLinksStorageTableV2.grantReadWriteData(createAssetLinkService);
    assetLinksMetadataStorageTable.grantReadWriteData(createAssetLinkService);
    assetStorageTable.grantReadWriteData(createAssetLinkService);
    authEntitiesStorageTable.grantReadData(createAssetLinkService);
    userRolesStorageTable.grantReadData(createAssetLinkService);
    rolesStorageTable.grantReadData(createAssetLinkService);
    kmsKeyLambdaPermissionAddToResourcePolicy(createAssetLinkService, kmsKey);
    globalLambdaEnvironmentsAndPermissions(createAssetLinkService, config);
    return createAssetLinkService;
}

// New function for metadata operations
export function buildAssetLinksMetadataFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    config: Config.Config,
    assetLinksStorageTableV2: dynamodb.Table,
    assetLinksMetadataStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    rolesStorageTable: dynamodb.Table,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "assetLinksMetadataService";
    const assetLinksMetadataService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assetLinks.${name}.lambda_handler`,
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
            ASSET_LINKS_STORAGE_TABLE_V2_NAME: assetLinksStorageTableV2.tableName,
            ASSET_LINKS_METADATA_STORAGE_TABLE_NAME: assetLinksMetadataStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: rolesStorageTable.tableName,
        },
    });
    assetLinksStorageTableV2.grantReadWriteData(assetLinksMetadataService);
    assetLinksMetadataStorageTable.grantReadWriteData(assetLinksMetadataService);
    assetStorageTable.grantReadWriteData(assetLinksMetadataService);
    authEntitiesStorageTable.grantReadData(assetLinksMetadataService);
    userRolesStorageTable.grantReadData(assetLinksMetadataService);
    rolesStorageTable.grantReadData(assetLinksMetadataService);
    kmsKeyLambdaPermissionAddToResourcePolicy(assetLinksMetadataService, kmsKey);
    globalLambdaEnvironmentsAndPermissions(assetLinksMetadataService, config);
    return assetLinksMetadataService;
}
