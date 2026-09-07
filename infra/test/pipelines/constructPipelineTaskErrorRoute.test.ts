/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every pipeline's first state has an error route to the state that releases the callback token.
 *
 * A pipeline sub-state-machine starts at `ConstructPipelineTask`. `PipelineEndTask` is the only state that
 * reports on the parent workflow's Step Functions callback token, so a failure in the first state with no
 * `Catch` ends the sub-execution having reported nothing — and the parent task then pends for its full
 * `taskTimeout`. The consequence scales with that timeout: one hour for the 3D thumbnail pipeline, five for
 * three others, fourteen for coordinate transform, and **seventy-three** for Splat Toolbox.
 *
 * The pipeline's own handler reports the token for errors it raises. What has no route is the class where
 * the handler never runs at all — the function timeout, an out-of-memory kill, an `ImportModuleError`, or an
 * invoke fault that exhausts the task's service-exception retries. Nothing in the handler can cover those,
 * because the handler is what did not happen.
 *
 * MEASURED, and the measurement is the reason this asserts over the SYNTHESIZED ASL rather than over the
 * TypeScript. Two `addCatch` idioms are in use — chained onto the `LambdaInvoke` expression
 * (`}).addCatch(...)`, used by GR00T and GenAI metadata labeling) and attached later by name
 * (`constructPipelineTask.addCatch(...)`). A source grep for the second form reported six pipelines as
 * missing the route when they had it, and the same grep would have reported Splat Toolbox as covered had
 * its Batch-task catch been written on the same line. The emitted state machine has one shape for both, so
 * only the artifact can answer the question.
 */

import { synthTemplate, SynthResult, TemplateName } from "../support/templateSynth";

/** The state that reports on the parent workflow's callback token. */
const RELEASER = "PipelineEndTask";

type Asl = { StartAt?: string; States?: Record<string, any> };

/**
 * Every pipeline sub-state-machine in the assembly, as parsed ASL keyed by its logical id.
 *
 * A state machine's `DefinitionString` is an `Fn::Join` over resolved tokens, so it is flattened before
 * parsing. A definition that will not parse is reported rather than skipped — silently dropping it is how
 * this whole file would become vacuous.
 */
function pipelineStateMachines(s: SynthResult): Array<{ id: string; stack: string; asl: Asl }> {
    const out: Array<{ id: string; stack: string; asl: Asl }> = [];
    const unparseable: string[] = [];
    for (const sm of s.ofType("AWS::StepFunctions::StateMachine")) {
        const raw = SynthResult.flatten(sm.properties.DefinitionString);
        let asl: Asl;
        try {
            asl = JSON.parse(raw);
        } catch {
            unparseable.push(sm.logicalId);
            continue;
        }
        if (asl.States && "ConstructPipelineTask" in asl.States) {
            out.push({ id: sm.logicalId, stack: sm.stack, asl });
        }
    }
    expect(unparseable).toEqual([]);
    return out;
}

