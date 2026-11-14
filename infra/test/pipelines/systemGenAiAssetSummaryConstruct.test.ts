/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The SYSTEM GenAI asset summary workflow, built in the SYSTEM GenAI metadata builder stack: present only with the
 * GenAI analysis layer, registered only with autoRegisterWithVAMS, its state machine (summarise -> end), its
 * least-privilege grants (the one summary model, the one guardrail, the default bucket's run-I/O area) and the
 * openPipeline instance that takes a folder input and registers the machine's states.
 */

import * as lambda from "aws-cdk-lib/aws-lambda";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import { SystemGenAiMetadataNestedStack } from "../../lib/nestedStacks/pipelines/system/genAiMetadata/systemGenAiMetadataBuilder-nestedStack";
import { assetSummaryModelId } from "../../lib/nestedStacks/pipelines/system/genAiMetadata/lambdaBuilder/systemGenAiAssetSummaryFunctions";
import {
    SYSTEM_GENAI_ASSET_SUMMARY_PIPELINE_ID,
    SYSTEM_GENAI_ASSET_SUMMARY_WORKFLOW_ID,
} from "../../common/systemPipelines";
import { makePipelineHarness } from "../support/pipelineConstructHarness";

const ANALYSIS_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0";
const TEXT_MODEL = "us.amazon.nova-pro-v1:0";
const GUARDRAIL_ID = "gr-operator";
const GUARDRAIL_ARN = `arn:aws:bedrock:us-east-1:123456789012:guardrail/${GUARDRAIL_ID}`;

const synth = (id: string, mutate?: (c: Config.Config) => void): Template => {
    const h = makePipelineHarness(id, (c) => {
        const sgm = c.app.pipelines.useSystemGenAiMetadata;
        sgm.enabled = true;
        sgm.autoRegisterWithVAMS = true;
        sgm.useGenAiAnalysis = true;
        sgm.bedrockModels.analysisModelId = ANALYSIS_MODEL;
        sgm.bedrockModels.textModelId = "";
        sgm.bedrockGuardrail = {
            guardrailIdentifier: GUARDRAIL_ID,
            guardrailVersion: "2",
            create: { enabled: false, promptAttackInputStrength: "LOW", piiFilter: "anonymize" },
        };
        mutate?.(c);
    });
    const stack = new SystemGenAiMetadataNestedStack(h.stack, "SystemGenAiMetadataNestedStack", {
        config: h.config,
        vpc: h.vpc,
        pipelineSubnets: h.subnets,
        pipelineSecurityGroups: h.securityGroups,
        storageResources: h.storage,
        lambdaCommonBaseLayer: h.lambdaCommonBaseLayer as lambda.LayerVersion,
        importGlobalPipelineWorkflowV2FunctionName: "vams-test-importFunction",
    });
    return Template.fromStack(stack);
};

/** The summary's own functions by handler (not the registration's custom-resource provider framework). */
const summaryFunctions = (template: Template): Record<string, any> => {
    const out: Record<string, any> = {};
    for (const [logicalId, fn] of Object.entries(template.findResources("AWS::Lambda::Function"))) {
        const handler = (fn as any).Properties.Handler || "";
        if (logicalId.startsWith("SystemGenAiAssetSummary") && !handler.startsWith("framework.")) {
            out[(fn as any).Properties.Handler] = { logicalId, ...(fn as any).Properties };
        }
    }
    return out;
};

const statementsOfFunction = (template: Template, fnLogicalId: string): any[] => {
    const fn = template.findResources("AWS::Lambda::Function")[fnLogicalId] as any;
    const roleId = fn.Properties.Role["Fn::GetAtt"][0];
    return Object.values(template.findResources("AWS::IAM::Policy"))
        .filter((policy: any) => (policy.Properties.Roles || []).some((r: any) => r.Ref === roleId))
        .flatMap((policy: any) => policy.Properties.PolicyDocument.Statement);
};

const actions = (statement: any): string[] =>
    Array.isArray(statement.Action) ? statement.Action : [statement.Action];

const s3Statements = (statements: any[]) =>
    statements.filter((s) => actions(s).some((a) => a.startsWith("s3:")));

const registrationIds = (template: Template): string[] =>
    Object.values(template.toJSON().Resources as Record<string, any>)
        .map((r) => JSON.stringify(r.Properties ?? {}))
        .filter((text) => text.includes("idOverrides"))
        .map((text) => (/"pipelineId":"([^"]+)"/.exec(text.replace(/\\"/g, '"')) || [])[1])
        .filter((id): id is string => !!id);

