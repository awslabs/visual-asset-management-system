/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Duration, Stack, Names, NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import {
    buildConstructPipelineFunction,
    buildOpenPipelineFunction,
    buildVamsExecuteSplatToolboxPipelineFunction,
    buildPipelineEndFunction,
} from "../lambdaBuilder/splatToolboxFunctions";
import { BatchGpuPipelineConstruct } from "../../../constructs/batch-gpu-pipeline";
import * as ecr from "aws-cdk-lib/aws-ecr";
import { NagSuppressions } from "cdk-nag";
import { CfnOutput } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as ServiceHelper from "../../../../../helper/service-helper";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import { Service } from "../../../../../helper/service-helper";
import * as Config from "../../../../../../config/config";
import { generateUniqueNameHash } from "../../../../../helper/security";
import { kmsKeyPolicyStatementGenerator } from "../../../../../helper/security";
import { grantExternalAssetBucketKmsKeys } from "../../../../../helper/security";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";
import { execFileSync } from "child_process";
import * as fs from "fs";
import * as os from "os";

export interface SplatToolboxConstructProps extends cdk.StackProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowV2FunctionName: string;
    codeBuildRepository?: ecr.IRepository;
}

/**
 * Default input properties
 */
const defaultProps: Partial<SplatToolboxConstructProps> = {
    //stackName: "",
    //env: {},
};

export class SplatToolboxConstruct extends Construct {
    public pipelineVamsLambdaFunctionName: string;

    /** Upstream container sources, pinned to a commit. */
    public static readonly GITHUB_REPO_LINK =
        "https://github.com/aws-solutions-library-samples/guidance-for-open-source-3d-reconstruction-toolbox-for-gaussian-splats-on-aws.git";
    public static readonly GITHUB_REPO_COMMIT_HASH = "73133959c04fb0f9f002e95b4d2a722de2d18722";

    /** Marker file recording which upstream commit the local container directory was synced from. */
    private static readonly SYNCED_COMMIT_FILE = ".synced-commit";

