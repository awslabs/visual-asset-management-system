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
import * as ecr_assets from "aws-cdk-lib/aws-ecr-assets";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import { Duration } from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../../../config/config";
import * as Config from "../../../../../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    globalLambdaEnvironmentsAndPermissions,
    suppressCdkNagLambda,
    suppressCdkNagErrorsByGrantReadWrite,
    grantReadWritePermissionsToAllAssetBuckets,
    grantReadPermissionsToAllAssetBuckets,
} from "../../../../../helper/security";
import * as ServiceHelper from "../../../../../helper/service-helper";
import { Service } from "../../../../../helper/service-helper";

/** The zip handlers of the pipeline: one module per state-machine task plus the two entry Lambdas. */
const LAMBDA_DIR = path.join(
    __dirname,
    "../../../../../../../backendPipelines/system/genAiMetadata/lambda"
);
/** The Blender and media Lambda images. */
const CONTAINERS_DIR = path.join(
    __dirname,
    "../../../../../../../backendPipelines/system/genAiMetadata/containers"
);
/** The 3D thumbnail container directory, whose Dockerfile.lambda builds the render3d Lambda image. */
const RENDER3D_IMAGE_DIR = path.join(
    __dirname,
    "../../../../../../../backendPipelines/preview/3dThumbnail/container"
);

// The rendering images load whole 3D scenes and point clouds into memory and write frames to disk.
const RENDER_IMAGE_MEMORY_MB = 10240;
const RENDER_IMAGE_TIMEOUT = Duration.seconds(900);
const RENDER_IMAGE_EPHEMERAL_STORAGE = cdk.Size.mebibytes(10240);
// The media image decodes single video frames, documents and audio headers.
const MEDIA_IMAGE_MEMORY_MB = 3008;
const MEDIA_IMAGE_TIMEOUT = Duration.seconds(600);
const MEDIA_IMAGE_EPHEMERAL_STORAGE = cdk.Size.mebibytes(4096);
// One segment of a file per invocation: a few frames or one text chunk, one Bedrock call each.
const SEGMENT_ANALYZE_TIMEOUT = Duration.seconds(300);

/**
 * The foundation-model id underneath a cross-Region inference-profile prefix, which is what the
 * `foundation-model/` ARNs in a Bedrock grant name. The prefix set is partition-specific:
 * `global.`/`us.` in the commercial partition, `us-gov.` in GovCloud, `eu.`/`apac.` for the regional
 * profiles. Anchored, and only the leading prefix is removed.
 */
function foundationModelId(modelId: string): string {
    return modelId.replace(/^(global|us-gov|us|eu|apac)\./, "");
}

/** Every function of the pipeline sits in the isolated pipeline subnets only when every Lambda does. */
function lambdasInVpc(config: Config.Config): boolean {
    return config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas;
}

function vpcPlacement(
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    securityGroups: ec2.ISecurityGroup[]
): Pick<lambda.FunctionOptions, "vpc" | "vpcSubnets" | "securityGroups"> {
    return lambdasInVpc(config)
        ? { vpc: vpc, vpcSubnets: { subnets: subnets }, securityGroups: securityGroups }
        : {};
}

/**
 * Bedrock invoke grant for one model id: the model's Region-qualified and Region-less
 * `foundation-model` ARNs and, for models reached through a cross-Region inference profile, the
 * account's inference-profile namespace (the profile id is chosen by the operator and is not known
 * at synthesis).
 */
function bedrockInvokeStatement(
    config: Config.Config,
    modelId: string,
    withInferenceProfiles: boolean
): iam.PolicyStatement {
    const model = foundationModelId(modelId);
    const resources = [
        `arn:${ServiceHelper.Partition()}:bedrock:` +
            config.env.region +
            "::foundation-model/" +
            model,
        `arn:${ServiceHelper.Partition()}:bedrock:` + "::foundation-model/" + model,
    ];
    if (withInferenceProfiles) {
        resources.push(
            `arn:${ServiceHelper.Partition()}:bedrock:` +
                config.env.region +
                ":" +
                config.env.account +
                ":inference-profile/*"
        );
    }
    return new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: withInferenceProfiles
            ? ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
            : ["bedrock:InvokeModel"],
        resources: resources,
    });
}

