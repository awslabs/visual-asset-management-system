/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as eks from "aws-cdk-lib/aws-eks";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Duration } from "aws-cdk-lib";
import { NagSuppressions } from "cdk-nag";
import { CfnOutput } from "aws-cdk-lib";
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { Service } from "../../../../../helper/service-helper";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import * as Config from "../../../../../../config/config";
import * as cr from "aws-cdk-lib/custom-resources";
import {
    buildConsolidatedHandlerFunction,
    buildOpenPipelineEKSFunction,
    buildVamsExecuteRapidPipelineEKSFunction,
} from "../lambdaBuilder/rapidPipelineEKSFunctions";

export interface RapidPipelineEKSConstructProps extends cdk.StackProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc; // Required - uses existing VPC (configured with 2 AZs when EKS is enabled)
    pipelineSubnetsPrivate: ec2.ISubnet[]; // Required - private subnets from existing VPC
    pipelineSubnetsIsolated?: ec2.ISubnet[]; // DEPRECATED: Do not use isolated subnets for Lambda functions
    pipelineSecurityGroups: ec2.ISecurityGroup[]; // Required - security groups from existing VPC
    lambdaCommonBaseLayer: lambda.LayerVersion;
    kubectlLayer: lambda.ILayerVersion; // kubectl binary layer for EKS cluster (supports multiple runtimes)
    kubernetesLayer: lambda.ILayerVersion; // Kubernetes Python client layer for Lambda functions
    importGlobalPipelineWorkflowFunctionName: string; // Lambda function name for registering pipelines
}

/**
 * RapidPipeline EKS construct that implements the complete EKS-based
 * 3D asset processing pipeline with consolidated Lambda functions.
 */
export class RapidPipelineEKSConstruct extends Construct {
    public pipelineVamsLambdaFunctionName: string;
    public openPipelineLambdaFunctionName: string;

