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
    kmsKeyPolicyStatementGenerator,
} from "../helper/security";
import { authResources } from "../nestedStacks/auth/authBuilder-nestedStack";

interface AuthFunctions {
    constraints: lambda.Function;
    scopeds3access: lambda.Function;
    authService: lambda.Function;
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

    const lambdaIdentityPrincipal: string = Service("LAMBDA").PrincipalString;
    let storageBucketScopedS3AccessRole = undefined

    if(config.app.authProvider.useCognito.enabled) {
        let idpPrincipal = Service("COGNITO_IDENTITY").PrincipalString;
        storageBucketScopedS3AccessRole = new iam.Role(scope, "storageBucketScopedS3AccessRole", {
            assumedBy: new iam.CompositePrincipal(
                new iam.FederatedPrincipal(
                    idpPrincipal,
                    {
                        StringEquals: {
                            [`${idpPrincipal}:aud`]: authResources.cognito.identityPoolId,
                        },
                        "ForAnyValue:StringLike": {
                            [`${idpPrincipal}:amr`]: "authenticated",
                        },
                    },
                    "sts:AssumeRoleWithWebIdentity"
                ),
                new iam.FederatedPrincipal(lambdaIdentityPrincipal)
            ),
            maxSessionDuration: Duration.seconds(config.app.authProvider.credTokenTimeoutSeconds),
        });
    }
    else if (config.app.authProvider.useExternalOAuthIdp.enabled) {
        let idpPrincipal = config.app.authProvider.useExternalOAuthIdp.idpAuthPrincipalDomain;
        storageBucketScopedS3AccessRole = new iam.Role(scope, "storageBucketScopedS3AccessRole", {
            assumedBy: new iam.CompositePrincipal(
                new iam.FederatedPrincipal(
                    idpPrincipal,
                    {
                        StringEquals: {
                            [`${idpPrincipal}:aud`]: config.app.authProvider.useExternalOAuthIdp.idpAuthClientId
                        }
                    },
                    "sts:AssumeRoleWithWebIdentity"
                ),
                new iam.FederatedPrincipal(lambdaIdentityPrincipal)
            ),
            maxSessionDuration: Duration.seconds(config.app.authProvider.credTokenTimeoutSeconds),
        });
    }

    //const storageBucketScopedS3AccessRole = new iam.Role(scope, "storageBucketScopedS3AccessRole", {
    //    assumedBy: [Service("LAMBDA").Principal, Service("COGNITO_IDENTITY").Principal]
    //});

    //Note KMS key needs to be added inside Lambda function as it overwritees policy when assumed from "storageBucketScopedS3AccessRole"
    storageResources.s3.assetBucket.grantReadWrite(storageBucketScopedS3AccessRole!);

    const scopeds3accessFunction = buildAuthFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        authResources,
        config,
        vpc,
        subnets,
        "scopeds3access",
        {
            AWS_PARTITION: ServiceHelper.Partition(),
            ROLE_ARN: storageBucketScopedS3AccessRole!.roleArn,
            S3_BUCKET: storageResources.s3.assetBucket.bucketName,
            KMS_KEY_ARN: storageResources.encryption.kmsKey
                ? storageResources.encryption.kmsKey.keyArn
                : "",
            USE_EXTERNAL_OAUTH: config.app.authProvider.useExternalOAuthIdp.enabled ? "true" : "false",
            COGNITO_AUTH: config.app.authProvider.useCognito.enabled ?
                "cognito-idp." +
                config.env.region +
                ".amazonaws.com/" +
                authResources.cognito.userPoolId : "",
            IDENTITY_POOL_ID: config.app.authProvider.useCognito.enabled ? authResources.cognito.identityPoolId : "",
            CRED_TOKEN_TIMEOUT_SECONDS: config.app.authProvider.credTokenTimeoutSeconds.toString(),
        }
    );

    storageBucketScopedS3AccessRole!.assumeRolePolicy?.addStatements(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["sts:AssumeRole"],
            principals: [scopeds3accessFunction.role!],
        })
    );

    return {
        constraints: buildAuthFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            authResources,
            config,
            vpc,
            subnets,
            "constraints"
        ),
        scopeds3access: scopeds3accessFunction,
        authService: buildAuthService(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        ),
    };
}

export function buildAuthFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    authResources: authResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    name: string,
    environment?: { [key: string]: string }
): lambda.Function {
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
        environment: {
            TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            ASSET_STORAGE_TABLE_NAME: storageResources.dynamo.assetStorageTable.tableName,
            DATABASE_STORAGE_TABLE_NAME: storageResources.dynamo.databaseStorageTable.tableName,
            AUTH_TABLE_NAME: storageResources.dynamo.authEntitiesStorageTable.tableName,
            USER_ROLES_TABLE_NAME: storageResources.dynamo.userRolesStorageTable.tableName,
            ...environment,
        },
    });
    storageResources.dynamo.authEntitiesStorageTable.grantReadWriteData(fun);
    storageResources.dynamo.assetStorageTable.grantReadData(fun);
    storageResources.dynamo.databaseStorageTable.grantReadData(fun);
    storageResources.dynamo.userRolesStorageTable.grantReadData(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, storageResources.encryption.kmsKey);
    return fun;
}

export function buildAuthService(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    storageResources: storageResources,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    environment?: { [key: string]: string }
): lambda.Function {
    const name = "authService";
    const authService = new lambda.Function(scope, name, {
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
            ...environment,
        },
    });

    storageResources.dynamo.authEntitiesStorageTable.grantReadData(authService);
    storageResources.dynamo.userRolesStorageTable.grantReadData(authService);
    kmsKeyLambdaPermissionAddToResourcePolicy(authService, storageResources.encryption.kmsKey);

    return authService;
}
