/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import { RapidPipelineConstruct } from "./constructs/rapidPipeline-construct";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";

export interface RapidPipelineNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnetsPrivate: ec2.ISubnet[];
    pipelineSubnetsIsolated: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
}

/**
 * Default input properties
 */
const defaultProps: Partial<RapidPipelineNestedStackProps> = {};

export class RapidPipelineNestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;
    constructor(parent: Construct, name: string, props: RapidPipelineNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const rapidPipelineConstructPipeline = new RapidPipelineConstruct(this, "RapidPipeline", {
            ...props,
            config: props.config,
            storageResources: props.storageResources,
            vpc: props.vpc,
            pipelineSubnetsPrivate: props.pipelineSubnetsPrivate,
            pipelineSubnetsIsolated: props.pipelineSubnetsIsolated,
            pipelineSecurityGroups: props.pipelineSecurityGroups,
            lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
        });

        this.pipelineVamsLambdaFunctionName =
            rapidPipelineConstructPipeline.pipelineVamsLambdaFunctionName;
    }
}
