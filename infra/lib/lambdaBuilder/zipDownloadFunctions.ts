/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as Config from "../../config/config";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import {
    PREPARED_DOWNLOADS_PREFIX,
    ZIP_DOWNLOAD_USER_ASSET_INDEX,
    storageResources,
} from "../nestedStacks/storage/storageBuilder-nestedStack";
import {
    globalLambdaEnvironmentsAndPermissions,
    grantReadPermissionsToAllAssetBuckets,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    suppressCdkNagErrorsByGrantReadWrite,
    suppressCdkNagLambda,
} from "../helper/security";

/**
 * Download as Zip (app.zipDownloads): the zip download service behind the six
 * `/download/zip` routes and the terminal callback of the zipArchive system pipeline.
 *
 * Both functions deploy whatever `app.zipDownloads.enabled` says, so a deployment that turns the
 * feature off keeps its job table readable and its routes answering (404 "not enabled"). With the
 * feature on, `grantZipDownloadLaunch` adds the two cross-calls the service makes (launch and abort)
 * and apiBuilder2 registers the callback under the `Zip-Download` system trigger value.
 */

/** The zipArchive system workflow every zip download job runs (database GLOBAL). */
export const ZIP_ARCHIVE_WORKFLOW_DATABASE_ID = "GLOBAL";
export const ZIP_ARCHIVE_WORKFLOW_ID = "utility-zip-archive";

/** Lifetime of the presigned URL the zip download service returns for a finished archive. */
export const ZIP_DOWNLOAD_PRESIGNED_URL_TIMEOUT_SECONDS = 900;

/**
 * The six routes the zip download service serves (backend common/apiRoutes.py ZIP_DOWNLOAD_ROUTES;
 * test/api/apiRouteBackendCdkParity.test.ts holds the two lists equal).
 */
export const ZIP_DOWNLOAD_ROUTES: ReadonlyArray<{
    routePath: string;
    method: apigateway.HttpMethod;
}> = [
    {
        routePath: "/database/{databaseId}/assets/{assetId}/download/zip",
        method: apigateway.HttpMethod.POST,
    },
    {
        routePath: "/database/{databaseId}/assets/{assetId}/download/zip",
        method: apigateway.HttpMethod.GET,
    },
    { routePath: "/download/zip", method: apigateway.HttpMethod.GET },
    {
        routePath: "/database/{databaseId}/assets/{assetId}/download/zip/{requestId}",
        method: apigateway.HttpMethod.GET,
    },
    {
        routePath: "/database/{databaseId}/assets/{assetId}/download/zip/{requestId}",
        method: apigateway.HttpMethod.DELETE,
    },
    {
        routePath: "/database/{databaseId}/assets/{assetId}/download/zip/{requestId}/url",
        method: apigateway.HttpMethod.POST,
    },
];

/** Stored system trigger value of a zip download run (systemPipelines.py SYSTEM_TRIGGER_TYPE_TO_STORED). */
export const ZIP_DOWNLOAD_SYSTEM_TRIGGER = "Zip-Download";

/** The zip download service's environment from app.zipDownloads (names read by zipDownloadService.py). */
export function zipDownloadServiceEnvironment(config: Config.Config): Record<string, string> {
    const zip = config.app.zipDownloads;
    return {
        ZIPDOWNLOADS_ENABLED: zip.enabled ? "true" : "false",
        ZIP_DOWNLOAD_RETENTION_DAYS: zip.retentionDays.toString(),
        ZIP_DOWNLOAD_MAX_FILES_PER_REQUEST: zip.maxFilesPerRequest.toString(),
        ZIP_DOWNLOAD_MAX_TOTAL_BYTES_PER_REQUEST: zip.maxTotalBytesPerRequest.toString(),
        ZIP_DOWNLOAD_MAX_ACTIVE_PER_USER: zip.maxActiveRequestsPerUser.toString(),
        ZIP_ARCHIVE_WORKFLOW_DATABASE_ID: ZIP_ARCHIVE_WORKFLOW_DATABASE_ID,
        ZIP_ARCHIVE_WORKFLOW_ID: ZIP_ARCHIVE_WORKFLOW_ID,
        // 15 minutes, or the deployment's presigned URL lifetime when that is shorter.
        PRESIGNED_URL_TIMEOUT_SECONDS: Math.min(
            ZIP_DOWNLOAD_PRESIGNED_URL_TIMEOUT_SECONDS,
            config.app.authProvider.presignedUrlTimeoutSeconds
        ).toString(),
    };
}

/** `<auxiliary bucket ARN>/vams.preparedDownloads/*`: every object a zip download job owns. */
function preparedDownloadsObjectArn(storageResources: storageResources): string {
    return `${storageResources.s3.assetAuxiliaryBucket.bucketArn}/${PREPARED_DOWNLOADS_PREFIX}*`;
}

function inVpc(config: Config.Config): boolean {
    return config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas;
}

/**
 * The zip download service: create, list (asset and global), get, cancel/delete and presigned URL,
 * one function for all six routes. Its own job table and its auxiliary-bucket prefix are granted as
 * exact statements (the actions the handler calls), the asset data read the way downloadAsset reads
 * it, and the execution row read by GetItem only (read-time reconcile).
 */
