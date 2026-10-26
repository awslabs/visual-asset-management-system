/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A ConstructPipeline error response reaches PipelineEnd in the RapidPipeline EKS state machine.
 *
 * CONSTRUCT_PIPELINE reports a rejected input by returning a body that carries no `jobManifest`: an
 * `error` object from `handle_construct_pipeline`, or a `statusCode`/`body` pair from
 * `lambda_handler`. The invocation succeeds, so no Catch routes it. RunJob's payload reads
 * `$.ConstructPipelineResult.Payload.jobManifest`, and a payload path absent from the state input
 * fails the task with States.Runtime before it is invoked, which RunJob's Catch does not route either.
 * The run then ends without PipelineEnd, and the parent workflow's task token is held until its
 * taskTimeout.
 *
 * So ConstructPipeline hands off to a Choice that tests the manifest's PRESENCE. IsPresent is the one
 * comparison defined on an absent path; a value comparison against one raises States.Runtime itself.
 * The failure branch is a Pass state that hands PipelineEnd every field its payload reads, with the
 * no-job placeholder as the job name, because no Kubernetes job exists before RunJob.
 *
 * Asserted on the emitted state machine definition, because what matters is the JSONPath Step
 * Functions resolves, not the TypeScript that produced it. The control is that the EKS pipeline and
 * the three states this suite follows are in the synth.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

/** The literal that means "no Kubernetes job exists to clean up". */
const NO_JOB_PLACEHOLDER = "failure-before-creation";

/** The field of CONSTRUCT_PIPELINE's result that RunJob submits, and only a successful result has. */
const MANIFEST_PATH = "$.ConstructPipelineResult.Payload.jobManifest";

function enableEksPipeline(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
        "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
}

/** The EKS pipeline's state machine definition, parsed out of the emitted `DefinitionString`. */
function eksAsl(synth: SynthResult): { StartAt: string; States: Record<string, any> } {
    const definition = Object.values(synth.templates)
        .flatMap((t: any) => Object.values(t.Resources ?? {}))
        .filter((r: any) => r.Type === "AWS::StepFunctions::StateMachine")
        .map((r: any) => SynthResult.flatten(r.Properties?.DefinitionString))
        .find((d) => d.includes('"CheckJob"') && d.includes('"InitializeCounter"'));
    if (!definition) throw new Error("no emitted state machine carries the EKS CheckJob state");
    return JSON.parse(definition);
}

/**
 * Every state-input JSONPath a Parameters block reads, including those inside intrinsic functions.
 * Context-object paths (`$$.…`) are excluded: they are always available.
 */
function readPaths(parameters: unknown): string[] {
    const out: string[] = [];
    const walk = (node: unknown) => {
        if (!node || typeof node !== "object") return;
        for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
            if (key.endsWith(".$") && typeof value === "string") {
                out.push(...(value.match(/(?<!\$)\$(?!\$)(?:\.[A-Za-z0-9_]+)*/g) ?? []));
            } else {
                walk(value);
            }
        }
    };
    walk(parameters);
    return out;
}

const isAtOrUnder = (candidate: string, root: string): boolean =>
    candidate === root || candidate.startsWith(`${root}.`);

/**
 * Whether a Choice rule matches exactly when `path` is absent. ASL spells that two ways: a `Not`
 * around `IsPresent: true`, or `IsPresent: false` directly.
 */
const testsAbsenceOf = (rule: any, path: string): boolean =>
    (rule.Variable === path && rule.IsPresent === false) ||
    (rule.Not?.Variable === path && rule.Not?.IsPresent === true);

