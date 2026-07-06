/*
 * Physna Sync add-on Lambda function builders.
 *
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import {
    suppressCdkNagErrorsByGrantReadWrite,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    grantReadPermissionsToAllAssetBuckets,
} from "../../../../helper/security";
import { suppressCdkNagLambda } from "../../../../helper/security";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../config/config";
import * as Config from "../../../../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";

interface BuildPhysnaLambdaProps {
    scope: Construct;
    name: string;
    lambdaCommonBaseLayer: LayerVersion;
    storageResources: storageResources;
    config: Config.Config;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    credsSecret: secretsmanager.ISecret;
}

function buildCommonPhysnaLambda(props: BuildPhysnaLambdaProps): lambda.Function {
    const {
        scope,
        name,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets,
        credsSecret,
    } = props;

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../../../../backend/backend`)),
        handler: `handlers.addon.physna.${name}.lambda_handler`,
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
            // Physna configuration
            PHYSNA_TENANT_ID: config.app.addons.usePhysnaSync.tenantId,
            PHYSNA_API_BASE: config.app.addons.usePhysnaSync.apiBaseEndpoint,
            PHYSNA_TOKEN_URL: config.app.addons.usePhysnaSync.authTokenEndpoint,
            PHYSNA_AUTH_TYPE: config.app.addons.usePhysnaSync.authType,
            PHYSNA_CREDS_SECRET_ARN: credsSecret.secretArn,
        },
    });

    // Grant DynamoDB read permissions on the tables the lambda reads
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
    storageResources.dynamo.fileAttributeStorageTable.grantReadData(fun);
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.syncTrackingOutboundStorageTable.grantReadWriteData(fun);

    // S3 read access to all asset buckets for file downloads
    grantReadPermissionsToAllAssetBuckets(fun);

    // Secrets Manager read on the Physna credentials secret
    credsSecret.grantRead(fun);

    // Required security calls
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    suppressCdkNagLambda(fun);
    return fun;
}

export function buildPhysnaFileSyncFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    credsSecret: secretsmanager.ISecret
): lambda.Function {
    return buildCommonPhysnaLambda({
        scope,
        name: "physnaFileSync",
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets,
        credsSecret,
    });
}

export function buildPhysnaAssetSyncFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    credsSecret: secretsmanager.ISecret
): lambda.Function {
    return buildCommonPhysnaLambda({
        scope,
        name: "physnaAssetSync",
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets,
        credsSecret,
    });
}

export function buildPhysnaViewerFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    credsSecret: secretsmanager.ISecret
): lambda.Function {
    const name = "physnaViewer";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../../../../backend/backend`)),
        handler: `handlers.addon.physna.${name}.lambda_handler`,
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
            // Physna configuration
            PHYSNA_TENANT_ID: config.app.addons.usePhysnaSync.tenantId,
            PHYSNA_API_BASE: config.app.addons.usePhysnaSync.apiBaseEndpoint,
            PHYSNA_TOKEN_URL: config.app.addons.usePhysnaSync.authTokenEndpoint,
            PHYSNA_AUTH_TYPE: config.app.addons.usePhysnaSync.authType,
            PHYSNA_CREDS_SECRET_ARN: credsSecret.secretArn,
        },
    });

    // The minimum set of DynamoDB read grants needed for two-tier auth plus
    // the asset record used for object-level enforcement. The auth-related
    // tables (authEntities, constraints, userRoles, roles) are handled by
    // setupSecurityAndLoggingEnvironmentAndPermissions below.
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);

    // Secrets Manager read on the Physna credentials secret.
    credsSecret.grantRead(fun);

    // Required security calls (all 4).
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    suppressCdkNagLambda(fun);
    return fun;
}
