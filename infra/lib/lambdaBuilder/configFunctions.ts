/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as path from "path";
import { Construct } from "constructs";
import * as lambdaPython from "@aws-cdk/aws-lambda-python-alpha";
import { Duration } from "aws-cdk-lib";
export function buildConfigService(
    scope: Construct,
    assetStorageBucket: s3.Bucket
): lambda.Function {
    const name = "configService";
    const assetService = new lambda.DockerImageFunction(scope, name, {
        code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, `../../../backend/`), {
            cmd: [`backend.handlers.config.${name}.lambda_handler`],
        }),
        timeout: Duration.minutes(15),
        memorySize: 3008,
        environment: {
            ASSET_STORAGE_BUCKET: assetStorageBucket.bucketName,
        },
    });
    return assetService;
}
