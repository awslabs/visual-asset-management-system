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
import { NagSuppressions } from "cdk-nag";
import { IHttpRouteAuthorizer } from "@aws-cdk/aws-apigatewayv2-alpha";
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
}

/**
 * Default input properties
 */
const defaultProps: Partial<AmplifyConfigLambdaConstructProps> = {
    stackName: "",
    env: {},
};

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
            runtime: lambda.Runtime.PYTHON_3_9,
            handler: "index.lambda_handler",
            code: lambda.Code.fromInline(this.getPythonLambdaFunction()),
            timeout: cdk.Duration.seconds(15),
            environment: {
                USER_POOL_ID: props.userPoolId,
                APP_CLIENT_ID: props.appClientId,
                IDENTITY_POOL_ID: props.identityPoolId,
                REGION: props?.env?.region || "us-east-1",
                API: props.api.url || "us-east-1",
            },
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
            runtime: cdk.aws_lambda.Runtime.PYTHON_3_9,
            handler: "index.lambda_handler",
            code: cdk.aws_lambda.Code.fromInline(this.getAuthorizerLambdaCode()),
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

    private getPythonLambdaFunction(): string {
        // string requires left justification so that the python code is correctly indented
        return `
import json
import os

def lambda_handler(event, context):
  region = os.getenv("REGION", None)
  user_pool_id = os.getenv("USER_POOL_ID", None)
  app_client_id = os.getenv("APP_CLIENT_ID", None)
  identity_pool_id = os.getenv("IDENTITY_POOL_ID", None)
  api = os.getenv("API", None)
  response = {
      "region": region,
      "userPoolId": user_pool_id,
      "appClientId": app_client_id,
      "identityPoolId": identity_pool_id,
      "api": api
  }
  return {
      "statusCode": "200",
      "body": json.dumps(response),
      "headers": {
          "Content-Type": "application/json",
      },
  }
      `;
    }

    private getAuthorizerLambdaCode(): string {
        return `
def lambda_handler(event, context): 
    return {
        "isAuthorized": True
    }
        `;
    }
}
