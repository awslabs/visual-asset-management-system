/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The SYSTEM GenAI metadata pipeline construct, synthesized on a plain stack with and without the
 * Fargate render branch and with and without a Bedrock guardrail. Pins the state machine's shape
 * (state names, the branch-task contract on every Lambda task, the catch routes, the choices), the
 * three container-image functions' sizes, the log-group name and the per-function grants.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import {
    SystemGenAiMetadataConstruct,
    allowedInputFileExtensions,
} from "../../lib/nestedStacks/pipelines/system/genAiMetadata/constructs/systemGenAiMetadata-construct";
import {
    SYSTEM_GENAI_METADATA_PIPELINE_ID,
    SYSTEM_GENAI_METADATA_WORKFLOW_ID,
    SYSTEM_WORKFLOW_DATABASE_ID,
} from "../../common/systemPipelines";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

const EXPECTED_STATES_NO_FARGATE = [
    "ConstructPipelineTask",
    "RenderBranchChoice",
    "BlenderRenderTask",
    "UsdFallbackChoice",
    "Render3dTask",
    "MediaExtractTask",
    "NoRenderPass",
    "RenderDegradePass",
    "GenerateMetadataTask",
    "VectorSearchChoice",
    "GenerateEmbeddingTask",
    "EmbeddingSkippedPass",
    "HandleConstructPipelineError",
    "HandleGenerateMetadataError",
    "HandleGenerateEmbeddingError",
    "PipelineEndTask",
    "EndStatesChoice",
    "SuccessState",
    "FailState",
];

const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = REGION;
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.useGlobalVpc.enabled = true;
    config.app.useGlobalVpc.useForAllLambdas = false;
    config.app.pipelines.useSystemGenAiMetadata.enabled = true;
    config.app.pipelines.useSystemGenAiMetadata.autoRegisterWithVAMS = true;
    config.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId =
        "global.anthropic.claude-haiku-4-5-20251001-v1:0";
    config.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = {
        guardrailIdentifier: "",
        guardrailVersion: "",
    };
    config.app.vectorSearch.enabled = true;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

const synth = (id: string, mutate?: (c: Config.Config) => void): Template => {
    const config = createMockConfig();
    mutate?.(config);
    Service.SetConfig(config);

    const app = newTestApp();
    const stack = new cdk.Stack(app, id, { env: { account: ACCOUNT, region: REGION } });
    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const securityGroups = [new ec2.SecurityGroup(stack, "Sg", { vpc })];
    const assetBucket = new s3.Bucket(stack, "AssetBucket");
    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(assetBucket, "/", "db", undefined, undefined, true);
    const storage = {
        encryption: { kmsKey: new kms.Key(stack, "Key") },
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
        },
        eventBridge: { orchestrationBus: new events.EventBus(stack, "Bus") },
    } as unknown as storageResources;

    const construct = new SystemGenAiMetadataConstruct(stack, "SystemGenAiMetadata", {
        config,
        storageResources: storage,
        vpc,
        pipelineSubnets: vpc.isolatedSubnets.length ? vpc.isolatedSubnets : vpc.privateSubnets,
        pipelineSecurityGroups: securityGroups,
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        importGlobalPipelineWorkflowV2FunctionName: "vams-test-importFunction",
    });
    return Template.fromStack(construct);
};

/** The ASL of the single state machine in the template, tokens replaced by a placeholder. */
const definition = (template: Template): any => {
    const machines = Object.values(template.findResources("AWS::StepFunctions::StateMachine"));
    expect(machines).toHaveLength(1);
    const raw = (machines[0] as any).Properties.DefinitionString;
    const parts: unknown[] = raw?.["Fn::Join"]?.[1] ?? [raw];
    return JSON.parse(parts.map((p) => (typeof p === "string" ? p : "TOKEN")).join(""));
};

const statementsOf = (template: Template, roleIdFragment: string): any[] =>
    Object.entries(template.findResources("AWS::IAM::Policy"))
        .filter(([logicalId]) => logicalId.includes(roleIdFragment))
        .flatMap(([, policy]) => (policy as any).Properties.PolicyDocument.Statement);

