/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `app.pipelines.useConversion3dBasic.enableStepInput` decides whether the 3D Basic Conversion image
 * includes cascadio (Open CASCADE) and reads STEP. Absent or null is false, so a config.json written
 * before the key keeps an image without Open CASCADE; any other value that is not a JSON boolean is a
 * configuration error. Every shipped config file carries it set to false and the ConfigBuilder mirrors
 * the default and the rule. CDK passes it to the image as the ENABLE_STEP_INPUT build argument and to
 * the function as the ENABLE_STEP_INPUT environment variable.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import configJson from "../../config/config.json";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";
import { synthTemplate, SynthResult } from "../support/templateSynth";

const RULE_ID = "conversion-3dbasic-step-input-boolean";

function appWith(block: Record<string, unknown>): any {
    return { app: { pipelines: { useConversion3dBasic: { enabled: true, ...block } } } };
}

describe("resolveConversion3dBasicStepInput", () => {
    test.each([
        ["absent", {}],
        ["null", { enableStepInput: null }],
    ])("is false when the key is %s", (_label, block) => {
        const config = appWith(block);
        Config.resolveConversion3dBasicStepInput(config);
        expect(config.app.pipelines.useConversion3dBasic.enableStepInput).toBe(false);
    });

    test.each([true, false])("keeps an explicit %s", (value) => {
        const config = appWith({ enableStepInput: value });
        Config.resolveConversion3dBasicStepInput(config);
        expect(config.app.pipelines.useConversion3dBasic.enableStepInput).toBe(value);
    });

    test.each(["true", "false", 1, 0, [], {}])("refuses the non-boolean %p", (value) => {
        expect(() =>
            Config.resolveConversion3dBasicStepInput(appWith({ enableStepInput: value }))
        ).toThrow(
            /^Configuration Error: app\.pipelines\.useConversion3dBasic\.enableStepInput must be true or false/
        );
    });
});

describe("the shipped config files and the ConfigBuilder", () => {
    test.each([
        ["config.json", configJson],
        ["config.template.commercial.json", commercialTemplate],
        ["config.template.govcloud.json", govcloudTemplate],
        ["config.template.eusovereign.json", eusovereignTemplate],
    ])("%s keeps STEP input off", (_name, file: any) => {
        expect(file.app.pipelines.useConversion3dBasic.enableStepInput).toBe(false);
    });

    test.each(["commercial", "govcloud", "eusovereign"] as const)(
        "the %s preset keeps STEP input off",
        (profile) => {
            expect(
                (makeDefaultConfig(profile) as any).app.pipelines.useConversion3dBasic
                    .enableStepInput
            ).toBe(false);
        }
    );

    test("the ConfigBuilder refuses a non-boolean and accepts a boolean or nothing", () => {
        const fired = (value: unknown) => {
            const cfg: any = makeDefaultConfig("commercial");
            if (value === undefined) delete cfg.app.pipelines.useConversion3dBasic.enableStepInput;
            else cfg.app.pipelines.useConversion3dBasic.enableStepInput = value;
            return evaluateRules(cfg).some((rule: any) => rule.id === RULE_ID);
        };
        expect(fired("true")).toBe(true);
        expect(fired(1)).toBe(true);
        expect(fired(true)).toBe(false);
        expect(fired(false)).toBe(false);
        expect(fired(undefined)).toBe(false);
    });
});

describe("CDK wiring", () => {
    const source = fs.readFileSync(
        path.join(
            __dirname,
            "../../lib/nestedStacks/pipelines/conversion/3dBasic/lambdaBuilder/conversion3dBasicFunctions.ts"
        ),
        "utf-8"
    );

    test("the image asset gets the build argument and the function the environment variable", () => {
        expect(source).toMatch(
            /buildArgs: \{ ENABLE_STEP_INPUT: enableStepInput \? "true" : "false" \}/
        );
        expect(source).toMatch(
            /environment: \{ ENABLE_STEP_INPUT: enableStepInput \? "true" : "false" \}/
        );
        expect(source).toMatch(
            /const enableStepInput = config\.app\.pipelines\.useConversion3dBasic\.enableStepInput === true;/
        );
    });

    test("the Dockerfile installs the STEP packages only for the true build argument", () => {
        const dockerfile = fs.readFileSync(
            path.join(
                __dirname,
                "../../../backendPipelines/conversion/3dBasic/lambdaContainer/Dockerfile"
            ),
            "utf-8"
        );
        expect(dockerfile).toMatch(/^ARG ENABLE_STEP_INPUT=false$/m);
        expect(dockerfile).toContain('if [ "${ENABLE_STEP_INPUT}" = "true" ]');
    });
});

describe("the synthesized function", () => {
    const conversionFunctions = (s: SynthResult) =>
        s
            .ofType("AWS::Lambda::Function")
            .filter((fn) => /vamsExecute3dBasicConversion/.test(fn.logicalId));

    test.each([
        ["the shipped config", undefined, "false"],
        ["enableStepInput true", true, "true"],
        ["the key absent", null, "false"],
    ])("%s sets ENABLE_STEP_INPUT", (_label, value, expected) => {
        const synth =
            value === undefined
                ? synthTemplate("commercial")
                : synthTemplate("commercial", {
                      mutate: (c: any) => {
                          if (value === null)
                              delete c.app.pipelines.useConversion3dBasic.enableStepInput;
                          else c.app.pipelines.useConversion3dBasic.enableStepInput = value;
                      },
                      mutateKey: `conversionStepInput-${String(value)}`,
                  });
        const functions = conversionFunctions(synth);
        expect(functions).toHaveLength(1);
        expect(functions[0].properties.Environment.Variables.ENABLE_STEP_INPUT).toBe(expected);
    });
});
