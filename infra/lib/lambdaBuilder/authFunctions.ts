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
    setupSecurityAndLoggingEnvironmentAndPermissions,
} from "../helper/security";
import { authResources } from "../nestedStacks/auth/authBuilder-nestedStack";
import { CUSTOM_AUTHORIZER_IGNORED_PATHS } from "../../config/config";

interface AuthFunctions {
    authConstraintsService: lambda.Function;
    authConstraintsTemplateService: lambda.Function;
    authLoginProfile: lambda.Function;
    routes: lambda.Function;
    cognitoUserService: lambda.Function;
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
        authConstraintsTemplateService: buildAuthConstraintsTemplateFunction(
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
        cognitoUserService: buildCognitoUserService(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            authResources,
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
    const fun = new lambda.Function(scope, name, {
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
        environment: {},
    });
    storageResources.dynamo.authEntitiesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.constraintsStorageTable.grantReadWriteData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    return fun;
}

export function buildAuthConstraintsTemplateFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    authResources: authResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "authConstraintsTemplateService";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.auth.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(2),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {},
    });
    storageResources.dynamo.authEntitiesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.constraintsStorageTable.grantReadWriteData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    return fun;
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
    const fun = new lambda.Function(scope, name, {
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
            USER_STORAGE_TABLE_NAME: storageResources.dynamo.userStorageTable.tableName,
            EXTERNAL_OATH_IDP_URL: config.app.authProvider.useExternalOAuthIdp.enabled
                ? config.app.authProvider.useExternalOAuthIdp.idpAuthProviderUrl
                : "", //Optional environment field they may get used for customConfigCommon method
            ...environment,
        },
    });

    storageResources.dynamo.userRolesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.userStorageTable.grantReadWriteData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);

    return fun;
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
    const fun = new lambda.Function(scope, name, {
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
            ...environment,
        },
    });

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);

    return fun;
}

export function buildCognitoUserService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    authResources: authResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "cognitoUserService";

    // Build environment variables
    const environment: { [key: string]: string } = {
        COGNITO_ENABLED: config.app.authProvider.useCognito.enabled ? "true" : "false",
    };

    // Add Cognito-specific variables if enabled and authResources available
    if (config.app.authProvider.useCognito.enabled && authResources?.cognito?.userPoolId) {
        environment.USER_POOL_ID = authResources.cognito.userPoolId;
    }

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.auth.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(2),
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

    // Grant Cognito permissions only if Cognito is enabled and user pool exists
    if (config.app.authProvider.useCognito.enabled && authResources?.cognito?.userPool) {
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: [
                    "cognito-idp:ListUsers",
                    "cognito-idp:AdminCreateUser",
                    "cognito-idp:AdminUpdateUserAttributes",
                    "cognito-idp:AdminDeleteUser",
                    "cognito-idp:AdminResetUserPassword",
                    "cognito-idp:AdminSetUserPassword",
                    "cognito-idp:AdminGetUser",
                ],
                resources: [authResources.cognito.userPool.userPoolArn],
            })
        );
    }

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);

    return fun;
}

export function buildApiGatewayAuthorizerHttpFunction(
    scope: Construct,
    lambdaAuthorizerLayer: LayerVersion,
    storageResources: storageResources,
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

    const fun = new lambda.Function(scope, name, {
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
    fun.grantInvoke(Service("APIGATEWAY").Principal);

    // Add global permissions
    globalLambdaEnvironmentsAndPermissions(fun, config);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);

    return fun;
}

export function buildApiGatewayAuthorizerWebsocketFunction(
    scope: Construct,
    lambdaAuthorizerLayer: LayerVersion,
    storageResources: storageResources,
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

    const fun = new lambda.Function(scope, name, {
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
    fun.grantInvoke(Service("APIGATEWAY").Principal);

    // Add global permissions
    globalLambdaEnvironmentsAndPermissions(fun, config);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);

    return fun;
}
