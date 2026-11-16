/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * cdk-nag passes with every NVIDIA pipeline enabled and its container image built inline (#1023).
 *
 * With `useCodeBuild: false` (the shipped default for Cosmos, Cosmos 3 and GR00T) the Cosmos 3,
 * Cosmos Predict, Cosmos Reason, Cosmos Transfer and GR00T fine-tuning constructs each bind their
 * `DockerImageAsset` through a throwaway ECS task definition, only to read back the image name.
 * Binding the image gives that task definition's execution role `ecr:GetAuthorizationToken` on `*`,
 * the only resource form Amazon ECR accepts for the action. The shared GPU construct names that
 * wildcard with `suppressCdkNagEcrAuthTokenWildcard`; these five built their own task definition and
 * did not, so `cdk synth` of this configuration stopped on AwsSolutions-IAM5.
 *
 * Synthesizes the whole app, not one construct: the stack-wide suppressions the core stack applies
 * (Lambda runtimes, the custom-resource provider framework) are part of what a clean synth means, and
 * a construct harness reports those as errors that a real synth never raises.
 *
 * The AwsSolutions pack is attached the way `bin/infra.ts` attaches it when `enableCdkNag` is set, by
 * wrapping `App.synth` for this one synth. The assembly's messages are read inside the wrapper, before
 * the synth harness removes the assembly directory.
 */

import * as cdk from "aws-cdk-lib";
import * as cxapi from "aws-cdk-lib/cx-api";
import { AwsSolutionsChecks } from "cdk-nag";
import { SynthResult, synthTemplate } from "../support/templateSynth";

/** A construct path inside one of the five temporary task definitions. */
const TEMP_TASK_DEF_PATH = /\/(?:TempTaskDef|TempTaskDefV2|ReasonTempTaskDef)\//;

/** Every NVIDIA pipeline and model on, image built inline rather than by CodeBuild. */
function allNvidiaInlineImage(c: any): void {
    c.app.useGlobalVpc.enabled = true;
    const pipelines = c.app.pipelines;
    const models: Record<string, string[]> = {
        useNvidiaCosmos: ["modelsPredict", "modelsTransfer", "modelsReason"],
        useNvidiaCosmos3: ["modelsOmni"],
        useNvidiaGr00t: ["modelsFinetune"],
    };
    for (const [key, groups] of Object.entries(models)) {
        const pipeline = pipelines[key];
        pipeline.enabled = true;
        pipeline.useCodeBuild = false;
        pipeline.huggingFaceToken = "synth-only";
        for (const group of groups) {
            for (const model of Object.values<any>(pipeline[group])) {
                model.enabled = true;
            }
        }
    }
}

interface NagMessage {
    path: string;
    message: string;
}

/** The part of an emitted IAM policy statement this suite reads. */
interface PolicyStatement {
    Action: string | string[];
    Resource: unknown;
}

describe("all NVIDIA pipelines with an inline container image pass cdk-nag", () => {
    let synth: SynthResult;
    let errors: NagMessage[] = [];
    let infos: NagMessage[] = [];

    beforeAll(() => {
        const originalSynth = cdk.App.prototype.synth;
        const spy = jest
            .spyOn(cdk.App.prototype, "synth")
            .mockImplementation(function (this: cdk.App, options?: cdk.StageSynthesisOptions) {
                cdk.Aspects.of(this).add(
                    new AwsSolutionsChecks({ logIgnores: true, reports: false })
                );
                const assembly = originalSynth.call(this, options);
                // A nested stack is not an artifact of its own; its constructs' annotations are
                // carried by the enclosing top-level stack.
                const messages = assembly.stacks.flatMap((artifact) => artifact.messages);
                const of = (level: cxapi.SynthesisMessageLevel): NagMessage[] =>
                    messages
                        .filter((m) => m.level === level)
                        .map((m) => ({ path: m.id, message: String(m.entry.data) }));
                errors = of(cxapi.SynthesisMessageLevel.ERROR);
                infos = of(cxapi.SynthesisMessageLevel.INFO);
                return assembly;
            });
        try {
            // Never shared: a cached synth carries no cdk-nag messages, since this file is the only
            // one that attaches the pack.
            synth = synthTemplate("commercial", {
                mutate: allNvidiaInlineImage,
                mutateKey: "all-nvidia-inline-image-cdk-nag",
                sharedCache: false,
            });
        } finally {
            spy.mockRestore();
        }
    }, 900_000);

    test("[control] each of the five temporary task definitions grants the ECR auth token on *", () => {
        // Without the grants there is no finding to suppress, and the assertions below hold vacuously.
        const grants = synth
            .ofType("AWS::IAM::Policy")
            .filter(
                (policy) =>
                    /TempTaskDef/.test(policy.logicalId) &&
                    (policy.properties.PolicyDocument.Statement as PolicyStatement[]).some(
                        (s) =>
                            [s.Action].flat().includes("ecr:GetAuthorizationToken") &&
                            [s.Resource].flat().includes("*")
                    )
            );
        expect(grants.map((g) => g.logicalId)).toHaveLength(5);
    });

    test("[control] cdk-nag ran and reports the auth token wildcard suppressed on each of them", () => {
        const suppressed = infos.filter(
            (m) =>
                TEMP_TASK_DEF_PATH.test(m.path) &&
                m.message.startsWith(
                    "CdkNagSuppression[Resource::*]: AwsSolutions-IAM5 was triggered but suppressed"
                )
        );
        const taskDefinitions = new Set(
            suppressed.map((m) => m.path.slice(0, m.path.search(TEMP_TASK_DEF_PATH)))
        );
        expect(taskDefinitions.size).toBe(5);
    });

    test("cdk-nag reports no error anywhere in the app", () => {
        expect(errors.map((m) => `${m.path}: ${m.message.split("\n")[0]}`)).toEqual([]);
    });
});
