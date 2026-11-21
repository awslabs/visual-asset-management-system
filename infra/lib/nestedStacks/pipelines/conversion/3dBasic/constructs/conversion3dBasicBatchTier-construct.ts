/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Duration, Stack } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import * as Config from "../../../../../../config/config";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { Service } from "../../../../../helper/service-helper";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import {
    NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
    generateUniqueNameHash,
    grantExternalAssetBucketKmsKeys,
    kmsKeyPolicyStatementGenerator,
} from "../../../../../helper/security";
import {
    BatchFargatePipelineConstruct,
    infrastructureFaultRetry,
} from "../../../constructs/batch-fargate-pipeline";
import {
    CONVERSION_3D_BASIC_BATCH_STATE_ID,
    CONVERSION_3D_BASIC_LAMBDA_STATE_ID,
    buildConstructConversionPipelineFunction,
    buildConversionPipelineEndFunction,
    buildOpenConversion3dBasicPipelineFunction,
    buildSelectJobSizeFunction,
    buildVamsExecuteConversion3dBasicLauncherFunction,
} from "../lambdaBuilder/conversion3dBasicLauncherFunctions";

/**
 * Bounds of the Batch tier. A job's own attempt limit sits a little above the 4-hour bound the
 * container gives itself (VAMS_ATTEMPT_SECONDS), so the container ends its run cleanly first and AWS
 * Batch only terminates a wedged one. The Batch state allows one more hour for the job to leave
 * RUNNABLE (a Fargate vCPU quota that is too low keeps a job there), the state machine covers the
 * Lambda attempt and the Batch state, and the registered pipeline's `taskTimeout` covers the state
 * machine.
 */
export const CONVERSION_3D_BASIC_CONTAINER_ATTEMPT_SECONDS = 4 * 60 * 60;
export const CONVERSION_3D_BASIC_BATCH_ATTEMPT_TIMEOUT = Duration.minutes(4 * 60 + 15);
export const CONVERSION_3D_BASIC_BATCH_STATE_TIMEOUT = Duration.minutes(4 * 60 + 15 + 60);
export const CONVERSION_3D_BASIC_STATE_MACHINE_TIMEOUT = Duration.hours(6);
/** The registered pipeline's task timeout in seconds (6.5 hours). */
export const CONVERSION_3D_BASIC_REGISTERED_TASK_TIMEOUT_SECONDS = 6.5 * 60 * 60;
/** The compute environment's vCPU ceiling: eight of the largest jobs at once, the cost guard. */
export const CONVERSION_3D_BASIC_BATCH_MAX_VCPUS = 128;
/** Ephemeral storage of a job in GiB (the Fargate maximum), so memory and not disk binds the limit. */
export const CONVERSION_3D_BASIC_BATCH_EPHEMERAL_STORAGE_GIB = 200;

/**
 * A Batch state with the state-level TimeoutSeconds the CDK integration cannot set next to the job's
 * own attempt duration (BatchSubmitJob maps its timeout properties to the job's attempt duration
 * only). Without it a job that never leaves RUNNABLE (a Fargate vCPU quota that is too low) waits for
 * the state machine's timeout and the workflow's task for its own.
 */
export class BoundedBatchSubmitJob extends tasks.BatchSubmitJob {
    public toStateJson(queryLanguage?: sfn.QueryLanguage): object {
        return {
            ...super.toStateJson(queryLanguage),
            TimeoutSeconds: CONVERSION_3D_BASIC_BATCH_STATE_TIMEOUT.toSeconds(),
        };
    }
}

export interface Conversion3dBasicBatchTierProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    /** The converter Lambda (conversion3dBasicFunctions.ts), the state machine's first attempt. */
    converterFunction: lambda.IFunction;
}

/**
 * The AWS Batch tier of the 3D Basic Conversion pipeline (app.pipelines.useConversion3dBasic.batchTier):
 * a Fargate compute environment and queue, one job definition (the jobs differ only in the vCPU and
 * memory their state overrides), the job and execution roles, the state machine that tries the
 * converter Lambda first and continues a refused run on the smallest permitted job size, and the
 * launcher Lambdas. `launcherFunctionName` is the function the pipeline registers in place of the
 * converter when the tier is on.
 */
