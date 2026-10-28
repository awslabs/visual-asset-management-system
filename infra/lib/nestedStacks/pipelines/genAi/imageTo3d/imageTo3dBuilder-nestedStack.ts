/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../../../config/config";
import { ImageTo3dCommonConstruct } from "./constructs/imageTo3dCommon-construct";
import { ImageTo3dCodeBuildConstruct } from "./constructs/imageTo3dCodeBuild-construct";
import { ImageTo3dConstruct } from "./constructs/imageTo3d-construct";

export interface ImageTo3dBuilderNestedStackProps extends cdk.StackProps {
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
const defaultProps: Partial<ImageTo3dBuilderNestedStackProps> = {};

/**
 * ImageTo3dBuilderNestedStack
 *
 * Nested stack entry point for the Image to 3D pipeline. Creates the shared resources
 * (S3 model weight cache, EFS) via ImageTo3dCommonConstruct, optionally builds the per-model
 * container images in the cloud via ImageTo3dCodeBuildConstruct, then creates the pipeline
 * itself (Batch GPU compute, one job definition per enabled model, state machine, lambdas).
 */
export class ImageTo3dBuilderNestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: ImageTo3dBuilderNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const imageTo3dConfig = props.config.app.pipelines.useImageTo3d;

        // Shared resources: S3 model weight cache bucket + EFS file system for HF caches
        const imageTo3dCommon = new ImageTo3dCommonConstruct(this, "ImageTo3dCommon", {
            config: props.config,
            vpc: props.vpc,
            subnets: props.pipelineSubnets,
            securityGroups: props.pipelineSecurityGroups,
            storageResources: props.storageResources,
        });

        // Conditionally build the per-model container images in the cloud (CodeBuild -> ECR)
        // instead of locally via Docker asset builds. One ECR repo + project per enabled model.
        let codeBuildConstruct: ImageTo3dCodeBuildConstruct | undefined;
        if (imageTo3dConfig.useCodeBuild) {
            codeBuildConstruct = new ImageTo3dCodeBuildConstruct(this, "ImageTo3dCodeBuild", {
                config: props.config,
                vpc: props.vpc,
                // Private subnets have NAT Gateway egress for pulling base images and cloning repos
                pipelineSubnets: props.pipelineSubnets,
                pipelineSecurityGroups: props.pipelineSecurityGroups,
                kmsKey: props.storageResources.encryption.kmsKey,
            });
        }

        const imageTo3dPipeline = new ImageTo3dConstruct(this, "ImageTo3dPipeline", {
            config: props.config,
            storageResources: props.storageResources,
            vpc: props.vpc,
            pipelineSubnets: props.pipelineSubnets,
            pipelineSecurityGroups: props.pipelineSecurityGroups,
            lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName:
                props.importGlobalPipelineWorkflowV2FunctionName,
            modelCacheBucket: imageTo3dCommon.modelCacheBucket,
            efsFileSystem: imageTo3dCommon.efsFileSystem,
            efsSecurityGroup: imageTo3dCommon.efsSecurityGroup,
            // CodeBuild-built images (optional). An empty map leaves every model on a local
            // Docker asset build.
            ...(codeBuildConstruct && Object.keys(codeBuildConstruct.modelImages).length > 0
                ? { codeBuildImages: codeBuildConstruct.modelImages }
                : {}),
        });

        this.pipelineVamsLambdaFunctionName = imageTo3dPipeline.pipelineVamsLambdaFunctionName;
    }
}
