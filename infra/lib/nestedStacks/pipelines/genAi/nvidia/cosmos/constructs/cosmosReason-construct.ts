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
import * as kms from "aws-cdk-lib/aws-kms";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as batch from "aws-cdk-lib/aws-batch";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Duration, Stack, RemovalPolicy } from "aws-cdk-lib";
import { Construct } from "constructs";
import {
    buildConstructReasonPipelineFunction,
    buildOpenReasonPipelineFunction,
    buildVamsExecuteCosmosReasonPipelineFunction,
    buildReasonPipelineEndFunction,
} from "../lambdaBuilder/cosmosReasonFunctions";
import { NagSuppressions } from "cdk-nag";
import { CfnOutput } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as ServiceHelper from "../../../../../../helper/service-helper";
import { Service } from "../../../../../../helper/service-helper";
import * as s3AssetBuckets from "../../../../../../helper/s3AssetBuckets";
import * as Config from "../../../../../../../config/config";
import {
    NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
    generateUniqueNameHash,
    grantExternalAssetBucketKmsKeys,
    kmsKeyPolicyStatementGenerator,
} from "../../../../../../helper/security";
import { VamsSchemaRegistration } from "../../../../constructs/vamsSchemaRegistration-construct";
import { populateHuggingFaceTokenSecret } from "../../customResources/populateHuggingFaceTokenSecret";
import { DockerImageAsset, Platform } from "aws-cdk-lib/aws-ecr-assets";

export interface CosmosReasonConstructProps extends cdk.StackProps {
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
const defaultProps: Partial<CosmosReasonConstructProps> = {};

/**
 * CosmosReasonConstruct
 *
 * Creates resources for the NVIDIA Cosmos Reason 2 VLM pipeline.
 * Cosmos Reason analyzes video files and generates text output
 * (captions, descriptions, reasoning, JSON structured data).
 *
 * Shares EFS and S3 model cache from CosmosCommonConstruct.
 * Creates its own Batch compute environment, job definitions, and Lambda functions.
 */
export class CosmosReasonConstruct extends Construct {
    public pipelineReason2BVamsLambdaFunctionName?: string;
    public pipelineReason8BVamsLambdaFunctionName?: string;

