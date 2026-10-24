/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as path from "path";
import { Construct } from "constructs";
import { ArnFormat, Duration, Stack } from "aws-cdk-lib";
import { NagSuppressions } from "cdk-nag";
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
    suppressCdkNagLambda,
    suppressCdkNagErrorsByGrantReadWrite,
    kmsKeyPolicyStatementGenerator,
    setupSecurityAndLoggingEnvironmentAndPermissions,
    isCognitoMfaCheckEnabled,
} from "../helper/security";
import { ApiOriginVerifySecretRef } from "../helper/apiOriginVerify";
import { authResources } from "../nestedStacks/auth/authBuilder-nestedStack";
import { CUSTOM_AUTHORIZER_IGNORED_PATHS } from "../../config/config";

interface AuthFunctions {
    authLoginProfile: lambda.Function;
    routes: lambda.Function;
    cognitoUserService: lambda.Function;
    apiKeyService: lambda.Function;
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
        apiKeyService: buildApiKeyServiceFunction(
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
    suppressCdkNagLambda(fun);
    return fun;
}

export function buildAuthConstraintsTemplateFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
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
    suppressCdkNagLambda(fun);
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
    suppressCdkNagLambda(fun);

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
    suppressCdkNagLambda(fun);

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
    suppressCdkNagLambda(fun);

    return fun;
}

export function buildApiKeyServiceFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): lambda.Function {
    const name = "apiKeyService";
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
    storageResources.dynamo.apiKeyStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    return fun;
}

export function buildApiGatewayAuthorizerRestFunction(
    scope: Construct,
    lambdaAuthorizerLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    apiOriginVerifySecret?: ApiOriginVerifySecretRef
): lambda.Function {
    const name = "apiGatewayAuthorizerRest";

    // Determine auth mode based on configuration
    const authMode = config.app.authProvider.useCognito.enabled
        ? "cognito"
        : config.app.authProvider.useExternalOAuthIdp.enabled
        ? "external"
        : "cognito";

    // Front type lets the authorizer resolve the true client IP (not the proxy IP)
    // when behind CloudFront/ALB. See backend clientIp.resolve_client_ip.
    const fronted = config.app.useCloudFront.enabled
        ? "cloudfront"
        : config.app.useAlb.enabled
        ? "alb"
        : "none";

    // Build environment variables
    const environment: { [key: string]: string } = {
        AUTH_MODE: authMode,
        API_FRONTED: fronted,
        ALLOWED_IP_RANGES: JSON.stringify(
            config.app.authProvider.authorizerOptions.allowedIpRanges || []
        ),
        IGNORED_PATHS: JSON.stringify(CUSTOM_AUTHORIZER_IGNORED_PATHS),
    };

    // Add Cognito-specific environment variables
    if (config.app.authProvider.useCognito.enabled) {
        environment.USER_POOL_ID = "${cognito_user_pool_id}"; // Will be replaced in nested stack
        environment.APP_CLIENT_ID = "${cognito_app_client_id}"; // Will be replaced in nested stack
        environment.COGNITO_BASE_URL = `https://${Service("COGNITO_IDP", false).Endpoint}`;
    }

    // MFA-preference check reachability (partition/VPC-aware; the authorizer resolves the
    // user's MFA status once and passes it to handlers via the authorizer context)
    environment.COGNITO_AUTH_ENABLED = isCognitoMfaCheckEnabled(config) ? "TRUE" : "FALSE";

    // Add External IDP-specific environment variables
    if (config.app.authProvider.useExternalOAuthIdp.enabled) {
        environment.JWT_ISSUER_URL =
            config.app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTIssuerUrl;
        environment.JWT_AUDIENCE =
            config.app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTAudience;
    }

    // API Key authentication table names resolved via SSM (no env vars needed)

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

    // API Gateway invokes this authorizer through the credentials role named as
    // authorizerCredentials on both OpenAPI security schemes (see rest-api-gateway-construct),
    // which holds the invoke grant, so the function carries no resource-based invoke policy.

    // Grant API key table read access for authorizer lookups
    storageResources.dynamo.apiKeyStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);

    // CloudFront deployments: the origin verification secret, compared against the header CloudFront
    // adds to forwarded requests before the CloudFront forwarding headers are read
    // (common/auth/originVerify.py). The environment carries the secret ARN, never its value.
    if (apiOriginVerifySecret) {
        fun.addEnvironment("ORIGIN_VERIFY_SECRET_ARN", apiOriginVerifySecret.secretArn);
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["secretsmanager:GetSecretValue"],
                resources: [apiOriginVerifySecret.secretArn],
            })
        );
        if (apiOriginVerifySecret.kmsKeyArn) {
            fun.addToRolePolicy(
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["kms:Decrypt"],
                    resources: [apiOriginVerifySecret.kmsKeyArn],
                })
            );
        }
    }

    // Add KMS permissions for encrypted DynamoDB table access
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);

    // Add global permissions
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);

    return fun;
}

