/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as apigw from "@aws-cdk/aws-apigatewayv2-alpha";
import * as apigwIntegrations from "@aws-cdk/aws-apigatewayv2-integrations-alpha";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as lambdaPython from "@aws-cdk/aws-lambda-python-alpha";
import * as lambdaNodejs from "aws-cdk-lib/aws-lambda-nodejs";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { Service } from "../../../helper/service-helper";

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
    //stackName: "",
    //env: {},
};

/**
 * Deploys a lambda and attaches it to a route on the apigatewayv2
 */
export class ApiGatewayV2LambdaConstruct extends Construct {
    constructor(parent: Construct, name: string, props: ApiGatewayV2LambdaConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const { lambdaFn } = props;

        // add lambda policies

        lambdaFn.grantInvoke(Service("APIGATEWAY").Principal);

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