export function buildVamsExecuteSystemGenAiMetadataFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    openPipelineLambdaFunction: lambda.IFunction,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "vamsExecuteSystemGenAiMetadataPipeline";
    const fun = new lambda.Function(scope, "VamsExecuteSystemGenAiMetadataPipeline", {
        code: lambda.Code.fromAsset(LAMBDA_DIR),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcPlacement(config, vpc, subnets, pipelineSecurityGroups),
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

    // The workflow task waits on a callback token, so a failure in this lambda is reported back to
    // Step Functions instead of leaving the task pending until its timeout.
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
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    orchestrationBus: events.IEventBus,
    stateMachineLogGroup: logs.ILogGroup,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "openPipeline";
    const fun = new lambda.Function(scope, "SystemGenAiMetadataOpenPipeline", {
        code: lambda.Code.fromAsset(LAMBDA_DIR),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcPlacement(config, vpc, subnets, pipelineSecurityGroups),
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

    // A rejected input (extension gate, missing token) is reported on the callback token here.
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

export function buildConstructPipelineFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "constructPipeline";
    const pipeline = config.app.pipelines.useSystemGenAiMetadata;
    const fun = new lambda.Function(scope, "SystemGenAiMetadataConstructPipeline", {
        code: lambda.Code.fromAsset(LAMBDA_DIR),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcPlacement(config, vpc, subnets, pipelineSecurityGroups),
        environment: {
            VECTOR_SEARCH_ENABLED: config.app.vectorSearch.enabled ? "true" : "false",
            USE_FARGATE_RENDERER: pipeline.useFargateRenderer ? "true" : "false",
            MAX_INPUT_FILE_SIZE_MB: String(pipeline.lambdaLimits.maxInputFileSizeMb),
            MAX_POINT_CLOUD_POINTS: String(pipeline.lambdaLimits.maxPointCloudPoints),
        },
    });

    grantReadWritePermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantReadWrite(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    suppressCdkNagLambda(fun);
    return fun;
}

export function buildGenerateMetadataFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "generateMetadata";
    const pipeline = config.app.pipelines.useSystemGenAiMetadata;
    const analysisModelId = pipeline.bedrockAnalysisModelId;
    const guardrail = pipeline.bedrockGuardrail;

    const fun = new lambda.Function(scope, "SystemGenAiMetadataGenerateMetadata", {
        code: lambda.Code.fromAsset(LAMBDA_DIR),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcPlacement(config, vpc, subnets, pipelineSecurityGroups),
        environment: {
            BEDROCK_ANALYSIS_MODEL_ID: analysisModelId,
            BEDROCK_GUARDRAIL_IDENTIFIER: guardrail.guardrailIdentifier,
            BEDROCK_GUARDRAIL_VERSION: guardrail.guardrailVersion,
        },
    });

    grantReadWritePermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantReadWrite(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    fun.addToRolePolicy(bedrockInvokeStatement(config, analysisModelId, true));

    if (guardrail.guardrailIdentifier !== "") {
        // arn:<partition>:bedrock:<region>:<account>:guardrail/<identifier> — the one guardrail the
        // analysis prompts are sent with.
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["bedrock:ApplyGuardrail"],
                resources: [Service("BEDROCK").ARN("guardrail", guardrail.guardrailIdentifier)],
            })
        );
    }

    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Cross-Region inference profiles are created by Bedrock under an account-scoped id " +
                    "that is not known at synthesis, so the analysis model's inference-profile grant is a " +
                    "wildcard on this account's inference-profile namespace in this Region only; the " +
                    "foundation-model ARNs beside it are exact.",
                appliesTo: [{ regex: "/^Resource::arn:.*:bedrock:.*:inference-profile/\\*$/g" }],
            },
        ],
        true
    );

    suppressCdkNagLambda(fun);
    return fun;
}

