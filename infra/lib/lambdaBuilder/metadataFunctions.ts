/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { storageResources } from "../storage-builder";

export function buildMetadataFunctions(
    scope: Construct,
    storageResources: storageResources
): lambda.Function[] {
    return ["create", "read", "update", "delete"].map((f) =>
        buildMetadataFunction(scope, storageResources, f)
    );
}

export function buildMetadataFunction(
    scope: Construct,
    storageResources: storageResources,
    name: string
): lambda.Function {
    const fun = new lambda.DockerImageFunction(scope, name + "-metadata", {
        code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`), {
            cmd: [`backend.handlers.metadata.${name}.lambda_handler`],
        }),
        timeout: Duration.minutes(15),
        memorySize: 3008,
        environment: {
            METADATA_STORAGE_TABLE_NAME: storageResources.dynamo.metadataStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
        },
    });
    storageResources.dynamo.metadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    return fun;
}
