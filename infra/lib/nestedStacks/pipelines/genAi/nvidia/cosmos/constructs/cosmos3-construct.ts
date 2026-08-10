/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { storageResources } from "../../../../../storage/storageBuilder-nestedStack";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as efs from "aws-cdk-lib/aws-efs";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as batch from "aws-cdk-lib/aws-batch";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Duration, Stack, RemovalPolicy } from "aws-cdk-lib";
import { Construct } from "constructs";
import {
    buildConstructPipelineFunction,
    buildOpenPipelineFunction,
    buildVamsExecuteCosmos3PipelineFunction,
    buildPipelineEndFunction,
} from "../lambdaBuilder/cosmos3Functions";
import { NagSuppressions } from "cdk-nag";
import { CfnOutput } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as ServiceHelper from "../../../../../../helper/service-helper";
import { Service } from "../../../../../../helper/service-helper";
import * as s3AssetBuckets from "../../../../../../helper/s3AssetBuckets";
import * as Config from "../../../../../../../config/config";
import {
    generateUniqueNameHash,
    kmsKeyPolicyStatementGenerator,
    grantExternalAssetBucketKmsKeys,
} from "../../../../../../helper/security";
import { VamsSchemaRegistration } from "../../../../constructs/vamsSchemaRegistration-construct";
import { populateHuggingFaceTokenSecret } from "../../customResources/populateHuggingFaceTokenSecret";
import { DockerImageAsset, Platform } from "aws-cdk-lib/aws-ecr-assets";

export interface Cosmos3ConstructProps extends cdk.StackProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowV2FunctionName: string;
    // From common construct:
    modelCacheBucket: s3.Bucket;
    efsFileSystem: efs.FileSystem;
    efsSecurityGroup: ec2.SecurityGroup;
    // Optional: CodeBuild-built image URI from ECR (bypasses local Docker build)
    codeBuildImageUri?: string;
}

/**
 * Default input properties
 */
const defaultProps: Partial<Cosmos3ConstructProps> = {};

export class Cosmos3Construct extends Construct {
    public pipelineCosmos3Nano16BVamsLambdaFunctionName?: string;
    public pipelineCosmos3Super64BVamsLambdaFunctionName?: string;
    public pipelineCosmos3SuperText2Image64BVamsLambdaFunctionName?: string;
    public pipelineCosmos3SuperImage2Video64BVamsLambdaFunctionName?: string;

