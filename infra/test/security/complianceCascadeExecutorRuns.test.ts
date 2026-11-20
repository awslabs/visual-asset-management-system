/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The compliance cascade executor is not retried by Lambda, and may invoke only itself.
 *
 * A run that reached the Lambda timeout used to be retried twice by Lambda's asynchronous invoke
 * defaults, each retry starting the cascade again from its first node. The executor now hands a
 * long cascade to its next run itself (`complianceCascadeExecutor._hand_over`), so its asynchronous
 * invoke configuration allows no retry, and its role may invoke its own function.
 */

import {
    SynthResult,
    actionsOf,
    lambdaByHandler,
    resourcesOf,
    statementsOf,
    synthTemplate,
} from "../support/templateSynth";

jest.setTimeout(600_000);

const EXECUTOR = "handlers.compliance.complianceCascadeExecutor.lambda_handler";

describe("compliance cascade executor runs", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial");
    });

    test("Lambda does not retry an executor invocation", () => {
        const executor = lambdaByHandler(synth, EXECUTOR);
        const configs = synth.where(
            "AWS::Lambda::EventInvokeConfig",
            (r) =>
                r.stack === executor.stack &&
                SynthResult.flatten(r.properties.FunctionName) === `\${${executor.logicalId}}`
        );
        expect(configs).toHaveLength(1);
        expect(configs[0].properties.MaximumRetryAttempts).toBe(0);
        expect(configs[0].properties.MaximumEventAgeInSeconds).toBe(3600);
    });

    test("the executor's role may invoke its own function to continue a run", () => {
        const executor = lambdaByHandler(synth, EXECUTOR);
        const invokes = statementsOf(synth, executor).filter((statement) =>
            actionsOf(statement).includes("lambda:InvokeFunction")
        );
        const own = `\${${executor.logicalId}.Arn}`;
        expect(invokes.some((statement) => resourcesOf(statement).includes(own))).toBe(true);
    });
});
