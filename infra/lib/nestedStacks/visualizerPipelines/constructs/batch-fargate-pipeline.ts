/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as batch from "@aws-cdk/aws-batch-alpha";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { CfnJobDefinition } from "aws-cdk-lib/aws-batch";
import path = require("path");

export interface BatchFargatePipelineConstructProps extends cdk.StackProps {
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    securityGroups: ec2.ISecurityGroup[];
    jobRole: iam.Role;
    executionRole: iam.Role;
    imageAssetPath: string;
    dockerfileName: string;
    batchJobDefinitionName: string;
}

const defaultProps: Partial<BatchFargatePipelineConstructProps> = {
    //stackName: "",
    //env: {},
};

export class BatchFargatePipelineConstruct extends Construct {
    public readonly batchJobDefinition: batch.JobDefinition;
    public readonly batchJobQueue: batch.JobQueue;

    constructor(parent: Construct, name: string, props: BatchFargatePipelineConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };
        const region = cdk.Stack.of(this).region;
        const account = cdk.Stack.of(this).account;

        const batchEnvironment = new batch.ComputeEnvironment(
            this,
            "PipelineBatchComputeEnvironment",
            {
                managed: true,
                enabled: true,
                computeResources: {
                    vpc: props.vpc,
                    vpcSubnets: props.vpc.selectSubnets({
                        subnets: props.subnets,
                    }),
                    securityGroups: props.securityGroups,
                    type: batch.ComputeResourceType.FARGATE,
                },
            }
        );

        // Visualizer Processor Docker container image
        const containerImage = ecs.AssetImage.fromAsset(
            path.join(__dirname, props.imageAssetPath),
            {
                file: props.dockerfileName,
            }
        );

        this.batchJobDefinition = new batch.JobDefinition(this, "PipelineBatchJobDefinition", {
            jobDefinitionName: props.batchJobDefinitionName,
            platformCapabilities: [batch.PlatformCapabilities.FARGATE],
            retryAttempts: 1,
            container: {
                vcpus: 16,
                memoryLimitMiB: 65536,
                //ephemeralStorage: { sizeInGiB: 60 },
                platformVersion: ecs.FargatePlatformVersion.LATEST,
                image: containerImage,
                environment: {
                    AWS_REGION: region,
                    AWS_ACCOUNT: account,
                },
                jobRole: props.jobRole,
                executionRole: props.executionRole,
            },
        });

        // TODO: add to L2 batch.JobDefinition construct when PR is approved: https://github.com/aws/aws-cdk/pull/25399
        const cfnJobDef = this.batchJobDefinition.node.defaultChild as CfnJobDefinition;
        const containerProps =
            cfnJobDef.containerProperties as CfnJobDefinition.ContainerPropertiesProperty;
        cfnJobDef.containerProperties = {
            ephemeralStorage: {
                sizeInGiB: 60,
            },
            ...containerProps,
        };

        this.batchJobQueue = new batch.JobQueue(this, "BatchJobQueue", {
            computeEnvironments: [
                {
                    computeEnvironment: batchEnvironment,
                    order: 1,
                },
            ],
        });
    }
}