    constructor(parent: Construct, name: string, props: Cosmos3ConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;
        const cosmosConfig = props.config.app.pipelines.useNvidiaCosmos3;

        /**
         * HuggingFace Token stored in Secrets Manager
         * Batch injects the secret into the container so the token is never an env var value.
         * The secret is created EMPTY and populated at deploy time from config by a custom
         * resource that carries the token in its code asset, so the token never lands in the
         * synthesized CloudFormation template.
         */
        const hfTokenSecret = new secretsmanager.Secret(this, "CosmosHfTokenSecret", {
            description: "HuggingFace API token for downloading NVIDIA Cosmos models",
        });

        populateHuggingFaceTokenSecret(
            this,
            "CosmosHfTokenSecretPopulate",
            hfTokenSecret,
            cosmosConfig.huggingFaceToken
        );

        NagSuppressions.addResourceSuppressions(
            hfTokenSecret,
            [
                {
                    id: "AwsSolutions-SMG4",
                    reason: "HuggingFace API token is externally managed by the user. Automatic rotation is not applicable as the token lifecycle is controlled by the HuggingFace account holder, not AWS.",
                },
            ],
            true
        );

        /**
         * Shared Cosmos resources (provided by CosmosCommonConstruct)
         */
        const modelCacheBucket = props.modelCacheBucket;
        const cosmosEfs = props.efsFileSystem;
        const nfsSecurityGroup = props.efsSecurityGroup;

        /**
         * Docker Container Image from ECR
         * Only built if any model is enabled.
         * If codeBuildImageUri is provided, use that directly (CodeBuild-built image in ECR).
         * Otherwise, fall back to inline DockerImageAsset build.
         */
        const anyEnabled =
            cosmosConfig.modelsOmni.nano16B?.enabled ||
            cosmosConfig.modelsOmni.super64B?.enabled ||
            cosmosConfig.modelsOmni.superText2Image64B?.enabled ||
            cosmosConfig.modelsOmni.superImage2Video64B?.enabled;

        let containerImage: DockerImageAsset | null = null;
        if (anyEnabled && !props.codeBuildImageUri) {
            containerImage = new DockerImageAsset(this, "CosmosContainerImage", {
                directory: path.join(
                    __dirname,
                    "../../../../../../../../backendPipelines/genAi/nvidia/cosmos/3/container"
                ),
                platform: Platform.LINUX_AMD64,
            });
        }

        /**
         * IAM Policies for Batch container roles
         */
        const inputBucketPolicy = new iam.PolicyDocument({
            statements: [
                ...s3AssetBuckets.getS3AssetBucketRecords().map((record) => {
                    const prefix = record.prefix || "/";
                    // Build the object-level resource as {bucketArn}/{prefix}*. Strip any
                    // leading slash from the prefix so the '/' separator after the bucket
                    // ARN is always present (root prefix yields {bucketArn}/*).
                    const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";
                    const objectPrefix = normalizedPrefix.replace(/^\/+/, "");
                    return new iam.PolicyStatement({
                        effect: iam.Effect.ALLOW,
                        actions: [
                            "s3:PutObject",
                            "s3:GetObject",
                            "s3:ListBucket",
                            "s3:DeleteObject",
                            "s3:GetObjectVersion",
                        ],
                        resources: [
                            record.bucket.bucketArn,
                            `${record.bucket.bucketArn}/${objectPrefix}*`,
                        ],
                    });
                }),
                // Model cache bucket access
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:DeleteObject"],
                    resources: [modelCacheBucket.bucketArn, `${modelCacheBucket.bucketArn}/*`],
                }),
            ],
        });

        const outputBucketPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    resources: [
                        props.storageResources.s3.assetAuxiliaryBucket.bucketArn,
                        `${props.storageResources.s3.assetAuxiliaryBucket.bucketArn}/*`,
                    ],
                }),
                new iam.PolicyStatement({
                    actions: ["s3:ListBucket"],
                    resources: [props.storageResources.s3.assetAuxiliaryBucket.bucketArn],
                }),
            ],
        });

        // Add KMS key permissions if provided
        if (props.storageResources.encryption.kmsKey) {
            inputBucketPolicy.addStatements(
                kmsKeyPolicyStatementGenerator(props.storageResources.encryption.kmsKey)
            );
            outputBucketPolicy.addStatements(
                kmsKeyPolicyStatementGenerator(props.storageResources.encryption.kmsKey)
            );
        }

        const containerExecutionRole = new iam.Role(this, "CosmosContainerExecutionRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            inlinePolicies: {
                InputBucketPolicy: inputBucketPolicy,
                OutputBucketPolicy: outputBucketPolicy,
            },
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
            ],
        });

        // Grant execution role access to read the HF token secret (required for Batch secrets injection)
        hfTokenSecret.grantRead(containerExecutionRole);

        const efsClientPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: [
                        "elasticfilesystem:ClientMount",
                        "elasticfilesystem:ClientWrite",
                        "elasticfilesystem:ClientRootAccess",
                    ],
                    resources: [cosmosEfs.fileSystemArn],
                }),
            ],
        });

        const containerJobRole = new iam.Role(this, "CosmosContainerJobRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            inlinePolicies: {
                InputBucketPolicy: inputBucketPolicy,
                OutputBucketPolicy: outputBucketPolicy,
                EfsClientPolicy: efsClientPolicy,
            },
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
            ],
        });

        // Grant access to any external asset bucket customer managed KMS keys so the
        // container can read/write objects in cross-account encrypted buckets
        // (no-op when no external keys are configured)
        grantExternalAssetBucketKmsKeys(containerJobRole);

        /**
         * Batch Compute Environment
         * Shared across all Cosmos model types for GPU-accelerated inference
         */
        const batchServiceRole = new iam.Role(this, "BatchServiceRole", {
            assumedBy: new iam.ServicePrincipal("batch.amazonaws.com"),
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSBatchServiceRole"),
            ],
        });

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

        // Batch compute security group - allow outbound and NFS access to EFS
        const batchSecurityGroup = new ec2.SecurityGroup(this, "BatchSecurityGroup", {
            vpc: props.vpc,
            description:
                "Security group for Cosmos 3 Batch compute environment with internet access",
            allowAllOutbound: true,
        });

        // Allow NFS traffic from Batch compute SG to EFS SG
        nfsSecurityGroup.addIngressRule(
            batchSecurityGroup,
            ec2.Port.tcp(2049),
            "Allow NFS from Cosmos 3 Batch compute to EFS"
        );

        // Determine single-GPU tier instance types from the Nano model config (or default)
        const instanceTypes = cosmosConfig.modelsOmni.nano16B?.enabled
            ? cosmosConfig.modelsOmni.nano16B.instanceTypes
            : ["g6e.4xlarge", "g6e.12xlarge"];

        // Max vCPUs for the single-GPU (Nano) tier
        const maxVCpus = Math.max(
            cosmosConfig.modelsOmni.nano16B?.enabled ? cosmosConfig.modelsOmni.nano16B.maxVCpus : 0,
            48
        );

        // Warm instances: if enabled, keep minVCpus at warmInstanceCount * 48 vCPUs
        const minVCpus =
            cosmosConfig.useWarmInstances && cosmosConfig.warmInstanceCount > 0
                ? cosmosConfig.warmInstanceCount * 48
                : 0;

        // Build user data for EFS mount on Batch instances
        const userData = `MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="==MYBOUNDARY=="

--==MYBOUNDARY==
Content-Type: text/x-shellscript; charset="us-ascii"

#!/bin/bash
yum install -y amazon-efs-utils
mkdir -p /mnt/efs/cosmos-models
mount -t efs -o tls ${cosmosEfs.fileSystemId}:/ /mnt/efs/cosmos-models
echo "${cosmosEfs.fileSystemId}:/ /mnt/efs/cosmos-models efs _netdev,tls 0 0" >> /etc/fstab

--==MYBOUNDARY==--
`;

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
                userData: Buffer.from(userData).toString("base64"),
                tagSpecifications: [
                    {
                        resourceType: "instance",
                        tags: [
                            {
                                key: "Name",
                                value: `VAMS-Cosmos3-Nano-Batch`,
                            },
                        ],
                    },
                ],
            },
        });

        const batchEnvironment = new batch.CfnComputeEnvironment(this, "CosmosOnDemandComputeEnv", {
            // No explicit name - let CDK auto-generate to allow CloudFormation replacements
            // when instance types change (custom-named resources can't be replaced in-place)
            type: "MANAGED",
            state: "ENABLED",
            serviceRole: batchServiceRole.roleArn,
            computeResources: {
                type: "EC2",
                allocationStrategy: "BEST_FIT_PROGRESSIVE",
                minvCpus: minVCpus,
                maxvCpus: maxVCpus * 2, // Allow headroom for concurrent jobs
                desiredvCpus: minVCpus,
                instanceTypes: instanceTypes,
                ec2Configuration: [
                    {
                        imageType: "ECS_AL2023_NVIDIA",
                    },
                ],
                subnets: props.pipelineSubnets.map((subnet) => subnet.subnetId),
                securityGroupIds: [batchSecurityGroup.securityGroupId],
                instanceRole: instanceProfile.attrArn,
                launchTemplate: {
                    launchTemplateId: launchTemplate.ref,
                    version: "$Latest",
                },
            },
        });

        const batchJobQueue = new batch.CfnJobQueue(this, "CosmosBatchJobQueue", {
            // No explicit name - let CDK auto-generate to allow CloudFormation replacements
            state: "ENABLED",
            priority: 1,
            computeEnvironmentOrder: [
                {
                    order: 1,
                    computeEnvironment: batchEnvironment.ref,
                },
            ],
        });

        /**
         * Large GPU Compute Environment for Super (64B) models (conditional)
         * Multi-GPU instances with 1024 GB EBS for larger model weights
         */
        const anySuperEnabled =
            cosmosConfig.modelsOmni.super64B?.enabled ||
            cosmosConfig.modelsOmni.superText2Image64B?.enabled ||
            cosmosConfig.modelsOmni.superImage2Video64B?.enabled;

        let batchEnvironmentSuper: batch.CfnComputeEnvironment | undefined;
        let batchJobQueueSuper: batch.CfnJobQueue | undefined;

        if (anySuperEnabled) {
            // Determine instance types and maxVCpus from the first enabled Super model config
            const instanceTypesSuper = cosmosConfig.modelsOmni.super64B?.enabled
                ? cosmosConfig.modelsOmni.super64B.instanceTypes
                : cosmosConfig.modelsOmni.superText2Image64B?.enabled
                ? cosmosConfig.modelsOmni.superText2Image64B.instanceTypes
                : cosmosConfig.modelsOmni.superImage2Video64B.instanceTypes;

            const maxVCpusSuper = Math.max(
                cosmosConfig.modelsOmni.super64B?.enabled
                    ? cosmosConfig.modelsOmni.super64B.maxVCpus
                    : 0,
                cosmosConfig.modelsOmni.superText2Image64B?.enabled
                    ? cosmosConfig.modelsOmni.superText2Image64B.maxVCpus
                    : 0,
                cosmosConfig.modelsOmni.superImage2Video64B?.enabled
                    ? cosmosConfig.modelsOmni.superImage2Video64B.maxVCpus
                    : 0,
                96
            );

            // Build user data for EFS mount on Super Batch instances
            const userDataSuper = `MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="==MYBOUNDARY=="

--==MYBOUNDARY==
Content-Type: text/x-shellscript; charset="us-ascii"

#!/bin/bash
yum install -y amazon-efs-utils
mkdir -p /mnt/efs/cosmos-models
mount -t efs -o tls ${cosmosEfs.fileSystemId}:/ /mnt/efs/cosmos-models
echo "${cosmosEfs.fileSystemId}:/ /mnt/efs/cosmos-models efs _netdev,tls 0 0" >> /etc/fstab

--==MYBOUNDARY==--
`;

            const launchTemplateSuper = new ec2.CfnLaunchTemplate(
                this,
                "BatchLaunchTemplateSuper",
                {
                    launchTemplateData: {
                        blockDeviceMappings: [
                            {
                                deviceName: "/dev/xvda",
                                ebs: {
                                    volumeSize: 1024,
                                    volumeType: "gp3",
                                    encrypted: true,
                                    deleteOnTermination: true,
                                },
                            },
                        ],
                        userData: Buffer.from(userDataSuper).toString("base64"),
                        tagSpecifications: [
                            {
                                resourceType: "instance",
                                tags: [
                                    {
                                        key: "Name",
                                        value: `VAMS-Cosmos3-Super-Batch`,
                                    },
                                ],
                            },
                        ],
                    },
                }
            );

            batchEnvironmentSuper = new batch.CfnComputeEnvironment(
                this,
                "CosmosOnDemandComputeEnvSuper",
                {
                    type: "MANAGED",
                    state: "ENABLED",
                    serviceRole: batchServiceRole.roleArn,
                    computeResources: {
                        type: "EC2",
                        allocationStrategy: "BEST_FIT_PROGRESSIVE",
                        minvCpus: 0,
                        maxvCpus: maxVCpusSuper * 2,
                        desiredvCpus: 0,
                        instanceTypes: instanceTypesSuper,
                        ec2Configuration: [
                            {
                                imageType: "ECS_AL2023_NVIDIA",
                            },
                        ],
                        subnets: props.pipelineSubnets.map((subnet) => subnet.subnetId),
                        securityGroupIds: [batchSecurityGroup.securityGroupId],
                        instanceRole: instanceProfile.attrArn,
                        launchTemplate: {
                            launchTemplateId: launchTemplateSuper.ref,
                            version: "$Latest",
                        },
                    },
                }
            );

            batchJobQueueSuper = new batch.CfnJobQueue(this, "CosmosBatchJobQueueSuper", {
                state: "ENABLED",
                priority: 1,
                computeEnvironmentOrder: [
                    {
                        order: 1,
                        computeEnvironment: batchEnvironmentSuper.ref,
                    },
                ],
            });
        }

        /**
         * Container image reference for job definitions
         * If codeBuildImageUri is provided, use that directly.
         * Otherwise, resolve via DockerImageAsset + ECS temp task definition.
         */
        let containerImageName: string | undefined;
        if (props.codeBuildImageUri) {
            // Use CodeBuild-built image from ECR
            containerImageName = props.codeBuildImageUri;
        } else if (containerImage) {
            // Fall back to inline DockerImageAsset build
            const tempTaskDef = new ecs.TaskDefinition(this, "TempTaskDef", {
                compatibility: ecs.Compatibility.EC2,
            });
            const container = tempTaskDef.addContainer("Container", {
                image: ecs.ContainerImage.fromDockerImageAsset(containerImage),
                memoryLimitMiB: 1024,
                logging: ecs.LogDrivers.awsLogs({
                    streamPrefix: "batch-temp",
                }),
            });
            containerImageName = container.imageName;
        }

        /**
         * Shared Lambda Functions
         */
        const constructPipelineFunction = buildConstructPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            props.storageResources.encryption.kmsKey
        );

        const pipelineEndFunction = buildPipelineEndFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources.s3.assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            props.storageResources.encryption.kmsKey
        );

        /**
         * Helper to create per-model resources (job definition, SFN, lambdas, registration)
         */
        const createModelResources = (
            modelKey: string,
            variant: string,
            taskMode: string,
            pipelineId: string,
            pipelineDescription: string,
            isAutoRegister: boolean,
            autoTriggerExtensions: string,
            outputType: string,
            containerImageName: string,
            computeEnv: batch.CfnComputeEnvironment,
            jobQueue: batch.CfnJobQueue,
            gpuCount: number,
            memoryMb: number,
            vcpus: number
        ) => {
            // Batch Job Definition
            const jobDefName = `Cosmos3GpuJob-${modelKey}-${
                props.config.name + "_" + props.config.app.baseStackName
            }`;

            const containerProperties: any = {
                image: containerImageName,
                vcpus: vcpus,
                memory: memoryMb,
                jobRoleArn: containerJobRole.roleArn,
                executionRoleArn: containerExecutionRole.roleArn,
                command: ["python", "__main__.py"],
                privileged: true,
                resourceRequirements: [
                    {
                        type: "GPU",
                        value: String(gpuCount),
                    },
                ],
                linuxParameters: {
                    sharedMemorySize: 32768,
                    devices: Array.from({ length: gpuCount }, (_, i) => ({
                        hostPath: `/dev/nvidia${i}`,
                        containerPath: `/dev/nvidia${i}`,
                        permissions: ["READ", "WRITE", "MKNOD"],
                    })).concat([
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
                    ]),
                },
                environment: [
                    { name: "MODEL_VARIANT", value: variant },
                    { name: "TASK_MODE", value: taskMode },
                    { name: "NUM_GPUS", value: String(gpuCount) },
                    { name: "AWS_REGION", value: region },
                    { name: "S3_MODEL_BUCKET", value: modelCacheBucket.bucketName },
                ],
                secrets: [
                    {
                        name: "HF_TOKEN",
                        valueFrom: hfTokenSecret.secretArn,
                    },
                ],
                mountPoints: [
                    {
                        sourceVolume: "cosmos-models",
                        containerPath: "/mnt/efs/cosmos-models",
                        readOnly: false,
                    },
                    {
                        sourceVolume: "shm",
                        containerPath: "/dev/shm",
                        readOnly: false,
                    },
                ],
                volumes: [
                    {
                        name: "cosmos-models",
                        host: {
                            sourcePath: "/mnt/efs/cosmos-models",
                        },
                    },
                    {
                        name: "shm",
                        host: {
                            sourcePath: "/dev/shm",
                        },
                    },
                ],
                ulimits: [
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
                ],
            };

            // NVIDIA driver environment variables
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

            const batchJobDefinition = new batch.CfnJobDefinition(this, `BatchJobDef-${modelKey}`, {
                // No explicit name - let CDK auto-generate to allow CloudFormation replacements
                type: "container",
                containerProperties,
                retryStrategy: {
                    attempts: 1,
                },
                timeout: {
                    attemptDurationSeconds: 28800, // 8 hours
                },
            });

            /**
             * Step Functions State Machine for this model
             */
            const constructPipelineTask = new tasks.LambdaInvoke(
                this,
                `ConstructPipelineTask-${modelKey}`,
                {
                    lambdaFunction: constructPipelineFunction,
                    outputPath: "$.Payload",
                }
            );

            const successState = new sfn.Succeed(this, `SuccessState-${modelKey}`, {
                comment: `Cosmos 3 ${modelKey} pipeline returned SUCCESS`,
            });

            const failState = new sfn.Fail(this, `FailState-${modelKey}`, {
                causePath: sfn.JsonPath.stringAt("$.error.Cause"),
                errorPath: sfn.JsonPath.stringAt("$.error.Error"),
            });

            const endStatesChoice = new sfn.Choice(this, `EndStatesChoice-${modelKey}`)
                .when(sfn.Condition.isPresent("$.error"), failState)
                .otherwise(successState);

            const pipeLineEndTask = new tasks.LambdaInvoke(this, `PipelineEndTask-${modelKey}`, {
                lambdaFunction: pipelineEndFunction,
                inputPath: "$",
                outputPath: "$.Payload",
            }).next(endStatesChoice);

            const handleBatchError = new sfn.Pass(this, `HandleBatchError-${modelKey}`, {
                resultPath: "$",
            }).next(pipeLineEndTask);

            const batchJob = new tasks.BatchSubmitJob(this, `CosmosBatchJob-${modelKey}`, {
                jobName: sfn.JsonPath.stringAt("$.jobName"),
                jobDefinitionArn: batchJobDefinition.attrJobDefinitionArn,
                jobQueueArn: jobQueue.ref,
                containerOverrides: {
                    command: [...sfn.JsonPath.listAt("$.definition")],
                    environment: {
                        AWS_REGION: region,
                        // Input configuration + metadata are read by the container from S3 (their
                        // locations travel in the pipeline definition / command JSON); they are no
                        // longer injected as inline env vars.
                        S3_MODEL_BUCKET: modelCacheBucket.bucketName,
                    },
                },
                integrationPattern: sfn.IntegrationPattern.RUN_JOB,
                resultPath: "$.batchResult",
            })
                .addCatch(handleBatchError, {
                    resultPath: "$.error",
                })
                .next(pipeLineEndTask);

            const sfnDefinition = sfn.Chain.start(constructPipelineTask.next(batchJob));

            const stateMachineLogGroup = new logs.LogGroup(this, `Cosmos3-${modelKey}-LogGroup`, {
                logGroupName:
                    `/aws/vendedlogs/VAMSstateMachine-Cosmos3-${modelKey}` +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        `Cosmos3-${modelKey}-StateMachineLogGroup`,
                        10
                    ),
                retention: logs.RetentionDays.TEN_YEARS,
                removalPolicy: RemovalPolicy.DESTROY,
            });

            const pipelineStateMachine = new sfn.StateMachine(
                this,
                `Cosmos3-${modelKey}-StateMachine`,
                {
                    definitionBody: sfn.DefinitionBody.fromChainable(sfnDefinition),
                    // Match the Batch attempt (8h) and outer task-token timeouts so
                    // pipelineEnd always runs and closes the token, even on a first-run
                    // Super download (~133 GB) followed by multi-GPU inference.
                    timeout: Duration.hours(8),
                    logs: {
                        destination: stateMachineLogGroup,
                        includeExecutionData: true,
                        level: sfn.LogLevel.ALL,
                    },
                    tracingEnabled: true,
                }
            );

            /**
             * Lambda: openPipeline (model-specific, bound to model's state machine)
             */
            const allowedInputFileExtensions = ".mp4,.mov,.jpg,.jpeg,.png,.webp";
            const openPipelineFunction = buildOpenPipelineFunction(
                this,
                props.lambdaCommonBaseLayer,
                props.storageResources.s3.assetAuxiliaryBucket,
                pipelineStateMachine,
                allowedInputFileExtensions,
                props.config,
                props.vpc,
                props.pipelineSubnets,
                props.storageResources.eventBridge.orchestrationBus,
                stateMachineLogGroup,
                props.storageResources.encryption.kmsKey,
                modelKey // Use modelKey (unique per model, e.g., "nano16B") not variant
            );

            /**
             * Lambda: vamsExecute (model-specific)
             */
            const vamsExecuteFunction = buildVamsExecuteCosmos3PipelineFunction(
                this,
                props.lambdaCommonBaseLayer,
                openPipelineFunction,
                props.config,
                props.vpc,
                props.pipelineSubnets,
                props.storageResources.encryption.kmsKey,
                modelKey
            );

            /**
             * Auto-Registration with VAMS (V2 vamsSchema bundle -> V2 pipeline/workflow/template
             * tables). Each model variant has its own per-model bundle under vamsSchema/<variant>.
             */
            if (isAutoRegister) {
                new VamsSchemaRegistration(this, `Cosmos3-${modelKey}-Registration`, {
                    importFunctionName: props.importGlobalPipelineWorkflowV2FunctionName,
                    artefactsBucket: props.storageResources.s3.artefactsBucket,
                    vamsSchemaDir: path.join(
                        __dirname,
                        "..",
                        "..",
                        "..",
                        "..",
                        "..",
                        "..",
                        "..",
                        "..",
                        "backendPipelines",
                        "genAi",
                        "nvidia",
                        "cosmos",
                        "3",
                        "vamsSchema",
                        variant
                    ),
                    resourceOverrides: {
                        lambdaName: vamsExecuteFunction.functionName,
                    },
                    idOverrides: {
                        pipelineId: pipelineId,
                        workflowId: pipelineId,
                    },
                    triggerEnabled: autoTriggerExtensions !== "",
                });
            }

            return {
                vamsExecuteFunction,
                pipelineStateMachine,
            };
        };

        /**
         * Per-Model Resources: Nano 16B (single-GPU tier)
         */
        if (cosmosConfig.modelsOmni.nano16B?.enabled) {
            const nano = createModelResources(
                "nano16B",
                "nano",
                "text2video",
                "nvidia-cosmos3-nano",
                "NVIDIA Cosmos 3 Nano (16B) omni world model",
                cosmosConfig.modelsOmni.nano16B.autoRegisterWithVAMS === true,
                cosmosConfig.modelsOmni.nano16B.autoTriggerOnFileExtensionsUpload || "",
                ".mp4",
                containerImageName!,
                batchEnvironment,
                batchJobQueue,
                1,
                110000,
                16
            );

            this.pipelineCosmos3Nano16BVamsLambdaFunctionName =
                nano.vamsExecuteFunction.functionName;

            new CfnOutput(this, "Cosmos3Nano16BLambdaFunctionName", {
                value: nano.vamsExecuteFunction.functionName,
                description:
                    "The Cosmos 3 Nano 16B Pipeline Lambda Function Name to use in a VAMS Pipeline",
            });
        }

        /**
         * Per-Model Resources: Super 64B (multi-GPU tier)
         */
        if (cosmosConfig.modelsOmni.super64B?.enabled) {
            const sup = createModelResources(
                "super64B",
                "super",
                "text2video",
                "nvidia-cosmos3-super",
                "NVIDIA Cosmos 3 Super (64B) omni world model",
                cosmosConfig.modelsOmni.super64B.autoRegisterWithVAMS === true,
                cosmosConfig.modelsOmni.super64B.autoTriggerOnFileExtensionsUpload || "",
                ".mp4",
                containerImageName!,
                batchEnvironmentSuper!,
                batchJobQueueSuper!,
                8,
                480000,
                96
            );

            this.pipelineCosmos3Super64BVamsLambdaFunctionName =
                sup.vamsExecuteFunction.functionName;

            new CfnOutput(this, "Cosmos3Super64BLambdaFunctionName", {
                value: sup.vamsExecuteFunction.functionName,
                description:
                    "The Cosmos 3 Super 64B Pipeline Lambda Function Name to use in a VAMS Pipeline",
            });
        }

        /**
         * Per-Model Resources: Super Text2Image 64B (multi-GPU tier)
         */
        if (cosmosConfig.modelsOmni.superText2Image64B?.enabled) {
            const t2i = createModelResources(
                "superText2Image64B",
                "super-text2image",
                "text2image",
                "nvidia-cosmos3-super-text2image",
                "NVIDIA Cosmos 3 Super Text2Image (64B)",
                cosmosConfig.modelsOmni.superText2Image64B.autoRegisterWithVAMS === true,
                "",
                ".png",
                containerImageName!,
                batchEnvironmentSuper!,
                batchJobQueueSuper!,
                8,
                480000,
                96
            );

            this.pipelineCosmos3SuperText2Image64BVamsLambdaFunctionName =
                t2i.vamsExecuteFunction.functionName;

            new CfnOutput(this, "Cosmos3SuperText2Image64BLambdaFunctionName", {
                value: t2i.vamsExecuteFunction.functionName,
                description:
                    "The Cosmos 3 Super Text2Image 64B Pipeline Lambda Function Name to use in a VAMS Pipeline",
            });
        }

        /**
         * Per-Model Resources: Super Image2Video 64B (multi-GPU tier)
         */
        if (cosmosConfig.modelsOmni.superImage2Video64B?.enabled) {
            const i2v = createModelResources(
                "superImage2Video64B",
                "super-image2video",
                "image2video",
                "nvidia-cosmos3-super-image2video",
                "NVIDIA Cosmos 3 Super Image2Video (64B)",
                cosmosConfig.modelsOmni.superImage2Video64B.autoRegisterWithVAMS === true,
                cosmosConfig.modelsOmni.superImage2Video64B.autoTriggerOnFileExtensionsUpload || "",
                ".mp4",
                containerImageName!,
                batchEnvironmentSuper!,
                batchJobQueueSuper!,
                8,
                480000,
                96
            );

            this.pipelineCosmos3SuperImage2Video64BVamsLambdaFunctionName =
                i2v.vamsExecuteFunction.functionName;

            new CfnOutput(this, "Cosmos3SuperImage2Video64BLambdaFunctionName", {
                value: i2v.vamsExecuteFunction.functionName,
                description:
                    "The Cosmos 3 Super Image2Video 64B Pipeline Lambda Function Name to use in a VAMS Pipeline",
            });
        }

        /**
         * CDK Nag Suppressions
         */
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-SQS3",
                    reason: "Intended not to use DLQs for these types of SQS events. Re-drives should come from re-executing workflows.",
                },
            ],
            true
        );

        const reason =
            "Intended Solution. The Cosmos Predict pipeline lambda functions need appropriate access to S3 for reading asset files and model data.";

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            regex: "^Resource::.*openPipeline/ServiceRole/.*/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            regex: "^Resource::.*Cosmos3.*StateMachine/Role/.*/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            regex: "^Resource::.*pipelineEnd/ServiceRole/.*/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            regex: "^Resource::.*vamsExecuteCosmos.*Pipeline/ServiceRole/.*/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            containerExecutionRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The IAM role for ECS Container execution uses AWS Managed Policies for ECS task execution and X-Ray tracing",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require access to objects in asset buckets and model cache bucket for Cosmos inference",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            containerJobRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The IAM role for ECS Container execution uses AWS Managed Policies for ECS task execution and X-Ray tracing",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require access to objects in asset buckets, model cache, and EFS for Cosmos model weights",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            batchServiceRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The IAM role for AWS Batch Service uses AWSBatchServiceRole managed policy which is required for batch operations",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            instanceRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The ECS Instance Role for EC2 Batch Compute Environment requires AmazonEC2ContainerServiceforEC2Role managed policy",
                },
            ],
            true
        );

        // State machine role suppressions for each enabled model
        const modelKeys = [];
        if (cosmosConfig.modelsOmni.nano16B?.enabled) modelKeys.push("nano16B");
        if (cosmosConfig.modelsOmni.super64B?.enabled) modelKeys.push("super64B");
        if (cosmosConfig.modelsOmni.superText2Image64B?.enabled)
            modelKeys.push("superText2Image64B");
        if (cosmosConfig.modelsOmni.superImage2Video64B?.enabled)
            modelKeys.push("superImage2Video64B");

        for (const modelKey of modelKeys) {
            NagSuppressions.addResourceSuppressionsByPath(
                Stack.of(this),
                `/${this.toString()}/Cosmos3-${modelKey}-StateMachine/Role/DefaultPolicy/Resource`,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "Cosmos Predict pipeline state machine uses default policy that contains wildcards for batch job submission and lambda invocation",
                        appliesTo: [
                            "Resource::*",
                            "Action::kms:GenerateDataKey*",
                            `Resource::arn:<AWS::Partition>:batch:${region}:${account}:job-definition/*`,
                            {
                                regex: "/^Resource::<.*Function.*.Arn>:.*$/g",
                            },
                            {
                                regex: "/^Action::s3:.*$/g",
                            },
                        ],
                    },
                ],
                true
            );
        }

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/constructPipeline/ServiceRole/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "constructPipeline requires AWS Managed Policies, AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "constructPipeline uses default policy that contains wildcard",
                    appliesTo: ["Resource::*"],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/pipelineEnd/ServiceRole/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "pipelineEnd requires AWS Managed Policies, AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "pipelineEnd uses default policy that contains wildcard",
                    appliesTo: ["Resource::*"],
                },
            ],
            true
        );
    }
}
