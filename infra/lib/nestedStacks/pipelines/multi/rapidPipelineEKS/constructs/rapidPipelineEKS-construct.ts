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
import {
    grantExternalAssetBucketKmsKeys,
    kmsKeyPolicyStatementGenerator,
} from "../../../../../helper/security";
import * as Config from "../../../../../../config/config";
import * as path from "path";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";
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
    importGlobalPipelineWorkflowV2FunctionName: string; // V2 vamsSchema import CR lambda name
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
            version: eks.KubernetesVersion.of(
                props.config.app.pipelines.useRapidPipeline.useEks.eksClusterVersion
            ),
            clusterName: `rapid-pipeline-eks-${stackIdentifier}`,
            vpc: eksVpc,
            vpcSubnets: [{ subnets: eksPrivateSubnets }], // Always use private subnets for EKS cluster
            defaultCapacity: 0, // No default node group
            // The Kubernetes API endpoint is reachable only from inside the VPC. Nothing outside VAMS
            // ever calls it: the only clients are this stack's own cluster-handler and kubectl provider
            // functions, and the node group, all of which live in the same private subnets. A public
            // endpoint on a cluster whose sole consumers are in-VPC is an internet-facing control plane
            // for no benefit, and it is also what made the pipeline unusable in a VPC-isolated
            // deployment.
            //
            // Both of the following are required together. Private access needs the cluster handler
            // inside the VPC — aws-cdk-lib states it outright ("requires ... placeClusterHandlerInVpc to
            // be set to true") — and it needs the VPC to resolve the endpoint's private hosted zone,
            // which needs DNS support and DNS hostnames on the VPC. The VPC builder enables both.
            endpointAccess: eks.EndpointAccess.PRIVATE,
            placeClusterHandlerInVpc: true,
            kubectlLayer: props.kubectlLayer, // Use our multi-runtime kubectl layer
            securityGroup: eksClusterSecurityGroup,
            // Observability configuration (configurable via config.json)
            clusterLogging: props.config.app.pipelines.useRapidPipeline.useEks.observability
                .enableControlPlaneLogs
                ? [
                      eks.ClusterLoggingTypes.API,
                      eks.ClusterLoggingTypes.AUDIT,
                      eks.ClusterLoggingTypes.AUTHENTICATOR,
                      eks.ClusterLoggingTypes.CONTROLLER_MANAGER,
                      eks.ClusterLoggingTypes.SCHEDULER,
                  ]
                : undefined,
        });

        // A private Kubernetes API endpoint is reached over the cluster's cross-account network
        // interfaces inside the VPC, so the pipeline Lambdas' security groups have to be admitted on
        // 443. This is not needed for a public endpoint — that path leaves the VPC through the NAT
        // gateway and arrives from a public address, which no security group governs — so the rule
        // belongs with the private endpoint above rather than being independent of it.
        //
        // The interfaces carry the EKS-managed cluster security group (which admits only itself) and
        // the group declared above (which admits nothing), so without this every Kubernetes call ends
        // in a connection timeout after the client's retries, and the pipeline reports a failure whose
        // stated cause is a missing job name.
        for (const pipelineSecurityGroup of eksSecurityGroups) {
            cluster.connections.allowFrom(
                pipelineSecurityGroup,
                ec2.Port.tcp(443),
                "Pipeline Lambda functions call the private Kubernetes API endpoint"
            );
        }

        // Enable CloudWatch Container Insights if configured
        if (
            props.config.app.pipelines.useRapidPipeline.useEks.observability.enableContainerInsights
        ) {
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
            const cloudwatchServiceAccount = new eks.ServiceAccount(
                this,
                "CloudWatchServiceAccount",
                {
                    cluster: cluster,
                    name: "cloudwatch-agent",
                    namespace: "amazon-cloudwatch",
                }
            );

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
                                    // Pinned rather than :latest. This DaemonSet runs on every node
                                    // with a host filesystem and container-runtime socket mounted,
                                    // so a moving tag changes what has that access without any
                                    // change to this repository. The bare version tag is the
                                    // multi-arch manifest; the -amd64/-arm64 variants are not.
                                    image: "public.ecr.aws/cloudwatch-agent/cloudwatch-agent:1.300072.0b1766",
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
                "node.kubernetes.io/instance-type":
                    props.config.app.pipelines.useRapidPipeline.useEks.nodeInstanceType,
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
            // Build the object-level resource as {bucketArn}/{prefix}*. Strip any
            // leading slash from the prefix so the '/' separator after the bucket
            // ARN is always present (root prefix yields {bucketArn}/*).
            const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";
            const objectPrefix = normalizedPrefix.replace(/^\/+/, "");

            serviceAccount.role.addToPrincipalPolicy(
                new iam.PolicyStatement({
                    actions: ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
                    resources: [
                        record.bucket.bucketArn,
                        `${record.bucket.bucketArn}/${objectPrefix}*`,
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

        // The VAMS-managed CMK, without which the S3 permissions above are unusable.
        //
        // The asset and auxiliary buckets are encrypted with this key when
        // `useKmsCmkEncryption` is enabled, and S3 requires `kms:Decrypt` on the key to serve
        // GetObject — so the pod's download failed with AccessDenied naming kms:Decrypt while every
        // s3: action it needed was already granted. The external-bucket grant below covers only
        // customer-managed keys on buckets VAMS does not own, so the deployment's own key was the one
        // key the role could not use.
        if (props.storageResources.encryption.kmsKey) {
            serviceAccount.role.addToPrincipalPolicy(
                kmsKeyPolicyStatementGenerator(props.storageResources.encryption.kmsKey)
            );
        }

        // Grant access to any external asset bucket customer managed KMS keys so the
        // pod can read/write objects in cross-account encrypted buckets
        // (no-op when no external keys are configured)
        grantExternalAssetBucketKmsKeys(serviceAccount.role);

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

        // Kubernetes access for the pipeline Lambda, scoped to the jobs it runs.
        //
        // The handler was mapped into system:masters, which is cluster-admin — a single Lambda
        // compromise became full cluster takeover. What it actually calls is a closed set, enumerated
        // from the client calls in kubernetes_utils.py: create / get / delete a batch/v1 Job and read
        // its status, list pods and read pod logs, and list events. All of it is namespaced to
        // KUBERNETES_NAMESPACE, which the handler defaults to "default" and the job manifests use.
        // The one cluster-scoped call in the file, list_namespace, sits in
        // validate_kubernetes_environment(), which nothing invokes.
        //
        // Server discovery (/version, /api/v1) needs no rule here: those are non-resource URLs already
        // granted to system:authenticated by the built-in system:discovery and
        // system:public-info-viewer cluster roles.
        const pipelineKubernetesGroup = "vams-rapid-pipeline";
        const pipelineRbacName = "vams-rapid-pipeline-job-runner";

        const pipelineJobRole = cluster.addManifest("PipelineJobRunnerRole", {
            apiVersion: "rbac.authorization.k8s.io/v1",
            kind: "Role",
            metadata: { name: pipelineRbacName, namespace: "default" },
            rules: [
                {
                    apiGroups: ["batch"],
                    resources: ["jobs"],
                    verbs: ["create", "get", "delete"],
                },
                {
                    apiGroups: ["batch"],
                    resources: ["jobs/status"],
                    verbs: ["get"],
                },
                {
                    apiGroups: [""],
                    resources: ["pods"],
                    verbs: ["get", "list"],
                },
                {
                    apiGroups: [""],
                    resources: ["pods/log"],
                    verbs: ["get"],
                },
                {
                    apiGroups: [""],
                    resources: ["events"],
                    verbs: ["list"],
                },
            ],
        });

        const pipelineJobRoleBinding = cluster.addManifest("PipelineJobRunnerRoleBinding", {
            apiVersion: "rbac.authorization.k8s.io/v1",
            kind: "RoleBinding",
            metadata: { name: pipelineRbacName, namespace: "default" },
            roleRef: {
                apiGroup: "rbac.authorization.k8s.io",
                kind: "Role",
                name: pipelineRbacName,
            },
            subjects: [
                {
                    kind: "Group",
                    name: pipelineKubernetesGroup,
                    apiGroup: "rbac.authorization.k8s.io",
                },
            ],
        });
        pipelineJobRoleBinding.node.addDependency(pipelineJobRole);

        consolidatedHandler.role &&
            cluster.awsAuth.addRoleMapping(consolidatedHandler.role, {
                groups: [pipelineKubernetesGroup],
                username: "pipeline-lambda",
            });

        // Ordering matters on an UPGRADE, where the aws-auth mapping is being narrowed rather than
        // created. If the ConfigMap were patched first, the Lambda would hold a group that grants
        // nothing until the RoleBinding landed, and a failure in between would leave the pipeline with
        // no Kubernetes access at all.
        cluster.awsAuth.node.addDependency(pipelineJobRoleBinding);

        // 6. Create CloudWatch Log Group for State Machine
        const stateMachineLogGroup = new logs.LogGroup(this, "StateMachineLogGroup", {
            encryptionKey: props.storageResources.encryption.kmsKey,
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
                inputMetadataS3Location: sfn.JsonPath.stringAt("$.inputMetadataS3Location"),
                inputConfigurationS3Location: sfn.JsonPath.stringAt(
                    "$.inputConfigurationS3Location"
                ),
                externalSfnTaskToken: sfn.JsonPath.stringAt("$.externalSfnTaskToken"),
                outputFileType: sfn.JsonPath.stringAt("$.outputFileType"),
                // The asset id resolved for this run. CONSTRUCT_PIPELINE locates the input file's
                // subdirectory within the asset with it, so the converted file is written beside its
                // source rather than at the asset root.
                assetId: sfn.JsonPath.stringAt("$.assetId"),
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

        // The success-path counterpart of PipelineEnd. It differs in one way that matters: it does not
        // pass `error`. The handler decides which callback to send with `has_error = "error" in event`,
        // a PRESENCE test — so an `error` key holding an empty value would still be read as a failure
        // and send SendTaskFailure for a job that succeeded. That is why this is a separate state
        // rather than one task with a conditional field, which Step Functions parameters cannot express.
        const pipelineEndSuccessTask = new tasks.LambdaInvoke(this, "PipelineEndSuccess", {
            lambdaFunction: consolidatedHandler,
            timeout: Duration.minutes(5),
            payload: sfn.TaskInput.fromObject({
                operation: "PIPELINE_END",
                jobName: sfn.JsonPath.stringAt("$.jobName"),
                k8sJobName: sfn.JsonPath.stringAt("$.k8sJobName"),
                externalSfnTaskToken: sfn.JsonPath.stringAt("$.externalSfnTaskToken"),
            }),
            resultPath: "$.PipelineEndResult",
            outputPath: "$",
            retryOnServiceExceptions: true,
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

        // Both PipelineEnd variants exit through the same Choice, so the state machine has one ending.
        // EndChoice tests IsPresent("$.error"), which the success path does not carry, so it resolves
        // to Succeed — the routing decision stays in one place rather than being implied by which task
        // happens to be terminal.
        pipelineEndSuccessTask.next(endChoice);

        // Define variables for job monitoring with enhanced configuration
        const jobCheckInterval = 10; // seconds between status checks

        // The poll ceiling is DERIVED from the configured job timeout, and deliberately outlives it.
        //
        // It was a hardcoded 360 attempts — 60 minutes — while the Kubernetes pod is given
        // `useEks.jobTimeout` seconds (7200 by default) and the registered bundle declares a
        // taskTimeout of 14400. A job running between those two figures was reported FAILED to the
        // parent workflow, via SendTaskFailure, while the pod carried on for up to another hour and
        // kept writing output. `useEks.jobTimeout` was meanwhile read by nothing at all: a configured
        // value that did nothing.
        //
        // The margin matters and is not padding. If the ceiling merely EQUALLED the pod deadline the
        // two clocks would expire together, so the poll would give up at the same instant Kubernetes
        // terminated the pod and the outcome would again be reported as a timeout rather than as the
        // pod's own failure. One extra minute lets the poll observe the terminated pod and report what
        // actually happened.
        const jobTimeoutSeconds = props.config.app.pipelines.useRapidPipeline.useEks.jobTimeout;
        const pollMarginSeconds = 60;
        const maxJobCheckAttempts = Math.ceil(
            (jobTimeoutSeconds + pollMarginSeconds) / jobCheckInterval
        );

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

        // RUN_JOB reports a failure by returning a 4xx/5xx body, which is not an invocation error and
        // so is not caught by the task's Catch. Nothing examined it: the run went straight to
        // InitializeCounter, which reads $.RunJobResult.Payload.body.k8sJobName and fails the whole
        // state machine with States.Runtime for a JSONPath that "could not be found".
        //
        // Two things went wrong there, and the second is the expensive one. The reported cause named a
        // missing field rather than the actual failure, and States.Runtime in a Pass state is raised
        // before the state is entered, so it is routed by no Catch and never reaches PipelineEnd —
        // leaving the parent workflow's task token unreleased until its taskTimeout hours later.
        const handleRunJobError = new sfn.Pass(this, "HandleRunJobError", {
            parameters: {
                "jobName.$": "$.jobName",
                k8sJobName: "failure-before-creation", // No Kubernetes job exists to clean up
                "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                status: "FAILED",
                error: {
                    Error: "RunJobFailed",
                    "Cause.$": "States.JsonToString($.RunJobResult.Payload)",
                },
                errorContext: {
                    "timestamp.$": "$$.State.EnteredTime",
                    "stateName.$": "$$.State.Name",
                    "executionName.$": "$$.Execution.Name",
                },
            },
            resultPath: "$",
        }).next(pipelineEndTask);

        // Written as the NEGATIVE case on purpose. IsPresent is the one comparison that is defined on
        // an absent path — a value comparison against one is what raised States.Runtime in the first
        // place — and it guards exactly the two fields InitializeCounter goes on to read.
        const runJobOutcomeChoice = new sfn.Choice(this, "RunJobSucceeded?")
            .when(
                sfn.Condition.not(
                    sfn.Condition.isPresent("$.RunJobResult.Payload.body.k8sJobName")
                ),
                handleRunJobError
            )
            .otherwise(counterState);

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
                }).next(pipelineEndSuccessTask)
            )
            .when(
                sfn.Condition.stringEquals("$.status", "FAILED"),
                new sfn.Pass(this, "Job Failed", {
                    parameters: {
                        "jobName.$": "$.jobName",
                        "k8sJobName.$": "$.k8sJobName",
                        "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                        status: "FAILED",
                        // Built here rather than read from $.error. A job that reports FAILED through
                        // the poll loop has raised no state-machine error, so no Catch has written
                        // $.error and reading it would fail this state with States.Runtime — the
                        // failure would be reported as a missing field and would skip PipelineEnd,
                        // leaving the parent's task token unreleased.
                        error: {
                            Error: "KubernetesJobFailed",
                            Cause: sfn.JsonPath.format(
                                "Kubernetes job {} reported status FAILED after {} status checks",
                                sfn.JsonPath.stringAt("$.k8sJobName"),
                                sfn.JsonPath.stringAt("$.counter")
                            ),
                        },
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

        // Lift the status CheckJob just reported into the field the choice above reads.
        //
        // Without this the loop can never end on its own. CheckJob writes its response under
        // `resultPath: "$.CheckJobResult"`, so the fresh status lands at
        // $.CheckJobResult.Payload.body.status — while "Job Complete?" compares $.status, which is set
        // once by InitializeCounter and then copied forward unchanged by IncrementCounter. A job that
        // starts RUNNING therefore reads as RUNNING for every subsequent check no matter what
        // Kubernetes reports, so the poll runs to its ceiling and the run is reported as a timeout
        // however it actually finished.
        //
        // Every field the loop and its exits consume is carried through explicitly: counter,
        // maxAttempts and checkInterval for "Check Max Attempts" and IncrementCounter, startTime for
        // the completion and timeout contexts, and jobName / k8sJobName / externalSfnTaskToken for
        // PipelineEnd. CheckJobResult itself is dropped, because from here on the status is what
        // matters and keeping it would grow the state payload on every iteration.
        const recordJobStatus = new sfn.Pass(this, "RecordJobStatus", {
            parameters: {
                "counter.$": "$.counter",
                "maxAttempts.$": "$.maxAttempts",
                "checkInterval.$": "$.checkInterval",
                "jobName.$": "$.jobName",
                "k8sJobName.$": "$.k8sJobName",
                "status.$": "$.CheckJobResult.Payload.body.status",
                "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                "startTime.$": "$.startTime",
            },
            resultPath: "$",
        });

        // Add error handling to the job status check
        checkJobTask.next(recordJobStatus);
        recordJobStatus.next(jobStatusChoice);

        // Define the state machine - connect the workflow
        counterState.next(checkJobTask);
        const definition = constructPipelineTask.next(runJobTask).next(runJobOutcomeChoice);

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
            eksSecurityGroups,
            props.storageResources.eventBridge.orchestrationBus,
            stateMachineLogGroup
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

        // Auto-register with VAMS (V2 vamsSchema bundle -> V2 pipeline/workflow/template tables).
        if (props.config.app.pipelines.useRapidPipeline.useEks.autoRegisterWithVAMS === true) {
            new VamsSchemaRegistration(this, "RapidPipelineEKSRegistration", {
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
                    "backendPipelines",
                    "multi",
                    "rapidPipelineEKS",
                    "vamsSchema"
                ),
                resourceOverrides: {
                    lambdaName: this.pipelineVamsLambdaFunctionName,
                },
                idOverrides: {
                    pipelineId: "rapid-pipeline-eks-to-glb",
                    workflowId: "rapid-pipeline-eks-to-glb",
                },
            });
        }

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
