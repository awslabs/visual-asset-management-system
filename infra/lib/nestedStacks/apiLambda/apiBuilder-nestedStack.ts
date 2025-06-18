/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { Names } from "aws-cdk-lib";
import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import * as apigwv2 from "@aws-cdk/aws-apigatewayv2-alpha";
import * as logs from "aws-cdk-lib/aws-logs";
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
    buildRunWorkflowFunction,
    buildProcessWorkflowExecutionOutputFunction,
} from "../../lambdaBuilder/workflowFunctions";
import {
    buildAssetColumnsFunction,
    buildAssetMetadataFunction,
    buildAssetService,
    buildStreamAuxiliaryPreviewAssetFunction,
    buildDownloadAssetFunction,
    buildAssetFiles,
    buildIngestAssetFunction,
    buildCreateAssetFunction,
    buildUploadFileFunction,
    buildAssetVersionsFunction
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
    buildAssetLinkService,
    buildGetAssetLinksFunction,
    buildDeleteAssetLinksFunction,
} from "../../lambdaBuilder/assetsLinkFunctions";
import { buildSearchFunction } from "../../lambdaBuilder/searchFunctions";
import {
    buildTagTypeService,
    buildCreateTagTypeFunction,
} from "../../lambdaBuilder/tagTypeFunctions";
import { buildRoleService, buildCreateRoleFunction } from "../../lambdaBuilder/roleFunctions";
import { buildUserRolesService } from "../../lambdaBuilder/userRoleFunctions";
import { buildSendEmailFunction } from "../../lambdaBuilder/sendEmailFunctions";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../config/config";
import { generateUniqueNameHash } from "../../helper/security";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { authResources } from "../auth/authBuilder-nestedStack";

interface apiGatewayLambdaConfiguration {
    routePath: string;
    method: apigwv2.HttpMethod;
    api: apigwv2.HttpApi;
}

export class ApiBuilderNestedStack extends NestedStack {
    constructor(
        parent: Construct,
        name: string,
        config: Config.Config,
        api: apigwv2.HttpApi,
        storageResources: storageResources,
        authResources: authResources,
        lambdaCommonBaseLayer: LayerVersion,
        lambdaCommonServiceSDKLayer: LayerVersion,
        vpc: ec2.IVpc,
        subnets: ec2.ISubnet[]
    ) {
        super(parent, name);

        apiBuilder(
            this,
            config,
            api,
            storageResources,
            authResources,
            lambdaCommonBaseLayer,
            lambdaCommonServiceSDKLayer,
            vpc,
            subnets
        );
    }
}

export function attachFunctionToApi(
    scope: Construct,
    lambdaFunction: lambda.Function,
    apiGatewayConfiguration: apiGatewayLambdaConfiguration
) {
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
}

