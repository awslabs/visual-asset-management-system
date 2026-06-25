/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { NestedStack } from "aws-cdk-lib";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import { IsaacLabTrainingConstruct } from "./constructs/isaacLabTraining-construct";
import { IsaacLabCodeBuildConstruct } from "./constructs/isaacLabCodeBuild-construct";
import * as Config from "../../../../../config/config";

export interface IsaacLabTrainingBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[]; // Private subnets for compute (with NAT Gateway)
    pipelineSubnetsIsolated: ec2.ISubnet[]; // Isolated subnets for EFS
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowFunctionName: string;
}

export class IsaacLabTrainingBuilderNestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: IsaacLabTrainingBuilderNestedStackProps) {
        super(parent, name);

        // Conditionally create CodeBuild construct for container image builds.
        // When useCodeBuild is enabled, the Isaac Lab container image is built in the
        // cloud (CodeBuild → ECR) instead of locally via DockerImageAsset.
        const isaacLabConfig = props.config.app.pipelines.useIsaacLabTraining;
        let codeBuildConstruct: IsaacLabCodeBuildConstruct | undefined;
        if (isaacLabConfig.useCodeBuild) {
            codeBuildConstruct = new IsaacLabCodeBuildConstruct(this, "IsaacLabCodeBuild", {
                config: props.config,
                vpc: props.vpc,
                // Use private subnets (with NAT Gateway egress) so CodeBuild can pull base images
                pipelineSubnets: props.pipelineSubnets,
                pipelineSecurityGroups: props.pipelineSecurityGroups,
            });
        }

        const isaacLabTraining = new IsaacLabTrainingConstruct(this, "IsaacLabTrainingConstruct", {
            ...props,
            // CodeBuild-built image (optional). Pass the ECR repository (not just the URI) so the
            // Batch container definition grants the execution role ECR pull + GetAuthorizationToken
            // permissions via ecs.ContainerImage.fromEcrRepository.
            ...(codeBuildConstruct?.trainingRepo
                ? {
                      codeBuildRepository: codeBuildConstruct.trainingRepo.repository,
                  }
                : {}),
        });

        this.pipelineVamsLambdaFunctionName = isaacLabTraining.pipelineVamsLambdaFunctionName;
    }
}
