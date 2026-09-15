/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The SYSTEM GenAI metadata pipeline construct, synthesized on a plain stack with and without the
 * Fargate render branch and with and without a Bedrock guardrail. Pins the state machine's shape
 * (state names, the branch-task contract on every Lambda task, the catch routes, the choices, the
 * video-segment Distributed Map and its child), the four container-image functions' sizes, the
 * log-group name, the per-function grants, the state machine role's self-grant policy, and the
 * openPipeline allow list against the pipeline bundle's filter.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as fs from "fs";
import * as path from "path";
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

/** The pipeline bundle's filter, which the Python suite pins to the classifier's ALLOW_LIST. */
const PIPELINE_BUNDLE = path.resolve(
    __dirname,
    "../../../backendPipelines/system/genAiMetadata/vamsSchema/pipeline.json"
);

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
    "VideoSegmentChoice",
    "VideoSegmentMap",
    "HandleVideoSegmentsError",
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
    // The baseline runs without a guardrail; the created guardrail has its own suite
    // (systemGenAiMetadataGuardrail.test.ts).
    config.app.pipelines.useSystemGenAiMetadata.bedrockGuardrail = {
        guardrailIdentifier: "",
        guardrailVersion: "",
        create: { enabled: false, promptAttackInputStrength: "LOW", piiFilter: "anonymize" },
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
            guardrailIdentifier: "kb4v3hkqvi6f",
            guardrailVersion: "1",
            create: { enabled: false, promptAttackInputStrength: "LOW", piiFilter: "anonymize" },
        };
    });
    const asl = definition(lambdaOnly);
    const aslFargate = definition(fargate);

    test("starts at ConstructPipelineTask and carries the expected states", () => {
        expect(asl.StartAt).toBe("ConstructPipelineTask");
        expect(Object.keys(asl.States).sort()).toEqual([...EXPECTED_STATES_NO_FARGATE].sort());
        expect(Object.keys(asl.States)).toHaveLength(22);
        expect(Object.keys(aslFargate.States).sort()).toEqual(
            [...EXPECTED_STATES_NO_FARGATE, "FargateRenderJob"].sort()
        );
        expect(Object.keys(aslFargate.States)).toHaveLength(23);
        // 5 h in every configuration: 360 windows, eight at a time, at the child's 300-s ceiling fit.
        expect(asl.TimeoutSeconds).toBe(18000);
        expect(aslFargate.TimeoutSeconds).toBe(18000);
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
        expect(asl.States.GenerateEmbeddingTask.Next).toBe("VideoSegmentChoice");
    });

    test("VideoSegmentChoice fans out only when a plan exists and the whole-file embedding succeeded", () => {
        expect(asl.States.VideoSegmentChoice).toEqual({
            Type: "Choice",
            Choices: [
                {
                    And: [
                        { Variable: "$.videoSegmentItemsS3Location", IsPresent: true },
                        { Variable: "$.embeddingStatus", IsPresent: true },
                        { Variable: "$.embeddingStatus", StringEquals: "SUCCEEDED" },
                    ],
                    Next: "VideoSegmentMap",
                },
            ],
            Default: "PipelineEndTask",
        });
        // The skipped-embedding route bypasses the windows: no segment vector for a file that has
        // no whole-file vector.
        expect(asl.States.EmbeddingSkippedPass.Next).toBe("PipelineEndTask");
        expect(aslFargate.States.GenerateEmbeddingTask.Next).toBe("VideoSegmentChoice");
    });

    test("VideoSegmentMap is a Distributed Map of EXPRESS children over the items file, eight at a time", () => {
        for (const map of [asl.States.VideoSegmentMap, aslFargate.States.VideoSegmentMap]) {
            expect(map.Type).toBe("Map");
            expect(map.ItemProcessor.ProcessorConfig).toEqual({
                Mode: "DISTRIBUTED",
                ExecutionType: "EXPRESS",
            });
            expect(map.ItemReader.Resource).toMatch(/:states:::s3:getObject$/);
            expect(map.ItemReader.ReaderConfig).toEqual({ InputType: "JSON" });
            // A static bucket (so the L2 grants s3:GetObject on it) and the key the media branch wrote.
            expect(map.ItemReader.Parameters.Bucket).toBe("TOKEN");
            expect(map.ItemReader.Parameters["Bucket.$"]).toBeUndefined();
            expect(map.ItemReader.Parameters["Key.$"]).toBe("$.videoSegmentItemsKey");
            expect(map.ItemSelector).toEqual({ "segment.$": "$$.Map.Item.Value", "state.$": "$" });
            expect(map.MaxConcurrency).toBe(8);
            // Zero tolerance is the service default; the CDK elides a literal 0, so neither key appears.
            expect(map.ToleratedFailurePercentage).toBeUndefined();
            expect(map.ToleratedFailureCount).toBeUndefined();
            expect(map.Label).toBe("VideoSegments");
            expect(map.ResultWriter.Resource).toMatch(/:states:::s3:putObject$/);
            // A static bucket again (the L2's scoped s3:PutObject grant) and the prefix the media
            // branch wrote.
            expect(map.ResultWriter.Parameters).toEqual({
                Bucket: "TOKEN",
                "Prefix.$": "$.videoSegmentResultsPrefix",
            });
            expect(map.ResultPath).toBe("$.videoSegmentMapResult");
            expect(map.Next).toBe("PipelineEndTask");
            expect(map.Catch).toEqual([
                {
                    ErrorEquals: ["States.ALL"],
                    ResultPath: "$.error",
                    Next: "HandleVideoSegmentsError",
                },
            ]);
        }
        expect(asl.States.HandleVideoSegmentsError).toMatchObject({
            Type: "Pass",
            ResultPath: "$",
            Next: "PipelineEndTask",
        });
    });

    test("the child processor is the single SegmentAnalyzeTask, invoked with the item and returning $.Payload", () => {
        const processor = asl.States.VideoSegmentMap.ItemProcessor;
        expect(processor.StartAt).toBe("SegmentAnalyzeTask");
        expect(Object.keys(processor.States)).toEqual(["SegmentAnalyzeTask"]);
        const child = processor.States.SegmentAnalyzeTask;
        expect(child.Type).toBe("Task");
        expect(child.Resource).toMatch(/states:::lambda:invoke/);
        expect(child.Parameters["Payload.$"]).toBe("$");
        expect(child.OutputPath).toBe("$.Payload");
        expect(child.ResultPath).toBeUndefined();
        expect(child.End).toBe(true);
        // The L2's default Lambda.* service-exception retrier stays in front; the CDK orders
        // States.ALL last.
        expect(child.Retry.length).toBeGreaterThanOrEqual(2);
        expect(child.Retry[child.Retry.length - 1]).toEqual({
            ErrorEquals: ["States.ALL"],
            IntervalSeconds: 10,
            MaxAttempts: 2,
            BackoffRate: 2,
        });
        // Twenty-two top-level states plus the child here; twenty-three plus it with Fargate.
        expect(Object.keys(asl.States).length + Object.keys(processor.States).length).toBe(23);
        expect(Object.keys(aslFargate.States.VideoSegmentMap.ItemProcessor.States)).toEqual([
            "SegmentAnalyzeTask",
        ]);
    });

    test("the state machine role can start child executions of itself through a policy the machine does not depend on", () => {
        const [smId, sm] = Object.entries(
            lambdaOnly.findResources("AWS::StepFunctions::StateMachine")
        )[0] as [string, any];
        const roleId = Object.keys(lambdaOnly.findResources("AWS::IAM::Role")).find((id) =>
            id.startsWith("SystemGenAiMetadataProcessingStateMachineRole")
        );
        expect(roleId).toBeDefined();
        const policies = Object.entries({
            ...lambdaOnly.findResources("AWS::IAM::Policy"),
            ...lambdaOnly.findResources("AWS::IAM::ManagedPolicy"),
        }).filter(([, p]) =>
            JSON.stringify((p as any).Properties.Roles ?? []).includes(roleId as string)
        );
        const holding = policies.filter(([, p]) =>
            ((p as any).Properties.PolicyDocument.Statement as any[]).some(
                (s) =>
                    actionsOf([s]).includes("states:StartExecution") &&
                    JSON.stringify(s.Resource).includes(smId)
            )
        );
        // The L2's own map policy, and only it: the self-grant sits outside the default policy the
        // machine depends on, which is the shape that does not cycle in CloudFormation.
        expect(holding).toHaveLength(1);
        const [policyId, policy] = holding[0];
        expect(policyId).toMatch(/^SystemGenAiMetadataProcessingStateMachineDistributedMapPolicy/);
        expect(policyId).not.toMatch(/DefaultPolicy/);
        expect(sm.DependsOn ?? []).not.toContain(policyId);
        const statements = (policy as any).Properties.PolicyDocument.Statement as any[];
        expect(actionsOf(statements)).toEqual(
            expect.arrayContaining([
                "states:StartExecution",
                "states:DescribeExecution",
                "states:StopExecution",
                "states:RedriveExecution",
            ])
        );
        for (const s of withActions(statements, "states:DescribeExecution")) {
            const arn = JSON.stringify(s.Resource);
            // Scoped to this machine's executions: the machine's name followed by the trailing wildcard.
            expect(arn).toContain(":execution:");
            expect(arn).toContain(smId);
            expect(arn).toMatch(/:\*"\]\]\}$/);
        }
        // The default policy holds the L2's grants for the items file and the result manifest, and the
        // deployment key both objects are encrypted with.
        const defaultStatements = policies
            .filter(([id]) => /DefaultPolicy/.test(id))
            .flatMap(([, p]) => (p as any).Properties.PolicyDocument.Statement as any[]);
        expect(actionsOf(defaultStatements)).toEqual(
            expect.arrayContaining([
                "s3:GetObject",
                "s3:PutObject",
                "s3:ListMultipartUploadParts",
                "s3:AbortMultipartUpload",
                "kms:Decrypt",
                "kms:GenerateDataKey*",
            ])
        );
        // The reader's and writer's grants are scoped to the aux bucket, never the wildcard a
        // bucket-path reader or writer emits.
        const s3Statements = defaultStatements.filter((s) =>
            actionsOf([s]).some((a) => a.startsWith("s3:"))
        );
        expect(s3Statements.length).toBeGreaterThanOrEqual(2);
        for (const s of s3Statements) {
            expect(JSON.stringify(s.Resource)).not.toBe('"*"');
            expect(JSON.stringify(s.Resource)).toContain("AuxBucket");
        }
    });

    test("the state machine role's Resource `*` statements are the log-delivery, X-Ray and (Fargate) DescribeJobs actions its Nag reason names", () => {
        const starActions = (template: Template): string[] => {
            const roleId = Object.keys(template.findResources("AWS::IAM::Role")).find((id) =>
                id.startsWith("SystemGenAiMetadataProcessingStateMachineRole")
            ) as string;
            const defaultPolicy = Object.entries(template.findResources("AWS::IAM::Policy")).find(
                ([id, p]) =>
                    /^SystemGenAiMetadataProcessingStateMachineRoleDefaultPolicy/.test(id) &&
                    JSON.stringify((p as any).Properties.Roles).includes(roleId)
            ) as [string, any];
            const statements = defaultPolicy[1].Properties.PolicyDocument.Statement as any[];
            return actionsOf(statements.filter((s) => s.Resource === "*")).sort();
        };
        const LOGS_AND_XRAY = [
            "logs:CreateLogDelivery",
            "logs:DeleteLogDelivery",
            "logs:DescribeLogGroups",
            "logs:DescribeResourcePolicies",
            "logs:GetLogDelivery",
            "logs:ListLogDeliveries",
            "logs:PutResourcePolicy",
            "logs:UpdateLogDelivery",
            "xray:GetSamplingRules",
            "xray:GetSamplingTargets",
            "xray:PutTelemetryRecords",
            "xray:PutTraceSegments",
        ];
        expect(starActions(lambdaOnly)).toEqual(LOGS_AND_XRAY);
        expect(starActions(fargate)).toEqual([...LOGS_AND_XRAY, "batch:DescribeJobs"].sort());
        // The reason the suppression carries names each of these groups, and no other resource of the
        // pipeline is suppressed under a construct-path regex (which a `Resource::` finding never
        // matches, so such an entry would suppress nothing while reading as if it did).
        const source = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../lib/nestedStacks/pipelines/system/genAiMetadata/constructs/systemGenAiMetadata-construct.ts"
            ),
            "utf-8"
        );
        for (const named of ["CreateLogDelivery", "PutTraceSegments", "batch:DescribeJobs"]) {
            expect(source).toContain(named);
        }
        expect(source).not.toMatch(/ServiceRole\/\.\*/);
        expect(source).not.toContain("Intended Solution.");
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

    test("four container-image functions at their sizes", () => {
        const images = Object.values(lambdaOnly.findResources("AWS::Lambda::Function"))
            .map((f: any) => f.Properties)
            .filter((p: any) => p.PackageType === "Image")
            .map((p: any) => [p.MemorySize, p.Timeout, p.EphemeralStorage?.Size].join("/"))
            .sort();
        expect(images).toEqual(
            ["10240/900/10240", "10240/900/10240", "3008/600/4096", "3008/300/4096"].sort()
        );
    });

    test("the segment function is the media image with its command pointed at the segment handler", () => {
        const [id, fn] = Object.entries(lambdaOnly.findResources("AWS::Lambda::Function")).find(
            ([logicalId]) => logicalId.startsWith("SystemGenAiMetadataSegmentAnalyze")
        ) as [string, any];
        expect(id).toBeDefined();
        const props = fn.Properties;
        expect(props.PackageType).toBe("Image");
        expect(props.Handler).toBeUndefined();
        expect(props.ImageConfig).toEqual({ Command: ["segment_handler.lambda_handler"] });
        expect(props.MemorySize).toBe(3008);
        expect(props.Timeout).toBe(300);
        expect(props.EphemeralStorage).toEqual({ Size: 4096 });
        // The same image as MediaExtract: one asset, two functions.
        const mediaExtract = Object.entries(lambdaOnly.findResources("AWS::Lambda::Function")).find(
            ([logicalId]) => logicalId.startsWith("SystemGenAiMetadataMediaExtract")
        ) as [string, any];
        expect(mediaExtract[1].Properties.ImageConfig).toBeUndefined();
        expect(JSON.stringify(props.Code.ImageUri)).toBe(
            JSON.stringify(mediaExtract[1].Properties.Code.ImageUri)
        );
        // The four registry env vars plus the guardrail pair generateMetadata carries; the global
        // helper adds the resource-name and role variables every VAMS Lambda has.
        expect(props.Environment.Variables).toMatchObject({
            BEDROCK_ANALYSIS_MODEL_ID: "global.anthropic.claude-haiku-4-5-20251001-v1:0",
            EMBEDDING_MODEL_ID: createMockConfig().app.vectorSearch.embeddingModelId,
            EMBEDDING_DIMENSIONS: String(createMockConfig().app.vectorSearch.embeddingDimensions),
            ORCHESTRATION_BUS_NAME: expect.anything(),
            BEDROCK_GUARDRAIL_IDENTIFIER: "",
            BEDROCK_GUARDRAIL_VERSION: "",
        });
        expect(props.Environment.Variables.STATE_MACHINE_ARN).toBeUndefined();
    });

    test("the segment function is granted Bedrock on both models, PutEvents, the buckets and no task token", () => {
        const statements = statementsOf(lambdaOnly, "SegmentAnalyze");
        const bedrock = withActions(statements, "bedrock:InvokeModel");
        expect(bedrock).toHaveLength(2);
        // Neither statement grants a streaming call: no function streams a response.
        expect(actionsOf(bedrock)).toEqual(["bedrock:InvokeModel", "bedrock:InvokeModel"]);
        const analysis = bedrock.find((s) =>
            JSON.stringify(s.Resource).includes("inference-profile")
        );
        const embedding = bedrock.find(
            (s) => !JSON.stringify(s.Resource).includes("inference-profile")
        );
        // The analysis model is a global inference profile: its exact profile ARN plus the underlying
        // model in every Region of the partition (the profile picks the destination per request).
        expect(analysis.Resource).toEqual([
            `arn:aws:bedrock:${REGION}:${ACCOUNT}:inference-profile/global.anthropic.claude-haiku-4-5-20251001-v1:0`,
            "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
        ]);
        // The embedding model is a plain id: the deployment Region's and the Region-less model ARN.
        const embeddingModelId = createMockConfig().app.vectorSearch.embeddingModelId;
        expect(embedding.Resource).toEqual([
            `arn:aws:bedrock:${REGION}::foundation-model/${embeddingModelId}`,
            `arn:aws:bedrock:::foundation-model/${embeddingModelId}`,
        ]);
        const actions = actionsOf(statements);
        expect(actions).toContain("events:PutEvents");
        expect(actions).toContain("s3:GetObject*");
        expect(actions).toContain("s3:PutObject");
        expect(actions.filter((a) => a.startsWith("states:"))).toEqual([]);
    });

    test("the embedding function is granted read-write on the asset buckets, not read only", () => {
        // generateEmbedding appends the content-chunk count row to the file's .metadata.json under the
        // run's output prefix in the ASSET bucket, so a read-only asset-bucket grant fails every run of
        // a text file with chunks at that PutObject. The write grant must name the asset bucket itself;
        // the aux-bucket grant alone does not cover it.
        const statements = statementsOf(lambdaOnly, "GenerateEmbedding");
        const onAssetBucket = statements.filter((s) =>
            JSON.stringify(s.Resource).includes("AssetBucket")
        );
        expect(onAssetBucket.length).toBeGreaterThan(0);
        const assetActions = actionsOf(onAssetBucket);
        expect(assetActions).toContain("s3:GetObject*");
        expect(assetActions).toContain("s3:PutObject");
        expect(assetActions).toContain("s3:DeleteObject*");
    });

    test("with vector search off the segment function is granted the analysis model only", () => {
        const off = synth("SearchOff", (c) => {
            c.app.vectorSearch.enabled = false;
        });
        const bedrock = withActions(statementsOf(off, "SegmentAnalyze"), "bedrock:InvokeModel");
        expect(bedrock).toHaveLength(1);
        expect(actionsOf(bedrock)).toEqual(["bedrock:InvokeModel"]);
        expect(JSON.stringify(bedrock[0].Resource)).toContain(
            ":inference-profile/global.anthropic"
        );
        // Control: the whole-file embedding function keeps its grant regardless of the flag.
        expect(
            withActions(statementsOf(off, "GenerateEmbedding"), "bedrock:InvokeModel")
        ).toHaveLength(1);
    });

    test("no Bedrock grant of the pipeline carries a wildcard resource other than the profile's Region", () => {
        for (const template of [lambdaOnly, fargate, guarded]) {
            const all = Object.values(template.findResources("AWS::IAM::Policy")).flatMap(
                (p: any) => p.Properties.PolicyDocument.Statement
            );
            const bedrock = all.filter((s: any) =>
                actionsOf([s]).some((a: string) => a.startsWith("bedrock:"))
            );
            expect(bedrock.length).toBeGreaterThan(0);
            for (const statement of bedrock) {
                for (const resource of ([] as string[]).concat(statement.Resource)) {
                    // The only `*` a Bedrock grant carries is the Region of a geographic profile's model.
                    expect(
                        resource.replace(
                            ":bedrock:*::foundation-model/",
                            ":bedrock:R::foundation-model/"
                        )
                    ).not.toContain("*");
                    expect(resource).not.toContain("inference-profile/*");
                }
                expect(actionsOf([statement])).not.toContain(
                    "bedrock:InvokeModelWithResponseStream"
                );
            }
        }
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

    test("openPipeline's allow list is the pipeline bundle's filter: 89 entries, the office formats included", () => {
        const allowed = allowedInputFileExtensions.split(",");
        expect(allowed).toHaveLength(89);
        expect(new Set(allowed).size).toBe(89);
        expect(allowed).toEqual(expect.arrayContaining([".docx", ".pptx", ".xlsx"]));
        // The bundle's allow patterns are `*<extension>` for every classifier ALLOW_LIST entry (pinned
        // in backendPipelines/system/genAiMetadata/lambda/tests/test_sysgenai_bundle.py), so the
        // construct literal, the bundle and the classifier cannot drift from one another.
        const bundle = JSON.parse(fs.readFileSync(PIPELINE_BUNDLE, "utf-8"));
        const bundleAllow: string[] = bundle.systemConfig.inputFileFilters.allow;
        expect(bundleAllow.map((pattern) => pattern.replace(/^\*/, ""))).toEqual(allowed);
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

    test("Bedrock grants: the analysis profile exactly with its model in every Region, the embedding model exactly in this one", () => {
        const metadata = withActions(
            statementsOf(lambdaOnly, "GenerateMetadata"),
            "bedrock:InvokeModel"
        );
        expect(metadata).toHaveLength(1);
        expect(actionsOf(metadata)).toEqual(["bedrock:InvokeModel"]);
        expect(metadata[0].Resource).toEqual([
            `arn:aws:bedrock:${REGION}:${ACCOUNT}:inference-profile/global.anthropic.claude-haiku-4-5-20251001-v1:0`,
            "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
        ]);

        const embeddingModelId = createMockConfig().app.vectorSearch.embeddingModelId;
        const embedding = withActions(
            statementsOf(lambdaOnly, "GenerateEmbedding"),
            "bedrock:InvokeModel"
        );
        expect(embedding).toHaveLength(1);
        expect(embedding[0].Resource).toEqual([
            `arn:aws:bedrock:${REGION}::foundation-model/${embeddingModelId}`,
            `arn:aws:bedrock:::foundation-model/${embeddingModelId}`,
        ]);
        expect(actionsOf(statementsOf(lambdaOnly, "GenerateEmbedding"))).toContain(
            "events:PutEvents"
        );
    });

    test("a plain analysis model id is granted in the deployment Region and Region-less, with no profile", () => {
        const plain = synth("PlainModel", (c) => {
            c.app.pipelines.useSystemGenAiMetadata.bedrockAnalysisModelId =
                "anthropic.claude-haiku-4-5-20251001-v1:0";
        });
        for (const fragment of ["GenerateMetadata", "SegmentAnalyze"]) {
            const analysis = withActions(
                statementsOf(plain, fragment),
                "bedrock:InvokeModel"
            ).filter((s) => JSON.stringify(s.Resource).includes("anthropic"));
            expect(analysis).toHaveLength(1);
            expect(analysis[0].Resource).toEqual([
                `arn:aws:bedrock:${REGION}::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0`,
                "arn:aws:bedrock:::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
            ]);
        }
        // No wildcard is left, so no function carries the geographic-routing justification.
        expect(JSON.stringify(plain.toJSON())).not.toContain("cross-Region inference profile");
    });

    test("the geographic model grant of a profile carries its own IAM5 justification on the function's policy", () => {
        for (const fragment of ["GenerateMetadata", "SegmentAnalyze"]) {
            const policies = Object.entries(lambdaOnly.findResources("AWS::IAM::Policy")).filter(
                ([logicalId]) => logicalId.includes(fragment)
            ) as [string, any][];
            expect(policies).toHaveLength(1);
            const rules: any[] = policies[0][1].Metadata?.cdk_nag?.rules_to_suppress ?? [];
            const geographic = rules.filter(
                (r) =>
                    r.id === "AwsSolutions-IAM5" &&
                    String(r.reason).includes("cross-Region inference profile") &&
                    JSON.stringify(r).includes("foundation-model")
            );
            expect(geographic).toHaveLength(1);
            expect(geographic[0].reason).toContain("destination Regions");
            expect(geographic[0].reason).not.toContain("not known at synthesis");
        }
        // The embedding function's model is a plain id, so it carries no such justification.
        const embeddingPolicies = Object.entries(
            lambdaOnly.findResources("AWS::IAM::Policy")
        ).filter(([logicalId]) => logicalId.includes("GenerateEmbedding")) as [string, any][];
        expect(JSON.stringify(embeddingPolicies[0][1].Metadata ?? {})).not.toContain(
            "cross-Region inference profile"
        );
    });

    test("only the callback Lambdas hold states:SendTask* grants", () => {
        for (const fragment of [
            "GenerateMetadata",
            "GenerateEmbedding",
            "ConstructPipeline",
            "SegmentAnalyze",
        ]) {
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

    test("bedrock:ApplyGuardrail is granted on the exact guardrail only when configured, to both analysis functions", () => {
        const all = (t: Template) =>
            Object.values(t.findResources("AWS::IAM::Policy")).flatMap(
                (p: any) => p.Properties.PolicyDocument.Statement
            );
        expect(withActions(all(lambdaOnly), "bedrock:ApplyGuardrail")).toEqual([]);
        for (const fragment of ["GenerateMetadata", "SegmentAnalyze"]) {
            const grants = withActions(statementsOf(guarded, fragment), "bedrock:ApplyGuardrail");
            expect(grants).toHaveLength(1);
            expect(JSON.stringify(grants[0].Resource)).toMatch(
                /:bedrock:us-east-1:123456789012:guardrail\/kb4v3hkqvi6f/
            );
        }
        // The whole-file analysis and the per-segment analysis each carry one, and no one else does.
        expect(withActions(all(guarded), "bedrock:ApplyGuardrail")).toHaveLength(2);
        const env = (
            Object.values(guarded.findResources("AWS::Lambda::Function")).find(
                (f: any) => f.Properties.Handler === "generateMetadata.lambda_handler"
            ) as any
        ).Properties.Environment.Variables;
        expect(env).toMatchObject({
            BEDROCK_GUARDRAIL_IDENTIFIER: "kb4v3hkqvi6f",
            BEDROCK_GUARDRAIL_VERSION: "1",
        });
        const segmentEnv = (
            Object.entries(guarded.findResources("AWS::Lambda::Function")).find(([id]) =>
                id.startsWith("SystemGenAiMetadataSegmentAnalyze")
            ) as [string, any]
        )[1].Properties.Environment.Variables;
        expect(segmentEnv).toMatchObject({
            BEDROCK_GUARDRAIL_IDENTIFIER: "kb4v3hkqvi6f",
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
