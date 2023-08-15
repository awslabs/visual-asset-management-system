/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { OpensearchServerlessConstruct } from "../constructs/opensearch-serverless";
import { storageResources } from "../storage-builder";
import { iam } from "cdk-nag/lib/rules";
import * as cdk from "aws-cdk-lib";

export function buildSearchFunction(
    scope: Construct,
    aossEndpoint: string,
    indexNameParam: string,
    aossConstruct: OpensearchServerlessConstruct,
    storageResources: storageResources
): lambda.Function {
    const fun = new lambda.DockerImageFunction(scope, "srch", {
        code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`), {
            cmd: ["backend.handlers.search.search.lambda_handler"],
        }),
        timeout: Duration.minutes(15),
        memorySize: 3008,
        environment: {
            AOSS_ENDPOINT_PARAM: aossEndpoint,
            AOSS_INDEX_NAME_PARAM: indexNameParam,
            AUTH_ENTITIES_TABLE: storageResources.dynamo.authEntitiesStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
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

    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    aossConstruct.grantCollectionAccess(fun);

    return fun;
}
