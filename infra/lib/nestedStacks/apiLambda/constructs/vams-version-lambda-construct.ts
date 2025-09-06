/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as apigatewayv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as apigwIntegrations from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as apigwAuthorizers from "aws-cdk-lib/aws-apigatewayv2-authorizers";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { LAMBDA_NODE_RUNTIME } from "../../../../config/config";
import { Construct } from "constructs";
import { IHttpRouteAuthorizer } from "aws-cdk-lib/aws-apigatewayv2";
import { Service } from "../../../helper/service-helper";
import * as Config from "../../../../config/config";
import { VAMS_VERSION } from "../../../../config/config";

interface InlineLambdaProps {
    /**
     * The VAMS Version to return
     */
    version: string;
}

export interface VamsVersionLambdaConstructProps extends cdk.StackProps {
    /**
     * Main Configuration Provider
     */
    config: Config.Config;

    /**
     * The ApiGatewayV2 HttpApi to create route from
     */
    api: apigatewayv2.HttpApi;
}

/**
 * Deploys a lambda to the api gateway under the path `/api/version`.
 * The route is unauthenticated.  Use this with `apigatewayv2-cloudfront` for a CORS free
 * version information
 */
export class VamsVersionLambdaConstruct extends Construct {
    constructor(parent: Construct, name: string, props: VamsVersionLambdaConstructProps) {
        super(parent, name);

        props = { ...props };

        const lambdaFn = new lambda.Function(this, "VamsVersionLambda", {
            runtime: LAMBDA_NODE_RUNTIME,
            handler: "index.handler",
            code: lambda.Code.fromInline(
                this.getJavascriptInlineFunction({
                    version: VAMS_VERSION || "0.0",
                })
            ),
            timeout: cdk.Duration.seconds(15),
        });

        // add lambda policies
        lambdaFn.grantInvoke(Service("APIGATEWAY").Principal);

        // add lambda integration
        const lambdaFnIntegration = new apigwIntegrations.HttpLambdaIntegration(
            "VamsVersionLambdaIntegration",
            lambdaFn
        );

        // add route to the api gateway
        props.api.addRoutes({
            path: "/api/version",
            methods: [apigatewayv2.HttpMethod.GET],
            integration: lambdaFnIntegration,
            authorizer: this.createNoOpAuthorizer(),
        });
    }

    private createNoOpAuthorizer(): IHttpRouteAuthorizer {
        const authorizerFn = new cdk.aws_lambda.Function(this, "AuthorizerLambdaVamsVersion", {
            runtime: LAMBDA_NODE_RUNTIME,
            handler: "index.handler",
            code: lambda.Code.fromInline(this.getAuthorizerLambdaCode()),
            timeout: cdk.Duration.seconds(15),
        });

        authorizerFn.grantInvoke(Service("APIGATEWAY").Principal);

        return new apigwAuthorizers.HttpLambdaAuthorizer("authorizerVamsVersion", authorizerFn, {
            authorizerName: "VamsVersionAuthorizer",
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
                    headers: {
                        'Content-Type': 'application/json'
                    },
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
