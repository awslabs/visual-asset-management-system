/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { storageResources } from "../storage-builder";
import * as cdk from "aws-cdk-lib";

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

export function buildMetadataIndexingFunction(
    scope: Construct,
    storageResources: storageResources,
    aossEndpoint: string,
    indexNameParam: string,
    handlerType: "a" | "m"
): lambda.Function {
    const fun = new lambda.DockerImageFunction(scope, "idx" + handlerType, {
        code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`), {
            cmd: [`backend.handlers.indexing.streams.lambda_handler_${handlerType}`],
        }),
        timeout: Duration.minutes(15),
        memorySize: 3008,
        environment: {
            METADATA_STORAGE_TABLE_NAME: storageResources.dynamo.metadataStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            ASSET_BUCKET_NAME: storageResources.s3.assetBucket.bucketName,
            AOSS_ENDPOINT_PARAM: aossEndpoint,
            AOSS_INDEX_NAME_PARAM: indexNameParam,
        },
    });

    // add access to read the parameter store param aossEndpoint
    fun.role?.addToPrincipalPolicy(
        new cdk.aws_iam.PolicyStatement({
            actions: ["ssm:GetParameter"],
            resources: [
                `arn:aws:ssm:${cdk.Stack.of(scope).region}:${
                    cdk.Stack.of(scope).account
                }:parameter/${cdk.Stack.of(scope).stackName}/*`,
            ],
        })
    );

    storageResources.dynamo.metadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.s3.assetBucket.grantRead(fun);

    // trigger the lambda from the dynamodb db streams
    storageResources.dynamo.metadataStorageTable.grantStreamRead(fun);
    storageResources.dynamo.assetStorageTable.grantStreamRead(fun);

    return fun;
}
