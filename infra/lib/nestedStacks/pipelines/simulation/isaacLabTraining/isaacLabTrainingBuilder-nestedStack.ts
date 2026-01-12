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

        const isaacLabTraining = new IsaacLabTrainingConstruct(this, "IsaacLabTrainingConstruct", {
            ...props,
        });

        this.pipelineVamsLambdaFunctionName = isaacLabTraining.pipelineVamsLambdaFunctionName;
    }
}
