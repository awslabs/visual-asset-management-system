/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cognito from "aws-cdk-lib/aws-cognito";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as cdk from "aws-cdk-lib";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
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
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    suppressCdkNagLambda,
    setupSecurityAndLoggingEnvironmentAndPermissions,
} from "../../../helper/security";

export interface SamlSettings {
    metadata: cognito.UserPoolIdentityProviderSamlMetadata;
    name: string;
    attributeMapping: cognito.AttributeMapping;
    cognitoDomainPrefix: string;
}

export interface OidcSettings {
    name: string;
    // Login-button label. Consumed by the API layer rather than this construct, but kept here so
    // this interface matches the object in config/oidc-config.ts that is assigned to it.
    displayName?: string;
    cognitoDomainPrefix: string;
    clientId: string;
    // Client secret ARN (secret is retrieved from AWS Secrets Manager at deploy time)
    clientSecretArn: string;
    issuerUrl: string;
    scopes: string[];
    attributeMapping: cognito.AttributeMapping;
    manageDomain: boolean;
}

export interface CognitoWebNativeConstructStackProps extends cdk.StackProps {
    lambdaCommonBaseLayer: LayerVersion;
    storageResources: storageResources;
    config: Config.Config;
    samlSettings?: SamlSettings;
    oidcSettings?: OidcSettings;
}

/** Name of the app client the web application authenticates against. */
export const COGNITO_WEB_CLIENT_NAME = "WebClient";

/** Token lifetimes and authentication flows declared on the web client. */
export function cognitoWebClientTokenAndAuthFlowSettings(config: Config.Config) {
    const credTokenTimeout = Duration.seconds(
        config.app.authProvider.useCognito.credTokenTimeoutSeconds
    );
    return {
        refreshTokenValidity: Duration.hours(24), //AppSec Guidelines Recommendation
        accessTokenValidity: credTokenTimeout,
        idTokenValidity: credTokenTimeout,
        authFlows: {
            userSrp: true,
            custom: true,
            userPassword: config.app.authProvider.useCognito.useUserPasswordAuthFlow,
        },
    };
}

/**
 * The same settings as UpdateUserPoolClient request parameters.
 *
 * That API replaces the whole app client configuration: a parameter the request omits is set back to
 * its Amazon Cognito default (30 days of refresh-token validity, 1 hour of access/ID token validity,
 * and the SRP/custom/refresh auth flows). Any post-deploy update of the web client therefore has to
 * send these alongside whatever it is changing. Durations are expressed in minutes to match what the
 * `UserPoolClient` resource emits, so the two can be compared directly.
 */
