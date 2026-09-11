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
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    suppressCdkNagLambda,
    setupSecurityAndLoggingEnvironmentAndPermissions,
} from "../helper/security";

export function buildConfigService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "configService";
    let urlFormat = "";

    //Only fill in if we have locaiton services enabled since this is not in all aws partitions
    if (config.app.useLocationService.enabled) {
        urlFormat = `https://maps.${
            Service.Service("GEO", false).Endpoint
        }/v2/styles/Standard/descriptor?key=<apiKey>`;
    }

    const fun = new lambda.Function(scope, name, {
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
            LOCATION_SERVICE_API_KEY_ARN_SSM_PARAM: config.locationServiceApiKeyArnSSMParam,
            LOCATION_SERVICE_URL_FORMAT: urlFormat,
            WEB_DEPLOYED_URL_SSM_PARAM: config.webUrlDeploymentSSMParam,
        },
    });

    storageResources.dynamo.appFeatureEnabledStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);

    // The two parameters this handler reads, named exactly. Both paths are known at synthesis and are
    // the only ones it looks up (see the environment variables above), so a wildcard over every
    // parameter whose path merely contains the deployment name grants read access to unrelated
    // configuration in the same account.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["ssm:GetParameter", "ssm:GetParameters"],
            resources: [
                Service.IAMArn(config.locationServiceApiKeyArnSSMParam.replace(/^\/+/, "")).ssm,
                Service.IAMArn(config.webUrlDeploymentSSMParam.replace(/^\/+/, "")).ssm,
            ],
        })
    );

    // Only reachable when Amazon Location Service is deployed — there is no API key to describe
    // otherwise, and the resource wildcard cannot be narrowed further because the key is created in a
    // separate stack whose name this builder does not have.
    if (config.app.useLocationService.enabled) {
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["geo:DescribeKey"],
                resources: [Service.IAMArn("*").geoapi],
            })
        );
    }

    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    return fun;
}
