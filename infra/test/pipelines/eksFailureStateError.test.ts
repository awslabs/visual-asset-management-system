/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The RapidPipeline EKS state machine's Failure state reports the run's error and cause.
 *
 * A failed run reaches Failure through EndChoice, which tests IsPresent("$.error") on PipelineEnd's
 * output. The state that set `error` is the one that knows what went wrong, so Failure reads the
 * error name and description from `$.error` with ErrorPath and CausePath. A Fail state's static
 * `Error` and `Cause` fields are literal text: a JSONPath expression written there is reported as
 * written, not evaluated.
 *
 * Step Functions requires a Fail state's ErrorPath and CausePath to select a field holding a string.
 * So the suite also follows every producer of `$.error`: each state that transitions to PipelineEnd
 * sets `error` at the top level of its output, either building `Error` and `Cause` as strings or
 * passing through the error output of a Catch that wrote it to `$.error`. PipelineEnd keeps its own
 * result out of `$.error`, so the object reaches EndChoice and Failure unchanged.
 *
 * Asserted on the emitted state machine definition. The control is that the EKS state machine, its
 * Failure state, EndChoice and the producers are in the synth.
 */

import { parseAsl } from "../support/asl";
import { SynthResult, synthTemplate } from "../support/templateSynth";

/**
 * Intrinsic functions that always return a string, from the set the Fail state documentation allows
 * in ErrorPath and CausePath. States.ArrayGetItem is left out: it returns whatever the item holds.
 */
const STRING_INTRINSICS = [
    "States.Format(",
    "States.JsonToString(",
    "States.Base64Encode(",
    "States.Base64Decode(",
    "States.Hash(",
    "States.UUID(",
];

/** The producers this state machine has, so the generic checks below cannot pass over an empty set. */
const KNOWN_PRODUCERS = [
    "HandleConstructPipelineTimeout",
    "HandleConstructPipelineInvokeError",
    "HandleRunJobTimeout",
    "HandleRunJobInvokeError",
    "HandleCheckJobTimeout",
    "HandleCheckJobInvokeError",
    "Timeout Job",
    "HandleRunJobError",
    "HandleConstructPipelineError",
    "Job Failed",
    "Unknown Job Status",
];

function enableEksPipeline(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
        "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
}

/** Every EKS state machine definition in the synth: the ones with a RunJob and an InitializeCounter. */
function eksDefinitions(synth: SynthResult): any[] {
    return synth
        .ofType("AWS::StepFunctions::StateMachine")
        .map((r) => parseAsl(r.properties))
        .filter((asl) => asl.States?.RunJob && asl.States?.InitializeCounter);
}

/** A transition into `target`, and the field of the source state that makes it. */
interface Edge {
    from: string;
    via: "Next" | "Choice" | "Default" | "Catch";
    catcher?: any;
}

/** Every transition into `target` from a top-level state. */
function edgesInto(states: Record<string, any>, target: string): Edge[] {
    const edges: Edge[] = [];
    for (const [from, state] of Object.entries(states)) {
        if (state.Next === target) edges.push({ from, via: "Next" });
        if (state.Default === target) edges.push({ from, via: "Default" });
        for (const rule of state.Choices ?? []) {
            if (rule.Next === target) edges.push({ from, via: "Choice" });
        }
        for (const catcher of state.Catch ?? []) {
            if (catcher.Next === target) edges.push({ from, via: "Catch", catcher });
        }
    }
    return edges;
}

/**
 * Why `error[field]` might not hold a string at run time, or null when it always does. A
 * literal must be a string; a `.$` value must be one of the intrinsics that return a string.
 */
function nonStringReason(error: Record<string, unknown>, field: "Error" | "Cause"): string | null {
    const literal = error[field];
    const dynamic = error[`${field}.$`];
    if (literal !== undefined && dynamic !== undefined) return `sets both ${field} and ${field}.$`;
    if (literal !== undefined) {
        return typeof literal === "string" ? null : `${field} is a ${typeof literal}`;
    }
    if (dynamic !== undefined) {
        return typeof dynamic === "string" && STRING_INTRINSICS.some((f) => dynamic.startsWith(f))
            ? null
            : `${field}.$ is ${JSON.stringify(dynamic)}, which may not resolve to a string`;
    }
    return `sets no ${field}`;
}

