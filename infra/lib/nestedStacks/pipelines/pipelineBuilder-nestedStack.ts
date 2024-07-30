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
import { SecurityGroupGatewayPipelineConstruct } from "./constructs/securitygroup-gateway-pipeline-construct";
import { PcPotreeViewerBuilderNestedStack } from "./preview/pcPotreeViewer/pcPotreeViewerBuilder-nestedStack";
import { Metadata3dExtractionNestedStack } from "./genAi/metadata3dExtraction/metadata3dExtractionBuilder-nestedStack";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";

export interface PipelineBuilderNestedStackProps extends cdk.StackProps {
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
const defaultProps: Partial<PipelineBuilderNestedStackProps> = {};

export class PipelineBuilderNestedStack extends NestedStack {
    constructor(parent: Construct, name: string, props: PipelineBuilderNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        if (
            props.config.app.pipelines.usePreviewPcPotreeViewer.enabled ||
            props.config.app.pipelines.useGenAiMetadata3dExtraction.enabled
        ) {
            const pipelineNetwork = new SecurityGroupGatewayPipelineConstruct(
                this,
                "PipelineNetwork",
                {
                    ...props,
                    config: props.config,
                    vpc: props.vpc,
                    vpceSecurityGroup: props.vpceSecurityGroup,
                    subnets: props.subnets,
                }
            );

            //Create nested stack for each turned on pipeline
            if (props.config.app.pipelines.usePreviewPcPotreeViewer.enabled) {
                const previewPcPotreeViewerPipelineNestedStack =
                    new PcPotreeViewerBuilderNestedStack(this, "PcPotreeViewerBuilderNestedStack", {
                        ...props,
                        config: props.config,
                        storageResources: props.storageResources,
                        lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                        vpc: props.vpc,
                        pipelineSubnets: pipelineNetwork.subnets.pipeline,
                        pipelineSecurityGroups: [pipelineNetwork.securityGroups.pipeline],
                    });
            }

            if (props.config.app.pipelines.useGenAiMetadata3dExtraction.enabled) {
                const genAiMetadata3dExtractionNestedStack = new Metadata3dExtractionNestedStack(
                    this,
                    "GenAiMetadata3dExtractionNestedStack",
                    {
                        ...props,
                        config: props.config,
                        storageResources: props.storageResources,
                        lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                        vpc: props.vpc,
                        pipelineSubnets: pipelineNetwork.subnets.pipeline,
                        pipelineSecurityGroups: [pipelineNetwork.securityGroups.pipeline],
                    }
                );
            }
        }
    }
}
