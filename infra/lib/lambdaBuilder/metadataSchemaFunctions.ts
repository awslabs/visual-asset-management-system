/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { storageResources } from "../storage-builder";

export function buildMetadataSchemaService(
    scope: Construct,
    storageResources: storageResources
): lambda.Function {
    const name = "metadataschema";
    const fn = new lambda.DockerImageFunction(scope, name, {
        code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`), {
            cmd: [`backend.handlers.metadataschema.schema.lambda_handler`],
        }),
        timeout: Duration.minutes(15),
        memorySize: 512,
        environment: {
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            METADATA_SCHEMA_STORAGE_TABLE_NAME:
                storageResources.dynamo.metadataSchemaStorageTable.tableName,
        },
    });
    storageResources.dynamo.databaseStorageTable.grantReadData(fn);
    storageResources.dynamo.metadataSchemaStorageTable.grantReadWriteData(fn);

    return fn;
}
