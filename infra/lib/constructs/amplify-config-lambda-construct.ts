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
import * as apigwIntegrations from "@aws-cdk/aws-apigatewayv2-integrations";
import * as iam from "@aws-cdk/aws-iam";
import * as lambda from "@aws-cdk/aws-lambda";
import * as cdk from "@aws-cdk/core";

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
    api: apigw.HttpApi;
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
export class AmplifyConfigLambdaConstruct extends cdk.Construct {
    constructor(parent: cdk.Construct, name: string, props: AmplifyConfigLambdaConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const lambdaFn = new lambda.Function(this, "Lambda", {
            runtime: lambda.Runtime.PYTHON_3_8,
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
            methods: [apigw.HttpMethod.GET],
            integration: lambdaFnIntegration,
            // set authorizer to none since this route needs to be public
            authorizer: new apigw.HttpNoneAuthorizer(),
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
}
