/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cognito from "aws-cdk-lib/aws-cognito";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as cdk from "aws-cdk-lib";

import { storageResources } from "../storage-builder";
import { Construct } from "constructs";
import { Duration } from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";

export interface SamlSettings {
    metadata: cognito.UserPoolIdentityProviderSamlMetadata;
    name: string;
    attributeMapping: cognito.AttributeMapping;
    cognitoDomainPrefix: string;
}

export interface CognitoWebNativeConstructProps extends cdk.StackProps {
    storageResources: storageResources;
    samlSettings?: SamlSettings;
}

/**
 * Deploys Cognito with an Authenticated & UnAuthenticated Role with a Web and Native client
 */
export class CognitoWebNativeConstruct extends Construct {
    public userPool: cognito.UserPool;
    public webClientUserPool: cognito.UserPoolClient;
    public nativeClientUserPool: cognito.UserPoolClient;
    public samlIdentityProviderName: string;
    public userPoolId: string;
    public identityPoolId: string;
    public webClientId: string;
    public nativeClientId: string;
    public authenticatedRole: iam.Role;
    public superAdminRole: iam.Role;
    public unauthenticatedRole: iam.Role;

    constructor(parent: Construct, name: string, props: CognitoWebNativeConstructProps) {
        super(parent, name);

        const preTokenGeneration = new lambda.DockerImageFunction(
            this,
            "PreTokenGenerationLambda",
            {
                code: lambda.DockerImageCode.fromImageAsset(
                    path.join(__dirname, "../../../backend/"),
                    {
                        cmd: ["backend.handlers.auth.pretokengen.lambda_handler"],
                    }
                ),
                timeout: Duration.minutes(2),
                memorySize: 1000,
                environment: {
                    TABLE_NAME: props.storageResources.dynamo.authEntitiesStorageTable.tableName,
                    ASSET_STORAGE_TABLE_NAME:
                        props.storageResources.dynamo.assetStorageTable.tableName,
                    DATABASE_STORAGE_TABLE_NAME:
                        props.storageResources.dynamo.databaseStorageTable.tableName,
                },
            }
        );
        props.storageResources.dynamo.authEntitiesStorageTable.grantReadWriteData(
            preTokenGeneration
        );

        const message =
            "Hello, Thank you for registering with your instance of Visual Asset Management System! Your verification code is {####}";
        const userPool = new cognito.UserPool(this, "UserPool", {
            selfSignUpEnabled: false,
            autoVerify: { email: true },
            userVerification: {
                emailSubject: "Verify your email with Visual Asset Management System!",
                emailBody: message,
                emailStyle: cognito.VerificationEmailStyle.CODE,
                smsMessage: message,
            },
            passwordPolicy: {
                minLength: 8,
                requireLowercase: true,
                requireUppercase: true,
                requireDigits: true,
                requireSymbols: true,
                tempPasswordValidity: Duration.days(3),
            },
            lambdaTriggers: {
                preTokenGeneration,
            },
            customAttributes: {
                "custom:groups": new cognito.StringAttribute({
                    mutable: true,
                }),
            },
        });

        const cfnUserPool = userPool.node.defaultChild as cognito.CfnUserPool;

        const userPoolAddOnsProperty: cognito.CfnUserPool.UserPoolAddOnsProperty = {
            advancedSecurityMode: "ENFORCED",
        };
        cfnUserPool.userPoolAddOns = userPoolAddOnsProperty;

        const supportedIdentityProviders = [cognito.UserPoolClientIdentityProvider.COGNITO];

        if (props.samlSettings) {
            const userPoolIdentityProviderSaml = new cognito.UserPoolIdentityProviderSaml(
                this,
                "MyUserPoolIdentityProviderSaml",
                {
                    metadata: props.samlSettings.metadata,
                    userPool: userPool,
                    name: props.samlSettings.name,
                    attributeMapping: props.samlSettings.attributeMapping,
                }
            );
            supportedIdentityProviders.push(
                cognito.UserPoolClientIdentityProvider.custom(
                    userPoolIdentityProviderSaml.providerName
                )
            );

            userPool.addDomain("UserPoolDomain", {
                cognitoDomain: {
                    domainPrefix: props.samlSettings.cognitoDomainPrefix,
                },
            });
        }

        const userPoolWebClient = new cognito.UserPoolClient(this, "UserPoolWebClient", {
            generateSecret: false,
            userPool: userPool,
            userPoolClientName: "WebClient",
            supportedIdentityProviders,
        });

        const identityPool = new cognito.CfnIdentityPool(this, "IdentityPool", {
            allowUnauthenticatedIdentities: false,
            cognitoIdentityProviders: [
                {
                    clientId: userPoolWebClient.userPoolClientId,
                    providerName: userPool.userPoolProviderName,
                },
            ],
        });

        const unauthenticatedRole = new iam.Role(this, "DefaultUnauthenticatedRole", {
            assumedBy: new iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                {
                    StringEquals: {
                        "cognito-identity.amazonaws.com:aud": identityPool.ref,
                    },
                    "ForAnyValue:StringLike": {
                        "cognito-identity.amazonaws.com:amr": "unauthenticated",
                    },
                },
                "sts:AssumeRoleWithWebIdentity"
            ),
        });
        unauthenticatedRole.addToPolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: [
                    "mobileanalytics:PutEvents",
                    "cognito-sync:DescribeDataset",
                    "cognito-sync:DescribeIdentityPoolUsage",
                    "cognito-sync:DescribeIdentityUsage",
                    "cognito-sync:GetBulkPublishDetails",
                    "cognito-sync:GetCognitoEvents",
                    "cognito-sync:GetIdentityPoolConfiguration",
                    "cognito-sync:ListDatasets",
                    "cognito-sync:ListIdentityPoolUsage",
                    "cognito-sync:ListRecords",
                ],
                resources: ["*"],
            })
        );

        const authenticatedRole = this.createAuthenticatedRole(
            "DefaultAuthenticatedRole",
            identityPool,
            props
        );
        const superAdminRole = this.createAuthenticatedRole("SuperAdminRole", identityPool, props);
        superAdminRole.addToPolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: [
                    "s3:PutObject",
                    "s3:GetObject",
                    "s3:CreateMultipartUpload",
                    "s3:ListBucket",
                ],
                resources: [
                    props.storageResources.s3.assetBucket.bucketArn,
                    props.storageResources.s3.assetBucket.bucketArn + "/*",
                ],
            })
        );

        const defaultPolicy = new cognito.CfnIdentityPoolRoleAttachment(
            this,
            "IdentityPoolRoleAttachment",
            {
                identityPoolId: identityPool.ref,
                roleMappings: {
                    default: {
                        type: "Token",
                        ambiguousRoleResolution: "AuthenticatedRole",
                        identityProvider: `cognito-idp.${cdk.Stack.of(this).region}.amazonaws.com/${
                            userPool.userPoolId
                        }:${userPoolWebClient.userPoolClientId}`,
                    },
                },
                roles: {
                    unauthenticated: unauthenticatedRole.roleArn,
                    authenticated: authenticatedRole.roleArn,
                },
            }
        );

        // Assign Cfn Outputs
        new cdk.CfnOutput(this, "UserPoolId", {
            value: userPool.userPoolId,
        });
        new cdk.CfnOutput(this, "IdentityPoolId", {
            value: identityPool.ref,
        });
        new cdk.CfnOutput(this, "WebClientId", {
            value: userPoolWebClient.userPoolClientId,
        });

        if (props.samlSettings) {
            new cdk.CfnOutput(this, "SAML_urn", {
                value: `urn:amazon:cognito:sp:${userPool.userPoolId}`,
                description: "SP urn / Audience URI / SP entity ID",
            });
        }

        // Add SSM Parameters
        new ssm.StringParameter(this, "COGNITO_USER_POOL_ID", {
            stringValue: userPool.userPoolId,
        });

        new ssm.StringParameter(this, "COGNITO_IDENTITY_POOL_ID", {
            stringValue: identityPool.ref,
        });

        new ssm.StringParameter(this, "COGNITO_WEB_CLIENT_ID", {
            stringValue: userPoolWebClient.userPoolClientId,
        });

        // assign public properties
        this.userPool = userPool;
        this.webClientUserPool = userPoolWebClient;
        this.authenticatedRole = authenticatedRole;
        this.unauthenticatedRole = unauthenticatedRole;
        this.superAdminRole = superAdminRole;
        this.userPoolId = userPool.userPoolId;
        this.identityPoolId = identityPool.ref;
        this.webClientId = userPoolWebClient.userPoolClientId;
    }

    private createAuthenticatedRole(
        id: string,
        identityPool: cognito.CfnIdentityPool,
        props: CognitoWebNativeConstructProps
    ) {
        const authenticatedRole = new iam.Role(this, id, {
            assumedBy: new iam.FederatedPrincipal(
                "cognito-identity.amazonaws.com",
                {
                    StringEquals: {
                        "cognito-identity.amazonaws.com:aud": identityPool.ref,
                    },
                    "ForAnyValue:StringLike": {
                        "cognito-identity.amazonaws.com:amr": "authenticated",
                    },
                },
                "sts:AssumeRoleWithWebIdentity"
            ),
        });
        authenticatedRole.addToPolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: [
                    "mobileanalytics:PutEvents",
                    "cognito-sync:DescribeDataset",
                    "cognito-sync:DescribeIdentityPoolUsage",
                    "cognito-sync:DescribeIdentityUsage",
                    "cognito-sync:GetBulkPublishDetails",
                    "cognito-sync:GetCognitoEvents",
                    "cognito-sync:GetIdentityPoolConfiguration",
                    "cognito-sync:ListDatasets",
                    "cognito-sync:ListIdentityPoolUsage",
                    "cognito-sync:ListRecords",
                    "cognito-identity:DescribeIdentity",
                    "cognito-identity:DescribeIdentityPool",
                    "cognito-identity:GetCredentialsForIdentity",
                    "cognito-identity:GetId",
                    "cognito-identity:GetIdentityPoolRoles",
                    "cognito-identity:GetOpenIdToken",
                    "cognito-identity:GetOpenIdTokenForDeveloperIdentity",
                    "cognito-identity:GetPrincipalTagAttributeMap",
                    "cognito-identity:ListIdentities",
                    "cognito-identity:ListIdentityPools",
                    "cognito-identity:ListTagsForResource",
                ],
                resources: ["*"],
            })
        );
        authenticatedRole.addToPolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["s3:GetObject"],
                resources: [props.storageResources.s3.assetBucket.bucketArn + "/metadataschema/*"],
            })
        );
        return authenticatedRole;
    }
}