export function buildZipDownloadServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "zipDownloadService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.zipDownloads.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc: inVpc(config) ? vpc : undefined,
        vpcSubnets: inVpc(config) ? { subnets: subnets } : undefined,
        environment: zipDownloadServiceEnvironment(config),
    });

    const zipTable = storageResources.dynamo.zipDownloadRequestsStorageTable;
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:UpdateItem",
                "dynamodb:DeleteItem",
                "dynamodb:Query",
            ],
            resources: [
                zipTable.tableArn,
                `${zipTable.tableArn}/index/${ZIP_DOWNLOAD_USER_ASSET_INDEX}`,
            ],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:GetItem"],
            resources: [storageResources.dynamo.workflowExecutionsStorageTableV2.tableArn],
        })
    );
    // The asset, its bucket record, and (for an asset-version pin) its version snapshot, as the
    // download function reads them.
    storageResources.dynamo.s3AssetBucketsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.assetVersionsStorageTable.grantReadData(fun);
    storageResources.dynamo.assetFileVersionsStorageTable.grantReadData(fun);

    // The job's own objects: manifest write, archive head and presign, cancel/delete cleanup.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "s3:PutObject",
                "s3:GetObject",
                "s3:DeleteObject",
                "s3:DeleteObjectVersion",
                "s3:AbortMultipartUpload",
                // ListParts confirms an aborted upload is gone (#1125 D16, abort_until_gone).
                "s3:ListMultipartUploadParts",
            ],
            resources: [preparedDownloadsObjectArn(storageResources)],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["s3:ListBucket", "s3:ListBucketVersions"],
            resources: [storageResources.s3.assetAuxiliaryBucket.bucketArn],
            conditions: { StringLike: { "s3:prefix": [`${PREPARED_DOWNLOADS_PREFIX}*`] } },
        })
    );
    // ListMultipartUploads takes no s3:prefix condition key; the handler confines every listing to
    // one job's prefix.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["s3:ListBucketMultipartUploads"],
            resources: [storageResources.s3.assetAuxiliaryBucket.bucketArn],
        })
    );
    // Selection checks (HeadObject, ListObjectVersions) on the asset buckets.
    grantReadPermissionsToAllAssetBuckets(fun);

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    // Scoped to this function (not the stack): applied to the stack, it would restamp the suppression
    // metadata on every construct the stack already holds, changing their templates.
    suppressCdkNagErrorsByGrantReadWrite(fun);

    return fun;
}

/**
 * The zipArchive system pipeline's terminal callback (handlers/zipDownloads/zipDownloadCallback.py):
 * finalizes a job from its run's terminal status and aborts a failed run's open multipart upload.
 * No API route. Registered in the system pipeline callback registry only when app.zipDownloads is on.
 */
export function buildZipDownloadCallbackFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "zipDownloadCallback";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.zipDownloads.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc: inVpc(config) ? vpc : undefined,
        vpcSubnets: inVpc(config) ? { subnets: subnets } : undefined,
        environment: {},
    });

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["dynamodb:GetItem", "dynamodb:UpdateItem"],
            resources: [storageResources.dynamo.zipDownloadRequestsStorageTable.tableArn],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            // Delete* removes an ended job's working objects (request and resolved manifests,
            // common/zipDownloads.delete_working_objects); the handler confines it to those two keys.
            actions: [
                "s3:GetObject",
                "s3:AbortMultipartUpload",
                "s3:DeleteObject",
                "s3:DeleteObjectVersion",
                // ListParts confirms an aborted upload is gone (#1125 D16, abort_until_gone).
                "s3:ListMultipartUploadParts",
            ],
            resources: [preparedDownloadsObjectArn(storageResources)],
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["s3:ListBucketVersions"],
            resources: [storageResources.s3.assetAuxiliaryBucket.bucketArn],
            conditions: { StringLike: { "s3:prefix": [`${PREPARED_DOWNLOADS_PREFIX}*`] } },
        })
    );
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["s3:ListBucketMultipartUploads"],
            resources: [storageResources.s3.assetAuxiliaryBucket.bucketArn],
        })
    );

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);

    return fun;
}

/**
 * With app.zipDownloads on: the service launches the zipArchive workflow through the execute function
 * and aborts a cancelled job's run through the execution service, both as SYSTEM_USER cross-calls.
 * The same execution service grant serves the run check of an idle job (a details read that records
 * a run ended outside VAMS whose status change event was lost; zipDownloadService._check_run_ended).
 * Each grant names the exact unqualified function ARN (no `:*`), like the re-run grant on the
 * execution service. Not called with the feature off, so neither name nor grant is deployed then.
 */
export function grantZipDownloadLaunch(
    zipDownloadService: lambda.Function,
    executeWorkflowV2: lambda.IFunction,
    executionService: lambda.IFunction
): void {
    zipDownloadService.addEnvironment(
        "EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME",
        executeWorkflowV2.functionName
    );
    zipDownloadService.addEnvironment(
        "EXECUTION_SERVICE_FUNCTION_NAME",
        executionService.functionName
    );
    zipDownloadService.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["lambda:InvokeFunction"],
            resources: [executeWorkflowV2.functionArn, executionService.functionArn],
        })
    );
}
