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
import {
    generateUniqueNameHash,
    grantExternalAssetBucketKmsKeys,
    kmsKeyPolicyStatementGenerator,
} from "../../../../../helper/security";
import {
    buildConstructPipelineFunction,
    buildExecuteBatchJobFunction,
    buildOpenPipelineFunction,
    buildPipelineEndFunction,
    buildVamsExecuteCoordinateTransformFunction,
} from "../lambdaBuilder/coordinateTransformFunctions";
import { CoordinateTransformCodeBuildConstruct } from "./coordinateTransformCodeBuild-construct";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";
import path = require("path");

export interface CoordinateTransformConstructProps extends cdk.StackProps {
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

export class CoordinateTransformConstruct extends Construct {
    public readonly pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: CoordinateTransformConstructProps) {
        super(parent, name);

        const region = cdk.Stack.of(this).region;
        const account = cdk.Stack.of(this).account;

        // IAM policies for container roles
        const s3BucketActions = [
            "s3:GetObject",
            "s3:PutObject",
            "s3:ListBucket",
            "s3:GetBucketLocation",
        ];

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
                        actions: s3BucketActions,
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
                    actions: s3BucketActions,
                    resources: [
                        props.assetAuxiliaryBucket.bucketArn,
                        props.assetAuxiliaryBucket.bucketArn + "/*",
                    ],
                }),
            ],
        });

        //Add KMS key use if provided
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

        // Container execution role
        const containerExecutionRole = new iam.Role(this, "CoordTransformContainerExecutionRole", {
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

        // Container job role
        const containerJobRole = new iam.Role(this, "CoordTransformContainerJobRole", {
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

        // Grant access to any external asset bucket customer managed KMS keys so the
        // container can read/write objects in cross-account encrypted buckets
        // (no-op when no external keys are configured)
        grantExternalAssetBucketKmsKeys(containerJobRole);

        // CodeBuild-based container build (when useCodeBuild is true)
        let codeBuildConstruct: CoordinateTransformCodeBuildConstruct | undefined;
        if (props.config.app.pipelines.useConversionCoordinateTransform?.useCodeBuild === true) {
            codeBuildConstruct = new CoordinateTransformCodeBuildConstruct(
                this,
                "CoordTransformCodeBuild",
                {
                    config: props.config,
                    vpc: props.vpc,
                    pipelineSubnets: props.pipelineSubnets,
                    pipelineSecurityGroups: props.pipelineSecurityGroups,
                }
            );

            new cdk.CfnOutput(this, "CoordTransformCodeBuildProject", {
                value: codeBuildConstruct.codeBuildProjectName,
                description:
                    "CodeBuild project name for Coordinate Transform container. Check build status: aws codebuild list-builds-for-project --project-name <value>",
            });
        }

        // Batch Fargate pipeline
        const batchPipeline = new BatchFargatePipelineConstruct(
            this,
            "BatchFargatePipeline_CoordTransform",
            {
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
                    "conversion",
                    "coordinateTransform",
                    "container"
                ),
                dockerfileName: "Dockerfile",
                batchJobDefinitionName:
                    "CoordinateTransformJob_" +
                    props.config.name +
                    "_" +
                    props.config.app.baseStackName,
                ephemeralStorageGiB: 60,
                ecrRepository: codeBuildConstruct?.repository,
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
            props.storageResources.eventBridge.orchestrationBus,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.kmsKey
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

        // No heartbeatTimeout: the container reports only terminal success/failure on the
        // internal token (it sends no periodic heartbeats), so a heartbeat window would fail
        // any transform outlasting it. The 4-hour taskTimeout bounds the wait instead.
        const coordTransformBatchJob = new tasks.LambdaInvoke(this, "CoordTransformBatchJob", {
            lambdaFunction: executeBatchJobFunction,
            integrationPattern: sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
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
            taskTimeout: sfn.Timeout.duration(cdk.Duration.hours(4)),
        })
            .addCatch(handleBatchError, {
                resultPath: "$.error",
            })
            .next(pipelineEndTask);

        const definition = constructPipelineTask.next(coordTransformBatchJob);

        const stateMachineLogGroup = new logs.LogGroup(
            this,
            "CoordTransformProcessing-StateMachineLogGroup",
            {
                logGroupName:
                    "/aws/vendedlogs/VAMSStateMachine-CoordTransform" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "CoordTransformProcessing-StateMachineLogGroup",
                        10
                    ),
                retention: logs.RetentionDays.TEN_YEARS,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            }
        );

        const stateMachine = new sfn.StateMachine(this, "CoordTransformProcessing-StateMachine", {
            definitionBody: sfn.DefinitionBody.fromChainable(definition),
            // Envelopes the 4-hour batch-job taskTimeout so an overrunning transform hits that
            // task's own timeout and reaches pipelineEnd — which releases the external task
            // token — rather than being cut short by an execution-level States.Timeout.
            timeout: cdk.Duration.hours(5),
            logs: {
                destination: stateMachineLogGroup,
                includeExecutionData: true,
                level: sfn.LogLevel.ALL,
            },
            tracingEnabled: true,
        });

        // Open pipeline Lambda
        const allowedExtensions = ".e57,.las,.laz,.ply";
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
        const vamsExecuteFunction = buildVamsExecuteCoordinateTransformFunction(
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
        if (
            props.config.app.pipelines.useConversionCoordinateTransform?.autoRegisterWithVAMS ===
            true
        ) {
            new VamsSchemaRegistration(this, "CoordinateTransformRegistration", {
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
                    "coordinateTransform",
                    "vamsSchema"
                ),
                resourceOverrides: { lambdaName: vamsExecuteFunction.functionName },
                idOverrides: {
                    pipelineId: "conversion-coordinate-transform",
                    workflowId: "conversion-coordinate-transform",
                },
                triggerEnabled:
                    props.config.app.pipelines.useConversionCoordinateTransform
                        ?.autoRegisterAutoTriggerOnFileUpload === true,
            });
        }

        // CDK Nag suppressions
        NagSuppressions.addResourceSuppressions(
            containerExecutionRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "ECS Container execution role uses AWS Managed Policies for task execution and X-Ray tracing.",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require wildcard access to objects in asset buckets for coordinate transformation processing.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            containerJobRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "ECS Container job role uses AWS Managed Policies for task execution and X-Ray tracing.",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require wildcard access to objects in asset buckets for coordinate transformation processing.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Pipeline Lambda functions require access to S3 asset buckets and Step Functions for coordinate transformation.",
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
            `/${this.toString()}/CoordTransformProcessing-StateMachine/Role/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "State machine default policy requires wildcard for Batch job submission and Lambda invocation.",
                    appliesTo: [
                        "Resource::*",
                        "Action::kms:GenerateDataKey*",
                        `Resource::arn:<AWS::Partition>:batch:${region}:${account}:job-definition/*`,
                        { regex: "/^Resource::<.*Function.*.Arn>:.*$/g" },
                        { regex: "/^Action::s3:.*$/g" },
                    ],
                },
            ],
            true
        );
    }
}
