/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { LAMBDA_NODE_RUNTIME } from "../../../../config/config";
import { Construct } from "constructs";
import { Service } from "../../../helper/service-helper";
import { authResources } from "../../auth/authBuilder-nestedStack";
import * as Config from "../../../../config/config";
import { suppressCdkNagLambda } from "../../../helper/security";

/**
 * Additional configuration needed to use federated identities
 */
export interface AmplifyConfigFederatedIdentityProps {
    /**
     * The name of the federated identity provider.
     */
    customFederatedIdentityProviderName: string;
    /**
     * The cognito auth domain
     */
    customCognitoAuthDomain: string;
    /**
     * redirect signin url
     */
    redirectSignIn?: string;
    /**
     * redirect signout url
     */
    redirectSignOut?: string;
}

interface InlineLambdaProps {
    /**
     * The ApiGatewayV2 HttpApi to attach the lambda
     */
    api: string;
    /**
     * region
     */
    region: string;

    /**
     * The Cognito UserPoolId to authenticate users in the front-end
     */
    cognitoUserPoolId: string;
    /**
     * The Cognito AppClientId to authenticate users in the front-end
     */
    cognitoAppClientId: string;
    /**
     * The Cognito IdentityPoolId to authenticate users in the front-end
     */
    cognitoIdentityPoolId: string;

    /**
     * Partition-aware Cognito user pool (IDP) endpoint for the front-end.
     * Required because Amplify JS resolves only the `aws` / `aws-cn` partitions and
     * would otherwise build a `.amazonaws.com` host in the EU Sovereign Cloud.
     */
    cognitoUserPoolEndpoint: string;

    /**
     * Additional configuration needed for federated auth
     */
    cognitoFederatedConfig?: AmplifyConfigFederatedIdentityProps;

    /**
     * External OAUTH IDP URL Configuration
     */
    externalOAuthIdpURL?: string;

    /**
     * External OAUTH IDP ClientID Configuration
     */
    externalOAuthIdpClientId?: string;

    /**
     * External OAUTH IDP Scope Configuration
     */
    externalOAuthIdpScope?: string;

    /**
     * External OAUTH IDP Scope attribute for MFA Configuration
     */
    externalOAuthIdpScopeMfa?: string;

    /**
     * External OAUTH IDP Token Endpoint Configuration
     */
    externalOAuthIdpTokenEndpoint?: string;

    /**
     * External OAUTH IDP Authorization Endpoint Configuration
     */
    externalOAuthIdpAuthorizationEndpoint?: string;

    /**
     * External OAUTH IDP Discovery Endpoint Configuration
     */
    externalOAuthIdpDiscoveryEndpoint?: string;

    /**
     * Name of deployed stack
     */
    stackName: string;

    /**
     * Content Security Policy to apply (generally for ALB deployment where CSP is not injected)
     */
    contentSecurityPolicy?: string;

    /**
     * HTML banner message to be displayed at the top of all web UI pages
     */
    bannerHtmlMessage?: string;
}

export interface AmplifyConfigLambdaConstructProps extends cdk.StackProps {
    /**
     * Main Configuration Provider
     */
    config: Config.Config;

    /**
     * The AuthResources Provider
     */
    authResources: authResources;
    /**
     * The ApiGatewayV2 HttpApi URL to attach the lambda (optional; may be empty at construct time; will be set via env var at runtime)
     */
    apiUrl?: string;
    /**
     * region
     */
    region: string;
    /**
     * Additional configuration needed for federated auth
     */
    cognitoFederatedConfig?: AmplifyConfigFederatedIdentityProps;

    /**
     * Content Security Policy to apply at the react level [none headers passed from static webpage service] (generally not used as already provided in Cloudfront and ALB deployment)
     */
    contentSecurityPolicy?: string;
}

/**
 * Builds the /api/amplify-config Lambda function. Route registration is handled by the REST API builder.
 * The API URL is read from process.env.API_URL at runtime (set after the API exists).
 */
export class AmplifyConfigLambdaConstruct extends Construct {
    public readonly lambdaFn: lambda.Function;

