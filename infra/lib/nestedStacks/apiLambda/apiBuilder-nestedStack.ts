/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { Names } from "aws-cdk-lib";
import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import * as sqs from "aws-cdk-lib/aws-sqs";

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

import { buildMetadataFunctions } from "../../lambdaBuilder/metadataFunctions";
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
    buildAssetLinksMetadataFunction,
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
            storageResources.dynamo.appFeatureEnabledStorageTable,
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
            storageResources.dynamo.commentStorageTable,
            storageResources.dynamo.assetStorageTable,
            storageResources.dynamo.userRolesStorageTable,
            storageResources.dynamo.authEntitiesStorageTable,
            storageResources.dynamo.rolesStorageTable,
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
            storageResources.dynamo.commentStorageTable,
            storageResources.dynamo.assetStorageTable,
            storageResources.dynamo.userRolesStorageTable,
            storageResources.dynamo.authEntitiesStorageTable,
            storageResources.dynamo.rolesStorageTable,
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
            storageResources.dynamo.commentStorageTable,
            storageResources.dynamo.assetStorageTable,
            storageResources.dynamo.userRolesStorageTable,
            storageResources.dynamo.authEntitiesStorageTable,
            storageResources.dynamo.rolesStorageTable,
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
            storageResources.dynamo.rolesStorageTable,
            storageResources.dynamo.authEntitiesStorageTable,
            storageResources.dynamo.userRolesStorageTable,
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
            storageResources.dynamo.rolesStorageTable,
            storageResources.dynamo.authEntitiesStorageTable,
            storageResources.dynamo.userRolesStorageTable,
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
            storageResources.dynamo.rolesStorageTable,
            storageResources.dynamo.userRolesStorageTable,
            storageResources.dynamo.authEntitiesStorageTable,
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
            storageResources.dynamo.subscriptionsStorageTable,
            storageResources.dynamo.assetStorageTable,
            storageResources.dynamo.userRolesStorageTable,
            storageResources.dynamo.authEntitiesStorageTable,
            storageResources.dynamo.userStorageTable,
            storageResources.dynamo.rolesStorageTable,
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
            storageResources.dynamo.subscriptionsStorageTable,
            storageResources.dynamo.assetStorageTable,
            storageResources.dynamo.userRolesStorageTable,
            storageResources.dynamo.authEntitiesStorageTable,
            storageResources.dynamo.rolesStorageTable,
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
            storageResources.dynamo.subscriptionsStorageTable,
            storageResources.dynamo.assetStorageTable,
            storageResources.dynamo.userRolesStorageTable,
            storageResources.dynamo.authEntitiesStorageTable,
            storageResources.dynamo.rolesStorageTable,
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
            storageResources.dynamo.assetLinksStorageTableV2,
            storageResources.dynamo.assetLinksMetadataStorageTable,
            storageResources.dynamo.assetStorageTable,
            storageResources.dynamo.userRolesStorageTable,
            storageResources.dynamo.authEntitiesStorageTable,
            storageResources.dynamo.rolesStorageTable,
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
            storageResources.dynamo.assetLinksStorageTableV2,
            storageResources.dynamo.assetLinksMetadataStorageTable,
            storageResources.dynamo.assetStorageTable,
            storageResources.dynamo.userRolesStorageTable,
            storageResources.dynamo.authEntitiesStorageTable,
            storageResources.dynamo.rolesStorageTable,
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

        // Asset Links Metadata Resources
        const assetLinksMetadataService = buildAssetLinksMetadataFunction(
            this,
            lambdaCommonBaseLayer,
            config,
            storageResources.dynamo.assetLinksStorageTableV2,
            storageResources.dynamo.assetLinksMetadataStorageTable,
            storageResources.dynamo.assetStorageTable,
            storageResources.dynamo.userRolesStorageTable,
            storageResources.dynamo.authEntitiesStorageTable,
            storageResources.dynamo.rolesStorageTable,
            vpc,
            subnets,
            storageResources.encryption.kmsKey
        );
        attachFunctionToApi(this, assetLinksMetadataService, {
            routePath: "/asset-links/{assetLinkId}/metadata",
            method: apigateway.HttpMethod.POST,
            api: api,
        });
        attachFunctionToApi(this, assetLinksMetadataService, {
            routePath: "/asset-links/{assetLinkId}/metadata",
            method: apigateway.HttpMethod.GET,
            api: api,
        });
        attachFunctionToApi(this, assetLinksMetadataService, {
            routePath: "/asset-links/{assetLinkId}/metadata/{metadataKey}",
            method: apigateway.HttpMethod.PUT,
            api: api,
        });
        attachFunctionToApi(this, assetLinksMetadataService, {
            routePath: "/asset-links/{assetLinkId}/metadata/{metadataKey}",
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

        // metdata
        const metadataCrudFunctions = buildMetadataFunctions(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        const methods = [
            apigateway.HttpMethod.PUT,
            apigateway.HttpMethod.GET,
            apigateway.HttpMethod.POST,
            apigateway.HttpMethod.DELETE,
        ];
        for (let i = 0; i < methods.length; i++) {
            attachFunctionToApi(this, metadataCrudFunctions[i], {
                routePath: "/database/{databaseId}/assets/{assetId}/metadata",
                method: methods[i],
                api: api,
            });
        }

        const metadataSchemaFunctions = buildMetadataSchemaService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );

        const metadataSchemaMethods = [
            apigateway.HttpMethod.GET,
            apigateway.HttpMethod.POST,
            apigateway.HttpMethod.PUT,
        ];
        for (let i = 0; i < metadataSchemaMethods.length; i++) {
            attachFunctionToApi(this, metadataSchemaFunctions, {
                routePath: "/metadataschema/{databaseId}",
                method: metadataSchemaMethods[i],
                api: api,
            });
        }
        attachFunctionToApi(this, metadataSchemaFunctions, {
            routePath: "/metadataschema/{databaseId}/{field}",
            method: apigateway.HttpMethod.DELETE,
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
            metadataCrudFunctions[1],
            metadataCrudFunctions[0],
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
            metadataCrudFunctions[1],
            config,
            vpc,
            subnets
        );

        attachFunctionToApi(this, runWorkflowFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/workflows/{workflowId}",
            method: apigateway.HttpMethod.POST,
            api: api,
        });

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