/**
 * The AWS Secrets Manager rotation function of the API origin verification secret (CloudFront
 * deployments only; built by the StaticWeb nested stack, which holds the distribution).
 *
 * Each rotation writes `{current: <new>, previous: <old>}` as the pending version, sets the regional
 * web ACL's origin verification matches to the new and the old value, waits for the web ACL change to
 * settle, points the distribution's origin header at the new value, then promotes the version. The
 * secret, distribution and web ACL are passed by ARN or id; the environment carries no secret value.
 *
 * Runs outside the VPC in every configuration: Amazon CloudFront and AWS WAF have no interface VPC
 * endpoint for their control plane, so a function in an isolated subnet cannot reach either API.
 */
export function buildApiOriginVerifyRotationFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    target: {
        secret: ApiOriginVerifySecretRef;
        distribution: cloudfront.IDistribution;
        /** The regional web ACL, or undefined when `app.useWaf` is off. */
        regionalWebAclArn?: string;
        /** The distribution's CLOUDFRONT-scoped web ACL, or undefined when `app.useWaf` is off. */
        cloudFrontWebAclArn?: string;
    }
): lambda.Function {
    const name = "apiOriginVerifyRotation";
    const environment: { [key: string]: string } = {
        ORIGIN_VERIFY_DISTRIBUTION_ID: target.distribution.distributionId,
    };
    if (target.regionalWebAclArn) {
        environment.ORIGIN_VERIFY_WEB_ACL_ARN = target.regionalWebAclArn;
    }

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(path.join(__dirname, `../../../backend/backend`)),
        handler: `handlers.auth.${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        environment,
    });

    // The four rotation steps read the versions, write the pending one and move AWSCURRENT to it.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "secretsmanager:DescribeSecret",
                "secretsmanager:GetSecretValue",
                "secretsmanager:PutSecretValue",
                "secretsmanager:UpdateSecretVersionStage",
            ],
            resources: [target.secret.secretArn],
        })
    );
    // GetRandomPassword is not a resource-level action.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["secretsmanager:GetRandomPassword"],
            resources: ["*"],
        })
    );
    if (target.secret.kmsKeyArn) {
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"],
                resources: [target.secret.kmsKeyArn],
            })
        );
    }
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                "cloudfront:GetDistribution",
                "cloudfront:GetDistributionConfig",
                "cloudfront:UpdateDistribution",
            ],
            resources: [target.distribution.distributionArn],
        })
    );
    if (target.regionalWebAclArn) {
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["wafv2:GetWebACL", "wafv2:UpdateWebACL"],
                resources: [target.regionalWebAclArn],
            })
        );
        // UpdateWebACL is also authorized against each AWS managed rule group the web ACL
        // references, so the function may name those rule groups in the update it submits.
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["wafv2:UpdateWebACL"],
                resources: [
                    Stack.of(scope).formatArn({
                        service: "wafv2",
                        resource: "regional",
                        resourceName: "managedruleset/*/*",
                        arnFormat: ArnFormat.SLASH_RESOURCE_NAME,
                    }),
                ],
            })
        );
    }
    // UpdateDistribution resubmits the distribution's web ACL association (WebACLId) with the rest of
    // the configuration, so the function may read that web ACL.
    if (target.cloudFrontWebAclArn) {
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["wafv2:GetWebACL"],
                resources: [target.cloudFrontWebAclArn],
            })
        );
    }

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    setupSecurityAndLoggingEnvironmentAndPermissions(fun, storageResources);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagLambda(fun);
    suppressCdkNagErrorsByGrantReadWrite(fun);
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "secretsmanager:GetRandomPassword generates the new value and supports no resource-level permission.",
                appliesTo: ["Resource::*"],
            },
            {
                id: "AwsSolutions-IAM5",
                reason: "UpdateWebACL is authorized against each AWS managed rule group the regional web ACL references; the action is the only one granted on them.",
                appliesTo: [
                    {
                        regex: "/^Resource::arn:.*:wafv2:.*:regional\\/managedruleset\\/\\*\\/\\*$/g",
                    },
                ],
            },
        ],
        true
    );

    return fun;
}