    constructor(parent: Construct, name: string, props: SplatToolboxConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;

        // Download and Sync splat toolbox repository container files
        SplatToolboxConstruct.syncContainerSources(
            SplatToolboxConstruct.GITHUB_REPO_LINK,
            SplatToolboxConstruct.GITHUB_REPO_COMMIT_HASH
        );

        /**
         * Batch Resources
         */
        const inputBucketPolicy = new iam.PolicyDocument({
            statements: [
                // Add permissions for all asset buckets from the global array
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

        //Add KMS key use if provided
        if (props.storageResources.encryption.kmsKey) {
            inputBucketPolicy.addStatements(
                kmsKeyPolicyStatementGenerator(props.storageResources.encryption.kmsKey)
            );

            outputBucketPolicy.addStatements(
                kmsKeyPolicyStatementGenerator(props.storageResources.encryption.kmsKey)
            );
        }

        const stateTaskPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
                    resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
                }),
            ],
        });

        const containerExecutionRole = new iam.Role(this, "SplatToolboxContainerExecutionRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            inlinePolicies: {
                InputBucketPolicy: inputBucketPolicy,
                OutputBucketPolicy: outputBucketPolicy,
                StateTaskPolicy: stateTaskPolicy,
            },
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
            ],
        });

        const containerJobRole = new iam.Role(this, "SplatToolboxContainerJobRole", {
            assumedBy: new iam.CompositePrincipal(
                Service("ECS_TASKS").Principal,
                Service("SAGEMAKER").Principal
            ),
            inlinePolicies: {
                InputBucketPolicy: inputBucketPolicy,
                OutputBucketPolicy: outputBucketPolicy,
                StateTaskPolicy: stateTaskPolicy,
            },
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonSageMakerFullAccess"),
            ],
        });

        // Grant access to any external asset bucket customer managed KMS keys so the
        // container can read/write objects in cross-account encrypted buckets
        // (no-op when no external keys are configured)
        grantExternalAssetBucketKmsKeys(containerJobRole);

        /**
         * AWS Batch Job Definition & Compute Env for Splat Toolbox Container
         */
        const splatToolboxBatchPipeline = new BatchGpuPipelineConstruct(
            this,
            "BatchPipeline_SplatToolbox",
            {
                vpc: props.vpc,
                subnets: props.pipelineSubnets,
                securityGroups: props.pipelineSecurityGroups,
                jobRole: containerJobRole,
                executionRole: containerExecutionRole,
                imageAssetPath: path.join(
                    "..",
                    "..",
                    "..",
                    "..",
                    "..",
                    "backendPipelines",
                    "3dRecon",
                    "splatToolbox",
                    "container"
                ),
                dockerfileName: "Dockerfile",
                codeBuildRepository: props.codeBuildRepository,
                containerExecutionCommand: ["python", "__main__.py"],
                batchJobDefinitionName: `SplatToolboxGpuJob-${
                    props.config.name + "_" + props.config.app.baseStackName
                }`,

                // Enable GPU-optimized settings for Splat Toolbox
                enableGpuDeviceMappings: true,
                enableSharedMemory: true,
                enableUlimits: true,
                enableWorkspaceVolume: true,
                enablePrivilegedMode: true,
                vcpus: 16,
                memory: 60000,
                retryAttempts: 1,
                timeoutSeconds: 259200, // 72 hours
                // Runtime variables are passed via Step Functions containerOverrides
                additionalEnvironmentVariables: [],
            }
        );

        /**
         * SFN States
         */

        // connect pipeline lambda function
        // transforms data input for AWS Batch
        const constructPipelineFunction = buildConstructPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            props.storageResources.encryption.kmsKey
        );

        // creates pipeline definition based on event notification input
        const constructPipelineTask = new tasks.LambdaInvoke(this, "ConstructPipelineTask", {
            lambdaFunction: constructPipelineFunction,
            outputPath: "$.Payload",
        });

        // end state: success
        const successState = new sfn.Succeed(this, "SuccessState", {
            comment: "Pipeline returned SUCCESS",
        });

        // end state: failure
        const failState = new sfn.Fail(this, "FailState", {
            causePath: sfn.JsonPath.stringAt("$.error.Cause"),
            errorPath: sfn.JsonPath.stringAt("$.error.Error"),
        });

        // end state evaluation: success or failure
        const endStatesChoice = new sfn.Choice(this, "EndStatesChoice")
            .when(sfn.Condition.isPresent("$.error"), failState)
            .otherwise(successState);

        // final lambda called on pipeline end to close out the statemachine run
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

        const pipeLineEndTask = new tasks.LambdaInvoke(this, "PipelineEndTask", {
            lambdaFunction: pipelineEndFunction,
            inputPath: "$",
            outputPath: "$.Payload",
        }).next(endStatesChoice);

        // error handler passthrough - Batch
        const handleSplatBatchError = new sfn.Pass(this, "HandleSplatBatchError", {
            resultPath: "$",
        }).next(pipeLineEndTask);

        // batch job Splat Toolbox
        const splatToolboxBatchJob = new tasks.BatchSubmitJob(this, "SplatToolboxBatchJob", {
            jobName: sfn.JsonPath.stringAt("$.jobName"),
            jobDefinitionArn: splatToolboxBatchPipeline.batchJobDefinition.attrJobDefinitionArn,
            jobQueueArn: splatToolboxBatchPipeline.batchJobQueue.ref,
            containerOverrides: {
                command: [...sfn.JsonPath.listAt("$.definition")],
                environment: {
                    EXTERNAL_SFN_TASK_TOKEN: sfn.JsonPath.stringAt("$.externalSfnTaskToken"),
                    AWS_REGION: region,
                },
            },
            integrationPattern: sfn.IntegrationPattern.RUN_JOB,
        })
            .addCatch(handleSplatBatchError, {
                resultPath: "$.error",
            })
            .next(successState);

        /**
         * SFN Definition
         */
        const sfnPipelineDefinition = sfn.Chain.start(
            constructPipelineTask.next(splatToolboxBatchJob)
        );

        /**
         * CloudWatch Log Group
         */
        const stateMachineLogGroup = new logs.LogGroup(
            this,
            "SplatToolboxProcessing-StateMachineLogGroup",
            {
                logGroupName:
                    "/aws/vendedlogs/VAMSstateMachine-SplatToolboxPipeline" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "SplatToolboxProcessing-StateMachineLogGroup",
                        10
                    ),
                retention: logs.RetentionDays.TEN_YEARS,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            }
        );

        /**
         * SFN State Machine
         */
        const pipelineStateMachine = new sfn.StateMachine(
            this,
            "SplatToolboxProcessing-StateMachine",
            {
                definitionBody: sfn.DefinitionBody.fromChainable(sfnPipelineDefinition),
                // Envelopes the Batch attempt (timeoutSeconds 259200) so a long-running job reaches
                // its own failure path — and the container's task-token callback — rather than being
                // cut short by the state machine.
                timeout: Duration.hours(73),
                logs: {
                    destination: stateMachineLogGroup,
                    includeExecutionData: true,
                    level: sfn.LogLevel.ALL,
                },
                tracingEnabled: true,
            }
        );

        // Stopping the state machine cancels the .sync Batch task, which requires terminating the
        // running job; the BatchSubmitJob task grants only batch:SubmitJob. Batch job ids are
        // generated at submit time and carry no deployment-specific prefix to scope on, so the
        // resource is a wildcard.
        pipelineStateMachine.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["batch:DescribeJobs", "batch:TerminateJob"],
                resources: ["*"],
            })
        );

        /**
         * Lambda Resources & SNS Subscriptions
         */

        //Build Lambda Web Visualizer Pipeline Resources to Open the Pipeline through a SNS Topic Subscription
        const allowedInputFileExtensions = ".zip,.mp4,.mov";
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
            props.storageResources.encryption.kmsKey
        );

        //Build Lambda VAMS Execution Function (as an optional pipeline execution action)
        const SplatToolboxPipelineVamsExecuteFunction =
            buildVamsExecuteSplatToolboxPipelineFunction(
                this,
                props.lambdaCommonBaseLayer,
                props.storageResources.s3.assetAuxiliaryBucket,
                openPipelineFunction,
                props.config,
                props.vpc,
                props.pipelineSubnets,
                props.storageResources.encryption.kmsKey
            );

        // Auto-register with VAMS (V2 vamsSchema bundle -> V2 pipeline/workflow/template tables). The
        // former Objects and 360-Environments registrations collapse to ONE pipeline + two templates
        // (splat-objects / splat-environments-360) selected per execution; the pipeline uses the longer
        // (48h) task timeout so either capture mode fits.
        if (props.config.app.pipelines.useSplatToolbox.autoRegisterWithVAMS === true) {
            new VamsSchemaRegistration(this, "SplatToolboxRegistration", {
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
                    "3dRecon",
                    "splatToolbox",
                    "vamsSchema"
                ),
                resourceOverrides: {
                    lambdaName: SplatToolboxPipelineVamsExecuteFunction.functionName,
                },
                idOverrides: {
                    pipelineId: "3dRecon-splat-toolbox",
                    workflowId: "3dRecon-splat-toolbox",
                },
            });
        }

        //Output VAMS Pipeline Execution Function name
        new CfnOutput(this, "SplatToolboxLambdaExecutionFunctionName", {
            value: SplatToolboxPipelineVamsExecuteFunction.functionName,
            description:
                "The Splat Toolbox Pipeline Lambda Function Name to use in a VAMS Pipeline",
            exportName: "SplatToolboxLambdaExecutionFunctionName",
        });
        this.pipelineVamsLambdaFunctionName = SplatToolboxPipelineVamsExecuteFunction.functionName;

        //Nag Supressions
        const reason =
            "Intended Solution. The pipeline lambda functions need appropriate access to S3.";

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
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
                            // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
                            regex: "^Resource::.*SplatToolboxProcessing-StateMachine/Role/.*/g",
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
                            // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
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
                            // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
                            regex: "^Resource::.*vamsExecuteSplatToolboxPipeline/ServiceRole/.*/g",
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
                    reason: "The IAM role for ECS Container execution uses AWS Managed Policies",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require access to objects in the DataBucket",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            containerJobRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The IAM role for ECS Container execution uses AWS Managed Policies",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require access to objects in the DataBucket",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/SplatToolboxProcessing-StateMachine/Role/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "PipelineProcessingStateMachine uses default policy that contains wildcard",
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

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/openPipeline/ServiceRole/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "openPipeline requires AWS Managed Policies, AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "openPipeline uses default policy that contains wildcard",
                    appliesTo: ["Resource::*"],
                },
            ],
            true
        );

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
                    reason: "openPipeline uses default policy that contains wildcard",
                    appliesTo: ["Resource::*"],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/vamsExecuteSplatToolboxPipeline/ServiceRole/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "vamsExecuteSplatToolboxPipeline requires AWS Managed Policies, AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "vamsExecuteSplatToolboxPipeline uses default policy that contains wildcard",
                    appliesTo: ["Resource::*"],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "vamsExecuteSplatToolboxPipeline uses default policy that contains wildcard",
                    appliesTo: [
                        "Action::kms:GenerateDataKey*",
                        {
                            regex: "/^Resource::<.*Function.*.Arn>:.*$/g",
                        },
                    ],
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

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/BatchPipeline_SplatToolbox/BatchServiceRole/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The IAM role for AWS Batch Service uses AWSBatchServiceRole managed policy which is required for batch operations",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSBatchServiceRole",
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/BatchPipeline_SplatToolbox/BatchInstanceRole/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The ECS Instance Role for EC2 Batch Compute Environment requires AmazonEC2ContainerServiceforEC2Role managed policy",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
                    ],
                },
            ],
            true
        );
    }

    /**
     * Sync the pinned upstream Splat Toolbox container sources into backendPipelines. Static and
     * idempotent so the CodeBuild construct can guarantee the sync has run before it uploads the
     * container directory as an S3 asset, without depending on construct instantiation order.
     */
    public static syncContainerSources(gitHubLink: string, gitHubCommitHash: string): void {
        if (SplatToolboxConstruct.syncedCommit === gitHubCommitHash) {
            return;
        }
        SplatToolboxConstruct.syncSplatToolboxContainerSources(gitHubLink, gitHubCommitHash);
        SplatToolboxConstruct.syncedCommit = gitHubCommitHash;
    }

    /** Commit synced during this synth; keyed by hash so a changed pin always re-syncs. */
    private static syncedCommit: string | undefined;

    private static syncSplatToolboxContainerSources(
        gitHubLink: string,
        gitHubCommitHash: string
    ): void {
        try {
            const targetDir = path.resolve(
                __dirname,
                "..",
                "..",
                "..",
                "..",
                "..",
                "..",
                "..",
                "backendPipelines",
                "3dRecon",
                "splatToolbox",
                "container"
            );

            // Always re-download to ensure the container matches the target commit hash.
            console.log(
                `Downloading/Syncing Splat Toolbox repository (commit: ${gitHubCommitHash})...`
            );
            const tempDir = path.join(os.tmpdir(), "splat-toolbox-repo");

            if (fs.existsSync(tempDir)) {
                fs.rmSync(tempDir, { recursive: true, force: true });
            }

            // execFileSync with an argument array: no shell, so the repo URL and commit hash are
            // passed to git verbatim rather than interpolated into a command string.
            // nosemgrep: detect-child-process
            execFileSync("git", ["clone", gitHubLink, tempDir], { stdio: "inherit" });
            // nosemgrep: detect-child-process
            execFileSync("git", ["-C", tempDir, "checkout", gitHubCommitHash], {
                stdio: "inherit",
            });

            // Confirm the working tree is actually at the requested commit. A tag or branch name
            // that moved, or a partial clone, would otherwise sync unexpected sources.
            // nosemgrep: detect-child-process
            const checkedOut = execFileSync("git", ["-C", tempDir, "rev-parse", "HEAD"], {
                encoding: "utf8",
            }).trim();
            if (checkedOut !== gitHubCommitHash) {
                throw new Error(
                    `checked-out commit ${checkedOut} does not match the requested ${gitHubCommitHash}`
                );
            }

            const sourceDir = path.join(tempDir, "source", "container");
            if (!fs.existsSync(sourceDir)) {
                throw new Error(`upstream source directory not found at ${sourceDir}`);
            }
            if (fs.existsSync(sourceDir)) {
                // Overwrite/sync files from downloaded source into target directory.
                // Existing local files (e.g. __main__.py, .gitignore) are preserved;
                // only files present in the source are written/overwritten.
                if (!fs.existsSync(targetDir)) {
                    fs.mkdirSync(targetDir, { recursive: true });
                }
                console.log(`Syncing from ${sourceDir} to ${targetDir}`);
                const copyRecursive = (src: string, dest: string) => {
                    const stats = fs.statSync(src);
                    if (stats.isDirectory()) {
                        if (!fs.existsSync(dest)) {
                            fs.mkdirSync(dest);
                        }
                        const files = fs.readdirSync(src);
                        for (const file of files) {
                            copyRecursive(path.join(src, file), path.join(dest, file));
                        }
                    } else {
                        fs.copyFileSync(src, dest);
                    }
                };
                const files = fs.readdirSync(sourceDir);
                for (const file of files) {
                    copyRecursive(path.join(sourceDir, file), path.join(targetDir, file));
                }

                // Modify Dockerfile to add __main__.py copy
                const dockerfilePath = path.join(targetDir, "Dockerfile");
                if (fs.existsSync(dockerfilePath)) {
                    let dockerfileContent = fs.readFileSync(dockerfilePath, "utf8");

                    // Stage the VAMS entry point and its support package. The package is named
                    // vams_utils, not utils: upstream copies its own ./src/pipeline/utils.py into
                    // CODE_PATH, and the two cannot share the `utils` import name — whichever wins
                    // breaks the other's imports.
                    if (!dockerfileContent.includes("COPY ./__main__.py")) {
                        // Anchor on the COPY that stages ./src/main.py into CODE_PATH. Upstream
                        // bundles several files onto that one COPY line, so match the line
                        // containing ./src/main.py rather than a COPY that starts with it.
                        const mainPyCopyLine = /^.*COPY\b.*\.\/src\/main\.py.*$/m;
                        if (!mainPyCopyLine.test(dockerfileContent)) {
                            throw new Error(
                                "could not locate the 'COPY ... ./src/main.py' line in the upstream " +
                                    "Dockerfile to anchor the __main__.py COPY. The container entry " +
                                    "point (python __main__.py) would be missing from the image."
                            );
                        }
                        dockerfileContent = dockerfileContent.replace(
                            mainPyCopyLine,
                            (line) =>
                                `COPY ./__main__.py                                                  \${CODE_PATH}\n` +
                                `${line}\n` +
                                `COPY ./vams_utils                                                   \${CODE_PATH}/vams_utils`
                        );
                        fs.writeFileSync(dockerfilePath, dockerfileContent);
                        console.log("Added __main__.py and the vams_utils package to Dockerfile");
                    }
                }

                // The Batch job runs `python __main__.py`, which imports `from vams_utils import
                // manifest_io`. Assert on the final file rather than trusting the edits above.
                const finalDockerfile = fs.readFileSync(dockerfilePath, "utf8");
                for (const required of ["COPY ./__main__.py", "COPY ./vams_utils"]) {
                    if (!finalDockerfile.includes(required)) {
                        throw new Error(
                            `the synced Dockerfile is missing '${required}'; the container entry ` +
                                "point would fail to import at runtime"
                        );
                    }
                }
                for (const required of ["__main__.py", "vams_utils"]) {
                    if (!fs.existsSync(path.join(targetDir, required))) {
                        throw new Error(`${required} is missing from ${targetDir}`);
                    }
                }
            }

            fs.rmSync(tempDir, { recursive: true, force: true });

            // Record the commit the local container directory now holds, so a later synth can tell
            // whether the sources match the pinned hash.
            fs.writeFileSync(
                path.join(targetDir, SplatToolboxConstruct.SYNCED_COMMIT_FILE),
                `${gitHubCommitHash}\n`,
                "utf8"
            );
            console.log(`Repository sync completed successfully (commit: ${gitHubCommitHash})`);
        } catch (error) {
            // Fail the synth rather than silently building from whatever is on disk. A stale local
            // container directory would otherwise produce an image that does not match the pinned
            // commit, with no signal that the sync never ran.
            throw new Error(
                `Splat Toolbox container source sync failed for commit ${gitHubCommitHash}. ` +
                    `The local container directory may be stale or partially written; delete ` +
                    `backendPipelines/3dRecon/splatToolbox/container and re-run. Cause: ${error}`
            );
        }
    }
}
