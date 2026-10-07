/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import { SystemGenAiMetadataConstruct } from "./constructs/systemGenAiMetadata-construct";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../../../config/config";

export interface SystemGenAiMetadataNestedStackProps extends cdk.StackProps {
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
const defaultProps: Partial<SystemGenAiMetadataNestedStackProps> = {};

export class SystemGenAiMetadataNestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;
    constructor(parent: Construct, name: string, props: SystemGenAiMetadataNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const systemGenAiMetadataPipeline = new SystemGenAiMetadataConstruct(
            this,
            "SystemGenAiMetadataPipeline",
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
            systemGenAiMetadataPipeline.pipelineVamsLambdaFunctionName;
    }
}
