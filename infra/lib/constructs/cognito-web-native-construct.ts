/**
 * Copyright 2021 Amazon.com, Inc. and its affiliates. All Rights Reserved.
 *
 * Licensed under the Amazon Software License (the "License").
 * You may not use this file except in compliance with the License.
 * A copy of the License is located at
 *
 *   http://aws.amazon.com/asl/
 *
 * or in the "license" file accompanying this file. This file is distributed
 * on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
 * express or implied. See the License for the specific language governing
 * permissions and limitations under the License.
 */

import * as cognito from "@aws-cdk/aws-cognito";
import * as iam from "@aws-cdk/aws-iam";
import * as ssm from "@aws-cdk/aws-ssm";
import * as cdk from "@aws-cdk/core";

import {storageResources} from "../storage-builder";

export interface CognitoWebNativeConstructProps extends cdk.StackProps {
    storageResources: storageResources
}

/**
 * Default input properties
 */
const defaultProps: Partial<CognitoWebNativeConstructProps> = {
    stackName: "",
    env: {}
};

/**
 * Deploys Cognito with an Authenticated & UnAuthenticated Role with a Web and Native client
 */
export class CognitoWebNativeConstruct extends cdk.Construct {
    public userPool: cognito.UserPool;
    public webClientUserPool: cognito.UserPoolClient;
    public nativeClientUserPool: cognito.UserPoolClient;
    public userPoolId: string;
    public identityPoolId: string;
    public webClientId: string;
    public nativeClientId: string;
    public authenticatedRole: iam.Role;
    public unauthenticatedRole: iam.Role;

    constructor(parent: cdk.Construct, name: string, props: CognitoWebNativeConstructProps) {
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
        });

        const cfnUserPool = userPool.node.defaultChild as cognito.CfnUserPool;

        cfnUserPool.policies = {
            passwordPolicy: {
                minimumLength: 8,
                requireLowercase: false,
                requireNumbers: false,
                requireUppercase: false,
                requireSymbols: false,
            },
        };

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
                    cognito.OAuthScope.OPENID
                ],
                flows: {
                    authorizationCodeGrant: true
                }
            },
            supportedIdentityProviders: [
                cognito.UserPoolClientIdentityProvider.COGNITO,
            ],
            userPool: userPool,
            generateSecret: false,
            userPoolClientName: `aws_appClient_cfn${cdk.Aws.STACK_NAME}`
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
                    "cognito-sync:Describe*",
                    "cognito-sync:Get*",
                    "cognito-sync:List*"
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
                    "cognito-sync:Describe*",
                    "cognito-sync:Get*",
                    "cognito-sync:List*",
                    "cognito-identity:Describe*",
                    "cognito-identity:Get*",
                    "cognito-identity:List*"
                ],
                resources: ["*"],
            })
        );
        authenticatedRole.addToPolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: [
                    "s3:PutObject",
                    "s3:GetObject"
                ],
                resources: [
                    props.storageResources.s3.assetBucket.bucketArn,
                    props.storageResources.s3.assetBucket.bucketArn + "/*"
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
