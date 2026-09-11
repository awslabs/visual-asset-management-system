/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { LAMBDA_NODE_RUNTIME } from "../../../../config/config";
import { Construct } from "constructs";
import * as Config from "../../../../config/config";
import { VAMS_VERSION } from "../../../../config/config";
import { suppressCdkNagLambda } from "../../../helper/security";

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
}

/**
 * Builds the /api/version Lambda function. Route registration is handled by the REST API builder.
 */
export class VamsVersionLambdaConstruct extends Construct {
    public readonly lambdaFn: lambda.Function;

    constructor(parent: Construct, name: string, props: VamsVersionLambdaConstructProps) {
        super(parent, name);

        props = { ...props };

        this.lambdaFn = new lambda.Function(this, "VamsVersionLambda", {
            runtime: LAMBDA_NODE_RUNTIME,
            handler: "index.handler",
            code: lambda.Code.fromInline(
                this.getJavascriptInlineFunction({
                    version: VAMS_VERSION || "0.0.0",
                })
            ),
            timeout: cdk.Duration.seconds(15),
        });

        // API Gateway invoke permission is granted by the REST API builder, which emits one
        // CfnPermission per registered route Lambda scoped to this deployment's own execute-api
        // source ARN. It cannot be granted here: the construct is created before the SpecRestApi
        // (whose inline OpenAPI document names this function), so referring to the API id from here
        // makes the two resources reference each other.

        suppressCdkNagLambda(this.lambdaFn);
    }

    private getJavascriptInlineFunction(props: InlineLambdaProps) {
        const resp = JSON.stringify(props);

        return `
            exports.handler = async function(event, context) {
                return {
                    headers: {
                        'Content-Type': 'application/json',
                        // REST API returns the Lambda response verbatim (no auto-CORS); this
                        // anonymous endpoint can be fetched cross-origin under ALB fronting.
                        'Access-Control-Allow-Origin': '*'
                    },
                    statusCode: 200,
                    body: JSON.stringify(${resp}),
                };
            };
        `;
    }
}
