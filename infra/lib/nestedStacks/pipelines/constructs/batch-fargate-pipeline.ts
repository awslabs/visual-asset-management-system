/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as batch from "aws-cdk-lib/aws-batch";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as cdk from "aws-cdk-lib";
import * as Config from "../../../../config/config";
import { Construct } from "constructs";
import { CfnJobDefinition } from "aws-cdk-lib/aws-batch";
import { generateUniqueNameHash } from "../../../helper/security";
import path = require("path");

export interface BatchFargatePipelineConstructProps extends cdk.StackProps {
    config: Config.Config;
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
    public readonly batchJobDefinition: batch.IJobDefinition;
    public readonly batchJobQueue: batch.JobQueue;

    constructor(parent: Construct, name: string, props: BatchFargatePipelineConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };
        const region = cdk.Stack.of(this).region;
        const account = cdk.Stack.of(this).account;

        const batchEnvironment = new batch.FargateComputeEnvironment(
            this,
            "PipelineBatchComputeEnvironment",
            {
                vpc: props.vpc,
                vpcSubnets: props.vpc.selectSubnets({
                    subnets: props.subnets,
                }),
                securityGroups: props.securityGroups,
            }
        );

        // Docker container image
        const containerImage = ecs.AssetImage.fromAsset(
            path.join(__dirname, props.imageAssetPath),
            {
                file: props.dockerfileName,
                platform: cdk.aws_ecr_assets.Platform.LINUX_AMD64, //Fix to the LINUX_AMD64 platform to standardize instruction set across all loads
            }
        );

        const batchJobName = props.batchJobDefinitionName + generateUniqueNameHash(
                    props.config.env.coreStackName,
                    props.config.env.account,
                    props.batchJobDefinitionName,
                    10
                )

        this.batchJobDefinition = new batch.EcsJobDefinition(this, "PipelineBatchJobDefinition", {
            jobDefinitionName: batchJobName,
            retryAttempts: 1,
            container: new batch.EcsFargateContainerDefinition(this, "PipelineBatchContainer", {
                cpu: 16,
                memory: cdk.Size.mebibytes(65536),
                ephemeralStorageSize: cdk.Size.gibibytes(60),
                image: containerImage,
                environment: {
                    AWS_REGION: region,
                    AWS_ACCOUNT: account,
                },
                jobRole: props.jobRole,
                executionRole: props.executionRole,
                user: "root",
            }),
        });

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
