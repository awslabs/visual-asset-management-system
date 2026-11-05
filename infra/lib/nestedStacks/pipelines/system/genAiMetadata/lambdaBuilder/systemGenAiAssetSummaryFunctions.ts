/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as events from "aws-cdk-lib/aws-events";
import * as logs from "aws-cdk-lib/aws-logs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../../config/config";
import * as Config from "../../../../../../config/config";
import {
    globalLambdaEnvironmentsAndPermissions,
    grantExternalAssetBucketKmsKeys,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    suppressCdkNagLambda,
} from "../../../../../helper/security";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { SystemGenAiGuardrailReference } from "../constructs/systemGenAiGuardrail-construct";
import {
    analysisSettingsEnvironment,
    grantApplyGuardrail,
    grantBedrockInvokeModel,
    guardrailEnvironment,
    vpcPlacement,
} from "./systemGenAiMetadataFunctions";

/** The zip handlers: the summary step and its entry Lambda live beside the per-file pipeline's handlers. */
const LAMBDA_DIR = path.join(
    __dirname,
    "../../../../../../../backendPipelines/system/genAiMetadata/lambda"
);

/** The state names of the summary state machine whose Lambdas openPipeline registers as log sources. */
export const SUMMARIZE_ASSET_STATE_NAME = "SummarizeAssetTask";
export const ASSET_SUMMARY_PIPELINE_END_STATE_NAME = "PipelineEndTask";

/**
 * The model of the summary's one text request: the text model when one is configured, else the analysis model.
 * Empty with the GenAI layer off, when the summary is not built at all.
 */
export function assetSummaryModelId(config: Config.Config): string {
    const pipeline = config.app.pipelines.useSystemGenAiMetadata;
    if (!pipeline.useGenAiAnalysis) return "";
    return (
        (pipeline.bedrockModels.textModelId ?? "").trim() || pipeline.bedrockModels.analysisModelId
    );
}

/**
 * The run-I/O area of the deployment's default asset bucket: every workflow execution's manifest, configuration
 * and metadata files, and the outputs of each step, are under `{baseAssetsPrefix}pipelines/` in that bucket
 * (backend `executionRecords.run_bucket_key`). The summary reads and writes nothing else.
 */
export function runIoArea(): { bucket: s3.IBucket; keyPattern: string } {
    const record = s3AssetBuckets.getS3AssetBucketRecords().find((r) => r.isDefault);
    if (!record) {
        throw new Error(
            "The SYSTEM GenAI asset summary needs the default asset bucket, and none is registered."
        );
    }
    const prefix = (record.prefix || "").replace(/^\/+/, "");
    const base = prefix === "" ? "" : prefix.endsWith("/") ? prefix : prefix + "/";
    return { bucket: record.bucket, keyPattern: `${base}pipelines/*` };
}

