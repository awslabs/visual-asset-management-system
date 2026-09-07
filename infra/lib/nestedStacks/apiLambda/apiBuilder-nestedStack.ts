/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { Names } from "aws-cdk-lib";
import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { SqsSubscription } from "aws-cdk-lib/aws-sns-subscriptions";

import * as lambda from "aws-cdk-lib/aws-lambda";
import { storageResources } from "../storage/storageBuilder-nestedStack";
import { buildConfigService } from "../../lambdaBuilder/configFunctions";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import {
    buildCreateDatabaseLambdaFunction,
    buildDatabaseService,
} from "../../lambdaBuilder/databaseFunctions";
import {
    buildAssetService,
    buildStreamAuxiliaryPreviewAssetFunction,
    buildStreamAssetFunction,
    buildDownloadAssetFunction,
    buildAssetFiles,
    buildIngestAssetFunction,
    buildCreateAssetFunction,
    buildUploadFileFunction,
    buildAssetVersionsFunction,
    buildSqsUploadFileLargeFunction,
    buildAssetExportService,
} from "../../lambdaBuilder/assetFunctions";
import {
    buildAddCommentLambdaFunction,
    buildEditCommentLambdaFunction,
    buildCommentService,
} from "../../lambdaBuilder/commentFunctions";
import { NestedStack } from "aws-cdk-lib";

import { buildMetadataSchemaService } from "../../lambdaBuilder/metadataSchemaFunctions";
import { buildMetadataService } from "../../lambdaBuilder/metadataFunctions";
import { buildAuthFunctions } from "../../lambdaBuilder/authFunctions";
import {
    buildSubscriptionService,
    buildCheckSubscriptionFunction,
    buildUnSubscribeFunction,
} from "../../lambdaBuilder/subscriptionFunctions";
import {
    buildAssetLinksService,
    buildCreateAssetLinkFunction,
} from "../../lambdaBuilder/assetsLinkFunctions";
import { buildSearchFunction } from "../../lambdaBuilder/searchIndexBucketSyncFunctions";
import { buildRoleService, buildCreateRoleFunction } from "../../lambdaBuilder/roleFunctions";
import { buildUserRolesService } from "../../lambdaBuilder/userRoleFunctions";
import { buildSendEmailFunction } from "../../lambdaBuilder/sendEmailFunctions";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { authResources } from "../auth/authBuilder-nestedStack";
import { DynamoDbMetadataSchemaDefaultsConstruct } from "./constructs/dynamodb-metadataschema-defaults-construct";
import * as iam from "aws-cdk-lib/aws-iam";
import { kmsKeyPolicyStatementGenerator } from "../../helper/security";
import { Service } from "../../../lib/helper/service-helper";
import { RouteRegistry, attachFunctionToApi } from "./apiRouteRegistry";

export class ApiBuilderNestedStack extends NestedStack {
    // Shared references consumed by the workflow/pipeline/execution functions in ApiBuilder2 (which
    // now own that whole domain): the metadata service and the upload-file lambda. Assigned in the
    // constructor; the process-output lambda invokes uploadFile, and the execute/process-output
    // lambdas invoke the metadata service.
    public metadataServiceFunction!: lambda.Function;
    public uploadFileFunction!: lambda.Function;

    constructor(
        parent: Construct,
        name: string,
        config: Config.Config,
        registry: RouteRegistry,
        storageResources: storageResources,
        authResources: authResources,
        lambdaCommonBaseLayer: LayerVersion,
        vpc: ec2.IVpc,
        subnets: ec2.ISubnet[]
    ) {
        super(parent, name);

        //config resources
        const createConfigFunction = buildConfigService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            storageResources.encryption.kmsKey
        );