describe("EKS Failure state reports the run's error and cause", () => {
    let synth: SynthResult;
    let definitions: any[];
    let states: Record<string, any>;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableEksPipeline,
            mutateKey: "eks-failure-state-error",
        });
        definitions = eksDefinitions(synth);
        states = definitions[0]?.States ?? {};
    });

    /** The states that transition to PipelineEnd. */
    const producers = (): string[] => edgesInto(states, "PipelineEnd").map((e) => e.from);

    test("the EKS state machine, its Failure state, EndChoice and the producers are in this synth", () => {
        expect(synth.resources.filter((r) => r.type === "Custom::AWSCDK-EKS-Cluster").length).toBe(
            1
        );
        expect(definitions).toHaveLength(1);
        expect(states.Failure).toBeDefined();
        expect(states.Failure.Type).toBe("Fail");
        expect(states.EndChoice?.Type).toBe("Choice");
        expect(states.PipelineEnd?.Parameters?.Payload?.operation).toBe("PIPELINE_END");
        expect(producers()).toEqual(expect.arrayContaining(KNOWN_PRODUCERS));
    });

    test("Failure reads the error name and cause from $.error", () => {
        expect(states.Failure.ErrorPath).toBe("$.error.Error");
        expect(states.Failure.CausePath).toBe("$.error.Cause");
    });

    test("Failure carries no literal Error or Cause", () => {
        // Step Functions rejects a Fail state with both Error and ErrorPath, and a literal would be
        // reported verbatim.
        expect(states.Failure.Error).toBeUndefined();
        expect(states.Failure.Cause).toBeUndefined();
    });

    test("EndChoice is the only route into Failure, and takes it when $.error is present", () => {
        expect(edgesInto(states, "Failure")).toEqual([{ from: "EndChoice", via: "Choice" }]);
        const rules = states.EndChoice.Choices.filter((rule: any) => rule.Next === "Failure");
        expect(rules).toEqual([{ Variable: "$.error", IsPresent: true, Next: "Failure" }]);
        expect(states.EndChoice.Default).toBe("Success");
        // Failure reads $.error from EndChoice's output, so EndChoice must pass its input through whole.
        expect(states.EndChoice.InputPath).toBeUndefined();
        expect(states.EndChoice.OutputPath).toBeUndefined();
    });

    test("PipelineEnd passes $.error through to EndChoice unchanged", () => {
        const end = states.PipelineEnd;
        expect(end.Next).toBe("EndChoice");
        expect(end.ResultPath).toBe("$.PipelineEndResult");
        expect([undefined, "$"]).toContain(end.OutputPath);
        expect(end.ResultSelector).toBeUndefined();
        expect(end.Parameters.Payload["error.$"]).toBe("$.error");
    });

    test("every state that transitions to PipelineEnd sets error at the top level of its output", () => {
        // PipelineEnd's payload reads $.error, so a producer without it fails PipelineEnd with
        // States.Runtime. A Pass's Parameters land at its ResultPath, so `error` is top-level only
        // when that is "$".
        const problems = edgesInto(states, "PipelineEnd").flatMap(({ from, via }) => {
            const state = states[from];
            const out: string[] = [];
            if (via !== "Next" || state.Type !== "Pass")
                out.push(`${from}: ${via} from ${state.Type}`);
            if (![undefined, "$"].includes(state.ResultPath)) {
                out.push(`${from}: ResultPath ${state.ResultPath}`);
            }
            if (![undefined, "$"].includes(state.OutputPath)) {
                out.push(`${from}: OutputPath ${state.OutputPath}`);
            }
            const params = state.Parameters ?? {};
            if (params.error === undefined && params["error.$"] === undefined) {
                out.push(`${from}: sets no error`);
            }
            return out;
        });
        expect(producers().length).toBeGreaterThan(0);
        expect(problems).toEqual([]);
    });

    test("a producer that builds error sets a string Error and Cause", () => {
        const built = producers().filter((name) => states[name].Parameters?.error !== undefined);
        expect(built.length).toBeGreaterThan(0);
        const problems = built.flatMap((name) => {
            const error = states[name].Parameters.error;
            return (["Error", "Cause"] as const)
                .map((field) => nonStringReason(error, field))
                .filter((reason): reason is string => reason !== null)
                .map((reason) => `${name}: ${reason}`);
        });
        expect(problems).toEqual([]);
    });

    test("a producer that passes error through is reached only from Catch blocks that write $.error", () => {
        // A Catch's error output carries the caught error's Error and Cause. Stored at $.error, it is
        // what the producer's "error.$": "$.error" copies.
        const passed = producers().filter(
            (name) => states[name].Parameters?.["error.$"] !== undefined
        );
        expect(passed.length).toBeGreaterThan(0);
        const problems = passed.flatMap((name) => {
            const out: string[] = [];
            if (states[name].Parameters["error.$"] !== "$.error") {
                out.push(`${name}: error.$ is ${states[name].Parameters["error.$"]}`);
            }
            const edges = edgesInto(states, name);
            if (edges.length === 0) out.push(`${name}: unreachable`);
            for (const edge of edges) {
                if (edge.via !== "Catch") {
                    out.push(`${name}: reached by ${edge.via} from ${edge.from}`);
                } else if (edge.catcher.ResultPath !== "$.error") {
                    out.push(
                        `${name}: Catch on ${edge.from} has ResultPath ${edge.catcher.ResultPath}`
                    );
                }
            }
            return out;
        });
        expect(problems).toEqual([]);
    });
});
