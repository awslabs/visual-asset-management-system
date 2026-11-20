/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as eventsTargets from "aws-cdk-lib/aws-events-targets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import { Construct } from "constructs";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import * as Config from "../../../../../../config/config";
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { Service } from "../../../../../helper/service-helper";
import { BatchFargatePipelineConstruct } from "../../../../pipelines/constructs/batch-fargate-pipeline";
import { VamsSchemaRegistration } from "../../../../pipelines/constructs/vamsSchemaRegistration-construct";
import {
    NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
    generateUniqueNameHash,
    grantExternalAssetBucketKmsKeys,
    kmsKeyPolicyStatementGenerator,
} from "../../../../../helper/security";
import {
    MIRIS_DEPENDENCY_MANIFEST_PREFIX,
    MIRIS_DEPENDENCY_MANIFEST_PREFIX_OBJECT_REGEX,
    buildConstructPipelineFunction,
    buildMirisUploadGateFunction,
    buildOpenPipelineFunction,
    buildPipelineEndFunction,
    buildVamsExecuteMirisUploadFunction,
} from "../lambdaBuilder/mirisUploadFunctions";
import path = require("path");

export interface MirisUploadConstructProps extends cdk.StackProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowV2FunctionName: string;
}

/**
 * The container's work beyond the Miris processing wait (`taskTimeoutSeconds`): downloading and
 * packaging the asset, the upload itself and writing the stream metadata.
 */
export const MIRIS_UPLOAD_CONTAINER_OVERHEAD_SECONDS = 1200;

/**
 * Time the state machine allows beyond the Batch attempt: the job queue wait and the construct and
 * end Lambdas. The state machine must outlast the attempt, or its own timeout ends the execution
 * while the job still runs and no end state reports to the outer workflow.
 */
export const MIRIS_UPLOAD_ORCHESTRATION_MARGIN_SECONDS = 1800;

/**
 * The job's memory. Parsing text USD costs about 8 times the file size, so this bounds the text
 * layers an asset may hold (about 448 MiB at 4096 MiB); the container reads it from
 * `VAMS_TASK_MEMORY_MIB` (memory_budget.py). A valid Fargate size for 1 vCPU.
 */
export const MIRIS_UPLOAD_MEMORY_MIB = 4096;

/**
 * IAM5 `appliesTo` patterns, anchored and narrow enough that no wildcard ARN can satisfy them. A
 * partition is the `<AWS::Partition>` token or a literal `aws*` partition; a bucket segment takes
 * bucket-name characters only.
 */
const PARTITION = "(<AWS::Partition>|aws[a-z-]*)";
const REGION = "(<AWS::Region>|[a-z]{2}(-[a-z]+)+-[0-9]+)";
const ACCOUNT = "(<AWS::AccountId>|[0-9]{12})";
const BUCKET_NAME = "[a-z0-9][a-z0-9.-]*";

/**
 * The job role's object grants: the asset bucket this deployment creates (its storage-stack logical
 * id `AssetBucket<hash>`, never the auxiliary bucket or a bucket policy) at the root prefix, and an
 * imported external bucket under its configured prefix.
 */
export const MIRIS_UPLOAD_JOB_ROLE_OBJECT_REGEXES = [
    "/^Resource::<AssetBucket[0-9A-F]{8}\\.Arn>/(pipelines/)?\\*$/g",
    `/^Resource::arn:${PARTITION}:s3:::${BUCKET_NAME}/([^*?/]+/)*\\*$/g`,
];

/** Batch job definitions and jobs in this deployment's own Region and account. */
export const MIRIS_UPLOAD_BATCH_JOB_DEFINITION_REGEX = `/^Resource::arn:${PARTITION}:batch:${REGION}:${ACCOUNT}:job-definition/\\*$/g`;
export const MIRIS_UPLOAD_BATCH_JOB_REGEX = `/^Resource::arn:${PARTITION}:batch:${REGION}:${ACCOUNT}:job/\\*$/g`;

/** The inner execution statuses whose end the state machine's own states cannot report. */
export const MIRIS_UPLOAD_UNREPORTED_EXECUTION_STATUSES = ["FAILED", "TIMED_OUT", "ABORTED"];

