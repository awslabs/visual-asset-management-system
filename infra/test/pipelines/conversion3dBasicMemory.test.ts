/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The 3D Basic Conversion function runs at the Lambda maximum, 10,240 MB, its own setting rather than
 * the shared Config.LAMBDA_MEMORY_SIZE, because the container's size check derives every input limit
 * from AWS_LAMBDA_FUNCTION_MEMORY_SIZE. Its ephemeral storage (6 GiB) keeps disk above every memory
 * limit at that size. The Lambda memory range (128 to 10,240 MB) is one function-configuration quota
 * in every partition, so the same value is synthesized for the commercial, GovCloud and EU Sovereign
 * templates. No other function changes memory.
 */

import * as Config from "../../config/config";
import {
    CONVERSION_EPHEMERAL_STORAGE,
    CONVERSION_MEMORY_SIZE_MB,
} from "../../lib/nestedStacks/pipelines/conversion/3dBasic/lambdaBuilder/conversion3dBasicFunctions";
import { synthTemplate, SynthResult } from "../support/templateSynth";

const conversionFunctions = (s: SynthResult) =>
    s
        .ofType("AWS::Lambda::Function")
        .filter((fn) => /vamsExecute3dBasicConversion/.test(fn.logicalId));

describe("the 3D Basic Conversion function's memory", () => {
    test("the constants are the Lambda maximum and 6 GiB, and the shared setting is unchanged", () => {
        expect(CONVERSION_MEMORY_SIZE_MB).toBe(10240);
        expect(CONVERSION_EPHEMERAL_STORAGE.toMebibytes()).toBe(6144);
        expect(Config.LAMBDA_MEMORY_SIZE).toBe(5308);
    });

    test.each(["commercial", "govcloud", "eusovereign"] as const)(
        "the %s template synthesizes 10,240 MB and 6 GiB for this function only",
        (profile) => {
            const synth = synthTemplate(profile);
            const functions = conversionFunctions(synth);
            expect(functions).toHaveLength(1);
            expect(functions[0].properties.MemorySize).toBe(10240);
            expect(functions[0].properties.EphemeralStorage).toEqual({ Size: 6144 });
            expect(functions[0].properties.Timeout).toBe(900);
        }
    );
});
