/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Whole-app synth matrix of the 3D Basic Conversion Batch tier (#1732), with the AwsSolutions rule pack
 * applied: the tier off in the shipped commercial template (nothing of it is emitted), and the tier on
 * in the commercial, hardened (customer managed key, global VPC with `useForAllLambdas`), GovCloud and
 * EU Sovereign templates, with STEP input on and off. Every cell must end with no cdk-nag error.
 */

import { synthTemplate, TemplateName } from "../support/templateSynth";

const tierOn = (step: boolean, extra?: (c: any) => void) => (c: any) => {
    c.app.useGlobalVpc.enabled = true;
    const block = c.app.pipelines.useConversion3dBasic;
    block.enabled = true;
    block.autoRegisterWithVAMS = true;
    block.enableStepInput = step;
    block.batchTier = { enabled: true, maxMemoryGiB: 120 };
    extra?.(c);
};

const cells: Array<[string, TemplateName, string, (c: any) => void, boolean]> = [
    ["commercial default (tier off)", "commercial", "t1732-default", (c: any) => c, false],
    ["commercial, tier on, STEP off", "commercial", "t1732-on", tierOn(false), true],
    ["commercial, tier on, STEP on", "commercial", "t1732-on-step", tierOn(true), true],
    [
        "hardened: KMS key, global VPC for all Lambdas, tier on",
        "commercial",
        "t1732-hardened",
        tierOn(true, (c) => {
            c.app.useKmsCmkEncryption.enabled = true;
            c.app.useGlobalVpc.useForAllLambdas = true;
        }),
        true,
    ],
    ["GovCloud, tier on", "govcloud", "t1732-gov", tierOn(false), true],
    ["EU Sovereign, tier on", "eusovereign", "t1732-eusc", tierOn(false), true],
];

describe("3D Basic Conversion Batch tier synth matrix", () => {
    test.each(cells)("%s", (_label, template, key, mutate, expectTier) => {
        const synth = synthTemplate(template, { mutate, mutateKey: key, nag: true });
        // The GovCloud and EU Sovereign templates carry a finding of their own in the Cognito user pool
        // (AwsSolutions-COG8, in AuthBuilder), unrelated to this tier; every cell must have none in the
        // conversion pipeline, and the commercial cells none at all.
        const errors = synth.errors
            .filter((e) => template === "commercial" || /Conversion3dBasic/.test(e.path))
            .map((e) => `${e.path}: ${e.message}`.slice(0, 160));
        expect(errors).toEqual([]);
        const conversionJobs = synth
            .ofType("AWS::Batch::JobDefinition")
            .filter((r) => /Conversion3dBasic/.test(r.logicalId));
        expect(conversionJobs.length).toBe(expectTier ? 1 : 0);
        const stateMachines = synth
            .ofType("AWS::StepFunctions::StateMachine")
            .filter((r) => /Conversion3dBasic/.test(r.logicalId));
        expect(stateMachines.length).toBe(expectTier ? 1 : 0);
    });

    test("the on cells name the partition in no hardcoded ARN", () => {
        for (const [, template, key, mutate, expectTier] of cells) {
            if (!expectTier) continue;
            const synth = synthTemplate(template, { mutate, mutateKey: key, nag: true });
            const roles = synth
                .ofType("AWS::IAM::Role")
                .filter((r) => /Conversion3dBasic/.test(r.logicalId));
            expect(roles.length).toBeGreaterThan(0);
            expect(JSON.stringify(roles.map((r) => r.properties))).not.toMatch(/arn:aws:/);
        }
    });
});
