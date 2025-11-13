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
    buildVamsExecuteMetadata3dLabelingPipelineFunction,
    buildMetadataGenerationPipelineFunction,
    buildPipelineEndFunction,
} from "../lambdaBuilder/metadata3dLabelingFunctions";
import { BatchFargatePipelineConstruct } from "../../../constructs/batch-fargate-pipeline";
import { NagSuppressions } from "cdk-nag";
import { CfnOutput } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { Service } from "../../../../../helper/service-helper";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import * as Config from "../../../../../../config/config";
import { generateUniqueNameHash } from "../../../../../helper/security";
import { kmsKeyPolicyStatementGenerator } from "../../../../../helper/security";
import { layerBundlingCommand } from "../../../../../helper/lambda";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cr from "aws-cdk-lib/custom-resources";

export interface Metadata3dLabelingConstructProps extends cdk.StackProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowFunctionName: string;
}

/**
 * Default input properties
 */
const defaultProps: Partial<Metadata3dLabelingConstructProps> = {
    //stackName: "",
    //env: {},
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
export class Metadata3dLabelingConstruct extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: Metadata3dLabelingConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;

        //Lambda layer resource
        //Deploy Metadata Generation Lambda Layer
        const lambdaMetadata3dLabelingLayer = new lambda.LayerVersion(
            this,
            "VAMSMetadata3dLabelingLayer",
            {
                layerVersionName: "vams_layer_genaimetadatalabeling",
                code: lambda.Code.fromAsset(
                    "../backendPipelines/genAi/Metadata3dLabeling/lambdaLayer",
                    {
                        bundling: {
                            image: cdk.DockerImage.fromBuild("./config/docker", {
                                file: "Dockerfile-customDependencyBuildConfig",
                                buildArgs: {
                                    IMAGE: Config.LAMBDA_PYTHON_RUNTIME.bundlingImage.image,
                                },
                            }),
                            user: "root",
                            command: ["bash", "-c", layerBundlingCommand()],
                        },
                    }
                ),
                compatibleRuntimes: [Config.LAMBDA_PYTHON_RUNTIME],
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            }
        );

        /**
         * Batch Resources
         */
        const inputBucketPolicy = new iam.PolicyDocument({
            statements: [
                // Add permissions for all asset buckets from the global array
                ...s3AssetBuckets.getS3AssetBucketRecords().map((record) => {
                    const prefix = record.prefix || "/";
                    // Ensure the prefix ends with a slash for proper path construction
                    const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";

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
                            `${record.bucket.bucketArn}${normalizedPrefix}*`,
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

        const containerExecutionRole = new iam.Role(
            this,
            "Metadata3dLabelingContainerExecutionRole",
            {
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
            }
        );

        const containerJobRole = new iam.Role(this, "Metadata3dLabelingContainerJobRole", {
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

        /**
         * AWS Batch Job Definition & Compute Env for Blender Image Renderer
         */
        const blenderRendererBatchPipeline = new BatchFargatePipelineConstruct(
            this,
            "BatchFargatePipeline_BlenderRenderer",
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
                    "genAi",
                    "Metadata3dLabeling",
                    "container"
                ),
                dockerfileName: "Dockerfile_BlenderRenderer",
                batchJobDefinitionName: "Metadata3dLabelingJob_BlenderRenderer",
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

        //Build Lambda Function for Metadata Generation
        //TODO: Add VPCe for Bedrock/Rekognition
        const metadataGenerationPipelineFunction = buildMetadataGenerationPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            lambdaMetadata3dLabelingLayer,
            props.storageResources.s3.assetAuxiliaryBucket,
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

        // error handler passthrough - Blender Renderer Batch
        const handleBlenderRendererError = new sfn.Pass(this, "HandleBlenderRendererError", {
            resultPath: "$",
        }).next(pipeLineEndTask);

        // error handler passthrough - Metadata Generation Lambda
        const handleMetadataGenerationError = new sfn.Pass(this, "HandleMetadataGenerationError", {
            resultPath: "$",
        }).next(pipeLineEndTask);

        //Lambda Function step function task for metadataGeneration
        const metadataGenerationLambdaFunctionTask = new tasks.LambdaInvoke(
            this,
            "MetadataGenerationLambdaFunctionTask",
            {
                lambdaFunction: metadataGenerationPipelineFunction,
                inputPath: "$",
                outputPath: "$.Payload",
            }
        )
            .addCatch(handleMetadataGenerationError, {
                resultPath: "$.error",
            })
            .next(pipeLineEndTask);

        // batch job Blender Renderer
        const blenderRendererBatchJob = new tasks.BatchSubmitJob(this, "BlenderRendererBatchJob", {
            jobName: sfn.JsonPath.stringAt("$.jobName"),
            jobDefinitionArn: blenderRendererBatchPipeline.batchJobDefinition.jobDefinitionArn,
            jobQueueArn: blenderRendererBatchPipeline.batchJobQueue.jobQueueArn,
            containerOverrides: {
                command: [...sfn.JsonPath.listAt("$.definition")],
                environment: {
                    TASK_TOKEN: sfn.JsonPath.taskToken,
                    AWS_REGION: region,
                },
            },
        })
            .addCatch(handleBlenderRendererError, {
                resultPath: "$.error",
            })
            .next(metadataGenerationLambdaFunctionTask);

        /**
         * SFN Definition
         */
        const sfnPipelineDefinition = sfn.Chain.start(
            constructPipelineTask.next(blenderRendererBatchJob)
        );

        /**
         * CloudWatch Log Group
         */
        const stateMachineLogGroup = new logs.LogGroup(
            this,
            "Metadata3dLabelingProcessing-StateMachineLogGroup",
            {
                logGroupName:
                    "/aws/vendedlogs/VAMSStateMachine-Metadata3dLabelingPipeline" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "Metadata3dLabelingProcessing-StateMachineLogGroup",
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
            "Metadata3dLabelingProcessing-StateMachine",
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
         * Lambda Resources
         */

        //Build Lambda Pipeline Resources to Open the Pipeline
        const allowedInputFileExtensions = ".glb,.fbx,.obj,.stl,.ply,.usd,.dae,.abc";
        const openPipelineFunction = buildOpenPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources.s3.assetAuxiliaryBucket,
            pipelineStateMachine,
            allowedInputFileExtensions,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.storageResources.encryption.kmsKey
        );

        //Build Lambda VAMS Execution Function (as an optional pipeline execution action)
        const Metadata3dLabelingPipelineExecuteFunction =
            buildVamsExecuteMetadata3dLabelingPipelineFunction(
                this,
                props.lambdaCommonBaseLayer,
                props.storageResources.s3.assetAuxiliaryBucket,
                openPipelineFunction,
                props.config,
                props.vpc,
                props.pipelineSubnets,
                props.storageResources.encryption.kmsKey
            );

        //Output VAMS Pipeline Execution Function name
        new CfnOutput(this, "Metadata3dLabelingLambdaExecutionFunctionName", {
            value: Metadata3dLabelingPipelineExecuteFunction.functionName,
            description: "The Metadata 3D Labeling Lambda Function Name to use in a VAMS Pipeline",
        });
        this.pipelineVamsLambdaFunctionName =
            Metadata3dLabelingPipelineExecuteFunction.functionName;

        // Create custom resource to automatically register pipeline and workflow
        if (props.config.app.pipelines.useGenAiMetadata3dLabeling.autoRegisterWithVAMS === true) {
            const importFunction = lambda.Function.fromFunctionArn(
                this,
                "ImportFunction",
                `arn:aws:lambda:${region}:${account}:function:${props.importGlobalPipelineWorkflowFunctionName}`
            );

            const importProvider = new cr.Provider(this, "ImportProvider", {
                onEventHandler: importFunction,
            });
            const currentTimestamp = new Date().toISOString();

            // Register GLB metadata labeling pipeline and workflow
            new cdk.CustomResource(this, "GenAiMetadata3dLabelingGlbPipelineWorkflow", {
                serviceToken: importProvider.serviceToken,
                properties: {
                    timestamp: currentTimestamp,
                    pipelineId: "genai-metadata-3d-labeling-obj-glb-fbx-ply-stl-usd",
                    pipelineDescription:
                        "GenAI 3D Metadata Labeling Pipeline (Asset-level metadata generation from file) - 3D Mesh files using Blender Image Extraction. Supported Input Formats: OBJ, GLB/GLTF, FBX, ABC, DAE, PLY, STL, USD",
                    pipelineType: "standardFile",
                    pipelineExecutionType: "Lambda",
                    assetType: ".all",
                    outputType: ".all",
                    waitForCallback: "Enabled", // Asynchronous pipeline
                    lambdaName: Metadata3dLabelingPipelineExecuteFunction.functionName,
                    taskTimeout: "18000", // 5 hours
                    taskHeartbeatTimeout: "",
                    inputParameters: JSON.stringify({
                        includeAllAssetFileHierarchyFiles: "True",
                        seedMetadataGenerationWithInputMetadata: "True",
                    }),
                    workflowId: "genai-metadata-3d-labeling-obj-glb-fbx-ply-stl-usd",
                    workflowDescription:
                        "GenAI 3D Metadata Labeling Pipeline (Asset-level metadata generation from file) - 3D Mesh files using Blender Image Extraction. Supported Input Formats: OBJ, GLB/GLTF, FBX, ABC, DAE, PLY, STL, USD",
                },
            });

            //Nag supression
            NagSuppressions.addResourceSuppressions(
                importProvider,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "* Wildcard permissions needed for pipelineWorkflow lambda import and execution for custom resource",
                    },
                ],
                true
            );
        }

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
                            regex: "^Resource::.*Metadata3dLabelingProcessing-StateMachine/Role/.*/g",
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
                            regex: "^Resource::.*vamsExecuteGenAiMetadata3dLabelingPipeline/ServiceRole/.*/g",
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

        // NagSuppressions.addResourceSuppressionsByPath(
        //     Stack.of(this),
        //     `/${this.toString()}/BatchFargatePipeline_BlenderRenderer/PipelineBatchComputeEnvironment/Resource-Service-Instance-Role/Resource`,
        //     [
        //         {
        //             id: "AwsSolutions-IAM4",
        //             reason: "The IAM role for AWS Batch Compute Environment uses AWSBatchServiceRole",
        //             appliesTo: [
        //                 "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSBatchServiceRole",
        //             ],
        //         },
        //     ],
        //     true
        // );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/Metadata3dLabelingProcessing-StateMachine/Role/DefaultPolicy/Resource`,
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
            `/${this.toString()}/vamsExecuteGenAiMetadata3dLabelingPipeline/ServiceRole/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "vamsExecuteGenAiMetadata3dLabelingPipeline requires AWS Managed Policies, AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole",
                    appliesTo: [
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                        "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "vamsExecuteGenAiMetadata3dLabelingPipeline uses default policy that contains wildcard",
                    appliesTo: ["Resource::*"],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "vamsExecuteGenAiMetadata3dLabelingPipeline uses default policy that contains wildcard",
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
