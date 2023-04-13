/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";

interface AuthFunctions {
    groups: lambda.Function;
}
export function buildAuthFunctions(scope: Construct, ddbtable: dynamodb.Table): AuthFunctions {
    return {
        groups: buildAuthFunction(scope, ddbtable, "groups"),
    };
}

export function buildAuthFunction(
    scope: Construct,
    ddbtable: dynamodb.Table,
    name: string
): lambda.Function {
    const fun = new lambda.DockerImageFunction(scope, name + "-metadata", {
        code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`), {
            cmd: [`backend.handlers.auth.${name}.lambda_handler`],
        }),
        timeout: Duration.minutes(1),
        memorySize: 512,
        environment: {
            TABLE_NAME: ddbtable.tableName,
        },
    });
    ddbtable.grantReadWriteData(fun);
    return fun;
}
