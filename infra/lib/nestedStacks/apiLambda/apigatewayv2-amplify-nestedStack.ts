/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as apigw from "aws-cdk-lib/aws-apigatewayv2";
import * as apigwv1 from "aws-cdk-lib/aws-apigateway";
import * as apigwAuthorizers from "aws-cdk-lib/aws-apigatewayv2-authorizers";
import * as logs from "aws-cdk-lib/aws-logs";
import * as cdk from "aws-cdk-lib";
import * as Config from "../../../config/config";
import { samlSettings } from "../../../config/saml-config";
import { generateUniqueNameHash } from "../../helper/security";
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
import { buildApiGatewayAuthorizerHttpFunction } from "../../lambdaBuilder/authFunctions";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as ec2 from "aws-cdk-lib/aws-ec2";

export interface ApiGatewayV2AmplifyNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    authResources: authResources;
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    lambdaAuthorizerLayer: LayerVersion;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
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

        // Create custom authorizer Lambda function
        const customAuthorizerFunction = buildApiGatewayAuthorizerHttpFunction(
            this,
            props.lambdaAuthorizerLayer,
            props.config,
            props.vpc,
            props.subnets
        );

        // Update environment variables with actual Cognito values if using Cognito
        if (props.config.app.authProvider.useCognito.enabled) {
            customAuthorizerFunction.addEnvironment(
                "USER_POOL_ID",
                props.authResources.cognito.userPoolId
            );
            customAuthorizerFunction.addEnvironment(
                "APP_CLIENT_ID",
                props.authResources.cognito.webClientId
            );
        }

        // Determine cache TTL based on IP restrictions
        const hasIpRestrictions =
            props.config.app.authProvider.authorizerOptions?.allowedIpRanges?.length > 0;
        const cacheTtlSeconds = hasIpRestrictions ? 30 : 30;

        // Setup custom Lambda authorizer with payload format version 2.0
        const apiGatewayAuthorizer = new apigwAuthorizers.HttpLambdaAuthorizer(
            "CustomHttpAuthorizer",
            customAuthorizerFunction,
            {
                authorizerName: "VamsCustomAuthorizer",
                resultsCacheTtl: cdk.Duration.seconds(cacheTtlSeconds),
                identitySource: ["$request.header.Authorization"],
                responseTypes: [apigwAuthorizers.HttpLambdaResponseType.SIMPLE],
            }
        );

        const accessLogs = new logs.LogGroup(this, "VAMS-API-AccessLogs", {
            logGroupName:
                "/aws/vendedlogs/VAMS-API-AccessLogs" +
                generateUniqueNameHash(
                    props.config.env.coreStackName,
                    props.config.env.account,
                    "VAMS-API-AccessLog",
                    10
                ),
            retention: logs.RetentionDays.ONE_YEAR,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

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
                    "Access-Control-Allow-Origin",
                ],
                allowMethods: [
                    apigw.CorsHttpMethod.HEAD,
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
            createDefaultStage: true,
        });

        const defaultStage = api.defaultStage?.node.defaultChild as apigw.CfnStage;
        if (defaultStage) {
            // Modify throttle settings using L1 construct properties
            defaultStage.defaultRouteSettings = {
                throttlingBurstLimit: props.config.app.api.globalBurstLimit, // Set burst limit
                throttlingRateLimit: props.config.app.api.globalRateLimit, // Set rate limit
            };

            //Modify log settings
            defaultStage.accessLogSettings = {
                destinationArn: accessLogs.logGroupArn,
                format: JSON.stringify({
                    requestId: "$context.requestId",
                    userAgent: "$context.identity.userAgent",
                    sourceIp: "$context.identity.sourceIp",
                    requestTime: "$context.requestTime",
                    requestTimeEpoch: "$context.requestTimeEpoch",
                    httpMethod: "$context.httpMethod",
                    path: "$context.path",
                    status: "$context.status",
                    protocol: "$context.protocol",
                    responseLength: "$context.responseLength",
                    domainName: "$context.domainName",
                }),
            };
        }

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
            customAuthorizerFunction: customAuthorizerFunction,
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
            customAuthorizerFunction: customAuthorizerFunction,
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
