/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import { Gr00tCommonConstruct } from "./constructs/gr00tCommon-construct";
import { Gr00tFinetuneConstruct } from "./constructs/gr00tFinetune-construct";
import { Gr00tCodeBuildConstruct } from "./constructs/gr00tCodeBuild-construct";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../../../../config/config";

export interface Gr00tBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowFunctionName: string;
}

/**
 * Default input properties
 */
const defaultProps: Partial<Gr00tBuilderNestedStackProps> = {};

/**
 * Gr00tBuilderNestedStack
 *
 * Nested stack entry point for NVIDIA Gr00t fine-tuning pipeline.
 * Creates shared resources (S3 model cache, EFS) via Gr00tCommonConstruct,
 * then conditionally creates the fine-tuning construct for GR00T-N1.5-3B.
 *
 * Current model types:
 * - Finetune (GR00T-N1.5-3B fine-tuning for embodied AI robots)
 *
 * Future model types (will be added here):
 * - Inference / Deployment pipelines for trained checkpoints
 */
export class Gr00tBuilderNestedStack extends NestedStack {
    public pipelineGr00tFinetuneVamsLambdaFunctionName = "";

    constructor(parent: Construct, name: string, props: Gr00tBuilderNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        // Create shared Gr00t resources (S3 model cache bucket, EFS filesystem)
        const gr00tCommon = new Gr00tCommonConstruct(this, "Gr00tCommon", {
            config: props.config,
            vpc: props.vpc,
            subnets: props.pipelineSubnets,
            securityGroups: props.pipelineSecurityGroups,
            storageResources: props.storageResources,
        });

        // Conditionally create CodeBuild construct for container image builds
        const gr00tConfig = props.config.app.pipelines.useNvidiaGr00t;
        let codeBuildConstruct: Gr00tCodeBuildConstruct | undefined;
        if (gr00tConfig.useCodeBuild) {
            codeBuildConstruct = new Gr00tCodeBuildConstruct(this, "Gr00tCodeBuild", {
                config: props.config,
                modelCacheBucket: gr00tCommon.modelCacheBucket,
                vpc: props.vpc,
                pipelineSubnets: props.pipelineSubnets,
                pipelineSecurityGroups: props.pipelineSecurityGroups,
            });
        }

        // Create Fine-Tuning pipeline (conditional on gr00tN1_5_3B.enabled)
        if (gr00tConfig.modelsFinetune.gr00tN1_5_3B.enabled) {
            const gr00tFinetune = new Gr00tFinetuneConstruct(this, "Gr00tFinetune", {
                config: props.config,
                storageResources: props.storageResources,
                vpc: props.vpc,
                pipelineSubnets: props.pipelineSubnets,
                pipelineSecurityGroups: props.pipelineSecurityGroups,
                lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                importGlobalPipelineWorkflowFunctionName:
                    props.importGlobalPipelineWorkflowFunctionName,
                // Shared resources from common construct
                modelCacheBucket: gr00tCommon.modelCacheBucket,
                efsFileSystem: gr00tCommon.efsFileSystem,
                efsSecurityGroup: gr00tCommon.efsSecurityGroup,
                // CodeBuild-built image (optional)
                ...(codeBuildConstruct?.finetuneRepo
                    ? {
                          codeBuildImageUri: codeBuildConstruct.finetuneRepo.imageUri,
                      }
                    : {}),
            });

            this.pipelineGr00tFinetuneVamsLambdaFunctionName =
                gr00tFinetune.vamsExecuteFunctionName;
        }

        // Future: other Gr00t pipelines would be added here
        // if (props.config.app.pipelines.useGr00tInference.enabled) { ... }
    }
}
