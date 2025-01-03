/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../helper/security";

export function buildCreateDatabaseLambdaFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "createDatabase";
    const createDatabaseFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.databases.${name}.lambda_handler`,
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
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
        },
    });

    storageResources.dynamo.databaseStorageTable.grantReadWriteData(createDatabaseFunction);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(createDatabaseFunction);
    storageResources.dynamo.userRolesStorageTable.grantReadData(createDatabaseFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(createDatabaseFunction, kmsKey);
    globalLambdaEnvironmentsAndPermissions(createDatabaseFunction, config);
    return createDatabaseFunction;
}

export function buildDatabaseService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "databaseService";
    const databaseService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.databases.${name}.lambda_handler`,
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
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            PIPELINE_STORAGE_TABLE_NAME: storageResources.dynamo.pipelineStorageTable.tableName,
            WORKFLOW_STORAGE_TABLE_NAME: storageResources.dynamo.workflowStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
        },
    });

    storageResources.dynamo.databaseStorageTable.grantReadWriteData(databaseService);
    storageResources.dynamo.workflowStorageTable.grantReadData(databaseService);
    storageResources.dynamo.pipelineStorageTable.grantReadData(databaseService);
    storageResources.dynamo.assetStorageTable.grantReadData(databaseService);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(databaseService);
    storageResources.dynamo.userRolesStorageTable.grantReadData(databaseService);
    kmsKeyLambdaPermissionAddToResourcePolicy(databaseService, kmsKey);
    globalLambdaEnvironmentsAndPermissions(databaseService, config);

    return databaseService;
}
