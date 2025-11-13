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
import {
    suppressCdkNagErrorsByGrantReadWrite,
    grantReadWritePermissionsToAllAssetBuckets,
    grantReadPermissionsToAllAssetBuckets,
} from "../helper/security";
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
            OPENSEARCH_ENDPOINT_SSM_PARAM: config.openSearchDomainEndpointSSMParam,
            OPENSEARCH_ASSET_INDEX_SSM_PARAM: config.openSearchAssetIndexNameSSMParam,
            OPENSEARCH_FILE_INDEX_SSM_PARAM: config.openSearchFileIndexNameSSMParam,
            OPENSEARCH_TYPE: config.app.openSearch.useProvisioned.enabled
                ? "provisioned"
                : "serverless",
            OPENSEARCH_DISABLED:
                !config.app.openSearch.useProvisioned.enabled &&
                !config.app.openSearch.useServerless.enabled
                    ? "true"
                    : "false",
            AUTH_ENTITIES_TABLE: storageResources.dynamo.authEntitiesStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });

    // add access to read the parameter store param for OpenSearch endpoint
    fun.role?.addToPrincipalPolicy(
        new cdk.aws_iam.PolicyStatement({
            actions: ["ssm:GetParameter"],
            resources: [Service.IAMArn("*" + config.name + "*").ssm],
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

export function buildFileIndexingFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "fileIndexer";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.indexing.fileIndexer.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.openSearch.useProvisioned.enabled ||
            (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas)
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.openSearch.useProvisioned.enabled ||
            (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas)
                ? { subnets: subnets }
                : undefined,

        environment: {
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            METADATA_STORAGE_TABLE_NAME: storageResources.dynamo.metadataStorageTable.tableName,
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            OPENSEARCH_FILE_INDEX_SSM_PARAM: config.openSearchFileIndexNameSSMParam,
            OPENSEARCH_ENDPOINT_SSM_PARAM: config.openSearchDomainEndpointSSMParam,
            OPENSEARCH_TYPE: config.app.openSearch.useProvisioned.enabled
                ? "provisioned"
                : "serverless",
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });

    // Add access to read SSM parameters
    fun.role?.addToPrincipalPolicy(
        new cdk.aws_iam.PolicyStatement({
            actions: ["ssm:GetParameter"],
            resources: [Service.IAMArn("*" + config.name + "*").ssm],
        })
    );

    // Grant permissions
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.metadataStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);

    // Grant stream read permissions
    storageResources.dynamo.metadataStorageTable.grantStreamRead(fun);

    // Grant S3 read permissions
    grantReadPermissionsToAllAssetBuckets(fun);

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    return fun;
}

export function buildAssetIndexingFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "assetIndexer";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.indexing.assetIndexer.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.openSearch.useProvisioned.enabled ||
            (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas)
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.openSearch.useProvisioned.enabled ||
            (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas)
                ? { subnets: subnets }
                : undefined,

        environment: {
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            METADATA_STORAGE_TABLE_NAME: storageResources.dynamo.metadataStorageTable.tableName,
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            ASSET_LINKS_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.assetLinksStorageTableV2.tableName,
            ASSET_VERSIONS_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetVersionsStorageTable.tableName,
            OPENSEARCH_ASSET_INDEX_SSM_PARAM: config.openSearchAssetIndexNameSSMParam,
            OPENSEARCH_ENDPOINT_SSM_PARAM: config.openSearchDomainEndpointSSMParam,
            OPENSEARCH_TYPE: config.app.openSearch.useProvisioned.enabled
                ? "provisioned"
                : "serverless",
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });

    // Add access to read SSM parameters
    fun.role?.addToPrincipalPolicy(
        new cdk.aws_iam.PolicyStatement({
            actions: ["ssm:GetParameter"],
            resources: [Service.IAMArn("*" + config.name + "*").ssm],
        })
    );

    // Grant permissions
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.metadataStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadData(fun);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    storageResources.dynamo.rolesStorageTable.grantReadData(fun);

    // Grant stream read permissions
    storageResources.dynamo.assetStorageTable.grantStreamRead(fun);
    storageResources.dynamo.metadataStorageTable.grantStreamRead(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantStreamRead(fun);

    // Apply security helpers
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
    const fun = new lambda.Function(scope, "sqsBucketSync-" + handlerType + "-" + index, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.indexing.sqsBucketSync.lambda_handler_` + handlerType,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,

        environment: {
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            METADATA_STORAGE_TABLE_NAME: storageResources.dynamo.metadataStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            ASSET_VERSIONS_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetVersionsStorageTable.tableName,
            TAG_TYPES_STORAGE_TABLE_NAME: storageResources.dynamo.tagTypeStorageTable.tableName, //Not directly used but needed to execute create_asset functions
            TAG_STORAGE_TABLE_NAME: storageResources.dynamo.tagStorageTable.tableName, //Not directly used but needed to execute create_asset functions
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            INDEXING_FUNCTION_NAME: indexingS3ObjectMetadataFunction
                ? indexingS3ObjectMetadataFunction.functionName
                : "",
            ASSET_BUCKET_NAME: bucketName,
            ASSET_BUCKET_PREFIX: bucketPrefix,
            DEFAULT_DATABASE_ID: defaultDatabaseId,
        },
    });

    storageResources.dynamo.metadataStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadWriteData(fun);
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

export function buildReindexerFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "crOsReindexer";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.indexing.crReindexer.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.openSearch.useProvisioned.enabled ||
            (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas)
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.openSearch.useProvisioned.enabled ||
            (config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas)
                ? { subnets: subnets }
                : undefined,

        environment: {
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            METADATA_STORAGE_TABLE_NAME: storageResources.dynamo.metadataStorageTable.tableName,
            OPENSEARCH_ASSET_INDEX_SSM_PARAM: config.openSearchAssetIndexNameSSMParam,
            OPENSEARCH_FILE_INDEX_SSM_PARAM: config.openSearchFileIndexNameSSMParam,
            OPENSEARCH_ENDPOINT_SSM_PARAM: config.openSearchDomainEndpointSSMParam,
            OPENSEARCH_TYPE: config.app.openSearch.useProvisioned.enabled
                ? "provisioned"
                : "serverless",
        },
    });

    // Add access to read SSM parameters
    fun.role?.addToPrincipalPolicy(
        new cdk.aws_iam.PolicyStatement({
            actions: ["ssm:GetParameter"],
            resources: [Service.IAMArn("*" + config.name + "*").ssm],
        })
    );

    // Grant DynamoDB permissions
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.metadataStorageTable.grantReadWriteData(fun);

    // Grant S3 read permissions
    grantReadPermissionsToAllAssetBuckets(fun);

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    return fun;
}
