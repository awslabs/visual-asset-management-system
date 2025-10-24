/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 * 
 * GPU-specific batch pipeline construct for SplatToolbox and other GPU workloads
 */
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as batch from "aws-cdk-lib/aws-batch";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { CfnJobDefinition, CfnComputeEnvironment, CfnJobQueue } from "aws-cdk-lib/aws-batch";
import path = require("path");

export interface BatchGpuPipelineConstructProps extends cdk.StackProps {
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    securityGroups: ec2.ISecurityGroup[];
    jobRole: iam.Role;
    executionRole: iam.Role;
    imageAssetPath: string;
    dockerfileName: string;
    containerExecutionCommand: string[];
    batchJobDefinitionName: string;
}

const defaultProps: Partial<BatchGpuPipelineConstructProps> = {};

export class BatchGpuPipelineConstruct extends Construct {
    public readonly batchJobDefinition: batch.CfnJobDefinition;
    public readonly batchJobQueue: batch.CfnJobQueue;

    constructor(parent: Construct, name: string, props: BatchGpuPipelineConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };
        const region = cdk.Stack.of(this).region;
        const account = cdk.Stack.of(this).account;

        // Create batch service role
        const batchServiceRole = new iam.Role(this, "BatchServiceRole", {
            assumedBy: new iam.ServicePrincipal("batch.amazonaws.com"),
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSBatchServiceRole"),
            ],
        });

        // Create instance role and profile
        const instanceRole = new iam.Role(this, "BatchInstanceRole", {
            assumedBy: new iam.ServicePrincipal("ec2.amazonaws.com"),
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonEC2ContainerServiceforEC2Role"
                ),
            ],
        });

        const instanceProfile = new iam.CfnInstanceProfile(this, "BatchInstanceProfile", {
            roles: [instanceRole.roleName],
        });

        // Create security group with outbound internet access
        const batchSecurityGroup = new ec2.SecurityGroup(this, "BatchSecurityGroup", {
            vpc: props.vpc,
            description: "Security group for Batch compute environment with internet access",
            allowAllOutbound: true,
        });
        
        // Create launch template
        const launchTemplate = new ec2.CfnLaunchTemplate(this, "BatchLaunchTemplate", {
            launchTemplateData: {
                blockDeviceMappings: [
                    {
                        deviceName: "/dev/xvda",
                        ebs: {
                            volumeSize: 200,
                            volumeType: "gp3",
                            encrypted: true,
                            deleteOnTermination: true,
                        },
                    },
                ],
                tagSpecifications: [
                    {
                        resourceType: "instance",
                        tags: [
                            {
                                key: "Name",
                                value: `VAMS-Batch-GPU-${props.batchJobDefinitionName}`
                            }
                        ]
                    }
                ]
            },
        });
        
        // Create on-demand compute environment
        const batchEnvironment = new CfnComputeEnvironment(
            this,
            `OnDemandComputeEnv${props.batchJobDefinitionName}`,
            {
                computeEnvironmentName: `VAMS-GPU-ONDEMAND-${props.batchJobDefinitionName}`,
                type: "MANAGED",
                state: "ENABLED",
                serviceRole: batchServiceRole.roleArn,
                computeResources: {
                    type: "EC2",
                    allocationStrategy: "BEST_FIT_PROGRESSIVE",
                    minvCpus: 0,
                    maxvCpus: 64,
                    desiredvCpus: 0,
                    instanceTypes: ["g5.4xlarge", "g5.8xlarge", "g6.4xlarge", "g6.8xlarge"],
                    ec2Configuration: [
                        {
                            imageType: "ECS_AL2",
                        },
                    ],
                    subnets: props.subnets.map((subnet) => subnet.subnetId),
                    securityGroupIds: [batchSecurityGroup.securityGroupId],
                    instanceRole: instanceProfile.attrArn,
                    launchTemplate: {
                        launchTemplateId: launchTemplate.ref,
                        version: "$Latest",
                    },
                },
            }
        );

        // Docker container image
        const containerImage = ecs.AssetImage.fromAsset(
            path.join(__dirname, props.imageAssetPath),
            {
                file: props.dockerfileName,
                platform: cdk.aws_ecr_assets.Platform.LINUX_AMD64
            }
        );

        // Create a temporary task definition to bind the image
        const tempTaskDef = new ecs.TaskDefinition(this, "TempTaskDef", {
            compatibility: ecs.Compatibility.EC2,
        });
        
        const container = tempTaskDef.addContainer("Container", {
            image: containerImage,
            memoryLimitMiB: 1024,
            logging: ecs.LogDrivers.awsLogs({
                streamPrefix: "batch-temp",
            }),
        });

        const cfnJobDefinition = new CfnJobDefinition(this, "PipelineBatchJobDefinition", {
            jobDefinitionName: props.batchJobDefinitionName,
            type: "container",
            containerProperties: {
                image: container.imageName,
                vcpus: 15,
                memory: 60000,
                jobRoleArn: props.jobRole.roleArn,
                executionRoleArn: props.executionRole.roleArn,
                command: props.containerExecutionCommand,
                resourceRequirements: [
                    {
                        type: "GPU",
                        value: "1",
                    },
                ],
                mountPoints: [
                    {
                        sourceVolume: "tmp",
                        containerPath: "/tmp",
                        readOnly: false,
                    },
                ],
                volumes: [
                    {
                        name: "tmp",
                        host: {
                            sourcePath: "/tmp",
                        },
                    },
                ],
                environment: [
                    {
                        name: "AWS_REGION",
                        value: region,
                    },
                    {
                        name: "AWS_ACCOUNT",
                        value: account,
                    },
                ],
            },
            retryStrategy: {
                attempts: 3,
            },
            timeout: {
                attemptDurationSeconds: 43200,
            },
        });

        const cfnJobQueue = new CfnJobQueue(this, "BatchJobQueue", {
            jobQueueName: `VAMS-GPU-Queue-${props.batchJobDefinitionName}`,
            state: "ENABLED",
            priority: 1,
            computeEnvironmentOrder: [
                {
                    order: 1,
                    computeEnvironment: batchEnvironment.ref,
                },
            ],
        });

        // Assign the CFN resources directly
        this.batchJobDefinition = cfnJobDefinition;
        this.batchJobQueue = cfnJobQueue;
    }
}
