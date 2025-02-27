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
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../helper/security";
import * as Config from "../../config/config";

export function buildTagTypeService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "tagTypeService";
    const tagTypeService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.tagTypes.${name}.lambda_handler`,
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
            TAG_TYPES_STORAGE_TABLE_NAME: storageResources.dynamo.tagTypeStorageTable.tableName,
            TAGS_STORAGE_TABLE_NAME: storageResources.dynamo.tagStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });

    storageResources.dynamo.tagStorageTable.grantReadWriteData(tagTypeService);
    storageResources.dynamo.tagTypeStorageTable.grantReadWriteData(tagTypeService);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(tagTypeService);
    storageResources.dynamo.userRolesStorageTable.grantReadData(tagTypeService);
    storageResources.dynamo.rolesStorageTable.grantReadData(tagTypeService);
    kmsKeyLambdaPermissionAddToResourcePolicy(tagTypeService, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(tagTypeService, config);
    return tagTypeService;
}

export function buildCreateTagTypeFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "createTagTypes";
    const createTagTypeFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.tagTypes.${name}.lambda_handler`,
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
            TAG_TYPES_STORAGE_TABLE_NAME: storageResources.dynamo.tagTypeStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });

    storageResources.dynamo.tagTypeStorageTable.grantReadWriteData(createTagTypeFunction);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(createTagTypeFunction);
    storageResources.dynamo.userRolesStorageTable.grantReadData(createTagTypeFunction);
    storageResources.dynamo.rolesStorageTable.grantReadData(createTagTypeFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(
        createTagTypeFunction,
        storageResources.encryption.kmsKey
    );
    globalLambdaEnvironmentsAndPermissions(createTagTypeFunction, config);
    return createTagTypeFunction;
}
