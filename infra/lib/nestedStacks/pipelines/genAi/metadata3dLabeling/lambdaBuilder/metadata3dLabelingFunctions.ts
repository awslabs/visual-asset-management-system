/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../../config/config";
import * as Config from "../../../../../../config/config";
import * as s3AssetBuckets from "../../../../../helper/s3AssetBuckets";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
} from "../../../../../helper/security";
import { suppressCdkNagLambda } from "../../../../../helper/security";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { suppressCdkNagErrorsByGrantReadWrite } from "../../../../../helper/security";
import {
    grantReadWritePermissionsToAllAssetBuckets,
    grantReadPermissionsToAllAssetBuckets,
} from "../../../../../helper/security";

export function buildVamsExecuteMetadata3dLabelingPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    openPipelineLambdaFunction: lambda.IFunction,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "vamsExecuteGenAiMetadata3dLabelingPipeline";
    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(
                __dirname,
                `../../../../../../../backendPipelines/genAi/metadata3dLabeling/lambda`
            )
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            OPEN_PIPELINE_FUNCTION_NAME: openPipelineLambdaFunction.functionName,
        },
    });

    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantRead(fun);
    openPipelineLambdaFunction.grantInvoke(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    // The workflow task waits on a callback token, so a failure in this lambda must be reported
    // back to Step Functions instead of leaving the task pending until its timeout.
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
    const name = "openPipeline";
    const vpcSubnets = vpc.selectSubnets({
        subnets: subnets,
    });

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(
                __dirname,
                `../../../../../../../backendPipelines/genAi/metadata3dLabeling/lambda`
            )
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        environment: {
            STATE_MACHINE_ARN: pipelineStateMachine.stateMachineArn,
            ALLOWED_INPUT_FILEEXTENSIONS: allowedPipelineInputExtensions,
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

    const stateTaskPolicy = new iam.PolicyStatement({
        actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
        resources: [
            `arn:${ServiceHelper.Partition()}:states:${config.env.region}:${config.env.account}:*`,
        ],
    });
    fun.addToRolePolicy(stateTaskPolicy);

    suppressCdkNagLambda(fun);
    return fun;
}

export function buildConstructPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "constructPipeline";
    const vpcSubnets = vpc.selectSubnets({
        subnets: subnets,
    });

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(
                __dirname,
                `../../../../../../../backendPipelines/genAi/metadata3dLabeling/lambda`
            )
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        securityGroups:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? pipelineSecurityGroups
                : undefined,
    });

    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);

    suppressCdkNagLambda(fun);
    return fun;
}

