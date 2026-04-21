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
    // Optional GPU-specific configurations
    enableGpuDeviceMappings?: boolean;
    enableSharedMemory?: boolean;
    enableUlimits?: boolean;
    enableWorkspaceVolume?: boolean;
    enablePrivilegedMode?: boolean;
    vcpus?: number;
    memory?: number;
    retryAttempts?: number;
    timeoutSeconds?: number;
    additionalEnvironmentVariables?: { name: string; value: string }[];
}

const defaultProps: Partial<BatchGpuPipelineConstructProps> = {
    enableGpuDeviceMappings: false,
    enableSharedMemory: false,
    enableUlimits: false,
    enableWorkspaceVolume: false,
    enablePrivilegedMode: false,
    vcpus: 15,
    memory: 60000,
    retryAttempts: 3,
    timeoutSeconds: 43200,
};

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
        const launchTemplateData: any = {
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
                            value: `VAMS-Batch-GPU-${props.batchJobDefinitionName}`,
                        },
                    ],
                },
            ],
        };

        // Add user data only if workspace volume is enabled
        if (props.enableWorkspaceVolume) {
            const userData = `MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="==MYBOUNDARY=="

--==MYBOUNDARY==
Content-Type: text/x-shellscript; charset="us-ascii"

#!/bin/bash
mkdir -p /mnt/workspace
chown ecs-agent:ecs-agent /mnt/workspace
chmod 775 /mnt/workspace

--==MYBOUNDARY==--
`;
            launchTemplateData.userData = Buffer.from(userData).toString("base64");
        }

        const launchTemplate = new ec2.CfnLaunchTemplate(this, "BatchLaunchTemplate", {
            launchTemplateData,
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
                platform: cdk.aws_ecr_assets.Platform.LINUX_AMD64,
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

        // Build container properties dynamically based on configuration
        const containerProperties: any = {
            image: container.imageName,
            vcpus: props.vcpus!,
            memory: props.memory!,
            jobRoleArn: props.jobRole.roleArn,
            executionRoleArn: props.executionRole.roleArn,
            command: props.containerExecutionCommand,
            resourceRequirements: [
                {
                    type: "GPU",
                    value: "1",
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
                ...(props.additionalEnvironmentVariables || []),
            ],
        };

        // Add privileged mode if enabled
        if (props.enablePrivilegedMode) {
            containerProperties.privileged = true;
        }

        // Add Linux parameters if GPU device mappings or shared memory enabled
        if (props.enableGpuDeviceMappings || props.enableSharedMemory) {
            containerProperties.linuxParameters = {};

            if (props.enableSharedMemory) {
                containerProperties.linuxParameters.sharedMemorySize = 8192;
            }

            if (props.enableGpuDeviceMappings) {
                containerProperties.linuxParameters.devices = [
                    {
                        hostPath: "/dev/nvidia0",
                        containerPath: "/dev/nvidia0",
                        permissions: ["READ", "WRITE", "MKNOD"],
                    },
                    {
                        hostPath: "/dev/nvidiactl",
                        containerPath: "/dev/nvidiactl",
                        permissions: ["READ", "WRITE", "MKNOD"],
                    },
                    {
                        hostPath: "/dev/nvidia-uvm",
                        containerPath: "/dev/nvidia-uvm",
                        permissions: ["READ", "WRITE", "MKNOD"],
                    },
                ];
                // Add NVIDIA environment variables
                containerProperties.environment.push(
                    {
                        name: "LD_LIBRARY_PATH",
                        value: "/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64:/usr/local/nvidia/lib:/usr/local/nvidia/lib64",
                    },
                    {
                        name: "NVIDIA_DRIVER_CAPABILITIES",
                        value: "compute,utility,graphics",
                    }
                );
            }
        }

        // Add mount points and volumes
        if (props.enableWorkspaceVolume || props.enableSharedMemory) {
            containerProperties.mountPoints = [];
            containerProperties.volumes = [];

            if (props.enableWorkspaceVolume) {
                containerProperties.mountPoints.push({
                    sourceVolume: "workspace",
                    containerPath: "/tmp",
                    readOnly: false,
                });
                containerProperties.volumes.push({
                    name: "workspace",
                    host: {
                        sourcePath: "/mnt/workspace",
                    },
                });
            } else {
                // Default /tmp mount
                containerProperties.mountPoints.push({
                    sourceVolume: "tmp",
                    containerPath: "/tmp",
                    readOnly: false,
                });
                containerProperties.volumes.push({
                    name: "tmp",
                    host: {
                        sourcePath: "/tmp",
                    },
                });
            }

            if (props.enableSharedMemory) {
                containerProperties.mountPoints.push({
                    sourceVolume: "shm",
                    containerPath: "/dev/shm",
                    readOnly: false,
                });
                containerProperties.volumes.push({
                    name: "shm",
                    host: {
                        sourcePath: "/dev/shm",
                    },
                });
            }
        }

        // Add ulimits if enabled
        if (props.enableUlimits) {
            containerProperties.ulimits = [
                {
                    name: "memlock",
                    softLimit: -1,
                    hardLimit: -1,
                },
                {
                    name: "stack",
                    softLimit: 67108864,
                    hardLimit: 67108864,
                },
            ];
        }

        const cfnJobDefinition = new CfnJobDefinition(this, "PipelineBatchJobDefinition", {
            jobDefinitionName: props.batchJobDefinitionName,
            type: "container",
            containerProperties,
            retryStrategy: {
                attempts: props.retryAttempts!,
            },
            timeout: {
                attemptDurationSeconds: props.timeoutSeconds!,
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
