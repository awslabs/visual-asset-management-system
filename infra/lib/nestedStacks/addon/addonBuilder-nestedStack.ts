/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { storageResources } from "../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { Stack, NestedStack } from "aws-cdk-lib";
import { GarnetFrameworkBuilderNestedStack } from "./garnetFramework/garnetFrameworkBuilder-nestedStack";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../config/config";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { LAMBDA_NODE_RUNTIME } from "../../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";
import { NagSuppressions } from "cdk-nag";

export interface AddonBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    privateSubnets: ec2.ISubnet[];
    isolatedSubnets: ec2.ISubnet[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
}

/**
 * Default input properties
 */
const defaultProps: Partial<AddonBuilderNestedStackProps> = {};

export class AddonBuilderNestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionNames: string[] = [];

    constructor(parent: Construct, name: string, props: AddonBuilderNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        //Garnet Framework
        if (props.config.app.addons.useGarnetFramework.enabled) {
            const garnetFrameworkBuilderNestedStack = new GarnetFrameworkBuilderNestedStack(
                this,
                "GarnetFrameworkBuilderNestedStack",
                {
                    ...props,
                    config: props.config,
                    storageResources: props.storageResources,
                    vpc: props.vpc,
                    isolatedSubnets: props.isolatedSubnets,
                    lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                }
            );
        }

        ////////////////////////////////////////////////////////////////////////////////////
        ///Create empty lambda with no permissions in case the nested stack has no
        // other components or pipelines enables (otherwise synth will error)
        const lambdaFn = new lambda.Function(this, "AddonNestedStackEmptyLambda", {
            runtime: LAMBDA_NODE_RUNTIME,
            handler: "index.handler",
            code: lambda.Code.fromInline(
                `
                exports.handler = async function(event, context) {
                    return {
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        statusCode: 400,
                        body: '',
                    };
                };
            `
            ),
            vpc:
                props.config.app.useGlobalVpc.enabled &&
                props.config.app.useGlobalVpc.useForAllLambdas
                    ? props.vpc
                    : undefined, //Use VPC when flagged to use for all lambdas
            vpcSubnets:
                props.config.app.useGlobalVpc.enabled &&
                props.config.app.useGlobalVpc.useForAllLambdas
                    ? { subnets: props.isolatedSubnets }
                    : undefined,
            timeout: cdk.Duration.seconds(1),
        });
    }
}
