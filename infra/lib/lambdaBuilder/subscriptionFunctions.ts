/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as sns from "aws-cdk-lib/aws-sns";
import * as iam from "aws-cdk-lib/aws-iam";
import * as snsSubscriptions from "aws-cdk-lib/aws-sns-subscriptions";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";
import { kmsKeyLambdaPermissionAddToResourcePolicy } from "../helper/security";
import * as Service from "../../lib/helper/service-helper";
import * as Config from "../../config/config";

export function buildSubscriptionService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    subscriptionsStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    userStorageTable: dynamodb.Table,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "subscriptionService";
    const assetTopicWildcardArn = cdk.Fn.sub(`arn:${Service.Partition()}:sns:*:*:AssetTopic*`);
    const subscriptionServiceFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.subscription.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        environment: {
            SUBSCRIPTIONS_STORAGE_TABLE_NAME: subscriptionsStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
            USER_STORAGE_TABLE_NAME: userStorageTable.tableName
        },
    });

    subscriptionServiceFunction.addToRolePolicy(
        new iam.PolicyStatement({
            actions: [
                "sns:CreateTopic",
                "sns:ListTopics",
                "sns:Subscribe",
                "sns:DeleteTopic",
                "sns:ListSubscriptionsByTopic",
                "sns:BatchUnsubscribe",
                "sns:Unsubscribe",
                "sns:GetSubscriptionAttributes",
            ],
            resources: [assetTopicWildcardArn],
        })
    );

    subscriptionsStorageTable.grantReadWriteData(subscriptionServiceFunction);
    assetStorageTable.grantReadWriteData(subscriptionServiceFunction);
    authEntitiesStorageTable.grantReadWriteData(subscriptionServiceFunction);
    userRolesStorageTable.grantReadData(subscriptionServiceFunction);
    userStorageTable.grantReadWriteData(subscriptionServiceFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(subscriptionServiceFunction, kmsKey);
    return subscriptionServiceFunction;
}

export function buildCheckSubscriptionFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    subscriptionsStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "checkSubscriptionService";
    const checkSubscriptionService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.subscription.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        environment: {
            SUBSCRIPTIONS_STORAGE_TABLE_NAME: subscriptionsStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
        },
    });
    subscriptionsStorageTable.grantReadWriteData(checkSubscriptionService);
    assetStorageTable.grantReadWriteData(checkSubscriptionService);
    authEntitiesStorageTable.grantReadWriteData(checkSubscriptionService);
    userRolesStorageTable.grantReadData(checkSubscriptionService);
    kmsKeyLambdaPermissionAddToResourcePolicy(checkSubscriptionService, kmsKey);
    return checkSubscriptionService;
}

export function buildUnSubscribeFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    subscriptionsStorageTable: dynamodb.Table,
    assetStorageTable: dynamodb.Table,
    userRolesStorageTable: dynamodb.Table,
    authEntitiesStorageTable: dynamodb.Table,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "unsubscribeService";
    const assetTopicWildcardArn = cdk.Fn.sub(`arn:${Service.Partition()}:sns:*:*:AssetTopic*`);
    const unsubscribeServiceFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.subscription.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        environment: {
            SUBSCRIPTIONS_STORAGE_TABLE_NAME: subscriptionsStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: assetStorageTable.tableName,
            AUTH_TABLE_NAME: authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: userRolesStorageTable.tableName,
        },
    });

    unsubscribeServiceFunction.addToRolePolicy(
        new iam.PolicyStatement({
            actions: [
                "sns:ListTopics",
                "sns:Subscribe",
                "sns:DeleteTopic",
                "sns:ListSubscriptionsByTopic",
                "sns:BatchUnsubscribe",
                "sns:Unsubscribe",
                "sns:GetSubscriptionAttributes",
            ],
            resources: [assetTopicWildcardArn],
        })
    );

    subscriptionsStorageTable.grantReadWriteData(unsubscribeServiceFunction);
    assetStorageTable.grantReadWriteData(unsubscribeServiceFunction);
    authEntitiesStorageTable.grantReadWriteData(unsubscribeServiceFunction);
    userRolesStorageTable.grantReadData(unsubscribeServiceFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(unsubscribeServiceFunction, kmsKey);
    return unsubscribeServiceFunction;
}
