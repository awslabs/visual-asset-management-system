/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import { Construct } from "constructs";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import { Stack } from "aws-cdk-lib";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import * as Config from "../../../../../../config/config";
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { Service } from "../../../../../helper/service-helper";
import { BatchFargatePipelineConstruct } from "../../../constructs/batch-fargate-pipeline";
import {
    NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
    generateUniqueNameHash,
    grantExternalAssetBucketKmsKeys,
    kmsKeyPolicyStatementGenerator,
    suppressCdkNagEcrAuthTokenWildcard,
} from "../../../../../helper/security";
import {
    buildConstructPipelineFunction,
    buildOpenPipelineFunction,
    buildPipelineEndFunction,
    buildVamsExecuteVideoSopBomFunction,
    resolveVideoSopBomBedrockModelId,
} from "../lambdaBuilder/videoSopBomFunctions";
import { VideoSopBomCodeBuildConstruct } from "./videoSopBomCodeBuild-construct";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";
import path = require("path");

export interface VideoSopBomConstructProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    assetAuxiliaryBucket: s3.IBucket;
    storageResources: storageResources;
    kmsKey?: kms.IKey;
    importGlobalPipelineWorkflowV2FunctionName: string;
}

/**
 * The marker the container places in its SendTaskSuccess payload. pipelineEnd requires the same string
 * before it releases the external token as a success, and the sub-state-machine's end Choice requires
 * it before it records SUCCEEDED.
 */
const REPORTER_MARKER = "video_sop_bom_pipeline";

/** The record's key prefix with a trailing slash and no leading one, so `${bucketArn}/${prefix}…` joins cleanly. */
const objectPrefixOf = (record: s3AssetBuckets.S3AssetBucketRecord): string => {
    const prefix = record.prefix || "/";
    const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";
    return normalizedPrefix.replace(/^\/+/, "");
};

