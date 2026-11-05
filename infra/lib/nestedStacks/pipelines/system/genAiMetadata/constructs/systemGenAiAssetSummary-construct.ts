/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as logs from "aws-cdk-lib/aws-logs";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Duration } from "aws-cdk-lib";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { storageResources } from "../../../../storage/storageBuilder-nestedStack";
import * as Config from "../../../../../../config/config";
import {
    generateUniqueNameHash,
    suppressCdkNagErrorsByGrantReadWrite,
} from "../../../../../helper/security";
import { VamsSchemaRegistration } from "../../../constructs/vamsSchemaRegistration-construct";
import { SystemGenAiGuardrailReference } from "./systemGenAiGuardrail-construct";
import {
    SYSTEM_GENAI_ASSET_SUMMARY_PIPELINE_ID,
    SYSTEM_GENAI_ASSET_SUMMARY_WORKFLOW_ID,
    SYSTEM_WORKFLOW_DATABASE_ID,
} from "../../../../../../common/systemPipelines";
import {
    ASSET_SUMMARY_PIPELINE_END_STATE_NAME,
    SUMMARIZE_ASSET_STATE_NAME,
    buildAssetSummaryOpenPipelineFunction,
    buildAssetSummaryPipelineEndFunction,
    buildSummarizeAssetFunction,
    buildVamsExecuteSystemGenAiAssetSummaryFunction,
} from "../lambdaBuilder/systemGenAiAssetSummaryFunctions";

export interface SystemGenAiAssetSummaryConstructProps {
    config: Config.Config;
    storageResources: storageResources;
    vpc: ec2.IVpc;
    pipelineSubnets: ec2.ISubnet[];
    pipelineSecurityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: LayerVersion;
    importGlobalPipelineWorkflowV2FunctionName: string;
    /** The SYSTEM GenAI metadata pipeline's guardrail; the summary prompt is sent with the same one. */
    guardrail: SystemGenAiGuardrailReference | undefined;
}

/** The summary state machine's bound; the bundle's taskTimeout (3600 seconds) covers it. */
export const ASSET_SUMMARY_STATE_MACHINE_TIMEOUT = Duration.hours(1);

/**
 * The SYSTEM - GenAI Asset Summary workflow: describes a whole asset, or one folder of it, from the descriptions
 * the SYSTEM GenAI metadata pipeline wrote on its files, and writes the asset's genai_asset_* metadata.
 * Built only with the GenAI analysis layer on (`useSystemGenAiMetadata.useGenAiAnalysis`), and registered as a
 * system pipeline and workflow only with `autoRegisterWithVAMS`. Creates:
 * - a state machine (summarise -> end) and its vended log group
 * - four zip Lambdas: the entry Lambda, its openPipeline instance, the summary step and pipelineEnd
 * - the VAMS pipeline/workflow/template registration of `vamsSchema/assetSummary/`
 */
export class SystemGenAiAssetSummaryConstruct extends Construct {
    public readonly pipelineVamsLambdaFunctionName: string;
    public readonly stateMachine: sfn.StateMachine;