describe("SYSTEM GenAI asset summary", () => {
    const template = synth("Summary");
    const functions = summaryFunctions(template);

    test("four zip functions: the entry, its openPipeline, the summary step and pipelineEnd", () => {
        expect(Object.keys(functions).sort()).toEqual([
            "openPipeline.lambda_handler",
            "pipelineEnd.lambda_handler",
            "summarizeAsset.lambda_handler",
            "vamsExecuteSystemGenAiAssetSummary.lambda_handler",
        ]);
    });

    test("the state machine summarises, then reports on the callback token", () => {
        const machines = Object.entries(
            template.findResources("AWS::StepFunctions::StateMachine")
        ).filter(([id]) => id.startsWith("SystemGenAiAssetSummary"));
        expect(machines).toHaveLength(1);
        const raw = (machines[0][1] as any).Properties.DefinitionString;
        const parts: unknown[] = raw?.["Fn::Join"]?.[1] ?? [raw];
        const asl = JSON.parse(parts.map((p) => (typeof p === "string" ? p : "TOKEN")).join(""));
        expect(asl.StartAt).toBe("SummarizeAssetTask");
        expect(Object.keys(asl.States).sort()).toEqual([
            "EndStatesChoice",
            "FailState",
            "HandleSummarizeAssetError",
            "PipelineEndTask",
            "SuccessState",
            "SummarizeAssetTask",
        ]);
        expect(asl.States.SummarizeAssetTask.Next).toBe("PipelineEndTask");
        expect(asl.States.SummarizeAssetTask.Catch[0]).toMatchObject({
            Next: "HandleSummarizeAssetError",
            ResultPath: "$.error",
        });
        expect(asl.States.SummarizeAssetTask.OutputPath).toBe("$.Payload");
        expect(asl.TimeoutSeconds).toBe(3600);
    });

    test("openPipeline takes a folder input and registers the machine's Lambda states", () => {
        const env = functions["openPipeline.lambda_handler"].Environment.Variables;
        expect(env.ALLOW_FOLDER_INPUT).toBe("true");
        expect(env.ALLOWED_INPUT_FILEEXTENSIONS).toBe("*");
        const text = JSON.stringify(env.ANALYSIS_FUNCTION_NAMES);
        expect(text).toContain("SummarizeAssetTask");
        expect(text).toContain("PipelineEndTask");
    });

    test("the summary step calls the analysis model when no text model is set, with the guardrail", () => {
        const fn = functions["summarizeAsset.lambda_handler"];
        const env = fn.Environment.Variables;
        expect(env.BEDROCK_TEXT_MODEL_ID).toBe("");
        expect(env.BEDROCK_ANALYSIS_MODEL_ID).toBe(ANALYSIS_MODEL);
        expect(env.BEDROCK_GUARDRAIL_IDENTIFIER).toBe(GUARDRAIL_ID);
        const statements = statementsOfFunction(template, fn.logicalId);
        const invoke = statements.filter((s) => actions(s).includes("bedrock:InvokeModel"));
        expect(invoke).toHaveLength(1);
        expect(JSON.stringify(invoke[0].Resource)).toContain(
            "foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"
        );
        const guard = statements.filter((s) => actions(s).includes("bedrock:ApplyGuardrail"));
        expect(guard.map((s) => s.Resource)).toEqual([GUARDRAIL_ARN]);
    });

    test("a configured text model is the one model the summary step may call", () => {
        const withText = synth("SummaryText", (c) => {
            c.app.pipelines.useSystemGenAiMetadata.bedrockModels.textModelId = TEXT_MODEL;
        });
        const fn = summaryFunctions(withText)["summarizeAsset.lambda_handler"];
        const invoke = statementsOfFunction(withText, fn.logicalId).filter((s) =>
            actions(s).includes("bedrock:InvokeModel")
        );
        expect(invoke).toHaveLength(1);
        const resources = JSON.stringify(invoke[0].Resource);
        expect(resources).toContain("amazon.nova-pro-v1:0");
        expect(resources).not.toContain("claude-haiku");
    });

    test("S3 access is the default bucket's run-I/O area only: read for the entry, read and put for the summary", () => {
        const pattern = (s: any) => JSON.stringify(s.Resource);
        for (const [handler, write] of [
            ["summarizeAsset.lambda_handler", true],
            ["vamsExecuteSystemGenAiAssetSummary.lambda_handler", false],
        ] as const) {
            const statements = s3Statements(
                statementsOfFunction(template, functions[handler].logicalId)
            );
            expect(statements.length).toBeGreaterThan(0);
            for (const s of statements) {
                expect(pattern(s)).toMatch(/AssetBucket/);
                expect(pattern(s)).not.toMatch(/AuxBucket|ArtefactsBucket/);
                for (const resource of Array.isArray(s.Resource) ? s.Resource : [s.Resource]) {
                    const text = JSON.stringify(resource);
                    // The bucket itself (for List) or its objects under pipelines/ only.
                    if (text.includes("/")) expect(text).toContain("/pipelines/*");
                }
            }
            const writes = statements.filter((s) => actions(s).some((a) => a.startsWith("s3:Put")));
            expect(writes.length > 0).toBe(write);
            expect(statements.some((s) => actions(s).some((a) => a.startsWith("s3:Delete")))).toBe(
                false
            );
        }
        for (const handler of ["openPipeline.lambda_handler", "pipelineEnd.lambda_handler"]) {
            expect(
                s3Statements(statementsOfFunction(template, functions[handler].logicalId))
            ).toEqual([]);
        }
    });

    test("only the summary step holds a Bedrock grant", () => {
        for (const [handler, fn] of Object.entries(functions)) {
            const bedrock = statementsOfFunction(template, fn.logicalId).filter((s) =>
                actions(s).some((a) => a.startsWith("bedrock:"))
            );
            expect([handler, bedrock.length > 0]).toEqual([
                handler,
                handler === "summarizeAsset.lambda_handler",
            ]);
        }
    });

    test("it registers under the system ids beside the per-file pipeline", () => {
        expect(registrationIds(template).sort()).toEqual(
            [SYSTEM_GENAI_ASSET_SUMMARY_PIPELINE_ID].sort()
        );
        expect(SYSTEM_GENAI_ASSET_SUMMARY_WORKFLOW_ID).toBe(SYSTEM_GENAI_ASSET_SUMMARY_PIPELINE_ID);
    });

    test("without autoRegisterWithVAMS it is built but not registered", () => {
        const unregistered = synth("SummaryNoRegistration", (c) => {
            c.app.pipelines.useSystemGenAiMetadata.autoRegisterWithVAMS = false;
        });
        expect(Object.keys(summaryFunctions(unregistered))).toHaveLength(4);
        expect(registrationIds(unregistered)).toEqual([]);
    });

    test("without the GenAI analysis layer there is no summary at all", () => {
        const attributesOnly = synth("SummaryGenAiOff", (c) => {
            c.app.pipelines.useSystemGenAiMetadata.useGenAiAnalysis = false;
        });
        expect(summaryFunctions(attributesOnly)).toEqual({});
        expect(registrationIds(attributesOnly)).toEqual([]);
        expect(
            assetSummaryModelId({
                app: { pipelines: { useSystemGenAiMetadata: { useGenAiAnalysis: false } } },
            } as unknown as Config.Config)
        ).toBe("");
    });
});
