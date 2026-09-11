/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as batch from "aws-cdk-lib/aws-batch";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecr from "aws-cdk-lib/aws-ecr";
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
    /**
     * Ephemeral storage size in GiB for the Fargate container.
     * Fargate supports 21-200 GiB. Default is 60 GiB.
     */
    ephemeralStorageGiB?: number;
    /**
     * Hard limit on a single job attempt, after which AWS Batch terminates the job itself.
     *
     * Required rather than optional so a new pipeline has to state its own bound: with no attempt
     * duration a wedged 16 vCPU / 64 GiB container runs until someone notices. The orchestration's
     * timeout is not a substitute — a pipeline that submits its job from a Lambda under
     * `WAIT_FOR_TASK_TOKEN` (coordinate transform) owns the job itself, so Step Functions giving up
     * bounds only the token wait, not the container.
     *
     * Set it to the enclosing orchestration bound (the task timeout, or the state machine timeout for
     * a `.sync` submission). Equal is correct here: the orchestration clock starts first, so it still
     * gives up before Batch does on a live execution, and this limit only takes effect once the
     * orchestration is no longer watching.
     */
    attemptDuration: cdk.Duration;
    /**
     * Optional CodeBuild-produced ECR image to use instead of a local Docker build. When provided,
     * imageAssetPath is ignored and the image is pulled from this repository at this tag.
     *
     * The tag travels with the repository in one prop rather than as a separate optional value: the
     * tag the job definition names and the tag CodeBuild pushes have to be the same string, and a
     * caller that supplies the repository alone would silently fall back to a mutable alias.
     */
    ecrImage?: { repository: ecr.IRepository; tag: string };
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

        // Container image: use ECR repository if provided, otherwise build locally
        const containerImage = props.ecrImage
            ? ecs.ContainerImage.fromEcrRepository(props.ecrImage.repository, props.ecrImage.tag)
            : ecs.AssetImage.fromAsset(path.join(__dirname, props.imageAssetPath), {
                  file: props.dockerfileName,
                  platform: cdk.aws_ecr_assets.Platform.LINUX_AMD64,
              });

        const batchJobName =
            props.batchJobDefinitionName +
            generateUniqueNameHash(
                props.config.env.coreStackName,
                props.config.env.account,
                props.batchJobDefinitionName,
                10
            );

        this.batchJobDefinition = new batch.EcsJobDefinition(this, "PipelineBatchJobDefinition", {
            jobDefinitionName: batchJobName,
            retryAttempts: 1,
            timeout: props.attemptDuration,
            container: new batch.EcsFargateContainerDefinition(this, "PipelineBatchContainer", {
                cpu: 16,
                memory: cdk.Size.mebibytes(65536),
                ephemeralStorageSize: cdk.Size.gibibytes(props.ephemeralStorageGiB ?? 60),
                image: containerImage,
                environment: {
                    AWS_REGION: region,
                    AWS_ACCOUNT: account,
                },
                jobRole: props.jobRole,
                executionRole: props.executionRole,
                // No `user` override: the job runs as whatever the image's own USER declares. An
                // override here replaces it, so a container that drops privileges in its Dockerfile
                // would still run as uid 0.
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
