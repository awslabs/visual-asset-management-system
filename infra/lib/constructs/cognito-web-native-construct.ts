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
import { NagSuppressions } from "cdk-nag";
import { Duration } from "aws-cdk-lib";

export interface CognitoWebNativeConstructProps extends cdk.StackProps {
    storageResources: storageResources;
}

/**
 * Default input properties
 */
const defaultProps: Partial<CognitoWebNativeConstructProps> = {
    stackName: "",
    env: {},
};

/**
 * Deploys Cognito with an Authenticated & UnAuthenticated Role with a Web and Native client
 */
export class CognitoWebNativeConstruct extends Construct {
    public userPool: cognito.UserPool;
    public webClientUserPool: cognito.UserPoolClient;
    public nativeClientUserPool: cognito.UserPoolClient;
    public userPoolId: string;
    public identityPoolId: string;
    public webClientId: string;
    public nativeClientId: string;
    public authenticatedRole: iam.Role;
    public unauthenticatedRole: iam.Role;

    constructor(parent: Construct, name: string, props: CognitoWebNativeConstructProps) {
        super(parent, name);

        const userPool = new cognito.UserPool(this, "UserPool", {
            selfSignUpEnabled: true,
            autoVerify: { email: true },
            userVerification: {
                emailSubject: "Verify your email the app!",
                emailBody:
                    "Hello {username}, Thanks for signing up to the app! Your verification code is {####}",
                emailStyle: cognito.VerificationEmailStyle.CODE,
                smsMessage:
                    "Hello {username}, Thanks for signing up to app! Your verification code is {####}",
            },
            passwordPolicy: {
                minLength: 8,
                requireLowercase: true,
                requireUppercase: true,
                requireDigits: true,
                requireSymbols: true,
                tempPasswordValidity: Duration.days(3),
            },
        });

        const cfnUserPool = userPool.node.defaultChild as cognito.CfnUserPool;

        const userPoolAddOnsProperty: cognito.CfnUserPool.UserPoolAddOnsProperty = {
            advancedSecurityMode: "ENFORCED",
        };
        cfnUserPool.userPoolAddOns = userPoolAddOnsProperty;

        const userPoolWebClient = new cognito.UserPoolClient(this, "UserPoolWebClient", {
            generateSecret: false,
            userPool: userPool,
            userPoolClientName: "WebClient",
        });

        const userPoolNativeClient = new cognito.UserPoolClient(this, "UserPoolNativeClient", {
            generateSecret: true,
            userPool: userPool,
            userPoolClientName: "NativeClient",
        });

        const userPoolAppClient = new cognito.UserPoolClient(this, "UserPoolAppClient", {
            oAuth: {
                callbackUrls: ["https://aws.amazon.com/cognito/"],
                logoutUrls: ["https://aws.amazon.com/cognito/"],
                scopes: [
                    cognito.OAuthScope.PHONE,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.OPENID,
                ],
                flows: {
                    authorizationCodeGrant: true,
                },
            },
            supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.COGNITO],
            userPool: userPool,
            generateSecret: false,
            userPoolClientName: `aws_appClient_cfn${cdk.Aws.STACK_NAME}`,
        });

        const identityPool = new cognito.CfnIdentityPool(this, "IdentityPool", {
            allowUnauthenticatedIdentities: false,
            cognitoIdentityProviders: [
                {
                    clientId: userPoolWebClient.userPoolClientId,
                    providerName: userPool.userPoolProviderName,
                },
                {
                    clientId: userPoolNativeClient.userPoolClientId,
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

        const authenticatedRole = new iam.Role(this, "DefaultAuthenticatedRole", {
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
                actions: ["s3:PutObject", "s3:GetObject"],
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
        new cdk.CfnOutput(this, "NativeClientId", {
            value: userPoolNativeClient.userPoolClientId,
        });

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

        new ssm.StringParameter(this, "COGNITO_NATIVE_CLIENT_ID", {
            stringValue: userPoolNativeClient.userPoolClientId,
        });

        // assign public properties
        this.userPool = userPool;
        this.webClientUserPool = userPoolWebClient;
        this.nativeClientUserPool = userPoolNativeClient;
        this.authenticatedRole = authenticatedRole;
        this.unauthenticatedRole = unauthenticatedRole;
        this.userPoolId = userPool.userPoolId;
        this.identityPoolId = identityPool.ref;
        this.webClientId = userPoolWebClient.userPoolClientId;
        this.nativeClientId = userPoolNativeClient.userPoolClientId;
    }
}
