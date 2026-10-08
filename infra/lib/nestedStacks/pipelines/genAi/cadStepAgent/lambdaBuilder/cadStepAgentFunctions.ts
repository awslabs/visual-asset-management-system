/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import * as batch from "aws-cdk-lib/aws-batch";
import * as events from "aws-cdk-lib/aws-events";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as kms from "aws-cdk-lib/aws-kms";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as cdk from "aws-cdk-lib";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import * as Config from "../../../../../../config/config";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { vendedBatchJobLogGroupEnvironment } from "../../../../../helper/batchJobLogGroup";
import {
    globalLambdaEnvironmentsAndPermissions,
    grantReadPermissionsToAllAssetBuckets,
    kmsKeyLambdaPermissionAddToResourcePolicy,
    suppressCdkNagErrorsByGrantReadWrite,
    suppressCdkNagLambda,
} from "../../../../../helper/security";
import path = require("path");

const LAMBDA_PYTHON_RUNTIME = Config.LAMBDA_PYTHON_RUNTIME;
const LAMBDA_CODE_PATH = path.join(
    __dirname,
    "../../../../../../../backendPipelines/genAi/cadStepAgent/lambda"
);

/** The deployment settings constructPipeline validates every run against. */
export interface CadStepAgentRunSettings {
    maxRunSeconds: number;
    allowInternetResearch: boolean;
    openAiEnabled: boolean;
}

function vpcProps(config: Config.Config, vpc: ec2.IVpc, subnets: ec2.ISubnet[]) {
    const inVpc = config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas;
    return {
        vpc: inVpc ? vpc : undefined,
        vpcSubnets: inVpc ? { subnets: subnets } : undefined,
    };
}

export function buildConstructPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    settings: CadStepAgentRunSettings,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;
    const name = "constructPipeline";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_CODE_PATH),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcProps(config, vpc, subnets),
        environment: {
            MAX_RUN_SECONDS: settings.maxRunSeconds.toString(),
            ALLOW_INTERNET_RESEARCH: settings.allowInternetResearch ? "true" : "false",
            OPENAI_ENABLED: settings.openAiEnabled ? "true" : "false",
        },
    });

    // Reads the input-configuration + shared metadata files from the asset bucket
    // (inputConfigurationS3Location / inputMetadataS3Location).
    grantReadPermissionsToAllAssetBuckets(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );
    return fun;
}

export function buildOpenPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    pipelineStateMachine: sfn.StateMachine,
    allowedPipelineInputExtensions: string,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    orchestrationBus: events.IEventBus,
    stateMachineLogGroup: logs.ILogGroup,
    kmsKey?: kms.IKey
): lambda.Function {
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;
    const name = "openPipeline";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_CODE_PATH),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcProps(config, vpc, subnets),
        environment: {
            STATE_MACHINE_ARN: pipelineStateMachine.stateMachineArn,
            ALLOWED_INPUT_FILEEXTENSIONS: allowedPipelineInputExtensions,
            // Orchestration bus + sub-SFN log group for optional sub-process registration
            ORCHESTRATION_BUS_NAME: orchestrationBus.eventBusName,
            STATE_MACHINE_LOG_GROUP_NAME: stateMachineLogGroup.logGroupName,
            STATE_MACHINE_LOG_GROUP_ARN: stateMachineLogGroup.logGroupArn,
        },
    });

    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantRead(fun);
    pipelineStateMachine.grantStartExecution(fun);
    orchestrationBus.grantPutEventsTo(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );
    return fun;
}

export function buildPipelineEndFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;
    const name = "pipelineEnd";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_CODE_PATH),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcProps(config, vpc, subnets),
        environment: {},
    });

    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantRead(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );
    return fun;
}

export function buildVamsExecuteCadStepAgentFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    openPipelineLambdaFunction: lambda.Function,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;
    const name = "vamsExecuteCadStepAgentPipeline";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_CODE_PATH),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcProps(config, vpc, subnets),
        environment: {
            OPEN_PIPELINE_FUNCTION_NAME: openPipelineLambdaFunction.functionName,
        },
    });

    // Reads the per-pipeline manifest envelope from the asset bucket at inputManifestS3Location.
    grantReadPermissionsToAllAssetBuckets(fun);
    openPipelineLambdaFunction.grantInvoke(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);
    // The workflow task waits on a callback token, so a failure in this lambda must be reported
    // back to Step Functions instead of leaving the task pending until its timeout.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );
    return fun;
}

export function buildExecuteBatchJobFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    batchJobQueue: batch.JobQueue,
    batchJobDefinition: batch.IJobDefinition,
    containerLogGroup: logs.ILogGroup,
    orchestrationBus: events.IEventBus,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;
    const name = "executeBatchJob";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_CODE_PATH),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcProps(config, vpc, subnets),
        environment: {
            BATCH_JOB_QUEUE: batchJobQueue.jobQueueName,
            BATCH_JOB_DEFINITION: batchJobDefinition.jobDefinitionName,
            // Orchestration bus for registering the submitted Batch job as an abortable sub-process
            ORCHESTRATION_BUS_NAME: orchestrationBus.eventBusName,
            // This pipeline's vended container log group, registered with the job as the
            // CadStepAgentBatchJob log source (streams are `<jobDefinitionName>/default/<task-id>`).
            ...vendedBatchJobLogGroupEnvironment(containerLogGroup),
        },
    });

    orchestrationBus.grantPutEventsTo(fun);
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["batch:SubmitJob", "batch:DescribeJobs"],
            resources: [
                `arn:${ServiceHelper.Partition()}:batch:${region}:${account}:job-queue/${
                    batchJobQueue.jobQueueName
                }`,
                `arn:${ServiceHelper.Partition()}:batch:${region}:${account}:job-definition/${
                    batchJobDefinition.jobDefinitionName
                }*`,
            ],
        })
    );

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);
    // A failed submit is reported on the inner token so the state machine reaches pipelineEnd.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );
    return fun;
}

export function buildInvokeAgentRuntimeFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    agentRuntimeArn: string,
    warmSessionSlots: number,
    sessionNamespace: string,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const region = cdk.Stack.of(scope).region;
    const account = cdk.Stack.of(scope).account;
    const name = "invokeAgentRuntime";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(LAMBDA_CODE_PATH),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        // The runtime acknowledges at once (the job runs as a background task), so the invoke itself
        // is short; the timeout covers a cold runtime start.
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcProps(config, vpc, subnets),
        environment: {
            AGENT_RUNTIME_ARN: agentRuntimeArn,
            AGENT_RUNTIME_QUALIFIER: "DEFAULT",
            WARM_SESSION_SLOTS: warmSessionSlots.toString(),
            SESSION_NAMESPACE: sessionNamespace,
        },
    });

    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["bedrock-agentcore:InvokeAgentRuntime"],
            // The runtime ARN plus its endpoints (`<runtime-arn>/runtime-endpoint/<name>`).
            resources: [agentRuntimeArn, `${agentRuntimeArn}/runtime-endpoint/*`],
        })
    );

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);
    // A rejected or failed invoke is reported on the inner token so the state machine reaches pipelineEnd.
    fun.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
            resources: [`arn:${ServiceHelper.Partition()}:states:${region}:${account}:*`],
        })
    );
    return fun;
}
