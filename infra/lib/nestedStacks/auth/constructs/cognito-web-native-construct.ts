/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cognito from "aws-cdk-lib/aws-cognito";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as cdk from "aws-cdk-lib";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import { Construct } from "constructs";
import { Duration, NestedStack } from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../config/config";
import { NagSuppressions } from "cdk-nag";
import { Service } from "../../../helper/service-helper";
import * as Config from "../../../../config/config";
import { handler } from "../../search/constructs/schemaDeploy/provisioned/deployschemaprovisioned";

export interface SamlSettings {
    metadata: cognito.UserPoolIdentityProviderSamlMetadata;
    name: string;
    attributeMapping: cognito.AttributeMapping;
    cognitoDomainPrefix: string;
}

export interface CognitoWebNativeConstructStackProps extends cdk.StackProps {
    lambdaCommonBaseLayer: LayerVersion;
    storageResources: storageResources;
    config: Config.Config;
    samlSettings?: SamlSettings;
}

/**
 * Deploys Cognito with an Authenticated & UnAuthenticated Role with a Web and Native client
 */
export class CognitoWebNativeConstructStack extends Construct {
    public userPool: cognito.UserPool;
    public webClientUserPool: cognito.UserPoolClient;
    public nativeClientUserPool: cognito.UserPoolClient;
    public samlIdentityProviderName: string;
    public userPoolId: string;
    public identityPoolId: string;
    public webClientId: string;
    public nativeClientId: string;
    public authenticatedRole: iam.Role;
    public unauthenticatedRole: iam.Role;

