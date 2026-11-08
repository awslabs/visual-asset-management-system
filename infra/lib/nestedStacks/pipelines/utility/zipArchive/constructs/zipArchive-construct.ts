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
import {
    PREPARED_DOWNLOADS_PREFIX,
    storageResources,
} from "../../../../storage/storageBuilder-nestedStack";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { Service } from "../../../../../helper/service-helper";
import { BatchFargatePipelineConstruct } from "../../../constructs/batch-fargate-pipeline";
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
    buildVamsExecuteZipArchiveFunction,
} from "../lambdaBuilder/zipArchiveFunctions";
import { ZipArchiveCodeBuildConstruct } from "./zipArchiveCodeBuild-construct";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";
import path = require("path");

/** Identity of the zipArchive system pipeline and workflow (one each, database GLOBAL). */
export const ZIP_ARCHIVE_PIPELINE_ID = "utility-zip-archive";
export const ZIP_ARCHIVE_WORKFLOW_ID = "utility-zip-archive";
export const ZIP_ARCHIVE_DATABASE_ID = "GLOBAL";

/**
 * Bounds of one zipArchive run. The Batch attempt and the state that waits for it end at 11 hours, the
 * state machine at 11.5, so a run that overruns reaches pipelineEnd (which answers the VAMS workflow's
 * token) before the workflow's own 12-hour taskTimeout in vamsSchema/pipeline.json (43200 s).
 */
export const ZIP_ARCHIVE_BATCH_TIMEOUT = cdk.Duration.hours(11);
export const ZIP_ARCHIVE_STATE_MACHINE_TIMEOUT = cdk.Duration.minutes(11 * 60 + 30);

