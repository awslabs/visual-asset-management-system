/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
import { grantBatchStateTimeoutCleanup } from "../../../constructs/stateTimeoutBatchSubmitJob";
import {
    configureLambdaBatchJobTagging,
    pipelineJobTags,
    VAMS_PIPELINE_TAG_VALUES,
} from "../../../constructs/pipelineJobTags";
import {
    NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
    generateUniqueNameHash,
    grantExternalAssetBucketKmsKeys,
    kmsKeyPolicyStatementGenerator,
} from "../../../../../helper/security";
import {
    buildConstructPipelineFunction,
    buildExecuteBatchJobFunction,
    buildOpenPipelineFunction,
    buildPipelineEndFunction,
    buildVamsExecuteCogConvertFunction,
} from "../lambdaBuilder/cogConvertFunctions";
import { CogConvertCodeBuildConstruct } from "./cogConvertCodeBuild-construct";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";
import path = require("path");

export interface CogConvertConstructProps extends cdk.StackProps {
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
 * Timeout chain. The registered pipeline's 14,400-second `taskTimeout` (vamsSchema/pipeline.json)
 * starts before the execution does, so the state machine ends 15 minutes inside it. The token wait
 * on CogConvertBatchJob leaves 15 minutes for ConstructPipelineTask and PipelineEndTask
 * (5-minute Lambdas each), and the job's attempt duration equals that wait so the container does
 * not outlive the run.
 */
export const COG_CONVERT_STATE_MACHINE_TIMEOUT = cdk.Duration.minutes(225);
export const COG_CONVERT_BATCH_TIMEOUT = cdk.Duration.minutes(210);

/**
 * Where the workflow stages a run's output: `pipelines/{first pipeline}/{first job}/output/{execution}/` with the
 * `files`, `metadata` and `results` folders beneath it (backend `executionRecords.pipeline_output_prefixes`). The
 * names in the first two segments belong to the FIRST pipeline of the workflow, which is not necessarily this one,
 * and the execution id is minted per run, so neither is known when the role is created: the job role is limited to
 * the `output/` area of every run instead, and does not reach `pipelines/templates/`, `pipelines/workflowExecutionInputs/`
 * or any asset file. A per-run prefix would need credentials scoped at launch, which a Batch task role cannot be.
 */
const RUN_OUTPUT_KEY_PATTERN = "pipelines/*/*/output/*";

export class CogConvertConstruct extends Construct {
    public readonly pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: CogConvertConstructProps) {
        super(parent, name);

        const region = cdk.Stack.of(this).region;
        const account = cdk.Stack.of(this).account;

        // IAM policies for container roles, least privilege. The container READS the input file (at a launch
        // version) from an asset bucket and WRITES only its results under the run-I/O area of the default
        // asset bucket (`{baseAssetsPrefix}pipelines/...`, where the workflow stages every pipeline's output).
        // It never writes where an asset's own files live, so a flaw in GDAL reading an untrusted TIFF cannot
        // overwrite an original.
        const s3ReadActions = [
            "s3:GetObject",
            "s3:GetObjectVersion",
            "s3:ListBucket",
            "s3:GetBucketLocation",
        ];
        // AbortMultipartUpload: a failed multipart upload of a large COG is aborted by the SDK.
        const s3WriteActions = ["s3:PutObject", "s3:AbortMultipartUpload"];

        const assetBucketStatements: iam.PolicyStatement[] = [];
        for (const record of s3AssetBuckets.getS3AssetBucketRecords()) {
            const prefix = record.prefix || "/";
            // Build the object-level resource as {bucketArn}/{prefix}*. Strip any leading slash from the
            // prefix so the '/' separator after the bucket ARN is always present (root prefix yields
            // {bucketArn}/*).
            const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";
            const objectPrefix = normalizedPrefix.replace(/^\/+/, "");

            assetBucketStatements.push(
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: s3ReadActions,
                    resources: [
                        record.bucket.bucketArn,
                        `${record.bucket.bucketArn}/${objectPrefix}*`,
                    ],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: s3WriteActions,
                    resources: [
                        `${record.bucket.bucketArn}/${objectPrefix}${RUN_OUTPUT_KEY_PATTERN}`,
                    ],
                })
            );
        }
        const assetBucketPolicy = new iam.PolicyDocument({ statements: assetBucketStatements });

        //Add KMS key use if provided
        if (props.kmsKey) {
            assetBucketPolicy.addStatements(kmsKeyPolicyStatementGenerator(props.kmsKey));
        }

        const stateTaskPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: [
                        "states:SendTaskSuccess",
                        "states:SendTaskFailure",
                        "states:SendTaskHeartbeat",
                    ],
                    resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
                }),
            ],
        });

        // Container EXECUTION role: used by the ECS agent, not by the container's own process. It pulls
        // the image and writes the task's log stream, and that is all the managed policy below grants.
        const containerExecutionRole = new iam.Role(this, "CogConvertContainerExecutionRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
            ],
        });

        // Container job role: the process in the container. No managed policies: image pull and log writing
        // belong to the execution role, and the container sends no X-Ray segments.
        const containerJobRole = new iam.Role(this, "CogConvertContainerJobRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            inlinePolicies: {
                AssetBucketPolicy: assetBucketPolicy,
                StateTaskPolicy: stateTaskPolicy,
            },
        });

        // Grant access to any external asset bucket customer managed KMS keys so the
        // container can read and write its objects in cross-account encrypted buckets
        // (no-op when no external keys are configured)
        grantExternalAssetBucketKmsKeys(containerJobRole);

        // CodeBuild-based container build (when useCodeBuild is true)
        let codeBuildConstruct: CogConvertCodeBuildConstruct | undefined;
        if (props.config.app.pipelines.useCogConversion?.useCodeBuild === true) {
            codeBuildConstruct = new CogConvertCodeBuildConstruct(this, "CogConvertCodeBuild", {
                config: props.config,
                vpc: props.vpc,
                pipelineSubnets: props.pipelineSubnets,
                pipelineSecurityGroups: props.pipelineSecurityGroups,
                kmsKey: props.kmsKey,
            });

            new cdk.CfnOutput(this, "CogConvertCodeBuildProject", {
                value: codeBuildConstruct.codeBuildProjectName,
                description:
                    "CodeBuild project name for COG conversion container. Check build status, then run the pipeline once it prints SUCCEEDED: aws codebuild batch-get-builds --ids \"$(aws codebuild list-builds-for-project --project-name <value> --no-paginate --query 'ids[0]' --output text)\" --query 'builds[0].buildStatus' --output text",
            });
        }

        // The container's stdout/stderr. A named vended group under the /aws/vendedlogs/Pipelines/
        // prefix the execution-service role is granted to read; KMS-encrypted and retained for a
        // year, unlike Batch's default group.
        const containerLogGroup = new logs.LogGroup(this, "CogConvertBatchJobLogGroup", {
            logGroupName:
                "/aws/vendedlogs/Pipelines/CogConvert" +
                generateUniqueNameHash(
                    props.config.env.coreStackName,
                    props.config.env.account,
                    "CogConvertBatchJobLogGroup",
                    10
                ),
            encryptionKey: props.kmsKey,
            retention: logs.RetentionDays.ONE_YEAR,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        // Batch Fargate pipeline
        const batchPipeline = new BatchFargatePipelineConstruct(
            this,
            "BatchFargatePipeline_CogConvert",
            {
                // Matches the taskTimeout on the state that waits for this job. This
                // pipeline submits its own job under WAIT_FOR_TASK_TOKEN, so Step Functions
                // bounds only the token wait -- without this the container outlives the run.
                attemptDuration: COG_CONVERT_BATCH_TIMEOUT,
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
                    "cogConvert",
                    "container"
                ),
                dockerfileName: "Dockerfile",
                batchJobDefinitionName:
                    "CogConvertJob_" + props.config.name + "_" + props.config.app.baseStackName,
                // The volume holds the downloaded input and the COG written beside it. A COG with
                // overviews and DEFLATE can exceed the input (the container checks free space for
                // about 1.5x the input before it downloads), so the largest Fargate volume is used.
                // Fargate permits 21-200 GiB.
                ephemeralStorageGiB: 200,
                // GDAL's COG driver holds a block cache and the overview chain in memory. 8 vCPU
                // is the smallest size that allows the 32 GiB the container's memory budget uses.
                cpu: 8,
                memoryMiB: 32768,
                // The task memory the container sizes GDAL's cache from (Fargate shows the micro-VM's memory,
                // not the task's, to the process).
                environment: { VAMS_TASK_MEMORY_MIB: "32768" },
                ecrImage: codeBuildConstruct
                    ? {
                          repository: codeBuildConstruct.repository,
                          tag: codeBuildConstruct.imageTag,
                      }
                    : undefined,
            }
        );

        // Lambda functions
        const constructPipelineFunction = buildConstructPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.kmsKey
        );

        const executeBatchJobFunction = buildExecuteBatchJobFunction(
            this,
            props.lambdaCommonBaseLayer,
            batchPipeline.batchJobQueue,
            batchPipeline.batchJobDefinition,
            containerLogGroup,
            props.storageResources.eventBridge.orchestrationBus,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.kmsKey
        );
        configureLambdaBatchJobTagging(
            executeBatchJobFunction,
            pipelineJobTags(props.config, VAMS_PIPELINE_TAG_VALUES.cogConvert)
        );

        // Step Functions state machine
        const constructPipelineTask = new tasks.LambdaInvoke(this, "ConstructPipelineTask", {
            lambdaFunction: constructPipelineFunction,
            outputPath: "$.Payload",
        });

        const pipelineEndFunction = buildPipelineEndFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.kmsKey
        );
        // After the token wait's own bound fires, pipelineEnd ends the job executeBatchJob submitted.
        grantBatchStateTimeoutCleanup(pipelineEndFunction, [
            batchPipeline.batchJobQueue.jobQueueArn,
        ]);

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

        const endStatesChoice = new sfn.Choice(this, "EndStatesChoice")
            .when(sfn.Condition.isPresent("$.error"), failState)
            .otherwise(successState);

        pipelineEndTask.next(endStatesChoice);

        const handleBatchError = new sfn.Pass(this, "HandleBatchError", {
            resultPath: "$",
        }).next(pipelineEndTask);

        // ConstructPipelineTask is the first state, so a failure there ends the execution before
        // PipelineEndTask runs -- and PipelineEndTask is the only state that reports on the parent
        // workflow's callback token, which then pends for its full 14400s taskTimeout. The handler
        // reports the token for the errors it raises itself; this covers the failures where it never
        // runs at all: the function timeout, an out-of-memory kill, an import failure, or an invoke
        // fault that exhausts the task's service-exception retries.
        const handleConstructPipelineError = new sfn.Pass(this, "HandleConstructPipelineError", {
            resultPath: "$",
        }).next(pipelineEndTask);

        // resultPath keeps the state and appends the error, so pipelineEnd still finds
        // externalSfnTaskToken alongside it.
        constructPipelineTask.addCatch(handleConstructPipelineError, {
            resultPath: "$.error",
        });

        // No heartbeatTimeout: the container reports only terminal success/failure on the
        // internal token (it sends no periodic heartbeats), so a heartbeat window would fail
        // any conversion outlasting it. The taskTimeout bounds the wait instead.
        const cogConvertBatchJob = new tasks.LambdaInvoke(this, "CogConvertBatchJob", {
            lambdaFunction: executeBatchJobFunction,
            integrationPattern: sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            // executeBatchJob submits a Batch job on every call, so a retry after a Lambda service
            // fault could start a second container on the same token. The fault is caught like any
            // other error and reaches pipelineEnd.
            retryOnServiceExceptions: false,
            payload: sfn.TaskInput.fromObject({
                taskToken: sfn.JsonPath.taskToken,
                "jobName.$": "$.jobName",
                "definition.$": "$.definition",
                // Lets the Lambda register the submitted Batch job as abortable. Stopping this
                // sub-state-machine does not stop the job, because WAIT_FOR_TASK_TOKEN leaves the
                // job's lifecycle with nobody.
                "orchestrationEventPrefix.$": "$.orchestrationEventPrefix",
            }),
            resultPath: "$.batchResult",
            taskTimeout: sfn.Timeout.duration(COG_CONVERT_BATCH_TIMEOUT),
        })
            .addCatch(handleBatchError, {
                resultPath: "$.error",
            })
            .next(pipelineEndTask);

        const definition = constructPipelineTask.next(cogConvertBatchJob);

        const stateMachineLogGroup = new logs.LogGroup(
            this,
            "CogConvertProcessing-StateMachineLogGroup",
            {
                logGroupName:
                    "/aws/vendedlogs/VAMSStateMachine-CogConvert" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "CogConvertProcessing-StateMachineLogGroup",
                        10
                    ),
                encryptionKey: props.kmsKey,
                retention: logs.RetentionDays.ONE_YEAR,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            }
        );

        const stateMachine = new sfn.StateMachine(this, "CogConvertProcessing-StateMachine", {
            definitionBody: sfn.DefinitionBody.fromChainable(definition),
            // Envelopes the batch-job taskTimeout so an overrunning conversion hits that task's own
            // timeout and reaches pipelineEnd — which releases the external task token — rather
            // than being cut short by an execution-level States.Timeout.
            timeout: COG_CONVERT_STATE_MACHINE_TIMEOUT,
            logs: {
                destination: stateMachineLogGroup,
                includeExecutionData: true,
                level: sfn.LogLevel.ALL,
            },
            tracingEnabled: true,
        });

        // Open pipeline Lambda
        const allowedExtensions = ".tif,.tiff";
        const openPipelineFunction = buildOpenPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.assetAuxiliaryBucket,
            stateMachine,
            allowedExtensions,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.storageResources.eventBridge.orchestrationBus,
            stateMachineLogGroup,
            props.kmsKey
        );

        // VAMS execute Lambda
        const vamsExecuteFunction = buildVamsExecuteCogConvertFunction(
            this,
            props.lambdaCommonBaseLayer,
            openPipelineFunction,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.kmsKey
        );

        this.pipelineVamsLambdaFunctionName = vamsExecuteFunction.functionName;

        // Auto-register with VAMS (V2 vamsSchema bundle -> V2 pipeline/workflow/template tables).
        if (props.config.app.pipelines.useCogConversion?.autoRegisterWithVAMS === true) {
            new VamsSchemaRegistration(this, "CogConvertRegistration", {
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
                    "cogConvert",
                    "vamsSchema"
                ),
                resourceOverrides: { lambdaName: vamsExecuteFunction.functionName },
                idOverrides: {
                    pipelineId: "conversion-cog-convert",
                    workflowId: "conversion-cog-convert",
                },
                triggerEnabled:
                    props.config.app.pipelines.useCogConversion
                        ?.autoRegisterAutoTriggerOnFileUpload === true,
            });
        }

        // CDK Nag suppressions
        NagSuppressions.addResourceSuppressions(
            containerExecutionRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "The execution role carries the AWS managed ECS task execution policy (image pull, log writing), whose resource wildcards AWS defines.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            containerJobRole,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "The container reads the input file of any asset in the registered asset buckets (the object is chosen per run, so the key is a wildcard under each bucket's asset prefix) and writes only under the output folders of the run area (pipelines/*/*/output/*); it has no write permission on asset files. The states:SendTask* actions are scoped to this account and Region because the task token is the run's own and its state machine ARN is not known when the role is created.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Pipeline Lambda functions require access to S3 asset buckets and Step Functions for COG conversion.",
                },
                {
                    id: "AwsSolutions-IAM4",
                    reason: "Pipeline Lambda functions use the AWS managed AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole for CloudWatch logging and VPC networking.",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/CogConvertProcessing-StateMachine/Role/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "State machine default policy requires wildcard for Batch job submission and Lambda invocation.",
                    appliesTo: [
                        "Resource::*",
                        "Action::kms:GenerateDataKey*",
                        {
                            regex: "/^Resource::arn:<AWS::Partition>:batch:(?:[a-z0-9-]+|<AWS::Region>):(?:\\d{12}|<AWS::AccountId>):job-definition\\/\\*$/g",
                        },
                        { regex: "/^Resource::<.*Function.*.Arn>:.*$/g" },
                        { regex: "/^Action::s3:.*$/g" },
                    ],
                },
            ],
            true
        );
    }
}