const actionsOf = (statements: any[]): string[] =>
    statements.flatMap((s) => (Array.isArray(s.Action) ? s.Action : [s.Action]));

const withActions = (statements: any[], action: string): any[] =>
    statements.filter((s) => actionsOf([s]).includes(action));

describe("SYSTEM GenAI metadata pipeline construct", () => {
    const lambdaOnly = synth("LambdaOnly");
    const fargate = synth("Fargate", (c) => {
        c.app.pipelines.useSystemGenAiMetadata.useFargateRenderer = true;
    });
    const guarded = synth("Guarded", (c) => {
        c.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = {
            guardrailIdentifier: "gr-test",
            guardrailVersion: "1",
        };
    });
    const asl = definition(lambdaOnly);
    const aslFargate = definition(fargate);

    test("starts at ConstructPipelineTask and carries the expected states", () => {
        expect(asl.StartAt).toBe("ConstructPipelineTask");
        expect(Object.keys(asl.States).sort()).toEqual([...EXPECTED_STATES_NO_FARGATE].sort());
        expect(Object.keys(aslFargate.States).sort()).toEqual(
            [...EXPECTED_STATES_NO_FARGATE, "FargateRenderJob"].sort()
        );
        expect(asl.TimeoutSeconds).toBe(18000);
    });

    test("every Lambda task replaces the state with its Payload (branch-task contract)", () => {
        const lambdaTasks = Object.entries(aslFargate.States).filter(([, s]: [string, any]) =>
            String(s.Resource ?? "").endsWith(":lambda:invoke")
        );
        expect(lambdaTasks.map(([id]) => id).sort()).toEqual(
            [
                "ConstructPipelineTask",
                "BlenderRenderTask",
                "Render3dTask",
                "MediaExtractTask",
                "GenerateMetadataTask",
                "GenerateEmbeddingTask",
                "PipelineEndTask",
            ].sort()
        );
        for (const [, state] of lambdaTasks as [string, any][]) {
            expect(state.OutputPath).toBe("$.Payload");
            expect(state.ResultPath).toBeUndefined();
        }
    });

    test("task failures are caught to their handler Pass, which runs PipelineEndTask", () => {
        for (const [task, handler] of [
            ["ConstructPipelineTask", "HandleConstructPipelineError"],
            ["GenerateMetadataTask", "HandleGenerateMetadataError"],
            ["GenerateEmbeddingTask", "HandleGenerateEmbeddingError"],
        ]) {
            const catches = asl.States[task].Catch;
            expect(catches).toHaveLength(1);
            expect(catches[0].ResultPath).toBe("$.error");
            expect(catches[0].Next).toBe(handler);
            expect(asl.States[handler].Type).toBe("Pass");
            expect(asl.States[handler].Next).toBe("PipelineEndTask");
        }
    });

    test("render faults degrade to analysis rather than failing", () => {
        for (const task of ["BlenderRenderTask", "Render3dTask", "MediaExtractTask"]) {
            const catches = asl.States[task].Catch;
            expect(catches).toHaveLength(1);
            expect(catches[0].ResultPath).toBe("$.renderError");
            expect(catches[0].Next).toBe("RenderDegradePass");
        }
        const fargateCatches = aslFargate.States.FargateRenderJob.Catch;
        expect(fargateCatches).toHaveLength(1);
        expect(fargateCatches[0].ResultPath).toBe("$.renderError");
        expect(fargateCatches[0].Next).toBe("RenderDegradePass");
        expect(asl.States.RenderDegradePass.Type).toBe("Pass");
        expect(asl.States.RenderDegradePass.Next).toBe("GenerateMetadataTask");
        expect(asl.States.NoRenderPass.Type).toBe("Pass");
        expect(asl.States.NoRenderPass.Next).toBe("GenerateMetadataTask");
        expect(asl.States.Render3dTask.Next).toBe("GenerateMetadataTask");
        expect(asl.States.MediaExtractTask.Next).toBe("GenerateMetadataTask");
    });

    test("RenderBranchChoice routes on $.renderBranch and defaults to NoRenderPass", () => {
        const route = (a: any) =>
            Object.fromEntries(
                a.States.RenderBranchChoice.Choices.map((c: any) => [c.StringEquals, c.Next])
            );
        for (const c of asl.States.RenderBranchChoice.Choices) {
            expect(c.Variable).toBe("$.renderBranch");
        }
        expect(route(asl)).toEqual({
            BLENDER: "BlenderRenderTask",
            RENDER3D: "Render3dTask",
            MEDIA: "MediaExtractTask",
        });
        expect(route(aslFargate)).toEqual({
            BLENDER: "BlenderRenderTask",
            RENDER3D: "Render3dTask",
            MEDIA: "MediaExtractTask",
            FARGATE: "FargateRenderJob",
        });
        expect(asl.States.RenderBranchChoice.Default).toBe("NoRenderPass");
        expect(asl.States.ConstructPipelineTask.Next).toBe("RenderBranchChoice");
    });

    test("a USD scene Blender could not render falls back to Render3dTask", () => {
        expect(asl.States.BlenderRenderTask.Next).toBe("UsdFallbackChoice");
        const choice = asl.States.UsdFallbackChoice;
        expect(choice.Choices).toHaveLength(1);
        expect(choice.Choices[0].Next).toBe("Render3dTask");
        expect(choice.Choices[0].And).toEqual([
            { Variable: "$.fileClass", IsPresent: true },
            { Variable: "$.fileClass", StringEquals: "usd" },
            { Variable: "$.renderSkipped", IsPresent: true },
            { Variable: "$.renderSkipped", StringEquals: "error" },
        ]);
        expect(choice.Default).toBe("GenerateMetadataTask");
    });

    test("embeddings follow a completed analysis with vector search on, otherwise SKIPPED", () => {
        expect(asl.States.GenerateMetadataTask.Next).toBe("VectorSearchChoice");
        const choice = asl.States.VectorSearchChoice;
        expect(choice.Choices).toHaveLength(1);
        expect(choice.Choices[0].Next).toBe("GenerateEmbeddingTask");
        expect(choice.Choices[0].And).toEqual([
            { Variable: "$.vectorSearchEnabled", IsPresent: true },
            { Variable: "$.vectorSearchEnabled", BooleanEquals: true },
            { Variable: "$.analysisStatus", IsPresent: true },
            { Not: { Variable: "$.analysisStatus", StringEquals: "FAILED" } },
        ]);
        expect(choice.Default).toBe("EmbeddingSkippedPass");
        expect(asl.States.EmbeddingSkippedPass).toMatchObject({
            Type: "Pass",
            Result: "SKIPPED",
            ResultPath: "$.embeddingStatus",
            Next: "PipelineEndTask",
        });
        expect(asl.States.GenerateEmbeddingTask.Next).toBe("PipelineEndTask");
    });

    test("PipelineEndTask ends the execution on $.error", () => {
        expect(asl.States.PipelineEndTask.Next).toBe("EndStatesChoice");
        const choice = asl.States.EndStatesChoice;
        expect(choice.Choices).toEqual([
            { Variable: "$.error", IsPresent: true, Next: "FailState" },
        ]);
        expect(choice.Default).toBe("SuccessState");
        expect(asl.States.SuccessState.Type).toBe("Succeed");
        expect(asl.States.FailState.Type).toBe("Fail");
    });

    test("the Fargate render job runs the analysis entry module on the whole state", () => {
        const job = aslFargate.States.FargateRenderJob;
        expect(String(job.Resource)).toMatch(/:batch:submitJob\.sync$/);
        expect(job.ResultPath).toBe("$.fargateRenderResult");
        expect(job.Next).toBe("GenerateMetadataTask");
        // The thumbnail image's ENTRYPOINT is `python3 -m preview_pipeline`, which dispatches this
        // argument to preview_pipeline/analysis/batch_job.py.
        expect(job.Parameters.ContainerOverrides.Command).toEqual(["analysisBatch"]);
        const env = job.Parameters.ContainerOverrides.Environment.find(
            (e: any) => e.Name === "ANALYSIS_STATE_JSON"
        );
        expect(env["Value.$"]).toBe("States.JsonToString($)");
        expect(fargate.findResources("AWS::Batch::JobDefinition")).toMatchObject({});
        expect(Object.keys(fargate.findResources("AWS::Batch::JobDefinition"))).toHaveLength(1);
        expect(Object.keys(lambdaOnly.findResources("AWS::Batch::JobDefinition"))).toHaveLength(0);
    });

    test("three container-image functions at their sizes", () => {
        const images = Object.values(lambdaOnly.findResources("AWS::Lambda::Function"))
            .map((f: any) => f.Properties)
            .filter((p: any) => p.PackageType === "Image")
            .map((p: any) => [p.MemorySize, p.Timeout, p.EphemeralStorage?.Size].join("/"))
            .sort();
        expect(images).toEqual(["10240/900/10240", "10240/900/10240", "3008/600/4096"].sort());
    });

    test("the state machine log group is a vended log group named for the pipeline", () => {
        const names = Object.values(lambdaOnly.findResources("AWS::Logs::LogGroup")).map((g: any) =>
            JSON.stringify(g.Properties.LogGroupName)
        );
        expect(
            names.some((n) => n.includes("/aws/vendedlogs/VAMSStateMachine-SystemGenAiMetadata"))
        ).toBe(true);
    });

    test("openPipeline receives the extension allow list, a multi-member dotted list", () => {
        expect(allowedInputFileExtensions.split(",").length).toBeGreaterThan(2);
        for (const ext of allowedInputFileExtensions.split(",")) {
            expect(ext).toMatch(/^\.[a-z0-9_]+$/);
        }
        const open = Object.values(lambdaOnly.findResources("AWS::Lambda::Function")).find(
            (f: any) => f.Properties.Handler === "openPipeline.lambda_handler"
        ) as any;
        expect(open.Properties.Environment.Variables.ALLOWED_INPUT_FILEEXTENSIONS).toBe(
            allowedInputFileExtensions
        );
    });

    test("the handlers are the shipped module names with their environment", () => {
        const byHandler: Record<string, any> = {};
        for (const f of Object.values(lambdaOnly.findResources("AWS::Lambda::Function")) as any[]) {
            // The registration's custom-resource provider functions are not pipeline handlers.
            if (String(f.Properties.Handler ?? "").endsWith(".lambda_handler"))
                byHandler[f.Properties.Handler] = f.Properties.Environment.Variables;
        }
        expect(Object.keys(byHandler).sort()).toEqual(
            [
                "vamsExecuteSystemGenAiMetadataPipeline.lambda_handler",
                "openPipeline.lambda_handler",
                "constructPipeline.lambda_handler",
                "generateMetadata.lambda_handler",
                "generateEmbedding.lambda_handler",
                "pipelineEnd.lambda_handler",
            ].sort()
        );
        expect(byHandler["constructPipeline.lambda_handler"]).toMatchObject({
            VECTOR_SEARCH_ENABLED: "true",
            USE_FARGATE_RENDERER: "false",
            MAX_INPUT_FILE_SIZE_MB: "2048",
            MAX_POINT_CLOUD_POINTS: "20000000",
        });
        expect(byHandler["generateMetadata.lambda_handler"]).toMatchObject({
            BEDROCK_ANALYSIS_MODEL_ID: "global.anthropic.claude-haiku-4-5-20251001-v1:0",
            BEDROCK_GUARDRAIL_IDENTIFIER: "",
            BEDROCK_GUARDRAIL_VERSION: "",
        });
        expect(byHandler["generateEmbedding.lambda_handler"]).toMatchObject({
            EMBEDDING_MODEL_ID: createMockConfig().app.vectorSearch.embeddingModelId,
            EMBEDDING_DIMENSIONS: String(createMockConfig().app.vectorSearch.embeddingDimensions),
        });
        expect(byHandler["generateEmbedding.lambda_handler"].ORCHESTRATION_BUS_NAME).toBeDefined();
        expect(byHandler["openPipeline.lambda_handler"].STATE_MACHINE_ARN).toBeDefined();
        expect(byHandler["openPipeline.lambda_handler"].STATE_MACHINE_LOG_GROUP_ARN).toBeDefined();
        expect(
            byHandler["vamsExecuteSystemGenAiMetadataPipeline.lambda_handler"]
                .OPEN_PIPELINE_FUNCTION_NAME
        ).toBeDefined();
    });

    test("Bedrock grants: analysis model with inference profiles, embedding model without", () => {
        const metadata = withActions(
            statementsOf(lambdaOnly, "GenerateMetadata"),
            "bedrock:InvokeModel"
        );
        expect(metadata).toHaveLength(1);
        const metadataResources = JSON.stringify(metadata[0].Resource);
        expect(metadataResources).toContain(":inference-profile/*");
        expect(metadataResources).toContain(
            "foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"
        );
        expect(metadataResources).not.toContain("global.anthropic");

        const embedding = withActions(
            statementsOf(lambdaOnly, "GenerateEmbedding"),
            "bedrock:InvokeModel"
        );
        expect(embedding).toHaveLength(1);
        expect(JSON.stringify(embedding[0].Resource)).not.toContain("inference-profile");
        expect(actionsOf(statementsOf(lambdaOnly, "GenerateEmbedding"))).toContain(
            "events:PutEvents"
        );
    });

    test("only the callback Lambdas hold states:SendTask* grants", () => {
        for (const fragment of ["GenerateMetadata", "GenerateEmbedding", "ConstructPipeline"]) {
            const actions = actionsOf(statementsOf(lambdaOnly, fragment));
            expect(actions.filter((a) => a.startsWith("states:"))).toEqual([]);
        }
        for (const fragment of [
            "VamsExecuteSystemGenAiMetadataPipeline",
            "SystemGenAiMetadataPipelineEnd",
            "SystemGenAiMetadataOpenPipeline",
        ]) {
            expect(actionsOf(statementsOf(lambdaOnly, fragment))).toContain(
                "states:SendTaskFailure"
            );
        }
        expect(actionsOf(statementsOf(lambdaOnly, "SystemGenAiMetadataOpenPipeline"))).toContain(
            "states:StartExecution"
        );
    });

    test("bedrock:ApplyGuardrail is granted on the exact guardrail only when configured", () => {
        const all = (t: Template) =>
            Object.values(t.findResources("AWS::IAM::Policy")).flatMap(
                (p: any) => p.Properties.PolicyDocument.Statement
            );
        expect(withActions(all(lambdaOnly), "bedrock:ApplyGuardrail")).toEqual([]);
        const grants = withActions(
            statementsOf(guarded, "GenerateMetadata"),
            "bedrock:ApplyGuardrail"
        );
        expect(grants).toHaveLength(1);
        expect(JSON.stringify(grants[0].Resource)).toMatch(
            /:bedrock:us-east-1:123456789012:guardrail\/gr-test/
        );
        expect(withActions(all(guarded), "bedrock:ApplyGuardrail")).toHaveLength(1);
        const env = (
            Object.values(guarded.findResources("AWS::Lambda::Function")).find(
                (f: any) => f.Properties.Handler === "generateMetadata.lambda_handler"
            ) as any
        ).Properties.Environment.Variables;
        expect(env).toMatchObject({
            BEDROCK_GUARDRAIL_IDENTIFIER: "gr-test",
            BEDROCK_GUARDRAIL_VERSION: "1",
        });
    });

    test("registers under the system ids", () => {
        const registrations = Object.values(
            lambdaOnly.findResources("AWS::CloudFormation::CustomResource")
        )
            .concat(
                Object.values(lambdaOnly.findResources("Custom::VamsSchemaRegistration")),
                Object.values(lambdaOnly.findResources("Custom::AWS"))
            )
            .map((r: any) => JSON.stringify(r.Properties));
        const withIds = registrations.filter((r) => r.includes("idOverrides"));
        expect(withIds).toHaveLength(1);
        const overrides = JSON.parse(JSON.parse(withIds[0]).idOverrides);
        expect(overrides).toEqual({
            pipelineId: SYSTEM_GENAI_METADATA_PIPELINE_ID,
            pipelineDatabaseId: SYSTEM_WORKFLOW_DATABASE_ID,
            workflowId: SYSTEM_GENAI_METADATA_WORKFLOW_ID,
            workflowDatabaseId: SYSTEM_WORKFLOW_DATABASE_ID,
        });
    });
});