    constructor(parent: Construct, name: string, props: CosmosReasonConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;
        const cosmosConfig = props.config.app.pipelines.useNvidiaCosmos;

        /**
         * Shared resources from common construct
         */
        const modelCacheBucket = props.modelCacheBucket;
        const cosmosEfs = props.efsFileSystem;
        const nfsSecurityGroup = props.efsSecurityGroup;

        /**
         * HuggingFace Token stored in Secrets Manager
         * Uses the same token as Predict (from CDK config) but stored in a separate secret,
         * which Batch injects into the container so the token is never an env var value.
         * The secret is created EMPTY and populated at deploy time by a custom resource that
         * carries the token in its code asset, so the token never lands in the synthesized
         * CloudFormation template.
         */
        const hfTokenSecret = new secretsmanager.Secret(this, "CosmosReasonHfTokenSecret", {
            description: "HuggingFace API token for downloading NVIDIA Cosmos Reason models",
            // Imported by ARN, not passed as the key object: the grants CDK derives from
            // grantRead/grantWrite then land only on each grantee's own policy. Passing the object
            // writes those grantees into the key's resource policy, which makes the storage stack
            // that owns the key reference this pipeline stack and forms a circular dependency.
            encryptionKey: props.storageResources.encryption.kmsKey
                ? kms.Key.fromKeyArn(
                      this,
                      "HfTokenSecretKmsKeyRef",
                      props.storageResources.encryption.kmsKey.keyArn
                  )
                : undefined,
        });

        populateHuggingFaceTokenSecret(
            this,
            "CosmosReasonHfTokenSecretPopulate",
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
         * Docker Container Image from ECR (Reason)
         * If codeBuildImageUri is provided, use that directly (CodeBuild-built image in ECR).
         * Otherwise, fall back to inline DockerImageAsset build.
         */
        let containerImage: DockerImageAsset | undefined;
        if (!props.codeBuildImageUri) {
            containerImage = new DockerImageAsset(this, "CosmosReasonContainerImage", {
                directory: path.join(
                    __dirname,
                    "../../../../../../../../backendPipelines/genAi/nvidia/cosmos/reason/container"
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

        const containerExecutionRole = new iam.Role(this, "CosmosReasonContainerExecutionRole", {
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

        // Grant execution role access to read the HF token secret
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

        const containerJobRole = new iam.Role(this, "CosmosReasonContainerJobRole", {
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
         * Uses standard GPU instances (same class as predict 2B) for Reason inference
         */
        const instanceRole = new iam.Role(this, "ReasonBatchInstanceRole", {
            assumedBy: Service("EC2").Principal,
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonEC2ContainerServiceforEC2Role"
                ),
            ],
        });

        const instanceProfile = new iam.CfnInstanceProfile(this, "ReasonBatchInstanceProfile", {
            roles: [instanceRole.roleName],
        });

        // Batch compute security group - allow outbound and NFS access to EFS
        const batchSecurityGroup = new ec2.SecurityGroup(this, "ReasonBatchSecurityGroup", {
            vpc: props.vpc,
            description:
                "Security group for Cosmos Reason Batch compute environment with internet access",
            allowAllOutbound: true,
        });

        // Allow NFS traffic from Batch compute SG to EFS SG
        nfsSecurityGroup.addIngressRule(
            batchSecurityGroup,
            ec2.Port.tcp(2049),
            "Allow NFS from Cosmos Reason Batch compute to EFS"
        );

        // Determine instance types from config
        const reasonConfig = cosmosConfig.modelsReason!;
        const instanceTypes = reasonConfig.reason2B.enabled
            ? reasonConfig.reason2B.instanceTypes
            : reasonConfig.reason8B.instanceTypes;

        // Max vCPUs across all Reason model types
        const maxVCpus = Math.max(
            reasonConfig.reason2B.enabled ? reasonConfig.reason2B.maxVCpus : 0,
            reasonConfig.reason8B.enabled ? reasonConfig.reason8B.maxVCpus : 0,
            48
        );

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

        const launchTemplate = new ec2.CfnLaunchTemplate(this, "ReasonBatchLaunchTemplate", {
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
                // Encoded by CloudFormation, not here: this string carries CDK tokens -- the EFS file
                // system id among them -- and Buffer.from() freezes a token as its DEBUG TEXT, because a
                // base64 blob is opaque to the resolver that runs afterwards. The deployed template read
                // "mount -t efs -o tls ${Token[TOKEN.NNNN]}:/ /mnt/efs/cosmos-models", which bash parses as
                // an array subscript ("invalid arithmetic operator") and which aborts the whole
                // scripts-user module, skipping every later line too. So the model cache was never mounted
                // and every run restored its weights from S3 on billed GPU time. Fn.base64 emits
                // Fn::Base64, so the encoding happens after token resolution.
                userData: cdk.Fn.base64(userData),
                tagSpecifications: [
                    {
                        resourceType: "instance",
                        tags: [
                            {
                                key: "Name",
                                value: `VAMS-Cosmos-Reason-Batch`,
                            },
                        ],
                    },
                ],
            },
        });

        const batchEnvironment = new batch.CfnComputeEnvironment(
            this,
            "CosmosReasonGpuComputeEnv",
            {
                type: "MANAGED",
                state: "ENABLED",
                // No serviceRole, so this environment uses the Batch service-linked role
                // (AWSServiceRoleForBatch). Naming a role instead is what forbids an in-place update of the
                // launch template, instance types, subnets or security groups -- Batch allows those fields to
                // be updated "only for ... Compute Environment having a Batch Service Linked Role" -- which
                // left this environment unable to take a change to its instance start-up script at all.
                //
                // Safe to ship to an existing deployment only because the construct id changed in the same
                // release: that makes the upgrade a CREATE of this resource and a DELETE of the old one rather
                // than an update, so replaceComputeEnvironment does not have to permit the one replacement the
                // upgrade needs. An environment still naming a service role can be neither updated in place nor
                // migrated to the service-linked role, so this property without the rename fails the upgrade.
                replaceComputeEnvironment: false,
                computeResources: {
                    type: "EC2",
                    allocationStrategy: "BEST_FIT_PROGRESSIVE",
                    // Each infrastructure update takes the current ECS-optimised AMI rather than staying on
                    // the one that was current when this environment was created, which matters on a GPU image
                    // carrying drivers. It is also the fourth condition CloudFormation names for updating a
                    // compute environment in place, alongside no serviceRole, a progressive allocation strategy
                    // and replaceComputeEnvironment.
                    updateToLatestImageVersion: true,
                    minvCpus: 0,
                    maxvCpus: maxVCpus * 2,
                    desiredvCpus: 0,
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
                        // Pinned to this template's own latest version, not "$Latest". Batch does not read
                        // the launch template when an instance launches: it MERGES it with its own bootstrap
                        // into a Batch-managed copy when the compute environment is created or updated.
                        // "$Latest" is a constant, so a new template version is not a change to the
                        // environment -- CloudFormation updates nothing and Batch goes on handing instances a
                        // stale merge, which is how the encoding fix above reached no instance at all.
                        version: launchTemplate.attrLatestVersionNumber,
                    },
                },
            }
        );

        const batchJobQueue = new batch.CfnJobQueue(this, "CosmosReasonBatchJobQueue", {
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
            const tempTaskDef = new ecs.TaskDefinition(this, "ReasonTempTaskDef", {
                compatibility: ecs.Compatibility.EC2,
            });
            const container = tempTaskDef.addContainer("ReasonContainer", {
                image: ecs.ContainerImage.fromDockerImageAsset(containerImage!),
                memoryLimitMiB: 1024,
                logging: ecs.LogDrivers.awsLogs({
                    streamPrefix: "batch-reason-temp",
                }),
            });
            containerImageName = container.imageName;
        }

        /**
         * Shared Lambda Functions
         */
        const constructPipelineFunction = buildConstructReasonPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            props.storageResources.encryption.kmsKey
        );

        const pipelineEndFunction = buildReasonPipelineEndFunction(
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
            modelSize: string,
            pipelineId: string,
            pipelineDescription: string,
            isAutoRegister: boolean,
            autoTriggerExtensions: string,
            gpuCount: number,
            memoryMb: number,
            vcpus: number
        ) => {
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
                    { name: "MODEL_TYPE", value: "reason" },
                    { name: "MODEL_SIZE", value: modelSize },
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
                    value: "compute,utility",
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
             * Step Functions State Machine for this model
             */

            const successState = new sfn.Succeed(this, `SuccessState-${modelKey}`, {
                comment: `Cosmos Reason ${modelKey} pipeline returned SUCCESS`,
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

            // error handler passthrough - Construct Pipeline Lambda.
            //
            // Without it a failure in the FIRST state ends the execution before PipelineEndTask runs,
            // and PipelineEndTask is the only state that resolves the parent workflow's task token.
            // The parent's waitForCallback task then stays RUNNING for its whole taskTimeout — eight
            // hours on these pipelines — for a job that failed in under a second.
            const handleConstructPipelineError = new sfn.Pass(
                this,
                `HandleConstructPipelineError-${modelKey}`,
                { resultPath: "$" }
            ).next(pipeLineEndTask);

            const constructPipelineTask = new tasks.LambdaInvoke(
                this,
                `ConstructPipelineTask-${modelKey}`,
                {
                    lambdaFunction: constructPipelineFunction,
                    outputPath: "$.Payload",
                }
            ).addCatch(handleConstructPipelineError, {
                resultPath: "$.error",
            });

            const batchJob = new tasks.BatchSubmitJob(this, `CosmosBatchJob-${modelKey}`, {
                jobName: sfn.JsonPath.stringAt("$.jobName"),
                jobDefinitionArn: batchJobDefinition.attrJobDefinitionArn,
                jobQueueArn: batchJobQueue.ref,
                containerOverrides: {
                    command: [...sfn.JsonPath.listAt("$.definition")],
                    environment: {
                        AWS_REGION: region,
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
                `CosmosReason-${modelKey}-LogGroup`,
                {
                    logGroupName:
                        `/aws/vendedlogs/VAMSstateMachine-CosmosReason-${modelKey}` +
                        generateUniqueNameHash(
                            props.config.env.coreStackName,
                            props.config.env.account,
                            `CosmosReason-${modelKey}-StateMachineLogGroup`,
                            10
                        ),
                    encryptionKey: props.storageResources.encryption.kmsKey,
                    retention: logs.RetentionDays.ONE_YEAR,
                    removalPolicy: RemovalPolicy.DESTROY,
                }
            );

            const pipelineStateMachine = new sfn.StateMachine(
                this,
                `CosmosReason-${modelKey}-StateMachine`,
                {
                    definitionBody: sfn.DefinitionBody.fromChainable(sfnDefinition),
                    // Envelopes the Batch attempt (attemptDurationSeconds 28800) so a long-running
                    // job reaches its own failure path — and the task-token callback — rather than
                    // being cut short by the state machine.
                    timeout: Duration.hours(9),
                    logs: {
                        destination: stateMachineLogGroup,
                        includeExecutionData: true,
                        level: sfn.LogLevel.ALL,
                    },
                    tracingEnabled: true,
                }
            );

            // The Batch `.sync` integration polls the submitted job and cancels it when the
            // execution stops. Batch job ids are runtime-generated with no name pattern to
            // scope on, so both actions take a wildcard resource.
            pipelineStateMachine.addToRolePolicy(
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["batch:DescribeJobs", "batch:TerminateJob"],
                    resources: ["*"],
                })
            );

            /**
             * Lambda: openPipeline
             */
            // Video only. Still images are rejected rather than accepted and then failed: NVIDIA's
            // vendored cosmos_reason2_utils crashes when writing an image input back out
            // (offline_inference calls save_tensor on a PIL Image, whose _tensor_to_pil_images reads
            // tensor.ndim), so an image run loads the model, completes inference, and then dies — a
            // paid GPU run to discover an unsupported input. Restore the image extensions in all five
            // places together, once upstream handles an image input:
            //   1. here;
            //   2. the reason vamsSchema bundles' inputFileFilters;
            //   3. their `description` field — a different key in the same two files, and the one an
            //      operator actually reads: vamsSchemaImport registers it into the pipeline record, so
            //      it is the text the web pipeline list and `vamscli pipeline` display, alongside the
            //      pipelineDescription literals below;
            //   4. the docs;
            //   5. openPipeline.py's own module default for ALLOWED_INPUT_FILEEXTENSIONS — what a
            //      direct or local invoke carrying no Lambda environment falls back to, so leaving it
            //      broad accepts an image the deployment rejects.
            const allowedInputFileExtensions = ".mp4,.mov";
            const openPipelineFunction = buildOpenReasonPipelineFunction(
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
                modelKey
            );

            /**
             * Lambda: vamsExecute
             */
            const vamsExecuteFunction = buildVamsExecuteCosmosReasonPipelineFunction(
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
             * tables). Each model variant has its own per-model bundle under
             * vamsSchema/reason-<modelSize> (e.g. reason-2b).
             */
            if (isAutoRegister) {
                new VamsSchemaRegistration(this, `CosmosReason-${modelKey}-Registration`, {
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
                        "reason",
                        "vamsSchema",
                        `reason-${modelSize.toLowerCase()}`
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
         * Per-Model Resources: Reason 2B
         */
        if (cosmosConfig.modelsReason?.reason2B?.enabled) {
            const reason2B = createModelResources(
                "reason2B",
                "2B",
                "nvidia-cosmos-reason2-2b",
                "NVIDIA Cosmos Reason 2B - Vision Language Model for video analysis and captioning",
                cosmosConfig.modelsReason.reason2B.autoRegisterWithVAMS === true,
                cosmosConfig.modelsReason.reason2B.autoTriggerOnFileExtensionsUpload || "",
                4,
                180000,
                48
            );

            this.pipelineReason2BVamsLambdaFunctionName = reason2B.vamsExecuteFunction.functionName;

            new CfnOutput(this, "CosmosReason2BLambdaFunctionName", {
                value: reason2B.vamsExecuteFunction.functionName,
                description:
                    "The Cosmos Reason 2B Pipeline Lambda Function Name to use in a VAMS Pipeline",
            });
        }

        /**
         * Per-Model Resources: Reason 8B
         */
        if (cosmosConfig.modelsReason?.reason8B?.enabled) {
            const reason8B = createModelResources(
                "reason8B",
                "8B",
                "nvidia-cosmos-reason2-8b",
                "NVIDIA Cosmos Reason 8B - Vision Language Model for video analysis and reasoning",
                cosmosConfig.modelsReason.reason8B.autoRegisterWithVAMS === true,
                cosmosConfig.modelsReason.reason8B.autoTriggerOnFileExtensionsUpload || "",
                4,
                180000,
                48
            );

            this.pipelineReason8BVamsLambdaFunctionName = reason8B.vamsExecuteFunction.functionName;

            new CfnOutput(this, "CosmosReason8BLambdaFunctionName", {
                value: reason8B.vamsExecuteFunction.functionName,
                description:
                    "The Cosmos Reason 8B Pipeline Lambda Function Name to use in a VAMS Pipeline",
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

        const nagReason =
            "Intended Solution. The Cosmos Reason pipeline lambda functions need appropriate access to S3 for reading asset files and model data.";

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: nagReason,
                    appliesTo: [
                        {
                            regex: "/^Resource::.*openPipeline/ServiceRole/.*/g",
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
                    reason: nagReason,
                    appliesTo: [
                        {
                            regex: "/^Resource::.*CosmosReason.*StateMachine/Role/.*/g",
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
                    reason: nagReason,
                    appliesTo: [
                        {
                            regex: "/^Resource::.*pipelineEnd/ServiceRole/.*/g",
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
                    reason: nagReason,
                    appliesTo: [
                        {
                            regex: "/^Resource::.*vamsExecuteCosmos.*Pipeline/ServiceRole/.*/g",
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
                    reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require access to objects in asset buckets and model cache bucket for Cosmos Reason inference",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            containerJobRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require access to objects in asset buckets, model cache, and EFS for Cosmos Reason model weights",
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
        if (cosmosConfig.modelsReason?.reason2B?.enabled) modelKeys.push("reason2B");
        if (cosmosConfig.modelsReason?.reason8B?.enabled) modelKeys.push("reason8B");

        for (const modelKey of modelKeys) {
            NagSuppressions.addResourceSuppressionsByPath(
                Stack.of(this),
                `/${this.toString()}/CosmosReason-${modelKey}-StateMachine/Role/DefaultPolicy/Resource`,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "Cosmos Reason pipeline state machine uses default policy that contains wildcards for batch job submission and lambda invocation",
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
