/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import { SplatToolboxConstruct } from "./constructs/splatToolbox-construct";
import { SplatToolboxCodeBuildConstruct } from "./constructs/splatToolboxCodeBuild-construct";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../../../config/config";

export interface SplatToolboxBuilderNestedStackProps extends cdk.StackProps {
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
const defaultProps: Partial<SplatToolboxBuilderNestedStackProps> = {};

export class SplatToolboxBuilderNestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;
    constructor(parent: Construct, name: string, props: SplatToolboxBuilderNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        // Conditionally create CodeBuild construct for container image builds.
        // When useCodeBuild is enabled, the Splat Toolbox container image is built in the
        // cloud (CodeBuild → ECR) instead of locally via a Docker asset build.
        let codeBuildConstruct: SplatToolboxCodeBuildConstruct | undefined;
        if (props.config.app.pipelines.useSplatToolbox.useCodeBuild) {
            // The upstream container sources are synced into backendPipelines before the CodeBuild
            // S3 asset is created, so the upload carries the pinned-commit sources.
            SplatToolboxConstruct.syncContainerSources(
                SplatToolboxConstruct.GITHUB_REPO_LINK,
                SplatToolboxConstruct.GITHUB_REPO_COMMIT_HASH
            );
            codeBuildConstruct = new SplatToolboxCodeBuildConstruct(this, "SplatToolboxCodeBuild", {
                config: props.config,
                vpc: props.vpc,
                // Use private subnets (with NAT Gateway egress) so CodeBuild can pull base images
                pipelineSubnets: props.pipelineSubnets,
                pipelineSecurityGroups: props.pipelineSecurityGroups,
            });
        }

        const splatToolboxPipeline = new SplatToolboxConstruct(this, "SplatToolboxPipeline", {
            ...props,
            config: props.config,
            storageResources: props.storageResources,
            vpc: props.vpc,
            pipelineSubnets: props.pipelineSubnets,
            pipelineSecurityGroups: props.pipelineSecurityGroups,
            lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName:
                props.importGlobalPipelineWorkflowV2FunctionName,
            // CodeBuild-built image (optional). Pass the ECR repository (not just the URI) so the
            // Batch container definition auto-grants the execution role ECR pull
            // permissions via ecs.ContainerImage.fromEcrRepository.
            ...(codeBuildConstruct?.splatToolboxRepo
                ? { codeBuildRepository: codeBuildConstruct.splatToolboxRepo.repository }
                : {}),
        });

        this.pipelineVamsLambdaFunctionName = splatToolboxPipeline.pipelineVamsLambdaFunctionName;
    }
}