describe("EKS ConstructPipeline error response reaches PipelineEnd", () => {
    let synth: SynthResult;
    let asl: { StartAt: string; States: Record<string, any> };
    let states: Record<string, any>;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableEksPipeline,
            mutateKey: "eks-construct-pipeline-outcome",
        });
        asl = eksAsl(synth);
        states = asl.States;
    });

    /** The state ConstructPipeline transitions to on a successful invocation. */
    const outcomeChoice = (): any => states[states.ConstructPipeline.Next];

    /** The Choice rule that matches a result with no manifest. */
    const absentManifestRule = (): any => {
        const rules = (outcomeChoice().Choices ?? []).filter((rule: any) =>
            testsAbsenceOf(rule, MANIFEST_PATH)
        );
        expect(rules).toHaveLength(1);
        return rules[0];
    };

    /** The Pass state a result with no manifest is routed to. */
    const errorHandler = (): any => states[absentManifestRule().Next];

    test("the pipeline IS in this synth and starts at ConstructPipeline", () => {
        expect(synth.resources.filter((r) => r.type === "Custom::AWSCDK-EKS-Cluster").length).toBe(
            1
        );
        expect(asl.StartAt).toBe("ConstructPipeline");
        for (const name of ["ConstructPipeline", "RunJob", "PipelineEnd"]) {
            expect(states[name]).toBeDefined();
        }
        expect(states.PipelineEnd.Parameters.Payload.operation).toBe("PIPELINE_END");
        expect(states.PipelineEnd.Parameters.Payload["error.$"]).toBe("$.error");
    });

    test("ConstructPipeline hands off to a Choice, not straight to RunJob", () => {
        expect(states.ConstructPipeline.Next).not.toBe("RunJob");
        expect(outcomeChoice()).toBeDefined();
        expect(outcomeChoice().Type).toBe("Choice");
    });

    test("the Choice routes a result without jobManifest to a Pass state", () => {
        // IsPresent, not a value comparison: a comparison on an absent path raises States.Runtime.
        expect(errorHandler()).toBeDefined();
        expect(errorHandler().Type).toBe("Pass");
        expect(JSON.stringify(outcomeChoice().Choices)).not.toMatch(
            /"(String|Numeric|Boolean|Timestamp)\w*"/
        );
    });

    test("the Choice's Default is RunJob, which still checks its own outcome", () => {
        expect(outcomeChoice().Default).toBe("RunJob");
        expect(states[states.RunJob.Next].Type).toBe("Choice");
    });

    test("the Pass says no job exists and goes to PipelineEnd", () => {
        const handler = errorHandler();
        expect(handler.Parameters.k8sJobName).toBe(NO_JOB_PLACEHOLDER);
        expect(handler.Parameters["k8sJobName.$"]).toBeUndefined();
        expect(handler.Parameters.status).toBe("FAILED");
        expect(handler.Next).toBe("PipelineEnd");
    });

    test("the Pass reports the ConstructPipeline response as the error", () => {
        expect(errorHandler().Parameters.error).toEqual({
            Error: "ConstructPipelineFailed",
            "Cause.$": "States.JsonToString($.ConstructPipelineResult.Payload)",
        });
        expect(errorHandler().Parameters.errorContext).toEqual({
            "timestamp.$": "$$.State.EnteredTime",
            "stateName.$": "$$.State.Name",
            "executionName.$": "$$.Execution.Name",
        });
    });

    test("the Pass supplies every field PipelineEnd's payload reads", () => {
        // The Pass replaces the state with its Parameters, so a field PipelineEnd reads and the Pass
        // does not set would fail PipelineEnd with States.Runtime.
        const fields = Object.keys(errorHandler().Parameters).map((k) => k.replace(/\.\$$/, ""));
        const reads = readPaths(states.PipelineEnd.Parameters.Payload);
        expect(reads.length).toBeGreaterThan(0);
        for (const read of reads) {
            expect(fields).toContain(read.split(".")[1]);
        }
    });

    test("the Pass reads only paths present after ConstructPipeline returns", () => {
        // Its input is ConstructPipeline's input, which carries every path the task payload reads,
        // with the invocation result at the task's ResultPath.
        const available = [
            ...readPaths(states.ConstructPipeline.Parameters.Payload),
            states.ConstructPipeline.ResultPath,
        ];
        expect(available).toContain("$.ConstructPipelineResult");
        const reads = readPaths(errorHandler().Parameters);
        expect(reads.length).toBeGreaterThan(0);
        expect(reads.filter((read) => !available.some((root) => isAtOrUnder(read, root)))).toEqual(
            []
        );
    });
});
