/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as apigatewayv2 from "@aws-cdk/aws-apigatewayv2-alpha";
import * as apigwIntegrations from "@aws-cdk/aws-apigatewayv2-integrations-alpha";
import * as apigwAuthorizers from "@aws-cdk/aws-apigatewayv2-authorizers-alpha";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { IHttpRouteAuthorizer } from "@aws-cdk/aws-apigatewayv2-alpha";

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
     * The Cognito UserPoolId to authenticate users in the front-end
     */
    userPoolId: string;
    /**
     * The Cognito AppClientId to authenticate users in the front-end
     */
    appClientId: string;
    /**
     * The Cognito IdentityPoolId to authenticate users in the front-end
     */
    identityPoolId: string;
    /**
     * The ApiGatewayV2 HttpApi to attach the lambda
     */
    api: string;
    /**
     * region
     */
    region: string;
    /**
     * Additional configuration needed for federated auth
     */
    federatedConfig?: AmplifyConfigFederatedIdentityProps;

    stackName: string;
}

export interface AmplifyConfigLambdaConstructProps extends cdk.StackProps {
    /**
     * The Cognito UserPoolId to authenticate users in the front-end
     */
    userPoolId: string;
    /**
     * The Cognito AppClientId to authenticate users in the front-end
     */
    appClientId: string;
    /**
     * The Cognito IdentityPoolId to authenticate users in the front-end
     */
    identityPoolId: string;
    /**
     * The ApiGatewayV2 HttpApi to attach the lambda
     */
    api: apigatewayv2.HttpApi;
    /**
     * region
     */
    region: string;
    /**
     * Additional configuration needed for federated auth
     */
    federatedConfig?: AmplifyConfigFederatedIdentityProps;
}

/**
 * Deploys a lambda to the api gateway under the path `/api/amplify-config`.
 * The route is unauthenticated.  Use this with `apigatewayv2-cloudfront` for a CORS free
 * amplify configuration setup
 */
export class AmplifyConfigLambdaConstruct extends Construct {
    constructor(parent: Construct, name: string, props: AmplifyConfigLambdaConstructProps) {
        super(parent, name);

        props = { ...props };

        const lambdaFn = new lambda.Function(this, "Lambda", {
            runtime: lambda.Runtime.NODEJS_18_X,
            handler: "index.handler",
            code: lambda.Code.fromInline(
                this.getJavascriptInlineFunction({
                    region: props.region,
                    userPoolId: props.userPoolId,
                    appClientId: props.appClientId,
                    identityPoolId: props.identityPoolId,
                    api: props.api.url || "us-east-1",
                    federatedConfig: props.federatedConfig,
                    stackName: props.stackName!,
                })
            ),
            timeout: cdk.Duration.seconds(15),
        });

        // add lambda policies
        lambdaFn.grantInvoke(new iam.ServicePrincipal("apigateway.amazonaws.com"));

        // add lambda integration
        const lambdaFnIntegration = new apigwIntegrations.HttpLambdaIntegration(
            "AmplifyConfigLambdaIntegration",
            lambdaFn
        );

        // add route to the api gateway
        props.api.addRoutes({
            path: "/api/amplify-config",
            methods: [apigatewayv2.HttpMethod.GET],
            integration: lambdaFnIntegration,
            authorizer: this.createNoOpAuthorizer(),
        });
    }

    private createNoOpAuthorizer(): IHttpRouteAuthorizer {
        const authorizerFn = new cdk.aws_lambda.Function(this, "AuthorizerLambda", {
            runtime: lambda.Runtime.NODEJS_18_X,
            handler: "index.handler",
            code: lambda.Code.fromInline(this.getAuthorizerLambdaCode()),
            timeout: cdk.Duration.seconds(15),
        });

        authorizerFn.grantInvoke(new cdk.aws_iam.ServicePrincipal("apigateway.amazonaws.com"));

        return new apigwAuthorizers.HttpLambdaAuthorizer("authorizer", authorizerFn, {
            authorizerName: "CognitoConfigAuthorizer",
            resultsCacheTtl: cdk.Duration.seconds(3600),
            identitySource: ["$context.routeKey"],
            responseTypes: [apigwAuthorizers.HttpLambdaResponseType.SIMPLE],
        });
    }

    private getJavascriptInlineFunction(props: InlineLambdaProps) {
        const resp = JSON.stringify(props);

        return `
            exports.handler = async function(event, context) {
                return {
                    statusCode: 200,
                    body: JSON.stringify(${resp}),
                };
            };
        `;
    }

    private getAuthorizerLambdaCode(): string {
        return `
            exports.handler = async function(event, context) {
                return {
                    isAuthorized: true
                }
            }
        `;
    }
}
