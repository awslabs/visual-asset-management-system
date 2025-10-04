/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Service from "../helper/service-helper";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../helper/security";

export function buildConfigService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    appFeatureEnabledStorageTable: dynamodb.Table,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "configService";

    let urlFormat = ''

    //Only fill in if we have locaiton services enabled since this is not in all aws partitions
    if(config.app.useLocationService.enabled) {
        urlFormat = `https://maps.${Service.Service("GEO").Endpoint}/v2/styles/Standard/descriptor?key=<apiKey>`
    }

    const configService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.config.${name}.lambda_handler`,
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
            APPFEATUREENABLED_STORAGE_TABLE_NAME: appFeatureEnabledStorageTable.tableName,
            LOCATION_SERVICE_API_KEY_ARN_SSM_PARAM: config.locationServiceApiKeyArnSSMParam,
            LOCATION_SERVICE_URL_FORMAT: urlFormat
        },
    });

    appFeatureEnabledStorageTable.grantReadData(configService);
    kmsKeyLambdaPermissionAddToResourcePolicy(configService, kmsKey);
    
    // Grant SSM read permissions for Location Service API Key parameter
    configService.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["ssm:GetParameter", "ssm:GetParameters"],
            resources: [
                Service.IAMArn("*" + config.name + "*").ssm,
            ],
        })
    );
    
    // Grant Location Services permissions to describe API keys
    configService.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["geo:DescribeKey"],
            resources: [
                Service.IAMArn("*").geoapi,
            ],
        })
    );
    
    globalLambdaEnvironmentsAndPermissions(configService, config);
    return configService;
}
