/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { aws_location, Stack, NestedStack, RemovalPolicy } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as Service from "../../helper/service-helper";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../config/config";

interface LocationServiceConstructProps extends cdk.StackProps {
    config: Config.Config;
}

export class LocationServiceNestedStack extends NestedStack {
    apiKey?: aws_location.CfnAPIKey;

    constructor(scope: Construct, id: string, props: LocationServiceConstructProps) {
        super(scope, id);

        // Create Location Services API Key
        const apiKey = new aws_location.CfnAPIKey(this, "LocationServiceApiKey", {
            keyName:
                `vams-location-api-key-` + props.config.name + "-" + props.config.app.baseStackName,
            noExpiry: true,
            restrictions: {
                allowActions: ["geo-maps:GetTile", "geo-maps:GetStaticMap"],
                allowResources: [Service.IAMArn("*").geomap],
            },
            description: "API Key for VAMS Location Services Maps",
        });

        //Retain the API key as an API key has to be around for 90 days before it can be deleted. 
        apiKey.applyRemovalPolicy(RemovalPolicy.RETAIN);

        // Store API Key in SSM Parameter Store
        const apiKeySSMParameter = new ssm.StringParameter(this, "LocationServiceApiKeyARNSSM", {
            parameterName: props.config.locationServiceApiKeyArnSSMParam,
            stringValue: apiKey.attrKeyArn,
            description: "Location Service API Key ARN for VAMS",
            tier: ssm.ParameterTier.STANDARD,
        });

        // Add dependency to ensure API key is created before SSM parameter
        apiKeySSMParameter.node.addDependency(apiKey);

        this.apiKey = apiKey;

        // Add CDK Nag suppressions
        this.addNagSuppressions();
    }

    private addNagSuppressions(): void {
        if (this.apiKey) {
            NagSuppressions.addResourceSuppressions(
                this.apiKey,
                [
                    {
                        id: "AwsSolutions-GEO1",
                        reason: "API Key is restricted to specific map resources and actions as required for VAMS Location Services functionality. The key is stored securely in SSM Parameter Store.",
                    },
                ],
                true
            );
        }
    }
}
