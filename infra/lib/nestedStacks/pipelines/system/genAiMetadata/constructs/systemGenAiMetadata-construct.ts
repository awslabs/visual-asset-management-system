/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
import { Duration, Stack, NestedStack, CfnOutput } from "aws-cdk-lib";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as ServiceHelper from "../../../../../helper/service-helper";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import { Service } from "../../../../../helper/service-helper";
import * as Config from "../../../../../../config/config";
import {
    NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
    generateUniqueNameHash,
    kmsKeyPolicyStatementGenerator,
    grantExternalAssetBucketKmsKeys,
} from "../../../../../helper/security";
import { BatchFargatePipelineConstruct } from "../../../constructs/batch-fargate-pipeline";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";
import {
    SYSTEM_GENAI_METADATA_PIPELINE_ID,
    SYSTEM_GENAI_METADATA_WORKFLOW_ID,
    SYSTEM_WORKFLOW_DATABASE_ID,
} from "../../../../../../common/systemPipelines";
import {
    buildBlenderRenderFunction,
    buildConstructPipelineFunction,
    buildGenerateEmbeddingFunction,
    buildGenerateMetadataFunction,
    buildMediaExtractFunction,
    buildOpenPipelineFunction,
    buildPipelineEndFunction,
    buildRender3dFunction,
    buildSegmentAnalyzeFunction,
    buildVamsExecuteSystemGenAiMetadataFunction,
} from "../lambdaBuilder/systemGenAiMetadataFunctions";

export interface SystemGenAiMetadataConstructProps extends cdk.StackProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowV2FunctionName: string;
}

/**
 * Default input properties
 */
const defaultProps: Partial<SystemGenAiMetadataConstructProps> = {};

/**
 * The input extensions the openPipeline handler admits: every file class the pipeline analyses,
 * generated from the classifier's allow list (backendPipelines/system/genAiMetadata/lambda/fileClassifier.py).
 */
export const allowedInputFileExtensions =
    ".3dm,.3ds,.3mf,.aac,.amf,.asm,.avi,.bim,.brep,.catpart,.catproduct,.cfg,.csv,.dae,.docx,.e57,.fbx,.fcs,.flac,.flv,.gif,.glb,.gltf,.htm,.html,.iam,.ifc,.ifczip,.iges,.igs,.inf,.ini,.ipt,.ipynb,.jpeg,.jpg,.js,.json,.jt,.las,.laz,.lcc,.log,.m4a,.m4v,.md,.mkv,.mov,.mp3,.mp4,.obj,.off,.ogg,.par,.pdf,.ply,.png,.pptx,.prt,.ps1,.py,.sh,.sldasm,.sldprt,.sog,.splat,.spz,.sql,.step,.stl,.stp,.svg,.toml,.ts,.txt,.usd,.usda,.usdc,.usdz,.wav,.webm,.wmv,.wrl,.x_b,.x_t,.xlsx,.xml,.yaml,.yml";

/** The Fargate render branch's attempt budget; the entry module reports it as the remaining time. */
const FARGATE_RENDER_ATTEMPT_DURATION = cdk.Duration.hours(4);

/**
 * The SYSTEM - GenAI Metadata Generation pipeline: a state machine that classifies one uploaded file,
 * renders or extracts a view of it on the branch its class selects (Blender, the 3D render image, the
 * media image, or an AWS Batch Fargate job for files above the Lambda limits), analyses the result
 * with Amazon Bedrock, and, when vector search is enabled, publishes a per-file-version embedding
 * followed by one embedding per time window or text chunk of the file through a Distributed Map.
 * Creates:
 * - SFN state machine and its vended log group
 * - six zip Lambdas and four container-image Lambdas
 * - the state machine role's deployment-key grant for the Map's CMK-encrypted items and results
 * - optionally a Batch Fargate job definition, queue and compute environment
 * - the VAMS pipeline/workflow/template registration
 */
