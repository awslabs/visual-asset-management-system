/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as apigw from "@aws-cdk/aws-apigatewayv2-alpha";
import * as apigwAuthorizers from "@aws-cdk/aws-apigatewayv2-authorizers-alpha";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as cloudfrontOrigins from "aws-cdk-lib/aws-cloudfront-origins";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";

export interface ApiGatewayV2CloudFrontProps extends cdk.StackProps {
    /**
     * The Cognito UserPool to use for the default authorizer
     */
    userPool: cognito.UserPool;
    /**
     * The Cognito UserPoolClient to use for the default authorizer
     */
    userPoolClient: cognito.UserPoolClient;
}

/**
 * Default input properties
 */
const defaultProps: Partial<ApiGatewayV2CloudFrontProps> = {
    stackName: "",
    env: {},
};

/**
 * Deploys Api gateway proxied through a CloudFront distribution at route `/api`
 *
 * Any Api's attached to the gateway should be located at `/api/*` so that requests are correctly proxied.
 * Make sure Api's return the header `"Cache-Control" = "no-cache, no-store"` or CloudFront will cache responses
 *
 * CORS: allowed origins for local development:
 * - https://example.com:3000, http://example.com:3000
 *
 * Creates:
 * - ApiGatewayV2 HttpApi
 */
export class ApiGatewayV2CloudFrontConstruct extends Construct {
    /**
     * Returns the ApiGatewayV2 instance to attach lambdas or other routes
     */
    public apiGatewayV2: apigw.HttpApi;
    public apiUrl: string;

    constructor(parent: Construct, name: string, props: ApiGatewayV2CloudFrontProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        // init cognito authorizer
        const cognitoAuth = new apigwAuthorizers.HttpUserPoolAuthorizer(
            "DefaultCognitoAuthorizer",
            props.userPool,
            {
                userPoolClients: [props.userPoolClient],
                identitySource: ["$request.header.Authorization"],
            }
        );

        // init api gateway
        const api = new apigw.HttpApi(this, "Api", {
            apiName: `${props.stackName}Api`,
            corsPreflight: {
                allowHeaders: [
                    "Authorization",
                    "Content-Type",
                    "Origin",
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
            defaultAuthorizer: cognitoAuth,
        });

        const apiUrl = `${api.httpApiId}.execute-api.${cdk.Stack.of(this).region}.amazonaws.com`;
        this.apiUrl = apiUrl;

        // export any cf outputs
        new cdk.CfnOutput(this, "GatewayUrl", {
            value: `https://${apiUrl}`,
        });

        // assign public properties
        this.apiGatewayV2 = api;
    }

    /**
     * Adds a proxy route from CloudFront /api to the api gateway url
     * @param cloudFrontDistribution
     * @param apiUrl
     */
    public addBehaviorToCloudFrontDistribution(cloudFrontDistribution: cloudfront.Distribution) {
        cloudFrontDistribution.addBehavior(
            "/api/*",
            new cloudfrontOrigins.HttpOrigin(this.apiUrl, {
                originSslProtocols: [cloudfront.OriginSslPolicy.TLS_V1_2],
                protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
            }),
            {
                cachePolicy: new cloudfront.CachePolicy(this, "CachePolicy", {
                    // required or CloudFront will strip the Authorization token from the request.
                    // must be in the cache policy
                    headerBehavior: cloudfront.CacheHeaderBehavior.allowList("Authorization"),
                    enableAcceptEncodingGzip: true,
                }),
                originRequestPolicy: new cloudfront.OriginRequestPolicy(
                    this,
                    "OriginRequestPolicy",
                    {
                        // required or CloudFront will strip all query strings off the request
                        queryStringBehavior: cloudfront.OriginRequestQueryStringBehavior.all(),
                    }
                ),
                allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
                viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            }
        );
    }
}
