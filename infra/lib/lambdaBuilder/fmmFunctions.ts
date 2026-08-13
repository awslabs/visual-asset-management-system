/*
 * Copyright 2024 Balfour Beatty. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    suppressCdkNagLambda,
    suppressCdkNagErrorsByGrantReadWrite,
    grantExternalAssetBucketKmsKeys,
} from "../helper/security";
import * as s3AssetBuckets from "../helper/s3AssetBuckets";

export function buildFMMSchemaService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "fmmSchemaService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.fmm.${name}.lambda_handler`,
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
            FMM_SCHEMA_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmSchemaStorageTable.tableName,
            FMM_ASSET_COMPLIANCE_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAssetComplianceStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
        },
    });
    storageResources.dynamo.fmmSchemaStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmAssetComplianceStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildFMMEvaluateService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    executeWorkflowFunction: lambda.Function
): lambda.Function {
    const name = "fmmEvaluateService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.fmm.${name}.lambda_handler`,
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
            FMM_SCHEMA_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmSchemaStorageTable.tableName,
            FMM_ASSET_COMPLIANCE_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAssetComplianceStorageTable.tableName,
            FMM_EVALUATION_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmEvaluationStorageTable.tableName,
            FMM_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAuditStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            ASSET_LINKS_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.assetLinksStorageTableV2.tableName,
            ASSET_FILE_METADATA_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetFileMetadataStorageTable.tableName,
            METADATA_SCHEMA_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.metadataSchemaStorageTableV2.tableName,
            PIPELINE_STORAGE_TABLE_NAME:
                storageResources.dynamo.pipelineStorageTable.tableName,
            WORKFLOW_STORAGE_TABLE_NAME:
                storageResources.dynamo.workflowStorageTable.tableName,
            FMM_CASCADE_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmCascadeStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME:
                storageResources.dynamo.databaseStorageTable.tableName,
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            S3_ASSETAUXILIARY_STORAGE_BUCKET:
                storageResources.s3.assetAuxiliaryBucket.bucketName,
            EXECUTE_WORKFLOW_FUNCTION_NAME: executeWorkflowFunction.functionName,
        },
    });
    storageResources.dynamo.fmmSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.fmmAssetComplianceStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmEvaluationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.metadataSchemaStorageTableV2.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTable.grantReadData(fun);
    storageResources.dynamo.workflowStorageTable.grantReadData(fun);
    storageResources.dynamo.fmmCascadeStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    executeWorkflowFunction.grantInvoke(fun);
    for (const record of s3AssetBuckets.getS3AssetBucketRecords()) {
        const prefix = record.prefix || "/";
        const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";
        const objectPrefix = normalizedPrefix.replace(/^\/+/, "");
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["s3:ListBucket", "s3:GetObject"],
                resources: [
                    record.bucket.bucketArn,
                    `${record.bucket.bucketArn}/${objectPrefix}*`,
                ],
            })
        );
    }
    grantExternalAssetBucketKmsKeys(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["sns:Publish"],
            resources: ["*"],
        })
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildFMMQuarantineService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "fmmQuarantineService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.fmm.${name}.lambda_handler`,
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
            FMM_ASSET_COMPLIANCE_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAssetComplianceStorageTable.tableName,
            FMM_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAuditStorageTable.tableName,
            ASSET_LINKS_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.assetLinksStorageTableV2.tableName,
            ASSET_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetStorageTable.tableName,
        },
    });
    storageResources.dynamo.fmmAssetComplianceStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildFMMCascadeService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    executeWorkflowFunction: lambda.Function
): lambda.Function {
    const name = "fmmCascadeService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.fmm.${name}.lambda_handler`,
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
            FMM_CASCADE_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmCascadeStorageTable.tableName,
            FMM_ASSET_COMPLIANCE_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAssetComplianceStorageTable.tableName,
            FMM_EVALUATION_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmEvaluationStorageTable.tableName,
            FMM_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAuditStorageTable.tableName,
            FMM_SCHEMA_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmSchemaStorageTable.tableName,
            ASSET_LINKS_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.assetLinksStorageTableV2.tableName,
            ASSET_FILE_METADATA_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetFileMetadataStorageTable.tableName,
            METADATA_SCHEMA_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.metadataSchemaStorageTableV2.tableName,
            WORKFLOW_STORAGE_TABLE_NAME:
                storageResources.dynamo.workflowStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetStorageTable.tableName,
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            S3_ASSETAUXILIARY_STORAGE_BUCKET:
                storageResources.s3.assetAuxiliaryBucket.bucketName,
            EXECUTE_WORKFLOW_FUNCTION_NAME: executeWorkflowFunction.functionName,
        },
    });
    storageResources.dynamo.fmmCascadeStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmAssetComplianceStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmEvaluationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.metadataSchemaStorageTableV2.grantReadData(fun);
    storageResources.dynamo.workflowStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    executeWorkflowFunction.grantInvoke(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["sns:Publish"],
            resources: ["*"],
        })
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildFMMAuditService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "fmmAuditService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.fmm.${name}.lambda_handler`,
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
            FMM_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAuditStorageTable.tableName,
        },
    });
    storageResources.dynamo.fmmAuditStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildFMMComplianceTrigger(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    executeWorkflowFunction: lambda.Function
): lambda.Function {
    const name = "fmmComplianceTrigger";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.fmm.${name}.lambda_handler`,
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
            FMM_ASSET_COMPLIANCE_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAssetComplianceStorageTable.tableName,
            FMM_EVALUATION_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmEvaluationStorageTable.tableName,
            FMM_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAuditStorageTable.tableName,
            FMM_SCHEMA_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmSchemaStorageTable.tableName,
            FMM_CASCADE_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmCascadeStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME:
                storageResources.dynamo.databaseStorageTable.tableName,
            ASSET_FILE_METADATA_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetFileMetadataStorageTable.tableName,
            METADATA_SCHEMA_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.metadataSchemaStorageTableV2.tableName,
            ASSET_LINKS_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.assetLinksStorageTableV2.tableName,
            WORKFLOW_STORAGE_TABLE_NAME:
                storageResources.dynamo.workflowStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetStorageTable.tableName,
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            S3_ASSETAUXILIARY_STORAGE_BUCKET:
                storageResources.s3.assetAuxiliaryBucket.bucketName,
            EXECUTE_WORKFLOW_FUNCTION_NAME: executeWorkflowFunction.functionName,
        },
    });
    storageResources.dynamo.fmmAssetComplianceStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmEvaluationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.fmmCascadeStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.metadataSchemaStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.workflowStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    executeWorkflowFunction.grantInvoke(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["sns:Publish"],
            resources: ["*"],
        })
    );
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildFMMSchemaBindingService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "fmmSchemaBindingService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.fmm.${name}.lambda_handler`,
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
            FMM_ASSET_COMPLIANCE_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAssetComplianceStorageTable.tableName,
            FMM_SCHEMA_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmSchemaStorageTable.tableName,
            FMM_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAuditStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME:
                storageResources.dynamo.databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetStorageTable.tableName,
        },
    });
    storageResources.dynamo.fmmAssetComplianceStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.fmmAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildFMMPipelineCallback(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "fmmPipelineCallback";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.fmm.${name}.lambda_handler`,
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
            FMM_EVALUATION_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmEvaluationStorageTable.tableName,
            FMM_ASSET_COMPLIANCE_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAssetComplianceStorageTable.tableName,
            FMM_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.fmmAuditStorageTable.tableName,
            S3_ASSET_STORAGE_BUCKET:
                storageResources.s3.assetAuxiliaryBucket.bucketName,
        },
    });
    storageResources.dynamo.fmmEvaluationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmAssetComplianceStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.fmmAuditStorageTable.grantReadWriteData(fun);
    storageResources.s3.assetAuxiliaryBucket.grantRead(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
