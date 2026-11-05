/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The RapidPipeline EKS state machine runs an execution whose input has no `inputVersionId`.
 *
 * ConstructPipeline's payload maps `inputVersionId.$: $.inputVersionId`, and a payload path absent
 * from the state input fails the task with States.Runtime before any Catch applies. openPipeline
 * writes the field on every execution it starts, but during an upgrade deploy the new state machine
 * can start before the new openPipeline code is live, and the older code writes no such field.
 *
 * So the machine starts with two Pass states: the first nests the execution input beside a
 * `{"inputVersionId": ""}` default, the second merges the input over the default with
 * States.JsonMerge (the second argument's fields win) and makes the merge the state. An input that
 * names a version keeps it; one without gets "".
 *
 * Asserted on the emitted definition. `resolve` below evaluates exactly the two state shapes this
 * suite pins (a Parameters block of `$`/literal fields, and one States.JsonMerge over two paths), so
 * the behaviour test fails if either state takes another shape.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

function enableEksPipeline(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
        "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
}

function eksAsl(synth: SynthResult): { StartAt: string; States: Record<string, any> } {
    const definition = Object.values(synth.templates)
        .flatMap((t: any) => Object.values(t.Resources ?? {}))
        .filter((r: any) => r.Type === "AWS::StepFunctions::StateMachine")
        .map((r: any) => SynthResult.flatten(r.Properties?.DefinitionString))
        .find((d) => d.includes('"CheckJob"') && d.includes('"InitializeCounter"'));
    if (!definition) throw new Error("no emitted state machine carries the EKS CheckJob state");
    return JSON.parse(definition);
}

/** A top-level `$.field` path read from `input`; `$` is the whole input. */
function at(input: Record<string, any>, path: string): any {
    if (path === "$") return input;
    const match = /^\$\.([A-Za-z0-9_]+)$/.exec(path);
    if (!match) throw new Error(`unsupported path ${path}`);
    return input[match[1]];
}

/** Applies one Pass state's Parameters and OutputPath, for the shapes this suite pins. */
function resolve(state: any, input: Record<string, any>): Record<string, any> {
    expect(state.Type).toBe("Pass");
    const out: Record<string, any> = {};
    for (const [key, value] of Object.entries(state.Parameters as Record<string, any>)) {
        if (!key.endsWith(".$")) {
            out[key] = value;
            continue;
        }
        const merge = /^States\.JsonMerge\((\$[.\w]*), (\$[.\w]*), false\)$/.exec(value);
        out[key.slice(0, -2)] = merge
            ? { ...at(input, merge[1]), ...at(input, merge[2]) }
            : at(input, value);
    }
    return state.OutputPath ? at(out, state.OutputPath) : out;
}

describe("EKS state machine defaults a missing inputVersionId", () => {
    let states: Record<string, any>;
    let startAt: string;

    beforeAll(() => {
        const synth = synthTemplate("commercial", {
            mutate: enableEksPipeline,
            mutateKey: "eks-input-version-default",
        });
        const asl = eksAsl(synth);
        states = asl.States;
        startAt = asl.StartAt;
    });

    const run = (input: Record<string, any>): Record<string, any> => {
        const first = states[startAt];
        const second = states[first.Next];
        expect(second.Next).toBe("ConstructPipeline");
        return resolve(second, resolve(first, input));
    };

    const OLD_INPUT = {
        jobName: "j",
        inputS3AssetFilePath: "s3://b/xid/a.glb",
        assetId: "xid",
        externalSfnTaskToken: "tok",
    };

    test("the two default states run before ConstructPipeline", () => {
        expect(startAt).toBe("StageInputVersionDefault");
        expect(states.StageInputVersionDefault.Next).toBe("DefaultInputVersionId");
        expect(states.DefaultInputVersionId.Next).toBe("ConstructPipeline");
        expect(states.StageInputVersionDefault.Parameters).toEqual({
            defaults: { inputVersionId: "" },
            "input.$": "$",
        });
        // The input is the SECOND argument, so its fields win over the default.
        expect(states.DefaultInputVersionId.Parameters).toEqual({
            "merged.$": "States.JsonMerge($.defaults, $.input, false)",
        });
        expect(states.DefaultInputVersionId.OutputPath).toBe("$.merged");
    });

    test("ConstructPipeline still reads the version from the state input", () => {
        expect(states.ConstructPipeline.Parameters.Payload["inputVersionId.$"]).toBe(
            "$.inputVersionId"
        );
    });

    test("an input without the field reaches ConstructPipeline with an empty version", () => {
        expect(run(OLD_INPUT)).toEqual({ ...OLD_INPUT, inputVersionId: "" });
    });

    test("an input with a version keeps it, and every other field passes unchanged", () => {
        const input = { ...OLD_INPUT, inputVersionId: "v-launch" };
        expect(run(input)).toEqual(input);
    });
});
