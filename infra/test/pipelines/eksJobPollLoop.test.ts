/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The RapidPipeline EKS poll loop has to be able to end, and every way it ends has to reach PipelineEnd.
 *
 * Two defects, found by running the pipeline rather than by reading it, because neither is visible in
 * the TypeScript — both are about which JSONPath a state reads.
 *
 *  1. **The loop could not end on its own.** `CheckJob` writes its response under
 *     `resultPath: "$.CheckJobResult"`, so the status it reports lands at
 *     `$.CheckJobResult.Payload.body.status`. `Job Complete?` compared `$.status`, which
 *     `InitializeCounter` sets once and `IncrementCounter` then copies forward unchanged. A job that
 *     began RUNNING read as RUNNING on every later check regardless of what Kubernetes said, so the
 *     loop ran to its ceiling and the run was reported as a timeout however it actually finished.
 *     Measured live: the Kubernetes job succeeded in 35 seconds (`Succeeded pods: 1`, container exit
 *     code 0) and `CheckJob` returned `body.status: "COMPLETED"`, while the state the choice read was
 *     still `"RUNNING"` nineteen polls later.
 *
 *  2. **The success branch could not have completed either.** `PipelineEnd`'s payload passes
 *     `error: $.error` unconditionally, and `Job Completed Successfully` sets no `error` — so the state
 *     would have failed with `States.Runtime` for a missing JSONPath, skipping the only state that
 *     releases the parent workflow's task token. The `FAILED` branch had the mirror-image bug: it read
 *     `$.error`, which no Catch writes on the poll path.
 *
 *     The fix is a separate success-path task rather than one task with an optional field, because the
 *     handler chooses its callback with `has_error = "error" in event` — a PRESENCE test. An `error`
 *     key holding an empty value still reads as a failure, so `SendTaskFailure` would be sent for a job
 *     that succeeded.
 *
 * The `$.error` assertion is written in its general form — every state that READS `$.error` must have
 * every predecessor either set it or catch into it — rather than as a check on the one branch that was
 * broken. That is the shape of the defect, and it is the version that would have caught it.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

function enableEksPipeline(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.useRapidPipeline.enabled = true;
    c.app.pipelines.useRapidPipeline.autoRegisterWithVAMS = false;
    c.app.pipelines.useRapidPipeline.useEks.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
        "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
}

/** Where CheckJob's response actually lands, given its resultPath. */
const CHECK_JOB_STATUS_PATH = "$.CheckJobResult.Payload.body.status";

