/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import * as s3 from "aws-cdk-lib/aws-s3";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import { NestedStack } from "aws-cdk-lib";
import * as Config from "../../../../../config/config";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import { VideoSopBomConstruct } from "./constructs/videoSopBom-construct";

export interface VideoSopBomNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    assetAuxiliaryBucket: s3.IBucket;
    storageResources: storageResources;
    kmsKey?: kms.IKey;
    importGlobalPipelineWorkflowV2FunctionName: string;
}

export class VideoSopBomBuilderNestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: VideoSopBomNestedStackProps) {
        super(parent, name);

        const videoSopBomPipeline = new VideoSopBomConstruct(this, "VideoSopBomPipeline", {
            config: props.config,
            vpc: props.vpc,
            pipelineSubnets: props.pipelineSubnets,
            pipelineSecurityGroups: props.pipelineSecurityGroups,
            lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
            assetAuxiliaryBucket: props.assetAuxiliaryBucket,
            storageResources: props.storageResources,
            kmsKey: props.kmsKey,
            importGlobalPipelineWorkflowV2FunctionName:
                props.importGlobalPipelineWorkflowV2FunctionName,
        });

        this.pipelineVamsLambdaFunctionName = videoSopBomPipeline.pipelineVamsLambdaFunctionName;
    }
}