export class VideoSopBomConstruct extends Construct {
    public readonly pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: VideoSopBomConstructProps) {
        super(parent, name);

        const region = cdk.Stack.of(this).region;
        const account = cdk.Stack.of(this).account;
        const pipelineConfig = props.config.app.pipelines.useGenAiVideoSopBom;

        // Input videos may live in any registered asset bucket; a versioned GetObject needs
        // GetObjectVersion as well.
        const inputBucketPolicy = new iam.PolicyDocument({
            statements: s3AssetBuckets.getS3AssetBucketRecords().map(
                (record) =>
                    new iam.PolicyStatement({
                        effect: iam.Effect.ALLOW,
                        actions: [
                            "s3:GetObject",
                            "s3:GetObjectVersion",
                            "s3:ListBucket",
                            "s3:GetBucketLocation",
                        ],
                        resources: [
                            record.bucket.bucketArn,
                            `${record.bucket.bucketArn}/${objectPrefixOf(record)}*`,
                        ],
                    })
            ),
        });

        // Outputs land only in the run bucket -- the default asset bucket record -- under its
        // pipelines/ prefix, where the workflow's process-output step reads them. ListBucket on that
        // bucket is already granted by the input policy above.
        const runBucketRecord = s3AssetBuckets
            .getS3AssetBucketRecords()
            .find((record) => record.isDefault);
        if (!runBucketRecord) {
            throw new Error(
                "VideoSopBomConstruct: no default asset bucket is registered, so the run bucket the " +
                    "container writes to cannot be resolved."
            );
        }
        const runBucketPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["s3:GetObject", "s3:PutObject"],
                    resources: [
                        `${runBucketRecord.bucket.bucketArn}/${objectPrefixOf(
                            runBucketRecord
                        )}pipelines/*`,
                    ],
                }),
            ],
        });

        // Working objects (definition document, FLAC audio, Transcribe output, analysis artefacts) live
        // under the auxiliary bucket's pipelines/ prefix; the container deletes them as it goes.
        const auxBucketPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:ListBucket",
                        "s3:GetBucketLocation",
                    ],
                    resources: [
                        props.assetAuxiliaryBucket.bucketArn,
                        props.assetAuxiliaryBucket.bucketArn + "/*",
                    ],
                }),
            ],
        });

        // The container's final act completes the .sync task on its inner token. No heartbeat: the
        // container never sends one, so the timeout chain (attempt < state machine < bundle) is the bound.
        const stateTaskPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
                    resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
                }),
            ],
        });

        // The foundation-model id underneath any cross-Region inference-profile prefix. The Region
        // segment is a wildcard because geo and global inference profiles route a request to
        // destination Regions the caller cannot enumerate; the model id itself stays exact. The
        // inference-profile ARN names the configured id as-is: with a cross-Region prefix it is the
        // profile the container invokes, and a bare foundation-model id names no profile at all.
        const bedrockModelId = resolveVideoSopBomBedrockModelId(props.config);
        const bedrockFoundationModel = bedrockModelId.replace(
            /^(global|us-gov|us|eu|apac|au|jp)\./,
            ""
        );
        const bedrockPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                    resources: [
                        `arn:${ServiceHelper.Partition()}:bedrock:*::foundation-model/${bedrockFoundationModel}`,
                        `arn:${ServiceHelper.Partition()}:bedrock:::foundation-model/${bedrockFoundationModel}`,
                        `arn:${ServiceHelper.Partition()}:bedrock:${region}:${account}:inference-profile/${bedrockModelId}`,
                    ],
                }),
            ],
        });

        // StartTranscriptionJob has no resource type; the request-level condition keys pin the output
        // bucket, and the CMK when one is configured (StringEquals, so a request that omits the key is
        // denied rather than written unencrypted). Get/Delete are scoped to this pipeline's job-name prefix.
        const transcribeStartConditions: { [key: string]: string } = {
            "transcribe:OutputBucketName": props.assetAuxiliaryBucket.bucketName,
        };
        if (props.kmsKey) {
            transcribeStartConditions["transcribe:OutputEncryptionKMSKeyId"] = props.kmsKey.keyArn;
        }
        const transcribePolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["transcribe:StartTranscriptionJob"],
                    resources: ["*"],
                    conditions: { StringEquals: transcribeStartConditions },
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: [
                        "transcribe:GetTranscriptionJob",
                        "transcribe:DeleteTranscriptionJob",
                    ],
                    resources: [
                        `arn:${ServiceHelper.Partition()}:transcribe:${region}:${account}:transcription-job/vams-video-sop-bom-*`,
                    ],
                }),
            ],
        });

        const jobRoleInlinePolicies: { [policyName: string]: iam.PolicyDocument } = {
            InputBucketPolicy: inputBucketPolicy,
            RunBucketPolicy: runBucketPolicy,
            AuxBucketPolicy: auxBucketPolicy,
            StateTaskPolicy: stateTaskPolicy,
            BedrockPolicy: bedrockPolicy,
            TranscribePolicy: transcribePolicy,
        };
        // One key statement for every KMS-encrypted object the container touches (asset, run and
        // auxiliary buckets, Transcribe's output write).
        if (props.kmsKey) {
            jobRoleInlinePolicies.KmsKeyPolicy = new iam.PolicyDocument({
                statements: [kmsKeyPolicyStatementGenerator(props.kmsKey)],
            });
        }

        // Container EXECUTION role: used by the ECS agent, not by the container's own process. It pulls
        // the image and writes the task's log stream, and that is all the managed policies grant.
        const containerExecutionRole = new iam.Role(this, "VideoSopBomContainerExecutionRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
            ],
        });

        // Container JOB role: the credentials reachable from inside a container that decodes untrusted
        // media, so it carries the inline statements above and nothing else.
        const containerJobRole = new iam.Role(this, "VideoSopBomContainerJobRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            inlinePolicies: jobRoleInlinePolicies,
        });

        // Customer managed keys of externally registered asset buckets (no-op when there are none).
        grantExternalAssetBucketKmsKeys(containerJobRole);

        // The container's stdout/stderr. A named vended group under /aws/vendedlogs/Pipelines/, which
        // the execution-service Lambdas already hold read grants for, so the execution log viewer can
        // show the container stream; KMS-encrypted and retained for a year, unlike Batch's default group.
        const containerLogGroup = new logs.LogGroup(
            this,
            "VideoSopBomProcessing-ContainerLogGroup",
            {
                logGroupName:
                    "/aws/vendedlogs/Pipelines/VideoSopBom" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "VideoSopBomProcessing-ContainerLogGroup",
                        10
                    ),
                encryptionKey: props.kmsKey,
                retention: logs.RetentionDays.ONE_YEAR,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            }
        );

        // CodeBuild-based container build (when useCodeBuild is true)
        let codeBuildConstruct: VideoSopBomCodeBuildConstruct | undefined;
        if (pipelineConfig?.useCodeBuild === true) {
            codeBuildConstruct = new VideoSopBomCodeBuildConstruct(this, "VideoSopBomCodeBuild", {
                config: props.config,
                vpc: props.vpc,
                pipelineSubnets: props.pipelineSubnets,
                pipelineSecurityGroups: props.pipelineSecurityGroups,
                kmsKey: props.kmsKey,
            });

            new cdk.CfnOutput(this, "VideoSopBomCodeBuildProject", {
                value: codeBuildConstruct.codeBuildProjectName,
                description:
                    "CodeBuild project name for the Video SOP/BOM Extraction container. Check build status: aws codebuild list-builds-for-project --project-name <value>",
            });
        }

        // Batch Fargate pipeline
        const batchPipeline = new BatchFargatePipelineConstruct(
            this,
            "BatchFargatePipeline_VideoSopBom",
            {
                // AWS Batch's own bound on one attempt, measured from the attempt's start. The state
                // machine's 8-hour timeout below envelopes it; the 2-hour gap absorbs a RUNNABLE wait
                // behind the Fargate On-Demand vCPU quota, during which the execution clock runs and
                // the attempt clock does not.
                attemptDuration: cdk.Duration.hours(6),
                config: props.config,
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
                    "genAi",
                    "videoSopBom",
                    "container"
                ),
                dockerfileName: "Dockerfile",
                batchJobDefinitionName:
                    "VideoSopBomJob_" + props.config.name + "_" + props.config.app.baseStackName,
                // ffmpeg, Transcribe polling and Bedrock calls are I/O-bound: 4 vCPU / 16 GiB.
                cpu: 4,
                memoryMiB: 16384,
                // The volume holds the downloaded inputs (bounded by the total byte cap), their
                // extracted audio and the key frames; the config test pins
                // ceil(cap x 1.5 / 1024) + 2 <= this figure.
                ephemeralStorageGiB: Config.VIDEO_SOP_BOM_EPHEMERAL_STORAGE_GIB,
                logGroup: containerLogGroup,
                ecrImage: codeBuildConstruct
                    ? {
                          repository: codeBuildConstruct.repository,
                          tag: codeBuildConstruct.imageTag,
                      }
                    : undefined,
            }
        );

        // Binding the container image grants the execution role the repository pull, and with it
        // ecr:GetAuthorizationToken on `*` — the one wildcard its default policy carries. That policy
        // exists only from this point, so the suppression is stamped here rather than beside the role.
        suppressCdkNagEcrAuthTokenWildcard(containerExecutionRole);

        // Lambda functions
        const constructPipelineFunction = buildConstructPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.kmsKey
        );

        const pipelineEndFunction = buildPipelineEndFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.config,
            props.vpc,
            props.pipelineSubnets
        );

        // Step Functions state machine
        const constructPipelineTask = new tasks.LambdaInvoke(this, "ConstructPipelineTask", {
            lambdaFunction: constructPipelineFunction,
            outputPath: "$.Payload",
        });

        // pipelineEnd returns its input event unchanged, so `$.error` survives the outputPath
        // replacement and the Choice below can still see it.
        const pipelineEndTask = new tasks.LambdaInvoke(this, "PipelineEndTask", {
            lambdaFunction: pipelineEndFunction,
            inputPath: "$",
            outputPath: "$.Payload",
        });

        const successState = new sfn.Succeed(this, "PipelineSuccess");
        const failState = new sfn.Fail(this, "PipelineFailed", {
            cause: "Pipeline processing failed",
            error: "See CloudWatch logs for details",
        });

        // Fails on a caught error, and on a callback payload without the container's reporter marker:
        // pipelineEnd fails the external token for that payload but returns the event without `$.error`,
        // so the marker is checked here too or the sub-execution would record SUCCEEDED for a run the
        // parent workflow was told had failed. The StringEquals arm is guarded by its own IsPresent
        // because a comparison against an absent path is a States.Runtime error, which no Catch sees.
        const reporterPath = "$.batchResult.reporter";
        const endStatesChoice = new sfn.Choice(this, "EndStatesChoice")
            .when(
                sfn.Condition.or(
                    sfn.Condition.isPresent("$.error"),
                    sfn.Condition.isNotPresent(reporterPath),
                    sfn.Condition.and(
                        sfn.Condition.isPresent(reporterPath),
                        sfn.Condition.not(sfn.Condition.stringEquals(reporterPath, REPORTER_MARKER))
                    )
                ),
                failState
            )
            .otherwise(successState);

        pipelineEndTask.next(endStatesChoice);

        const handleBatchError = new sfn.Pass(this, "HandleBatchError", {
            resultPath: "$",
        }).next(pipelineEndTask);

        // ConstructPipelineTask is the first state, so a failure there ends the execution before
        // PipelineEndTask runs -- and PipelineEndTask is the only state that reports on the parent
        // workflow's callback token, which then pends for its full taskTimeout. The handler reports the
        // token for the errors it raises itself; this covers the failures where it never runs at all:
        // the function timeout, an out-of-memory kill, an import failure, or an invoke fault that
        // exhausts the task's service-exception retries.
        const handleConstructPipelineError = new sfn.Pass(this, "HandleConstructPipelineError", {
            resultPath: "$",
        }).next(pipelineEndTask);

        // resultPath keeps the state and appends the error, so pipelineEnd still finds
        // externalSfnTaskToken alongside it.
        constructPipelineTask.addCatch(handleConstructPipelineError, {
            resultPath: "$.error",
        });

        // A .sync submission: Step Functions owns the job, and the container completes the task by
        // calling SendTaskSuccess/SendTaskFailure on TASK_TOKEN as its final act. No task timeout prop —
        // on a BatchSubmitJob it renders as a SubmitJob attempt-duration override, not a task clock, so
        // the state machine timeout below is the Step Functions bound. resultPath keeps
        // externalSfnTaskToken and batchJobName beside the callback output for pipelineEnd.
        const videoSopBomBatchJob = new tasks.BatchSubmitJob(this, "VideoSopBomBatchJob", {
            jobName: sfn.JsonPath.stringAt("$.batchJobName"),
            jobDefinitionArn: batchPipeline.batchJobDefinition.jobDefinitionArn,
            jobQueueArn: batchPipeline.batchJobQueue.jobQueueArn,
            containerOverrides: {
                command: [...sfn.JsonPath.listAt("$.definitionCommand")],
                environment: {
                    TASK_TOKEN: sfn.JsonPath.taskToken,
                    AWS_REGION: region,
                },
            },
            integrationPattern: sfn.IntegrationPattern.RUN_JOB,
            resultPath: "$.batchResult",
        })
            .addCatch(handleBatchError, {
                resultPath: "$.error",
            })
            .next(pipelineEndTask);

        const definition = constructPipelineTask.next(videoSopBomBatchJob);

        const stateMachineLogGroup = new logs.LogGroup(
            this,
            "VideoSopBomProcessing-StateMachineLogGroup",
            {
                logGroupName:
                    "/aws/vendedlogs/VAMSStateMachine-VideoSopBom" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "VideoSopBomProcessing-StateMachineLogGroup",
                        10
                    ),
                encryptionKey: props.kmsKey,
                retention: logs.RetentionDays.ONE_YEAR,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            }
        );

        const stateMachine = new sfn.StateMachine(this, "VideoSopBomProcessing-StateMachine", {
            definitionBody: sfn.DefinitionBody.fromChainable(definition),
            // Envelopes the 6-hour Batch attempt so an overrunning container is ended by Batch and
            // reaches pipelineEnd through the task's Catch -- which releases the external task token --
            // rather than being cut short by an execution-level States.Timeout.
            timeout: cdk.Duration.hours(8),
            logs: {
                destination: stateMachineLogGroup,
                includeExecutionData: true,
                level: sfn.LogLevel.ALL,
            },
            tracingEnabled: true,
        });

        // Stopping the state machine cancels the .sync Batch task, which requires terminating the
        // running job; the BatchSubmitJob task grants only batch:SubmitJob. DescribeJobs has no resource
        // type; job ids are generated at submit time, so TerminateJob is scoped to this account's jobs.
        stateMachine.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["batch:DescribeJobs"],
                resources: ["*"],
            })
        );
        stateMachine.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["batch:TerminateJob"],
                resources: [`arn:${ServiceHelper.Partition()}:batch:${region}:${account}:job/*`],
            })
        );

        // Open pipeline Lambda. The allow list also appears in the bundle's inputFileFilters and as the
        // handler's default; the extension-gates harness reads this literal.
        const allowedInputFileExtensions = ".mp4,.mov,.m4v,.webm,.mkv";
        const openPipelineFunction = buildOpenPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            stateMachine,
            allowedInputFileExtensions,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.storageResources.eventBridge.orchestrationBus,
            stateMachineLogGroup
        );

        // VAMS execute Lambda
        const vamsExecuteFunction = buildVamsExecuteVideoSopBomFunction(
            this,
            props.lambdaCommonBaseLayer,
            openPipelineFunction,
            allowedInputFileExtensions,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.kmsKey
        );

        this.pipelineVamsLambdaFunctionName = vamsExecuteFunction.functionName;

        // Auto-register with VAMS (V2 vamsSchema bundle -> V2 pipeline/workflow/template tables). The
        // bundle ships no trigger, so there is no deploy-time trigger enable to wire.
        if (pipelineConfig?.autoRegisterWithVAMS === true) {
            new VamsSchemaRegistration(this, "VideoSopBomRegistration", {
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
                    "genAi",
                    "videoSopBom",
                    "vamsSchema"
                ),
                resourceOverrides: { lambdaName: vamsExecuteFunction.functionName },
                idOverrides: {
                    pipelineId: "genai-video-sop-bom",
                    workflowId: "genai-video-sop-bom",
                },
            });
        }

        // CDK Nag suppressions. Every IAM4 entry names its managed policy and every IAM5 entry the one
        // wildcard shape it covers.
        NagSuppressions.addResourceSuppressions(
            containerExecutionRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/AWSXrayWriteOnlyAccess",
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            containerJobRole,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "transcribe:StartTranscriptionJob has no resource type; the request is pinned to " +
                        "the auxiliary bucket, and to the CMK when one is configured, by its condition keys.",
                    appliesTo: [{ regex: "/^Resource::\\*$/g" }],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "Geo and global Bedrock inference profiles route a request to destination Regions " +
                        "the caller cannot enumerate, so the foundation-model ARN carries a Region " +
                        "wildcard; the model id in it is exact.",
                    appliesTo: [
                        { regex: "/^Resource::arn:.*:bedrock:\\*::foundation-model/.*$/g" },
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "Transcription job names carry a per-run random suffix after the fixed " +
                        "vams-video-sop-bom- prefix, so Get/Delete are scoped to that prefix.",
                    appliesTo: [
                        {
                            regex: "/^Resource::arn:.*:transcribe:.*:transcription-job/vams-video-sop-bom-\\*$/g",
                        },
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "Object ARNs under one named bucket and key prefix: Amazon S3 expresses object " +
                        "access only as the bucket ARN plus a key wildcard. Externally registered asset " +
                        "buckets are imported by ARN, so theirs render as a literal bucket ARN.",
                    appliesTo: [
                        { regex: "/^Resource::<.*Bucket.*\\.Arn>/.*\\*$/g" },
                        { regex: "/^Resource::arn:.*:s3:::.*\\*$/g" },
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "Task-token callback: the execution holding the token is created per run, so its " +
                        "ARN is unknown at synthesis. Scoped to this account and Region.",
                    appliesTo: [{ regex: "/^Resource::arn:.*:states:.*:\\*$/g" }],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "kms:GenerateDataKey* and kms:ReEncrypt* are the action families Amazon S3 SSE-KMS " +
                        "and Amazon Transcribe output encryption call on the deployment's key; both are " +
                        "scoped to that key's ARN.",
                    appliesTo: ["Action::kms:GenerateDataKey*", "Action::kms:ReEncrypt*"],
                },
            ],
            true
        );

        // The Lambdas' asset-bucket reads on an externally registered bucket render as a literal ARN
        // plus key wildcard, which the shared bucket-object suppression does not cover.
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "Externally registered asset buckets are imported by ARN, so an object grant on " +
                        "them renders as a literal bucket ARN plus a key wildcard rather than a construct " +
                        "reference.",
                    appliesTo: [{ regex: "/^Resource::arn:.*:s3:::.*\\*$/g" }],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/VideoSopBomProcessing-StateMachine/Role/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "batch:DescribeJobs supports no resource-level permissions and Batch job ids are " +
                        "generated at submit time, so cancelling the .sync job on StopExecution needs " +
                        "DescribeJobs on * and TerminateJob on job/*; BatchSubmitJob grants SubmitJob on " +
                        "job-definition/* and LambdaInvoke grants the functions' version qualifiers, and " +
                        "the logging and X-Ray delivery actions have no resource type.",
                    appliesTo: [
                        "Resource::*",
                        "Action::kms:GenerateDataKey*",
                        `Resource::arn:<AWS::Partition>:batch:${region}:${account}:job-definition/*`,
                        { regex: "/^Resource::arn:.*:batch:.*:job/\\*$/g" },
                        { regex: "/^Resource::<.*\\.Arn>:\\*$/g" },
                    ],
                },
            ],
            true
        );
    }
}
