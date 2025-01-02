/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import * as path from "path";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import { kmsKeyLambdaPermissionAddToResourcePolicy, globalLambdaEnvironmentsAndPermissions } from "../helper/security";
import * as Service from "../../lib/helper/service-helper";
import * as Config from "../../config/config";

export function buildSendEmailFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const assetTopicWildcardArn = cdk.Fn.sub(`arn:${Service.Partition()}:sns:*:*:AssetTopic*`);
    const name = "sendEmail";
    const sendEmailFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.sendEmail.${name}.lambda_handler`,
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
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName
        },
    });

    sendEmailFunction.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["sns:Publish"],
            resources: [assetTopicWildcardArn],
        })
    );

    storageResources.dynamo.assetStorageTable.grantReadData(sendEmailFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(
        sendEmailFunction,
        storageResources.encryption.kmsKey
    );
    globalLambdaEnvironmentsAndPermissions(sendEmailFunction, config);
    return sendEmailFunction;
}
