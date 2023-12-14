/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { storageResources } from "../storage-builder";

interface AuthFunctions {
    groups: lambda.Function;
    constraints: lambda.Function;
    scopeds3access: lambda.Function;
}
export function buildAuthFunctions(
    scope: Construct,
    storageResources: storageResources
): AuthFunctions {
    const storageBucketRole = new iam.Role(scope, "storageBucketRole", {
        assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });

    storageResources.s3.assetBucket.grantReadWrite(storageBucketRole);

    const scopeds3access = buildAuthFunction(scope, storageResources, "scopeds3access", {
        ROLE_ARN: storageBucketRole.roleArn,
        S3_BUCKET: storageResources.s3.assetBucket.bucketName,
    });

    storageBucketRole.assumeRolePolicy?.addStatements(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["sts:AssumeRole"],
            principals: [scopeds3access.role!],
        })
    );

    return {
        groups: buildAuthFunction(scope, storageResources, "groups"),
        constraints: buildAuthFunction(scope, storageResources, "finegrainedaccessconstraints"),
        scopeds3access,
    };
}

export function buildAuthFunction(
    scope: Construct,
    storageResources: storageResources,
    name: string,
    environment?: { [key: string]: string }
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
            ...environment,
        },
    });
    storageResources.dynamo.authEntitiesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    return fun;
}
