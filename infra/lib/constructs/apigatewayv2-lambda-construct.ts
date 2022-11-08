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
import * as lambdaPython from "@aws-cdk/aws-lambda-python";
import * as lambdaNodejs from "@aws-cdk/aws-lambda-nodejs";
import * as cdk from "@aws-cdk/core";

export interface ApiGatewayV2LambdaConstructProps extends cdk.StackProps {
    /**
     * The lambda function
     */
    lambdaFn: lambda.Function | lambdaPython.PythonFunction | lambdaNodejs.NodejsFunction;
    /**
     * The apigatewayv2 route path
     */
    routePath: string;
    /**
     * Api methods supported by this API
     */
    methods: Array<apigw.HttpMethod>;
    /**
     * The ApiGatewayV2 HttpApi to attach the lambda
     */
    api: apigw.HttpApi;
}

/**
 * Default input properties
 */
const defaultProps: Partial<ApiGatewayV2LambdaConstructProps> = {
    stackName: "",
    env: {},
};

/**
 * Deploys a lambda and attaches it to a route on the apigatewayv2
 */
export class ApiGatewayV2LambdaConstruct extends cdk.Construct {
    constructor(parent: cdk.Construct, name: string, props: ApiGatewayV2LambdaConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const { lambdaFn } = props;

        // add lambda policies
        lambdaFn.grantInvoke(new iam.ServicePrincipal("apigateway.amazonaws.com"));

        // add lambda integration
        const lambdaFnIntegration = new apigwIntegrations.HttpLambdaIntegration(
            "lint-" + name,
            props.lambdaFn
        );

        // add route to the api gateway
        props.api.addRoutes({
            path: props.routePath,
            methods: props.methods,
            integration: lambdaFnIntegration,
        });
    }
}
