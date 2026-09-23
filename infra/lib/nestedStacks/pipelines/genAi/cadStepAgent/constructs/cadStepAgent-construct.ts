/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as bedrock from "aws-cdk-lib/aws-bedrock";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as logs from "aws-cdk-lib/aws-logs";
import { Construct } from "constructs";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import { Stack } from "aws-cdk-lib";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import * as Config from "../../../../../../config/config";
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
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
    buildInvokeAgentRuntimeFunction,
    buildOpenPipelineFunction,
    buildPipelineEndFunction,
    buildVamsExecuteCadStepAgentFunction,
} from "../lambdaBuilder/cadStepAgentFunctions";
import { CadStepAgentCodeBuildConstruct } from "./cadStepAgentCodeBuild-construct";
import { CadStepAgentAgentCoreConstruct } from "./cadStepAgentAgentCore-construct";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";
import path = require("path");

export interface CadStepAgentConstructProps extends cdk.StackProps {
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

export const CAD_STEP_AGENT_PIPELINE_ID = "genai-cad-step-agent";
/**
 * How long the run state waits on the inner task token, and the Batch job's attempt duration. Below
 * the 7200 s taskTimeout the vamsSchema bundles put on the workflow task, so the inner wait ends
 * first and pipelineEnd reports the outer token; above the largest `maxRunSeconds` getConfig accepts
 * (6000 s), so the container's watchdog reports before the wait does.
 */
export const RUN_TASK_TIMEOUT = cdk.Duration.seconds(6600);
/**
 * Retry of the run state on the agentcore runtime's busy refusal (`CadStepAgentBusy`): a warm session
 * slot still running an earlier job. Five retries at 30 s doubling wait about 15.5 minutes in all;
 * with the largest `maxRunSeconds` getConfig accepts (6000 s) the state still ends inside the bundles'
 * 7200 s workflow task timeout.
 */
export const BUSY_RETRY = {
    interval: cdk.Duration.seconds(30),
    backoffRate: 2,
    maxAttempts: 5,
};
export const CAD_STEP_AGENT_MODIFY_WORKFLOW_ID = "genai-cad-step-agent-modify";
export const CAD_STEP_AGENT_GENERATE_WORKFLOW_ID = "genai-cad-step-agent-generate";

const VAMS_SCHEMA_DIR = path.join(
    __dirname,
    "..",
    "..",
    "..",
    "..",
    "..",
    "..",
    "..",
    "backendPipelines",
    "genAi",
    "cadStepAgent",
    "vamsSchema"
);

export class CadStepAgentConstruct extends Construct {
    public readonly pipelineVamsLambdaFunctionName: string;

