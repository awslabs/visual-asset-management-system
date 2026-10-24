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
    grantReadPermissionsToAllAssetBuckets,
} from "../helper/security";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    suppressCdkNagLambda,
    suppressCdkNagDynamoStreamListWildcard,
    setupSecurityAndLoggingEnvironmentAndPermissions,
} from "../helper/security";
import { searchLambdasInVpc } from "../helper/searchPlacement";

export function buildSearchFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "search";
    const inVpc = searchLambdasInVpc(config);
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.osSemanticSearch.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc: inVpc ? vpc : undefined, //Provisioned OpenSearch, a private Serverless collection, or useForAllLambdas
        vpcSubnets: inVpc ? { subnets: subnets } : undefined,

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
        },
    });

    // add access to read the parameter store param for OpenSearch endpoint
    fun.role?.addToPrincipalPolicy(
        new cdk.aws_iam.PolicyStatement({
            actions: ["ssm:GetParameter"],
            resources: [Service.IAMArn("*" + config.name + "*").ssm],
        })
    );

    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

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
    const inVpc = searchLambdasInVpc(config);
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.osSemanticSearch.osFileIndexer.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc: inVpc ? vpc : undefined, //Provisioned OpenSearch, a private Serverless collection, or useForAllLambdas
        vpcSubnets: inVpc ? { subnets: subnets } : undefined,

        environment: {
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

    // Grant permissions
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.fileAttributeStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);

    // Grant S3 read permissions
    grantReadPermissionsToAllAssetBuckets(fun);

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
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
    const inVpc = searchLambdasInVpc(config);
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.osSemanticSearch.osAssetIndexer.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc: inVpc ? vpc : undefined, //Provisioned OpenSearch, a private Serverless collection, or useForAllLambdas
        vpcSubnets: inVpc ? { subnets: subnets } : undefined,

        environment: {
            OPENSEARCH_ASSET_INDEX_SSM_PARAM: config.openSearchAssetIndexNameSSMParam,
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

    // Grant permissions
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadData(fun);

    // Grant stream read permissions
    storageResources.dynamo.assetStorageTable.grantStreamRead(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantStreamRead(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantStreamRead(fun);
    storageResources.dynamo.assetLinksMetadataStorageTable.grantStreamRead(fun);

    // Apply security helpers
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagDynamoStreamListWildcard(fun);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    return fun;
}
