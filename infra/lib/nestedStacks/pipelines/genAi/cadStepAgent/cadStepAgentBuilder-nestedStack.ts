/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import { CadStepAgentConstruct } from "./constructs/cadStepAgent-construct";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as kms from "aws-cdk-lib/aws-kms";
import * as Config from "../../../../../config/config";

export interface CadStepAgentBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    assetAuxiliaryBucket: s3.IBucket;
    kmsKey?: kms.IKey;
    importGlobalPipelineWorkflowV2FunctionName: string;
}

/**
 * GenAI CAD STEP agent pipeline: a Strands agent that creates or modifies STEP files, hosted on
 * Amazon Bedrock AgentCore Runtime or AWS Batch on Fargate per configuration.
 */
export class CadStepAgentBuilderNestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: CadStepAgentBuilderNestedStackProps) {
        super(parent, name);

        const pipeline = new CadStepAgentConstruct(this, "CadStepAgentPipeline", {
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

        this.pipelineVamsLambdaFunctionName = pipeline.pipelineVamsLambdaFunctionName;
    }
}
