/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as apigatewayv2 from "@aws-cdk/aws-apigatewayv2-alpha";
import * as apigwIntegrations from "@aws-cdk/aws-apigatewayv2-integrations-alpha";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cdk from 'aws-cdk-lib';
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
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
            code: lambda.Code.fromInline(this.getPythonLambdaFunction()), // TODO: support both python and typescript versions
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
        // TODO: replace with specific dynamo resource assignment when table is in CDK
        lambdaFn.grantInvoke(new iam.ServicePrincipal("apigateway.amazonaws.com"));

        // add lambda integration
        const lambdaFnIntegration = new apigwIntegrations.HttpLambdaIntegration("AmplifyConfigLambdaIntegration", lambdaFn);

        // add route to the api gateway
        props.api.addRoutes({
            path: "/api/amplify-config",
            methods: [apigatewayv2.HttpMethod.GET],
            integration: lambdaFnIntegration,
            // set authorizer to none since this route needs to be public
            authorizer: new apigatewayv2.HttpNoneAuthorizer(),
        });

        NagSuppressions.addResourceSuppressions(props.api, [
            {
                id: "AwsSolutions-APIG4",
                reason: "required configuration for amplify to load for unauth users."
            }
        ], true);
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
}