export interface ZipArchiveConstructProps extends cdk.StackProps {
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
 * The zipArchive system pipeline (backendPipelines/utility/zipArchive): an AWS Batch Fargate job that
 * streams a selection of asset files into one zip (ZIP64), tar or tar.gz archive. A Download as Zip
 * job (mode 1) writes it under `vams.preparedDownloads/` in the auxiliary bucket and records progress on
 * the job row; a workflow run (mode 2) writes it to the run's outputs so it becomes an asset file.
 * Mirrors the coordinate transform construct; built only when app.zipDownloads.enabled. The image is
 * built with local Docker, or with AWS CodeBuild when app.zipDownloads.useCodeBuild.
 */
export class ZipArchiveConstruct extends Construct {
    public readonly pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: ZipArchiveConstructProps) {
        super(parent, name);

        const region = cdk.Stack.of(this).region;
        const account = cdk.Stack.of(this).account;
        const zip = props.config.app.zipDownloads;

        // Asset buckets: read the selection (any version), and in mode 2 write the archive to the run's
        // outputs prefix as a multipart upload.
        const inputBucketPolicy = new iam.PolicyDocument({
            statements: [
                ...s3AssetBuckets.getS3AssetBucketRecords().map((record) => {
                    const prefix = record.prefix || "/";
                    // {bucketArn}/{prefix}*, with any leading slash stripped so the root prefix
                    // yields {bucketArn}/*.
                    const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";
                    const objectPrefix = normalizedPrefix.replace(/^\/+/, "");
                    return new iam.PolicyStatement({
                        effect: iam.Effect.ALLOW,
                        actions: [
                            "s3:GetObject",
                            // Every member is read at the S3 version the job resolved.
                            "s3:GetObjectVersion",
                            "s3:PutObject",
                            "s3:AbortMultipartUpload",
                            "s3:ListMultipartUploadParts",
                            "s3:ListBucket",
                            // Folder and whole-asset entries expand by ListObjectVersions.
                            "s3:ListBucketVersions",
                            "s3:GetBucketLocation",
                        ],
                        resources: [
                            record.bucket.bucketArn,
                            `${record.bucket.bucketArn}/${objectPrefix}*`,
                        ],
                    });
                }),
            ],
        });

        // Auxiliary bucket: only the prepared-downloads prefix (request manifest read, resolved
        // manifest and archive written). The deletes remove the job's own archive version when it
        // landed after another writer finalized the job without it (runner.Job, one outcome).
        const outputBucketPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:AbortMultipartUpload",
                        "s3:ListMultipartUploadParts",
                        "s3:DeleteObject",
                        "s3:DeleteObjectVersion",
                    ],
                    resources: [
                        `${props.assetAuxiliaryBucket.bucketArn}/${PREPARED_DOWNLOADS_PREFIX}*`,
                    ],
                }),
                // s3:ListBucket makes S3 answer a HeadObject on a missing archive key with 404, not
                // 403: the container decides whether its own archive landed (#1125 D21).
                new iam.PolicyStatement({
                    actions: ["s3:ListBucket"],
                    resources: [props.assetAuxiliaryBucket.bucketArn],
                    conditions: {
                        StringLike: { "s3:prefix": [`${PREPARED_DOWNLOADS_PREFIX}*`] },
                    },
                }),
            ],
        });

        // The job row (status and progress) and, for a mode 2 asset-version pin, the version's files.
        const tablePolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: ["dynamodb:GetItem", "dynamodb:UpdateItem"],
                    resources: [
                        props.storageResources.dynamo.zipDownloadRequestsStorageTable.tableArn,
                    ],
                }),
                new iam.PolicyStatement({
                    actions: ["dynamodb:Query"],
                    resources: [
                        props.storageResources.dynamo.assetFileVersionsStorageTable.tableArn,
                    ],
                }),
            ],
        });

        if (props.kmsKey) {
            inputBucketPolicy.addStatements(kmsKeyPolicyStatementGenerator(props.kmsKey));
            outputBucketPolicy.addStatements(kmsKeyPolicyStatementGenerator(props.kmsKey));
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

        // Container EXECUTION role: used by the ECS agent to pull the image and write the log stream.
        const containerExecutionRole = new iam.Role(this, "ZipArchiveContainerExecutionRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
            ],
        });

        // Container JOB role: what the zipArchive process itself calls.
        const containerJobRole = new iam.Role(this, "ZipArchiveContainerJobRole", {
            assumedBy: Service("ECS_TASKS").Principal,
            inlinePolicies: {
                InputBucketPolicy: inputBucketPolicy,
                OutputBucketPolicy: outputBucketPolicy,
                TablePolicy: tablePolicy,
                StateTaskPolicy: stateTaskPolicy,
            },
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
            ],
        });

        // Cross-account external asset buckets encrypted with their own keys (no-op without them).
        grantExternalAssetBucketKmsKeys(containerJobRole);

        // CodeBuild-based container build (when useCodeBuild is true), as the coordinate transform does.
        let codeBuildConstruct: ZipArchiveCodeBuildConstruct | undefined;
        if (zip.useCodeBuild === true) {
            codeBuildConstruct = new ZipArchiveCodeBuildConstruct(this, "ZipArchiveCodeBuild", {
                config: props.config,
                kmsKey: props.kmsKey,
            });

            new cdk.CfnOutput(this, "ZipArchiveCodeBuildProject", {
                value: codeBuildConstruct.codeBuildProjectName,
                description:
                    "CodeBuild project name for the Zip Archive container. Check build status, then use Download as Zip once it prints SUCCEEDED: aws codebuild batch-get-builds --ids \"$(aws codebuild list-builds-for-project --project-name <value> --no-paginate --query 'ids[0]' --output text)\" --query 'builds[0].buildStatus' --output text",
            });
        }

        // The container's stdout/stderr, in the /aws/vendedlogs/Pipelines/ prefix the execution
        // service may read.
        const containerLogGroup = new logs.LogGroup(this, "ZipArchiveBatchJobLogGroup", {
            logGroupName:
                "/aws/vendedlogs/Pipelines/ZipArchive" +
                generateUniqueNameHash(
                    props.config.env.coreStackName,
                    props.config.env.account,
                    "ZipArchiveBatchJobLogGroup",
                    10
                ),
            encryptionKey: props.kmsKey,
            retention: logs.RetentionDays.ONE_YEAR,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        const batchPipeline = new BatchFargatePipelineConstruct(
            this,
            "BatchFargatePipeline_ZipArchive",
            {
                // WAIT_FOR_TASK_TOKEN bounds only the token wait, so the job carries its own bound.
                attemptDuration: ZIP_ARCHIVE_BATCH_TIMEOUT,
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
                    "utility",
                    "zipArchive",
                    "container"
                ),
                dockerfileName: "Dockerfile",
                batchJobDefinitionName:
                    "ZipArchiveJob_" + props.config.name + "_" + props.config.app.baseStackName,
                cpu: zip.computeVcpu,
                memoryMiB: Config.fargateMemoryMiB(zip.computeMemoryGiB),
                // The engine streams S3 to S3 through bounded memory buffers and never stages a member
                // on disk, so the Fargate minimum is enough.
                ephemeralStorageGiB: 21,
                ecrImage: codeBuildConstruct
                    ? {
                          repository: codeBuildConstruct.repository,
                          tag: codeBuildConstruct.imageTag,
                      }
                    : undefined,
                environment: {
                    ZIP_DOWNLOAD_REQUESTS_STORAGE_TABLE_NAME:
                        props.storageResources.dynamo.zipDownloadRequestsStorageTable.tableName,
                    ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME:
                        props.storageResources.dynamo.assetFileVersionsStorageTable.tableName,
                    ASSET_AUXILIARY_BUCKET_NAME: props.assetAuxiliaryBucket.bucketName,
                    PREPARED_DOWNLOADS_PREFIX: PREPARED_DOWNLOADS_PREFIX,
                },
            }
        );

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

        pipelineEndTask.next(
            new sfn.Choice(this, "EndStatesChoice")
                .when(sfn.Condition.isPresent("$.error"), failState)
                .otherwise(successState)
        );

        const handleBatchError = new sfn.Pass(this, "HandleBatchError", {
            resultPath: "$",
        }).next(pipelineEndTask);

        // A constructPipeline failure it cannot report itself (timeout, out of memory, import error)
        // still reaches pipelineEnd, which answers the VAMS workflow's token.
        const handleConstructPipelineError = new sfn.Pass(this, "HandleConstructPipelineError", {
            resultPath: "$",
        }).next(pipelineEndTask);
        constructPipelineTask.addCatch(handleConstructPipelineError, { resultPath: "$.error" });

        // The container answers this internal token with its terminal result and heartbeats the VAMS
        // workflow's token while it runs; the 11-hour taskTimeout bounds the wait.
        const archiveBatchJob = new tasks.LambdaInvoke(this, "ZipArchiveBatchJob", {
            lambdaFunction: executeBatchJobFunction,
            integrationPattern: sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            payload: sfn.TaskInput.fromObject({
                taskToken: sfn.JsonPath.taskToken,
                "jobName.$": "$.jobName",
                "definition.$": "$.definition",
                // Lets the Lambda register the submitted Batch job as abortable.
                "orchestrationEventPrefix.$": "$.orchestrationEventPrefix",
            }),
            resultPath: "$.batchResult",
            taskTimeout: sfn.Timeout.duration(ZIP_ARCHIVE_BATCH_TIMEOUT),
        })
            .addCatch(handleBatchError, { resultPath: "$.error" })
            .next(pipelineEndTask);

        const stateMachineLogGroup = new logs.LogGroup(
            this,
            "ZipArchiveProcessing-StateMachineLogGroup",
            {
                logGroupName:
                    "/aws/vendedlogs/VAMSStateMachine-ZipArchive" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "ZipArchiveProcessing-StateMachineLogGroup",
                        10
                    ),
                encryptionKey: props.kmsKey,
                retention: logs.RetentionDays.ONE_YEAR,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            }
        );

        const stateMachine = new sfn.StateMachine(this, "ZipArchiveProcessing-StateMachine", {
            definitionBody: sfn.DefinitionBody.fromChainable(
                constructPipelineTask.next(archiveBatchJob)
            ),
            timeout: ZIP_ARCHIVE_STATE_MACHINE_TIMEOUT,
            logs: {
                destination: stateMachineLogGroup,
                includeExecutionData: true,
                level: sfn.LogLevel.ALL,
            },
            tracingEnabled: true,
        });

        const openPipelineFunction = buildOpenPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.assetAuxiliaryBucket,
            stateMachine,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.storageResources.eventBridge.orchestrationBus,
            stateMachineLogGroup,
            props.kmsKey
        );

        const vamsExecuteFunction = buildVamsExecuteZipArchiveFunction(
            this,
            props.lambdaCommonBaseLayer,
            openPipelineFunction,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.kmsKey
        );

        this.pipelineVamsLambdaFunctionName = vamsExecuteFunction.functionName;

        // A system pipeline (isSystem in its vamsSchema bundle) with fixed ids, always registered:
        // the zip download service launches workflow GLOBAL/utility-zip-archive by name.
        new VamsSchemaRegistration(this, "ZipArchiveRegistration", {
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
                "utility",
                "zipArchive",
                "vamsSchema"
            ),
            resourceOverrides: { lambdaName: vamsExecuteFunction.functionName },
            idOverrides: {
                pipelineId: ZIP_ARCHIVE_PIPELINE_ID,
                pipelineDatabaseId: ZIP_ARCHIVE_DATABASE_ID,
                workflowId: ZIP_ARCHIVE_WORKFLOW_ID,
                workflowDatabaseId: ZIP_ARCHIVE_DATABASE_ID,
            },
        });

        // CDK Nag suppressions
        for (const role of [containerExecutionRole, containerJobRole]) {
            NagSuppressions.addResourceSuppressions(
                role,
                [
                    { id: "AwsSolutions-IAM4", reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED },
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "The zipArchive container reads any file of the selected asset and writes under one object prefix.",
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
                    reason: "Pipeline Lambda functions read the asset buckets and answer Step Functions task tokens.",
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
            `/${this.toString()}/ZipArchiveProcessing-StateMachine/Role/DefaultPolicy/Resource`,
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