export class MirisUploadConstruct extends Construct {
    public readonly pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: MirisUploadConstructProps) {
        super(parent, name);

        const region = cdk.Stack.of(this).region;
        const account = cdk.Stack.of(this).account;
        const upload = props.config.app.addons.useMirisStreaming.upload;
        const kmsKey = props.storageResources.encryption.kmsKey;

        // The job role reads the asset from the asset buckets, writes the stream metadata under the
        // run's output path (`{baseAssetsPrefix}pipelines/...`) and the dependency manifest under the
        // auxiliary bucket's manifest prefix, and reads the Integration Key secret.
        const assetBucketPolicy = new iam.PolicyDocument({
            statements: s3AssetBuckets.getS3AssetBucketRecords().flatMap((record) => {
                const prefix = record.prefix || "/";
                // {bucketArn}/{prefix}: the leading slash is stripped so the separator after the
                // bucket ARN is always present (the root prefix yields {bucketArn}/).
                const objectPrefix = (prefix.endsWith("/") ? prefix : prefix + "/").replace(
                    /^\/+/,
                    ""
                );
                return [
                    new iam.PolicyStatement({
                        effect: iam.Effect.ALLOW,
                        actions: ["s3:ListBucket"],
                        resources: [record.bucket.bucketArn],
                    }),
                    new iam.PolicyStatement({
                        effect: iam.Effect.ALLOW,
                        actions: ["s3:GetObject", "s3:GetObjectVersion"],
                        resources: [`${record.bucket.bucketArn}/${objectPrefix}*`],
                    }),
                    new iam.PolicyStatement({
                        effect: iam.Effect.ALLOW,
                        actions: ["s3:PutObject"],
                        resources: [`${record.bucket.bucketArn}/${objectPrefix}pipelines/*`],
                    }),
                ];
            }),
        });
        // The dependency manifest the stream metadata names, written to the auxiliary bucket.
        assetBucketPolicy.addStatements(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["s3:PutObject"],
                resources: [
                    `${props.storageResources.s3.assetAuxiliaryBucket.bucketArn}/${MIRIS_DEPENDENCY_MANIFEST_PREFIX}/*`,
                ],
            })
        );
        if (kmsKey) {
            assetBucketPolicy.addStatements(kmsKeyPolicyStatementGenerator(kmsKey));
        }

        const secretsPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["secretsmanager:GetSecretValue"],
                    resources: [props.config.app.addons.useMirisStreaming.apiKeySecretArn],
                }),
            ],
        });

        // Container EXECUTION role: used by the ECS agent to pull the image and write the task's log
        // stream, and that is all the managed policy below grants.
        const containerExecutionRole = new iam.Role(this, "MirisUploadContainerExecutionRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
            ],
        });

        // Container job role
        const containerJobRole = new iam.Role(this, "MirisUploadContainerJobRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            inlinePolicies: {
                AssetBucketPolicy: assetBucketPolicy,
                SecretsPolicy: secretsPolicy,
            },
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
            ],
        });

        // Customer managed keys of external asset buckets (no-op when none are configured).
        grantExternalAssetBucketKmsKeys(containerJobRole);

        // The container's stdout/stderr. A named vended group under the /aws/vendedlogs/Pipelines/
        // prefix the execution-service role is granted to read; KMS-encrypted and retained for a year.
        const containerLogGroup = new logs.LogGroup(this, "MirisUploadBatchJobLogGroup", {
            logGroupName:
                "/aws/vendedlogs/Pipelines/MirisUpload" +
                generateUniqueNameHash(
                    props.config.env.coreStackName,
                    props.config.env.account,
                    "MirisUploadBatchJobLogGroup",
                    10
                ),
            encryptionKey: kmsKey,
            retention: logs.RetentionDays.ONE_YEAR,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        const attemptDurationSeconds =
            upload.taskTimeoutSeconds + MIRIS_UPLOAD_CONTAINER_OVERHEAD_SECONDS;
        const executionTimeoutSeconds =
            attemptDurationSeconds + MIRIS_UPLOAD_ORCHESTRATION_MARGIN_SECONDS;

        // Batch Fargate pipeline
        const batchPipeline = new BatchFargatePipelineConstruct(
            this,
            "BatchFargatePipeline_MirisUpload",
            {
                // The state machine timeout envelopes this attempt; the .sync submission cancels
                // the job when the execution stops.
                attemptDuration: cdk.Duration.seconds(attemptDurationSeconds),
                config: props.config,
                vpc: props.vpc,
                subnets: props.pipelineSubnets,
                securityGroups: props.pipelineSecurityGroups,
                jobRole: containerJobRole,
                executionRole: containerExecutionRole,
                logGroup: containerLogGroup,
                imageAssetPath: path.join(
                    "..",
                    "..",
                    "..",
                    "..",
                    "..",
                    "backendPipelines",
                    "conversion",
                    "mirisUpload",
                    "container"
                ),
                dockerfileName: "Dockerfile",
                batchJobDefinitionName:
                    "MirisUploadJob_" + props.config.name + "_" + props.config.app.baseStackName,
                // Sized for an I/O-bound workload (S3 download, HTTPS upload, state polling) on the
                // construct's x86_64 platform: usd-core ships no aarch64 Linux wheel. The memory is
                // for parsing text USD.
                cpu: 1,
                memoryMiB: MIRIS_UPLOAD_MEMORY_MIB,
                ephemeralStorageGiB: 21,
            }
        );

        // Lambda functions
        const constructPipelineFunction = buildConstructPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            props.config,
            props.vpc,
            props.pipelineSubnets
        );

        const pipelineEndFunction = buildPipelineEndFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            props.config,
            props.vpc,
            props.pipelineSubnets
        );

        /**
         * Step Functions state machine
         *
         *   ConstructPipelineTask -> SubmitMirisUploadBatchJob -> MirisUploadEnd (SendTaskSuccess)
         *          | on catch                | on catch
         *          +-----------+-------------+
         *                      v
         *   MirisUploadEndFailure (SendTaskFailure + claim release) -> MirisUploadFail
         *
         * A timed-out or stopped execution reaches neither end task; the execution-ended rule below
         * runs the same end Lambda for it.
         */
        const constructPipelineTask = new tasks.LambdaInvoke(this, "ConstructPipelineTask", {
            lambdaFunction: constructPipelineFunction,
            outputPath: "$.Payload",
        });

        // BATCH_STATE_NAME in openPipeline.py names this state.
        const submitBatchTask = new tasks.BatchSubmitJob(this, "SubmitMirisUploadBatchJob", {
            jobName: sfn.JsonPath.stringAt("$.jobName"),
            jobDefinitionArn: batchPipeline.batchJobDefinition.jobDefinitionArn,
            jobQueueArn: batchPipeline.batchJobQueue.jobQueueArn,
            containerOverrides: {
                command: [...sfn.JsonPath.listAt("$.definition")],
                // The container ends before either time limit stops it (time_budget.py) and sizes
                // its text USD budget to the task's memory (memory_budget.py).
                environment: {
                    AWS_REGION: region,
                    VAMS_TASK_MEMORY_MIB: String(MIRIS_UPLOAD_MEMORY_MIB),
                    MIRIS_UPLOAD_EXECUTION_START_TIME:
                        sfn.JsonPath.stringAt("$$.Execution.StartTime"),
                    MIRIS_UPLOAD_EXECUTION_TIMEOUT_SECONDS: String(executionTimeoutSeconds),
                    MIRIS_UPLOAD_ATTEMPT_DURATION_SECONDS: String(attemptDurationSeconds),
                },
            },
            integrationPattern: sfn.IntegrationPattern.RUN_JOB,
            // Keeps the constructPipeline output (externalSfnTaskToken and the claim fields) for the
            // end task; the BatchSubmitJob result would otherwise replace the whole state.
            resultPath: sfn.JsonPath.DISCARD,
        });

        // A successful upload retires the asset's claims on other content, so it carries the claim
        // fields as the failure branch does.
        const pipelineEndTask = new tasks.LambdaInvoke(this, "MirisUploadEnd", {
            lambdaFunction: pipelineEndFunction,
            payload: sfn.TaskInput.fromObject({
                "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                status: "success",
                "databaseId.$": "$.databaseId",
                "assetId.$": "$.assetId",
                "claimFingerprint.$": "$.claimFingerprint",
                "inputOutputS3AssetAuxiliaryFilesPath.$": "$.inputOutputS3AssetAuxiliaryFilesPath",
            }),
        });

        // The same end Lambda with a failure status, so the outer workflow's callback token receives
        // SendTaskFailure and the gate's claim is released before the Fail state stops the execution.
        const pipelineEndFailureTask = new tasks.LambdaInvoke(this, "MirisUploadEndFailure", {
            lambdaFunction: pipelineEndFunction,
            payload: sfn.TaskInput.fromObject({
                "externalSfnTaskToken.$": "$.externalSfnTaskToken",
                status: "failed",
                "errorType.$": "$.error.Error",
                "errorCause.$": "$.error.Cause",
                // openPipeline and constructPipeline both set it, so either catch resolves it.
                "ambiguousRootReportS3Uri.$": "$.ambiguousRootReportS3Uri",
                "databaseId.$": "$.databaseId",
                "assetId.$": "$.assetId",
                "claimFingerprint.$": "$.claimFingerprint",
                "inputOutputS3AssetAuxiliaryFilesPath.$": "$.inputOutputS3AssetAuxiliaryFilesPath",
            }),
            resultPath: sfn.JsonPath.DISCARD,
        });

        const failState = new sfn.Fail(this, "MirisUploadFail", {
            error: "MirisUploadFailed",
            cause: "See Batch / container logs for details.",
        });

        pipelineEndFailureTask.next(failState);
        // constructPipeline returns the claim fields it was given, so both catches resolve the same
        // JSONPaths.
        constructPipelineTask.addCatch(pipelineEndFailureTask, { resultPath: "$.error" });
        submitBatchTask
            .addCatch(pipelineEndFailureTask, { resultPath: "$.error" })
            .next(pipelineEndTask);

        const definition = constructPipelineTask.next(submitBatchTask);

        const stateMachineLogGroup = new logs.LogGroup(this, "MirisUploadStateMachineLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSStateMachine-MirisUpload" +
                generateUniqueNameHash(
                    props.config.env.coreStackName,
                    props.config.env.account,
                    "MirisUploadStateMachineLogGroup",
                    10
                ),
            encryptionKey: kmsKey,
            retention: logs.RetentionDays.ONE_YEAR,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        const stateMachine = new sfn.StateMachine(this, "MirisUploadInnerStateMachine", {
            definitionBody: sfn.DefinitionBody.fromChainable(definition),
            timeout: cdk.Duration.seconds(executionTimeoutSeconds),
            logs: {
                destination: stateMachineLogGroup,
                includeExecutionData: true,
                level: sfn.LogLevel.ALL,
            },
            tracingEnabled: true,
        });

        // Stopping the execution cancels the .sync Batch task, which terminates the running job; the
        // BatchSubmitJob task grants only batch:SubmitJob. DescribeJobs has no resource type; job ids
        // are generated at submit time, so TerminateJob is scoped to this account's jobs.
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

        // An execution that times out or is stopped never reaches its own end states, so its end is
        // reported from the status change: the end Lambda releases the claim and fails the outer
        // workflow's task token. A FAILED execution whose failure branch already reported meets a
        // closed token, which the Lambda treats as done.
        new events.Rule(this, "MirisUploadExecutionEndedRule", {
            description:
                "Releases the Miris upload claim and fails the workflow task when the inner execution ends unreported",
            eventPattern: {
                source: ["aws.states"],
                detailType: ["Step Functions Execution Status Change"],
                detail: {
                    status: MIRIS_UPLOAD_UNREPORTED_EXECUTION_STATUSES,
                    stateMachineArn: [stateMachine.stateMachineArn],
                },
            },
            targets: [new eventsTargets.LambdaFunction(pipelineEndFunction, { retryAttempts: 2 })],
        });

        // Open pipeline Lambda
        const openPipelineFunction = buildOpenPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            stateMachine,
            stateMachineLogGroup,
            {
                jobDefinitionName: batchPipeline.batchJobDefinition.jobDefinitionName,
                logGroup: containerLogGroup,
            },
            props.config,
            props.vpc,
            props.pipelineSubnets
        );

        // Gate Lambda: claims the asset's upload for vamsExecute, so the per-file trigger fan-out
        // runs one upload
        const gateFunction = buildMirisUploadGateFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            props.config,
            props.vpc,
            props.pipelineSubnets
        );

        // VAMS execute Lambda
        const vamsExecuteFunction = buildVamsExecuteMirisUploadFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            openPipelineFunction,
            gateFunction,
            props.config,
            props.vpc,
            props.pipelineSubnets
        );

        this.pipelineVamsLambdaFunctionName = vamsExecuteFunction.functionName;

        // Auto-register with VAMS (V2 vamsSchema bundle -> V2 pipeline/workflow/template tables).
        if (upload.autoRegisterWithVAMS === true) {
            new VamsSchemaRegistration(this, "MirisUploadRegistration", {
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
                    "conversion",
                    "mirisUpload",
                    "vamsSchema"
                ),
                resourceOverrides: { lambdaName: vamsExecuteFunction.functionName },
                idOverrides: {
                    pipelineId: "miris-upload",
                    workflowId: "miris-upload",
                },
                triggerEnabled: upload.autoRegisterAutoTriggerOnFileUpload === true,
            });
        }

        // CDK Nag suppressions
        NagSuppressions.addResourceSuppressions(
            containerExecutionRole,
            [
                { id: "AwsSolutions-IAM4", reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ecr:GetAuthorizationToken, which the ECS agent calls to pull the container image, supports no resource-level permissions.",
                    appliesTo: ["Resource::*"],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            containerJobRole,
            [
                { id: "AwsSolutions-IAM4", reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "The container reads the asset's files, whose keys are known only at run time, " +
                        "under each asset bucket's VAMS prefix, and writes the stream metadata only under " +
                        "that prefix's pipelines/ run-output area.",
                    // A bucket this deployment creates renders as a GetAtt, an imported external
                    // bucket as the literal ARN from config.
                    appliesTo: MIRIS_UPLOAD_JOB_ROLE_OBJECT_REGEXES.map((regex) => ({ regex })),
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "Dependency manifests are keyed per asset and content hash, known only at run time, " +
                        "under the fixed pipelines/miris-upload/manifests/ prefix of this deployment's " +
                        "auxiliary bucket; the container only writes them.",
                    appliesTo: [{ regex: MIRIS_DEPENDENCY_MANIFEST_PREFIX_OBJECT_REGEX }],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Encrypt and decrypt with the deployment's own KMS key; the wildcards are the GenerateDataKey and ReEncrypt action families on that one key.",
                    appliesTo: [{ regex: "/^Action::kms:(GenerateDataKey|ReEncrypt)\\*$/g" }],
                },
            ],
            true
        );

        // The .sync Batch integration and the state machine's own logging and tracing.
        NagSuppressions.addResourceSuppressions(
            stateMachine.role,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "BatchSubmitJob grants SubmitJob on job-definition/* because Batch authorizes " +
                        "the revision-qualified ARN; Region and account are pinned, the queue is its own ARN.",
                    appliesTo: [{ regex: MIRIS_UPLOAD_BATCH_JOB_DEFINITION_REGEX }],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "Step Functions log delivery (logs:*LogDelivery*, PutResourcePolicy, " +
                        "DescribeLogGroups), X-Ray trace write and batch:DescribeJobs support no " +
                        "resource-level permissions, so Resource must be '*'.",
                    appliesTo: ["Resource::*"],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "Cancelling the .sync Batch task terminates a job whose id is generated at submit " +
                        "time; Region and account are pinned.",
                    appliesTo: [{ regex: MIRIS_UPLOAD_BATCH_JOB_REGEX }],
                },
            ],
            true
        );
    }
}