export function buildMetadataGenerationPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    lambdaMetadataGenerationLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "metadataGenerationPipeline";
    const vpcSubnets = vpc.selectSubnets({
        subnets: subnets,
    });

    let bedrockModelId = "global.anthropic.claude-sonnet-4-5-20250929-v1:0";
    if (
        config.app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId &&
        config.app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId != "" &&
        config.app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId != "UNDEFINED"
    ) {
        bedrockModelId = config.app.pipelines.useGenAiMetadata3dLabeling.bedrockModelId;
    }

    // The foundation-model id underneath any cross-Region inference-profile prefix, which is what the
    // `foundation-model/*` ARN in the grant below has to name. The prefix set is partition-specific:
    // `global.`/`us.` in the commercial partition, `us-gov.` in GovCloud, `eu.`/`apac.` for the
    // regional profiles. Anchored, and only the leading prefix is removed — a bare `.replace()` would
    // also strip the same text from the middle of a model name.
    const bedrockModelPermissions = bedrockModelId.replace(/^(global|us-gov|us|eu|apac)\./, "");

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(
                __dirname,
                `../../../../../../../backendPipelines/genAi/metadata3dLabeling/lambda`
            )
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer, lambdaMetadataGenerationLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        securityGroups:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? pipelineSecurityGroups
                : undefined,

        environment: {
            BEDROCK_MODEL_ID: bedrockModelId,
        },
    });

    grantReadWritePermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantReadWrite(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    // Add permissions to Lambda function to access Bedrock
    const bedrockPolicy = new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: [
            `arn:${ServiceHelper.Partition()}:bedrock:` +
                config.env.region +
                "::foundation-model/" +
                bedrockModelPermissions,
            `arn:${ServiceHelper.Partition()}:bedrock:` +
                "::foundation-model/" +
                bedrockModelPermissions,
            `arn:${ServiceHelper.Partition()}:bedrock:` +
                config.env.region +
                ":" +
                config.env.account +
                ":inference-profile/*",
        ],
    });
    fun.addToRolePolicy(bedrockPolicy);

    // Add permissions to Lambda function to access Rekognition
    // No resource-level permissioning. * Resource needed. https://docs.aws.amazon.com/rekognition/latest/dg/security_iam_id-based-policy-examples.html
    const rekognitionPolicy = new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
            "rekognition:ListCollections",
            "rekognition:DetectModerationLabels",
            "rekognition:GetLabelDetection",
            "rekognition:DetectText",
            "rekognition:DetectLabels",
            "rekognition:DetectProtectiveEquipment",
            "rekognition:ListTagsForResource",
            "rekognition:ListDatasetEntries",
            "rekognition:ListDatasetLabels",
            "rekognition:DescribeDataset",
            "rekognition:DetectCustomLabels",
            "rekognition:GetTextDetection",
            "rekognition:GetSegmentDetection",
            "rekognition:DescribeStreamProcessor",
            "rekognition:ListStreamProcessors",
            "rekognition:DescribeProjects",
            "rekognition:DescribeProjectVersions",
        ],
        resources: ["*"],
    });
    fun.addToRolePolicy(rekognitionPolicy);

    // The two resource wildcards this handler genuinely needs, each named rather than covered by a
    // blanket. Amazon Rekognition's detection APIs analyse bytes supplied in the request and publish no
    // resource to scope to (see the link above the policy). The Bedrock inference-profile wildcard
    // exists because the profile is chosen by the operator through
    // `useGenAiMetadata3dLabeling.bedrockModelId` and its id is not known at synthesis; the
    // foundation-model ARNs beside it are already exact.
    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Amazon Rekognition's Detect*/Describe* APIs operate on image bytes passed in the " +
                    "request and support no resource-level permissions, so Resource must be '*'. " +
                    "https://docs.aws.amazon.com/rekognition/latest/dg/security_iam_id-based-policy-examples.html",
                appliesTo: [{ regex: "/^Resource::\\*$/g" }],
            },
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "The Bedrock cross-Region inference profile is selected by the operator through " +
                    "pipelines.useGenAiMetadata3dLabeling.bedrockModelId, so its identifier is not known " +
                    "at synthesis. Scoped to this account and Region, and to inference profiles only.",
                appliesTo: [{ regex: "/^Resource::arn:.*:bedrock:.*:inference-profile/\\*$/g" }],
            },
        ],
        true
    );

    suppressCdkNagLambda(fun);
    return fun;
}

export function buildPipelineEndFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "pipelineEnd";
    const vpcSubnets = vpc.selectSubnets({
        subnets: subnets,
    });

    const fun = new lambda.Function(scope, name, {
        code: lambda.Code.fromAsset(
            path.join(
                __dirname,
                `../../../../../../../backendPipelines/genAi/metadata3dLabeling/lambda`
            )
        ),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(5),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        vpc:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? vpc
                : undefined, //Use VPC when flagged to use for all lambdas
        vpcSubnets:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? { subnets: subnets }
                : undefined,
        securityGroups:
            config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas
                ? pipelineSecurityGroups
                : undefined,
        environment: {},
    });

    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantRead(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    const stateTaskPolicy = new iam.PolicyStatement({
        actions: ["states:SendTaskSuccess", "states:SendTaskFailure"],
        resources: [
            `arn:${ServiceHelper.Partition()}:states:${config.env.region}:${config.env.account}:*`,
        ],
    });
    fun.addToRolePolicy(stateTaskPolicy);

    suppressCdkNagLambda(fun);
    return fun;
}