describe.each(["commercial", "govcloud", "eusovereign"] as TemplateName[])(
    "%s: ConstructPipelineTask error route",
    (templateName) => {
        let synth: SynthResult;

        beforeAll(() => {
            // Every pipeline on, so the assertion covers the whole set rather than the shipped subset.
            synth = synthTemplate(templateName, {
                mutateKey: "all-pipelines-on",
                mutate: (c: any) => {
                    c.app.useGlobalVpc = { ...(c.app.useGlobalVpc ?? {}), enabled: true };
                    const pipelines = c.app?.pipelines ?? {};
                    for (const key of Object.keys(pipelines)) {
                        const entry = pipelines[key];
                        if (entry && typeof entry === "object" && "enabled" in entry) {
                            entry.enabled = true;
                            // Splat Toolbox's Dockerfile arrives from an upstream sync and is
                            // gitignored, so the non-CodeBuild branch resolves a path a fresh checkout
                            // does not have. The harness refuses that configuration up front
                            // (assertNoUntrackedDockerAsset), which is why every pipeline carrying the
                            // flag gets it set rather than only the ones that need an image built.
                            if ("useCodeBuild" in entry) {
                                entry.useCodeBuild = true;
                            }
                        }
                    }
                    if (pipelines.useNvidiaCosmos)
                        pipelines.useNvidiaCosmos.huggingFaceToken = "synth-only";
                    if (pipelines.useNvidiaCosmos3)
                        pipelines.useNvidiaCosmos3.huggingFaceToken = "synth-only";
                    if (pipelines.useNvidiaGr00t)
                        pipelines.useNvidiaGr00t.huggingFaceToken = "synth-only";
                    if (pipelines.useIsaacLabTraining)
                        pipelines.useIsaacLabTraining.acceptNvidiaEula = true;
                },
            });
        });

        it("[control] the assembly contains pipeline state machines with a ConstructPipelineTask", () => {
            // Without this, every assertion below is satisfied by an assembly that emitted none — and
            // these state machines exist only when their pipelines are enabled, which the shipped
            // templates do not do.
            expect(pipelineStateMachines(synth).length).toBeGreaterThan(0);
        });

        it("ConstructPipelineTask is the state the machine starts at", () => {
            // The premise the rule rests on. If a pipeline ever starts somewhere else, the reasoning
            // about "the first state" no longer applies and this fails rather than passing silently.
            const offenders = pipelineStateMachines(synth)
                .filter((m) => m.asl.StartAt !== "ConstructPipelineTask")
                .map((m) => `${m.id}: StartAt is ${m.asl.StartAt}`);
            expect(offenders).toEqual([]);
        });

        it("every ConstructPipelineTask carries a Catch", () => {
            const offenders: string[] = [];
            for (const { id, stack, asl } of pipelineStateMachines(synth)) {
                const state = asl.States!.ConstructPipelineTask;
                const catches = state.Catch;
                if (!Array.isArray(catches) || catches.length === 0) {
                    offenders.push(
                        `${stack}/${id}: ConstructPipelineTask has no Catch. A failure before the ` +
                            `handler runs ends the sub-execution without reporting the parent's ` +
                            `callback token, and the parent task pends for its full taskTimeout.`
                    );
                }
            }
            expect(offenders).toEqual([]);
        });

        it("the Catch reaches the state that releases the callback token", () => {
            // A Catch pointing at a Fail state satisfies the rule above while leaving the token
            // unreported, so the DESTINATION is asserted — following the chain, since the catch target
            // is a Pass whose Next is the releaser.
            const offenders: string[] = [];
            for (const { id, asl, stack } of pipelineStateMachines(synth)) {
                const catches = asl.States!.ConstructPipelineTask.Catch;
                if (!Array.isArray(catches) || catches.length === 0) continue; // reported above

                let reached = false;
                for (const entry of catches) {
                    let next: string | undefined = entry.Next;
                    const seen = new Set<string>();
                    while (next && !seen.has(next)) {
                        if (next === RELEASER) {
                            reached = true;
                            break;
                        }
                        seen.add(next);
                        next = asl.States![next]?.Next;
                    }
                    if (reached) break;
                }
                if (!reached) {
                    offenders.push(
                        `${stack}/${id}: ConstructPipelineTask's Catch does not reach ${RELEASER}, ` +
                            `so the caught failure still reports nothing on the callback token ` +
                            `(targets: ${catches.map((c: any) => c.Next).join(", ")})`
                    );
                }
            }
            expect(offenders).toEqual([]);
        });

        it("the Catch preserves the state rather than replacing it", () => {
            // `ResultPath: "$.error"` appends the error beside the existing state. Without it the error
            // REPLACES the state, and `pipelineEnd` reads `externalSfnTaskToken` off that state to know
            // which token to report — so a catch that reaches the releaser still releases nothing.
            const offenders: string[] = [];
            for (const { id, asl, stack } of pipelineStateMachines(synth)) {
                const catches = asl.States!.ConstructPipelineTask.Catch;
                if (!Array.isArray(catches) || catches.length === 0) continue;
                for (const entry of catches) {
                    if (entry.ResultPath !== "$.error") {
                        offenders.push(
                            `${stack}/${id}: Catch -> ${entry.Next} has ResultPath ` +
                                `${JSON.stringify(
                                    entry.ResultPath
                                )}, not "$.error", so the error ` +
                                `replaces the state carrying externalSfnTaskToken`
                        );
                    }
                }
            }
            expect(offenders).toEqual([]);
        });
    }
);
