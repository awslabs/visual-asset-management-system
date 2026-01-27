/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { Names } from "aws-cdk-lib";
import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import { SqsSubscription } from "aws-cdk-lib/aws-sns-subscriptions";

import { ApiGatewayV2LambdaConstruct } from "./constructs/apigatewayv2-lambda-construct";
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
    buildListWorkflowExecutionsFunction,
    buildWorkflowService,
    buildCreateWorkflowFunction,
    buildExecuteWorkflowFunction,
    buildProcessWorkflowExecutionOutputFunction,
    buildImportGlobalPipelineWorkflowFunction,
    buildSqsAutoExecuteWorkflowFunction,
} from "../../lambdaBuilder/workflowFunctions";
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
import {
    buildCreatePipelineFunction,
    buildEnablePipelineFunction,
    buildPipelineService,
} from "../../lambdaBuilder/pipelineFunctions";
import { NestedStack } from "aws-cdk-lib";

import { buildMetadataSchemaService } from "../../lambdaBuilder/metadataSchemaFunctions";
import { buildMetadataService } from "../../lambdaBuilder/metadataFunctions";
import { buildAuthFunctions } from "../../lambdaBuilder/authFunctions";
import { buildTagService, buildCreateTagFunction } from "../../lambdaBuilder/tagFunctions";
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
import {
    buildTagTypeService,
    buildCreateTagTypeFunction,
} from "../../lambdaBuilder/tagTypeFunctions";
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

interface apiGatewayLambdaConfiguration {
    routePath: string;
    method: apigateway.HttpMethod;
    api: apigateway.HttpApi;
}

export class ApiBuilderNestedStack extends NestedStack {
    public importGlobalPipelineWorkflowFunctionName = "";