    constructor(parent: Construct, name: string, props: CognitoWebNativeConstructStackProps) {
        super(parent, name);

        //Check if GovCloud is enabled and set the handler to v1 instead (GovCloud does not support advanced security mode which can use the v2 pretokengen lambdas)
        const handlerName = props.config.app.govCloud.enabled? "pretokengenv1" : "pretokengenv2";
        const preTokenGeneration = new lambda.Function(this, handlerName, {
            code: lambda.Code.fromAsset(path.join(__dirname, `../../../../../backend/backend`)),
            handler: `handlers.auth.${handlerName}.lambda_handler`,
            runtime: LAMBDA_PYTHON_RUNTIME,
            layers: [props.lambdaCommonBaseLayer],
            timeout: Duration.minutes(2),
            memorySize: Config.LAMBDA_MEMORY_SIZE,
            environment: {
                AUTH_TABLE_NAME: props.storageResources.dynamo.authEntitiesStorageTable.tableName,
                USER_ROLES_TABLE_NAME:
                    props.storageResources.dynamo.userRolesStorageTable.tableName,
                ASSET_STORAGE_TABLE_NAME: props.storageResources.dynamo.assetStorageTable.tableName,
                DATABASE_STORAGE_TABLE_NAME:
                    props.storageResources.dynamo.databaseStorageTable.tableName,
            },
        });
        props.storageResources.dynamo.authEntitiesStorageTable.grantReadWriteData(
            preTokenGeneration
        );
        props.storageResources.dynamo.userRolesStorageTable.grantReadData(preTokenGeneration);

        const message =
            "Hello, Thank you for registering with your instance of Visual Asset Management System! Your verification code is:  {####}  ";
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
            customAttributes: {
                "custom:groups": new cognito.StringAttribute({
                    mutable: true,
                }),
            },
        });

        const cfnUserPool = userPool.node.defaultChild as cognito.CfnUserPool;

        //(Non-GovCloud) Add pretokengen lambda trigger (V2) - this will generate claims for both Access and ID token claims
        //(GovCloud) Add pretokengen lambda trigger (V1) - this will generate claims for only Access token claims (ID token will not have claims and can't be used)
        cfnUserPool.lambdaConfig = {
            preTokenGenerationConfig: {
                lambdaArn: preTokenGeneration.functionArn,
                lambdaVersion: props.config.app.govCloud.enabled? "V1_0" : "V2_0",
            },
        };

        userPool.node.addDependency(preTokenGeneration);
        preTokenGeneration.grantInvoke(Service("COGNITO_IDP").Principal);

        //Only enable advanced security for non-govcloud environments (currently no supported by cognito)
        if (!props.config.app.govCloud.enabled) {
            const userPoolAddOnsProperty: cognito.CfnUserPool.UserPoolAddOnsProperty = {
                advancedSecurityMode: "ENFORCED",
            };
            cfnUserPool.userPoolAddOns = userPoolAddOnsProperty;
        }

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
            refreshTokenValidity: Duration.hours(24), //AppSec Guidelines Recommendation
            accessTokenValidity: cdk.Duration.seconds(
                props.config.app.authProvider.credTokenTimeoutSeconds
            ),
            idTokenValidity: cdk.Duration.seconds(
                props.config.app.authProvider.credTokenTimeoutSeconds
            ),
            supportedIdentityProviders,
            authFlows: {
                userSrp: true,
                custom: true,
                userPassword: props.config.app.authProvider.useCognito.useUserPasswordAuthFlow,
            },
        });

        // Classic flow is enabled because using assume_role_with_web_identity to extend auth token timeout
        const identityPool = new cognito.CfnIdentityPool(this, "IdentityPool", {
            allowUnauthenticatedIdentities: false,
            cognitoIdentityProviders: [
                {
                    clientId: userPoolWebClient.userPoolClientId,
                    providerName: userPool.userPoolProviderName,
                },
            ],
            allowClassicFlow: true,
        });

        const cognitoIdentityPrincipal: string = Service("COGNITO_IDENTITY").PrincipalString;
        const unauthenticatedRole = new iam.Role(this, "DefaultUnauthenticatedRole", {
            assumedBy: new iam.FederatedPrincipal(
                cognitoIdentityPrincipal,
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

        const authenticatedRole = this.createAuthenticatedRole(
            "DefaultAuthenticatedRole",
            identityPool,
            props
        );

        const defaultPolicy = new cognito.CfnIdentityPoolRoleAttachment(
            this,
            "IdentityPoolRoleAttachment",
            {
                identityPoolId: identityPool.ref,
                // Disabled due to using Classic Auth Flow which doesn't support roleMappings
                // Instead this should be handled in in s3scopedaccess
                /*
                roleMappings: {
                    default: {
                        type: "Token",
                        ambiguousRoleResolution: "AuthenticatedRole",
                        identityProvider: `${Service("COGNITO_IDP", false).Endpoint}/${
                            //Don't use fips endpoints here due to RoleMapping ProviderName error
                            userPool.userPoolId
                        }:${userPoolWebClient.userPoolClientId}`,
                    },
                },
                */
                roles: {
                    unauthenticated: unauthenticatedRole.roleArn,
                    authenticated: authenticatedRole.roleArn,
                },
            }
        );

        const cognitoUser = new cognito.CfnUserPoolUser(this, "AdminUser", {
            username: props.config.app.adminEmailAddress,
            userPoolId: userPool.userPoolId,
            desiredDeliveryMediums: ["EMAIL"],
            userAttributes: [
                {
                    name: "email",
                    value: props.config.app.adminEmailAddress,
                },
            ],
        });

        // Assign Cfn Outputs
        new cdk.CfnOutput(this, "AuthCognito_UserPoolId", {
            value: userPool.userPoolId,
        });
        new cdk.CfnOutput(this, "AuthCognito_IdentityPoolId", {
            value: identityPool.ref,
        });
        new cdk.CfnOutput(this, "AuthCognito_WebClientId", {
            value: userPoolWebClient.userPoolClientId,
        });

        if (props.samlSettings) {
            new cdk.CfnOutput(this, "AuthCognito_SAML_urn", {
                value: `urn:amazon:cognito:sp:${userPool.userPoolId}`,
                description: "SP urn / Audience URI / SP entity ID",
            });
        }

        if (props.config.app.authProvider.useCognito.useSaml && props.samlSettings) {
            const samlIdpResponseUrl = new cdk.CfnOutput(this, "AuthCognito_SAML_IdpResponseUrl", {
                value: `https://${props.samlSettings!.cognitoDomainPrefix}.auth.${
                    props.config.env.region
                }.amazoncognito.com/saml2/idpresponse`,
                description: "SAML IdP Response URL",
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
        this.userPoolId = userPool.userPoolId;
        this.identityPoolId = identityPool.ref;
        this.webClientId = userPoolWebClient.userPoolClientId;
    }

    private createAuthenticatedRole(
        id: string,
        identityPool: cognito.CfnIdentityPool,
        props: CognitoWebNativeConstructStackProps
    ) {
        const cognitoIdentityPrincipal: string = Service("COGNITO_IDENTITY").PrincipalString;
        const authenticatedRole = new iam.Role(this, id, {
            assumedBy: new iam.FederatedPrincipal(
                cognitoIdentityPrincipal,
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
            maxSessionDuration: cdk.Duration.seconds(
                props.config.app.authProvider.credTokenTimeoutSeconds
            ),
        });

        return authenticatedRole;
    }
}
