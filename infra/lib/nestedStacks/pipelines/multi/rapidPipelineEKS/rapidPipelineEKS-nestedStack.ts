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
    importGlobalPipelineWorkflowV2FunctionName: string;
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
        const kubectlLayerConstruct = new KubectlLayerConstruct(
            this,
            "KubectlLayer",
            props.config.app.pipelines.useRapidPipeline.useEks.eksClusterVersion
        );
        const kubectlLayer = kubectlLayerConstruct.layer;

        const kubernetesLayerConstruct = new KubernetesLambdaLayerConstruct(
            this,
            "KubernetesLambdaLayer"
        );
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
                importGlobalPipelineWorkflowV2FunctionName:
                    props.importGlobalPipelineWorkflowV2FunctionName,
            }
        );

        // Export the pipeline Lambda function name for registration
        this.pipelineVamsLambdaFunctionName =
            rapidPipelineEksConstruct.pipelineVamsLambdaFunctionName;

        // Add tag to track resources
        cdk.Tags.of(rapidPipelineEksConstruct).add("Pipeline", "RapidPipelineEKS");

        // CDK Nag suppressions, scoped to the resources that actually produce a finding.
        //
        // This was one `addStackSuppressions` call covering IAM4, IAM5, SF1, SF2, EKS2 and L1 across the
        // whole nested stack with `applyToChildren`, which also covered every resource added here later.
        // The entries below were authored from a measured Nag run: with the blanket removed, a full
        // `cdk synth --all` reported exactly 19 findings, and each entry names one of those resources and
        // the specific policy, log export or resource ARN it covers. L1 is gone because it produced no
        // finding at all — the suppression was dead.
        //
        // Almost everything here belongs to constructs `aws-eks` generates rather than to VAMS code,
        // which is why the findings cannot be fixed rather than suppressed.
        const eksConstructPath = `${this.node.path}/RapidPipelineEKSConstruct`;
        const clusterProviderPath = `${this.node.path}/@aws-cdk--aws-eks.ClusterResourceProvider`;

        // The kubectl handler's role: created by aws-eks, with its managed policies chosen by the
        // construct. The ECR-public policy is attached behind an Fn::If, so it is matched by regex.
        NagSuppressions.addResourceSuppressionsByPath(
            this,
            [`${eksConstructPath}/EksCluster/KubectlHandlerRole/Resource`],
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "aws-eks creates this kubectl handler and chooses its managed policies; VAMS cannot substitute them.",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonEC2ContainerRegistryPullOnly",
                        { regex: "/^Policy::.*ElasticContainerRegistryPublicReadOnly.*$/g" },
                    ],
                },
            ]
        );

        // The cluster resource provider's two handler roles, both generated by aws-eks.
        for (const handler of ["OnEventHandler", "IsCompleteHandler"]) {
            NagSuppressions.addResourceSuppressionsByPath(
                this,
                [`${clusterProviderPath}/${handler}/ServiceRole/Resource`],
                [
                    {
                        id: "AwsSolutions-IAM4",
                        reason: "aws-eks creates this cluster-resource handler and its role; VAMS does not author the policies.",
                        appliesTo: [
                            "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                            "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        ],
                    },
                ]
            );
        }

        // The cluster's own control-plane role.
        NagSuppressions.addResourceSuppressionsByPath(
            this,
            [`${eksConstructPath}/EksCluster/Role/Resource`],
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "Amazon EKS requires AmazonEKSClusterPolicy on a cluster control-plane role.",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/AmazonEKSClusterPolicy",
                    ],
                },
            ]
        );

        // aws-eks generates this waiter state machine and exposes no logging or tracing configuration.
        // VAMS's own state machine sets level ALL and tracingEnabled, so it produces neither finding.
        NagSuppressions.addResourceSuppressionsByPath(
            this,
            [`${clusterProviderPath}/Provider/waiter-state-machine/Resource`],
            [
                {
                    id: "AwsSolutions-SF1",
                    reason: "aws-eks generates this waiter state machine and exposes no logging configuration.",
                },
                {
                    id: "AwsSolutions-SF2",
                    reason: "aws-eks generates this waiter state machine and exposes no tracing configuration.",
                },
            ]
        );

        // Control plane log export is opt-in, so the finding reflects a deliberate default rather than an
        // oversight. Each export is named individually so enabling one does not silently suppress the rest.
        NagSuppressions.addResourceSuppressionsByPath(
            this,
            [`${eksConstructPath}/EksCluster/Resource/Resource/Default`],
            [
                {
                    id: "AwsSolutions-EKS2",
                    reason: "Control plane log export is opt-in via useRapidPipeline.useEks.observability, an operator cost decision.",
                    appliesTo: [
                        "LogExport::api",
                        "LogExport::audit",
                        "LogExport::authenticator",
                        "LogExport::controllerManager",
                        "LogExport::scheduler",
                    ],
                },
            ]
        );

        // VAMS's state machine role. The wildcard is X-Ray, which `tracingEnabled: true` grants and which
        // publishes no resource to scope to.
        NagSuppressions.addResourceSuppressionsByPath(
            this,
            [`${eksConstructPath}/StateMachine/Role/DefaultPolicy/Resource`],
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "X-Ray tracing grants PutTraceSegments on * because the API publishes no resource.",
                    appliesTo: ["Resource::*"],
                },
            ]
        );

        // aws-eks generates this cluster creation role; its wildcards are over the cluster's own
        // sub-resources, which do not exist until the cluster is created.
        NagSuppressions.addResourceSuppressionsByPath(
            this,
            [`${eksConstructPath}/EksCluster/Resource/CreationRole/DefaultPolicy/Resource`],
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "aws-eks generates this creation role; the wildcards cover the cluster's own sub-resources.",
                    appliesTo: [
                        "Resource::*",
                        { regex: "/^Resource::arn:.*:eks:.*:cluster\\/.*$/g" },
                        { regex: "/^Resource::arn:.*:eks:.*:fargateprofile\\/.*$/g" },
                    ],
                },
            ]
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
