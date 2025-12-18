/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { NestedStack } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cr from "aws-cdk-lib/custom-resources";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";

import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import * as Config from "../../../../../config/config";
import { RapidPipelineEKSConstruct } from "./constructs/rapidPipelineEKS-construct";
import { KubernetesLambdaLayerConstruct } from "./constructs/kubernetes-layer-construct";
import { KubectlLayerConstruct } from "./constructs/kubectl-layer-construct";

export interface RapidPipelineEKSNestedStackProps extends cdk.NestedStackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnetsPrivate: ec2.ISubnet[];
    pipelineSubnetsIsolated?: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowFunctionName: string;
}

/**
 * Default input properties
 */
const defaultProps: Partial<RapidPipelineEKSNestedStackProps> = {};

/**
 * Deploys a Step Function for EKS Job workflow
 * Creates:
 * - Lambda Layers (kubectl and Kubernetes Python client)
 * - EKS Cluster with node group
 * - Step Functions State Machine
 * - Lambda Functions for pipeline operations
 * - IAM Roles and Policies
 */
export class RapidPipelineEKSNestedStack extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: RapidPipelineEKSNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        console.log("Creating RapidPipeline EKS implementation with shared pipeline network");

        // 1. Create Lambda layers for EKS operations
        const kubectlLayerConstruct = new KubectlLayerConstruct(this, "KubectlLayer");
        const kubectlLayer = kubectlLayerConstruct.layer;

        const kubernetesLayerConstruct = new KubernetesLambdaLayerConstruct(this, "KubernetesLambdaLayer");
        const kubernetesLayer = kubernetesLayerConstruct.layer;

        // 2. Create the EKS construct with all pipeline resources
        const rapidPipelineEksConstruct = new RapidPipelineEKSConstruct(
            this,
            "RapidPipelineEKSConstruct",
            {
                config: props.config,
                // Use existing VPC (configured with 2 AZs when EKS is enabled)
                vpc: props.vpc,
                pipelineSubnetsPrivate: props.pipelineSubnetsPrivate,
                pipelineSecurityGroups: props.pipelineSecurityGroups,
                storageResources: props.storageResources,
                lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                kubectlLayer: kubectlLayer, // Multi-runtime kubectl layer for EKS cluster
                kubernetesLayer: kubernetesLayer, // Kubernetes Python client layer for Lambda functions
                importGlobalPipelineWorkflowFunctionName:
                    props.importGlobalPipelineWorkflowFunctionName,
            }
        );

        // Export the pipeline Lambda function name for registration
        this.pipelineVamsLambdaFunctionName =
            rapidPipelineEksConstruct.pipelineVamsLambdaFunctionName;

        // Add tag to track resources
        cdk.Tags.of(rapidPipelineEksConstruct).add("Pipeline", "RapidPipelineEKS");

        // Add stack-level CDK Nag suppressions for EKS-generated resources
        NagSuppressions.addStackSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "EKS cluster and Lambda functions require wildcard permissions for dynamic resource access within the VAMS pipeline system. Permissions are scoped to specific resource types and follow least privilege principles.",
                },
                {
                    id: "AwsSolutions-IAM4",
                    reason: "EKS requires AWS managed policies (AmazonEKSWorkerNodePolicy, AmazonEKS_CNI_Policy, AmazonEC2ContainerRegistryReadOnly) for proper cluster operation as per AWS best practices.",
                },
                {
                    id: "AwsSolutions-SF1",
                    reason: "Step Functions state machine has comprehensive CloudWatch logging enabled with includeExecutionData and LogLevel.ALL.",
                },
                {
                    id: "AwsSolutions-SF2",
                    reason: "Step Functions state machine has X-Ray tracing enabled for monitoring and debugging.",
                },
                {
                    id: "AwsSolutions-EKS1",
                    reason: "EKS cluster uses PUBLIC endpoint access to force Lambda functions to use NAT Gateway instead of VPC endpoints, ensuring reliable connectivity.",
                },
                {
                    id: "AwsSolutions-EKS2",
                    reason: "Control plane logging can be enabled in production deployments. Disabled in development to reduce costs.",
                },
                {
                    id: "AwsSolutions-L1",
                    reason: "Using Python 3.12 runtime which is the latest supported version for Lambda and compatible with Kubernetes Python client layer.",
                },
            ],
            true
        );

        // Add suppressions for CDK-generated EKS provider resources
        NagSuppressions.addResourceSuppressionsByPath(
            this,
            [`/${this.node.path}/@aws-cdk--aws-eks.ClusterResourceProvider/Provider`],
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "CDK-generated EKS cluster resource provider requires AWS managed policies for cluster management.",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "CDK-generated EKS cluster resource provider requires wildcard permissions for cluster operations.",
                },
                {
                    id: "AwsSolutions-L1",
                    reason: "CDK-generated resource uses specific Lambda runtime for compatibility.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            this,
            [`/${this.node.path}/@aws-cdk--aws-eks.KubectlProvider/Provider`],
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "CDK-generated kubectl provider requires AWS managed policies for Kubernetes operations.",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "CDK-generated kubectl provider requires wildcard permissions for Kubernetes API access.",
                },
                {
                    id: "AwsSolutions-L1",
                    reason: "CDK-generated resource uses specific Lambda runtime for kubectl compatibility.",
                },
            ],
            true
        );

        // Add suppressions for Lambda layers
        NagSuppressions.addResourceSuppressionsByPath(
            this,
            [`/${this.node.path}/KubectlLayer`, `/${this.node.path}/KubernetesLambdaLayer`],
            [
                {
                    id: "AwsSolutions-L1",
                    reason: "Lambda layers use specific runtime versions for compatibility with EKS operations and Kubernetes Python client.",
                },
            ],
            true
        );

        // Add suppressions for Lambda functions
        NagSuppressions.addResourceSuppressionsByPath(
            this,
            [
                `/${this.node.path}/RapidPipelineEKSConstruct/ConsolidatedHandler`,
                `/${this.node.path}/RapidPipelineEKSConstruct/OpenPipelineHandler`,
                `/${this.node.path}/RapidPipelineEKSConstruct/VamsExecuteHandler`,
            ],
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Lambda functions require wildcard permissions for EKS cluster operations, dynamic S3 bucket access, and Step Functions integration within the VAMS pipeline system.",
                },
                {
                    id: "AwsSolutions-L1",
                    reason: "Using Python 3.12 runtime which is the latest supported version and compatible with Kubernetes Python client layer.",
                },
            ],
            true
        );

        console.log("RapidPipeline EKS nested stack created successfully");
        console.log(
            `Pipeline Lambda function: ${this.pipelineVamsLambdaFunctionName || "pending"}`
        );

    
    }
}