    constructor(
        parent: Construct,
        name: string,
        config: Config.Config,
        api: apigateway.HttpApi,
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
            api: api,
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
            api: api,
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
            api: api,
        });
        attachFunctionToApi(this, databaseService, {
            routePath: "/database/{databaseId}",
            method: apigateway.HttpMethod.GET,
            api: api,
        });
        attachFunctionToApi(this, databaseService, {
            routePath: "/database/{databaseId}",
            method: apigateway.HttpMethod.PUT,
            api: api,
        });
        attachFunctionToApi(this, databaseService, {
            routePath: "/database/{databaseId}",
            method: apigateway.HttpMethod.DELETE,
            api: api,
        });

        attachFunctionToApi(this, databaseService, {
            routePath: "/buckets",
            method: apigateway.HttpMethod.GET,
            api: api,
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
                api: api,
            });
        }

        attachFunctionToApi(this, commentService, {
            routePath:
                "/comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}",
            method: apigateway.HttpMethod.DELETE,
            api: api,
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
            api: api,
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
            api: api,
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
            api: api,
        });
        attachFunctionToApi(this, roleService, {
            routePath: "/roles/{roleId}",
            method: apigateway.HttpMethod.DELETE,
            api: api,
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
            api: api,
        });
        attachFunctionToApi(this, createRoleFunction, {
            routePath: "/roles",
            method: apigateway.HttpMethod.PUT,
            api: api,
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
            api: api,
        });
        attachFunctionToApi(this, userRolesService, {
            routePath: "/user-roles",
            method: apigateway.HttpMethod.POST,
            api: api,
        });
        attachFunctionToApi(this, userRolesService, {
            routePath: "/user-roles",
            method: apigateway.HttpMethod.PUT,
            api: api,
        });
        attachFunctionToApi(this, userRolesService, {
            routePath: "/user-roles",
            method: apigateway.HttpMethod.DELETE,
            api: api,
        });

        //Tags Resources
        const tagService = buildTagService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, tagService, {
            routePath: "/tags",
            method: apigateway.HttpMethod.GET,
            api: api,
        });
        attachFunctionToApi(this, tagService, {
            routePath: "/tags/{tagId}",
            method: apigateway.HttpMethod.DELETE,
            api: api,
        });

        const createTagFunction = buildCreateTagFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, createTagFunction, {
            routePath: "/tags",
            method: apigateway.HttpMethod.POST,
            api: api,
        });
        attachFunctionToApi(this, createTagFunction, {
            routePath: "/tags",
            method: apigateway.HttpMethod.PUT,
            api: api,
        });

        //Tag Types Resources
        const tagTypeService = buildTagTypeService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, tagTypeService, {
            routePath: "/tag-types",
            method: apigateway.HttpMethod.GET,
            api: api,
        });
        attachFunctionToApi(this, tagTypeService, {
            routePath: "/tag-types/{tagTypeId}",
            method: apigateway.HttpMethod.DELETE,
            api: api,
        });

        const createTagTypeFunction = buildCreateTagTypeFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, createTagTypeFunction, {
            routePath: "/tag-types",
            method: apigateway.HttpMethod.POST,
            api: api,
        });
        attachFunctionToApi(this, createTagTypeFunction, {
            routePath: "/tag-types",
            method: apigateway.HttpMethod.PUT,
            api: api,
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
            api: api,
        });
        attachFunctionToApi(this, subscriptionService, {
            routePath: "/subscriptions",
            method: apigateway.HttpMethod.POST,
            api: api,
        });
        attachFunctionToApi(this, subscriptionService, {
            routePath: "/subscriptions",
            method: apigateway.HttpMethod.PUT,
            api: api,
        });
        attachFunctionToApi(this, subscriptionService, {
            routePath: "/subscriptions",
            method: apigateway.HttpMethod.DELETE,
            api: api,
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
            api: api,
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
            api: api,
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
            api: api,
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
            api: api,
        });

        attachFunctionToApi(this, assetLinksService, {
            routePath: "/asset-links/single/{assetLinkId}",
            method: apigateway.HttpMethod.GET,
            api: api,
        });

        attachFunctionToApi(this, assetLinksService, {
            routePath: "/asset-links/{assetLinkId}",
            method: apigateway.HttpMethod.PUT,
            api: api,
        });
        attachFunctionToApi(this, assetLinksService, {
            routePath: "/asset-links/{relationId}",
            method: apigateway.HttpMethod.DELETE,
            api: api,
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

        // Asset Link Metadata Routes (updated - removed metadataKey path parameter)
        attachFunctionToApi(this, metadataService, {
            routePath: "/asset-links/{assetLinkId}/metadata",
            method: apigateway.HttpMethod.GET,
            api: api,
        });
        attachFunctionToApi(this, metadataService, {
            routePath: "/asset-links/{assetLinkId}/metadata",
            method: apigateway.HttpMethod.POST,
            api: api,
        });
        attachFunctionToApi(this, metadataService, {
            routePath: "/asset-links/{assetLinkId}/metadata",
            method: apigateway.HttpMethod.PUT,
            api: api,
        });
        attachFunctionToApi(this, metadataService, {
            routePath: "/asset-links/{assetLinkId}/metadata",
            method: apigateway.HttpMethod.DELETE,
            api: api,
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
            api: api,
        });
        attachFunctionToApi(this, assetService, {
            routePath: "/database/{databaseId}/assets/{assetId}",
            method: apigateway.HttpMethod.GET,
            api: api,
        });

        attachFunctionToApi(this, assetService, {
            routePath: "/database/{databaseId}/assets/{assetId}/archiveAsset",
            method: apigateway.HttpMethod.DELETE,
            api: api,
        });

        attachFunctionToApi(this, assetService, {
            routePath: "/database/{databaseId}/assets/{assetId}/deleteAsset",
            method: apigateway.HttpMethod.DELETE,
            api: api,
        });
        attachFunctionToApi(this, assetService, {
            routePath: "/database/{databaseId}/assets/{assetId}",
            method: apigateway.HttpMethod.PUT,
            api: api,
        });
        attachFunctionToApi(this, assetService, {
            routePath: "/database/{databaseId}/assets/{assetId}/unarchiveAsset",
            method: apigateway.HttpMethod.PUT,
            api: api,
        });
        attachFunctionToApi(this, assetService, {
            routePath: "/assets",
            method: apigateway.HttpMethod.GET,
            api: api,
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
            api: api,
        });

        // Add new file operation routes
        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/fileInfo",
            method: apigateway.HttpMethod.GET,
            api: api,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/moveFile",
            method: apigateway.HttpMethod.POST,
            api: api,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/copyFile",
            method: apigateway.HttpMethod.POST,
            api: api,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/archiveFile",
            method: apigateway.HttpMethod.DELETE,
            api: api,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/deleteAssetPreview",
            method: apigateway.HttpMethod.DELETE,
            api: api,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/deleteAuxiliaryPreviewAssetFiles",
            method: apigateway.HttpMethod.DELETE,
            api: api,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/deleteFile",
            method: apigateway.HttpMethod.DELETE,
            api: api,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/revertFileVersion/{versionId}",
            method: apigateway.HttpMethod.POST,
            api: api,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/unarchiveFile",
            method: apigateway.HttpMethod.POST,
            api: api,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/setPrimaryFile",
            method: apigateway.HttpMethod.PUT,
            api: api,
        });

        attachFunctionToApi(this, assetFilesFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/createFolder",
            method: apigateway.HttpMethod.POST,
            api: api,
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
            api: api,
        });

        // Create SQS queue for large file processing
        const largeFileProcessingQueue = new sqs.Queue(this, "LargeFileProcessingQueue", {
            queueName: `${config.name}-${config.env.coreStackName}-sqsUploadLargeFile-queue`,
            visibilityTimeout: cdk.Duration.minutes(15), // Match Lambda timeout
            retentionPeriod: cdk.Duration.days(5),
            deadLetterQueue: undefined, // No DLQ initially as per requirements
            encryption: storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: storageResources.encryption.kmsKey,
            enforceSSL: true,
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
        attachFunctionToApi(this, uploadFileFunction, {
            routePath: "/uploads",
            method: apigateway.HttpMethod.POST,
            api: api,
        });

        attachFunctionToApi(this, uploadFileFunction, {
            routePath: "/uploads/{uploadId}/complete",
            method: apigateway.HttpMethod.POST,
            api: api,
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
            api: api,
        });
        attachFunctionToApi(this, streamAuxiliaryPreviewAssetFunction, {
            routePath:
                "/database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}",
            method: apigateway.HttpMethod.HEAD,
            api: api,
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
            api: api,
        });
        attachFunctionToApi(this, streamAssetFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}",
            method: apigateway.HttpMethod.HEAD,
            api: api,
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
            api: api,
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
            api: api,
        });
        // Attach to revertVersion endpoint
        attachFunctionToApi(this, assetVersionsFunction, {
            routePath:
                "/database/{databaseId}/assets/{assetId}/revertAssetVersion/{assetVersionId}",
            method: apigateway.HttpMethod.POST,
            api: api,
        });
        // Attach to getVersions endpoint
        attachFunctionToApi(this, assetVersionsFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/getVersions",
            method: apigateway.HttpMethod.GET,
            api: api,
        });
        // Attach to getVersion endpoint
        attachFunctionToApi(this, assetVersionsFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/getVersion/{assetVersionId}",
            method: apigateway.HttpMethod.GET,
            api: api,
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
            api: api,
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
                api: api,
            });
        }

        // File Metadata/Attribute Routes (new)
        for (let i = 0; i < methods.length; i++) {
            attachFunctionToApi(this, metadataService, {
                routePath: "/database/{databaseId}/assets/{assetId}/metadata/file",
                method: methods[i],
                api: api,
            });
        }

        // Database Metadata Routes (new)
        for (let i = 0; i < methods.length; i++) {
            attachFunctionToApi(this, metadataService, {
                routePath: "/database/{databaseId}/metadata",
                method: methods[i],
                api: api,
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
            api: api,
        });

        attachFunctionToApi(this, metadataSchemaService, {
            routePath: "/database/{databaseId}/metadataSchema/{metadataSchemaId}",
            method: apigateway.HttpMethod.DELETE,
            api: api,
        });

        // NEW V2 Routes: /metadataschema - GET/POST/PUT
        attachFunctionToApi(this, metadataSchemaService, {
            routePath: "/metadataschema",
            method: apigateway.HttpMethod.GET,
            api: api,
        });

        attachFunctionToApi(this, metadataSchemaService, {
            routePath: "/metadataschema",
            method: apigateway.HttpMethod.POST,
            api: api,
        });

        attachFunctionToApi(this, metadataSchemaService, {
            routePath: "/metadataschema",
            method: apigateway.HttpMethod.PUT,
            api: api,
        });

        //Pipeline Resources
        const enablePipelineFunction = buildEnablePipelineFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );

        const createPipelineFunction = buildCreatePipelineFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            enablePipelineFunction,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, createPipelineFunction, {
            routePath: "/pipelines",
            method: apigateway.HttpMethod.PUT,
            api: api,
        });

        const pipelineService = buildPipelineService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, pipelineService, {
            routePath: "/database/{databaseId}/pipelines",
            method: apigateway.HttpMethod.GET,
            api: api,
        });
        attachFunctionToApi(this, pipelineService, {
            routePath: "/database/{databaseId}/pipelines/{pipelineId}",
            method: apigateway.HttpMethod.GET,
            api: api,
        });
        attachFunctionToApi(this, pipelineService, {
            routePath: "/database/{databaseId}/pipelines/{pipelineId}",
            method: apigateway.HttpMethod.DELETE,
            api: api,
        });
        attachFunctionToApi(this, pipelineService, {
            routePath: "/pipelines",
            method: apigateway.HttpMethod.GET,
            api: api,
        });

        //Workflows
        const workflowService = buildWorkflowService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, workflowService, {
            routePath: "/database/{databaseId}/workflows",
            method: apigateway.HttpMethod.GET,
            api: api,
        });
        attachFunctionToApi(this, workflowService, {
            routePath: "/database/{databaseId}/workflows/{workflowId}",
            method: apigateway.HttpMethod.GET,
            api: api,
        });
        attachFunctionToApi(this, workflowService, {
            routePath: "/database/{databaseId}/workflows/{workflowId}",
            method: apigateway.HttpMethod.DELETE,
            api: api,
        });
        attachFunctionToApi(this, workflowService, {
            routePath: "/workflows",
            method: apigateway.HttpMethod.GET,
            api: api,
        });

        const listWorkflowExecutionsFunction = buildListWorkflowExecutionsFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, listWorkflowExecutionsFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/workflows/executions/{workflowId}",
            method: apigateway.HttpMethod.GET,
            api: api,
        });

        attachFunctionToApi(this, listWorkflowExecutionsFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/workflows/executions",
            method: apigateway.HttpMethod.GET,
            api: api,
        });

        const processWorkflowExecutionOutputFunction = buildProcessWorkflowExecutionOutputFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            uploadFileFunction,
            metadataService,
            config,
            vpc,
            subnets
        );

        const createWorkflowFunction = buildCreateWorkflowFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            processWorkflowExecutionOutputFunction,
            config.env.coreStackName,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, createWorkflowFunction, {
            routePath: "/workflows",
            method: apigateway.HttpMethod.PUT,
            api: api,
        });

        const runWorkflowFunction = buildExecuteWorkflowFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            metadataService,
            config,
            vpc,
            subnets
        );

        attachFunctionToApi(this, runWorkflowFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/workflows/{workflowId}",
            method: apigateway.HttpMethod.POST,
            api: api,
        });

        // Use the workflow auto-execute queue from storage resources
        const workflowAutoExecuteQueue = storageResources.sqs.workflowAutoExecuteQueue;

        // Create the auto-execute workflow Lambda function
        const sqsAutoExecuteWorkflowFunction = buildSqsAutoExecuteWorkflowFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            runWorkflowFunction,
            config,
            vpc,
            subnets
        );

        // Grant SQS permissions to the Lambda
        workflowAutoExecuteQueue.grantConsumeMessages(sqsAutoExecuteWorkflowFunction);

        // Setup event source mapping with GovCloud support
        if (config.app.govCloud.enabled) {
            const esmWorkflowAutoExecute = new lambda.EventSourceMapping(
                this,
                "WorkflowAutoExecuteSqsEventSource",
                {
                    eventSourceArn: workflowAutoExecuteQueue.queueArn,
                    target: sqsAutoExecuteWorkflowFunction,
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                }
            );
            const cfnEsmWorkflowAutoExecute = esmWorkflowAutoExecute.node
                .defaultChild as lambda.CfnEventSourceMapping;
            cfnEsmWorkflowAutoExecute.addPropertyDeletionOverride("Tags");
        } else {
            sqsAutoExecuteWorkflowFunction.addEventSource(
                new eventsources.SqsEventSource(workflowAutoExecuteQueue, {
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                })
            );
        }

        // Create the import global pipeline workflow function with direct function references
        const importGlobalPipelineWorkflowFunction = buildImportGlobalPipelineWorkflowFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets,
            createPipelineFunction,
            pipelineService,
            createWorkflowFunction,
            workflowService
        );

        // Set the class variable for use by core stack
        this.importGlobalPipelineWorkflowFunctionName =
            importGlobalPipelineWorkflowFunction.functionName;

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
            api: api,
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

        attachFunctionToApi(this, authFunctions.authConstraintsService, {
            routePath: "/auth/constraints",
            method: apigateway.HttpMethod.GET,
            api: api,
        });
        for (let i = 0; i < methods.length; i++) {
            attachFunctionToApi(this, authFunctions.authConstraintsService, {
                routePath: "/auth/constraints/{constraintId}",
                method: methods[i],
                api: api,
            });
        }

        attachFunctionToApi(this, authFunctions.routes, {
            routePath: "/auth/routes",
            method: apigateway.HttpMethod.POST,
            api: api,
        });

        attachFunctionToApi(this, authFunctions.authLoginProfile, {
            routePath: "/auth/loginProfile/{userId}",
            method: apigateway.HttpMethod.GET,
            api: api,
        });

        attachFunctionToApi(this, authFunctions.authLoginProfile, {
            routePath: "/auth/loginProfile/{userId}",
            method: apigateway.HttpMethod.POST,
            api: api,
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
                    assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
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
                                                Service("DYNAMODB").Endpoint,
                                                Service("S3").Endpoint,
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
    }
}

export function attachFunctionToApi(
    scope: Construct,
    lambdaFunction: lambda.Function,
    apiGatewayConfiguration: apiGatewayLambdaConfiguration
): ApiGatewayV2LambdaConstruct {
    const apig = new ApiGatewayV2LambdaConstruct(
        scope,
        apiGatewayConfiguration.method + apiGatewayConfiguration.routePath,
        {
            ...{},
            lambdaFn: lambdaFunction,
            routePath: apiGatewayConfiguration.routePath,
            methods: [apiGatewayConfiguration.method],
            api: apiGatewayConfiguration.api,
        }
    );

    return apig;
}