export class Conversion3dBasicBatchTier extends Construct {
    public readonly launcherFunction: lambda.Function;
    public readonly stateMachine: sfn.StateMachine;
    public readonly batchPipeline: BatchFargatePipelineConstruct;
    public readonly jobRole: iam.Role;

    constructor(scope: Construct, id: string, props: Conversion3dBasicBatchTierProps) {
        super(scope, id);

        const stack = Stack.of(this);
        const region = stack.region;
        const account = stack.account;
        const kmsKey = props.storageResources.encryption.kmsKey;
        const tierConfig = props.config.app.pipelines.useConversion3dBasic;
        const maxMemoryGiB = tierConfig.batchTier.maxMemoryGiB;
        const jobSizes = Config.CONVERSION_3D_BASIC_BATCH_TIER_JOB_SIZES.filter(
            (size) => size.memoryGiB <= maxMemoryGiB
        );
        const network = {
            config: props.config,
            vpc: props.vpc,
            subnets: props.pipelineSubnets,
            securityGroups: props.pipelineSecurityGroups,
        };

        /**
         * IAM. The job role reads the asset buckets and the run's auxiliary files, and writes only the
         * run's output and results prefixes. It carries no Step Functions action: the task token never
         * enters the job (the .sync integration reports the job's end), so a conversion of a hostile
         * file holds no way to answer the workflow.
         */
        const assetBucketStatements = s3AssetBuckets.getS3AssetBucketRecords().flatMap((record) => {
            const prefix = record.prefix || "/";
            const normalizedPrefix = (prefix.endsWith("/") ? prefix : prefix + "/").replace(
                /^\/+/,
                ""
            );
            return [
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["s3:GetObject", "s3:GetObjectVersion"],
                    resources: [`${record.bucket.bucketArn}/${normalizedPrefix}*`],
                }),
                // ListBucket makes a missing key answer 404 instead of 403 for the HeadObject and GetObject
                // calls the job makes; it never lists. StringLikeIfExists limits an actual listing (which
                // carries s3:prefix) to the bucket's base prefix and leaves those key-level calls alone,
                // where the condition key is absent. A bucket at its root has no prefix to limit to.
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["s3:ListBucket"],
                    resources: [record.bucket.bucketArn],
                    conditions:
                        normalizedPrefix === ""
                            ? undefined
                            : { StringLikeIfExists: { "s3:prefix": [`${normalizedPrefix}*`] } },
                }),
                // Output and results of a run: <baseAssetsPrefix>pipelines/<pipeline>/<job>/output/<execution>/...
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["s3:PutObject"],
                    resources: [
                        `${record.bucket.bucketArn}/${normalizedPrefix}pipelines/*/output/*`,
                    ],
                }),
            ];
        });
        const auxiliaryBucket = props.storageResources.s3.assetAuxiliaryBucket;
        const jobPolicy = new iam.PolicyDocument({
            statements: [
                ...assetBucketStatements,
                new iam.PolicyStatement({
                    actions: ["s3:GetObject", "s3:GetObjectVersion"],
                    resources: [`${auxiliaryBucket.bucketArn}/*`],
                }),
                new iam.PolicyStatement({
                    actions: ["s3:ListBucket"],
                    resources: [auxiliaryBucket.bucketArn],
                }),
            ],
        });
        if (kmsKey) {
            jobPolicy.addStatements(kmsKeyPolicyStatementGenerator(kmsKey));
        }

        this.jobRole = new iam.Role(this, "Conversion3dBasicBatchJobRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            inlinePolicies: { JobPolicy: jobPolicy },
        });
        // Customer managed KMS keys of external asset buckets (no-op when none is configured).
        grantExternalAssetBucketKmsKeys(this.jobRole);

        // Pulls the image and writes the container log: the managed execution policy and nothing else.
        const executionRole = new iam.Role(this, "Conversion3dBasicBatchExecutionRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
            ],
        });

        // The container's stdout/stderr: a named vended group under the prefix the execution-service
        // role is granted to read, KMS-encrypted and retained for a year.
        // pragma: allowlist nextline secret
        const containerLogGroup = new logs.LogGroup(this, "Conversion3dBasicBatchJobLogGroup", {
            logGroupName:
                "/aws/vendedlogs/Pipelines/Conversion3dBasic" +
                generateUniqueNameHash(
                    props.config.env.coreStackName,
                    props.config.env.account,
                    "Conversion3dBasicBatchJobLogGroup", // pragma: allowlist secret
                    10
                ),
            encryptionKey: kmsKey,
            retention: logs.RetentionDays.ONE_YEAR,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        /**
         * Batch: one job definition, registered at the smallest size; each tier's state overrides vCPU
         * and memory. The image is the Dockerfile's `batch` target, built with the same STEP argument
         * as the function image so both tiers read the same formats.
         */
        const enableStepInput = tierConfig.enableStepInput === true;
        this.batchPipeline = new BatchFargatePipelineConstruct(
            this,
            "BatchFargatePipeline_Conversion3dBasic",
            {
                attemptDuration: CONVERSION_3D_BASIC_BATCH_ATTEMPT_TIMEOUT,
                cpu: Config.CONVERSION_3D_BASIC_BATCH_TIER_JOB_SIZES[0].vcpu,
                memoryMiB: Config.fargateMemoryMiB(
                    Config.CONVERSION_3D_BASIC_BATCH_TIER_JOB_SIZES[0].memoryGiB
                ),
                config: props.config,
                vpc: props.vpc,
                subnets: props.pipelineSubnets,
                securityGroups: props.pipelineSecurityGroups,
                jobRole: this.jobRole,
                executionRole: executionRole,
                logGroup: containerLogGroup,
                imageAssetPath: path.join(
                    "..",
                    "..",
                    "..",
                    "..",
                    "..",
                    "backendPipelines",
                    "conversion",
                    "3dBasic",
                    "lambdaContainer"
                ),
                dockerfileName: "Dockerfile",
                imageTarget: "batch",
                imageBuildArgs: { ENABLE_STEP_INPUT: enableStepInput ? "true" : "false" },
                ephemeralStorageGiB: CONVERSION_3D_BASIC_BATCH_EPHEMERAL_STORAGE_GIB,
                maxvCpus: CONVERSION_3D_BASIC_BATCH_MAX_VCPUS,
                initProcessEnabled: true,
                environment: {
                    // The S3 clients read the Region at import; do not rely on the platform setting it.
                    AWS_REGION: region,
                    AWS_DEFAULT_REGION: region,
                },
                // Retry only an attempt that failed to start its task, never the conversion itself.
                retry: infrastructureFaultRetry(3),
                batchJobDefinitionName:
                    "Conversion3dBasicJob" +
                    props.config.name +
                    "_" +
                    props.config.app.baseStackName,
            }
        );

        /**
         * Launcher Lambdas
         */
        const selectJobSizeFunction = buildSelectJobSizeFunction(
            this,
            props.lambdaCommonBaseLayer,
            network,
            kmsKey
        );
        const constructPipelineFunction = buildConstructConversionPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            network,
            kmsKey
        );
        const pipelineEndFunction = buildConversionPipelineEndFunction(
            this,
            props.lambdaCommonBaseLayer,
            auxiliaryBucket,
            network,
            kmsKey
        );

        /**
         * State machine: the Lambda attempt, a Choice on its answer, the tier selector, the request
         * builder, a Choice on the job size and three Batch states, all ending in pipelineEnd.
         */
        const successState = new sfn.Succeed(this, "SuccessState", {
            comment: "Pipeline returned SUCCESS",
        });
        const failState = new sfn.Fail(this, "FailState", {
            causePath: sfn.JsonPath.stringAt("$.error.Cause"),
            errorPath: sfn.JsonPath.stringAt("$.error.Error"),
        });
        const endStatesChoice = new sfn.Choice(this, "EndStatesChoice")
            .when(sfn.Condition.isPresent("$.error"), failState)
            .otherwise(successState);
        const pipelineEndTask = new tasks.LambdaInvoke(this, "PipelineEndTask", {
            lambdaFunction: pipelineEndFunction,
            inputPath: "$",
            outputPath: "$.Payload",
        }).next(endStatesChoice);
        // Every state's failure is reported to the workflow through pipelineEnd instead of leaving the
        // task pending until its timeout. resultPath keeps the state, so externalSfnTaskToken is found.
        const handleError = new sfn.Pass(this, "HandleError", { resultPath: "$" }).next(
            pipelineEndTask
        );

        const constructPipelineTask = new tasks.LambdaInvoke(this, "ConstructPipelineTask", {
            lambdaFunction: constructPipelineFunction,
            resultSelector: { "conversionEvent.$": "$.Payload.conversionEvent" },
            resultPath: "$.construct",
        }).addCatch(handleError, { resultPath: "$.error" });

        // One state per job size: a Batch state's vCPU and memory overrides are static.
        const batchStates = jobSizes.map((size) => {
            const memoryMiB = Config.fargateMemoryMiB(size.memoryGiB);
            return {
                memoryMiB,
                state: new BoundedBatchSubmitJob(
                    this,
                    // The first state keeps the id openPipeline registers the container log under.
                    size === jobSizes[0]
                        ? CONVERSION_3D_BASIC_BATCH_STATE_ID
                        : `${CONVERSION_3D_BASIC_BATCH_STATE_ID}${size.vcpu}Vcpu`,
                    {
                        jobName: sfn.JsonPath.stringAt("$.jobName"),
                        jobDefinitionArn: this.batchPipeline.batchJobDefinition.jobDefinitionArn,
                        jobQueueArn: this.batchPipeline.batchJobQueue.jobQueueArn,
                        // The job's own attempt bound, counted from RUNNING. The state's bound, which also
                        // covers the time the job waits in RUNNABLE, is added by BoundedBatchSubmitJob.
                        taskTimeout: sfn.Timeout.duration(
                            CONVERSION_3D_BASIC_BATCH_ATTEMPT_TIMEOUT
                        ),
                        resultPath: sfn.JsonPath.DISCARD,
                        containerOverrides: {
                            vcpus: size.vcpu,
                            memory: cdk.Size.mebibytes(memoryMiB),
                            environment: {
                                VAMS_CONVERSION_EVENT: sfn.JsonPath.stringAt(
                                    "$.construct.conversionEvent"
                                ),
                                // The task's memory, which a Fargate container cannot read from its cgroup
                                VAMS_TASK_MEMORY_MIB: String(memoryMiB),
                                VAMS_TASK_VCPU: String(size.vcpu),
                                VAMS_ATTEMPT_SECONDS: String(
                                    CONVERSION_3D_BASIC_CONTAINER_ATTEMPT_SECONDS
                                ),
                            },
                        },
                    }
                )
                    .addCatch(handleError, { resultPath: "$.error" })
                    .next(pipelineEndTask),
            };
        });
        const sizeChoice = new sfn.Choice(this, "JobSizeChoice");
        batchStates.slice(1).forEach((entry) => {
            sizeChoice.when(
                sfn.Condition.numberEquals("$.selection.jobSize.memoryMiB", entry.memoryMiB),
                entry.state
            );
        });
        sizeChoice.otherwise(batchStates[0].state);
        const constructAndRun = constructPipelineTask.next(sizeChoice);

        // A forced Batch run (the input configuration's computeTier) skips the Lambda attempt.
        const useForcedSize = new sfn.Pass(this, "UseForcedJobSize", {
            parameters: { "jobSize.$": "$.forcedJobSize" },
            resultPath: "$.selection",
        }).next(constructAndRun);

        const selectJobSizeTask = new tasks.LambdaInvoke(this, "SelectJobSizeTask", {
            lambdaFunction: selectJobSizeFunction,
            payload: sfn.TaskInput.fromObject({
                attempt: sfn.JsonPath.objectAt("$.attempt"),
                maxMemoryGiB: sfn.JsonPath.numberAt("$.maxMemoryGiB"),
            }),
            resultSelector: {
                "jobSize.$": "$.Payload.jobSize",
                "escalationReason.$": "$.Payload.escalationReason",
                "requiredMemoryMiB.$": "$.Payload.requiredMemoryMiB",
            },
            resultPath: "$.selection",
        })
            .addCatch(handleError, { resultPath: "$.error" })
            .next(constructAndRun);

        const afterAttempt = new sfn.Choice(this, "EscalateChoice")
            .when(
                sfn.Condition.and(
                    sfn.Condition.isPresent("$.attempt.Payload.escalate"),
                    sfn.Condition.booleanEquals("$.attempt.Payload.escalate", true)
                ),
                selectJobSizeTask
            )
            .otherwise(pipelineEndTask);

        const attemptTask = new tasks.LambdaInvoke(this, CONVERSION_3D_BASIC_LAMBDA_STATE_ID, {
            lambdaFunction: props.converterFunction as lambda.IFunction,
            payload: sfn.TaskInput.fromObject({
                body: sfn.JsonPath.jsonMerge(
                    sfn.JsonPath.objectAt("$.conversionRequest"),
                    sfn.JsonPath.objectAt("$.attemptFlag")
                ),
            }),
            resultPath: "$.attempt",
        })
            .addCatch(handleError, { resultPath: "$.error" })
            .next(afterAttempt);

        const allowEscalation = new sfn.Pass(this, "AllowEscalation", {
            parameters: { allowEscalation: true },
            resultPath: "$.attemptFlag",
        }).next(attemptTask);
        const keepOnLambda = new sfn.Pass(this, "KeepOnLambda", {
            parameters: { allowEscalation: false },
            resultPath: "$.attemptFlag",
        }).next(attemptTask);

        const startChoice = new sfn.Choice(this, "ComputeTierChoice")
            .when(sfn.Condition.stringEquals("$.computeTier", "batch"), useForcedSize)
            .when(sfn.Condition.stringEquals("$.computeTier", "lambda"), keepOnLambda)
            .otherwise(allowEscalation);

        const stateMachineLogGroup = new logs.LogGroup(
            this,
            "Conversion3dBasicProcessing-StateMachineLogGroup", // pragma: allowlist secret
            {
                encryptionKey: kmsKey,
                logGroupName:
                    "/aws/vendedlogs/VAMSstateMachine-Conversion3dBasicPipeline" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        // pragma: allowlist nextline secret
                        "Conversion3dBasicProcessing-StateMachineLogGroup",
                        10
                    ),
                retention: logs.RetentionDays.ONE_YEAR,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            }
        );

        this.stateMachine = new sfn.StateMachine(this, "Conversion3dBasicProcessing-StateMachine", {
            definitionBody: sfn.DefinitionBody.fromChainable(sfn.Chain.start(startChoice)),
            timeout: CONVERSION_3D_BASIC_STATE_MACHINE_TIMEOUT,
            // ERROR level without execution data: the execution input carries the workflow's task
            // token (externalSfnTaskToken), which every VAMS logger redacts and which must not persist in
            // a log group. The run's stages and their errors are in the execution history and the
            // launcher and container logs.
            logs: {
                destination: stateMachineLogGroup,
                includeExecutionData: false,
                level: sfn.LogLevel.ERROR,
            },
            tracingEnabled: true,
        });
        // Stopping the state machine cancels the .sync Batch task, which needs the job terminated; job
        // ids are generated at submit time, so TerminateJob is scoped to this account's jobs.
        this.stateMachine.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["batch:DescribeJobs"],
                resources: ["*"],
            })
        );
        this.stateMachine.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["batch:TerminateJob"],
                resources: [`arn:${ServiceHelper.Partition()}:batch:${region}:${account}:job/*`],
            })
        );

        /**
         * The function the workflow invokes in place of the converter, and the function that starts
         * the state machine.
         */
        const openPipelineFunction = buildOpenConversion3dBasicPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            auxiliaryBucket,
            this.stateMachine,
            network,
            props.storageResources.eventBridge.orchestrationBus,
            stateMachineLogGroup,
            {
                converterLogGroupName: `/aws/lambda/${props.converterFunction.functionName}`,
                converterLogGroupArn: `arn:${ServiceHelper.Partition()}:logs:${region}:${account}:log-group:/aws/lambda/${
                    props.converterFunction.functionName
                }`,
                batchLogGroup: containerLogGroup,
                batchJobDefinitionName: this.batchPipeline.batchJobDefinition.jobDefinitionName,
            },
            maxMemoryGiB,
            kmsKey
        );
        this.launcherFunction = buildVamsExecuteConversion3dBasicLauncherFunction(
            this,
            props.lambdaCommonBaseLayer,
            auxiliaryBucket,
            openPipelineFunction,
            network,
            kmsKey
        );

        NagSuppressions.addResourceSuppressions(this.stateMachine, [
            {
                id: "AwsSolutions-SF1",
                reason:
                    "The state machine logs errors, not every event, on purpose: its execution input carries the " +
                    "workflow's task token (externalSfnTaskToken), which every VAMS logger redacts and which must " +
                    "not persist in a log group, and ERROR level without execution data cannot store it. " +
                    "The run's stages and errors are in the execution history and in the launcher and container logs.",
            },
        ]);
        this.addSuppressions();
    }

    private addSuppressions(): void {
        const stack = Stack.of(this);
        const reason =
            "Intended Solution. The pipeline lambda functions need appropriate access to S3, Step Functions and the state machine.";
        for (const name of [
            "openPipeline",
            "pipelineEnd",
            "vamsExecuteConversion3dBasicPipeline",
            "constructPipeline",
            "selectJobSize",
        ]) {
            NagSuppressions.addResourceSuppressions(
                this,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: reason,
                        appliesTo: [{ regex: `/^Resource::.*${name}/ServiceRole/.*/g` }],
                    },
                ],
                true
            );
        }
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            regex: "/^Resource::.*Conversion3dBasicProcessing-StateMachine/Role/.*/g",
                        },
                    ],
                },
            ],
            true
        );
        NagSuppressions.addResourceSuppressions(
            this.jobRole,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "The conversion job reads input files and run configuration in deployment-specific VAMS asset buckets " +
                        "(object keys under each bucket's base prefix, which are not known at deploy time) and writes only " +
                        "under <baseAssetsPrefix>pipelines/*/output/*, where the two '*' are the generated pipeline/job and " +
                        "execution ids. It holds no Step Functions, delete, ACL or policy action. ListBucket on an asset bucket with a base prefix is limited to that prefix for a listing; the job never lists, and the auxiliary bucket's ListBucket is bucket-wide because the run's auxiliary keys are generated per execution.",
                },
            ],
            true
        );
        NagSuppressions.addResourceSuppressions(
            this.node.findChild("Conversion3dBasicBatchExecutionRole"),
            [
                { id: "AwsSolutions-IAM4", reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED },
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "The execution role's default policy holds ecr:GetAuthorizationToken, which supports no " +
                        "resource-level permission, and the log write grant of the container log group.",
                    appliesTo: ["Resource::*", { regex: "/^Resource::.*:log-stream:\\*$/g" }],
                },
            ],
            true
        );
        NagSuppressions.addResourceSuppressionsByPath(
            stack,
            `/${this.toString()}/Conversion3dBasicProcessing-StateMachine/Role/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason:
                        "batch:DescribeJobs supports no resource-level permissions and Batch job ids are generated at submit " +
                        "time, so cancelling the .sync job on StopExecution needs DescribeJobs on * and TerminateJob on job/*; " +
                        "BatchSubmitJob grants SubmitJob on job-definition/* and LambdaInvoke grants the functions' version " +
                        "qualifiers, and the logging and X-Ray delivery actions have no resource type.",
                    appliesTo: [
                        "Resource::*",
                        "Action::kms:GenerateDataKey*",
                        {
                            regex: "/^Resource::arn:<AWS::Partition>:batch:(?:[a-z0-9-]+|<AWS::Region>):(?:\\d{12}|<AWS::AccountId>):job-definition\\/\\*$/g",
                        },
                        { regex: "/^Resource::arn:.*:batch:.*:job/\\*$/g" },
                        { regex: "/^Resource::<.*Function.*.Arn>:.*$/g" },
                        { regex: "/^Action::s3:.*$/g" },
                    ],
                },
            ],
            true
        );
    }
}
