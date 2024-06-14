/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { storageResources } from "../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import { SecurityGroupGatewayVisualizerPipelineConstruct } from "./constructs/securitygroup-gateway-visualizerPipeline-construct";
import { VisualizationPipelineConstruct } from "./constructs/visualizerPipeline-construct";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";

export interface VisualizerPipelineBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    vpceSecurityGroup: ec2.ISecurityGroup;
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
}

/**
 * Default input properties
 */
const defaultProps: Partial<VisualizerPipelineBuilderNestedStackProps> = {};

export class VisualizerPipelineBuilderNestedStack extends NestedStack {
    constructor(parent: Construct, name: string, props: VisualizerPipelineBuilderNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const visualizerPipelineNetwork = new SecurityGroupGatewayVisualizerPipelineConstruct(
            this,
            "VisualizerPipelineNetwork",
            {
                ...props,
                config: props.config,
                vpc: props.vpc,
                vpceSecurityGroup: props.vpceSecurityGroup,
                subnets: props.subnets,
            }
        );

        const visualizerPipeline = new VisualizationPipelineConstruct(this, "VisualizerPipeline", {
            ...props,
            config: props.config,
            storageResources: props.storageResources,
            vpc: props.vpc,
            visualizerPipelineSubnets: visualizerPipelineNetwork.subnets.pipeline,
            visualizerPipelineSecurityGroups: [visualizerPipelineNetwork.securityGroups.pipeline],
            lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
        });
    }
}