        attachFunctionToApi(this, createConfigFunction, {
            routePath: "/secure-config",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        //Database Resources
        const createDatabaseFunction = buildCreateDatabaseLambdaFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, createDatabaseFunction, {
            routePath: "/database",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        const databaseService = buildDatabaseService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, databaseService, {
            routePath: "/database",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, databaseService, {
            routePath: "/database/{databaseId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, databaseService, {
            routePath: "/database/{databaseId}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });
        attachFunctionToApi(this, databaseService, {
            routePath: "/database/{databaseId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        attachFunctionToApi(this, databaseService, {
            routePath: "/buckets",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        //Email Resources
        const sendEmailFunction = buildSendEmailFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );

        //Comment Resources
        const commentService = buildCommentService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            storageResources.encryption.kmsKey
        );

        const commentServiceRoutes = [
            "/comments/assets/{assetId}",
            "/comments/assets/{assetId}/assetVersionId/{assetVersionId}",
            "/comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}",
        ];
        for (let i = 0; i < commentServiceRoutes.length; i++) {
            attachFunctionToApi(this, commentService, {
                routePath: commentServiceRoutes[i],
                method: apigateway.HttpMethod.GET,
                registry: registry,
            });
        }

        attachFunctionToApi(this, commentService, {
            routePath:
                "/comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        const addCommentFunction = buildAddCommentLambdaFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            storageResources.encryption.kmsKey
        );
        attachFunctionToApi(this, addCommentFunction, {
            routePath:
                "/comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        const editCommentFunction = buildEditCommentLambdaFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            storageResources.encryption.kmsKey
        );
        attachFunctionToApi(this, editCommentFunction, {
            routePath:
                "/comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });

        // Role Resources
        const roleService = buildRoleService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            storageResources.encryption.kmsKey
        );
        attachFunctionToApi(this, roleService, {
            routePath: "/roles",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, roleService, {
            routePath: "/roles/{roleId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        const createRoleFunction = buildCreateRoleFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            storageResources.encryption.kmsKey
        );
        attachFunctionToApi(this, createRoleFunction, {
            routePath: "/roles",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, createRoleFunction, {
            routePath: "/roles",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });

        // UserRole Resources
        const userRolesService = buildUserRolesService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            storageResources.encryption.kmsKey
        );
        attachFunctionToApi(this, userRolesService, {
            routePath: "/user-roles",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, userRolesService, {
            routePath: "/user-roles",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, userRolesService, {
            routePath: "/user-roles",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });
        attachFunctionToApi(this, userRolesService, {
            routePath: "/user-roles",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        //Subscription Resources
        const subscriptionService = buildSubscriptionService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            storageResources.encryption.kmsKey
        );
        attachFunctionToApi(this, subscriptionService, {
            routePath: "/subscriptions",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, subscriptionService, {
            routePath: "/subscriptions",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, subscriptionService, {
            routePath: "/subscriptions",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });
        attachFunctionToApi(this, subscriptionService, {
            routePath: "/subscriptions",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        const unSubscribeService = buildUnSubscribeFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            storageResources.encryption.kmsKey
        );
        attachFunctionToApi(this, unSubscribeService, {
            routePath: "/unsubscribe",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        const checkSubscriptionService = buildCheckSubscriptionFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            storageResources.encryption.kmsKey
        );
        attachFunctionToApi(this, checkSubscriptionService, {
            routePath: "/check-subscription",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        //Asset Links Resources
        // Create Asset Link (POST)
        const createAssetLinkService = buildCreateAssetLinkFunction(
            this,
            lambdaCommonBaseLayer,
            config,
            storageResources,
            vpc,
            subnets,
            storageResources.encryption.kmsKey
        );
        attachFunctionToApi(this, createAssetLinkService, {
            routePath: "/asset-links",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        // Get and Delete Asset Links (GET and DELETE)
        const assetLinksService = buildAssetLinksService(
            this,
            lambdaCommonBaseLayer,
            config,
            storageResources,
            vpc,
            subnets,
            storageResources.encryption.kmsKey
        );
        attachFunctionToApi(this, assetLinksService, {
            routePath: "/database/{databaseId}/assets/{assetId}/asset-links",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, assetLinksService, {
            routePath: "/asset-links/single/{assetLinkId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, assetLinksService, {
            routePath: "/asset-links/{assetLinkId}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });
        attachFunctionToApi(this, assetLinksService, {
            routePath: "/asset-links/{assetLinkId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        // Centralized Metadata Service - Handles all entity types
        const metadataService = buildMetadataService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        // Shared with ApiBuilder2's execute + process-output lambdas (they invoke the metadata service).
        this.metadataServiceFunction = metadataService;

        // Asset Link Metadata Routes (updated - removed metadataKey path parameter)
        attachFunctionToApi(this, metadataService, {
            routePath: "/asset-links/{assetLinkId}/metadata",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, metadataService, {
            routePath: "/asset-links/{assetLinkId}/metadata",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, metadataService, {
            routePath: "/asset-links/{assetLinkId}/metadata",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });
        attachFunctionToApi(this, metadataService, {
            routePath: "/asset-links/{assetLinkId}/metadata",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        //Asset Resources
        const assetService = buildAssetService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            sendEmailFunction,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, assetService, {
            routePath: "/database/{databaseId}/assets",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, assetService, {
            routePath: "/database/{databaseId}/assets/{assetId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, assetService, {
            routePath: "/database/{databaseId}/assets/{assetId}/archiveAsset",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        attachFunctionToApi(this, assetService, {
            routePath: "/database/{databaseId}/assets/{assetId}/deleteAsset",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });
        attachFunctionToApi(this, assetService, {
            routePath: "/database/{databaseId}/assets/{assetId}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });
        attachFunctionToApi(this, assetService, {
            routePath: "/database/{databaseId}/assets/{assetId}/unarchiveAsset",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });
        attachFunctionToApi(this, assetService, {
            routePath: "/assets",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        const assetFilesFunction = buildAssetFiles(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            sendEmailFunction,
            config,
            vpc,
            subnets
        );

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/listFiles",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        // Add new file operation routes
        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/fileInfo",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/moveFile",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/copyFile",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/archiveFile",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/deleteAssetPreview",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/deleteAuxiliaryPreviewAssetFiles",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/deleteFile",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/revertFileVersion/{versionId}",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/unarchiveFile",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/setPrimaryFile",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/createFolder",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        const createAssetFunction = buildCreateAssetFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, createAssetFunction, {
            routePath: "/assets",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        // Dead-letter queue for the large-file processing queue.
        //
        // Each message here is one large upload a user is waiting on. Without a DLQ a message the
        // event source cannot land is redelivered every 15 minutes for its full 5-day retention and
        // then discarded, with no record of which upload was lost: the same silent cycle measured on
        // the bucket-sync queues, where 15 records circulated for 14 hours with Errors and Throttles
        // both zero because AWS Lambda's recursive-loop detection was dropping the invocations before
        // the handler ran. Retention is longer than the source queue's precisely so a failed upload
        // outlives the window in which it would otherwise have expired unnoticed.
        const largeFileProcessingDlq = new sqs.Queue(this, "LargeFileProcessingDLQ", {
            retentionPeriod: cdk.Duration.days(14),
            encryption: storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: storageResources.encryption.kmsKey,
            enforceSSL: true,
        });

        // Create SQS queue for large file processing
        const largeFileProcessingQueue = new sqs.Queue(this, "LargeFileProcessingQueue", {
            queueName: `${config.name}-${config.env.coreStackName}-sqsUploadLargeFile-queue`,
            visibilityTimeout: cdk.Duration.minutes(15), // Match Lambda timeout
            retentionPeriod: cdk.Duration.days(5),
            encryption: storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: storageResources.encryption.kmsKey,
            enforceSSL: true,
            // 3 receives, matching the indexer and bucket-sync queues, so a record that cannot be
            // processed reaches a DLQ after the same number of attempts wherever it entered VAMS.
            // The event source mapping uses batchSize 1, so dead-lettering isolates the one failing
            // upload rather than a batch of unrelated ones.
            deadLetterQueue: { queue: largeFileProcessingDlq, maxReceiveCount: 3 },
        });

        const uploadFileFunction = buildUploadFileFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            sendEmailFunction,
            largeFileProcessingQueue,
            config,
            vpc,
            subnets
        );
        // Shared with ApiBuilder2's process-output lambda (writes outputs back to the asset).
        this.uploadFileFunction = uploadFileFunction;
        attachFunctionToApi(this, uploadFileFunction, {
            routePath: "/uploads",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        attachFunctionToApi(this, uploadFileFunction, {
            routePath: "/uploads/{uploadId}/complete",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        // Create large file processor Lambda function
        const sqsUploadFileLargeFunction = buildSqsUploadFileLargeFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            sendEmailFunction,
            config,
            vpc,
            subnets
        );

        // Create event source mapping from SQS to Lambda (no batching)
        const esmUploadLargeFileProcessing = new lambda.EventSourceMapping(
            this,
            "UploadLargeFileProcessingEventSourceMapping",
            {
                target: sqsUploadFileLargeFunction,
                eventSourceArn: largeFileProcessingQueue.queueArn,
                batchSize: 1, // Process one message at a time
                maxBatchingWindow: cdk.Duration.seconds(0), // No batching window
            }
        );

        // Due to cdk version upgrade, not all regions support tags for EventSourceMapping
        // this line should remove the tags for regions that dont support it (govcloud currently not supported)
        if (config.app.govCloud.enabled) {
            const cfnEsmUploadLarge = esmUploadLargeFileProcessing.node
                .defaultChild as lambda.CfnEventSourceMapping;
            cfnEsmUploadLarge.addPropertyDeletionOverride("Tags");
        }

        // Grant SQS permissions to the large file processor Lambda
        largeFileProcessingQueue.grantConsumeMessages(sqsUploadFileLargeFunction);

        // Grant SQS send message permissions to uploadFile Lambda
        largeFileProcessingQueue.grantSendMessages(uploadFileFunction);

        const streamAuxiliaryPreviewAssetFunction = buildStreamAuxiliaryPreviewAssetFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, streamAuxiliaryPreviewAssetFunction, {
            routePath:
                "/database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, streamAuxiliaryPreviewAssetFunction, {
            routePath:
                "/database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}",
            method: apigateway.HttpMethod.HEAD,
            registry: registry,
        });

        const streamAssetFunction = buildStreamAssetFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, streamAssetFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, streamAssetFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}",
            method: apigateway.HttpMethod.HEAD,
            registry: registry,
        });

        const assetDownloadFunction = buildDownloadAssetFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, assetDownloadFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/download",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        // Asset Versions Function
        const assetVersionsFunction = buildAssetVersionsFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            sendEmailFunction,
            config,
            vpc,
            subnets
        );
        // Attach to createVersion endpoint
        attachFunctionToApi(this, assetVersionsFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/createVersion",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        // Attach to revertVersion endpoint
        attachFunctionToApi(this, assetVersionsFunction, {
            routePath:
                "/database/{databaseId}/assets/{assetId}/revertAssetVersion/{assetVersionId}",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        // Attach to getVersions endpoint
        attachFunctionToApi(this, assetVersionsFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/getVersions",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        // Attach to getVersion endpoint
        attachFunctionToApi(this, assetVersionsFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/getVersion/{assetVersionId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        // Attach to updateVersion endpoint (edit comment, alias)
        attachFunctionToApi(this, assetVersionsFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });
        // Attach to archiveVersion endpoint
        attachFunctionToApi(this, assetVersionsFunction, {
            routePath:
                "/database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/archive",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        // Attach to unarchiveVersion endpoint
        attachFunctionToApi(this, assetVersionsFunction, {
            routePath:
                "/database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/unarchive",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        // Asset Export Service Function
        const assetExportServiceFunction = buildAssetExportService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            assetLinksService,
            config,
            vpc,
            subnets
        );
        // Attach to export endpoint
        attachFunctionToApi(this, assetExportServiceFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/export",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        // Asset Metadata Routes (migrated to centralized metadata service)
        const methods = [
            apigateway.HttpMethod.GET,
            apigateway.HttpMethod.POST,
            apigateway.HttpMethod.PUT,
            apigateway.HttpMethod.DELETE,
        ];
        for (let i = 0; i < methods.length; i++) {
            attachFunctionToApi(this, metadataService, {
                routePath: "/database/{databaseId}/assets/{assetId}/metadata",
                method: methods[i],
                registry: registry,
            });
        }

        // File Metadata/Attribute Routes (new)
        for (let i = 0; i < methods.length; i++) {
            attachFunctionToApi(this, metadataService, {
                routePath: "/database/{databaseId}/assets/{assetId}/metadata/file",
                method: methods[i],
                registry: registry,
            });
        }

        // Database Metadata Routes (new)
        for (let i = 0; i < methods.length; i++) {
            attachFunctionToApi(this, metadataService, {
                routePath: "/database/{databaseId}/metadata",
                method: methods[i],
                registry: registry,
            });
        }

        const metadataSchemaService = buildMetadataSchemaService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );

        // NEW V2 Routes: /database/{databaseId}/metadataSchema/{metadataSchemaId} - GET/DELETE
        attachFunctionToApi(this, metadataSchemaService, {
            routePath: "/database/{databaseId}/metadataSchema/{metadataSchemaId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, metadataSchemaService, {
            routePath: "/database/{databaseId}/metadataSchema/{metadataSchemaId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        // NEW V2 Routes: /metadataschema - GET/POST/PUT
        attachFunctionToApi(this, metadataSchemaService, {
            routePath: "/metadataschema",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, metadataSchemaService, {
            routePath: "/metadataschema",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        attachFunctionToApi(this, metadataSchemaService, {
            routePath: "/metadataschema",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });

        // Pipeline, workflow, and execution API domains (CRUD + templates + triggers + execute +
        // execution ops) and the workflow Step Functions execution lambdas (process-output /
        // interim-tracking / error-handler / register) are all built in ApiBuilder2NestedStack,
        // which owns that whole domain. ApiBuilder only exposes the two shared functions those
        // lambdas need (the metadata service and the upload-file lambda) via public properties.
        this.metadataServiceFunction = metadataService;
        this.uploadFileFunction = uploadFileFunction;

        const ingestAssetFunction = buildIngestAssetFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            uploadFileFunction,
            createAssetFunction,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, ingestAssetFunction, {
            routePath: "/ingest-asset",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        const authFunctions = buildAuthFunctions(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            authResources,
            config,
            vpc,
            subnets
        );

        // NOTE: the auth constraints service and its routes (/auth/constraints,
        // /auth/constraints/{constraintId}, /auth/constraints/permissionObjects, and
        // /auth/constraintsTemplateImport) are wired in apiBuilder2-nestedStack.ts.

        attachFunctionToApi(this, authFunctions.routes, {
            routePath: "/auth/routes",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.routes, {
            routePath: "/auth/routes/api",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.routes, {
            routePath: "/auth/routes/api/allowed",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.authLoginProfile, {
            routePath: "/auth/loginProfile/{userId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.authLoginProfile, {
            routePath: "/auth/loginProfile/{userId}",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        // Cognito User Management Routes
        attachFunctionToApi(this, authFunctions.cognitoUserService, {
            routePath: "/user/cognito",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.cognitoUserService, {
            routePath: "/user/cognito",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.cognitoUserService, {
            routePath: "/user/cognito/{userId}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.cognitoUserService, {
            routePath: "/user/cognito/{userId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.cognitoUserService, {
            routePath: "/user/cognito/{userId}/resetPassword",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        // API Key Management Routes
        attachFunctionToApi(this, authFunctions.apiKeyService, {
            routePath: "/auth/api-keys",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.apiKeyService, {
            routePath: "/auth/api-keys",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.apiKeyService, {
            routePath: "/auth/api-keys/{apiKeyId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.apiKeyService, {
            routePath: "/auth/api-keys/{apiKeyId}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.apiKeyService, {
            routePath: "/auth/api-keys/{apiKeyId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        // User-level (self-service) API key routes — scoped to the requesting
        // user's own keys with mandatory expiration (enforced by the handler).
        attachFunctionToApi(this, authFunctions.apiKeyService, {
            routePath: "/auth/user/api-keys",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.apiKeyService, {
            routePath: "/auth/user/api-keys",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.apiKeyService, {
            routePath: "/auth/user/api-keys/{apiKeyId}",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.apiKeyService, {
            routePath: "/auth/user/api-keys/{apiKeyId}",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });

        attachFunctionToApi(this, authFunctions.apiKeyService, {
            routePath: "/auth/user/api-keys/{apiKeyId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        // Metadata Schema Defaults - Auto-load default schemas if configured
        if (
            config.app.metadataSchema.autoLoadDefaultAssetLinksSchema ||
            config.app.metadataSchema.autoLoadDefaultDatabaseSchema ||
            config.app.metadataSchema.autoLoadDefaultAssetSchema ||
            config.app.metadataSchema.autoLoadDefaultAssetFileSchema
        ) {
            // Setup Custom Resource Role Policy for metadata schema initialization
            const metadataSchemaCustomResourcePolicy = new iam.PolicyDocument({
                statements: [
                    new iam.PolicyStatement({
                        effect: iam.Effect.ALLOW,
                        actions: ["dynamodb:PutItem"],
                        resources: [storageResources.dynamo.metadataSchemaStorageTableV2.tableArn],
                    }),
                ],
            });

            const metadataSchemaCustomResourceRole = new iam.Role(
                this,
                "MetadataSchemaDefaultCustomResourceRole",
                {
                    assumedBy: Service("LAMBDA").Principal,
                    inlinePolicies: {
                        TablePolicy: metadataSchemaCustomResourcePolicy,
                    },
                    managedPolicies: [
                        iam.ManagedPolicy.fromAwsManagedPolicyName(
                            "service-role/AWSLambdaBasicExecutionRole"
                        ),
                    ],
                }
            );

            // Add KMS permissions when KMS encryption is enabled, regardless of timing issues
            if (config.app.useKmsCmkEncryption.enabled) {
                if (storageResources.encryption.kmsKey) {
                    // KMS key is available, add specific permissions
                    metadataSchemaCustomResourceRole.attachInlinePolicy(
                        new iam.Policy(this, "CRAuthKmsPolicy", {
                            statements: [
                                kmsKeyPolicyStatementGenerator(storageResources.encryption.kmsKey),
                            ],
                        })
                    );
                } else {
                    // KMS key not yet available, add general KMS permissions for custom resources
                    metadataSchemaCustomResourceRole.attachInlinePolicy(
                        new iam.Policy(this, "CRAuthKmsPolicy", {
                            statements: [
                                new iam.PolicyStatement({
                                    actions: [
                                        "kms:Decrypt",
                                        "kms:DescribeKey",
                                        "kms:Encrypt",
                                        "kms:GenerateDataKey*",
                                        "kms:ReEncrypt*",
                                        "kms:ListKeys",
                                        "kms:CreateGrant",
                                        "kms:ListAliases",
                                    ],
                                    effect: iam.Effect.ALLOW,
                                    resources: ["*"], // Will be constrained by KMS key policy
                                    conditions: {
                                        StringEquals: {
                                            "kms:ViaService": [
                                                Service("DYNAMODB", false).Endpoint,
                                                Service("S3", false).Endpoint,
                                            ],
                                        },
                                    },
                                }),
                            ],
                        })
                    );
                }
            }

            // Instantiate the metadata schema defaults construct
            const metadataSchemaDefaults = new DynamoDbMetadataSchemaDefaultsConstruct(
                this,
                "MetadataSchemaDefaults",
                {
                    customResourceRole: metadataSchemaCustomResourceRole,
                    lambdaCommonBaseLayer: lambdaCommonBaseLayer,
                    storageResources: storageResources,
                    config: config,
                }
            );
        }

        //Nag Supressions
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-SQS3",
                    reason: "Intended not to use DLQs for these types of SQS events. Re-drives should come from re-uploading files.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Not providing IAM wildcard permissions to constraint tables.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Wildcard scoped to deployment-named SQS queues and EventBridge buses via config.name. External (non-VAMS) resources require user-configured resource-based policies. Pipeline resources are created at runtime, so ARNs cannot be known at deploy time.",
                },
            ],
            true
        );
    }
}