export function cognitoWebClientUpdateParameters(config: Config.Config): Record<string, unknown> {
    const settings = cognitoWebClientTokenAndAuthFlowSettings(config);
    return {
        ClientName: COGNITO_WEB_CLIENT_NAME,
        RefreshTokenValidity: settings.refreshTokenValidity.toMinutes(),
        AccessTokenValidity: settings.accessTokenValidity.toMinutes(),
        IdTokenValidity: settings.idTokenValidity.toMinutes(),
        TokenValidityUnits: {
            RefreshToken: "minutes",
            AccessToken: "minutes",
            IdToken: "minutes",
        },
        // Order matches the list cognito.UserPoolClient builds from the same authFlows object.
        ExplicitAuthFlows: [
            ...(settings.authFlows.userPassword ? ["ALLOW_USER_PASSWORD_AUTH"] : []),
            "ALLOW_CUSTOM_AUTH",
            "ALLOW_USER_SRP_AUTH",
            "ALLOW_REFRESH_TOKEN_AUTH",
        ],
    };
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
    public unauthenticatedRole: iam.Role;

    constructor(parent: Construct, name: string, props: CognitoWebNativeConstructStackProps) {
        super(parent, name);

        //Check if GovCloud is enabled and set the handler to v1 instead (GovCloud does not support advanced security mode which can use the v2 pretokengen lambdas)
        const handlerName = props.config.app.govCloud.enabled ? "pretokengenv1" : "pretokengenv2";
        const fun = new lambda.Function(this, handlerName, {
            code: lambda.Code.fromAsset(path.join(__dirname, `../../../../../backend/backend`)),
            handler: `handlers.auth.${handlerName}.lambda_handler`,
            runtime: LAMBDA_PYTHON_RUNTIME,
            layers: [props.lambdaCommonBaseLayer],
            timeout: Duration.minutes(2),
            memorySize: Config.LAMBDA_MEMORY_SIZE,
            environment: {},
        });
        kmsKeyLambdaPermissionAddToResourcePolicy(fun, props.storageResources.encryption.kmsKey);
        setupSecurityAndLoggingEnvironmentAndPermissions(fun, props.storageResources);
        globalLambdaEnvironmentsAndPermissions(fun, props.config);
        suppressCdkNagLambda(fun);

        const messageVerification =
            "Hello, Thank you for registering with your instance of Visual Asset Management System! Your verification code is: {####}";
        const messageInvitation =
            "Hello, You have been registered to join the Visual Asset Management System! Your username is {username} and your temporary password is: {####}";
        const userPool = new cognito.UserPool(this, "UserPool", {
            selfSignUpEnabled: false,
            autoVerify: { email: true },
            //(Non-GovCloud) Plus feature plan enables threat protection (not currently supported by GovCloud cognito)
            featurePlan: props.config.app.govCloud.enabled ? undefined : cognito.FeaturePlan.PLUS,
            mfa: cognito.Mfa.OPTIONAL,
            mfaSecondFactor: {
                otp: true,
                sms: true,
                email: false,
            },
            accountRecovery: cognito.AccountRecovery.PHONE_WITHOUT_MFA_AND_EMAIL,
            userVerification: {
                emailSubject: "Visual Asset Management System - Email Verification",
                emailBody: messageVerification,
                emailStyle: cognito.VerificationEmailStyle.CODE,
                smsMessage: messageVerification,
            },
            userInvitation: {
                emailSubject: "Visual Asset Management System - Registration",
                emailBody: messageInvitation,
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
        //(GovCloud) Add pretokengen lambda trigger (V1) - this will generate claims for only ID token claims (access token will not have claims and can't be used)
        cfnUserPool.lambdaConfig = {
            preTokenGenerationConfig: {
                lambdaArn: fun.functionArn,
                lambdaVersion: props.config.app.govCloud.enabled ? "V1_0" : "V2_0",
            },
        };

        userPool.node.addDependency(fun);
        fun.grantInvoke(Service("COGNITO_IDP").Principal);

        //Only enable threat protection for non-govcloud environments (currently no supported by cognito)
        if (!props.config.app.govCloud.enabled) {
            cfnUserPool.userPoolAddOns = {
                advancedSecurityMode: cognito.StandardThreatProtectionMode.FULL_FUNCTION.valueOf(),
            };
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

        // OIDC federation
        let oidcIdentityProvider: cognito.UserPoolIdentityProviderOidc | undefined;
        if (props.oidcSettings) {
            // UserPoolIdentityProviderOidc.clientSecret takes a plain string, so the SecretValue is
            // unwrapped to its CloudFormation dynamic reference ({{resolve:secretsmanager:...}}).
            // The template carries that reference, not the secret: CloudFormation resolves it during
            // the deploy. Never replace this with a value read at synth time, which would place the
            // secret in the template and in cdk.out.
            const clientSecret = cdk.SecretValue.secretsManager(
                props.oidcSettings.clientSecretArn
            ).unsafeUnwrap();

            oidcIdentityProvider = new cognito.UserPoolIdentityProviderOidc(
                this,
                "MyUserPoolIdentityProviderOidc",
                {
                    userPool: userPool,
                    name: props.oidcSettings.name,
                    clientId: props.oidcSettings.clientId,
                    clientSecret: clientSecret,
                    issuerUrl: props.oidcSettings.issuerUrl,
                    scopes: props.oidcSettings.scopes,
                    attributeMapping: props.oidcSettings.attributeMapping,
                    // How Cognito calls the provider userInfo endpoint. Endpoints themselves are
                    // auto-discovered from <issuerUrl>/.well-known/openid-configuration.
                    attributeRequestMethod: cognito.OidcAttributeRequestMethod.GET,
                }
            );
            supportedIdentityProviders.push(
                cognito.UserPoolClientIdentityProvider.custom(oidcIdentityProvider.providerName)
            );

            // Only create the hosted domain when we are asked to manage it. When the
            // domain was created out-of-band, recreating it would fail the deploy.
            if (props.oidcSettings.manageDomain) {
                userPool.addDomain("UserPoolDomainOidc", {
                    cognitoDomain: {
                        domainPrefix: props.oidcSettings.cognitoDomainPrefix,
                    },
                });
            }
        }

        const userPoolWebClient = new cognito.UserPoolClient(this, "UserPoolWebClient", {
            generateSecret: false,
            userPool: userPool,
            userPoolClientName: COGNITO_WEB_CLIENT_NAME,
            supportedIdentityProviders,
            ...cognitoWebClientTokenAndAuthFlowSettings(props.config),
        });

        // Ensure the web client is created after the OIDC identity provider so it
        // can reference the provider in its supported identity providers list.
        if (oidcIdentityProvider) {
            userPoolWebClient.node.addDependency(oidcIdentityProvider);
        }

        // Classic flow is enabled because using assume_role_with_web_identity to extend auth token timeout
        const identityPool = new cognito.CfnIdentityPool(this, "IdentityPool", {
            allowUnauthenticatedIdentities: false,
            cognitoIdentityProviders: [
                {
                    clientId: userPoolWebClient.userPoolClientId,
                    providerName: `${Service("COGNITO_IDP", false).Endpoint}/${
                        userPool.userPoolId
                    }`,
                },
            ],
            allowClassicFlow: true,
        });

        const cognitoIdentityPrincipal: string = Service("COGNITO_IDENTITY").PrincipalString;
        const cognitoIdentityAudString = cognitoIdentityPrincipal + ":aud";
        const cognitoIdentityAmrString = cognitoIdentityPrincipal + ":amr";
        const unauthenticatedRole = new iam.Role(this, "DefaultUnauthenticatedRole", {
            assumedBy: new iam.FederatedPrincipal(
                cognitoIdentityPrincipal,
                {
                    StringEquals: {
                        [cognitoIdentityAudString]: identityPool.ref,
                    },
                    "ForAnyValue:StringLike": {
                        [cognitoIdentityAmrString]: "unauthenticated",
                    },
                },
                "sts:AssumeRoleWithWebIdentity"
            ),
        });

        const defaultPolicy = new cognito.CfnIdentityPoolRoleAttachment(
            this,
            "IdentityPoolRoleAttachment",
            {
                identityPoolId: identityPool.ref,
                roles: {
                    unauthenticated: unauthenticatedRole.roleArn,
                },
            }
        );

        const cognitoUser = new cognito.CfnUserPoolUser(this, "AdminUser", {
            username: props.config.app.adminUserId,
            userPoolId: userPool.userPoolId,
            desiredDeliveryMediums: ["EMAIL"],
            userAttributes: [
                {
                    name: "email",
                    value: props.config.app.adminEmailAddress,
                },
                {
                    name: "email_verified",
                    value: "True",
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
                value: `https://${props.samlSettings!.cognitoDomainPrefix}.${
                    Service("COGNITO_HOSTED_UI").Endpoint
                }/saml2/idpresponse`,
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
        this.unauthenticatedRole = unauthenticatedRole;
        this.userPoolId = userPool.userPoolId;
        this.identityPoolId = identityPool.ref;
        this.webClientId = userPoolWebClient.userPoolClientId;

        //Nag supressions
        NagSuppressions.addResourceSuppressions(
            userPool,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Intend to use Cognito SMS Role as-is.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            fun,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Not providing IAM wildcard permissions to constraint tables.",
                },
            ],
            true
        );
    }
}
