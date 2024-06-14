/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import * as cdk from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import { kmsKeyLambdaPermissionAddToResourcePolicy } from "../helper/security";

export function buildSearchFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "search";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.search.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.openSearch.useProvisioned.enabled ||
            (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas)
                ? vpc
                : undefined, //Use VPC when provisioned OS or flag to use for all lambdas
        vpcSubnets:
            config.app.openSearch.useProvisioned.enabled ||
            (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas)
                ? { subnets: subnets }
                : undefined,

        environment: {
            AOS_ENDPOINT_PARAM: config.openSearchDomainEndpointSSMParam,
            AOS_INDEX_NAME_PARAM: config.openSearchIndexNameSSMParam,
            AOS_TYPE: config.app.openSearch.useProvisioned.enabled ? "es" : "aoss",
            AOS_DISABLED:
                !config.app.openSearch.useProvisioned.enabled &&
                !config.app.openSearch.useServerless.enabled
                    ? "true"
                    : "false",
            AUTH_ENTITIES_TABLE: storageResources.dynamo.authEntitiesStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
        },
    });

    // add access to read the parameter store param aosEndpoint
    fun.role?.addToPrincipalPolicy(
        new cdk.aws_iam.PolicyStatement({
            actions: ["ssm:GetParameter"],
            resources: [Service.IAMArn("*vams*").ssm],
        })
    );

    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);

    return fun;
}

export function buildIndexingFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    handlerType: "a" | "m",
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const fun = new lambda.Function(scope, "idx" + handlerType, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.indexing.streams.lambda_handler_${handlerType}`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.openSearch.useProvisioned.enabled ||
            (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas)
                ? vpc
                : undefined, //Use VPC when provisioned OS or flag to use for all lambdas
        vpcSubnets:
            config.app.openSearch.useProvisioned.enabled ||
            (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas)
                ? { subnets: subnets }
                : undefined,

        environment: {
            METADATA_STORAGE_TABLE_NAME: storageResources.dynamo.metadataStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            ASSET_BUCKET_NAME: storageResources.s3.assetBucket.bucketName,
            AOS_ENDPOINT_PARAM: config.openSearchDomainEndpointSSMParam,
            AOS_INDEX_NAME_PARAM: config.openSearchIndexNameSSMParam,
            AOS_TYPE: config.app.openSearch.useProvisioned.enabled ? "es" : "aoss",
        },
    });

    // add access to read the parameter store param aossEndpoint
    fun.role?.addToPrincipalPolicy(
        new cdk.aws_iam.PolicyStatement({
            actions: ["ssm:GetParameter"],
            resources: [Service.IAMArn("*vams*").ssm],
        })
    );

    storageResources.dynamo.metadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.s3.assetBucket.grantRead(fun);

    // trigger the lambda from the dynamodb db streams
    storageResources.dynamo.metadataStorageTable.grantStreamRead(fun);
    storageResources.dynamo.assetStorageTable.grantStreamRead(fun);

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);

    return fun;
}