    constructor(parent: Construct, name: string, props: AmplifyConfigLambdaConstructProps) {
        super(parent, name);

        props = { ...props };

        this.lambdaFn = new lambda.Function(this, "AmplifyConfigLambda", {
            runtime: LAMBDA_NODE_RUNTIME,
            handler: "index.handler",
            code: lambda.Code.fromInline(
                this.getJavascriptInlineFunction({
                    region: props.region,
                    api: props.apiUrl || "",
                    cognitoUserPoolId: props.config.app.authProvider.useCognito.enabled
                        ? props.authResources.cognito.userPoolId
                        : "undefined",
                    cognitoAppClientId: props.config.app.authProvider.useCognito.enabled
                        ? props.authResources.cognito.webClientId
                        : "undefined",
                    cognitoIdentityPoolId: props.config.app.authProvider.useCognito.enabled
                        ? props.authResources.cognito.identityPoolId
                        : "undefined",
                    // Amplify JS only knows the `aws` and `aws-cn` partitions, so it resolves
                    // every region to the commercial `.amazonaws.com` suffix. In the EU
                    // Sovereign Cloud the correct suffix is `.amazonaws.eu`, so the frontend
                    // must be given explicit endpoints. Service() is partition-aware and
                    // already backs the CSP allow-list, so this stays correct in every
                    // partition (commercial and GovCloud keep resolving to .amazonaws.com).
                    cognitoUserPoolEndpoint: props.config.app.authProvider.useCognito.enabled
                        ? `https://${Service("COGNITO_IDP", false).Endpoint}`
                        : "undefined",
                    cognitoFederatedConfig: props.cognitoFederatedConfig,
                    externalOAuthIdpURL:
                        props.config.app.authProvider.useExternalOAuthIdp.idpAuthProviderUrl ||
                        "undefined",
                    externalOAuthIdpClientId:
                        props.config.app.authProvider.useExternalOAuthIdp.idpAuthClientId ||
                        "undefined",
                    externalOAuthIdpScope:
                        props.config.app.authProvider.useExternalOAuthIdp.idpAuthProviderScope ||
                        "undefined",
                    externalOAuthIdpScopeMfa:
                        props.config.app.authProvider.useExternalOAuthIdp.idpAuthProviderScopeMfa ||
                        "undefined",
                    externalOAuthIdpTokenEndpoint:
                        props.config.app.authProvider.useExternalOAuthIdp
                            .idpAuthProviderTokenEndpoint || "undefined",
                    externalOAuthIdpAuthorizationEndpoint:
                        props.config.app.authProvider.useExternalOAuthIdp
                            .idpAuthProviderAuthorizationEndpoint || "undefined",
                    externalOAuthIdpDiscoveryEndpoint:
                        props.config.app.authProvider.useExternalOAuthIdp
                            .idpAuthProviderDiscoveryEndpoint || "undefined",
                    stackName: props.stackName!,
                    contentSecurityPolicy: "",
                    bannerHtmlMessage: props.config.app.webUi.optionalBannerHtmlMessage || "",
                })
            ),
            timeout: cdk.Duration.seconds(15),
        });

        // add lambda policies
        this.lambdaFn.grantInvoke(Service("APIGATEWAY").Principal);

        suppressCdkNagLambda(this.lambdaFn);
    }

    private getJavascriptInlineFunction(props: InlineLambdaProps) {
        const resp = JSON.stringify(props);

        return `
            exports.handler = async function(event, context) {
                const config = ${resp};
                // Derive the API base URL from the invoking request rather than from a
                // CDK reference to the REST API. Referencing the API id at deploy time
                // would create an Api <-> Lambda circular dependency (this Lambda is
                // itself an integration target in the API spec). The REST API stage URL
                // is reconstructed from the request context: protocol + host + stage.
                try {
                    var rc = (event && event.requestContext) || {};
                    var host = rc.domainName
                        || (event && event.headers && (event.headers.Host || event.headers.host))
                        || "";
                    if (host) {
                        var stageSeg = rc.stage ? ("/" + rc.stage) : "";
                        config.api = "https://" + host + stageSeg + "/";
                    }
                } catch (e) {
                    // Fall back to the build-time value if request context is unavailable.
                }
                return {
                    headers: {
                        'Content-Type': 'application/json',
                        // REST API returns the Lambda response verbatim (no auto-CORS). Under
                        // ALB fronting this endpoint is fetched cross-origin (the ALB
                        // 301-redirects /api/* to the execute-api host), so the response must
                        // carry the CORS origin header for the browser to read it.
                        'Access-Control-Allow-Origin': '*'
                    },
                    statusCode: 200,
                    body: JSON.stringify(config),
                };
            };
        `;
    }
}
