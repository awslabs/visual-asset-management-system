/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import { Conversion3dBasicConstruct } from "./constructs/conversion3dBasic-construct";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../../../config/config";

export interface Conversion3dBasicNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowV2FunctionName: string;
}

/**
 * Default input properties
 */
const defaultProps: Partial<Conversion3dBasicNestedStackProps> = {};

export class Conversion3dBasicNestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;
    constructor(parent: Construct, name: string, props: Conversion3dBasicNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const conversion3dBasicConstructPipeline = new Conversion3dBasicConstruct(
            this,
            "Conversion3dBasicPipeline",
            {
                ...props,
                config: props.config,
                storageResources: props.storageResources,
                vpc: props.vpc,
                pipelineSubnets: props.pipelineSubnets,
                pipelineSecurityGroups: props.pipelineSecurityGroups,
                lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                importGlobalPipelineWorkflowV2FunctionName:
                    props.importGlobalPipelineWorkflowV2FunctionName,
            }
        );

        this.pipelineVamsLambdaFunctionName =
            conversion3dBasicConstructPipeline.pipelineVamsLambdaFunctionName;
    }
}