describe("RapidPipeline EKS job poll loop", () => {
    let states: Record<string, any>;
    let asl: any;

    beforeAll(() => {
        const synth: SynthResult = synthTemplate("commercial", {
            mutate: enableEksPipeline,
            mutateKey: "eks-poll-loop",
        });
        const definition = Object.values(synth.templates)
            .flatMap((t: any) => Object.values(t.Resources ?? {}))
            .filter((r: any) => r.Type === "AWS::StepFunctions::StateMachine")
            .map((r: any) => SynthResult.flatten(r.Properties?.DefinitionString))
            .find((d) => d.includes('"CheckJob"') && d.includes("InitializeCounter"));
        expect(definition).toBeDefined();
        asl = JSON.parse(definition!);
        states = asl.States;
    });

    /** Every state name a given state can transition to. */
    const successorsOf = (name: string): string[] => {
        const s = states[name] ?? {};
        return [
            s.Next,
            s.Default,
            ...(s.Choices ?? []).map((c: any) => c.Next),
            ...(s.Catch ?? []).map((c: any) => c.Next),
        ].filter(Boolean);
    };

    /** Every state that can transition INTO a given state, excluding Catch edges. */
    const flowPredecessorsOf = (name: string): string[] =>
        Object.keys(states).filter((from) => {
            const s = states[from];
            return [s.Next, s.Default, ...(s.Choices ?? []).map((c: any) => c.Next)]
                .filter(Boolean)
                .includes(name);
        });

    /** Every state that reaches a given state through Catch. */
    const catchPredecessorsOf = (name: string): string[] =>
        Object.keys(states).filter((from) =>
            (states[from].Catch ?? []).some((c: any) => c.Next === name)
        );

    const reaches = (from: string, predicate: (name: string) => boolean): boolean => {
        const seen = new Set<string>();
        const walk = (n: string): boolean => {
            if (!n || seen.has(n) || !states[n]) return false;
            seen.add(n);
            if (predicate(n)) return true;
            return successorsOf(n).some(walk);
        };
        return walk(from);
    };

    /**
     * The field map a state actually evaluates.
     *
     * A Pass state's fields sit directly in `Parameters`, but a LambdaInvoke's sit one level down in
     * `Parameters.Payload` — `Parameters` itself holds only `FunctionName` and `Payload`. Reading
     * `Parameters` for both makes every assertion about a task's fields vacuously true, which is how
     * the first version of this suite passed with the success-path defect restored.
     */
    const fieldsOf = (name: string): Record<string, unknown> => {
        const params = states[name]?.Parameters ?? {};
        return (params.Payload as Record<string, unknown>) ?? params;
    };

    /** A PIPELINE_END invocation, which is the only thing that releases the parent's task token. */
    const isPipelineEnd = (name: string): boolean => fieldsOf(name).operation === "PIPELINE_END";

    test("the state machine and its loop states ARE in this synth", () => {
        // The control. Every graph assertion below is trivially satisfied by an empty state map.
        for (const required of [
            "CheckJob",
            "InitializeCounter",
            "IncrementCounter",
            "Job Complete?",
            "Check Max Attempts",
        ]) {
            expect(states[required]).toBeDefined();
        }
        expect(Object.keys(states).filter(isPipelineEnd).length).toBeGreaterThan(0);
    });

    describe("the loop can observe a status change", () => {
        test("the status the choice compares is refreshed from CheckJob's result on every pass", () => {
            // The core property. Asserted as "some state between CheckJob and the choice assigns
            // $.status from CheckJob's result path", rather than by naming that state, because what
            // matters is the dataflow and not which construct performs it.
            const compared = (states["Job Complete?"].Choices ?? []).map((c: any) => c.Variable);
            expect(compared.length).toBeGreaterThan(0);
            const comparedField = compared[0];

            const assignsFreshStatus = (name: string): boolean => {
                const key = `${comparedField.replace(/^\$\./, "")}.$`;
                return fieldsOf(name)[key] === CHECK_JOB_STATUS_PATH;
            };

            let cursor = "CheckJob";
            let lifted = false;
            const seen = new Set<string>();
            while (cursor && states[cursor] && !seen.has(cursor)) {
                seen.add(cursor);
                if (assignsFreshStatus(cursor)) lifted = true;
                if (cursor === "Job Complete?") break;
                cursor = successorsOf(cursor)[0];
            }
            expect(lifted).toBe(true);
        });

        test("the retry path returns to CheckJob, so the refresh happens each iteration", () => {
            // A lift that only ran once would satisfy the assertion above while still deciding the
            // outcome from a single early observation.
            expect(reaches("Check Max Attempts", (n) => n === "CheckJob")).toBe(true);
        });

        test("no state carries the stale status forward as if it were fresh", () => {
            // IncrementCounter propagating "status.$": "$.status" is what made the stale value survive
            // every iteration. It is only safe now because the lift happens after CheckJob and before
            // the choice; if the lift were removed this would be the mechanism again, so the ordering
            // above is the assertion that matters and this one records the dependency.
            expect(fieldsOf("IncrementCounter")["status.$"]).toBeDefined();
            expect(reaches("IncrementCounter", (n) => n === "CheckJob")).toBe(true);
        });
    });

    describe("every ending releases the parent's task token", () => {
        test("each branch of the status choice reaches a PIPELINE_END invocation", () => {
            const branches = [
                ...(states["Job Complete?"].Choices ?? []).map((c: any) => c.Next),
                states["Job Complete?"].Default,
            ].filter(Boolean);
            expect(branches.length).toBeGreaterThanOrEqual(3);
            for (const branch of branches) {
                expect(reaches(branch, isPipelineEnd)).toBe(true);
            }
        });

        test("the poll-exhausted branch reaches one too", () => {
            const branches = [
                ...(states["Check Max Attempts"].Choices ?? []).map((c: any) => c.Next),
                states["Check Max Attempts"].Default,
            ].filter(Boolean);
            for (const branch of branches) {
                expect(reaches(branch, isPipelineEnd)).toBe(true);
            }
        });

        test("the success branch reaches a PIPELINE_END that passes NO error key", () => {
            // The handler tests for the PRESENCE of `error`, so a success payload carrying an empty
            // error still sends SendTaskFailure. This is why there are two PipelineEnd states.
            const successBranch = (states["Job Complete?"].Choices ?? []).find(
                (c: any) => c.StringEquals === "COMPLETED"
            )?.Next;
            expect(successBranch).toBeDefined();

            const ends: string[] = [];
            const walk = (n: string, seen = new Set<string>()) => {
                if (!n || seen.has(n) || !states[n]) return;
                seen.add(n);
                if (isPipelineEnd(n)) ends.push(n);
                successorsOf(n).forEach((s) => walk(s, seen));
            };
            walk(successBranch);
            expect(ends.length).toBeGreaterThan(0);
            for (const end of ends) {
                expect(Object.keys(fieldsOf(end))).not.toContain("error");
                expect(Object.keys(fieldsOf(end))).not.toContain("error.$");
            }
        });
    });

    test("no state reads $.error unless every predecessor supplies it", () => {
        // The general form of the defect. "Job Completed Successfully" flowed into a PipelineEnd that
        // read $.error without setting it, which fails the state with States.Runtime — and because that
        // is raised as the state is entered, no Catch routes it and the token is never released.
        //
        // A predecessor supplies it either by assigning `error` in its own Parameters, or by catching
        // into it (`Catch[].ResultPath === "$.error"`).
        const readsError = (name: string): boolean =>
            Object.values(fieldsOf(name)).some((v) => v === "$.error");
        const suppliesError = (name: string): boolean => {
            const fields = fieldsOf(name);
            if ("error" in fields || "error.$" in fields) return true;
            return (states[name].Catch ?? []).some((c: any) => c.ResultPath === "$.error");
        };

        const consumers = Object.keys(states).filter(readsError);
        expect(consumers.length).toBeGreaterThan(0); // control: PipelineEnd does read it

        const violations: string[] = [];
        for (const consumer of consumers) {
            const preds = [...flowPredecessorsOf(consumer), ...catchPredecessorsOf(consumer)];
            expect(preds.length).toBeGreaterThan(0);
            for (const pred of preds) {
                const viaCatch = (states[pred].Catch ?? []).some(
                    (c: any) => c.Next === consumer && c.ResultPath === "$.error"
                );
                if (!viaCatch && !suppliesError(pred)) {
                    violations.push(`${pred} -> ${consumer}`);
                }
            }
        }
        expect(violations).toEqual([]);
    });
});