/** Reads (and with `write`, writes) the run-I/O objects of the default asset bucket only. */
function grantRunIoArea(fun: lambda.Function, write: boolean): void {
    const area = runIoArea();
    area.bucket.grantRead(fun, area.keyPattern);
    if (write) {
        area.bucket.grantPut(fun, area.keyPattern);
    }
    // The default bucket may be an external one encrypted with its own key (a no-op otherwise).
    grantExternalAssetBucketKmsKeys(fun);
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Scoped to the run-I/O area of the default asset bucket ({baseAssetsPrefix}pipelines/*), where " +
                    "each workflow execution's manifest, configuration and metadata files and its step outputs " +
                    "live under keys named per execution at run time; the bucket grant's action wildcards " +
                    "(GetObject*, GetBucket*, List*, Abort*) are the CDK read and put grants.",
                appliesTo: [
                    { regex: "/^Action::s3:.*$/g" },
                    { regex: "/^Resource::.*\\/pipelines\\/\\*$/g" },
                ],
            },
        ],
        true
    );
}

/**
 * The summary step: reads the run's metadata envelope and configuration, makes one Converse call on the summary
 * model with the pipeline's guardrail, and writes the asset's metadata file and the run's results.
 */
export function buildSummarizeAssetFunction(
    scope: Construct,
    layer: LayerVersion,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    securityGroups: ec2.ISecurityGroup[],
    guardrail: SystemGenAiGuardrailReference | undefined,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "summarizeAsset";
    const pipeline = config.app.pipelines.useSystemGenAiMetadata;
    const modelId = assetSummaryModelId(config);
    const fun = new lambda.Function(scope, "SystemGenAiAssetSummarySummarizeAsset", {
        code: lambda.Code.fromAsset(LAMBDA_DIR),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [layer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcPlacement(config, vpc, subnets, securityGroups),
        environment: {
            BEDROCK_ANALYSIS_MODEL_ID: pipeline.bedrockModels.analysisModelId,
            GENAI_ANALYSIS_ENABLED: "true",
            ...analysisSettingsEnvironment(config),
            ...guardrailEnvironment(guardrail),
        },
    });
    grantRunIoArea(fun, true);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    // The one model the summary calls and the one guardrail its prompt is sent with (and its tool answer is
    // screened with).
    grantBedrockInvokeModel(fun, config, [modelId]);
    grantApplyGuardrail(fun, guardrail);
    suppressCdkNagLambda(fun);
    return fun;
}

/** The only state that reports on the parent workflow's callback token. */
export function buildAssetSummaryPipelineEndFunction(
    scope: Construct,
    layer: LayerVersion,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    securityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "pipelineEnd";
    const fun = new lambda.Function(scope, "SystemGenAiAssetSummaryPipelineEnd", {
        code: lambda.Code.fromAsset(LAMBDA_DIR),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [layer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcPlacement(config, vpc, subnets, securityGroups),
        environment: {},
    });
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [
                `arn:${ServiceHelper.Partition()}:states:${config.env.region}:${
                    config.env.account
                }:*`,
            ],
        })
    );
    suppressCdkNagLambda(fun);
    return fun;
}

/**
 * The summary's instance of openPipeline: takes a whole-asset or folder input, starts the summary state machine
 * and registers its log sources. It reads no object.
 */
export function buildAssetSummaryOpenPipelineFunction(
    scope: Construct,
    layer: LayerVersion,
    stateMachine: sfn.StateMachine,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    securityGroups: ec2.ISecurityGroup[],
    orchestrationBus: events.IEventBus,
    stateMachineLogGroup: logs.ILogGroup,
    stateFunctions: Record<string, lambda.IFunction>,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "openPipeline";
    const fun = new lambda.Function(scope, "SystemGenAiAssetSummaryOpenPipeline", {
        code: lambda.Code.fromAsset(LAMBDA_DIR),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [layer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcPlacement(config, vpc, subnets, securityGroups),
        environment: {
            STATE_MACHINE_ARN: stateMachine.stateMachineArn,
            ALLOWED_INPUT_FILEEXTENSIONS: "*",
            ALLOW_FOLDER_INPUT: "true",
            ORCHESTRATION_BUS_NAME: orchestrationBus.eventBusName,
            STATE_MACHINE_LOG_GROUP_NAME: stateMachineLogGroup.logGroupName,
            STATE_MACHINE_LOG_GROUP_ARN: stateMachineLogGroup.logGroupArn,
            SUB_EXECUTION_LABEL: "GenAI asset summary",
            STATE_MACHINE_LOG_LABEL: "GenAI asset summary state machine",
            // The function each Lambda state runs, keyed by the state's name (openPipeline.py lambda_log_entries).
            ANALYSIS_FUNCTION_NAMES: cdk.Stack.of(scope).toJsonString(
                Object.fromEntries(
                    Object.entries(stateFunctions).map(([state, fn]) => [state, fn.functionName])
                )
            ),
        },
    });
    stateMachine.grantStartExecution(fun);
    orchestrationBus.grantPutEventsTo(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    // A refused input is reported on the callback token here.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [
                `arn:${ServiceHelper.Partition()}:states:${config.env.region}:${
                    config.env.account
                }:*`,
            ],
        })
    );
    suppressCdkNagLambda(fun);
    return fun;
}

/** The workflow's entry Lambda: reads the run's manifest and configuration, then invokes openPipeline. */
export function buildVamsExecuteSystemGenAiAssetSummaryFunction(
    scope: Construct,
    layer: LayerVersion,
    openPipelineFunction: lambda.IFunction,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    securityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "vamsExecuteSystemGenAiAssetSummary";
    const fun = new lambda.Function(scope, "VamsExecuteSystemGenAiAssetSummary", {
        code: lambda.Code.fromAsset(LAMBDA_DIR),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [layer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcPlacement(config, vpc, subnets, securityGroups),
        environment: { OPEN_PIPELINE_FUNCTION_NAME: openPipelineFunction.functionName },
    });
    grantRunIoArea(fun, false);
    openPipelineFunction.grantInvoke(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    // The workflow task waits on a callback token, so a failure here is reported back to Step Functions.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [
                `arn:${ServiceHelper.Partition()}:states:${config.env.region}:${
                    config.env.account
                }:*`,
            ],
        })
    );
    suppressCdkNagLambda(fun);
    return fun;
}
