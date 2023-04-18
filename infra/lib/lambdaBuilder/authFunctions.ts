/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { storageResources } from "../storage-builder";

interface AuthFunctions {
    groups: lambda.Function;
}
export function buildAuthFunctions(
    scope: Construct,
    storageResources: storageResources
): AuthFunctions {
    return {
        groups: buildAuthFunction(scope, storageResources, "groups"),
    };
}

export function buildAuthFunction(
    scope: Construct,
    storageResources: storageResources,
    name: string
): lambda.Function {
    const fun = new lambda.DockerImageFunction(scope, name, {
        code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`), {
            cmd: [`backend.handlers.auth.${name}.lambda_handler`],
        }),
        timeout: Duration.minutes(1),
        memorySize: 512,
        environment: {
            TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
        },
    });
    storageResources.dynamo.authEntitiesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    return fun;
}