export function buildGenerateEmbeddingFunction(
    scope: Construct,
    lambdaCommonBaseLayer: LayerVersion,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    orchestrationBus: events.IEventBus,
    kmsKey?: kms.IKey
): lambda.Function {
    const name = "generateEmbedding";
    const embeddingModelId = config.app.vectorSearch.embeddingModelId;

    const fun = new lambda.Function(scope, "SystemGenAiMetadataGenerateEmbedding", {
        code: lambda.Code.fromAsset(LAMBDA_DIR),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcPlacement(config, vpc, subnets, pipelineSecurityGroups),
        environment: {
            EMBEDDING_MODEL_ID: embeddingModelId,
            EMBEDDING_DIMENSIONS: String(config.app.vectorSearch.embeddingDimensions),
            ORCHESTRATION_BUS_NAME: orchestrationBus.eventBusName,
        },
    });

    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantReadWrite(fun);
    orchestrationBus.grantPutEventsTo(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    // Embedding models are invoked by their foundation-model id; they have no inference profiles.
    fun.addToRolePolicy(bedrockInvokeStatement(config, embeddingModelId, false));

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
    const fun = new lambda.Function(scope, "SystemGenAiMetadataPipelineEnd", {
        code: lambda.Code.fromAsset(LAMBDA_DIR),
        handler: `${name}.lambda_handler`,
        runtime: LAMBDA_PYTHON_RUNTIME,
        layers: [lambdaCommonBaseLayer],
        timeout: Duration.minutes(15),
        memorySize: Config.LAMBDA_MEMORY_SIZE,
        ...vpcPlacement(config, vpc, subnets, pipelineSecurityGroups),
        environment: {},
    });

    grantReadPermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantRead(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);

    // The only state that reports on the parent workflow's callback token.
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

/** Common grants of the three branch-task images: asset buckets, the auxiliary bucket and the key. */
function grantImageFunction(
    scope: Construct,
    fun: lambda.DockerImageFunction,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    kmsKey?: kms.IKey
): void {
    grantReadWritePermissionsToAllAssetBuckets(fun);
    assetAuxiliaryBucket.grantReadWrite(fun);
    kmsKeyLambdaPermissionAddToResourcePolicy(fun, kmsKey);
    globalLambdaEnvironmentsAndPermissions(fun, config);
    suppressCdkNagErrorsByGrantReadWrite(scope);
    suppressCdkNagLambda(fun);
}

export function buildBlenderRenderFunction(
    scope: Construct,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.DockerImageFunction {
    const fun = new lambda.DockerImageFunction(scope, "SystemGenAiMetadataBlenderRender", {
        code: lambda.DockerImageCode.fromImageAsset(path.join(CONTAINERS_DIR, "blender"), {
            file: "Dockerfile",
            platform: ecr_assets.Platform.LINUX_AMD64,
        }),
        timeout: RENDER_IMAGE_TIMEOUT,
        memorySize: RENDER_IMAGE_MEMORY_MB,
        ephemeralStorageSize: RENDER_IMAGE_EPHEMERAL_STORAGE,
        ...vpcPlacement(config, vpc, subnets, pipelineSecurityGroups),
    });
    grantImageFunction(scope, fun, assetAuxiliaryBucket, config, kmsKey);
    return fun;
}

export function buildRender3dFunction(
    scope: Construct,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.DockerImageFunction {
    const fun = new lambda.DockerImageFunction(scope, "SystemGenAiMetadataRender3d", {
        code: lambda.DockerImageCode.fromImageAsset(RENDER3D_IMAGE_DIR, {
            file: "Dockerfile.lambda",
            platform: ecr_assets.Platform.LINUX_AMD64,
        }),
        timeout: RENDER_IMAGE_TIMEOUT,
        memorySize: RENDER_IMAGE_MEMORY_MB,
        ephemeralStorageSize: RENDER_IMAGE_EPHEMERAL_STORAGE,
        ...vpcPlacement(config, vpc, subnets, pipelineSecurityGroups),
    });
    grantImageFunction(scope, fun, assetAuxiliaryBucket, config, kmsKey);
    return fun;
}

export function buildMediaExtractFunction(
    scope: Construct,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    kmsKey?: kms.IKey
): lambda.DockerImageFunction {
    const fun = new lambda.DockerImageFunction(scope, "SystemGenAiMetadataMediaExtract", {
        code: lambda.DockerImageCode.fromImageAsset(path.join(CONTAINERS_DIR, "media"), {
            file: "Dockerfile",
            platform: ecr_assets.Platform.LINUX_AMD64,
        }),
        timeout: MEDIA_IMAGE_TIMEOUT,
        memorySize: MEDIA_IMAGE_MEMORY_MB,
        ephemeralStorageSize: MEDIA_IMAGE_EPHEMERAL_STORAGE,
        ...vpcPlacement(config, vpc, subnets, pipelineSecurityGroups),
    });
    grantImageFunction(scope, fun, assetAuxiliaryBucket, config, kmsKey);
    return fun;
}

/**
 * The media image's second function: the child of the video-segment Distributed Map. One invocation
 * analyses one time window (or one text chunk) of a file with the analysis model, embeds the result
 * with the embedding model, writes the segment's embedding document and publishes its event.
 */
export function buildSegmentAnalyzeFunction(
    scope: Construct,
    assetAuxiliaryBucket: s3.IBucket,
    config: Config.Config,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[],
    pipelineSecurityGroups: ec2.ISecurityGroup[],
    orchestrationBus: events.IEventBus,
    kmsKey?: kms.IKey
): lambda.DockerImageFunction {
    const pipeline = config.app.pipelines.useSystemGenAiMetadata;
    const analysisModelId = pipeline.bedrockAnalysisModelId;
    const embeddingModelId = config.app.vectorSearch.embeddingModelId;
    const guardrail = pipeline.bedrockGuardrail;

    const fun = new lambda.DockerImageFunction(scope, "SystemGenAiMetadataSegmentAnalyze", {
        code: lambda.DockerImageCode.fromImageAsset(path.join(CONTAINERS_DIR, "media"), {
            file: "Dockerfile",
            platform: ecr_assets.Platform.LINUX_AMD64,
            cmd: ["segment_handler.lambda_handler"],
        }),
        timeout: SEGMENT_ANALYZE_TIMEOUT,
        memorySize: MEDIA_IMAGE_MEMORY_MB,
        // The whole file is downloaded when a ranged read of it is not possible.
        ephemeralStorageSize: MEDIA_IMAGE_EPHEMERAL_STORAGE,
        ...vpcPlacement(config, vpc, subnets, pipelineSecurityGroups),
        environment: {
            BEDROCK_ANALYSIS_MODEL_ID: analysisModelId,
            EMBEDDING_MODEL_ID: embeddingModelId,
            EMBEDDING_DIMENSIONS: String(config.app.vectorSearch.embeddingDimensions),
            ORCHESTRATION_BUS_NAME: orchestrationBus.eventBusName,
            BEDROCK_GUARDRAIL_IDENTIFIER: guardrail.guardrailIdentifier,
            BEDROCK_GUARDRAIL_VERSION: guardrail.guardrailVersion,
        },
    });

    // Reads the file from its asset bucket and writes the segment's result there; the embedding
    // document and the frames it decodes go to the auxiliary bucket.
    grantImageFunction(scope, fun, assetAuxiliaryBucket, config, kmsKey);
    orchestrationBus.grantPutEventsTo(fun);

    fun.addToRolePolicy(bedrockInvokeStatement(config, analysisModelId, true));
    // The Map runs only on the vector-search path, so the embedding grant follows that flag.
    if (config.app.vectorSearch.enabled) {
        fun.addToRolePolicy(bedrockInvokeStatement(config, embeddingModelId, false));
    }

    if (guardrail.guardrailIdentifier !== "") {
        // arn:<partition>:bedrock:<region>:<account>:guardrail/<identifier> — the one guardrail the
        // per-segment analysis prompts are sent with.
        fun.addToRolePolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                actions: ["bedrock:ApplyGuardrail"],
                resources: [Service("BEDROCK").ARN("guardrail", guardrail.guardrailIdentifier)],
            })
        );
    }

    NagSuppressions.addResourceSuppressions(
        fun,
        [
            {
                id: "AwsSolutions-IAM5",
                reason:
                    "Cross-Region inference profiles are created by Bedrock under an account-scoped id " +
                    "that is not known at synthesis, so the analysis model's inference-profile grant is a " +
                    "wildcard on this account's inference-profile namespace in this Region only; the " +
                    "foundation-model ARNs beside it are exact.",
                appliesTo: [{ regex: "/^Resource::arn:.*:bedrock:.*:inference-profile/\\*$/g" }],
            },
        ],
        true
    );
    return fun;
}
