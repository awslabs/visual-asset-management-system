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
        //
        // The name is explicit because AWS::Location::APIKey requires one — CloudFormation will not
        // generate it. It is already unique per deployment: `baseStackName` carries the Region appended
        // in getConfig(), so the name is vams-location-api-key-{name}-{stack}-{region} and two VAMS
        // deployments collide only when the configuration name, the stack name and the Region all match,
        // which makes them the same deployment.
        const apiKey = new aws_location.CfnAPIKey(this, "LocationServiceApiKey", {
            keyName:
                `vams-location-api-key-` + props.config.name + "-" + props.config.app.baseStackName,
            noExpiry: true,
            restrictions: {
                allowActions: ["geo-maps:GetTile", "geo-maps:GetStaticMap"],
                allowResources: [Service.IAMArn("*").geomap],
            },
            description: "API Key for VAMS Location Services Maps",
            // Lets the key be deleted with the stack. Without it a rolled-back first deployment left the
            // key behind, and because the name is deterministic every retry then failed at changeset
            // validation with an already-exists error naming a resource the operator was not working on.
            // The delete is what a teardown intends, and the key is not shared with anything outside the
            // deployment that owns it.
            forceDelete: true,
            // Required for ANY update to a key whose maps have been loaded in the last seven days, which
            // is every live deployment. AWS Location Service rejects the update outright otherwise:
            // "This update may cause some users to lose API access. Because this API Key has been used
            // in the last 7 days, you must set 'ForceUpdate' to true to confirm this change." Measured
            // the hard way — the equivalent `aws location update-key` call succeeds WITHOUT the flag, so
            // the CLI is not a valid proxy for what the CloudFormation resource handler enforces, and a
            // deployment carrying only forceDelete rolled the whole core stack back.
            forceUpdate: true,
        });

        // Deleted with the stack rather than retained. The earlier retain policy cited a 90-day wait
        // before an API key can be deleted; that applies to a key that has been DEPRECATED by being
        // given a past expiry, not to one created with noExpiry. Measured against AWS Location Service
        // in us-west-2: a noExpiry key deletes immediately with no force flag, both unused and after it
        // had served a geo-maps request.
        apiKey.applyRemovalPolicy(RemovalPolicy.DESTROY);

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
