/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as batch from "aws-cdk-lib/aws-batch";
import * as efs from "aws-cdk-lib/aws-efs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as cr from "aws-cdk-lib/custom-resources";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { DockerImageAsset, Platform } from "aws-cdk-lib/aws-ecr-assets";
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import { IsaacLabTrainingFunctions } from "../lambdaBuilder/isaacLabTrainingFunctions";
import * as Config from "../../../../../../config/config";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import {
    generateUniqueNameHash,
    grantExternalAssetBucketKmsKeys,
} from "../../../../../helper/security";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";
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
    importGlobalPipelineWorkflowV2FunctionName: string;
    // Optional: CodeBuild-built image in ECR (bypasses local Docker build). Passing the
    // repository (rather than a URI string) lets the Batch container definition auto-grant the
    // execution role ECR pull + ecr:GetAuthorizationToken permissions.
    codeBuildRepository?: ecr.IRepository;
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

        // Container image resolution.
        // If a CodeBuild-built ECR repository is provided, use that directly,
        // which avoids slow local Docker builds of the large Isaac Lab GPU image.
        // Otherwise, fall back to building and pushing the container to ECR using CDK DockerImageAsset.
        // ACCEPT_EULA must be set to true in config.json to accept the NVIDIA Software License Agreement
        // See: https://docs.nvidia.com/ngc/gpu-cloud/ngc-catalog-user-guide/index.html#ngc-software-license
        let containerImageRef: ecs.ContainerImage;
        if (props.codeBuildRepository) {
            // Use CodeBuild-built image from ECR. fromEcrRepository grants the Batch execution
            // role ECR pull + ecr:GetAuthorizationToken permissions (fromRegistry does not).
            containerImageRef = ecs.ContainerImage.fromEcrRepository(
                props.codeBuildRepository,
                "latest"
            );
        } else {
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
            containerImageRef = ecs.ContainerImage.fromDockerImageAsset(containerImage);
        }

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
                // NVIDIA-accelerated AMI for the GPU instance families above
                images: [
                    {
                        imageType: batch.EcsMachineImageType.ECS_AL2023_NVIDIA,
                    },
                ],
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

        // Grant access to any external asset bucket customer managed KMS keys so the
        // container can read/write objects in cross-account encrypted buckets
        // (no-op when no external keys are configured)
        grantExternalAssetBucketKmsKeys(jobRole);

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
                image: containerImageRef,
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
                "inputMetadataS3Location.$": "$.openResult.Payload.inputMetadataS3Location",
                "inputConfigurationS3Location.$":
                    "$.openResult.Payload.inputConfigurationS3Location",
                "externalSfnTaskToken.$": "$.openResult.Payload.externalSfnTaskToken",
                "outputS3AssetFilesPath.$": "$.openResult.Payload.outputS3AssetFilesPath",
                "inputS3AssetFilePath.$": "$.openResult.Payload.inputS3AssetFilePath",
                // Read from the ORIGINAL state machine input, not from openResult: the open lambda
                // does not echo this field. These parameters REPLACE the state, so a field omitted
                // here is unreachable downstream — and the batch task references it by path, which
                // makes an omission a States.Runtime failure rather than a skipped registration.
                "orchestrationEventPrefix.$": "$.orchestrationEventPrefix",
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
                "inputMetadataS3Location.$": "$.inputMetadataS3Location",
                "inputConfigurationS3Location.$": "$.inputConfigurationS3Location",
                "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                "outputS3AssetFilesPath.$": "$.outputS3AssetFilesPath",
                "inputS3AssetFilePath.$": "$.inputS3AssetFilePath",
                // Needed so the lambda can register the submitted Batch job as an abortable
                // sub-process: this pipeline submits under WAIT_FOR_TASK_TOKEN rather than the
                // Batch .sync integration, so Step Functions does not stop the job on abort.
                "orchestrationEventPrefix.$": "$.orchestrationEventPrefix",
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

        const stateMachineLogGroup = new logs.LogGroup(this, "IsaacLab-StateMachineLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSstateMachine-IsaacLabTrainingPipeline" +
                generateUniqueNameHash(
                    props.config.env.coreStackName,
                    props.config.env.account,
                    "IsaacLab-StateMachineLogGroup",
                    10
                ),
            retention: logs.RetentionDays.TEN_YEARS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        const stateMachine = new sfn.StateMachine(this, "IsaacLabStateMachine", {
            definitionBody: sfn.DefinitionBody.fromChainable(definition),
            timeout: cdk.Duration.hours(8),
            logs: {
                destination: stateMachineLogGroup,
                includeExecutionData: true,
                level: sfn.LogLevel.ALL,
            },
            tracingEnabled: true,
        });

        // Grant vamsExecuteFunction permission to start the SFN
        stateMachine.grantStartExecution(lambdaFunctions.vamsExecuteFunction);

        // Add STATE_MACHINE_ARN to vamsExecuteFunction (must be done after state machine creation)
        lambdaFunctions.vamsExecuteFunction.addEnvironment(
            "STATE_MACHINE_ARN",
            stateMachine.stateMachineArn
        );

        lambdaFunctions.vamsExecuteFunction.addEnvironment(
            "ORCHESTRATION_BUS_NAME",
            props.storageResources.eventBridge.orchestrationBus.eventBusName
        );
        props.storageResources.eventBridge.orchestrationBus.grantPutEventsTo(
            lambdaFunctions.vamsExecuteFunction
        );

        // Registered with the sub-execution so the execution log viewer can read this state
        // machine's logs
        lambdaFunctions.vamsExecuteFunction.addEnvironment(
            "STATE_MACHINE_LOG_GROUP_NAME",
            stateMachineLogGroup.logGroupName
        );
        lambdaFunctions.vamsExecuteFunction.addEnvironment(
            "STATE_MACHINE_LOG_GROUP_ARN",
            stateMachineLogGroup.logGroupArn
        );

        // Set output
        this.pipelineVamsLambdaFunctionName = lambdaFunctions.vamsExecuteFunction.functionName;

        // Register pipeline with VAMS if autoRegisterWithVAMS is enabled (V2 vamsSchema bundles ->
        // V2 pipeline/workflow/template tables). Training and Evaluation are distinct pipelines (a
        // train -> evaluate chain), each with its own bundle.
        if (props.config.app.pipelines.useIsaacLabTraining?.autoRegisterWithVAMS === true) {
            const isaacLabVamsSchemaRoot = path.join(
                __dirname,
                "..",
                "..",
                "..",
                "..",
                "..",
                "..",
                "..",
                "backendPipelines",
                "simulation",
                "isaacLabTraining",
                "vamsSchema"
            );

            new VamsSchemaRegistration(this, "IsaacLabTrainingRegistration", {
                importFunctionName: props.importGlobalPipelineWorkflowV2FunctionName,
                artefactsBucket: props.storageResources.s3.artefactsBucket,
                vamsSchemaDir: path.join(isaacLabVamsSchemaRoot, "training"),
                resourceOverrides: {
                    lambdaName: lambdaFunctions.vamsExecuteFunction.functionName,
                },
                idOverrides: {
                    pipelineId: "isaaclab-training",
                    workflowId: "isaaclab-training",
                },
            });

            new VamsSchemaRegistration(this, "IsaacLabEvaluationRegistration", {
                importFunctionName: props.importGlobalPipelineWorkflowV2FunctionName,
                artefactsBucket: props.storageResources.s3.artefactsBucket,
                vamsSchemaDir: path.join(isaacLabVamsSchemaRoot, "evaluation"),
                resourceOverrides: {
                    lambdaName: lambdaFunctions.vamsExecuteFunction.functionName,
                },
                idOverrides: {
                    pipelineId: "isaaclab-evaluation",
                    workflowId: "isaaclab-evaluation",
                },
            });
        }

        // CDK-nag suppressions for IsaacLab pipeline
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Wildcard permissions required: batch:DescribeComputeEnvironments does not support resource-level permissions, S3 bucket access needs object-level wildcards, Batch job operations require dynamic resource access, and the state machine's CloudWatch Logs delivery + X-Ray trace actions are not resource-scopable",
                },
                {
                    id: "AwsSolutions-IAM4",
                    reason: "AWS managed policy required for ECS/EC2 integration with Batch",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
                    ],
                },
            ],
            true
        );
    }
}
