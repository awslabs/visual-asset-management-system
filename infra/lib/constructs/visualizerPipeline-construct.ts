/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { storageResources } from "./../storage-builder";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as iam from "aws-cdk-lib/aws-iam";
import * as path from "path";
import { LambdaSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import * as cdk from "aws-cdk-lib";
import { Duration, Stack } from "aws-cdk-lib";
import { Construct } from "constructs";
import {
    buildConstructPipelineFunction,
    buildOpenPipelineFunction,
    buildExecuteVisualizerPCPipelineFunction,
    buildPipelineEndFunction,
} from "../lambdaBuilder/visualizerPipelineFunctions";
import { BatchFargatePipelineConstruct } from "./nested/batch-fargate-pipeline";
import { NagSuppressions } from "cdk-nag";
import { CfnOutput } from "aws-cdk-lib";

export interface VisualizationPipelineConstructProps extends cdk.StackProps {
    storage: storageResources;
    vpc: ec2.Vpc;
    visualizerPipelineSubnets: ec2.ISubnet[];
    visualizerPipelineSecurityGroups: ec2.SecurityGroup[];
}

/**
 * Default input properties
 */
const defaultProps: Partial<VisualizationPipelineConstructProps> = {
    stackName: "",
    env: {},
};

/**
 * Deploys a specific batch to ECS workflow for a particular container image for the website visualizer files
 * Creates:
 * - SNS
 * - SFN
 * - Batch
 * - ECR Image
 * - ECS
 * - IAM Roles / Policy Documents for permissions to S3 / Lambda
 * On redeployment, will automatically invalidate the CloudFront distribution cache
 */
export class VisualizationPipelineConstruct extends Construct {
    constructor(parent: Construct, name: string, props: VisualizationPipelineConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;

        const vpcSubnets = props.vpc.selectSubnets({
            subnets: props.visualizerPipelineSubnets,
        });

        /**
         * Batch Resources
         */
        const inputBucketPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    resources: [
                        props.storage.s3.assetBucket.bucketArn,
                        `${props.storage.s3.assetBucket.bucketArn}/*`,
                    ],
                }),
                new iam.PolicyStatement({
                    actions: ["s3:ListBucket"],
                    resources: [props.storage.s3.assetBucket.bucketArn],
                }),
            ],
        });

        const outputBucketPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    resources: [
                        props.storage.s3.assetVisualizerBucket.bucketArn,
                        `${props.storage.s3.assetVisualizerBucket.bucketArn}/*`,
                    ],
                }),
                new iam.PolicyStatement({
                    actions: ["s3:ListBucket"],
                    resources: [props.storage.s3.assetVisualizerBucket.bucketArn],
                }),
            ],
        });

        const stateTaskPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
                    resources: [`arn:aws:states:${region}:${account}:*`],
                }),
            ],
        });

        const containerExecutionRole = new iam.Role(
            this,
            "VisualizerPipelineContainerExecutionRole",
            {
                assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
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
            }
        );

        const containerJobRole = new iam.Role(this, "VisualizerPipelineContainerJobRole", {
            assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
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

        /**
         * AWS Batch Job Definition & Compute Env for PDAL Container
         */
        const pdalBatchPipeline = new BatchFargatePipelineConstruct(
            this,
            "BatchFargatePipeline_PDAL",
            {
                vpc: props.vpc,
                subnets: props.visualizerPipelineSubnets,
                securityGroups: props.visualizerPipelineSecurityGroups,
                jobRole: containerJobRole,
                executionRole: containerExecutionRole,
                imageAssetPath: path.join(
                    "..",
                    "..",
                    "..",
                    "..",
                    "backendVisualizerPipelines",
                    "pc"
                ),
                dockerfileName: "Dockerfile_PDAL",
                batchJobDefinitionName: "VisualizerPipelineJob_PDAL",
            }
        );

        /**
         * AWS Batch Job Definition & Compute Env for Potree Container
         */
        const potreeBatchPipeline = new BatchFargatePipelineConstruct(
            this,
            "BatchFargatePipeline_Potree",
            {
                vpc: props.vpc,
                subnets: props.visualizerPipelineSubnets,
                securityGroups: props.visualizerPipelineSecurityGroups,
                jobRole: containerJobRole,
                executionRole: containerExecutionRole,
                imageAssetPath: path.join(
                    "..",
                    "..",
                    "..",
                    "..",
                    "backendVisualizerPipelines",
                    "pc"
                ),
                dockerfileName: "Dockerfile_Potree",
                batchJobDefinitionName: "VisualizerPipelineJob_Potree",
            }
        );

        /**
         * SFN States
         */

        // connect pipeline lambda function
        // transforms data input for AWS Batch
        const constructPipelineFunction = buildConstructPipelineFunction(
            this,
            props.vpc,
            props.visualizerPipelineSubnets,
            props.visualizerPipelineSecurityGroups
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
            cause: "AWS Batch Job Failed",
            error: "Batch Job returned FAIL",
        });

        // end state evaluation: success or failure
        const endStatesChoice = new sfn.Choice(this, "EndStatesChoice")
            .when(sfn.Condition.isPresent("$.result.error"), failState)
            .otherwise(successState);

        // final lambda called on pipeline end to close out the statemachine run
        const pipelineEndFunction = buildPipelineEndFunction(
            this,
            props.storage.s3.assetBucket,
            props.storage.s3.assetVisualizerBucket,
            props.vpc,
            props.visualizerPipelineSubnets,
            props.visualizerPipelineSecurityGroups
        );

        const pipeLineEndTask = new tasks.LambdaInvoke(this, "PipelineEndTask", {
            lambdaFunction: constructPipelineFunction,
            inputPath: "$",
            outputPath: "$",
        }).next(endStatesChoice);

        // error handler passthrough - PDAL Batch
        const handlePdalError = new sfn.Pass(this, "HandlePdalError", {
            resultPath: "$",
        }).next(pipeLineEndTask);

        // error handler passthrough - Potree Batch
        const handlePotreeError = new sfn.Pass(this, "HandlePotreeError", {
            resultPath: "$",
        }).next(pipeLineEndTask);

        // batch job Potree Converter
        const potreeConverterBatchJob = new tasks.BatchSubmitJob(this, "PotreeConverterBatchJob", {
            jobName: sfn.JsonPath.stringAt("$.jobName"),
            jobDefinitionArn: potreeBatchPipeline.batchJobDefinition.jobDefinitionArn,
            jobQueueArn: potreeBatchPipeline.batchJobQueue.jobQueueArn,
            containerOverrides: {
                command: [...sfn.JsonPath.listAt("$.pipeline.definition")],
                environment: {
                    TASK_TOKEN: sfn.JsonPath.taskToken,
                },
            },
        })
            .addCatch(handlePotreeError, {
                resultPath: "$.error",
            })
            .next(pipeLineEndTask);

        // batch job PDAL
        const pdalConverterBatchJob = new tasks.BatchSubmitJob(this, "PdalConverterBatchJob", {
            jobName: sfn.JsonPath.stringAt("$.jobName"),
            jobDefinitionArn: pdalBatchPipeline.batchJobDefinition.jobDefinitionArn,
            jobQueueArn: pdalBatchPipeline.batchJobQueue.jobQueueArn,
            containerOverrides: {
                command: [...sfn.JsonPath.listAt("$.pipeline.definition")],
                environment: {
                    TASK_TOKEN: sfn.JsonPath.taskToken,
                },
            },
        })
            .addCatch(handlePdalError, {
                resultPath: "$.error",
            })
            .next(potreeConverterBatchJob);

        /**
         * SFN Definition
         */
        const sfnPipelineDefinition = sfn.Chain.start(
            constructPipelineTask.next(
                new sfn.Choice(this, "FilePathExtensionCheck")
                    .when(
                        sfn.Condition.stringEquals("$.pipeline.type", "POTREE"),
                        potreeConverterBatchJob
                    )
                    .otherwise(pdalConverterBatchJob)
            )
        );

        /**
         * CloudWatch Log Group
         */
        const stateMachineLogGroup = new logs.LogGroup(
            this,
            "PointCloudVisualizerPipelineProcessing-StateMachineLogGroup",
            {
                logGroupName: "/aws/stateMachine-VizPipeline/",
                retention: logs.RetentionDays.ONE_WEEK,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            }
        );

        /**
         * SFN State Machine
         */
        const pipelineStateMachine = new sfn.StateMachine(
            this,
            "PointCloudVisualizerPipelineProcessing-StateMachine",
            {
                definitionBody: sfn.DefinitionBody.fromChainable(sfnPipelineDefinition),
                timeout: Duration.hours(5),
                logs: {
                    destination: stateMachineLogGroup,
                    includeExecutionData: true,
                    level: sfn.LogLevel.ALL,
                },
                tracingEnabled: true,
            }
        );

        /**
         * Lambda Resources & SNS Subscriptions
         */
        //Build Lambda VAMS Execution Function (as an optional pipeline execution action)
        const visualizerPCPipelineExecuteFunction = buildExecuteVisualizerPCPipelineFunction(
            this,
            props.storage.s3.assetBucket,
            props.storage.s3.assetVisualizerBucket,
            props.storage.sns.assetBucketObjectCreatedTopic
        );

        visualizerPCPipelineExecuteFunction.addToRolePolicy(
            new iam.PolicyStatement({
                actions: [
                    "kms:Decrypt",
                    "kms:DescribeKey",
                    "kms:Encrypt",
                    "kms:GenerateDataKey*",
                    "kms:ImportKeyMaterial",
                ],
                resources: [props.storage.sns.kmsTopicKey.keyArn],
            })
        );

        //Build Lambda Web Visualizer Pipeline Resources to Open the Pipeline through a SNS Topic Subscription
        const allowedInputFileExtensions = ".laz,.las,.e57";
        const openPipelineFunction = buildOpenPipelineFunction(
            this,
            props.storage.s3.assetBucket,
            props.storage.s3.assetVisualizerBucket,
            pipelineStateMachine,
            allowedInputFileExtensions,
            props.vpc,
            props.visualizerPipelineSubnets
        );

        //Add subscription to kick-off lambda function of pipeline (as the main pipeline execution action)
        props.storage.sns.assetBucketObjectCreatedTopic.addSubscription(
            new LambdaSubscription(openPipelineFunction, {
                filterPolicy: {
                    //Future TODO: If SNS Subscription String Filtering ever supports suffix matching, add a filter here for LAS/LAZ/E57 files to reduce calls to Lambda
                },
            })
        );

        //Output VAMS Pipeline Execution Function name
        new CfnOutput(this, "PCVisualizerLambdaExecutionFunctionName", {
            value: visualizerPCPipelineExecuteFunction.functionName,
            description:
                "The Point Cloud Visualizer Pipeline Lambda Function Name to use in a VAMS Pipeline",
            exportName: "PCVisualizerLambdaExecutionFunctionName",
        });

        //Nag Supressions
        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/VisualizerPipelineContainerExecutionRole/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The IAM role for ECS Container execution uses AWS Managed Policies",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/AWSXrayWriteOnlyAccess",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require access to objects in the DataBucket",
                    appliesTo: [
                        `Resource::arn:aws:states:${region}:${account}:*`,
                        {
                            regex: "/^Resource::<DataBucket.*.Arn>/\\*$/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/VisualizerPipelineContainerJobRole/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The IAM role for ECS Container execution uses AWS Managed Policies",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/AWSXrayWriteOnlyAccess",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "ECS Containers require access to objects in the DataBucket",
                    appliesTo: [
                        "Resource::*",
                        `Resource::arn:aws:states:${region}:${account}:*`,
                        {
                            regex: "/^Resource::<DataBucket.*.Arn>/\\*$/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/BatchFargatePipeline_PDAL/PipelineBatchComputeEnvironment/Resource-Service-Instance-Role/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The IAM role for AWS Batch Compute Environment uses AWSBatchServiceRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSBatchServiceRole",
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/BatchFargatePipeline_Potree/PipelineBatchComputeEnvironment/Resource-Service-Instance-Role/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The IAM role for AWS Batch Compute Environment uses AWSBatchServiceRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSBatchServiceRole",
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/PointCloudVisualizerPipelineProcessing-StateMachine/Role/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "PointCloudProcessingStateMachine uses default policy that contains wildcard",
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
            `/${this.toString()}/executeVisualizerPCPipeline/ServiceRole/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "executeVisualizerPCPipeline requires AWS Managed Policies, AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "executeVisualizerPCPipeline uses default policy that contains wildcard",
                    appliesTo: ["Resource::*"],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "executeVisualizerPCPipeline uses default policy that contains wildcard",
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
    }
}
