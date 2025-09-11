/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../config/config";
import * as ServiceHelper from "../../lib/helper/service-helper";
import { Service } from "../helper/service-helper";
import * as Config from "../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    kmsKeyPolicyStatementGenerator,
} from "../helper/security";
import { authResources } from "../nestedStacks/auth/authBuilder-nestedStack";
import { CUSTOM_AUTHORIZER_IGNORED_PATHS } from "../../config/config";

interface AuthFunctions {
    authConstraintsService: lambda.Function;
    authLoginProfile: lambda.Function;
    routes: lambda.Function;
}

export function buildAuthFunctions(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    authResources: authResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): AuthFunctions {
    return {
        authConstraintsService: buildAuthConstraintsFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            authResources,
            config,
            vpc,
            subnets
        ),
        authLoginProfile: buildAuthLoginProfile(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        ),
        routes: buildRoutesService(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        ),
    };
}

export function buildAuthConstraintsFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    authResources: authResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "authConstraintsService";
    const authServiceFun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.auth.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(1),
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
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
        },
    });
    storageResources.dynamo.authEntitiesStorageTable.grantReadWriteData(authServiceFun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(authServiceFun);
    storageResources.dynamo.rolesStorageTable.grantReadData(authServiceFun);
    kmsKeyLambdaPermissionAddToResourcePolicy(authServiceFun, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(authServiceFun, config);
    return authServiceFun;
}

export function buildAuthLoginProfile(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    environment?: { [key: string]: string }
): lambda.Function {
    const name = "authLoginProfile";
    const authLoginProfileFunc = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.auth.${name}.lambda_handler`,
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
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
            USER_STORAGE_TABLE_NAME: storageResources.dynamo.userStorageTable.tableName,
            EXTERNAL_OATH_IDP_URL: config.app.authProvider.useExternalOAuthIdp.enabled
                ? config.app.authProvider.useExternalOAuthIdp.idpAuthProviderUrl
                : "", //Optional environment field they may get used for customConfigCommon method
            ...environment,
        },
    });

    storageResources.dynamo.authEntitiesStorageTable.grantReadData(authLoginProfileFunc);
    storageResources.dynamo.userRolesStorageTable.grantReadWriteData(authLoginProfileFunc);
    storageResources.dynamo.rolesStorageTable.grantReadData(authLoginProfileFunc);
    storageResources.dynamo.userStorageTable.grantReadWriteData(authLoginProfileFunc);
    kmsKeyLambdaPermissionAddToResourcePolicy(
        authLoginProfileFunc,
        storageResources.encryption.kmsKey
    );
    globalLambdaEnvironmentsAndPermissions(authLoginProfileFunc, config);

    return authLoginProfileFunc;
}

export function buildRoutesService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    environment?: { [key: string]: string }
): lambda.Function {
    const name = "routes";
    const routesFunc = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.auth.${name}.lambda_handler`,
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
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ROLES_TABLE_NAME: storageResources.dynamo.rolesStorageTable.tableName,
            ...environment,
        },
    });

    storageResources.dynamo.authEntitiesStorageTable.grantReadData(routesFunc);
    storageResources.dynamo.userRolesStorageTable.grantReadData(routesFunc);
    storageResources.dynamo.rolesStorageTable.grantReadData(routesFunc);
    kmsKeyLambdaPermissionAddToResourcePolicy(routesFunc, storageResources.encryption.kmsKey);
    globalLambdaEnvironmentsAndPermissions(routesFunc, config);

    return routesFunc;
}

export function buildApiGatewayAuthorizerHttpFunction(
    scope: Construct,
    lambdaAuthorizerLayer: LayerVersion,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "apiGatewayAuthorizerHttp";

    // Determine auth mode based on configuration
    const authMode = config.app.authProvider.useCognito.enabled
        ? "cognito"
        : config.app.authProvider.useExternalOAuthIdp.enabled
        ? "external"
        : "cognito";

    // Build environment variables
    const environment: { [key: string]: string } = {
        AUTH_MODE: authMode,
        ALLOWED_IP_RANGES: JSON.stringify(
            config.app.authProvider.authorizerOptions.allowedIpRanges || []
        ),
        IGNORED_PATHS: JSON.stringify(CUSTOM_AUTHORIZER_IGNORED_PATHS),
    };

    // Add Cognito-specific environment variables
    if (config.app.authProvider.useCognito.enabled) {
        environment.USER_POOL_ID = "${cognito_user_pool_id}"; // Will be replaced in nested stack
        environment.APP_CLIENT_ID = "${cognito_app_client_id}"; // Will be replaced in nested stack
        environment.COGNITO_BASE_URL = `https://${Service("COGNITO_IDP").Endpoint}`;
    }

    // Add External IDP-specific environment variables
    if (config.app.authProvider.useExternalOAuthIdp.enabled) {
        environment.JWT_ISSUER_URL =
            config.app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTIssuerUrl;
        environment.JWT_AUDIENCE =
            config.app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTAudience;
    }

    const authorizerFunc = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.auth.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaAuthorizerLayer],
        timeout: Duration.minutes(1),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: environment,
    });

    // Grant API Gateway invoke permissions
    authorizerFunc.grantInvoke(Service("APIGATEWAY").Principal);

    // Add global permissions
    globalLambdaEnvironmentsAndPermissions(authorizerFunc, config);

    return authorizerFunc;
}

export function buildApiGatewayAuthorizerWebsocketFunction(
    scope: Construct,
    lambdaAuthorizerLayer: LayerVersion,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "apiGatewayAuthorizerWebsocket";

    // Determine auth mode based on configuration
    const authMode = config.app.authProvider.useCognito.enabled
        ? "cognito"
        : config.app.authProvider.useExternalOAuthIdp.enabled
        ? "external"
        : "cognito";

    // Build environment variables
    const environment: { [key: string]: string } = {
        AUTH_MODE: authMode,
        ALLOWED_IP_RANGES: JSON.stringify(
            config.app.authProvider.authorizerOptions.allowedIpRanges || []
        ),
        IGNORED_PATHS: JSON.stringify(CUSTOM_AUTHORIZER_IGNORED_PATHS),
    };

    // Add Cognito-specific environment variables
    if (config.app.authProvider.useCognito.enabled) {
        environment.USER_POOL_ID = "${cognito_user_pool_id}"; // Will be replaced in nested stack
        environment.APP_CLIENT_ID = "${cognito_app_client_id}"; // Will be replaced in nested stack
    }

    // Add External IDP-specific environment variables
    if (config.app.authProvider.useExternalOAuthIdp.enabled) {
        environment.JWT_ISSUER_URL =
            config.app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTIssuerUrl;
        environment.JWT_AUDIENCE =
            config.app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTAudience;
    }

    const authorizerFunc = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.auth.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaAuthorizerLayer],
        timeout: Duration.minutes(1),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined,
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: environment,
    });

    // Grant API Gateway invoke permissions
    authorizerFunc.grantInvoke(Service("APIGATEWAY").Principal);

    // Add global permissions
    globalLambdaEnvironmentsAndPermissions(authorizerFunc, config);

    return authorizerFunc;
}
