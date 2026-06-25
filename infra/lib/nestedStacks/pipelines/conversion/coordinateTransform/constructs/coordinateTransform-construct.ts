/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as cr from "aws-cdk-lib/custom-resources";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import { Construct } from "constructs";
import { LayerVersion, Runtime } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import { Stack } from "aws-cdk-lib";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import * as Config from "../../../../../../config/config";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { Service } from "../../../../../helper/service-helper";
import { BatchFargatePipelineConstruct } from "../../../constructs/batch-fargate-pipeline";
import { generateUniqueNameHash } from "../../../../../helper/security";
import {
    buildConstructPipelineFunction,
    buildExecuteBatchJobFunction,
    buildOpenPipelineFunction,
    buildPipelineEndFunction,
    buildVamsExecuteCoordinateTransformFunction,
} from "../lambdaBuilder/coordinateTransformFunctions";
import { CoordinateTransformCodeBuildConstruct } from "./coordinateTransformCodeBuild-construct";
import path = require("path");

export interface CoordinateTransformConstructProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    assetAuxiliaryBucket: s3.IBucket;
    kmsKey?: kms.IKey;
    importGlobalPipelineWorkflowFunctionName: string;
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

        const coordTransformBatchJob = new tasks.LambdaInvoke(this, "CoordTransformBatchJob", {
            lambdaFunction: executeBatchJobFunction,
            integrationPattern: sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            payload: sfn.TaskInput.fromObject({
                taskToken: sfn.JsonPath.taskToken,
                "jobName.$": "$.jobName",
                "definition.$": "$.definition",
            }),
            resultPath: "$.batchResult",
            taskTimeout: sfn.Timeout.duration(cdk.Duration.hours(4)),
            heartbeatTimeout: sfn.Timeout.duration(cdk.Duration.minutes(30)),
        })
            .addCatch(handleBatchError, {
                resultPath: "$.error",
            })
            .next(pipelineEndTask);

        const definition = constructPipelineTask.next(coordTransformBatchJob);

        const stateMachine = new sfn.StateMachine(this, "CoordTransformProcessing-StateMachine", {
            definitionBody: sfn.DefinitionBody.fromChainable(definition),
            timeout: cdk.Duration.hours(4),
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

        // Auto-register with VAMS
        if (
            props.config.app.pipelines.useConversionCoordinateTransform?.autoRegisterWithVAMS ===
            true
        ) {
            const currentTimestamp = new Date().toISOString();

            const importFunction = lambda.Function.fromFunctionArn(
                this,
                "ImportFunction",
                `arn:${ServiceHelper.Partition()}:lambda:${region}:${account}:function:${
                    props.importGlobalPipelineWorkflowFunctionName
                }`
            );

            const importProvider = new cr.Provider(this, "ImportProvider", {
                onEventHandler: importFunction,
            });

            new cdk.CustomResource(this, "CoordinateTransformPipelineWorkflow", {
                serviceToken: importProvider.serviceToken,
                properties: {
                    timestamp: currentTimestamp,
                    pipelineId: "conversion-coordinate-transform",
                    pipelineDescription:
                        "Coordinate Transform Pipeline - Reprojects E57, LAS, LAZ, and PLY point clouds between coordinate reference systems",
                    pipelineType: "standardFile",
                    pipelineExecutionType: "Lambda",
                    assetType: ".all",
                    outputType: ".laz",
                    waitForCallback: "Enabled",
                    lambdaName: vamsExecuteFunction.functionName,
                    taskTimeout: "14400",
                    taskHeartbeatTimeout: "",
                    inputParameters: JSON.stringify({
                        sourceCrs: "EPSG:4326",
                        targetCrs: "EPSG:27700",
                        outputFormats: ["laz"],
                    }),
                    workflowId: "conversion-coordinate-transform",
                    workflowDescription:
                        "Coordinate transformation for point cloud data between CRS systems",
                    autoTriggerOnFileExtensionsUpload:
                        props.config.app.pipelines.useConversionCoordinateTransform
                            ?.autoRegisterAutoTriggerOnFileUpload === true
                            ? ".e57,.las,.laz,.ply"
                            : "",
                },
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

        NagSuppressions.addResourceSuppressions(
            stateMachine,
            [
                {
                    id: "AwsSolutions-SF1",
                    reason: "CloudWatch logging for Step Functions state machine will be added in future iteration. Pipeline errors are captured via pipelineEnd Lambda.",
                },
                {
                    id: "AwsSolutions-SF2",
                    reason: "X-Ray tracing for Step Functions state machine will be added in future iteration.",
                },
            ],
            true
        );
    }
}
