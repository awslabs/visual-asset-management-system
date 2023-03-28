/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";
import * as lambdaPython from "@aws-cdk/aws-lambda-python-alpha";
import { Duration } from "aws-cdk-lib";

export function buildCreateDatabaseLambdaFunction(
    scope: Construct,
    databaseStorageTable: dynamodb.Table
): lambda.Function {
    const name = "createDatabase";
    const createDatabaseFunction = new lambda.DockerImageFunction(scope, name, {
        code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`), {
            cmd: ["backend.handlers.databases.createDatabase.lambda_handler"],
        }),
        timeout: Duration.minutes(15),
        memorySize: 3008,
        environment: {
            DATABASE_STORAGE_TABLE_NAME: databaseStorageTable.tableName,
        },
    });
    databaseStorageTable.grantReadWriteData(createDatabaseFunction);
    return createDatabaseFunction;
}

export function buildDatabaseService(
    scope: Construct,
    databaseStorageTable: dynamodb.Table,
    workflowStorageTable: dynamodb.Table,
    pipelineStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table
): lambda.Function {
    const name = "databaseService";
    const databaseService = new lambda.DockerImageFunction(scope, name, {
        code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`), {
            cmd: [`backend.handlers.databases.${name}.lambda_handler`],
        }),
        timeout: Duration.minutes(15),
        memorySize: 3008,
        environment: {
            DATABASE_STORAGE_TABLE_NAME: databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            PIPELINE_STORAGE_TABLE_NAME: pipelineStorageTable.tableName,
            WORKFLOW_STORAGE_TABLE_NAME: workflowStorageTable.tableName,
        },
    });
    databaseStorageTable.grantReadWriteData(databaseService);
    workflowStorageTable.grantReadData(databaseService);
    pipelineStorageTable.grantReadData(databaseService);
    assetStorageTable.grantReadData(databaseService);

    return databaseService;
}
