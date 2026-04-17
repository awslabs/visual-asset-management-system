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
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as batch from "aws-cdk-lib/aws-batch";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Duration, Stack, RemovalPolicy } from "aws-cdk-lib";
import { Construct } from "constructs";
import {
    buildConstructTransferPipelineFunction,
    buildOpenTransferPipelineFunction,
    buildVamsExecuteCosmosTransferPipelineFunction,
    buildTransferPipelineEndFunction,
} from "../lambdaBuilder/cosmosTransferFunctions";
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
} from "../../../../../../helper/security";
import * as cr from "aws-cdk-lib/custom-resources";
import { DockerImageAsset, Platform } from "aws-cdk-lib/aws-ecr-assets";

export interface CosmosTransferConstructProps extends cdk.StackProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowFunctionName: string;
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
const defaultProps: Partial<CosmosTransferConstructProps> = {};

export class CosmosTransferConstruct extends Construct {
    public pipelineTransfer2BVamsLambdaFunctionName?: string;

    constructor(parent: Construct, name: string, props: CosmosTransferConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;
        const cosmosConfig = props.config.app.pipelines.useNvidiaCosmos;
        const transferConfig = cosmosConfig.modelsTransfer!;

        /**
         * HuggingFace Token stored in Secrets Manager
         * The token value comes from the CDK config and is stored as a secret
         * so Batch can inject it securely without exposing it in environment variables.
         */
        const hfTokenSecret = new secretsmanager.Secret(this, "CosmosTransferHfTokenSecret", {
            description: "HuggingFace API token for downloading NVIDIA Cosmos Transfer models",
            secretStringValue: cdk.SecretValue.unsafePlainText(cosmosConfig.huggingFaceToken),
        });

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
         * Docker Container Image from ECR (Transfer 2.5)
         * If codeBuildImageUri is provided, use that directly (CodeBuild-built image in ECR).
         * Otherwise, fall back to inline DockerImageAsset build.
         */
        let containerImage: DockerImageAsset | undefined;
        if (!props.codeBuildImageUri) {
            containerImage = new DockerImageAsset(this, "CosmosTransferContainerImage", {
                directory: path.join(
                    __dirname,
                    "../../../../../../../../backendPipelines/genAi/nvidia/cosmos/transfer/container"
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
                    const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";
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
                            `${record.bucket.bucketArn}${normalizedPrefix}*`,
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

        const containerExecutionRole = new iam.Role(this, "CosmosTransferContainerExecutionRole", {
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

        const containerJobRole = new iam.Role(this, "CosmosTransferContainerJobRole", {
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

        /**
         * Batch Compute Environment for Transfer 2B (p4d.24xlarge)
         * Transfer 2B requires 65.4GB VRAM -- needs p4d.24xlarge (8x A100)
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
                "Security group for Cosmos Transfer Batch compute environment with internet access",
            allowAllOutbound: true,
        });

        // Allow NFS traffic from Batch compute SG to EFS SG
        nfsSecurityGroup.addIngressRule(
            batchSecurityGroup,
            ec2.Port.tcp(2049),
            "Allow NFS from Cosmos Transfer Batch compute to EFS"
        );

        const transfer2BConfig = transferConfig.transfer2B;
        const instanceTypes = transfer2BConfig.instanceTypes;
        const maxVCpus = Math.max(transfer2BConfig.maxVCpus, 96);

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
                            volumeSize: 500,
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
                                value: `VAMS-Cosmos-Transfer-Batch`,
                            },
                        ],
                    },
                ],
            },
        });

        const batchEnvironment = new batch.CfnComputeEnvironment(
            this,
            "CosmosTransferOnDemandComputeEnv",
            {
                type: "MANAGED",
                state: "ENABLED",
                serviceRole: batchServiceRole.roleArn,
                computeResources: {
                    type: "EC2",
                    allocationStrategy: "BEST_FIT_PROGRESSIVE",
                    minvCpus: 0,
                    maxvCpus: maxVCpus * 2,
                    desiredvCpus: 0,
                    instanceTypes: instanceTypes,
                    ec2Configuration: [
                        {
                            imageType: "ECS_AL2_NVIDIA",
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
            }
        );

        const batchJobQueue = new batch.CfnJobQueue(this, "CosmosTransferBatchJobQueue", {
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
         * Container image reference for job definitions
         * If codeBuildImageUri is provided, use that directly.
         * Otherwise, resolve via DockerImageAsset + ECS temp task definition.
         */
        let containerImageName: string;
        if (props.codeBuildImageUri) {
            containerImageName = props.codeBuildImageUri;
        } else {
            const tempTaskDef = new ecs.TaskDefinition(this, "TempTaskDef", {
                compatibility: ecs.Compatibility.EC2,
            });
            const container = tempTaskDef.addContainer("Container", {
                image: ecs.ContainerImage.fromDockerImageAsset(containerImage!),
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
        const constructPipelineFunction = buildConstructTransferPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            props.storageResources.encryption.kmsKey
        );

        const pipelineEndFunction = buildTransferPipelineEndFunction(
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
         * Transfer 2B Model Resources
         */
        if (transfer2BConfig.enabled) {
            const modelKey = "transfer2B";
            const gpuCount = 8;
            const memoryMb = 300000;
            const vcpus = 96;

            // Batch Job Definition
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
                    { name: "MODEL_TYPE", value: "transfer" },
                    { name: "CONTROL_TYPE", value: "edge" },
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

            // NVIDIA driver and NCCL environment variables
            containerProperties.environment.push(
                {
                    name: "LD_LIBRARY_PATH",
                    value: "/usr/local/cuda/lib64:/usr/local/cuda/extras/CUPTI/lib64:/usr/local/nvidia/lib:/usr/local/nvidia/lib64",
                },
                {
                    name: "NVIDIA_DRIVER_CAPABILITIES",
                    value: "compute,utility",
                },
                {
                    name: "NCCL_DEBUG",
                    value: "WARN",
                },
                {
                    name: "NCCL_SOCKET_IFNAME",
                    value: "eth0",
                }
            );

            const batchJobDefinition = new batch.CfnJobDefinition(this, `BatchJobDef-${modelKey}`, {
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
             * Step Functions State Machine
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
                comment: `Cosmos Transfer ${modelKey} pipeline returned SUCCESS`,
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
                jobQueueArn: batchJobQueue.ref,
                containerOverrides: {
                    command: [...sfn.JsonPath.listAt("$.definition")],
                    environment: {
                        AWS_REGION: region,
                        INPUT_PARAMETERS: sfn.JsonPath.stringAt("$.inputParameters"),
                        INPUT_METADATA: sfn.JsonPath.stringAt("$.inputMetadata"),
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

            const stateMachineLogGroup = new logs.LogGroup(
                this,
                `CosmosTransfer-${modelKey}-LogGroup`,
                {
                    logGroupName:
                        `/aws/vendedlogs/VAMSstateMachine-CosmosTransfer-${modelKey}` +
                        generateUniqueNameHash(
                            props.config.env.coreStackName,
                            props.config.env.account,
                            `CosmosTransfer-${modelKey}-StateMachineLogGroup`,
                            10
                        ),
                    retention: logs.RetentionDays.TEN_YEARS,
                    removalPolicy: RemovalPolicy.DESTROY,
                }
            );

            const pipelineStateMachine = new sfn.StateMachine(
                this,
                `CosmosTransfer-${modelKey}-StateMachine`,
                {
                    definitionBody: sfn.DefinitionBody.fromChainable(sfnDefinition),
                    timeout: Duration.hours(5),
                    logs: {
                        destination: stateMachineLogGroup,
                        includeExecutionData: true,
                        level: sfn.LogLevel.ALL,
                    },
                    tracingEnabled: true,
                }
            );

            /**
             * Lambda: openPipeline
             */
            const allowedInputFileExtensions = ".mp4,.mov,.avi";
            const openPipelineFunction = buildOpenTransferPipelineFunction(
                this,
                props.lambdaCommonBaseLayer,
                props.storageResources.s3.assetAuxiliaryBucket,
                pipelineStateMachine,
                allowedInputFileExtensions,
                props.config,
                props.vpc,
                props.pipelineSubnets,
                props.storageResources.encryption.kmsKey,
                modelKey
            );

            /**
             * Lambda: vamsExecute
             */
            const vamsExecuteFunction = buildVamsExecuteCosmosTransferPipelineFunction(
                this,
                props.lambdaCommonBaseLayer,
                openPipelineFunction,
                props.config,
                props.vpc,
                props.pipelineSubnets,
                props.storageResources.encryption.kmsKey,
                modelKey
            );

            this.pipelineTransfer2BVamsLambdaFunctionName = vamsExecuteFunction.functionName;

            new CfnOutput(this, "CosmosTransfer2BLambdaFunctionName", {
                value: vamsExecuteFunction.functionName,
                description:
                    "The Cosmos Transfer 2B Pipeline Lambda Function Name to use in a VAMS Pipeline",
            });

            /**
             * Auto-Registration with VAMS
             */
            if (transfer2BConfig.autoRegisterWithVAMS) {
                const importFunction = lambda.Function.fromFunctionArn(
                    this,
                    `ImportFunction-${modelKey}`,
                    `arn:${ServiceHelper.Partition()}:lambda:${region}:${account}:function:${
                        props.importGlobalPipelineWorkflowFunctionName
                    }`
                );

                const importProvider = new cr.Provider(this, `ImportProvider-${modelKey}`, {
                    onEventHandler: importFunction,
                });

                NagSuppressions.addResourceSuppressionsByPath(
                    Stack.of(this),
                    `/${this.toString()}/ImportProvider-${modelKey}/framework-onEvent/ServiceRole/DefaultPolicy/Resource`,
                    [
                        {
                            id: "AwsSolutions-IAM5",
                            reason: "Custom resource provider requires wildcard permissions to invoke the import global pipeline workflow function with version qualifiers. Scope is limited to the single import function.",
                            appliesTo: [
                                `Resource::arn:${ServiceHelper.Partition()}:lambda:${region}:${account}:function:<importGlobalPipelineWorkflow15C3C6ED>:*`,
                            ],
                        },
                    ],
                    true
                );

                new cdk.CustomResource(this, `CosmosTransfer-${modelKey}-PipelineWorkflow`, {
                    serviceToken: importProvider.serviceToken,
                    properties: {
                        pipelineId: "nvidia-cosmos-transfer2-edge-2b",
                        pipelineDescription:
                            "NVIDIA Cosmos Transfer 2B - Style/content transfer with control signals (edge, depth, segmentation, blur)",
                        pipelineType: "standardFile",
                        pipelineExecutionType: "Lambda",
                        assetType: ".all",
                        outputType: ".mp4",
                        waitForCallback: "Enabled",
                        lambdaName: vamsExecuteFunction.functionName,
                        taskTimeout: "28800",
                        taskHeartbeatTimeout: "",
                        inputParameters: JSON.stringify({
                            MODEL_TYPE: "transfer",
                            CONTROL_TYPE: "edge",
                            DISABLE_GUARDRAILS: "true",
                        }),
                        workflowId: "nvidia-cosmos-transfer2-edge-2b",
                        workflowDescription:
                            "NVIDIA Cosmos Transfer 2B - Style/content transfer with control signals (edge, depth, segmentation, blur)",
                        autoTriggerOnFileExtensionsUpload:
                            transfer2BConfig.autoTriggerOnFileExtensionsUpload || "",
                    },
                });
            }
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
            "Intended Solution. The Cosmos Transfer pipeline lambda functions need appropriate access to S3 for reading asset files and model data.";

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
                            regex: "^Resource::.*CosmosTransfer.*StateMachine/Role/.*/g",
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

        // State machine role suppressions
        if (transfer2BConfig.enabled) {
            NagSuppressions.addResourceSuppressionsByPath(
                Stack.of(this),
                `/${this.toString()}/CosmosTransfer-transfer2B-StateMachine/Role/DefaultPolicy/Resource`,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "Cosmos Transfer pipeline state machine uses default policy that contains wildcards for batch job submission and lambda invocation",
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
