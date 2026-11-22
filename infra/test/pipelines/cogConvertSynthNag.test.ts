/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * COG Conversion synthesized with CDK Nag, as bin/infra.ts does, in the hardened commercial cell, GovCloud
 * and the EU Sovereign Cloud. CI's synth job runs the default configuration, which leaves the pipeline off,
 * so a CDK Nag error in it would otherwise first show at a deployment.
 *
 * The state machine logs errors only, without execution data, because its input carries the workflow's
 * task token. AwsSolutions-SF1 asks for every event, and the construct suppresses it with that reason.
 */

import { synthTemplate, TemplateName } from "../support/templateSynth";

const cogOn = (extra?: (c: any) => void) => (c: any) => {
    c.app.useGlobalVpc.enabled = true;
    const block = c.app.pipelines.useCogConversion;
    block.enabled = true;
    block.autoRegisterWithVAMS = true;
    extra?.(c);
};

const cells: Array<[string, TemplateName, string, (c: any) => void]> = [
    [
        "hardened commercial: KMS key, global VPC for all Lambdas",
        "commercial",
        "fl05-cog-hardened",
        cogOn((c) => {
            c.app.useKmsCmkEncryption.enabled = true;
            c.app.useGlobalVpc.useForAllLambdas = true;
        }),
    ],
    ["GovCloud", "govcloud", "fl05-cog-gov", cogOn()],
    ["EU Sovereign", "eusovereign", "fl05-cog-eusc", cogOn()],
];

describe("COG Conversion synth with CDK Nag", () => {
    test.each(cells)("%s", (_label, template, key, mutate) => {
        const synth = synthTemplate(template, { mutate, mutateKey: key, nag: true });
        // The GovCloud and EU Sovereign templates carry a finding of their own in the Cognito user pool
        // (AwsSolutions-COG8, in AuthBuilder), unrelated to this pipeline; every cell must have none in
        // the pipeline, and the commercial cell none at all.
        const errors = synth.errors
            .filter((e) => template === "commercial" || /CogConvert/.test(e.path))
            .map((e) => `${e.path}: ${e.message}`.slice(0, 160));
        expect(errors).toEqual([]);
        const machines = synth
            .ofType("AWS::StepFunctions::StateMachine")
            .filter((r) => /CogConvert/.test(r.logicalId));
        expect(machines).toHaveLength(1);
        expect(machines[0].properties.LoggingConfiguration).toMatchObject({
            Level: "ERROR",
            IncludeExecutionData: false,
        });
    });
});
