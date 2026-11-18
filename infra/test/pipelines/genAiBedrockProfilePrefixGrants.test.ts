/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The GenAI pipelines grant the model behind every geographic cross-Region inference-profile prefix
 * (#1526).
 *
 * The GenAI CAD STEP agent, the Video SOP/BOM extraction pipeline and the Image to 3D Blender agent
 * derive their `bedrock:InvokeModel` grant from the configured model id by removing its cross-Region
 * prefix. Each had its own short prefix list, so a `jp.`, `au.` or `in.` profile id was granted as the
 * foundation model `jp.anthropic...`, which does not exist, and every call was refused. The Blender
 * agent also left out the inference-profile resource, taking the id for a bare model id. All three now
 * remove the prefixes of `BEDROCK_INFERENCE_PROFILE_PREFIXES`.
 *
 * Each grant stays exact (never `foundation-model/*`): the foundation-model ARN names the bare model in
 * any Region, because a cross-Region profile routes to Regions the caller cannot enumerate, and the
 * inference-profile ARN names the configured id in the deployment Region and account.
 */

import { ACCOUNT, SynthResult, synthTemplate } from "../support/templateSynth";

const MODEL = "anthropic.claude-sonnet-4-5-20250929-v1:0";
const PREFIXES = ["jp", "au", "in"] as const;

/** Enable the three pipelines (and nothing else that grants Bedrock) with `modelId`. */
function threePipelines(modelId: string) {
    return (c: any) => {
        c.app.useGlobalVpc.enabled = true;
        c.app.useGlobalVpc.addVpcEndpoints = true;
        for (const name of Object.keys(c.app.pipelines)) {
            const entry = c.app.pipelines[name];
            if (entry && typeof entry === "object" && "enabled" in entry) entry.enabled = false;
        }
        if (c.app.pipelines.useRapidPipeline) {
            c.app.pipelines.useRapidPipeline.useEcs.enabled = false;
            c.app.pipelines.useRapidPipeline.useEks.enabled = false;
        }
        if (c.app.vectorSearch) c.app.vectorSearch.enabled = false;

        const cad = c.app.pipelines.useGenAiCadStepAgent;
        cad.enabled = true;
        cad.runtime = "agentcore";
        cad.useCodeBuild = true;
        cad.bedrockModelId = modelId;

        const video = c.app.pipelines.useGenAiVideoSopBom;
        video.enabled = true;
        video.bedrockModelId = modelId;

        const imageTo3d = c.app.pipelines.useImageTo3d;
        imageTo3d.enabled = true;
        imageTo3d.huggingFaceToken = "hf_test_token";
        for (const model of Object.keys(imageTo3d.models)) {
            imageTo3d.models[model].enabled = model === "blenderagent";
        }
        imageTo3d.models.blenderagent.bedrockModelId = modelId;
        imageTo3d.models.blenderagent.useStabilityImageEditing = false;
    };
}

/** The Bedrock statements of every IAM policy and role in the synth, with the owning resource. */
function bedrockStatements(synth: SynthResult) {
    const out: { stack: string; logicalId: string; resources: string[] }[] = [];
    for (const r of [...synth.ofType("AWS::IAM::Policy"), ...synth.ofType("AWS::IAM::Role")]) {
        const props = r.properties as any;
        const docs: any[] = [];
        if (props.PolicyDocument) docs.push(props.PolicyDocument);
        for (const p of props.Policies ?? []) docs.push(p.PolicyDocument);
        for (const d of docs) {
            for (const s of d?.Statement ?? []) {
                const actions = Array.isArray(s.Action) ? s.Action : [s.Action];
                if (!actions.includes("bedrock:InvokeModel")) continue;
                const resources = (Array.isArray(s.Resource) ? s.Resource : [s.Resource]).map(
                    (x: any) => SynthResult.flatten(x)
                );
                out.push({ stack: r.stack, logicalId: r.logicalId, resources });
            }
        }
    }
    return out;
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

describe.each(PREFIXES)("a %s. cross-Region inference profile", (prefix) => {
    const modelId = `${prefix}.${MODEL}`;
    let synth: SynthResult;
    let statements: ReturnType<typeof bedrockStatements>;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: threePipelines(modelId),
            mutateKey: `genai-bedrock-profile-prefix-${prefix}`,
        });
        statements = bedrockStatements(synth);
    }, 900_000);

    const owned = (pattern: RegExp) =>
        statements.filter((s) => pattern.test(s.stack) || pattern.test(s.logicalId));

    const foundationModel = new RegExp(`^arn:aws:bedrock:\\*::foundation-model/${escape(MODEL)}$`);
    const inferenceProfile = () =>
        new RegExp(
            `^arn:aws:bedrock:(${escape(synth.region)}|\\$\\{AWS::Region\\}):` +
                `(${ACCOUNT}|\\$\\{AWS::AccountId\\}):inference-profile/${escape(modelId)}$`
        );

    test.each([
        ["GenAI CAD STEP agent", /cadstepagent/i],
        ["Video SOP/BOM extraction", /videosopbom/i],
        ["Image to 3D Blender agent", /imageto3d/i],
    ])("the %s grant names the bare model and the configured profile", (_name, pattern) => {
        const grants = owned(pattern);
        expect(grants.length).toBeGreaterThan(0);
        for (const grant of grants) {
            expect(grant.resources.filter((r) => foundationModel.test(r))).toHaveLength(1);
            expect(grant.resources.filter((r) => inferenceProfile().test(r))).toHaveLength(1);
            // The profile prefix is never granted as part of a foundation-model id.
            expect(
                grant.resources.some((r) =>
                    new RegExp(`foundation-model/${escape(prefix)}\\.`).test(r)
                )
            ).toBe(false);
            expect(grant.resources.some((r) => /foundation-model\/\*/.test(r))).toBe(false);
            expect(grant.resources.some((r) => /inference-profile\/\*/.test(r))).toBe(false);
        }
    });
});
