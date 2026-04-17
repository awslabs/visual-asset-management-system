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
import { CosmosCommonConstruct } from "./constructs/cosmosCommon-construct";
import { CosmosPredictConstruct } from "./constructs/cosmosPredict-construct";
import { CosmosTransferConstruct } from "./constructs/cosmosTransfer-construct";
import { CosmosReasonConstruct } from "./constructs/cosmosReason-construct";
import { CosmosCodeBuildConstruct } from "./constructs/cosmosCodeBuild-construct";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../../../../config/config";

export interface CosmosBuilderNestedStackProps extends cdk.StackProps {
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
const defaultProps: Partial<CosmosBuilderNestedStackProps> = {};

/**
 * CosmosBuilderNestedStack
 *
 * Common nested stack entry point for all NVIDIA Cosmos pipelines.
 * Creates shared resources (S3 model cache, EFS) via CosmosCommonConstruct,
 * then conditionally creates sub-constructs for each Cosmos model type.
 *
 * Current model types:
 * - Predict (Text2World, Video2World)
 * - Transfer (style/content transfer with control signals)
 * - Reason (Vision Language Model for video/image analysis)
 *
 * Future model types (will be added here):
 * - Tokenize
 */
export class CosmosBuilderNestedStack extends NestedStack {
    public pipelineText2World2Bv2VamsLambdaFunctionName?: string;
    public pipelineVideo2World2Bv2VamsLambdaFunctionName?: string;
    public pipelineText2World14Bv2VamsLambdaFunctionName?: string;
    public pipelineVideo2World14Bv2VamsLambdaFunctionName?: string;
    public pipelineTransfer2BVamsLambdaFunctionName?: string;
    public pipelineReason2BVamsLambdaFunctionName?: string;
    public pipelineReason8BVamsLambdaFunctionName?: string;

