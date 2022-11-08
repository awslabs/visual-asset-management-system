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

import * as apigw from "@aws-cdk/aws-apigatewayv2";
import * as apigwAuthorizers from "@aws-cdk/aws-apigatewayv2-authorizers";
import * as cloudfront from "@aws-cdk/aws-cloudfront";
import * as cloudfrontOrigins from "@aws-cdk/aws-cloudfront-origins";
import * as cognito from "@aws-cdk/aws-cognito";
import * as cdk from "@aws-cdk/core";

export interface ApiGatewayV2CloudFrontProps extends cdk.StackProps {
    /**
     * The Cognito UserPool to use for the default authorizer
     */
    userPool: cognito.UserPool;
    /**
     * The Cognito UserPoolClient to use for the default authorizer
     */
    userPoolClient: cognito.UserPoolClient;
    /**
     * The CloudFront Distribution to attach the `/api/*` behavior
     */
    cloudFrontDistribution: cloudfront.Distribution;
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
export class ApiGatewayV2CloudFrontConstruct extends cdk.Construct {
    /**
     * Returns the ApiGatewayV2 instance to attach lambdas or other routes
     */
    public apiGatewayV2: apigw.HttpApi;

    constructor(parent: cdk.Construct, name: string, props: ApiGatewayV2CloudFrontProps) {
        super(parent, name);

        props = {...defaultProps, ...props};

        // init cognito authorizer
        const cognitoAuth = new apigwAuthorizers.HttpUserPoolAuthorizer(
            props.stackName + "DefaultCognitoAuthorizer", 
            props.userPool, 
            {
                userPoolClients: [props.userPoolClient],
                identitySource: ['$request.header.Authorization']
        });

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
                    apigw.CorsHttpMethod.DELETE
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
        this.addBehaviorToCloudFrontDistribution(      
            props.cloudFrontDistribution, 
            apiUrl);

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
    private addBehaviorToCloudFrontDistribution(
        cloudFrontDistribution: cloudfront.Distribution,
        apiUrl: string
    ) {
        cloudFrontDistribution.addBehavior(
            "/api/*",
            new cloudfrontOrigins.HttpOrigin(apiUrl, {
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