    constructor(scope: Construct, id: string, props: RapidPipelineEKSConstructProps) {
        super(scope, id);

        const region = cdk.Stack.of(this).region;
        const account = cdk.Stack.of(this).account;

        // Get asset bucket records for S3 permissions
        const assetBucketRecords = s3AssetBuckets.getS3AssetBucketRecords();

        // Use the existing VPC from props (VPC is configured with 2 AZs when EKS is enabled)
        const eksVpc = props.vpc!;
        const eksPrivateSubnets = props.pipelineSubnetsPrivate!;
        const eksSecurityGroups = props.pipelineSecurityGroups!;

        // Create unique stack identifier for multi-stack deployments
        const stackIdentifier = `${props.config.name}-${props.config.app.baseStackName}`;

        // Security group for the cluster
        const eksClusterSecurityGroup = new ec2.SecurityGroup(this, "EksClusterSecurityGroup", {
            vpc: eksVpc,
            description: "Security group for RapidPipeline EKS cluster",
            allowAllOutbound: true,
        });

        // 1. Create EKS cluster with updated configuration for better reliability
        const cluster = new eks.Cluster(this, "EksCluster", {
            version: eks.KubernetesVersion.V1_31,
            clusterName: `rapid-pipeline-eks-${stackIdentifier}`,
            vpc: eksVpc,
            vpcSubnets: [{ subnets: eksPrivateSubnets }], // Always use private subnets for EKS cluster
            defaultCapacity: 0, // No default node group
            endpointAccess: eks.EndpointAccess.PUBLIC, // PUBLIC only (not PUBLIC_AND_PRIVATE) forces Lambda to use NAT Gateway instead of VPC endpoints
            kubectlLayer: props.kubectlLayer, // Use our multi-runtime kubectl layer
            securityGroup: eksClusterSecurityGroup,
            // Observability configuration (configurable via config.json)
            clusterLogging: props.config.app.pipelines.useRapidPipeline.useEks.observability.enableControlPlaneLogs
                ? [
                      eks.ClusterLoggingTypes.API,
                      eks.ClusterLoggingTypes.AUDIT,
                      eks.ClusterLoggingTypes.AUTHENTICATOR,
                      eks.ClusterLoggingTypes.CONTROLLER_MANAGER,
                      eks.ClusterLoggingTypes.SCHEDULER,
                  ]
                : undefined,
        });

        // Enable CloudWatch Container Insights if configured
        if (props.config.app.pipelines.useRapidPipeline.useEks.observability.enableContainerInsights) {
            // Create namespace for CloudWatch
            const cloudwatchNamespace = cluster.addManifest("CloudWatchNamespace", {
                apiVersion: "v1",
                kind: "Namespace",
                metadata: {
                    name: "amazon-cloudwatch",
                    labels: {
                        name: "amazon-cloudwatch",
                    },
                },
            });

            // Create ServiceAccount with IRSA for CloudWatch agent
            const cloudwatchServiceAccount = new eks.ServiceAccount(this, "CloudWatchServiceAccount", {
                cluster: cluster,
                name: "cloudwatch-agent",
                namespace: "amazon-cloudwatch",
            });

            // Get the IAM role created by the ServiceAccount
            const cloudwatchAgentRole = cloudwatchServiceAccount.role;

            // Grant CloudWatch permissions to the agent role
            cloudwatchAgentRole.addToPrincipalPolicy(
                new iam.PolicyStatement({
                    actions: [
                        "cloudwatch:PutMetricData",
                        "ec2:DescribeVolumes",
                        "ec2:DescribeTags",
                        "logs:PutLogEvents",
                        "logs:DescribeLogStreams",
                        "logs:DescribeLogGroups",
                        "logs:CreateLogStream",
                        "logs:CreateLogGroup",
                    ],
                    resources: ["*"],
                })
            );

            // Add dependency on namespace
            cloudwatchServiceAccount.node.addDependency(cloudwatchNamespace);

            // Create ConfigMap for CloudWatch agent
            const cloudwatchConfigMap = cluster.addManifest("CloudWatchConfigMap", {
                apiVersion: "v1",
                kind: "ConfigMap",
                metadata: {
                    name: "cwagentconfig",
                    namespace: "amazon-cloudwatch",
                },
                data: {
                    "cwagentconfig.json": JSON.stringify({
                        logs: {
                            metrics_collected: {
                                kubernetes: {
                                    cluster_name: cluster.clusterName,
                                    metrics_collection_interval: 60,
                                },
                            },
                            force_flush_interval: 5,
                        },
                    }),
                },
            });
            cloudwatchConfigMap.node.addDependency(cloudwatchNamespace);

            // Deploy CloudWatch agent DaemonSet
            const cloudwatchDaemonSet = cluster.addManifest("CloudWatchDaemonSet", {
                apiVersion: "apps/v1",
                kind: "DaemonSet",
                metadata: {
                    name: "cloudwatch-agent",
                    namespace: "amazon-cloudwatch",
                },
                spec: {
                    selector: {
                        matchLabels: {
                            name: "cloudwatch-agent",
                        },
                    },
                    template: {
                        metadata: {
                            labels: {
                                name: "cloudwatch-agent",
                            },
                        },
                        spec: {
                            serviceAccountName: "cloudwatch-agent",
                            containers: [
                                {
                                    name: "cloudwatch-agent",
                                    image: "public.ecr.aws/cloudwatch-agent/cloudwatch-agent:latest",
                                    resources: {
                                        limits: {
                                            cpu: "200m",
                                            memory: "200Mi",
                                        },
                                        requests: {
                                            cpu: "200m",
                                            memory: "200Mi",
                                        },
                                    },
                                    env: [
                                        {
                                            name: "HOST_IP",
                                            valueFrom: {
                                                fieldRef: {
                                                    fieldPath: "status.hostIP",
                                                },
                                            },
                                        },
                                        {
                                            name: "HOST_NAME",
                                            valueFrom: {
                                                fieldRef: {
                                                    fieldPath: "spec.nodeName",
                                                },
                                            },
                                        },
                                        {
                                            name: "K8S_NAMESPACE",
                                            valueFrom: {
                                                fieldRef: {
                                                    fieldPath: "metadata.namespace",
                                                },
                                            },
                                        },
                                        {
                                            name: "CI_VERSION",
                                            value: "k8s/1.3.23",
                                        },
                                    ],
                                    volumeMounts: [
                                        {
                                            name: "cwagentconfig",
                                            mountPath: "/etc/cwagentconfig",
                                        },
                                        {
                                            name: "rootfs",
                                            mountPath: "/rootfs",
                                            readOnly: true,
                                        },
                                        {
                                            name: "dockersock",
                                            mountPath: "/var/run/docker.sock",
                                            readOnly: true,
                                        },
                                        {
                                            name: "varlibdocker",
                                            mountPath: "/var/lib/docker",
                                            readOnly: true,
                                        },
                                        {
                                            name: "containerdsock",
                                            mountPath: "/run/containerd/containerd.sock",
                                            readOnly: true,
                                        },
                                        {
                                            name: "sys",
                                            mountPath: "/sys",
                                            readOnly: true,
                                        },
                                        {
                                            name: "devdisk",
                                            mountPath: "/dev/disk",
                                            readOnly: true,
                                        },
                                    ],
                                },
                            ],
                            volumes: [
                                {
                                    name: "cwagentconfig",
                                    configMap: {
                                        name: "cwagentconfig",
                                    },
                                },
                                {
                                    name: "rootfs",
                                    hostPath: {
                                        path: "/",
                                    },
                                },
                                {
                                    name: "dockersock",
                                    hostPath: {
                                        path: "/var/run/docker.sock",
                                    },
                                },
                                {
                                    name: "varlibdocker",
                                    hostPath: {
                                        path: "/var/lib/docker",
                                    },
                                },
                                {
                                    name: "containerdsock",
                                    hostPath: {
                                        path: "/run/containerd/containerd.sock",
                                    },
                                },
                                {
                                    name: "sys",
                                    hostPath: {
                                        path: "/sys",
                                    },
                                },
                                {
                                    name: "devdisk",
                                    hostPath: {
                                        path: "/dev/disk",
                                    },
                                },
                            ],
                            terminationGracePeriodSeconds: 60,
                        },
                    },
                },
            });
            cloudwatchDaemonSet.node.addDependency(cloudwatchServiceAccount);
            cloudwatchDaemonSet.node.addDependency(cloudwatchConfigMap);

            // Add CDK Nag suppression for CloudWatch agent IAM role
            NagSuppressions.addResourceSuppressions(
                cloudwatchAgentRole,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "CloudWatch Container Insights agent requires wildcard permissions to collect metrics and logs from all pods and nodes in the EKS cluster. This is the AWS-recommended configuration for Container Insights.",
                    },
                ],
                true
            );
        }

        // Lambda role will be mapped to EKS cluster for Kubernetes API access

        // 2. Create IAM role for node group with required permissions
        const nodeGroupRole = new iam.Role(this, "NodeGroupRole", {
            assumedBy: Service("EC2").Principal,
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonEKSWorkerNodePolicy"),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonEKS_CNI_Policy"),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonEC2ContainerRegistryReadOnly"),
            ],
        });

        // Add S3 access using new pattern
        assetBucketRecords.forEach((record) => {
            const prefix = record.prefix || "/";
            const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";

            nodeGroupRole.addToPolicy(
                new iam.PolicyStatement({
                    actions: ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
                    resources: [
                        record.bucket.bucketArn,
                        `${record.bucket.bucketArn}${normalizedPrefix}*`,
                    ],
                })
            );
        });

        // Add auxiliary bucket access
        nodeGroupRole.addToPolicy(
            new iam.PolicyStatement({
                actions: ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
                resources: [
                    props.storageResources.s3.assetAuxiliaryBucket.bucketArn,
                    `${props.storageResources.s3.assetAuxiliaryBucket.bucketArn}/*`,
                ],
            })
        );

        // 3. Add node group for pipeline processing
        cluster.addNodegroupCapacity("WorkerNodeGroup", {
            nodegroupName: `rapid-pipeline-eks-workers-${stackIdentifier}`,
            instanceTypes: [ec2.InstanceType.of(ec2.InstanceClass.M5, ec2.InstanceSize.XLARGE2)],
            minSize: props.config.app.pipelines.useRapidPipeline.useEks.minNodes,
            desiredSize: props.config.app.pipelines.useRapidPipeline.useEks.desiredNodes,
            maxSize: props.config.app.pipelines.useRapidPipeline.useEks.maxNodes,
            diskSize: 50,
            nodeRole: nodeGroupRole,
            capacityType: eks.CapacityType.ON_DEMAND,
            labels: {
                role: "pipeline-worker",
                "node.kubernetes.io/instance-type": props.config.app.pipelines.useRapidPipeline.useEks.nodeInstanceType,
            },
            tags: {
                Name: `rapid-pipeline-eks-nodegroup-${stackIdentifier}`,
                ManagedBy: "cdk",
                InstanceType: props.config.app.pipelines.useRapidPipeline.useEks.nodeInstanceType,
            },
        });

        // 4. Create service account for pod S3 access
        const serviceAccountName = "rapid-pipeline-sa";
        const serviceAccount = cluster.addServiceAccount("PipelineServiceAccount", {
            name: serviceAccountName,
            namespace: "default",
        });

        // Add S3 access for the service account using new pattern
        assetBucketRecords.forEach((record) => {
            const prefix = record.prefix || "/";
            const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";

            serviceAccount.role.addToPrincipalPolicy(
                new iam.PolicyStatement({
                    actions: ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
                    resources: [
                        record.bucket.bucketArn,
                        `${record.bucket.bucketArn}${normalizedPrefix}*`,
                    ],
                })
            );
        });

        // Add auxiliary bucket access
        serviceAccount.role.addToPrincipalPolicy(
            new iam.PolicyStatement({
                actions: ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
                resources: [
                    props.storageResources.s3.assetAuxiliaryBucket.bucketArn,
                    `${props.storageResources.s3.assetAuxiliaryBucket.bucketArn}/*`,
                ],
            })
        );

        // Add AWS Marketplace permissions for the service account
        serviceAccount.role.addToPrincipalPolicy(
            new iam.PolicyStatement({
                actions: ["aws-marketplace:RegisterUsage", "aws-marketplace:MeterUsage"],
                resources: ["*"],
            })
        );

        // Define a unique state machine name
        const stateMachineName = `rapid-pipeline-eks-${stackIdentifier}`;

        // 5. Create consolidated Lambda function for pipeline operations (CONSTRUCT, RUN, CHECK, END)
        const consolidatedHandler = buildConsolidatedHandlerFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.kubernetesLayer,
            props.storageResources,
            cluster.clusterName,
            serviceAccountName, // Pass service account name for job manifests
            props.config,
            eksVpc,
            eksPrivateSubnets,
            eksSecurityGroups
        );

        // Grant EKS permissions to the Lambda - add role mapping for cluster access
        consolidatedHandler.role &&
            cluster.awsAuth.addRoleMapping(consolidatedHandler.role, {
                groups: ["system:masters"],
                username: "pipeline-lambda",
            });


        // 6. Create CloudWatch Log Group for State Machine
        const stateMachineLogGroup = new logs.LogGroup(this, "StateMachineLogGroup", {
            retention: logs.RetentionDays.TWO_WEEKS,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        // 7. Create Step Function tasks using the consolidated Lambda

        // Transforms data input for Kubernetes job creation
        const constructPipelineTask = new tasks.LambdaInvoke(this, "ConstructPipeline", {
            lambdaFunction: consolidatedHandler,
            timeout: Duration.minutes(5), // Add timeout for construct operation
            payload: sfn.TaskInput.fromObject({
                operation: "CONSTRUCT_PIPELINE",
                jobName: sfn.JsonPath.stringAt("$.jobName"),
                inputS3AssetFilePath: sfn.JsonPath.stringAt("$.inputS3AssetFilePath"),
                outputS3AssetFilesPath: sfn.JsonPath.stringAt("$.outputS3AssetFilesPath"),
                outputS3AssetPreviewPath: sfn.JsonPath.stringAt("$.outputS3AssetPreviewPath"),
                outputS3AssetMetadataPath: sfn.JsonPath.stringAt("$.outputS3AssetMetadataPath"),
                inputOutputS3AssetAuxiliaryFilesPath: sfn.JsonPath.stringAt(
                    "$.inputOutputS3AssetAuxiliaryFilesPath"
                ),
                isTest: true,
                inputMetadata: sfn.JsonPath.stringAt("$.inputMetadata"),
                inputParameters: sfn.JsonPath.stringAt("$.inputParameters"),
                externalSfnTaskToken: sfn.JsonPath.stringAt("$.externalSfnTaskToken"),
                outputFileType: sfn.JsonPath.stringAt("$.outputFileType"),
            }),
            resultPath: "$.ConstructPipelineResult",
            outputPath: "$",
            retryOnServiceExceptions: true, // Retry on transient AWS service errors
        });

        // Submits Kubernetes job to EKS cluster
        const runJobTask = new tasks.LambdaInvoke(this, "RunJob", {
            lambdaFunction: consolidatedHandler,
            integrationPattern: sfn.IntegrationPattern.REQUEST_RESPONSE,
            timeout: Duration.minutes(10), // Add timeout for job creation
            payload: sfn.TaskInput.fromObject({
                operation: "RUN_JOB",
                jobName: sfn.JsonPath.stringAt("$.jobName"),
                jobManifest: sfn.JsonPath.stringAt("$.ConstructPipelineResult.Payload.jobManifest"),
                externalSfnTaskToken: sfn.JsonPath.stringAt("$.externalSfnTaskToken"),
            }),
            resultPath: "$.RunJobResult",
            outputPath: "$",
            retryOnServiceExceptions: true, // Retry on transient AWS service errors
        });

        // Monitors Kubernetes job execution status
        const checkJobTask = new tasks.LambdaInvoke(this, "CheckJob", {
            lambdaFunction: consolidatedHandler,
            timeout: Duration.minutes(3), // Add timeout for status check
            payload: sfn.TaskInput.fromObject({
                operation: "CHECK_JOB",
                jobName: sfn.JsonPath.stringAt("$.jobName"),
                k8sJobName: sfn.JsonPath.stringAt("$.k8sJobName"),
                externalSfnTaskToken: sfn.JsonPath.stringAt("$.externalSfnTaskToken"),
            }),
            resultPath: "$.CheckJobResult",
            outputPath: "$",
            retryOnServiceExceptions: true, // Retry on transient AWS service errors
        });

        // Final Lambda called on pipeline end to close out the state machine run
        const pipelineEndTask = new tasks.LambdaInvoke(this, "PipelineEnd", {
            lambdaFunction: consolidatedHandler,
            timeout: Duration.minutes(5), // Add timeout for cleanup operations
            payload: sfn.TaskInput.fromObject({
                operation: "PIPELINE_END",
                jobName: sfn.JsonPath.stringAt("$.jobName"),
                k8sJobName: sfn.JsonPath.stringAt("$.k8sJobName"),
                externalSfnTaskToken: sfn.JsonPath.stringAt("$.externalSfnTaskToken"),
                error: sfn.JsonPath.stringAt("$.error"),
            }),
            resultPath: "$.PipelineEndResult",
            outputPath: "$",
            retryOnServiceExceptions: true, // Retry on transient AWS service errors
        });

        // End state: success
        const successState = new sfn.Succeed(this, "Success");
        
        // End state: failure
        const failState = new sfn.Fail(this, "Failure", {
            cause: sfn.JsonPath.stringAt("$.error.Cause || 'Unknown error'"),
            error: sfn.JsonPath.stringAt("$.error.Error || 'PipelineExecutionFailed'"),
        });

        // Error handler passthrough - from Kubernetes job execution
        const handleErrorTask = new sfn.Pass(this, "HandleError", {
            parameters: {
                "jobName.$": "$.jobName",
                k8sJobName: "timeout-before-creation", // Job name placeholder for early-stage errors
                "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                "error.$": "$.error",
                errorContext: {
                    "timestamp.$": "$$.State.EnteredTime",
                    "stateName.$": "$$.State.Name",
                    "executionName.$": "$$.Execution.Name",
                },
            },
            resultPath: "$",
        }).next(pipelineEndTask);

        // Error handler for timeout scenarios
        const handleTimeoutError = new sfn.Pass(this, "HandleTimeoutError", {
            parameters: {
                "jobName.$": "$.jobName",
                k8sJobName: "timeout-before-creation",
                "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                error: {
                    Error: "States.Timeout",
                    Cause: "Lambda function execution timed out",
                },
                errorContext: {
                    "timestamp.$": "$$.State.EnteredTime",
                    "stateName.$": "$$.State.Name",
                    "executionName.$": "$$.Execution.Name",
                },
            },
            resultPath: "$",
        }).next(pipelineEndTask);

        // Error handler for task failures
        const handleTaskFailureError = new sfn.Pass(this, "HandleTaskFailureError", {
            parameters: {
                "jobName.$": "$.jobName",
                "k8sJobName.$": "$.k8sJobName",
                "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                "error.$": "$.error",
                errorContext: {
                    "timestamp.$": "$$.State.EnteredTime",
                    "stateName.$": "$$.State.Name",
                    "executionName.$": "$$.Execution.Name",
                },
            },
            resultPath: "$",
        }).next(pipelineEndTask);

        // End state evaluation: success or failure
        const endChoice = new sfn.Choice(this, "EndChoice")
            .when(sfn.Condition.isPresent("$.error"), failState)
            .otherwise(successState);

        // Add comprehensive error handling to all Lambda tasks
        constructPipelineTask.addCatch(handleErrorTask, {
            errors: ["States.ALL"],
            resultPath: "$.error",
        });

        runJobTask.addCatch(handleErrorTask, {
            errors: ["States.ALL"],
            resultPath: "$.error",
        });

        checkJobTask.addCatch(handleErrorTask, {
            errors: ["States.ALL"],
            resultPath: "$.error",
        });

        // Add timeout-specific error handling
        constructPipelineTask.addCatch(handleTimeoutError, {
            errors: ["States.Timeout"],
            resultPath: "$.error",
        });

        runJobTask.addCatch(handleTimeoutError, {
            errors: ["States.Timeout"],
            resultPath: "$.error",
        });

        checkJobTask.addCatch(handleTimeoutError, {
            errors: ["States.Timeout"],
            resultPath: "$.error",
        });

        // Add task failure specific error handling
        constructPipelineTask.addCatch(handleTaskFailureError, {
            errors: ["States.TaskFailed"],
            resultPath: "$.error",
        });

        runJobTask.addCatch(handleTaskFailureError, {
            errors: ["States.TaskFailed"],
            resultPath: "$.error",
        });

        checkJobTask.addCatch(handleTaskFailureError, {
            errors: ["States.TaskFailed"],
            resultPath: "$.error",
        });

        pipelineEndTask.next(endChoice);

        // Define variables for job monitoring with enhanced configuration
        const maxJobCheckAttempts = 360; // 60 minutes maximum (with 10-second intervals) - increased for large files
        const jobCheckInterval = 10; // seconds between status checks

        // Enhanced counter initialization with additional context
        const counterState = new sfn.Pass(this, "InitializeCounter", {
            parameters: {
                counter: 0,
                maxAttempts: maxJobCheckAttempts,
                checkInterval: jobCheckInterval,
                "jobName.$": "$.jobName",
                "k8sJobName.$": "$.RunJobResult.Payload.body.k8sJobName",
                "status.$": "$.RunJobResult.Payload.body.status",
                "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                "startTime.$": "$$.State.EnteredTime", // Capture job start time for monitoring
            },
        });

        // Enhanced counter increment with validation
        const incrementCounter = new sfn.Pass(this, "IncrementCounter", {
            parameters: {
                "counter.$": "States.MathAdd($.counter, 1)",
                "maxAttempts.$": "$.maxAttempts",
                "checkInterval.$": "$.checkInterval",
                "jobName.$": "$.jobName",
                "k8sJobName.$": "$.k8sJobName",
                "status.$": "$.status",
                "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                "startTime.$": "$.startTime",
                "lastCheckTime.$": "$$.State.EnteredTime", // Track last status check time
            },
        });

        // Define job status check wait state with configurable interval
        const waitX = new sfn.Wait(this, "Wait 10 Seconds", {
            time: sfn.WaitTime.duration(Duration.seconds(jobCheckInterval)),
        });

        // Check job status after waiting with error handling
        waitX.next(checkJobTask);

        // Enhanced timeout handling with detailed error information
        const timeoutJobState = new sfn.Pass(this, "Timeout Job", {
            parameters: {
                "jobName.$": "$.jobName",
                k8sJobName: "failure-before-creation", // Job name placeholder for timeout errors
                "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                status: "FAILED",
                error: {
                    Error: "JobTimeoutError",
                    Cause: sfn.JsonPath.format(
                        "Job exceeded maximum execution time. Attempts: {}, Max: {}, Check Interval: {} seconds",
                        sfn.JsonPath.stringAt("$.counter"),
                        sfn.JsonPath.stringAt("$.maxAttempts"),
                        sfn.JsonPath.stringAt("$.checkInterval")
                    ),
                },
                timeoutContext: {
                    "totalAttempts.$": "$.counter",
                    "maxAttempts.$": "$.maxAttempts",
                    "startTime.$": "$.startTime",
                    "timeoutTime.$": "$$.State.EnteredTime",
                },
            },
        }).next(pipelineEndTask);

        // Enhanced max attempts check with better logic
        const checkMaxAttemptsChoice = new sfn.Choice(this, "Check Max Attempts")
            .when(
                sfn.Condition.numberGreaterThanEquals("$.counter", maxJobCheckAttempts),
                timeoutJobState
            )
            .otherwise(incrementCounter.next(waitX));

        // Enhanced job status choice with better status handling
        const jobStatusChoice = new sfn.Choice(this, "Job Complete?")
            .when(
                sfn.Condition.stringEquals("$.status", "COMPLETED"),
                new sfn.Pass(this, "Job Completed Successfully", {
                    parameters: {
                        "jobName.$": "$.jobName",
                        "k8sJobName.$": "$.k8sJobName",
                        "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                        status: "COMPLETED",
                        completionContext: {
                            "totalAttempts.$": "$.counter",
                            "startTime.$": "$.startTime",
                            "completionTime.$": "$$.State.EnteredTime",
                        },
                    },
                }).next(pipelineEndTask)
            )
            .when(
                sfn.Condition.stringEquals("$.status", "FAILED"),
                new sfn.Pass(this, "Job Failed", {
                    parameters: {
                        "jobName.$": "$.jobName",
                        "k8sJobName.$": "$.k8sJobName",
                        "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                        status: "FAILED",
                        "error.$": "$.error",
                        failureContext: {
                            "totalAttempts.$": "$.counter",
                            "startTime.$": "$.startTime",
                            "failureTime.$": "$$.State.EnteredTime",
                        },
                    },
                }).next(pipelineEndTask)
            )
            .when(sfn.Condition.stringEquals("$.status", "RUNNING"), checkMaxAttemptsChoice)
            .otherwise(
                // Handle unknown status
                new sfn.Pass(this, "Unknown Job Status", {
                    parameters: {
                        "jobName.$": "$.jobName",
                        "k8sJobName.$": "$.k8sJobName",
                        "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                        status: "FAILED",
                        error: {
                            Error: "UnknownJobStatus",
                            Cause: sfn.JsonPath.format(
                                "Received unknown job status: {}",
                                sfn.JsonPath.stringAt("$.status")
                            ),
                        },
                        unknownStatusContext: {
                            "receivedStatus.$": "$.status",
                            "totalAttempts.$": "$.counter",
                            "startTime.$": "$.startTime",
                            "errorTime.$": "$$.State.EnteredTime",
                        },
                    },
                }).next(pipelineEndTask)
            );

        // Add error handling to the job status check
        checkJobTask.next(jobStatusChoice);

        // Define the state machine - connect the workflow
        const definition = constructPipelineTask
            .next(runJobTask)
            .next(counterState)
            .next(checkJobTask);

        // 8. Create Step Function State Machine with enhanced configuration
        const stateMachine = new sfn.StateMachine(this, "StateMachine", {
            definition,
            timeout: Duration.hours(6), // Increased timeout for large file processing
            logs: {
                destination: stateMachineLogGroup,
                includeExecutionData: true,
                level: sfn.LogLevel.ALL,
            },
            tracingEnabled: true,
            stateMachineName: stateMachineName,
            comment: "Enhanced EKS Pipeline with comprehensive error handling and monitoring",
        });

        // 9. Create separate openPipeline Lambda function using builder
        // This Lambda starts the state machine and is NOT referenced by the state machine
        const openPipelineHandler = buildOpenPipelineEKSFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            stateMachine,
            props.config,
            eksVpc,
            eksPrivateSubnets,
            eksSecurityGroups
        );

        // 10. Create vamsExecute Lambda function using builder
        const vamsExecuteHandler = buildVamsExecuteRapidPipelineEKSFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            openPipelineHandler,
            props.config,
            eksVpc,
            eksPrivateSubnets,
            eksSecurityGroups
        );

        // Set the public properties
        this.pipelineVamsLambdaFunctionName = vamsExecuteHandler.functionName;
        this.openPipelineLambdaFunctionName = openPipelineHandler.functionName;

        // Outputs
        new CfnOutput(this, "EksClusterName", {
            value: cluster.clusterName,
            description: "EKS Cluster Name",
        });

        new CfnOutput(this, "StateMachineArn", {
            value: stateMachine.stateMachineArn,
            description: "Step Functions State Machine ARN",
        });

        new CfnOutput(this, "ConsolidatedHandlerArn", {
            value: consolidatedHandler.functionArn,
            description: "Consolidated Lambda Handler ARN",
        });

        new CfnOutput(this, "OpenPipelineHandlerArn", {
            value: openPipelineHandler.functionArn,
            description: "Open Pipeline Lambda Handler ARN",
        });

        new CfnOutput(this, "VamsExecuteHandlerArn", {
            value: vamsExecuteHandler.functionArn,
            description: "VAMS Execute Lambda Handler ARN",
        });

        // Add CDK Nag suppressions
        NagSuppressions.addResourceSuppressions(
            consolidatedHandler,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Lambda function requires wildcard permissions for EKS cluster operations and dynamic S3 bucket access within the VAMS asset management system.",
                },
                {
                    id: "AwsSolutions-IAM4",
                    reason: "Using AWS managed policies for Lambda execution role as recommended by AWS best practices.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            nodeGroupRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "Using AWS managed policies for EKS node group role as required by EKS service.",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Node group requires wildcard permissions for dynamic S3 bucket access within the VAMS asset management system.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            serviceAccount.role,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Service account requires wildcard permissions for dynamic S3 bucket access and AWS Marketplace metering.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            stateMachine,
            [
                {
                    id: "AwsSolutions-SF1",
                    reason: "Step Functions state machine has comprehensive logging enabled with CloudWatch Logs.",
                },
                {
                    id: "AwsSolutions-SF2",
                    reason: "Step Functions state machine has X-Ray tracing enabled for monitoring and debugging.",
                },
            ],
            true
        );
    }
}
