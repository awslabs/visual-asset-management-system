/*
 * Miris Spatial Streaming add-on Lambda function builders.
 *
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../../config/config";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../config/config";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import * as s3AssetBuckets from "../../../../helper/s3AssetBuckets";
import {
    MIRIS_DEPENDENCY_MANIFEST_PREFIX,
    MIRIS_DEPENDENCY_MANIFEST_PREFIX_OBJECT_REGEX,
} from "../upload/lambdaBuilder/mirisUploadFunctions";
import {
    globalLambdaEnvironmentsAndPermissions,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    suppressCdkNagDynamoStreamListWildcard,
    suppressCdkNagErrorsByGrantReadWrite,
    suppressCdkNagLambda,
} from "../../../../helper/security";

const BACKEND_CODE_PATH = path.join(__dirname, "../../../../../../backend/backend");

/** Read access to exactly the configured Miris Integration Key secret. */
function mirisIntegrationKeyReadStatement(config: Config.Config): iam.PolicyStatement {
    return new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["secretsmanager:GetSecretValue"],
        resources: [config.app.addons.useMirisStreaming.apiKeySecretArn],
    });
}

/** CloudWatch namespace of the `MirisAuthFailures` metric the add-on's Lambdas publish. */
export function mirisMetricsNamespace(config: Config.Config): string {
    return `VAMS/${config.name}-${config.app.baseStackName}/Miris`;
}

/** The Miris Integration API settings every add-on Lambda reads. */
function mirisIntegrationEnvironment(config: Config.Config): Record<string, string> {
    const miris = config.app.addons.useMirisStreaming;
    return {
        MIRIS_API_KEY_SECRET_ARN: miris.apiKeySecretArn,
        MIRIS_API_BASE_URL: miris.mirisApiBaseUrl,
        MIRIS_METRICS_NAMESPACE: mirisMetricsNamespace(config),
    };
}

function lambdaVpcProps(
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): Pick<lambda.FunctionProps, "vpc" | "vpcSubnets"> {
    const inVpc = config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas;
    return {
        vpc: inVpc ? vpc : undefined,
        vpcSubnets: inVpc ? { subnets: subnets } : undefined,
    };
}

/** Backs GET /addon/miris/asset-status: the Miris stream status of one asset file. */
export function buildGetMirisAssetStatusFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "getAssetStatus";
    const fun = new lambda.Function(scope, "MirisGetAssetStatusFunction", {
        code: lambda.Code.fromAsset(BACKEND_CODE_PATH),
        handler: `handlers.addon.miris.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.seconds(30),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...lambdaVpcProps(config, vpc, subnets),
        environment: mirisIntegrationEnvironment(config),
    });

    // Read-only: the asset record for Tier-2 authorization, and the Miris stream keys
    // stored as file metadata on the viewed file.
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);

    // The Miris Integration Key, which authorizes the asset state and tag reads.
    fun.addToRolePolicy(mirisIntegrationKeyReadStatement(config));

    // Staleness: the asset's bucket from the buckets table, one listing of the asset's root
    // prefix, and the dependency manifest the upload pipeline wrote to the auxiliary bucket.
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    const assetBucketArns = s3AssetBuckets
        .getS3AssetBucketRecords()
        .map((record) => record.bucket.bucketArn);
    if (assetBucketArns.length > 0) {
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["s3:ListBucket"],
                resources: assetBucketArns,
            })
        );
    }
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["s3:GetObject"],
            resources: [
                `${storageResources.s3.assetAuxiliaryBucket.bucketArn}/${MIRIS_DEPENDENCY_MANIFEST_PREFIX}/*`,
            ],
        })
    );
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Dependency manifests are keyed per asset and content hash, known only at run time, under " +
                    "the fixed pipelines/miris-upload/manifests/ prefix of this deployment's auxiliary bucket; " +
                    "the status function only reads them.",
                appliesTo: [{ regex: MIRIS_DEPENDENCY_MANIFEST_PREFIX_OBJECT_REGEX }],
            },
        ],
        true
    );

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    return fun;
}

/**
 * Backs POST /addon/miris/viewer-key: a viewer key minted for one asset file, whose key ID is
 * recorded in `viewerKeyTable`.
 */
export function buildMintMirisViewerKeyFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    viewerKeyTable: dynamodb.ITable
): lambda.Function {
    const name = "mintViewerKey";
    const fun = new lambda.Function(scope, "MirisMintViewerKeyFunction", {
        code: lambda.Code.fromAsset(BACKEND_CODE_PATH),
        handler: `handlers.addon.miris.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.seconds(30),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...lambdaVpcProps(config, vpc, subnets),
        environment: {
            ...mirisIntegrationEnvironment(config),
            MIRIS_VIEWER_KEY_TTL_MINUTES: String(
                config.app.addons.useMirisStreaming.viewerKeyTtlMinutes
            ),
            ...(config.app.compliance.quarantineBlocksDownload && {
                COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD: "true",
            }),
        },
    });

    // The Miris Integration Key, which authorizes the asset tag read, the viewer key creates and the
    // delete of a key whose table row could not be written.
    fun.addToRolePolicy(mirisIntegrationKeyReadStatement(config));

    // Read-only: the asset record for Tier-2 authorization, and the Miris stream keys
    // stored as file metadata on the file.
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileMetadataStorageTable.grantReadData(fun);
    // The asset's compliance state, read by the quarantine block before a key is returned.
    if (config.app.compliance.quarantineBlocksDownload) {
        storageResources.dynamo.complianceAssetStateStorageTable.grantReadData(fun);
    }

    // Put-only: one row per minted key. The table has no index.
    viewerKeyTable.grant(fun, "dynamodb:PutItem");

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    return fun;
}

/**
 * Deletes a per-view Miris viewer key from the Miris workspace when DynamoDB TTL removes its row
 * from `viewerKeyTable`. The caller attaches the table's stream with an event source mapping.
 */
export function buildMirisViewerKeyCleanupFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    viewerKeyTable: dynamodb.ITable
): lambda.Function {
    const name = "viewerKeyCleanup";
    const fun = new lambda.Function(scope, "MirisViewerKeyCleanupFunction", {
        code: lambda.Code.fromAsset(BACKEND_CODE_PATH),
        handler: `handlers.addon.miris.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...lambdaVpcProps(config, vpc, subnets),
        environment: mirisIntegrationEnvironment(config),
    });

    // The Miris Integration Key, which authorizes the viewer key deletes.
    fun.addToRolePolicy(mirisIntegrationKeyReadStatement(config));

    // The table's stream, which carries the key IDs of TTL-deleted rows.
    viewerKeyTable.grantStreamRead(fun);

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagDynamoStreamListWildcard(fun);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);

    return fun;
}