export class SystemGenAiMetadataConstruct extends NestedStack {
    public pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: SystemGenAiMetadataConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;
        const pipelineConfig = props.config.app.pipelines.useSystemGenAiMetadata;
        const kmsKey = props.storageResources.encryption.kmsKey;
        const assetAuxiliaryBucket = props.storageResources.s3.assetAuxiliaryBucket;

        /**
         * CloudWatch Log Group
         */
        const stateMachineLogGroup = new logs.LogGroup(
            this,
            "SystemGenAiMetadataProcessing-StateMachineLogGroup",
            {
                logGroupName:
                    "/aws/vendedlogs/VAMSStateMachine-SystemGenAiMetadata" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "SystemGenAiMetadataProcessing-StateMachineLogGroup",
                        10
                    ),
                encryptionKey: kmsKey,
                retention: logs.RetentionDays.ONE_YEAR,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            }
        );

        /**
         * Lambda task functions
         */
        const constructPipelineFunction = buildConstructPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            kmsKey
        );
        const generateMetadataFunction = buildGenerateMetadataFunction(
            this,
            props.lambdaCommonBaseLayer,
            assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            kmsKey
        );
        const generateEmbeddingFunction = buildGenerateEmbeddingFunction(
            this,
            props.lambdaCommonBaseLayer,
            assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            props.storageResources.eventBridge.orchestrationBus,
            kmsKey
        );
        const pipelineEndFunction = buildPipelineEndFunction(
            this,
            props.lambdaCommonBaseLayer,
            assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            kmsKey
        );
        const blenderRenderFunction = buildBlenderRenderFunction(
            this,
            assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            kmsKey
        );
        const render3dFunction = buildRender3dFunction(
            this,
            assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            kmsKey
        );
        const mediaExtractFunction = buildMediaExtractFunction(
            this,
            assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            kmsKey
        );
        const segmentAnalyzeFunction = buildSegmentAnalyzeFunction(
            this,
            assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            props.storageResources.eventBridge.orchestrationBus,
            kmsKey
        );

        /**
         * Fargate render branch (files above the Lambda limits), on the sub-flag only
         */
        let batchPipeline: BatchFargatePipelineConstruct | undefined;
        let containerExecutionRole: iam.Role | undefined;
        let containerJobRole: iam.Role | undefined;
        let renderLogGroup: logs.LogGroup | undefined;
        if (pipelineConfig.useFargateRenderer) {
            const inputBucketPolicy = new iam.PolicyDocument({
                statements: [
                    ...s3AssetBuckets.getS3AssetBucketRecords().map((record) => {
                        const prefix = record.prefix || "/";
                        // {bucketArn}/{prefix}* with the leading slash stripped so the separator
                        // after the bucket ARN is always present (root prefix yields {bucketArn}/*).
                        const normalizedPrefix = prefix.endsWith("/") ? prefix : prefix + "/";
                        const objectPrefix = normalizedPrefix.replace(/^\/+/, "");
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
                                `${record.bucket.bucketArn}/${objectPrefix}*`,
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
                            assetAuxiliaryBucket.bucketArn,
                            `${assetAuxiliaryBucket.bucketArn}/*`,
                        ],
                    }),
                    new iam.PolicyStatement({
                        actions: ["s3:ListBucket"],
                        resources: [assetAuxiliaryBucket.bucketArn],
                    }),
                ],
            });
            if (kmsKey) {
                inputBucketPolicy.addStatements(kmsKeyPolicyStatementGenerator(kmsKey));
                outputBucketPolicy.addStatements(kmsKeyPolicyStatementGenerator(kmsKey));
            }

            const managedPolicies = [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
                iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
            ];
            containerExecutionRole = new iam.Role(
                this,
                "SystemGenAiMetadataContainerExecutionRole",
                {
                    assumedBy: Service("ECS_TASKS").Principal,
                    inlinePolicies: {
                        InputBucketPolicy: inputBucketPolicy,
                        OutputBucketPolicy: outputBucketPolicy,
                    },
                    managedPolicies: managedPolicies,
                }
            );
            containerJobRole = new iam.Role(this, "SystemGenAiMetadataContainerJobRole", {
                assumedBy: Service("ECS_TASKS").Principal,
                inlinePolicies: {
                    InputBucketPolicy: inputBucketPolicy,
                    OutputBucketPolicy: outputBucketPolicy,
                },
                managedPolicies: managedPolicies,
            });
            // Cross-account encrypted asset buckets (no-op when no external keys are configured).
            grantExternalAssetBucketKmsKeys(containerJobRole);

            // The container's stdout/stderr. A named vended group under the /aws/vendedlogs/Pipelines/
            // prefix the execution-service role is granted to read; KMS-encrypted and retained for a
            // year, unlike Batch's default group.
            renderLogGroup = new logs.LogGroup(this, "SystemGenAiMetadataRenderBatchJobLogGroup", {
                logGroupName:
                    "/aws/vendedlogs/Pipelines/SystemGenAiMetadataRender" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "SystemGenAiMetadataRenderBatchJobLogGroup",
                        10
                    ),
                encryptionKey: kmsKey,
                retention: logs.RetentionDays.ONE_YEAR,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            });

            batchPipeline = new BatchFargatePipelineConstruct(
                this,
                "BatchFargatePipeline_SystemGenAiMetadata",
                {
                    attemptDuration: FARGATE_RENDER_ATTEMPT_DURATION,
                    config: props.config,
                    vpc: props.vpc,
                    subnets: props.pipelineSubnets,
                    securityGroups: props.pipelineSecurityGroups,
                    jobRole: containerJobRole,
                    executionRole: containerExecutionRole,
                    logGroup: renderLogGroup,
                    imageAssetPath: path.join(
                        "..",
                        "..",
                        "..",
                        "..",
                        "..",
                        "backendPipelines",
                        "preview",
                        "3dThumbnail",
                        "container"
                    ),
                    dockerfileName: "Dockerfile",
                    ephemeralStorageGiB: 200,
                    batchJobDefinitionName:
                        "SystemGenAiMetadataRenderJob" +
                        props.config.name +
                        "_" +
                        props.config.app.baseStackName,
                }
            );
        }

        /**
         * SFN States. Every Lambda task replaces the state with its Payload (the branch-task
         * contract: each handler returns the whole state merged with its own keys).
         */
        const invoke = (id: string, fn: cdk.aws_lambda.IFunction) =>
            new tasks.LambdaInvoke(this, id, { lambdaFunction: fn, outputPath: "$.Payload" });

        const constructPipelineTask = invoke("ConstructPipelineTask", constructPipelineFunction);
        const blenderRenderTask = invoke("BlenderRenderTask", blenderRenderFunction);
        const render3dTask = invoke("Render3dTask", render3dFunction);
        const mediaExtractTask = invoke("MediaExtractTask", mediaExtractFunction);
        const generateMetadataTask = invoke("GenerateMetadataTask", generateMetadataFunction);
        const generateEmbeddingTask = invoke("GenerateEmbeddingTask", generateEmbeddingFunction);
        const pipelineEndTask = invoke("PipelineEndTask", pipelineEndFunction);

        const noRenderPass = new sfn.Pass(this, "NoRenderPass", {
            comment: "No render branch applies to this file class; analysis reads attributes only.",
        });
        const renderDegradePass = new sfn.Pass(this, "RenderDegradePass", {
            comment:
                "The render branch faulted; analysis continues from the manifest without views.",
        });
        const embeddingSkippedPass = new sfn.Pass(this, "EmbeddingSkippedPass", {
            result: sfn.Result.fromString("SKIPPED"),
            resultPath: "$.embeddingStatus",
        });

        // The end states
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
        pipelineEndTask.next(endStatesChoice);

        // PipelineEndTask is the only state that reports on the parent workflow's callback token, so
        // every task failure is routed to it with the error alongside the state; resultPath keeps
        // externalSfnTaskToken in place.
        const handleError = (id: string) =>
            new sfn.Pass(this, id, { resultPath: "$" }).next(pipelineEndTask);
        const handleConstructPipelineError = handleError("HandleConstructPipelineError");
        const handleGenerateMetadataError = handleError("HandleGenerateMetadataError");
        const handleGenerateEmbeddingError = handleError("HandleGenerateEmbeddingError");
        constructPipelineTask.addCatch(handleConstructPipelineError, { resultPath: "$.error" });
        generateMetadataTask.addCatch(handleGenerateMetadataError, { resultPath: "$.error" });
        generateEmbeddingTask.addCatch(handleGenerateEmbeddingError, { resultPath: "$.error" });

        // A render fault degrades the analysis rather than failing the execution.
        for (const renderTask of [blenderRenderTask, render3dTask, mediaExtractTask]) {
            renderTask.addCatch(renderDegradePass, { resultPath: "$.renderError" });
        }
        renderDegradePass.next(generateMetadataTask);
        noRenderPass.next(generateMetadataTask);
        render3dTask.next(generateMetadataTask);
        mediaExtractTask.next(generateMetadataTask);

        // A USD scene Blender could not render falls back to the 3D render image.
        const usdFallbackChoice = new sfn.Choice(this, "UsdFallbackChoice")
            .when(
                sfn.Condition.and(
                    sfn.Condition.isPresent("$.fileClass"),
                    sfn.Condition.stringEquals("$.fileClass", "usd"),
                    sfn.Condition.isPresent("$.renderSkipped"),
                    sfn.Condition.stringEquals("$.renderSkipped", "error")
                ),
                render3dTask
            )
            .otherwise(generateMetadataTask);
        blenderRenderTask.next(usdFallbackChoice);

        // Embeddings follow a completed analysis only, and only when vector search is on.
        const vectorSearchChoice = new sfn.Choice(this, "VectorSearchChoice")
            .when(
                sfn.Condition.and(
                    sfn.Condition.isPresent("$.vectorSearchEnabled"),
                    sfn.Condition.booleanEquals("$.vectorSearchEnabled", true),
                    sfn.Condition.isPresent("$.analysisStatus"),
                    sfn.Condition.not(sfn.Condition.stringEquals("$.analysisStatus", "FAILED"))
                ),
                generateEmbeddingTask
            )
            .otherwise(embeddingSkippedPass);
        generateMetadataTask.next(vectorSearchChoice);
        embeddingSkippedPass.next(pipelineEndTask);

        /**
         * SFN States — video segments. One EXPRESS child execution per time window (or text chunk)
         * of the plan the media branch wrote, eight at a time; the Map's own result manifest lands
         * under the execution's auxiliary prefix, and an uncaught child fault is routed to
         * PipelineEndTask with the error alongside the state like every other task failure.
         */
        const handleVideoSegmentsError = handleError("HandleVideoSegmentsError");

        // The child: receives {segment, state} and returns {segmentKey, status, documentS3Location}
        // plus, when status is FAILED or SKIPPED, error — one of BedrockSegmentError,
        // BedrockGuardrailIntervened, SegmentPublishError, SegmentFramesUnavailable. The Map
        // tolerates no failed child, and a child is idempotent by document key and event, so a
        // fault the handler did not catch re-runs the window behind the L2's default Lambda
        // service-exception retrier before it can fail the Map.
        const segmentAnalyzeTask = invoke("SegmentAnalyzeTask", segmentAnalyzeFunction).addRetry({
            errors: [sfn.Errors.ALL],
            interval: Duration.seconds(10),
            maxAttempts: 2,
            backoffRate: 2,
        });

        // The items file and the result manifest are both in the auxiliary bucket, named statically
        // so the L2 scopes the role's S3 grants to it; the key and the prefix are the ones the media
        // branch wrote to the state. Zero tolerated failures is the Step Functions default, and the
        // CDK elides a literal 0, so neither ToleratedFailure* key is set.
        const videoSegmentMap = new sfn.DistributedMap(this, "VideoSegmentMap", {
            mapExecutionType: sfn.StateMachineType.EXPRESS,
            itemReader: new sfn.S3JsonItemReader({
                bucket: assetAuxiliaryBucket,
                key: sfn.JsonPath.stringAt("$.videoSegmentItemsKey"),
            }),
            itemSelector: {
                segment: sfn.JsonPath.stringAt("$$.Map.Item.Value"),
                state: sfn.JsonPath.entirePayload,
            },
            maxConcurrency: 8,
            label: "VideoSegments",
            resultWriter: new sfn.ResultWriter({
                bucket: assetAuxiliaryBucket,
                prefix: sfn.JsonPath.stringAt("$.videoSegmentResultsPrefix"),
            }),
            resultPath: "$.videoSegmentMapResult",
        });
        // The class renders ProcessorConfig.Mode DISTRIBUTED and mapExecutionType supplies the
        // ExecutionType; a ProcessorConfig passed here would be validated against and then ignored.
        videoSegmentMap.itemProcessor(segmentAnalyzeTask);
        videoSegmentMap.addCatch(handleVideoSegmentsError, { resultPath: "$.error" });
        videoSegmentMap.next(pipelineEndTask);

        // Segments are analysed only when a plan exists and the whole-file vector was published.
        const videoSegmentChoice = new sfn.Choice(this, "VideoSegmentChoice")
            .when(
                sfn.Condition.and(
                    sfn.Condition.isPresent("$.videoSegmentItemsS3Location"),
                    sfn.Condition.isPresent("$.embeddingStatus"),
                    sfn.Condition.stringEquals("$.embeddingStatus", "SUCCEEDED")
                ),
                videoSegmentMap
            )
            .otherwise(pipelineEndTask);
        generateEmbeddingTask.next(videoSegmentChoice);

        // The render branch constructPipeline selected for the file class and size.
        const renderBranchChoice = new sfn.Choice(this, "RenderBranchChoice")
            .when(sfn.Condition.stringEquals("$.renderBranch", "BLENDER"), blenderRenderTask)
            .when(sfn.Condition.stringEquals("$.renderBranch", "RENDER3D"), render3dTask)
            .when(sfn.Condition.stringEquals("$.renderBranch", "MEDIA"), mediaExtractTask);
        if (batchPipeline) {
            const fargateRenderJob = new tasks.BatchSubmitJob(this, "FargateRenderJob", {
                jobName: sfn.JsonPath.format(
                    "SystemGenAiMetadata-{}",
                    sfn.JsonPath.stringAt("$$.Execution.Name")
                ),
                jobDefinitionArn: batchPipeline.batchJobDefinition.jobDefinitionArn,
                jobQueueArn: batchPipeline.batchJobQueue.jobQueueArn,
                containerOverrides: {
                    // The image's entry point is `python3 -m preview_pipeline`; this argument selects
                    // the analysis branch entry module (preview_pipeline/analysis/batch_job.py).
                    command: ["analysisBatch"],
                    environment: {
                        ANALYSIS_STATE_JSON: sfn.JsonPath.jsonToString(sfn.JsonPath.objectAt("$")),
                        AWS_REGION: region,
                    },
                },
                resultPath: "$.fargateRenderResult",
            });
            fargateRenderJob.addCatch(renderDegradePass, { resultPath: "$.renderError" });
            fargateRenderJob.next(generateMetadataTask);
            renderBranchChoice.when(
                sfn.Condition.stringEquals("$.renderBranch", "FARGATE"),
                fargateRenderJob
            );
        }
        // constructPipeline emits NONE for every class without a branch, and FARGATE only when the
        // sub-flag is on, so the default arm is the only path for a branch that is not deployed.
        renderBranchChoice.otherwise(noRenderPass);
        constructPipelineTask.next(renderBranchChoice);

        /**
         * SFN State Machine
         */
        const pipelineStateMachine = new sfn.StateMachine(
            this,
            "SystemGenAiMetadataProcessing-StateMachine",
            {
                definitionBody: sfn.DefinitionBody.fromChainable(
                    sfn.Chain.start(constructPipelineTask)
                ),
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
         * The Distributed Map's own policy on the state machine role (the L2 writes it: StartExecution
         * on the machine, DescribeExecution/StopExecution/RedriveExecution on its executions) covers
         * the child executions, and the L2 grants the role the items-file read and the result-manifest
         * write on the auxiliary bucket. Both objects are CMK-encrypted, so the role also needs the
         * deployment key.
         */
        if (kmsKey) {
            pipelineStateMachine.addToRolePolicy(kmsKeyPolicyStatementGenerator(kmsKey));
        }

        // Stopping the state machine cancels the .sync Batch task, which requires terminating the
        // running job; the BatchSubmitJob task grants only batch:SubmitJob. DescribeJobs has no resource
        // type; job ids are generated at submit time, so TerminateJob is scoped to this account's jobs.
        if (batchPipeline) {
            pipelineStateMachine.addToRolePolicy(
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["batch:DescribeJobs"],
                    resources: ["*"],
                })
            );
            pipelineStateMachine.addToRolePolicy(
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["batch:TerminateJob"],
                    resources: [
                        `arn:${ServiceHelper.Partition()}:batch:${region}:${account}:job/*`,
                    ],
                })
            );
        }

        /**
         * Entry Lambdas
         */
        const openPipelineFunction = buildOpenPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            assetAuxiliaryBucket,
            pipelineStateMachine,
            allowedInputFileExtensions,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            props.storageResources.eventBridge.orchestrationBus,
            stateMachineLogGroup,
            // The Fargate render job's vended container group + job definition name, registered as
            // the FargateRenderJob state's log source; absent without the renderer sub-flag.
            batchPipeline && renderLogGroup
                ? {
                      jobDefinitionName: batchPipeline.batchJobDefinition.jobDefinitionName,
                      logGroup: renderLogGroup,
                  }
                : undefined,
            kmsKey
        );

        const vamsExecuteFunction = buildVamsExecuteSystemGenAiMetadataFunction(
            this,
            props.lambdaCommonBaseLayer,
            assetAuxiliaryBucket,
            openPipelineFunction,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.pipelineSecurityGroups,
            kmsKey
        );

        // Registration in the V2 pipeline/workflow/template tables under the system ids.
        if (pipelineConfig.autoRegisterWithVAMS === true) {
            new VamsSchemaRegistration(this, "SystemGenAiMetadataRegistration", {
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
                    "system",
                    "genAiMetadata",
                    "vamsSchema"
                ),
                resourceOverrides: {
                    lambdaName: vamsExecuteFunction.functionName,
                },
                idOverrides: {
                    pipelineId: SYSTEM_GENAI_METADATA_PIPELINE_ID,
                    pipelineDatabaseId: SYSTEM_WORKFLOW_DATABASE_ID,
                    workflowId: SYSTEM_GENAI_METADATA_WORKFLOW_ID,
                    workflowDatabaseId: SYSTEM_WORKFLOW_DATABASE_ID,
                },
                triggerEnabled: pipelineConfig.autoRegisterAutoTriggerOnFileUpload === true,
            });
        }

        new CfnOutput(this, "SystemGenAiMetadataLambdaExecutionFunctionName", {
            value: vamsExecuteFunction.functionName,
            description:
                "The SYSTEM GenAI Metadata Generation Pipeline Lambda Function Name to use in a VAMS Pipeline",
        });
        this.pipelineVamsLambdaFunctionName = vamsExecuteFunction.functionName;

        /**
         * Nag Suppressions
         */
        const reason =
            "Intended Solution. The pipeline lambda functions need appropriate access to S3.";
        for (const rolePath of [
            "SystemGenAiMetadataOpenPipeline/ServiceRole",
            "SystemGenAiMetadataPipelineEnd/ServiceRole",
            "VamsExecuteSystemGenAiMetadataPipeline/ServiceRole",
            "SystemGenAiMetadataProcessing-StateMachine/Role",
        ]) {
            NagSuppressions.addResourceSuppressions(
                this,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: reason,
                        appliesTo: [{ regex: `/^Resource::.*${rolePath}/.*/g` }],
                    },
                ],
                true
            );
        }

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/SystemGenAiMetadataProcessing-StateMachine/Role/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "The state machine role's default policy carries the wildcard permissions Step Functions needs to invoke the pipeline's Lambda functions by version and, with the Fargate renderer, to submit and track Batch jobs: batch:DescribeJobs supports no resource-level permissions and Batch job ids are generated at submit time, so cancelling the .sync job on StopExecution needs DescribeJobs on * and TerminateJob on job/*. The video-segment map reads its items file from, and writes its result manifest under, run-time prefixes of the CMK-encrypted auxiliary bucket, so its two S3 grants name the bucket with an object wildcard and the deployment key's data-key actions sit beside them",
                    appliesTo: [
                        "Resource::*",
                        "Action::kms:GenerateDataKey*",
                        "Action::kms:ReEncrypt*",
                        `Resource::arn:<AWS::Partition>:batch:${region}:${account}:job-definition/*`,
                        { regex: "/^Resource::arn:.*:batch:.*:job/\\*$/g" },
                        {
                            regex: "/^Resource::<.*Function.*.Arn>:.*$/g",
                        },
                        {
                            regex: "/^Action::s3:.*$/g",
                        },
                        {
                            regex: "/^Resource::arn:<AWS::Partition>:s3:::<.*>/\\*$/g",
                        },
                    ],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressionsByPath(
            Stack.of(this),
            `/${this.toString()}/SystemGenAiMetadataProcessing-StateMachine/DistributedMapPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Child executions of the video-segment Distributed Map are named by Step Functions at run time, so the execution ARNs the map policy describes, stops and redrives are scoped to this state machine's name (and the map's label) with a trailing wildcard",
                    appliesTo: [
                        {
                            regex: "/^Resource::arn:<AWS::Partition>:states:.*:execution:.*:\\*$/g",
                        },
                    ],
                },
            ],
            true
        );

        for (const role of [containerExecutionRole, containerJobRole]) {
            if (!role) continue;
            NagSuppressions.addResourceSuppressions(
                role,
                [
                    {
                        id: "AwsSolutions-IAM4",
                        reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED,
                    },
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "The Fargate render container reads input files from the deployment-specific VAMS asset buckets and writes rendered views and the analysis manifest to the auxiliary bucket",
                    },
                ],
                true
            );
        }

        for (const functionId of [
            "SystemGenAiMetadataOpenPipeline",
            "SystemGenAiMetadataConstructPipeline",
            "SystemGenAiMetadataGenerateMetadata",
            "SystemGenAiMetadataGenerateEmbedding",
            "SystemGenAiMetadataPipelineEnd",
            "VamsExecuteSystemGenAiMetadataPipeline",
            "SystemGenAiMetadataBlenderRender",
            "SystemGenAiMetadataRender3d",
            "SystemGenAiMetadataMediaExtract",
            "SystemGenAiMetadataSegmentAnalyze",
        ]) {
            NagSuppressions.addResourceSuppressionsByPath(
                Stack.of(this),
                `/${this.toString()}/${functionId}/ServiceRole/Resource`,
                [
                    {
                        id: "AwsSolutions-IAM4",
                        reason: `${functionId} requires AWS Managed Policies, AWSLambdaBasicExecutionRole and AWSLambdaVPCAccessExecutionRole for CloudWatch logging and VPC network interface management`,
                        appliesTo: [
                            "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
                            "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
                        ],
                    },
                ],
                true
            );
        }
    }
}