    constructor(parent: Construct, name: string, props: CosmosBuilderNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        // Create shared Cosmos resources (S3 model cache bucket, EFS filesystem)
        const cosmosCommon = new CosmosCommonConstruct(this, "CosmosCommon", {
            config: props.config,
            vpc: props.vpc,
            subnets: props.pipelineSubnets,
            securityGroups: props.pipelineSecurityGroups,
            storageResources: props.storageResources,
        });

        // Conditionally create CodeBuild construct for container image builds
        const cosmosConfig = props.config.app.pipelines.useNvidiaCosmos;
        let codeBuildConstruct: CosmosCodeBuildConstruct | undefined;
        if (cosmosConfig.useCodeBuild) {
            codeBuildConstruct = new CosmosCodeBuildConstruct(this, "CosmosCodeBuild", {
                config: props.config,
                modelCacheBucket: cosmosCommon.modelCacheBucket,
                vpc: props.vpc,
                pipelineSubnets: props.pipelineSubnets,
                pipelineSecurityGroups: props.pipelineSecurityGroups,
            });
        }

        // Create Predict pipeline (conditional on useNvidiaCosmos.enabled)
        if (props.config.app.pipelines.useNvidiaCosmos.enabled) {
            const cosmosPredictConstruct = new CosmosPredictConstruct(
                this,
                "CosmosPredictPipeline",
                {
                    config: props.config,
                    storageResources: props.storageResources,
                    vpc: props.vpc,
                    pipelineSubnets: props.pipelineSubnets,
                    pipelineSecurityGroups: props.pipelineSecurityGroups,
                    lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                    importGlobalPipelineWorkflowFunctionName:
                        props.importGlobalPipelineWorkflowFunctionName,
                    // Shared resources from common construct
                    modelCacheBucket: cosmosCommon.modelCacheBucket,
                    efsFileSystem: cosmosCommon.efsFileSystem,
                    efsSecurityGroup: cosmosCommon.efsSecurityGroup,
                    // CodeBuild-built image (optional)
                    ...(codeBuildConstruct?.predictV2Repo
                        ? {
                              codeBuildImageUri: codeBuildConstruct.predictV2Repo.imageUri,
                          }
                        : {}),
                }
            );

            this.pipelineText2World2Bv2VamsLambdaFunctionName =
                cosmosPredictConstruct.pipelineText2World2Bv2VamsLambdaFunctionName;
            this.pipelineVideo2World2Bv2VamsLambdaFunctionName =
                cosmosPredictConstruct.pipelineVideo2World2Bv2VamsLambdaFunctionName;
            this.pipelineText2World14Bv2VamsLambdaFunctionName =
                cosmosPredictConstruct.pipelineText2World14Bv2VamsLambdaFunctionName;
            this.pipelineVideo2World14Bv2VamsLambdaFunctionName =
                cosmosPredictConstruct.pipelineVideo2World14Bv2VamsLambdaFunctionName;
        }

        // Create Transfer pipeline (conditional on modelsTransfer being configured)
        const transferConfig = props.config.app.pipelines.useNvidiaCosmos.modelsTransfer;
        if (transferConfig?.transfer2B?.enabled) {
            const cosmosTransferConstruct = new CosmosTransferConstruct(
                this,
                "CosmosTransferPipeline",
                {
                    config: props.config,
                    storageResources: props.storageResources,
                    vpc: props.vpc,
                    pipelineSubnets: props.pipelineSubnets,
                    pipelineSecurityGroups: props.pipelineSecurityGroups,
                    lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                    importGlobalPipelineWorkflowFunctionName:
                        props.importGlobalPipelineWorkflowFunctionName,
                    // Shared resources from common construct
                    modelCacheBucket: cosmosCommon.modelCacheBucket,
                    efsFileSystem: cosmosCommon.efsFileSystem,
                    efsSecurityGroup: cosmosCommon.efsSecurityGroup,
                    // CodeBuild-built image (optional)
                    ...(codeBuildConstruct?.transferRepo
                        ? {
                              codeBuildImageUri: codeBuildConstruct.transferRepo.imageUri,
                          }
                        : {}),
                }
            );

            this.pipelineTransfer2BVamsLambdaFunctionName =
                cosmosTransferConstruct.pipelineTransfer2BVamsLambdaFunctionName;
        }

        // Create Reason pipeline (conditional on modelsReason being configured)
        const reasonConfig = props.config.app.pipelines.useNvidiaCosmos.modelsReason;
        const anyReasonEnabled = reasonConfig?.reason2B?.enabled || reasonConfig?.reason8B?.enabled;

        if (anyReasonEnabled) {
            const cosmosReasonConstruct = new CosmosReasonConstruct(this, "CosmosReasonPipeline", {
                config: props.config,
                storageResources: props.storageResources,
                vpc: props.vpc,
                pipelineSubnets: props.pipelineSubnets,
                pipelineSecurityGroups: props.pipelineSecurityGroups,
                lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                importGlobalPipelineWorkflowFunctionName:
                    props.importGlobalPipelineWorkflowFunctionName,
                // Shared resources from common construct
                modelCacheBucket: cosmosCommon.modelCacheBucket,
                efsFileSystem: cosmosCommon.efsFileSystem,
                efsSecurityGroup: cosmosCommon.efsSecurityGroup,
                // CodeBuild-built image (optional)
                ...(codeBuildConstruct?.reasonRepo
                    ? {
                          codeBuildImageUri: codeBuildConstruct.reasonRepo.imageUri,
                      }
                    : {}),
            });

            this.pipelineReason2BVamsLambdaFunctionName =
                cosmosReasonConstruct.pipelineReason2BVamsLambdaFunctionName;
            this.pipelineReason8BVamsLambdaFunctionName =
                cosmosReasonConstruct.pipelineReason8BVamsLambdaFunctionName;
        }

        // Future: other Cosmos model types would be added here
        // if (props.config.app.pipelines.useCosmosTokenize.enabled) { ... }
    }
}
