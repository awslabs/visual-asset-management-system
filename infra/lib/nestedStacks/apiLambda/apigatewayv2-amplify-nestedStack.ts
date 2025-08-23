/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as apigw from "@aws-cdk/aws-apigatewayv2-alpha";
import * as apigwAuthorizers from "@aws-cdk/aws-apigatewayv2-authorizers-alpha";
import * as cdk from "aws-cdk-lib";
import * as Config from "../../../config/config";
import { samlSettings } from "../../../config/saml-config";
import {
    AmplifyConfigLambdaConstruct,
    AmplifyConfigLambdaConstructProps,
} from "./constructs/amplify-config-lambda-construct";
import {
    VamsVersionLambdaConstruct,
    VamsVersionLambdaConstructProps,
} from "./constructs/vams-version-lambda-construct";
import { Construct } from "constructs";
import { NestedStack } from "aws-cdk-lib";
import { Service } from "../../helper/service-helper";
import { authResources } from "../auth/authBuilder-nestedStack";
import { storageResources } from "../storage/storageBuilder-nestedStack";

export interface ApiGatewayV2AmplifyNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    authResources: authResources;
    storageResources: storageResources;
}

/**
 * Default input properties
 */
const defaultProps: Partial<ApiGatewayV2AmplifyNestedStackProps> = {
    //stackName: "",
    //env: {},
};

/**
 * Deploys Api gateway
 *
 * CORS: allowed origins for local development:
 * - https://example.com:3000, http://example.com:3000
 *
 * Creates:
 * - ApiGatewayV2 HttpApi
 */
export class ApiGatewayV2AmplifyNestedStack extends NestedStack {
    /**
     * Returns the ApiGatewayV2 instance to attach lambdas or other routes
     */
    public apiGatewayV2: apigw.HttpApi;
    public apiEndpoint: string;

    constructor(parent: Construct, name: string, props: ApiGatewayV2AmplifyNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        let apiGatewayAuthorizer = undefined;

        //Setup Gateway Authorizer
        if (props.config.app.authProvider.useCognito.enabled) {
            // init cognito authorizer
            apiGatewayAuthorizer = new apigwAuthorizers.HttpUserPoolAuthorizer(
                "DefaultCognitoAuthorizer",
                props.authResources.cognito.userPool,
                {
                    userPoolClients: [props.authResources.cognito.webClientUserPool],
                    identitySource: ["$request.header.Authorization"],
                }
            );
        } else if (props.config.app.authProvider.useExternalOAuthIdp.enabled) {
            //init external OATH IDP JWT authorizer

            apiGatewayAuthorizer = new apigwAuthorizers.HttpJwtAuthorizer(
                "DefaultJwtAuthorizer",
                props.config.app.authProvider.useExternalOAuthIdp.lambdaAuthorizorJWTIssuerUrl,
                {
                    jwtAudience: [
                        props.config.app.authProvider.useExternalOAuthIdp
                            .lambdaAuthorizorJWTAudience,
                    ],
                    identitySource: ["$request.header.Authorization"],
                }
            );
        }

        // init api gateway
        const api = new apigw.HttpApi(this, "Api", {
            apiName: `${props.stackName}Api`,
            corsPreflight: {
                allowHeaders: [
                    "Authorization",
                    "Content-Type",
                    "Origin",
                    "Range",
                    "X-Amz-Date",
                    "X-Api-Key",
                    "X-Amz-Security-Token",
                    "X-Amz-User-Agent",
                ],
                allowMethods: [
                    apigw.CorsHttpMethod.OPTIONS,
                    apigw.CorsHttpMethod.GET,
                    apigw.CorsHttpMethod.PUT,
                    apigw.CorsHttpMethod.POST,
                    apigw.CorsHttpMethod.PATCH,
                    apigw.CorsHttpMethod.DELETE,
                ],
                // allow origins for development.  no origin is needed for cloudfront
                //allowOrigins: ["https://example.com:3000", "http://example.com:3000"],
                //allowCredentials: true,
                allowCredentials: false,
                allowOrigins: ["*"],
                exposeHeaders: ["Access-Control-Allow-Origin"],
                maxAge: cdk.Duration.hours(1),
            },
            defaultAuthorizer: apiGatewayAuthorizer,
        });

        //Always use non-FIPS URL in non-GovCloud. All endpoints in GovCloud are FIPS-compliant already
        //https://docs.aws.amazon.com/govcloud-us/latest/UserGuide/govcloud-abp.html
        const apiEndpoint = `${api.httpApiId}.${Service("EXECUTE_API", false).Endpoint}`;
        this.apiEndpoint = apiEndpoint;

        //Generate Global CSP policy
        let authDomain = "";

        if (props.config.app.authProvider.useCognito.useSaml) {
            authDomain = `https://${samlSettings.cognitoDomainPrefix}.auth.${props.config.env.region}.amazoncognito.com`;
        } else if (props.config.app.authProvider.useExternalOAuthIdp.enabled) {
            authDomain = props.config.app.authProvider.useExternalOAuthIdp.idpAuthProviderUrl;
        }

        //Setup Initial Amplify Config
        const amplifyConfigProps: AmplifyConfigLambdaConstructProps = {
            ...props,
            api: api,
            apiUrl: `https://${this.apiEndpoint}/`,
            authResources: props.authResources,
            region: props.config.env.region,
        };

        if (props.config.app.authProvider.useCognito.useSaml) {
            amplifyConfigProps.cognitoFederatedConfig = {
                customCognitoAuthDomain: authDomain,
                customFederatedIdentityProviderName: samlSettings.name,
                // if necessary, the callback urls can be determined here and passed to the UI through the config endpoint
                // redirectSignIn: callbackUrls[0],
                // redirectSignOut: callbackUrls[0],
            };
        }

        const amplifyConfigFn = new AmplifyConfigLambdaConstruct(
            this,
            "AmplifyConfigNestedStack",
            amplifyConfigProps
        );

        //Setup Version
        const vamsVersionProps: VamsVersionLambdaConstructProps = {
            ...props,
            api: api,
        };

        const vamsVersionFn = new VamsVersionLambdaConstruct(
            this,
            "VamsVersionNestedStack",
            vamsVersionProps
        );

        // export any cf outputs
        new cdk.CfnOutput(this, "GatewayUrl", {
            value: `https://${this.apiEndpoint}/`,
        });

        // assign public properties
        this.apiGatewayV2 = api;
    }
}
