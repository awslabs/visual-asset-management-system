/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as batch from "aws-cdk-lib/aws-batch";
import * as efs from "aws-cdk-lib/aws-efs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cr from "aws-cdk-lib/custom-resources";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { DockerImageAsset, Platform } from "aws-cdk-lib/aws-ecr-assets";
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import { IsaacLabTrainingFunctions } from "../lambdaBuilder/isaacLabTrainingFunctions";
import * as Config from "../../../../../../config/config";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { NagSuppressions } from "cdk-nag";
import * as path from "path";

export interface IsaacLabTrainingConstructProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[]; // Private subnets for compute (with NAT Gateway for internet access)
    pipelineSubnetsIsolated: ec2.ISubnet[]; // Isolated subnets for EFS (no internet needed)
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowFunctionName: string;
}

export class IsaacLabTrainingConstruct extends Construct {
    public pipelineVamsLambdaFunctionName: string;

    constructor(scope: Construct, id: string, props: IsaacLabTrainingConstructProps) {
        super(scope, id);

        const account = cdk.Stack.of(this).account;
        const region = cdk.Stack.of(this).region;

        // Note: ECR pull-through cache is NOT supported for NVIDIA NGC (nvcr.io)
        // Supported upstream registries: ECR Public, Kubernetes, Quay, Docker Hub, Azure, GitHub, GitLab
        // For faster Batch job startup, consider:
        // 1. Pre-baking the image into a custom AMI
        // 2. Keeping warm instances (minvCpus > 0)
        // 3. Using larger EBS volumes with Docker layer caching

        // Build and push container to ECR using CDK DockerImageAsset
        // ACCEPT_EULA must be set to true in config.json to accept the NVIDIA Software License Agreement
        // See: https://docs.nvidia.com/ngc/gpu-cloud/ngc-catalog-user-guide/index.html#ngc-software-license
        const containerImage = new DockerImageAsset(this, "IsaacLabTrainingImage", {
            directory: path.join(
                __dirname,
                "../../../../../../../backendPipelines/simulation/isaacLabTraining/container"
            ),
            platform: Platform.LINUX_AMD64,
            buildArgs: {
                ACCEPT_EULA: props.config.app.pipelines.useIsaacLabTraining.acceptNvidiaEula
                    ? "Y"
                    : "N",
            },
        });

        // EFS for training checkpoints - use isolated subnets (no internet access needed for EFS)
        const trainingEfs = new efs.FileSystem(this, "TrainingEfs", {
            vpc: props.vpc,
            vpcSubnets:
                props.pipelineSubnetsIsolated.length > 0
                    ? { subnets: props.pipelineSubnetsIsolated }
                    : undefined,
            securityGroup: props.pipelineSecurityGroups[0],
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            performanceMode: efs.PerformanceMode.GENERAL_PURPOSE,
            throughputMode: efs.ThroughputMode.BURSTING,
        });

        // Allow NFS traffic from the security group to itself for EFS access
        props.pipelineSecurityGroups[0].addIngressRule(
            props.pipelineSecurityGroups[0],
            ec2.Port.tcp(2049),
            "Allow NFS for EFS access"
        );

        // Launch template with larger EBS volume for Isaac Lab container (10GB+)
        const launchTemplate = new ec2.LaunchTemplate(this, "IsaacLabLaunchTemplate", {
            blockDevices: [
                {
                    deviceName: "/dev/xvda",
                    volume: ec2.BlockDeviceVolume.ebs(100, {
                        volumeType: ec2.EbsDeviceVolumeType.GP3,
                        encrypted: true,
                    }),
                },
            ],
        });

        // Batch compute environment for GPU instances
        // Uses private subnets with NAT Gateway for internet access to download Omniverse assets
        const computeEnvironment = new batch.ManagedEc2EcsComputeEnvironment(
            this,
            "GpuComputeEnv",
            {
                vpc: props.vpc,
                vpcSubnets:
                    props.pipelineSubnets.length > 0
                        ? { subnets: props.pipelineSubnets }
                        : undefined,
                securityGroups: props.pipelineSecurityGroups,
                instanceTypes: [
                    // Priority 1: G6 instances (L4 GPU - best price/performance for Isaac Lab)
                    ec2.InstanceType.of(ec2.InstanceClass.G6, ec2.InstanceSize.XLARGE2),
                    ec2.InstanceType.of(ec2.InstanceClass.G6, ec2.InstanceSize.XLARGE4),
                    ec2.InstanceType.of(ec2.InstanceClass.G6, ec2.InstanceSize.XLARGE12),
                    // Priority 2: G6E instances (L40S GPU - higher performance)
                    ec2.InstanceType.of(ec2.InstanceClass.G6E, ec2.InstanceSize.XLARGE2),
                    ec2.InstanceType.of(ec2.InstanceClass.G6E, ec2.InstanceSize.XLARGE12),
                    // Fallback: G5 instances (A10G GPU)
                    ec2.InstanceType.of(ec2.InstanceClass.G5, ec2.InstanceSize.XLARGE2),
                    ec2.InstanceType.of(ec2.InstanceClass.G5, ec2.InstanceSize.XLARGE4),
                ],
                maxvCpus: 256,
                // Keep 1 warm instance (8 vCPUs for g6.2xlarge) when enabled to avoid cold start delays
                minvCpus: props.config.app.pipelines.useIsaacLabTraining.keepWarmInstance ? 8 : 0,
                allocationStrategy: batch.AllocationStrategy.BEST_FIT_PROGRESSIVE,
                launchTemplate: launchTemplate,
            }
        );

        // Enable Container Insights on the ECS cluster created by Batch
        // First, we need to get the ECS cluster ARN from the Batch compute environment
        const getEcsClusterArn = new cr.AwsCustomResource(this, "GetEcsClusterArn", {
            onCreate: {
                service: "Batch",
                action: "describeComputeEnvironments",
                parameters: {
                    computeEnvironments: [computeEnvironment.computeEnvironmentName],
                },
                physicalResourceId: cr.PhysicalResourceId.of("EcsClusterArn"),
            },
            onUpdate: {
                service: "Batch",
                action: "describeComputeEnvironments",
                parameters: {
                    computeEnvironments: [computeEnvironment.computeEnvironmentName],
                },
                physicalResourceId: cr.PhysicalResourceId.of("EcsClusterArn"),
            },
            policy: cr.AwsCustomResourcePolicy.fromStatements([
                new iam.PolicyStatement({
                    actions: ["batch:DescribeComputeEnvironments"],
                    // DescribeComputeEnvironments does not support resource-level permissions
                    resources: ["*"],
                }),
            ]),
        });
        getEcsClusterArn.node.addDependency(computeEnvironment);

        const ecsClusterArn = getEcsClusterArn.getResponseField(
            "computeEnvironments.0.ecsClusterArn"
        );

        // Now enable Container Insights on the ECS cluster
        const enableContainerInsights = new cr.AwsCustomResource(this, "EnableContainerInsights", {
            onCreate: {
                service: "ECS",
                action: "updateClusterSettings",
                parameters: {
                    cluster: ecsClusterArn,
                    settings: [
                        {
                            name: "containerInsights",
                            value: "enabled",
                        },
                    ],
                },
                physicalResourceId: cr.PhysicalResourceId.of("ContainerInsights"),
            },
            onUpdate: {
                service: "ECS",
                action: "updateClusterSettings",
                parameters: {
                    cluster: ecsClusterArn,
                    settings: [
                        {
                            name: "containerInsights",
                            value: "enabled",
                        },
                    ],
                },
                physicalResourceId: cr.PhysicalResourceId.of("ContainerInsights"),
            },
            policy: cr.AwsCustomResourcePolicy.fromStatements([
                new iam.PolicyStatement({
                    actions: ["ecs:UpdateClusterSettings"],
                    resources: [
                        `arn:${ServiceHelper.Partition()}:ecs:${region}:${account}:cluster/*`,
                    ],
                }),
            ]),
        });
        enableContainerInsights.node.addDependency(getEcsClusterArn);

        // Batch job queue
        const jobQueue = new batch.JobQueue(this, "IsaacLabJobQueue", {
            computeEnvironments: [
                {
                    computeEnvironment: computeEnvironment,
                    order: 1,
                },
            ],
        });

        // IAM role for Batch job
        const jobRole = new iam.Role(this, "BatchJobRole", {
            assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        });

        // Grant VAMS asset bucket read/write access for inputs and outputs
        s3AssetBuckets.getS3AssetBucketRecords().forEach((record) => {
            record.bucket.grantReadWrite(jobRole);
        });

        // Grant VAMS auxiliary bucket read/write access (for intermediate storage if needed)
        props.storageResources.s3.assetAuxiliaryBucket.grantReadWrite(jobRole);

        // Grant Step Functions callback permissions for async task completion
        jobRole.addToPolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: [
                    "states:SendTaskSuccess",
                    "states:SendTaskFailure",
                    "states:SendTaskHeartbeat",
                ],
                resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
            })
        );

        // Batch job definition using CDK-managed container image
        const jobDefinition = new batch.EcsJobDefinition(this, "IsaacLabJobDef", {
            container: new batch.EcsEc2ContainerDefinition(this, "Container", {
                image: ecs.ContainerImage.fromDockerImageAsset(containerImage),
                cpu: 8,
                memory: cdk.Size.gibibytes(32),
                gpu: 1,
                jobRole: jobRole,
                environment: {
                    AWS_REGION: region,
                    AWS_DEFAULT_REGION: region,
                },
                volumes: [
                    batch.EcsVolume.efs({
                        name: "training-efs",
                        fileSystem: trainingEfs,
                        containerPath: "/mnt/efs",
                    }),
                ],
            }),
            timeout: cdk.Duration.hours(6),
        });

        // Lambda functions
        const lambdaFunctions = new IsaacLabTrainingFunctions(this, "LambdaFunctions", {
            config: props.config,
            vpc: props.vpc,
            pipelineSubnets: props.pipelineSubnets,
            pipelineSecurityGroups: props.pipelineSecurityGroups,
            storageResources: props.storageResources,
            lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
            batchJobQueue: jobQueue,
            batchJobDefinition: jobDefinition,
        });

        // Step Functions state machine
        const openPipelineState = new tasks.LambdaInvoke(this, "OpenPipelineState", {
            lambdaFunction: lambdaFunctions.openPipelineFunction,
            resultPath: "$.openResult",
        });

        // Pass state to merge openResult into main state for downstream access
        const prepareExecutionState = new sfn.Pass(this, "PrepareExecutionState", {
            parameters: {
                "jobName.$": "$.openResult.Payload.jobName",
                "definition.$": "$.openResult.Payload.definition",
                "numNodes.$": "$.openResult.Payload.numNodes",
                "inputMetadata.$": "$.openResult.Payload.inputMetadata",
                "inputParameters.$": "$.openResult.Payload.inputParameters",
                "externalSfnTaskToken.$": "$.openResult.Payload.externalSfnTaskToken",
                "outputS3AssetFilesPath.$": "$.openResult.Payload.outputS3AssetFilesPath",
                "inputS3AssetFilePath.$": "$.openResult.Payload.inputS3AssetFilePath",
            },
        });

        // Execute task uses waitForTaskToken - container calls back when Batch job completes
        // resultPath preserves original input and adds batchResult
        const executeBatchJobState = new tasks.LambdaInvoke(this, "ExecuteBatchJobState", {
            lambdaFunction: lambdaFunctions.executeBatchJobFunction,
            integrationPattern: sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            payload: sfn.TaskInput.fromObject({
                taskToken: sfn.JsonPath.taskToken,
                "jobName.$": "$.jobName",
                "definition.$": "$.definition",
                "numNodes.$": "$.numNodes",
                "inputMetadata.$": "$.inputMetadata",
                "inputParameters.$": "$.inputParameters",
                "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                "outputS3AssetFilesPath.$": "$.outputS3AssetFilesPath",
                "inputS3AssetFilePath.$": "$.inputS3AssetFilePath",
            }),
            resultPath: "$.batchResult",
            taskTimeout: sfn.Timeout.duration(cdk.Duration.hours(8)),
            heartbeatTimeout: sfn.Timeout.duration(cdk.Duration.minutes(30)),
        });

        const closePipelineState = new tasks.LambdaInvoke(this, "ClosePipelineState", {
            lambdaFunction: lambdaFunctions.closePipelineFunction,
            outputPath: "$.Payload",
        });

        // Error handler state - notifies external SFN of failure
        // When catch triggers, error info goes to $.errorInfo, original state preserved
        const handleErrorState = new tasks.LambdaInvoke(this, "HandleErrorState", {
            lambdaFunction: lambdaFunctions.handleErrorFunction,
            payload: sfn.TaskInput.fromObject({
                "error.$": "$.errorInfo",
                "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                "jobName.$": "$.jobName",
                "outputS3AssetFilesPath.$": "$.outputS3AssetFilesPath",
            }),
            resultPath: "$.errorHandlerResult",
        });

        // Fail state after error handling
        const failState = new sfn.Fail(this, "PipelineFailed", {
            error: "PipelineExecutionFailed",
            cause: "Batch job failed or timed out",
        });

        // Chain error handler to fail state
        handleErrorState.next(failState);

        // Add catch to executeBatchJobState - resultPath preserves original input
        executeBatchJobState.addCatch(handleErrorState, {
            errors: ["States.ALL"],
            resultPath: "$.errorInfo",
        });

        const definition = openPipelineState
            .next(prepareExecutionState)
            .next(executeBatchJobState)
            .next(closePipelineState);

        const stateMachine = new sfn.StateMachine(this, "IsaacLabStateMachine", {
            stateMachineName: "isaaclab-pipeline-internal",
            definitionBody: sfn.DefinitionBody.fromChainable(definition),
            timeout: cdk.Duration.hours(8),
        });

        // Grant vamsExecuteFunction permission to start the SFN
        stateMachine.grantStartExecution(lambdaFunctions.vamsExecuteFunction);

        // Add STATE_MACHINE_ARN to vamsExecuteFunction (must be done after state machine creation)
        lambdaFunctions.vamsExecuteFunction.addEnvironment(
            "STATE_MACHINE_ARN",
            stateMachine.stateMachineArn
        );

        // Set output
        this.pipelineVamsLambdaFunctionName = lambdaFunctions.vamsExecuteFunction.functionName;

        // Register pipeline with VAMS if autoRegisterWithVAMS is enabled
        if (props.config.app.pipelines.useIsaacLabTraining?.autoRegisterWithVAMS === true) {
            const region = cdk.Stack.of(this).region;
            const account = cdk.Stack.of(this).account;

            const importFunction = lambda.Function.fromFunctionArn(
                this,
                "ImportFunction",
                `arn:${ServiceHelper.Partition()}:lambda:${region}:${account}:function:${
                    props.importGlobalPipelineWorkflowFunctionName
                }`
            );

            const importProvider = new cr.Provider(this, "ImportProvider", {
                onEventHandler: importFunction,
            });

            const currentTimestamp = new Date().toISOString();

            // Register Isaac Lab Training pipeline
            new cdk.CustomResource(this, "IsaacLabTrainingPipelineWorkflow", {
                serviceToken: importProvider.serviceToken,
                properties: {
                    timestamp: currentTimestamp,
                    pipelineId: "isaaclab-training",
                    pipelineDescription:
                        "Isaac Lab RL Training Pipeline - Train reinforcement learning policies using NVIDIA Isaac Lab on AWS Batch with GPU acceleration.",
                    pipelineType: "standardFile",
                    pipelineExecutionType: "Lambda",
                    assetType: ".all",
                    outputType: ".pt",
                    waitForCallback: "Enabled",
                    lambdaName: lambdaFunctions.vamsExecuteFunction.functionName,
                    taskTimeout: "28800",
                    taskHeartbeatTimeout: "3600",
                    inputParameters: JSON.stringify({
                        trainingConfig: {
                            mode: "train",
                            task: "Isaac-Cartpole-Direct-v0",
                            numEnvs: 4096,
                            maxIterations: 1500,
                            rlLibrary: "rsl_rl",
                        },
                        computeConfig: {
                            numNodes: 1,
                        },
                    }),
                    workflowId: "isaaclab-training",
                    workflowDescription:
                        "Automated workflow for RL policy training using Isaac Lab simulation on GPU instances.",
                },
            });

            // Register Isaac Lab Evaluation pipeline
            new cdk.CustomResource(this, "IsaacLabEvaluationPipelineWorkflow", {
                serviceToken: importProvider.serviceToken,
                properties: {
                    timestamp: currentTimestamp,
                    pipelineId: "isaaclab-evaluation",
                    pipelineDescription:
                        "Isaac Lab RL Evaluation Pipeline - Evaluate trained RL policies using NVIDIA Isaac Lab simulation.",
                    pipelineType: "standardFile",
                    pipelineExecutionType: "Lambda",
                    assetType: ".pt",
                    outputType: ".json",
                    waitForCallback: "Enabled",
                    lambdaName: lambdaFunctions.vamsExecuteFunction.functionName,
                    taskTimeout: "7200",
                    taskHeartbeatTimeout: "1800",
                    inputParameters: JSON.stringify({
                        trainingConfig: {
                            mode: "evaluate",
                            task: "Isaac-Cartpole-Direct-v0",
                            numEnvs: 100,
                            numEpisodes: 50,
                            recordVideo: false,
                        },
                    }),
                    workflowId: "isaaclab-evaluation",
                    workflowDescription:
                        "Automated workflow for evaluating trained RL policies using Isaac Lab simulation.",
                },
            });

            // Nag suppression for import provider
            NagSuppressions.addResourceSuppressions(
                importProvider,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "Wildcard permissions needed for pipeline/workflow import custom resource",
                    },
                ],
                true
            );
        }

        // CDK-nag suppressions for IsaacLab pipeline
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Wildcard permissions required: batch:DescribeComputeEnvironments does not support resource-level permissions, S3 bucket access needs object-level wildcards, and Batch job operations require dynamic resource access",
                },
                {
                    id: "AwsSolutions-IAM4",
                    reason: "AWS managed policy required for ECS/EC2 integration with Batch",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
                    ],
                },
                {
                    id: "AwsSolutions-SF1",
                    reason: "CloudWatch logging will be added in future iteration",
                },
                {
                    id: "AwsSolutions-SF2",
                    reason: "X-Ray tracing will be added in future iteration",
                },
            ],
            true
        );
    }
}