    constructor(parent: Construct, name: string, props: SystemGenAiAssetSummaryConstructProps) {
        super(parent, name);

        const config = props.config;
        const pipelineConfig = config.app.pipelines.useSystemGenAiMetadata;
        const kmsKey = props.storageResources.encryption.kmsKey;
        const network = [props.vpc, props.pipelineSubnets, props.pipelineSecurityGroups] as const;

        const stateMachineLogGroup = new logs.LogGroup(this, "StateMachineLogGroup", {
            logGroupName:
                "/aws/vendedlogs/VAMSStateMachine-SystemGenAiAssetSummary" +
                generateUniqueNameHash(
                    config.env.coreStackName,
                    config.env.account,
                    "SystemGenAiAssetSummary-StateMachineLogGroup",
                    10
                ),
            encryptionKey: kmsKey,
            retention: logs.RetentionDays.ONE_YEAR,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        const summarizeFunction = buildSummarizeAssetFunction(
            this,
            props.lambdaCommonBaseLayer,
            config,
            ...network,
            props.guardrail,
            kmsKey
        );
        const pipelineEndFunction = buildAssetSummaryPipelineEndFunction(
            this,
            props.lambdaCommonBaseLayer,
            config,
            ...network,
            kmsKey
        );

        /**
         * SFN States: summarise, then report on the parent workflow's callback token. A summary failure the
         * handler catches is recorded through execution.status.json; any other fault is routed to the end task
         * with the error beside the state, which fails the token.
         */
        const summarizeTask = new tasks.LambdaInvoke(this, SUMMARIZE_ASSET_STATE_NAME, {
            lambdaFunction: summarizeFunction,
            outputPath: "$.Payload",
        });
        const pipelineEndTask = new tasks.LambdaInvoke(
            this,
            ASSET_SUMMARY_PIPELINE_END_STATE_NAME,
            {
                lambdaFunction: pipelineEndFunction,
                outputPath: "$.Payload",
            }
        );
        const endStatesChoice = new sfn.Choice(this, "EndStatesChoice")
            .when(
                sfn.Condition.isPresent("$.error"),
                new sfn.Fail(this, "FailState", {
                    causePath: sfn.JsonPath.stringAt("$.error.Cause"),
                    errorPath: sfn.JsonPath.stringAt("$.error.Error"),
                })
            )
            .otherwise(
                new sfn.Succeed(this, "SuccessState", { comment: "Pipeline returned SUCCESS" })
            );
        pipelineEndTask.next(endStatesChoice);
        summarizeTask.addCatch(
            new sfn.Pass(this, "HandleSummarizeAssetError", { resultPath: "$" }).next(
                pipelineEndTask
            ),
            { resultPath: "$.error" }
        );
        summarizeTask.next(pipelineEndTask);

        this.stateMachine = new sfn.StateMachine(this, "StateMachine", {
            definitionBody: sfn.DefinitionBody.fromChainable(sfn.Chain.start(summarizeTask)),
            timeout: ASSET_SUMMARY_STATE_MACHINE_TIMEOUT,
            logs: {
                destination: stateMachineLogGroup,
                includeExecutionData: true,
                level: sfn.LogLevel.ALL,
            },
            tracingEnabled: true,
        });

        const openPipelineFunction = buildAssetSummaryOpenPipelineFunction(
            this,
            props.lambdaCommonBaseLayer,
            this.stateMachine,
            config,
            ...network,
            props.storageResources.eventBridge.orchestrationBus,
            stateMachineLogGroup,
            {
                [SUMMARIZE_ASSET_STATE_NAME]: summarizeFunction,
                [ASSET_SUMMARY_PIPELINE_END_STATE_NAME]: pipelineEndFunction,
            },
            kmsKey
        );
        const vamsExecuteFunction = buildVamsExecuteSystemGenAiAssetSummaryFunction(
            this,
            props.lambdaCommonBaseLayer,
            openPipelineFunction,
            config,
            ...network,
            kmsKey
        );
        this.pipelineVamsLambdaFunctionName = vamsExecuteFunction.functionName;

        if (pipelineConfig.autoRegisterWithVAMS === true) {
            new VamsSchemaRegistration(this, "Registration", {
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
                    "vamsSchema",
                    "assetSummary"
                ),
                resourceOverrides: { lambdaName: vamsExecuteFunction.functionName },
                idOverrides: {
                    pipelineId: SYSTEM_GENAI_ASSET_SUMMARY_PIPELINE_ID,
                    pipelineDatabaseId: SYSTEM_WORKFLOW_DATABASE_ID,
                    workflowId: SYSTEM_GENAI_ASSET_SUMMARY_WORKFLOW_ID,
                    workflowDatabaseId: SYSTEM_WORKFLOW_DATABASE_ID,
                },
                // No triggerEnabled: the bundle ships no trigger; a summary runs on request.
            });
        }

        // The shape-specific entries every pipeline builder applies: the task-token callback on this account's
        // executions and the invoke grants on one named function's versions (`<function ARN>:*`).
        suppressCdkNagErrorsByGrantReadWrite(this);
        NagSuppressions.addResourceSuppressions(
            this.stateMachine,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "The state machine role's default policy carries the wildcard permissions Step Functions needs: Resource `*` for the CloudWatch Logs delivery actions the ALL-level execution log needs and the X-Ray trace actions tracing needs.",
                    appliesTo: ["Resource::*"],
                },
            ],
            true
        );
    }
}
