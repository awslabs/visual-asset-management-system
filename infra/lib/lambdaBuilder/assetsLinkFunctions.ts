/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";
import { kmsKeyLambdaPermissionAddToResourcePolicy } from "../helper/security";
import * as Config from "../../config/config";

export function buildAssetLinkService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetLinksStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
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
        environment: {
            ASSET_LINKS_STORAGE_TABLE_NAME: assetLinksStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
        },
    });
    assetLinksStorageTable.grantReadWriteData(assetLinksService);
    assetStorageTable.grantReadWriteData(assetLinksService);
    authEntitiesStorageTable.grantReadWriteData(assetLinksService);
    userRolesStorageTable.grantReadWriteData(assetLinksService);
    kmsKeyLambdaPermissionAddToResourcePolicy(assetLinksService, kmsKey);
    return assetLinksService;
}

export function buildGetAssetLinksFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetLinksStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "getAssetLinksService";
    const getAssetLinksService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assetLinks.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        environment: {
            ASSET_LINKS_STORAGE_TABLE_NAME: assetLinksStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
        },
    });
    assetLinksStorageTable.grantReadWriteData(getAssetLinksService);
    assetStorageTable.grantReadWriteData(getAssetLinksService);
    authEntitiesStorageTable.grantReadWriteData(getAssetLinksService);
    userRolesStorageTable.grantReadWriteData(getAssetLinksService);
    kmsKeyLambdaPermissionAddToResourcePolicy(getAssetLinksService, kmsKey);
    return getAssetLinksService;
}

export function buildDeleteAssetLinksFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetLinksStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "deleteAssetLinksService";
    const deleteAssetLinksService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assetLinks.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        environment: {
            ASSET_LINKS_STORAGE_TABLE_NAME: assetLinksStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
        },
    });
    assetLinksStorageTable.grantReadWriteData(deleteAssetLinksService);
    assetStorageTable.grantReadWriteData(deleteAssetLinksService);
    authEntitiesStorageTable.grantReadWriteData(deleteAssetLinksService);
    userRolesStorageTable.grantReadWriteData(deleteAssetLinksService);
    kmsKeyLambdaPermissionAddToResourcePolicy(deleteAssetLinksService, kmsKey);
    return deleteAssetLinksService;
}
