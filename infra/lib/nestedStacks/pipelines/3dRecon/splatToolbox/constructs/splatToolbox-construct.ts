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
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { SqsSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import { SqsEventSource } from "aws-cdk-lib/aws-lambda-event-sources";
import { LambdaSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import * as cdk from "aws-cdk-lib";
import { Duration, Stack, Names, NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import {
    buildConstructPipelineFunction,
    buildOpenPipelineFunction,
    buildVamsExecuteSplatToolboxPipelineFunction,
    buildSqsExecuteSplatToolboxPipelineFunction,
    buildPipelineEndFunction,
} from "../lambdaBuilder/splatToolboxFunctions";
import { BatchGpuPipelineConstruct } from "../../../constructs/batch-gpu-pipeline";
import { NagSuppressions } from "cdk-nag";
import { CfnOutput } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as ServiceHelper from "../../../../../helper/service-helper";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import { Service } from "../../../../../helper/service-helper";
import * as Config from "../../../../../../config/config";
import { generateUniqueNameHash } from "../../../../../helper/security";
import { kmsKeyPolicyStatementGenerator } from "../../../../../helper/security";
import * as cr from "aws-cdk-lib/custom-resources";
import { execSync } from "child_process";
import * as fs from "fs";
import * as os from "os";

export interface SplatToolboxConstructProps extends cdk.StackProps {
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
const defaultProps: Partial<SplatToolboxConstructProps> = {
    //stackName: "",
    //env: {},
};

export class SplatToolboxConstruct extends Construct {
    public pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: SplatToolboxConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;

        const splatGitHubRepoLink =
            "https://github.com/aws-solutions-library-samples/guidance-for-open-source-3d-reconstruction-toolbox-for-gaussian-splats-on-aws.git";
        const splatGitHubRepoCommitHash = "0200c497584f511e54a129cbd1a783df98aeb4b2";

        // Download and Sync splat toolbox repository container files
        this.syncSplatToolboxContainer(splatGitHubRepoLink, splatGitHubRepoCommitHash);

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
                containerExecutionCommand: ["python", "__main__.py"],
                batchJobDefinitionName: `SplatToolboxGpuJob-${
                    props.config.name + "_" + props.config.app.baseStackName
                }`,
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
                    INPUT_PARAMETERS: sfn.JsonPath.stringAt("$.inputParameters"),
                    INPUT_METADATA: sfn.JsonPath.stringAt("$.inputMetadata"),
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

        // Create custom resource to automatically register pipeline and workflow
        if (props.config.app.pipelines.useSplatToolbox.autoRegisterWithVAMS === true) {
            const importFunction = lambda.Function.fromFunctionArn(
                this,
                "ImportFunction",
                `arn:aws:lambda:${region}:${account}:function:${props.importGlobalPipelineWorkflowFunctionName}`
            );

            const importProvider = new cr.Provider(this, "ImportProvider", {
                onEventHandler: importFunction,
            });

            NagSuppressions.addResourceSuppressionsByPath(
                Stack.of(this),
                `/${this.toString()}/ImportProvider/framework-onEvent/ServiceRole/DefaultPolicy/Resource`,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "Custom resource provider requires wildcard permissions to invoke the import function with version qualifiers",
                        appliesTo: [
                            `Resource::arn:aws:lambda:${region}:${account}:function:<importGlobalPipelineWorkflow15C3C6ED>:*`,
                        ],
                    },
                ],
                true
            );
            // Register Splat Toolbox pipeline and workflow for Objects
            new cdk.CustomResource(this, "3dReconSplatToolboxObjectsPipelineWorkflow", {
                serviceToken: importProvider.serviceToken,
                properties: {
                    pipelineId: "3dRecon-splat-toolbox-objects",
                    pipelineDescription:
                        "3D Gaussian Splat Pipeline - Auto process images and videos into 3D splat objects - .zip (2D video), mov, .mp4 inputs",
                    pipelineType: "standardFile",
                    pipelineExecutionType: "Lambda",
                    assetType: ".all",
                    outputType: ".all",
                    waitForCallback: "Enabled", // Asynchronous pipeline
                    lambdaName: SplatToolboxPipelineVamsExecuteFunction.functionName,
                    taskTimeout: "28800", // 8 hours
                    taskHeartbeatTimeout: "",
                    inputParameters: "",
                    workflowId: "3dRecon-splat-toolbox-objects",
                    workflowDescription:
                        "3D Gaussian Splat Pipeline - Auto process images and 2D videos into 3D splat objects - .zip (2D video), mov, .mp4 inputs",
                },
            });

            // Register Splat Toolbox pipeline and workflow for Environment Generation
            new cdk.CustomResource(this, "3dReconSplatToolboxEnvironmentPipelineWorkflow", {
                serviceToken: importProvider.serviceToken,
                properties: {
                    pipelineId: "3dRecon-splat-toolbox-environments-360",
                    pipelineDescription:
                        "3D Gaussian Splat Pipeline - Auto process 360 videos into 3D splat objects - .zip (360 video), mov, .mp4 inputs",
                    pipelineType: "standardFile",
                    pipelineExecutionType: "Lambda",
                    assetType: ".all",
                    outputType: ".all",
                    waitForCallback: "Enabled", // Asynchronous pipeline
                    lambdaName: SplatToolboxPipelineVamsExecuteFunction.functionName,
                    taskTimeout: "172800", // 48 hours
                    taskHeartbeatTimeout: "",
                    inputParameters: JSON.stringify({ SPHERICAL_CAMERA: true }),
                    workflowId: "3dRecon-splat-toolbox-environments-360",
                    workflowDescription:
                        "3D Gaussian Splat Pipeline - Auto process 360 videos into 3D splat objects - .zip (360 video), mov, .mp4 inputs",
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
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-SQS3",
                    reason: "Intended not to use DLQs for these types of SQS events. Re-drives should come from re-executing workflows.",
                },
            ],
            true
        );

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

    private syncSplatToolboxContainer(gitHubLink: string, gitHubCommitHash: string): void {
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

            // Check if Dockerfile already exists - if so, skip the entire sync process
            const dockerfilePath = path.join(targetDir, "Dockerfile");
            if (fs.existsSync(dockerfilePath)) {
                console.log(
                    "Splat Toolbox already exists in target pipeline directory. Skipping repository sync."
                );
                return;
            }

            console.log("Downloading/Syncing Splat Toolbox repository...");
            const tempDir = path.join(os.tmpdir(), "splat-toolbox-repo");

            if (fs.existsSync(tempDir)) {
                fs.rmSync(tempDir, { recursive: true, force: true });
            }

            execSync(`git clone ${gitHubLink} "${tempDir}"`, { stdio: "inherit" });
            execSync(`git -C "${tempDir}" checkout ${gitHubCommitHash}`, { stdio: "inherit" });

            const sourceDir = path.join(tempDir, "source", "container");
            if (fs.existsSync(sourceDir)) {
                console.log(`Copying from ${sourceDir} to ${targetDir}`);
                if (!fs.existsSync(targetDir)) {
                    fs.mkdirSync(targetDir, { recursive: true });
                }
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

                    // Add COPY for __main__.py if not already present
                    if (!dockerfileContent.includes("COPY ./__main__.py")) {
                        // Find the COPY ./src/main.py line and add __main__.py before it
                        dockerfileContent = dockerfileContent.replace(
                            /(COPY \.\/src\/main\.py)/,
                            "COPY ./__main__.py                                                  ${CODE_PATH}\n$1"
                        );
                        fs.writeFileSync(dockerfilePath, dockerfileContent);
                        console.log("Added __main__.py to Dockerfile");
                    }
                }
            }

            fs.rmSync(tempDir, { recursive: true, force: true });
            console.log("Repository sync completed successfully");
        } catch (error) {
            console.warn("Repository sync failed, continuing with existing files:", error);
        }
    }
}
