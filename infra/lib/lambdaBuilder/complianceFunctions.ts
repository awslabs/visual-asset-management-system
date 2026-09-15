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

export function buildComplianceSchemaServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "complianceSchemaService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
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
            COMPLIANCE_SCHEMA_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceSchemaStorageTable.tableName,
            COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAssetStateStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
        },
    });
    storageResources.dynamo.complianceSchemaStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildComplianceEvaluateServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    executeWorkflowFunction: lambda.Function
): lambda.Function {
    const name = "complianceEvaluateService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
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
            COMPLIANCE_SCHEMA_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceSchemaStorageTable.tableName,
            COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAssetStateStorageTable.tableName,
            COMPLIANCE_EVALUATION_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceEvaluationStorageTable.tableName,
            COMPLIANCE_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAuditStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            ASSET_LINKS_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.assetLinksStorageTableV2.tableName,
            ASSET_FILE_METADATA_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetFileMetadataStorageTable.tableName,
            METADATA_SCHEMA_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.metadataSchemaStorageTableV2.tableName,
            PIPELINE_STORAGE_TABLE_NAME: storageResources.dynamo.pipelineStorageTable.tableName,
            WORKFLOW_STORAGE_TABLE_NAME: storageResources.dynamo.workflowStorageTable.tableName,
            COMPLIANCE_CASCADE_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceCascadeStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            S3_ASSETAUXILIARY_STORAGE_BUCKET: storageResources.s3.assetAuxiliaryBucket.bucketName,
            EXECUTE_WORKFLOW_FUNCTION_NAME: executeWorkflowFunction.functionName,
        },
    });
    storageResources.dynamo.complianceSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceEvaluationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.metadataSchemaStorageTableV2.grantReadData(fun);
    storageResources.dynamo.pipelineStorageTable.grantReadData(fun);
    storageResources.dynamo.workflowStorageTable.grantReadData(fun);
    storageResources.dynamo.complianceCascadeStorageTable.grantReadWriteData(fun);
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
                resources: [record.bucket.bucketArn, `${record.bucket.bucketArn}/${objectPrefix}*`],
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

export function buildComplianceQuarantineServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "complianceQuarantineService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
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
            COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAssetStateStorageTable.tableName,
            COMPLIANCE_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAuditStorageTable.tableName,
            ASSET_LINKS_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.assetLinksStorageTableV2.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
        },
    });
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetLinksStorageTableV2.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildComplianceCascadeServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    executeWorkflowFunction: lambda.Function
): lambda.Function {
    const name = "complianceCascadeService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
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
            COMPLIANCE_CASCADE_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceCascadeStorageTable.tableName,
            COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAssetStateStorageTable.tableName,
            COMPLIANCE_EVALUATION_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceEvaluationStorageTable.tableName,
            COMPLIANCE_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAuditStorageTable.tableName,
            COMPLIANCE_SCHEMA_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceSchemaStorageTable.tableName,
            ASSET_LINKS_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.assetLinksStorageTableV2.tableName,
            ASSET_FILE_METADATA_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetFileMetadataStorageTable.tableName,
            METADATA_SCHEMA_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.metadataSchemaStorageTableV2.tableName,
            WORKFLOW_STORAGE_TABLE_NAME: storageResources.dynamo.workflowStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            S3_ASSETAUXILIARY_STORAGE_BUCKET: storageResources.s3.assetAuxiliaryBucket.bucketName,
            EXECUTE_WORKFLOW_FUNCTION_NAME: executeWorkflowFunction.functionName,
        },
    });
    storageResources.dynamo.complianceCascadeStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceEvaluationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceSchemaStorageTable.grantReadData(fun);
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

export function buildComplianceAuditServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "complianceAuditService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
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
            COMPLIANCE_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAuditStorageTable.tableName,
        },
    });
    storageResources.dynamo.complianceAuditStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildComplianceTrigger(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    executeWorkflowFunction: lambda.Function
): lambda.Function {
    const name = "complianceTrigger";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
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
            COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAssetStateStorageTable.tableName,
            COMPLIANCE_EVALUATION_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceEvaluationStorageTable.tableName,
            COMPLIANCE_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAuditStorageTable.tableName,
            COMPLIANCE_SCHEMA_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceSchemaStorageTable.tableName,
            COMPLIANCE_CASCADE_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceCascadeStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            ASSET_FILE_METADATA_STORAGE_TABLE_NAME:
                storageResources.dynamo.assetFileMetadataStorageTable.tableName,
            METADATA_SCHEMA_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.metadataSchemaStorageTableV2.tableName,
            ASSET_LINKS_STORAGE_TABLE_V2_NAME:
                storageResources.dynamo.assetLinksStorageTableV2.tableName,
            WORKFLOW_STORAGE_TABLE_NAME: storageResources.dynamo.workflowStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            S3_ASSET_BUCKETS_STORAGE_TABLE_NAME:
                storageResources.dynamo.s3AssetBucketsStorageTable.tableName,
            S3_ASSETAUXILIARY_STORAGE_BUCKET: storageResources.s3.assetAuxiliaryBucket.bucketName,
            EXECUTE_WORKFLOW_FUNCTION_NAME: executeWorkflowFunction.functionName,
        },
    });
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceEvaluationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.complianceCascadeStorageTable.grantReadWriteData(fun);
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

export function buildComplianceSchemaBindingServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "complianceSchemaBindingService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
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
            COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAssetStateStorageTable.tableName,
            COMPLIANCE_SCHEMA_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceSchemaStorageTable.tableName,
            COMPLIANCE_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAuditStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
        },
    });
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceSchemaStorageTable.grantReadData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}

export function buildComplianceWorkflowCallback(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "complianceWorkflowCallback";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.compliance.${name}.lambda_handler`,
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
            COMPLIANCE_EVALUATION_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceEvaluationStorageTable.tableName,
            COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAssetStateStorageTable.tableName,
            COMPLIANCE_AUDIT_STORAGE_TABLE_NAME:
                storageResources.dynamo.complianceAuditStorageTable.tableName,
            S3_ASSET_STORAGE_BUCKET: storageResources.s3.assetAuxiliaryBucket.bucketName,
        },
    });
    storageResources.dynamo.complianceEvaluationStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAssetStateStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.complianceAuditStorageTable.grantReadWriteData(fun);
    storageResources.s3.assetAuxiliaryBucket.grantRead(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    return fun;
}