    constructor(parent: Construct, name: string, props: CadStepAgentConstructProps) {
        super(parent, name);

        const region = cdk.Stack.of(this).region;
        const account = cdk.Stack.of(this).account;
        const cad = props.config.app.pipelines.useGenAiCadStepAgent;
        const runtime = cad.runtime;
        const openAiEnabled = (cad.openAi?.apiKeySecretArn ?? "").trim() !== "";

        // IAM policies for the container role (shared by both runtimes)
        const s3BucketActions = [
            "s3:GetObject",
            "s3:PutObject",
            "s3:ListBucket",
            "s3:GetBucketLocation",
        ];

        const inputBucketPolicy = new iam.PolicyDocument({
            statements: [
                ...s3AssetBuckets.getS3AssetBucketRecords().map((record) => {
                    const prefix = record.prefix || "/";
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

        // The model grant names the inference profile the agent invokes and the foundation model
        // behind it. A cross-Region profile (global., us., eu., apac., us-gov.) routes requests to
        // other Regions, and Amazon Bedrock authorizes the foundation-model ARN of the Region it
        // routed to, so that case grants the model in every Region of the partition; a plain model id
        // is invoked in the deployment Region only. Never foundation-model/*.
        const bedrockModelId = cad.bedrockModelId;
        const crossRegionPrefix = /^(global|us-gov|us|eu|apac)\./.exec(bedrockModelId)?.[1];
        const bedrockModelPermissions = bedrockModelId.replace(/^(global|us-gov|us|eu|apac)\./, "");
        const foundationModelRegion = crossRegionPrefix ? "*" : region;
        const bedrockPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                    resources: [
                        `arn:${ServiceHelper.Partition()}:bedrock:${foundationModelRegion}::foundation-model/${bedrockModelPermissions}`,
                        `arn:${ServiceHelper.Partition()}:bedrock:${region}:${account}:inference-profile/${bedrockModelId}`,
                    ],
                }),
            ],
        });

        // Every model invocation and every fetched page runs through a guardrail with a PROMPT_ATTACK
        // input filter. The deployment creates one unless the configuration names an existing
        // guardrail id and version.
        const configuredGuardrailId = (cad.bedrockGuardrail?.guardrailId ?? "").trim();
        const configuredGuardrailVersion = (cad.bedrockGuardrail?.guardrailVersion ?? "").trim();
        let guardrailId: string;
        let guardrailVersion: string;
        let guardrailArn: string;
        if (configuredGuardrailId !== "") {
            guardrailId = configuredGuardrailId;
            guardrailVersion = configuredGuardrailVersion;
            guardrailArn = `arn:${ServiceHelper.Partition()}:bedrock:${region}:${account}:guardrail/${guardrailId}`;
        } else {
            // A PROMPT_ATTACK filter applies to inputs only (its output strength must be NONE). LOW is
            // the starting strength the guidance names; the filter is never disabled.
            const guardrail = new bedrock.CfnGuardrail(this, "CadStepAgentGuardrail", {
                name:
                    "vams-cad-step-agent-" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "CadStepAgentGuardrail",
                        10
                    ),
                description:
                    "VAMS GenAI CAD STEP agent: prompt-attack input filter over the instruction and fetched pages",
                blockedInputMessaging:
                    "The instruction or a fetched page was blocked by the prompt-attack filter.",
                blockedOutputsMessaging: "The model response was blocked by the guardrail.",
                contentPolicyConfig: {
                    filtersConfig: [
                        { type: "PROMPT_ATTACK", inputStrength: "LOW", outputStrength: "NONE" },
                    ],
                },
                kmsKeyArn: props.kmsKey?.keyArn,
            });
            // A numbered version pins what the container applies; its description carries the filter
            // configuration so a change there is a new version rather than a stale one.
            const version = new bedrock.CfnGuardrailVersion(this, "CadStepAgentGuardrailVersion", {
                guardrailIdentifier: guardrail.attrGuardrailId,
                description: "PROMPT_ATTACK input filter, strength LOW",
            });
            guardrailId = guardrail.attrGuardrailId;
            guardrailVersion = version.attrVersion;
            guardrailArn = guardrail.attrGuardrailArn;
        }
        const guardrailPolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    actions: ["bedrock:ApplyGuardrail"],
                    resources: [guardrailArn],
                }),
            ],
        });

        const inlinePolicies: { [name: string]: iam.PolicyDocument } = {
            InputBucketPolicy: inputBucketPolicy,
            OutputBucketPolicy: outputBucketPolicy,
            StateTaskPolicy: stateTaskPolicy,
            BedrockModelPolicy: bedrockPolicy,
            BedrockGuardrailPolicy: guardrailPolicy,
        };
        if (openAiEnabled) {
            // Read-only on the ONE configured secret; the key never leaves the container process.
            inlinePolicies.OpenAiSecretPolicy = new iam.PolicyDocument({
                statements: [
                    new iam.PolicyStatement({
                        actions: ["secretsmanager:GetSecretValue"],
                        resources: [cad.openAi.apiKeySecretArn],
                    }),
                ],
            });
        }

        // The environment both runtimes hand the agent container. Model ids, the guardrail and the
        // secret ARN only — the API key itself is read from Secrets Manager at run start.
        const containerEnvironment: { [key: string]: string } = {
            BEDROCK_MODEL_ID: bedrockModelId,
            BEDROCK_GUARDRAIL_ID: guardrailId,
            BEDROCK_GUARDRAIL_VERSION: guardrailVersion,
            OPENAI_MODEL_ID: openAiEnabled ? cad.openAi.modelId : "",
            OPENAI_API_KEY_SECRET_ARN: openAiEnabled ? cad.openAi.apiKeySecretArn : "",
        };

        // Container image (CodeBuild for either runtime; a local Docker asset only for Fargate).
        let codeBuildConstruct: CadStepAgentCodeBuildConstruct | undefined;
        if (cad.useCodeBuild === true) {
            codeBuildConstruct = new CadStepAgentCodeBuildConstruct(this, "CadStepAgentCodeBuild", {
                config: props.config,
                platform: runtime === "agentcore" ? "linux/arm64" : "linux/amd64",
            });
            new cdk.CfnOutput(this, "CadStepAgentCodeBuildProject", {
                value: codeBuildConstruct.codeBuildProjectName,
                description:
                    "CodeBuild project name for the CAD STEP agent container. Check build status: aws codebuild list-builds-for-project --project-name <value>",
            });
        }

        // Lambda functions shared by both runtimes
        const constructPipelineFunction = buildConstructPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            {
                maxRunSeconds: cad.maxRunSeconds,
                allowInternetResearch: cad.allowInternetResearch,
                openAiEnabled,
            },
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.kmsKey
        );

        const pipelineEndFunction = buildPipelineEndFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.assetAuxiliaryBucket,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.kmsKey
        );

        // The state that runs the agent: an AgentCore invoke or a Batch job submit, either way under
        // WAIT_FOR_TASK_TOKEN with the container reporting the inner token itself.
        let runAgentFunction: lambda.Function;
        let runtimeStateName: string;
        if (runtime === "agentcore") {
            const runtimeRole = new iam.Role(this, "CadStepAgentRuntimeRole", {
                assumedBy: new iam.ServicePrincipal(Service("BEDROCK_AGENTCORE").PrincipalString, {
                    conditions: {
                        StringEquals: { "aws:SourceAccount": account },
                        ArnLike: {
                            "aws:SourceArn": `arn:${ServiceHelper.Partition()}:bedrock-agentcore:${region}:${account}:*`,
                        },
                    },
                }),
                inlinePolicies,
            });
            grantExternalAssetBucketKmsKeys(runtimeRole);

            if (!codeBuildConstruct) {
                throw new Error(
                    "useGenAiCadStepAgent.runtime agentcore requires useCodeBuild (validated in getConfig)"
                );
            }
            const agentCore = new CadStepAgentAgentCoreConstruct(this, "CadStepAgentAgentCore", {
                config: props.config,
                executionRole: runtimeRole,
                image: {
                    repository: codeBuildConstruct.repository,
                    tag: codeBuildConstruct.imageTag,
                    build: codeBuildConstruct.imageBuild,
                },
                environment: containerEnvironment,
            });

            runAgentFunction = buildInvokeAgentRuntimeFunction(
                this,
                props.lambdaCommonBaseLayer,
                agentCore.runtimeArn,
                cad.agentCore.warmSessionSlots,
                `vams-cad-${props.config.name}-${props.config.app.baseStackName}`.toLowerCase(),
                props.config,
                props.vpc,
                props.pipelineSubnets,
                props.kmsKey
            );
            runtimeStateName = "CadStepAgentAgentCoreRun";

            NagSuppressions.addResourceSuppressions(
                runtimeRole,
                [
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "The agent container reads and writes objects under the registered asset-bucket prefixes and the auxiliary bucket, and reports on the workflow task token of whichever execution invoked it.",
                    },
                ],
                true
            );
        } else {
            const containerExecutionRole = new iam.Role(
                this,
                "CadStepAgentContainerExecutionRole",
                {
                    assumedBy: Service("ECS_TASKS").Principal,
                    managedPolicies: [
                        iam.ManagedPolicy.fromAwsManagedPolicyName(
                            "service-role/AmazonECSTaskExecutionRolePolicy"
                        ),
                        iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
                    ],
                }
            );
            const containerJobRole = new iam.Role(this, "CadStepAgentContainerJobRole", {
                assumedBy: Service("ECS_TASKS").Principal,
                inlinePolicies,
                managedPolicies: [
                    iam.ManagedPolicy.fromAwsManagedPolicyName(
                        "service-role/AmazonECSTaskExecutionRolePolicy"
                    ),
                    iam.ManagedPolicy.fromAwsManagedPolicyName("AWSXrayWriteOnlyAccess"),
                ],
            });
            grantExternalAssetBucketKmsKeys(containerJobRole);

            // The container's stdout/stderr: a named vended group under the /aws/vendedlogs/Pipelines/
            // prefix the execution-service role is granted to read.
            const containerLogGroup = new logs.LogGroup(this, "CadStepAgentBatchJobLogGroup", {
                logGroupName:
                    "/aws/vendedlogs/Pipelines/CadStepAgent" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "CadStepAgentBatchJobLogGroup",
                        10
                    ),
                encryptionKey: props.kmsKey,
                retention: logs.RetentionDays.ONE_YEAR,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            });

            const batchPipeline = new BatchFargatePipelineConstruct(
                this,
                "BatchFargatePipeline_CadStepAgent",
                {
                    // Matches the taskTimeout on the state that waits for this job.
                    attemptDuration: RUN_TASK_TIMEOUT,
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
                        "cadStepAgent",
                        "container"
                    ),
                    dockerfileName: "Dockerfile",
                    batchJobDefinitionName:
                        "CadStepAgentJob_" +
                        props.config.name +
                        "_" +
                        props.config.app.baseStackName,
                    // One STEP input, a handful of script attempts and their outputs; 40 GiB is ample.
                    ephemeralStorageGiB: 40,
                    logGroup: containerLogGroup,
                    environment: containerEnvironment,
                    // The SIGTERM of an abort must reach the agent interpreter, which then stops the run
                    // and reports nothing (the workflow already recorded the abort).
                    initProcessEnabled: true,
                    ecrImage: codeBuildConstruct
                        ? {
                              repository: codeBuildConstruct.repository,
                              tag: codeBuildConstruct.imageTag,
                          }
                        : undefined,
                }
            );
            // A Batch job definition accepts a tag that does not exist yet (the job fails at start
            // instead); depending on the build keeps the first job from racing the image push.
            if (codeBuildConstruct) {
                batchPipeline.batchJobDefinition.node.addDependency(codeBuildConstruct.imageBuild);
            }

            runAgentFunction = buildExecuteBatchJobFunction(
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
            runtimeStateName = "CadStepAgentBatchJob";

            NagSuppressions.addResourceSuppressions(
                containerExecutionRole,
                [
                    { id: "AwsSolutions-IAM4", reason: NAG_REASON_ECS_TASK_EXECUTION_MANAGED },
                    {
                        id: "AwsSolutions-IAM5",
                        reason: "ECS containers require wildcard access to objects under the registered asset-bucket prefixes for STEP file processing.",
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
                        reason: "ECS containers require wildcard access to objects under the registered asset-bucket prefixes for STEP file processing, and report on the workflow task token of whichever execution invoked them.",
                    },
                ],
                true
            );
        }

        // Step Functions state machine
        const constructPipelineTask = new tasks.LambdaInvoke(this, "ConstructPipelineTask", {
            lambdaFunction: constructPipelineFunction,
            outputPath: "$.Payload",
        });

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

        const handleRunError = new sfn.Pass(this, "HandleRunError", {
            resultPath: "$",
        }).next(pipelineEndTask);

        // ConstructPipelineTask is the first state; a failure there ends the execution before
        // PipelineEndTask runs, and PipelineEndTask is the only state that reports on the parent
        // workflow's callback token. The handler reports the token for the errors it raises itself; this
        // covers the failures where it never runs at all.
        const handleConstructPipelineError = new sfn.Pass(this, "HandleConstructPipelineError", {
            resultPath: "$",
        }).next(pipelineEndTask);
        constructPipelineTask.addCatch(handleConstructPipelineError, {
            resultPath: "$.error",
        });

        // No heartbeatTimeout: the container reports only terminal success/failure on the inner token.
        // The taskTimeout bounds the wait and sits inside the workflow task's 7200 s outer bound (the
        // bundle's taskTimeout), so a hung container reaches pipelineEnd with a States.Timeout on THIS
        // task, which reports the outer token, rather than the outer token expiring first. The
        // container's own maxRunSeconds is validated in getConfig to end before this wait does.
        const runAgentTask = new tasks.LambdaInvoke(this, runtimeStateName, {
            lambdaFunction: runAgentFunction,
            integrationPattern: sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            payload: sfn.TaskInput.fromObject({
                taskToken: sfn.JsonPath.taskToken,
                "jobName.$": "$.jobName",
                "definition.$": "$.definition",
                "orchestrationEventPrefix.$": "$.orchestrationEventPrefix",
            }),
            resultPath: "$.runResult",
            taskTimeout: sfn.Timeout.duration(RUN_TASK_TIMEOUT),
        })
            // A busy warm slot is waited for, not routed around: the retry re-invokes the Lambda, which
            // derives the same session id from the job name and so asks the same slot again.
            .addRetry({ errors: ["CadStepAgentBusy"], ...BUSY_RETRY })
            .addCatch(handleRunError, {
                resultPath: "$.error",
            })
            .next(pipelineEndTask);

        const definition = constructPipelineTask.next(runAgentTask);

        const stateMachineLogGroup = new logs.LogGroup(this, "CadStepAgent-StateMachineLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSStateMachine-CadStepAgent" +
                generateUniqueNameHash(
                    props.config.env.coreStackName,
                    props.config.env.account,
                    "CadStepAgent-StateMachineLogGroup",
                    10
                ),
            encryptionKey: props.kmsKey,
            retention: logs.RetentionDays.ONE_YEAR,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        const stateMachine = new sfn.StateMachine(this, "CadStepAgent-StateMachine", {
            definitionBody: sfn.DefinitionBody.fromChainable(definition),
            // Envelopes the run taskTimeout so an overrunning agent hits that task's own timeout and
            // reaches pipelineEnd rather than an execution-level States.Timeout.
            timeout: cdk.Duration.hours(3),
            logs: {
                destination: stateMachineLogGroup,
                includeExecutionData: true,
                level: sfn.LogLevel.ALL,
            },
            tracingEnabled: true,
        });

        // Open pipeline Lambda. The allow list gates the modify template's input; a generate run has
        // no input file and is not gated.
        const allowedExtensions = ".stp,.step";
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
        const vamsExecuteFunction = buildVamsExecuteCadStepAgentFunction(
            this,
            props.lambdaCommonBaseLayer,
            openPipelineFunction,
            props.config,
            props.vpc,
            props.pipelineSubnets,
            props.kmsKey
        );

        this.pipelineVamsLambdaFunctionName = vamsExecuteFunction.functionName;

        // Auto-register with VAMS. Two bundles over one pipeline: the main bundle carries the pipeline,
        // both templates and the modify workflow (one input file); the second carries the identical
        // pipeline and the generate workflow (no input file). It registers after the first so the two
        // never race on the pipeline row.
        if (cad.autoRegisterWithVAMS === true) {
            const mainRegistration = new VamsSchemaRegistration(this, "CadStepAgentRegistration", {
                importFunctionName: props.importGlobalPipelineWorkflowV2FunctionName,
                artefactsBucket: props.storageResources.s3.artefactsBucket,
                vamsSchemaDir: VAMS_SCHEMA_DIR,
                resourceOverrides: { lambdaName: vamsExecuteFunction.functionName },
                idOverrides: {
                    pipelineId: CAD_STEP_AGENT_PIPELINE_ID,
                    workflowId: CAD_STEP_AGENT_MODIFY_WORKFLOW_ID,
                },
                triggerEnabled: cad.autoRegisterAutoTriggerOnFileUpload === true,
            });
            const generateRegistration = new VamsSchemaRegistration(
                this,
                "CadStepAgentGenerateRegistration",
                {
                    importFunctionName: props.importGlobalPipelineWorkflowV2FunctionName,
                    artefactsBucket: props.storageResources.s3.artefactsBucket,
                    vamsSchemaDir: path.join(VAMS_SCHEMA_DIR, "generateWorkflow"),
                    resourceOverrides: { lambdaName: vamsExecuteFunction.functionName },
                    idOverrides: {
                        pipelineId: CAD_STEP_AGENT_PIPELINE_ID,
                        workflowId: CAD_STEP_AGENT_GENERATE_WORKFLOW_ID,
                    },
                    triggerEnabled: false,
                }
            );
            generateRegistration.node.addDependency(mainRegistration);
        }

        // CDK Nag suppressions
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Pipeline Lambda functions require access to S3 asset buckets and Step Functions callbacks for the CAD STEP agent run.",
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
            `/${this.toString()}/CadStepAgent-StateMachine/Role/DefaultPolicy/Resource`,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "State machine default policy requires wildcard for Lambda invocation and log delivery.",
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