export function apiBuilder(
    scope: Construct,
    config: Config.Config,
    api: apigwv2.HttpApi,
    storageResources: storageResources,
    authResources: authResources,
    lambdaCommonBaseLayer: LayerVersion,
    lambdaCommonServiceSDKLayer: LayerVersion,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
) {
    //config resources
    const createConfigFunction = buildConfigService(
        scope,
        lambdaCommonBaseLayer,
        storageResources.s3.assetBucket,
        storageResources.dynamo.appFeatureEnabledStorageTable,
        config,
        vpc,
        subnets,
        storageResources.encryption.kmsKey
    );

    attachFunctionToApi(scope, createConfigFunction, {
        routePath: "/secure-config",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });

    //Database Resources
    const createDatabaseFunction = buildCreateDatabaseLambdaFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, createDatabaseFunction, {
        routePath: "/databases",
        method: apigwv2.HttpMethod.PUT,
        api: api,
    });

    const databaseService = buildDatabaseService(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, databaseService, {
        routePath: "/databases",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    attachFunctionToApi(scope, databaseService, {
        routePath: "/databases/{databaseId}",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    attachFunctionToApi(scope, databaseService, {
        routePath: "/databases/{databaseId}",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });

    //Email Resources
    const sendEmailFunction = buildSendEmailFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );

    //Comment Resources
    const commentService = buildCommentService(
        scope,
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
        attachFunctionToApi(scope, commentService, {
            routePath: commentServiceRoutes[i],
            method: apigwv2.HttpMethod.GET,
            api: api,
        });
    }

    attachFunctionToApi(scope, commentService, {
        routePath: "/comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });

    const addCommentFunction = buildAddCommentLambdaFunction(
        scope,
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
    attachFunctionToApi(scope, addCommentFunction, {
        routePath: "/comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });

    const editCommentFunction = buildEditCommentLambdaFunction(
        scope,
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
    attachFunctionToApi(scope, editCommentFunction, {
        routePath: "/comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}",
        method: apigwv2.HttpMethod.PUT,
        api: api,
    });

    // Role Resources
    const roleService = buildRoleService(
        scope,
        lambdaCommonBaseLayer,
        storageResources.dynamo.rolesStorageTable,
        storageResources.dynamo.authEntitiesStorageTable,
        storageResources.dynamo.userRolesStorageTable,
        config,
        vpc,
        subnets,
        storageResources.encryption.kmsKey
    );
    attachFunctionToApi(scope, roleService, {
        routePath: "/roles",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    attachFunctionToApi(scope, roleService, {
        routePath: "/roles/{roleId}",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });

    const createRoleFunction = buildCreateRoleFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources.dynamo.rolesStorageTable,
        storageResources.dynamo.authEntitiesStorageTable,
        storageResources.dynamo.userRolesStorageTable,
        config,
        vpc,
        subnets,
        storageResources.encryption.kmsKey
    );
    attachFunctionToApi(scope, createRoleFunction, {
        routePath: "/roles",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });
    attachFunctionToApi(scope, createRoleFunction, {
        routePath: "/roles",
        method: apigwv2.HttpMethod.PUT,
        api: api,
    });

    // UserRole Resources
    const userRolesService = buildUserRolesService(
        scope,
        lambdaCommonBaseLayer,
        storageResources.dynamo.rolesStorageTable,
        storageResources.dynamo.userRolesStorageTable,
        storageResources.dynamo.authEntitiesStorageTable,
        config,
        vpc,
        subnets,
        storageResources.encryption.kmsKey
    );
    attachFunctionToApi(scope, userRolesService, {
        routePath: "/user-roles",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    attachFunctionToApi(scope, userRolesService, {
        routePath: "/user-roles",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });
    attachFunctionToApi(scope, userRolesService, {
        routePath: "/user-roles",
        method: apigwv2.HttpMethod.PUT,
        api: api,
    });
    attachFunctionToApi(scope, userRolesService, {
        routePath: "/user-roles",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });

    //Tags Resources
    const tagService = buildTagService(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, tagService, {
        routePath: "/tags",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    attachFunctionToApi(scope, tagService, {
        routePath: "/tags/{tagId}",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });

    const createTagFunction = buildCreateTagFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, createTagFunction, {
        routePath: "/tags",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });
    attachFunctionToApi(scope, createTagFunction, {
        routePath: "/tags",
        method: apigwv2.HttpMethod.PUT,
        api: api,
    });

    //Tag Types Resources
    const tagTypeService = buildTagTypeService(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, tagTypeService, {
        routePath: "/tag-types",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    attachFunctionToApi(scope, tagTypeService, {
        routePath: "/tag-types/{tagTypeId}",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });

    const createTagTypeFunction = buildCreateTagTypeFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, createTagTypeFunction, {
        routePath: "/tag-types",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });
    attachFunctionToApi(scope, createTagTypeFunction, {
        routePath: "/tag-types",
        method: apigwv2.HttpMethod.PUT,
        api: api,
    });

    //Subscription Resources
    const subscriptionService = buildSubscriptionService(
        scope,
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
    attachFunctionToApi(scope, subscriptionService, {
        routePath: "/subscriptions",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    attachFunctionToApi(scope, subscriptionService, {
        routePath: "/subscriptions",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });
    attachFunctionToApi(scope, subscriptionService, {
        routePath: "/subscriptions",
        method: apigwv2.HttpMethod.PUT,
        api: api,
    });
    attachFunctionToApi(scope, subscriptionService, {
        routePath: "/subscriptions",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });

    const unSubscribeService = buildUnSubscribeFunction(
        scope,
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
    attachFunctionToApi(scope, unSubscribeService, {
        routePath: "/unsubscribe",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });

    const checkSubscriptionService = buildCheckSubscriptionFunction(
        scope,
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
    attachFunctionToApi(scope, checkSubscriptionService, {
        routePath: "/check-subscription",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });

    //Link Assets Resources
    const linkAssetService = buildAssetLinkService(
        scope,
        lambdaCommonBaseLayer,
        config,
        storageResources.dynamo.assetLinksStorageTable,
        storageResources.dynamo.assetStorageTable,
        storageResources.dynamo.userRolesStorageTable,
        storageResources.dynamo.authEntitiesStorageTable,
        storageResources.dynamo.rolesStorageTable,
        vpc,
        subnets,
        storageResources.encryption.kmsKey
    );
    attachFunctionToApi(scope, linkAssetService, {
        routePath: "/asset-links",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });

    const getAssetLinksService = buildGetAssetLinksFunction(
        scope,
        lambdaCommonBaseLayer,
        config,
        storageResources.dynamo.assetLinksStorageTable,
        storageResources.dynamo.assetStorageTable,
        storageResources.dynamo.userRolesStorageTable,
        storageResources.dynamo.authEntitiesStorageTable,
        storageResources.dynamo.rolesStorageTable,
        vpc,
        subnets,
        storageResources.encryption.kmsKey
    );
    attachFunctionToApi(scope, getAssetLinksService, {
        routePath: "/asset-links/{assetId}",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });

    const deleteAssetLinksService = buildDeleteAssetLinksFunction(
        scope,
        lambdaCommonBaseLayer,
        config,
        storageResources.dynamo.assetLinksStorageTable,
        storageResources.dynamo.assetStorageTable,
        storageResources.dynamo.userRolesStorageTable,
        storageResources.dynamo.authEntitiesStorageTable,
        storageResources.dynamo.rolesStorageTable,
        vpc,
        subnets,
        storageResources.encryption.kmsKey
    );
    attachFunctionToApi(scope, deleteAssetLinksService, {
        routePath: "/asset-links/{relationId}",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });

    //Asset Resources
    const assetService = buildAssetService(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, assetService, {
        routePath: "/database/{databaseId}/assets",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    attachFunctionToApi(scope, assetService, {
        routePath: "/database/{databaseId}/assets/{assetId}",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    
    attachFunctionToApi(scope, assetService, {
        routePath: "/database/{databaseId}/assets/{assetId}/archiveAsset",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });
    
    attachFunctionToApi(scope, assetService, {
        routePath: "/database/{databaseId}/assets/{assetId}/deleteAsset",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });
    attachFunctionToApi(scope, assetService, {
        routePath: "/database/{databaseId}/assets/{assetId}",
        method: apigwv2.HttpMethod.PUT,
        api: api,
    });
    attachFunctionToApi(scope, assetService, {
        routePath: "/assets",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });

    const assetFilesFunction = buildAssetFiles(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        sendEmailFunction,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, assetFilesFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/listFiles",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    
    // Add new file operation routes
    attachFunctionToApi(scope, assetFilesFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/fileInfo",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    
    attachFunctionToApi(scope, assetFilesFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/moveFile",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });
    
    attachFunctionToApi(scope, assetFilesFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/copyFile",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });
    
    attachFunctionToApi(scope, assetFilesFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/archiveFile",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });
    
    attachFunctionToApi(scope, assetFilesFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/deleteFile",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });
    
    attachFunctionToApi(scope, assetFilesFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/revertFileVersion/{versionId}",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });
    
    attachFunctionToApi(scope, assetFilesFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/unarchiveFile",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });

    const assetMetadataFunction = buildAssetMetadataFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, assetMetadataFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/metadata",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });

    const assetColumnsFunction = buildAssetColumnsFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, assetColumnsFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/columns",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });

    const createAssetFunction = buildCreateAssetFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, createAssetFunction, {
        routePath: "/assets",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });

    const uploadFileFunction = buildUploadFileFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        sendEmailFunction,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, uploadFileFunction, {
        routePath: "/uploads",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });

    attachFunctionToApi(scope, uploadFileFunction, {
        routePath: "/uploads/{uploadId}/complete",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });

    attachFunctionToApi(scope, uploadFileFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/createFolder",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });

    const streamAuxiliaryPreviewAssetFunction = buildStreamAuxiliaryPreviewAssetFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, streamAuxiliaryPreviewAssetFunction, {
        routePath: "/auxiliaryPreviewAssets/stream/{assetId}/{proxy+}",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });

    const assetDownloadFunction = buildDownloadAssetFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, assetDownloadFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/download",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });
    
    // Asset Versions Function
    const assetVersionsFunction = buildAssetVersionsFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    // Attach to createVersion endpoint
    attachFunctionToApi(scope, assetVersionsFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/createVersion",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });
    // Attach to revertVersion endpoint
    attachFunctionToApi(scope, assetVersionsFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/revertAssetVersion/{assetVersionId}",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });
    // Attach to getVersions endpoint
    attachFunctionToApi(scope, assetVersionsFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/getVersions",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    // Attach to getVersion endpoint
    attachFunctionToApi(scope, assetVersionsFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/getVersion/{assetVersionId}",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });

    // metdata
    const metadataCrudFunctions = buildMetadataFunctions(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    const methods = [
        apigwv2.HttpMethod.PUT,
        apigwv2.HttpMethod.GET,
        apigwv2.HttpMethod.POST,
        apigwv2.HttpMethod.DELETE,
    ];
    for (let i = 0; i < methods.length; i++) {
        attachFunctionToApi(scope, metadataCrudFunctions[i], {
            routePath: "/metadata/{databaseId}/{assetId}",
            method: methods[i],
            api: api,
        });
    }

    const metadataSchemaFunctions = buildMetadataSchemaService(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );

    const metadataSchemaMethods = [
        apigwv2.HttpMethod.GET,
        apigwv2.HttpMethod.POST,
        apigwv2.HttpMethod.PUT,
    ];
    for (let i = 0; i < metadataSchemaMethods.length; i++) {
        attachFunctionToApi(scope, metadataSchemaFunctions, {
            routePath: "/metadataschema/{databaseId}",
            method: metadataSchemaMethods[i],
            api: api,
        });
    }
    attachFunctionToApi(scope, metadataSchemaFunctions, {
        routePath: "/metadataschema/{databaseId}/{field}",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });

    //Pipeline Resources
    const enablePipelineFunction = buildEnablePipelineFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );

    const createPipelineFunction = buildCreatePipelineFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        enablePipelineFunction,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, createPipelineFunction, {
        routePath: "/pipelines",
        method: apigwv2.HttpMethod.PUT,
        api: api,
    });

    const pipelineService = buildPipelineService(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, pipelineService, {
        routePath: "/database/{databaseId}/pipelines",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    attachFunctionToApi(scope, pipelineService, {
        routePath: "/database/{databaseId}/pipelines/{pipelineId}",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    attachFunctionToApi(scope, pipelineService, {
        routePath: "/database/{databaseId}/pipelines/{pipelineId}",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });
    attachFunctionToApi(scope, pipelineService, {
        routePath: "/pipelines",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });

    //Workflows
    const workflowService = buildWorkflowService(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, workflowService, {
        routePath: "/database/{databaseId}/workflows",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    attachFunctionToApi(scope, workflowService, {
        routePath: "/database/{databaseId}/workflows/{workflowId}",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    attachFunctionToApi(scope, workflowService, {
        routePath: "/database/{databaseId}/workflows/{workflowId}",
        method: apigwv2.HttpMethod.DELETE,
        api: api,
    });
    attachFunctionToApi(scope, workflowService, {
        routePath: "/workflows",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });

    const listWorkflowExecutionsFunction = buildListWorkflowExecutionsFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, listWorkflowExecutionsFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/workflows/{workflowId}/executions",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });

    const processWorkflowExecutionOutputFunction = buildProcessWorkflowExecutionOutputFunction(
        scope,
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
        scope,
        lambdaCommonServiceSDKLayer,
        storageResources,
        processWorkflowExecutionOutputFunction,
        config.env.coreStackName,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, createWorkflowFunction, {
        routePath: "/workflows",
        method: apigwv2.HttpMethod.PUT,
        api: api,
    });

    const runWorkflowFunction = buildRunWorkflowFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        metadataCrudFunctions[1],
        config,
        vpc,
        subnets
    );

    attachFunctionToApi(scope, runWorkflowFunction, {
        routePath: "/database/{databaseId}/assets/{assetId}/workflows/{workflowId}",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });

    const ingestAssetFunction = buildIngestAssetFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        uploadFileFunction,
        createAssetFunction,
        config,
        vpc,
        subnets
    );
    attachFunctionToApi(scope, ingestAssetFunction, {
        routePath: "/ingest-asset",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });

    const authFunctions = buildAuthFunctions(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        authResources,
        config,
        vpc,
        subnets
    );

    attachFunctionToApi(scope, authFunctions.authConstraintsService, {
        routePath: "/auth/constraints",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });
    for (let i = 0; i < methods.length; i++) {
        attachFunctionToApi(scope, authFunctions.authConstraintsService, {
            routePath: "/auth/constraints/{constraintId}",
            method: methods[i],
            api: api,
        });
    }

    attachFunctionToApi(scope, authFunctions.routes, {
        routePath: "/auth/routes",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });

    attachFunctionToApi(scope, authFunctions.authLoginProfile, {
        routePath: "/auth/loginProfile/{userId}",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });

    attachFunctionToApi(scope, authFunctions.authLoginProfile, {
        routePath: "/auth/loginProfile/{userId}",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });

    //Enabling API Gateway Access Logging: Currently the only way to do this is via V1 constructs
    //https://github.com/aws/aws-cdk/issues/11100#issuecomment-904627081

    const accessLogs = new logs.LogGroup(scope, "VAMS-API-AccessLogs", {
        logGroupName:
            "/aws/vendedlogs/VAMS-API-AccessLogs" +
            generateUniqueNameHash(
                config.env.coreStackName,
                config.env.account,
                "VAMS-API-AccessLogs",
                10
            ),
        retention: logs.RetentionDays.TEN_YEARS,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const stage = api.defaultStage?.node.defaultChild as apigateway.CfnStage;
    stage.accessLogSettings = {
        destinationArn: accessLogs.logGroupArn,
        format: JSON.stringify({
            requestId: "$context.requestId",
            userAgent: "$context.identity.userAgent",
            sourceIp: "$context.identity.sourceIp",
            requestTime: "$context.requestTime",
            requestTimeEpoch: "$context.requestTimeEpoch",
            httpMethod: "$context.httpMethod",
            path: "$context.path",
            status: "$context.status",
            protocol: "$context.protocol",
            responseLength: "$context.responseLength",
            domainName: "$context.domainName",
        }),
    };
}
