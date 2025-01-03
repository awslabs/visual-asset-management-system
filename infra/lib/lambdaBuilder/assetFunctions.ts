/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as path from "path";
import * as sns from "aws-cdk-lib/aws-sns";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { suppressCdkNagErrorsByGrantReadWrite } from "../helper/security";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { buildSendEmailFunction } from "./sendEmailFunctions";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../helper/security";

export function buildAssetService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "assetService";
    const assetService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            S3_ASSET_STORAGE_BUCKET: storageResources.s3.assetBucket.bucketName,
            S3_ASSET_AUXILIARY_BUCKET: storageResources.s3.assetAuxiliaryBucket.bucketName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
        },
    });
    storageResources.dynamo.assetStorageTable.grantReadWriteData(assetService);
    storageResources.s3.assetAuxiliaryBucket.grantReadWrite(assetService);
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(assetService);
    storageResources.s3.assetBucket.grantReadWrite(assetService);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(assetService);
    storageResources.dynamo.userRolesStorageTable.grantReadData(assetService);
    kmsKeyLambdaPermissionAddToResourcePolicy(assetService, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(assetService, config);

    suppressCdkNagErrorsByGrantReadWrite(scope);

    return assetService;
}

export function buildAssetFiles(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "assetFiles";
    const assetService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            S3_ASSET_STORAGE_BUCKET: storageResources.s3.assetBucket.bucketName,
        },
    });
    storageResources.dynamo.assetStorageTable.grantReadWriteData(assetService);
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(assetService);
    storageResources.s3.assetBucket.grantRead(assetService);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(assetService);
    storageResources.dynamo.userRolesStorageTable.grantReadData(assetService);
    kmsKeyLambdaPermissionAddToResourcePolicy(assetService, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(assetService, config);

    suppressCdkNagErrorsByGrantReadWrite(scope);

    return assetService;
}

export function buildUploadAssetFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "uploadAsset";
    const assetTopicWildcardArn = cdk.Fn.sub(`arn:${Service.Partition()}:sns:*:*:AssetTopic*`);
    const sendEmailFunction = buildSendEmailFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    const uploadAssetFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            SUBSCRIPTIONS_STORAGE_TABLE_NAME:
                storageResources.dynamo.subscriptionsStorageTable.tableName,
            ASSET_LINKS_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetLinksStorageTable.tableName,
            SEND_EMAIL_FUNCTION_NAME: sendEmailFunction.functionName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            TAG_TYPES_STORAGE_TABLE_NAME: storageResources.dynamo.tagTypeStorageTable.tableName,
            TAG_STORAGE_TABLE_NAME: storageResources.dynamo.tagStorageTable.tableName,
            S3_ASSET_STORAGE_BUCKET: storageResources.s3.assetBucket.bucketName,
        },
    });
    uploadAssetFunction.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["sns:CreateTopic", "sns:ListTopics"],
            resources: [assetTopicWildcardArn],
        })
    );

    storageResources.s3.assetBucket.grantReadWrite(uploadAssetFunction);
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(uploadAssetFunction);
    storageResources.dynamo.assetStorageTable.grantReadWriteData(uploadAssetFunction);
    storageResources.dynamo.subscriptionsStorageTable.grantReadWriteData(uploadAssetFunction);
    storageResources.dynamo.assetLinksStorageTable.grantReadWriteData(uploadAssetFunction);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(uploadAssetFunction);
    storageResources.dynamo.userRolesStorageTable.grantReadData(uploadAssetFunction);
    storageResources.dynamo.tagTypeStorageTable.grantReadData(uploadAssetFunction);
    storageResources.dynamo.tagStorageTable.grantReadData(uploadAssetFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(
        uploadAssetFunction,
        storageResources.encryption.kmsKey
    );
    globalLambdaEnvironmentsAndPermissions(uploadAssetFunction, config);

    suppressCdkNagErrorsByGrantReadWrite(scope);
    sendEmailFunction.grantInvoke(uploadAssetFunction);
    return uploadAssetFunction;
}

export function buildAssetMetadataFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
) {
    const name = "metadata";
    const assetMetadataFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            S3_ASSET_STORAGE_BUCKET: storageResources.s3.assetBucket.bucketName,
        },
    });
    storageResources.s3.assetBucket.grantRead(assetMetadataFunction);
    storageResources.dynamo.assetStorageTable.grantReadData(assetMetadataFunction);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(assetMetadataFunction);
    storageResources.dynamo.userRolesStorageTable.grantReadData(assetMetadataFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(
        assetMetadataFunction,
        storageResources.encryption.kmsKey
    );
    globalLambdaEnvironmentsAndPermissions(assetMetadataFunction, config);

    suppressCdkNagErrorsByGrantReadWrite(scope);
    return assetMetadataFunction;
}

export function buildAssetColumnsFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
) {
    const name = "assetColumns";
    const assetColumnsFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            S3_ASSET_STORAGE_BUCKET: storageResources.s3.assetBucket.bucketName,
        },
    });
    storageResources.s3.assetBucket.grantRead(assetColumnsFunction);
    storageResources.dynamo.assetStorageTable.grantReadData(assetColumnsFunction);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(assetColumnsFunction);
    storageResources.dynamo.userRolesStorageTable.grantReadData(assetColumnsFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(
        assetColumnsFunction,
        storageResources.encryption.kmsKey
    );
    globalLambdaEnvironmentsAndPermissions(assetColumnsFunction, config);

    suppressCdkNagErrorsByGrantReadWrite(scope);
    return assetColumnsFunction;
}

export function buildStreamAuxiliaryPreviewAssetFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "streamAuxiliaryPreviewAsset";
    const streamAuxiliaryPreviewAssetFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            ASSET_AUXILIARY_BUCKET_NAME: storageResources.s3.assetAuxiliaryBucket.bucketName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
        },
    });
    storageResources.s3.assetAuxiliaryBucket.grantRead(streamAuxiliaryPreviewAssetFunction);
    storageResources.dynamo.assetStorageTable.grantReadData(streamAuxiliaryPreviewAssetFunction);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(
        streamAuxiliaryPreviewAssetFunction
    );
    storageResources.dynamo.userRolesStorageTable.grantReadData(
        streamAuxiliaryPreviewAssetFunction
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(
        streamAuxiliaryPreviewAssetFunction,
        storageResources.encryption.kmsKey
    );
    globalLambdaEnvironmentsAndPermissions(streamAuxiliaryPreviewAssetFunction, config);

    suppressCdkNagErrorsByGrantReadWrite(scope);
    return streamAuxiliaryPreviewAssetFunction;
}

export function buildDownloadAssetFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
) {
    const name = "downloadAsset";
    const downloadAssetFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            //S3_ENDPOINT: Service.Service("S3").Endpoint,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            S3_ASSET_STORAGE_BUCKET: storageResources.s3.assetBucket.bucketName,
            CRED_TOKEN_TIMEOUT_SECONDS: config.app.authProvider.credTokenTimeoutSeconds.toString(),
        },
    });
    storageResources.s3.assetBucket.grantRead(downloadAssetFunction);
    storageResources.dynamo.assetStorageTable.grantReadData(downloadAssetFunction);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(downloadAssetFunction);
    storageResources.dynamo.userRolesStorageTable.grantReadData(downloadAssetFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(
        downloadAssetFunction,
        storageResources.encryption.kmsKey
    );
    globalLambdaEnvironmentsAndPermissions(downloadAssetFunction, config);

    suppressCdkNagErrorsByGrantReadWrite(scope);

    return downloadAssetFunction;
}

export function buildRevertAssetFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "revertAsset";
    const revertAssetFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            S3_ASSET_STORAGE_BUCKET: storageResources.s3.assetBucket.bucketName,
        },
    });
    storageResources.s3.assetBucket.grantReadWrite(revertAssetFunction);
    storageResources.dynamo.databaseStorageTable.grantReadData(revertAssetFunction);
    storageResources.dynamo.assetStorageTable.grantReadWriteData(revertAssetFunction);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(revertAssetFunction);
    storageResources.dynamo.userRolesStorageTable.grantReadData(revertAssetFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(
        revertAssetFunction,
        storageResources.encryption.kmsKey
    );
    globalLambdaEnvironmentsAndPermissions(revertAssetFunction, config);

    suppressCdkNagErrorsByGrantReadWrite(scope);
    return revertAssetFunction;
}

export function buildUploadAssetWorkflowFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    uploadAssetWorkflowStateMachine: sfn.StateMachine,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "upload_asset_workflow";

    const uploadAssetWorkflowFunction = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `functions.assets.${name}.lambda_handler.lambda_handler`,
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
            UPLOAD_WORKFLOW_ARN: uploadAssetWorkflowStateMachine.stateMachineArn,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            TAG_TYPES_STORAGE_TABLE_NAME: storageResources.dynamo.tagTypeStorageTable.tableName,
            TAG_STORAGE_TABLE_NAME: storageResources.dynamo.tagStorageTable.tableName,
        },
    });

    uploadAssetWorkflowStateMachine.grantStartExecution(uploadAssetWorkflowFunction);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(uploadAssetWorkflowFunction);
    storageResources.dynamo.userRolesStorageTable.grantReadData(uploadAssetWorkflowFunction);
    storageResources.dynamo.tagTypeStorageTable.grantReadData(uploadAssetWorkflowFunction);
    storageResources.dynamo.tagStorageTable.grantReadData(uploadAssetWorkflowFunction);
    kmsKeyLambdaPermissionAddToResourcePolicy(uploadAssetWorkflowFunction, kmsKey);
    globalLambdaEnvironmentsAndPermissions(uploadAssetWorkflowFunction, config);

    suppressCdkNagErrorsByGrantReadWrite(scope);
    return uploadAssetWorkflowFunction;
}

export function buildIngestAssetFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    uploadAssetLambdaFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "ingestAsset";
    const ingestAssetService = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.assets.${name}.lambda_handler`,
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
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            S3_ASSET_STORAGE_BUCKET: storageResources.s3.assetBucket.bucketName,
            UPLOAD_LAMBDA_FUNCTION_NAME: uploadAssetLambdaFunction.functionName,
            METADATA_STORAGE_TABLE_NAME: storageResources.dynamo.metadataStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            CRED_TOKEN_TIMEOUT_SECONDS: config.app.authProvider.credTokenTimeoutSeconds.toString(),
        },
    });

    storageResources.dynamo.assetStorageTable.grantReadWriteData(ingestAssetService);
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(ingestAssetService);
    storageResources.dynamo.metadataStorageTable.grantReadWriteData(ingestAssetService);
    storageResources.dynamo.authEntitiesStorageTable.grantReadData(ingestAssetService);
    storageResources.dynamo.userRolesStorageTable.grantReadData(ingestAssetService);
    storageResources.s3.assetBucket.grantReadWrite(ingestAssetService);
    uploadAssetLambdaFunction.grantInvoke(ingestAssetService);
    kmsKeyLambdaPermissionAddToResourcePolicy(ingestAssetService, kmsKey);
    globalLambdaEnvironmentsAndPermissions(ingestAssetService, config);

    suppressCdkNagErrorsByGrantReadWrite(scope);

    return ingestAssetService;
}
