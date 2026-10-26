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
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { CfnJobDefinition, CfnComputeEnvironment, CfnJobQueue } from "aws-cdk-lib/aws-batch";
import { Service } from "../../../helper/service-helper";
import path = require("path");
import { suppressCdkNagEcrAuthTokenWildcard } from "../../../helper/security";

export interface BatchGpuPipelineConstructProps extends cdk.StackProps {
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    securityGroups: ec2.ISecurityGroup[];
    jobRole: iam.Role;
    executionRole: iam.Role;
    imageAssetPath: string;
    dockerfileName: string;
    /**
     * CodeBuild-produced ECR image. The tag travels with the repository in one prop because the tag
     * the job definition names and the tag CodeBuild pushes have to be the same string.
     */
    codeBuildImage?: { repository: ecr.IRepository; tag: string };
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

        // Create instance role and profile
        const instanceRole = new iam.Role(this, "BatchInstanceRole", {
            assumedBy: Service("EC2").Principal,
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
            `GpuComputeEnv${props.batchJobDefinitionName}`,
            {
                // No explicit name, so CloudFormation can REPLACE this environment. A
                // custom-named resource cannot be replaced -- "CloudFormation cannot update a
                // stack when a custom-named resource requires replacing" -- which left this
                // environment unable to take a change to its instance start-up script by either
                // route: not in place, because a named service role forbids that, and not by
                // replacement, because of the name. The other GPU constructs are all auto-named
                // for the same reason.
                type: "MANAGED",
                state: "ENABLED",
                // No serviceRole, so this environment uses the Batch service-linked role
                // (AWSServiceRoleForBatch), which Batch creates on demand for a managed environment. Naming a
                // role instead is what forbids an in-place update of the launch template, instance types,
                // subnets or security groups -- Batch allows those fields to be updated "only for ... Compute
                // Environment having a Batch Service Linked Role" -- leaving replacement as the only way to
                // deliver a change to the instance start-up script. VAMS already runs the Isaac Lab, Coordinate
                // Transform and Fargate environments on the service-linked role, and AWS maintains its
                // permissions as Batch gains features, which a hand-rolled role does not track.
                // Updated in PLACE for the rest of this environment's life: Batch re-merges the launch template
                // and rolls the instances, with no new environment, no job-queue re-point and no orphan left
                // behind. That needs three things together, and all three are here -- no serviceRole above (so
                // the Batch service-linked role is used), BEST_FIT_PROGRESSIVE below, and this property.
                //
                // It is safe to ship to an existing deployment only because the environment's construct id
                // changed in the same release. That makes the upgrade a CREATE of this resource and a DELETE of
                // the old one rather than an update, so this property does not have to permit the one
                // replacement the upgrade needs -- an environment still naming a service role can be neither
                // updated in place nor left alone. Setting this without the rename would fail a v2.5 upgrade.
                replaceComputeEnvironment: false,
                computeResources: {
                    type: "EC2",
                    allocationStrategy: "BEST_FIT_PROGRESSIVE",
                    // Each infrastructure update takes the current ECS-optimised AMI rather than staying on the one
                    // that was current when this environment was first created, which matters on a GPU image
                    // carrying drivers. This is also the fourth condition CloudFormation names for updating a
                    // compute environment in place, alongside no serviceRole, a progressive allocation strategy and
                    // replaceComputeEnvironment.
                    updateToLatestImageVersion: true,
                    minvCpus: 0,
                    maxvCpus: 64,
                    desiredvCpus: 0,
                    instanceTypes: ["g5.4xlarge", "g5.8xlarge", "g6.4xlarge", "g6.8xlarge"],
                    ec2Configuration: [
                        {
                            // NVIDIA-accelerated AMI for the GPU instance families above
                            imageType: "ECS_AL2023_NVIDIA",
                        },
                    ],
                    subnets: props.subnets.map((subnet) => subnet.subnetId),
                    securityGroupIds: [batchSecurityGroup.securityGroupId],
                    instanceRole: instanceProfile.attrArn,
                    launchTemplate: {
                        launchTemplateId: launchTemplate.ref,
                        // Pinned to the template's own latest version rather than "$Latest". Batch does not read this
                        // template when an instance launches: it merges it with its own bootstrap into a Batch-managed
                        // copy when the compute environment is created or updated, and "$Latest" never changes, so an
                        // edit to the user data above would not reach a running fleet.
                        version: launchTemplate.attrLatestVersionNumber,
                    },
                },
            }
        );

        // Container image resolution. A CodeBuild-built ECR repository is used directly, which
        // avoids a slow local Docker build of the large GPU image; otherwise the image is built
        // locally from imageAssetPath.
        const containerImage = props.codeBuildImage
            ? ecs.ContainerImage.fromEcrRepository(
                  props.codeBuildImage.repository,
                  props.codeBuildImage.tag
              )
            : // Synth-time asset path built from __dirname and a construct prop that the calling
              // construct hard-codes; CDK resolves it on the operator's machine, never from request
              // input.
              // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
              ecs.AssetImage.fromAsset(path.join(__dirname, props.imageAssetPath), {
                  file: props.dockerfileName,
                  platform: cdk.aws_ecr_assets.Platform.LINUX_AMD64,
              });

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

        // Binding a container image gives this task definition's execution role
        // `ecr:GetAuthorizationToken` on `*`, which is the only form Amazon ECR accepts for it. Named
        // here rather than covered by a blanket, so an unrelated wildcard acquired later still surfaces.
        suppressCdkNagEcrAuthTokenWildcard(tempTaskDef);

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
