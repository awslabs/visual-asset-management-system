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
import * as Service from "../helper/service-helper";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { suppressCdkNagErrorsByGrantReadWrite, grantReadWritePermissionsToAllAssetBuckets, grantReadPermissionsToAllAssetBuckets } from "../helper/security";
import * as kms from "aws-cdk-lib/aws-kms";
import * as iam from "aws-cdk-lib/aws-iam";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../helper/security";

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
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
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
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);

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
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME: storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            METADATA_STORAGE_TABLE_NAME: storageResources.dynamo.metadataStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
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
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);

    // trigger the lambda from the dynamodb db streams
    storageResources.dynamo.metadataStorageTable.grantStreamRead(fun);
    storageResources.dynamo.assetStorageTable.grantStreamRead(fun);

    grantReadPermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    return fun;
}

export function buildSqsBucketSyncFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    indexingS3ObjectMetadataFunction: lambda.Function | undefined,
    bucketName: string,
    bucketPrefix: string,
    defaultDatabaseId: string,
    handlerType: "created" | "deleted",
    index: number,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const assetTopicWildcardArn = cdk.Fn.sub(`arn:${Service.Partition()}:sns:*:*:AssetTopic*`);
    const fun = new lambda.Function(scope, "sqsBucketSync-"+handlerType+'-'+index, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.indexing.sqsBucketSync.lambda_handler_`+handlerType,
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
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME: storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            METADATA_STORAGE_TABLE_NAME: storageResources.dynamo.metadataStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            ASSET_VERSIONS_STORAGE_TABLE_NAME: storageResources.dynamo.assetVersionsStorageTable.tableName,
            ASSET_LINKS_STORAGE_TABLE_NAME: storageResources.dynamo.assetLinksStorageTable.tableName, //Not directly used but needed to execute create_asset functions
            TAG_TYPES_STORAGE_TABLE_NAME: storageResources.dynamo.tagTypeStorageTable.tableName, //Not directly used but needed to execute create_asset functions
            TAG_STORAGE_TABLE_NAME: storageResources.dynamo.tagStorageTable.tableName, //Not directly used but needed to execute create_asset functions
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            INDEXING_FUNCTION_NAME: indexingS3ObjectMetadataFunction? indexingS3ObjectMetadataFunction.functionName : "",
            ASSET_BUCKET_NAME: bucketName,
            ASSET_BUCKET_PREFIX: bucketPrefix,
            DEFAULT_DATABASE_ID: defaultDatabaseId
        },
    });

    storageResources.dynamo.metadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetLinksStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.tagTypeStorageTable.grantReadData(fun);
    storageResources.dynamo.tagStorageTable.grantReadData(fun);


    if (indexingS3ObjectMetadataFunction) {
        indexingS3ObjectMetadataFunction.grantInvoke(fun);
    }

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["sns:CreateTopic", "sns:ListTopics", "sns:DeleteTopic"],
            resources: [assetTopicWildcardArn],
        })
    );

    grantReadWritePermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    return fun;
}
