/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * After a RunJob error, the RapidPipeline EKS state machine deletes the Kubernetes job a second time,
 * once no RUN_JOB invocation can still be running.
 *
 * RunJob's retrier resends the invocation on Step Functions-side client errors
 * (`Lambda.ClientExecutionTimeoutException`, `Lambda.SdkClientException`, `Lambda.ServiceException`,
 * `Lambda.AWSLambdaException`), which can be raised while the earlier invocation is still running. If a
 * later attempt then ends in an error RunJob's States.ALL catcher routes, PipelineEnd reports the
 * failure and deletes the job before the earlier invocation creates it. Nothing deletes a job created
 * after that, and it runs until `activeDeadlineSeconds`.
 *
 * Every RUN_JOB attempt starts before the catcher fires, and each invocation ends within the function's
 * timeout of its own start. So a Wait of at least that timeout after PipelineEnd outlasts all of them,
 * and a PIPELINE_END after it with no task token deletes a job created late without notifying the
 * parent workflow a second time.
 *
 * The routes are walked through the emitted state machine definition, carrying which top-level fields
 * each state's output holds, so a Choice on IsPresent is decided the way Step Functions decides it. The
 * positive control is that the state machine, its RunJob catchers and the function RunJob invokes are
 * in the synth.
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

/**
 * The EKS state machine's states and the template that emits it. The definition is an `Fn::Join` of
 * literal fragments around CloudFormation tokens, flattened so each token becomes `${LogicalId...}`.
 */
function eksStateMachine(synth: SynthResult): { stack: string; states: Record<string, any> } {
    for (const [stack, template] of Object.entries<any>(synth.templates)) {
        for (const resource of Object.values<any>(template.Resources ?? {})) {
            if (resource.Type !== "AWS::StepFunctions::StateMachine") continue;
            const definition = SynthResult.flatten(resource.Properties?.DefinitionString);
            if (definition.includes('"RunJob"') && definition.includes('"InitializeCounter"')) {
                return { stack, states: JSON.parse(definition).States };
            }
        }
    }
    throw new Error("no emitted state machine carries the EKS RunJob state");
}

/**
 * The function a Lambda Task state invokes: `Parameters.FunctionName` for the lambda:invoke
 * integration, otherwise the function ARN in `Resource`. The reference must be a GetAtt or Ref to a
 * function in the state machine's own template, which is what the flattened token names.
 */
function invokedFunction(
    synth: SynthResult,
    stack: string,
    state: any
): { logicalId: string; resource: any } {
    const reference: string = String(state.Resource).endsWith(":states:::lambda:invoke")
        ? state.Parameters?.FunctionName
        : state.Resource;
    const match = /^\$\{([A-Za-z0-9]+)(?:\.Arn)?\}$/.exec(String(reference));
    if (!match) throw new Error(`cannot resolve the invoked function from ${reference}`);
    const logicalId = match[1];
    const resource = synth.templates[stack].Resources?.[logicalId];
    if (!resource) throw new Error(`${logicalId} is not a resource in ${stack}`);
    return { logicalId, resource };
}

/** The payload a Lambda Task sends, or an empty map for any other state. */
const payloadOf = (state: any): Record<string, unknown> => state?.Parameters?.Payload ?? {};

const isPipelineEndInvoke = (state: any): boolean =>
    state?.Type === "Task" && payloadOf(state).operation === "PIPELINE_END";

/** The top-level field a JSONPath writes or reads, or null for the whole state (`$`). */
function topField(path: string): string | null {
    if (path === "$") return null;
    const match = /^\$\.([A-Za-z0-9_]+)/.exec(path);
    if (!match) throw new Error(`unsupported path ${path}`);
    return match[1];
}

/** Whether a Choice rule holds for a state whose output holds exactly the `present` fields. */
function holds(rule: any, present: Set<string>): boolean {
    if (rule.Not) return !holds(rule.Not, present);
    if (rule.And) return rule.And.every((r: any) => holds(r, present));
    if (rule.Or) return rule.Or.some((r: any) => holds(r, present));
    if ("IsPresent" in rule) {
        const match = /^\$\.([A-Za-z0-9_]+)$/.exec(rule.Variable);
        if (!match)
            throw new Error(`cannot decide IsPresent on ${rule.Variable} from top-level fields`);
        return present.has(match[1]) === rule.IsPresent;
    }
    throw new Error(`cannot decide the Choice rule ${JSON.stringify(rule)} on this route`);
}

/** A step of a walked route: the state entered and the top-level fields of its input. */
interface Step {
    name: string;
    input: Set<string>;
}

/**
 * The states a run visits from `start`, given the top-level fields of its input, until a terminal
 * state. Every Task returns normally unless `catchAt[name]` names one of its catchers' errors, in
 * which case it takes that catcher.
 */
function walk(
    states: Record<string, any>,
    start: string,
    input: Set<string>,
    catchAt: Record<string, string> = {}
): Step[] {
    const route: Step[] = [];
    let next: string | undefined = start;
    let present = new Set(input);
    while (next) {
        if (route.length > 50) throw new Error(`route from ${start} does not end`);
        const current: string = next;
        const state: any = states[current];
        if (!state) throw new Error(`route from ${start} enters ${current}, which does not exist`);
        if (
            ![undefined, "$"].includes(state.InputPath) ||
            ![undefined, "$"].includes(state.OutputPath)
        ) {
            throw new Error(
                `${current} narrows its input or output, which this walk does not model`
            );
        }
        route.push({ name: current, input: new Set(present) });

        const writeAt = (resultPath: string | null | undefined, fields: string[] | null) => {
            if (resultPath === null) return; // ResultPath null keeps the input unchanged
            const target = topField(resultPath ?? "$");
            if (target === null) {
                if (fields === null) {
                    throw new Error(`${current} replaces its input with a Task result`);
                }
                present = new Set(fields);
            } else {
                present.add(target);
            }
        };
        const following = (): string | undefined => (state.End ? undefined : state.Next);

        switch (state.Type) {
            case "Pass":
                if (state.Parameters !== undefined) {
                    writeAt(
                        state.ResultPath,
                        Object.keys(state.Parameters).map((k) => k.replace(/\.\$$/, ""))
                    );
                } else if (state.Result !== undefined) {
                    throw new Error(`${current} has a Result, which this walk does not model`);
                }
                next = following();
                break;
            case "Task": {
                const error = catchAt[current];
                if (error) {
                    const catcher = (state.Catch ?? []).find((c: any) =>
                        c.ErrorEquals.includes(error)
                    );
                    if (!catcher) throw new Error(`${current} has no catcher for ${error}`);
                    writeAt(catcher.ResultPath, null);
                    next = catcher.Next;
                } else {
                    writeAt(state.ResultPath, null);
                    next = following();
                }
                break;
            }
            case "Wait":
                next = following();
                break;
            case "Choice": {
                const rule = (state.Choices ?? []).find((r: any) => holds(r, present));
                next = rule ? rule.Next : state.Default;
                if (!next) throw new Error(`${current} matches no rule and has no Default`);
                break;
            }
            case "Succeed":
            case "Fail":
                next = undefined;
                break;
            default:
                throw new Error(`${current} is a ${state.Type}, which this walk does not model`);
        }
    }
    return route;
}

/** The top-level fields a Task's payload reads: what its input must carry for it to be invoked. */
function payloadReads(state: any): Set<string> {
    const fields = new Set<string>();
    for (const [key, value] of Object.entries(payloadOf(state))) {
        if (key.endsWith(".$") && typeof value === "string" && value.startsWith("$.")) {
            const field = topField(value);
            if (field) fields.add(field);
        }
    }
    return fields;
}

/** A task's catcher for `error`, and the route a run takes from it. */
function catchRoute(states: Record<string, any>, task: string, error: string): Step[] {
    const catcher = (states[task].Catch ?? []).find((c: any) => c.ErrorEquals.includes(error));
    if (!catcher) throw new Error(`${task} has no catcher for ${error}`);
    const input = payloadReads(states[task]);
    const errorField = topField(catcher.ResultPath ?? "$");
    if (errorField) input.add(errorField);
    return walk(states, catcher.Next, input);
}

const names = (route: Step[]): string[] => route.map((s) => s.name);

describe("EKS deletes a job that a retried RunJob invocation creates after PipelineEnd", () => {
    let synth: SynthResult;
    let stack: string;
    let states: Record<string, any>;
    let fn: { logicalId: string; resource: any };

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableEksPipeline,
            mutateKey: "eks-run-job-late-create-cleanup",
        });
        ({ stack, states } = eksStateMachine(synth));
        fn = invokedFunction(synth, stack, states.RunJob);
    });

    /** The PIPELINE_END invocations that send the parent workflow its callback. */
    const callbackEnds = (): string[] =>
        Object.keys(states).filter(
            (n) =>
                isPipelineEndInvoke(states[n]) && "externalSfnTaskToken.$" in payloadOf(states[n])
        );

    /** The states that hand a run to a PIPELINE_END invocation that sends the callback. */
    const producers = (): string[] => {
        const ends = new Set(callbackEnds());
        return Object.keys(states).filter((n) => ends.has(states[n].Next));
    };

    test("the state machine, RunJob's catchers and the function RunJob invokes are in this synth", () => {
        // The control: the EKS pipeline ships disabled, and a missing function timeout or catcher
        // would leave the route assertions below nothing to compare against.
        expect(synth.resources.filter((r) => r.type === "Custom::AWSCDK-EKS-Cluster").length).toBe(
            1
        );
        expect(states.RunJob.Type).toBe("Task");
        expect(payloadOf(states.RunJob).operation).toBe("RUN_JOB");
        const caught = (states.RunJob.Catch ?? []).flatMap((c: any) => c.ErrorEquals);
        expect(caught).toEqual(expect.arrayContaining(["States.Timeout", "States.ALL"]));
        expect(fn.resource.Type).toBe("AWS::Lambda::Function");
        expect(typeof fn.resource.Properties.Timeout).toBe("number");
        expect(states.EndChoice?.Type).toBe("Choice");
        expect(callbackEnds()).toEqual(
            expect.arrayContaining(["PipelineEnd", "PipelineEndSuccess"])
        );
    });

    describe("RunJob's States.ALL route", () => {
        let route: Step[];
        let waitAt: number;
        let cleanupAt: number;

        beforeAll(() => {
            route = catchRoute(states, "RunJob", "States.ALL");
            waitAt = route.findIndex((s) => states[s.name].Type === "Wait");
            cleanupAt =
                waitAt < 0
                    ? -1
                    : route.findIndex((s, i) => i > waitAt && isPipelineEndInvoke(states[s.name]));
        });

        test("reports the failure at PipelineEnd before any Wait", () => {
            const endAt = names(route).indexOf("PipelineEnd");
            expect(endAt).toBeGreaterThanOrEqual(0);
            expect(waitAt).toBeGreaterThan(endAt);
            // The parent's callback: PipelineEnd sends the token, and no state after the Wait does.
            expect(payloadOf(states.PipelineEnd)["externalSfnTaskToken.$"]).toBe(
                "$.externalSfnTaskToken"
            );
            for (const step of route.slice(waitAt)) {
                expect({
                    state: step.name,
                    payload: Object.keys(payloadOf(states[step.name])),
                }).toEqual({
                    state: step.name,
                    payload: expect.not.arrayContaining([
                        "externalSfnTaskToken",
                        "externalSfnTaskToken.$",
                    ]),
                });
            }
        });

        test("waits at least as long as the function RunJob invokes can run", () => {
            expect(waitAt).toBeGreaterThan(0);
            const wait = states[route[waitAt].name];
            expect(typeof wait.Seconds).toBe("number");
            expect(wait.Seconds).toBeGreaterThanOrEqual(fn.resource.Properties.Timeout);
        });

        test("then deletes the same job with a PIPELINE_END that carries no token and no error", () => {
            expect(waitAt).toBeGreaterThan(0);
            expect(cleanupAt).toBeGreaterThan(waitAt);
            const cleanup = states[route[cleanupAt].name];
            expect(invokedFunction(synth, stack, cleanup).logicalId).toBe(fn.logicalId);
            const payload = payloadOf(cleanup);
            expect(payload["k8sJobName.$"]).toBe(payloadOf(states.PipelineEnd)["k8sJobName.$"]);
            for (const absent of [
                "externalSfnTaskToken",
                "externalSfnTaskToken.$",
                "error",
                "error.$",
            ]) {
                expect(Object.keys(payload)).not.toContain(absent);
            }
            // The job name the delete reads is the manifest name RunJob's handler put there.
            expect(route[cleanupAt].input.has("k8sJobName")).toBe(true);
        });

        test("then ends at Failure through EndChoice with $.error intact", () => {
            expect(cleanupAt).toBeGreaterThan(0);
            const tail = names(route).slice(cleanupAt + 1);
            expect(tail).toEqual(["EndChoice", "Failure"]);
            const cleanup = states[route[cleanupAt].name];
            expect(["$", "$.error", undefined]).not.toContain(cleanup.ResultPath);
            expect(route[cleanupAt + 1].input.has("error")).toBe(true);
        });

        test("a cleanup whose own invocation fails still ends at Failure with the run's error", () => {
            expect(cleanupAt).toBeGreaterThan(0);
            const cleanupName = route[cleanupAt].name;
            const catcher = (states[cleanupName].Catch ?? []).find((c: any) =>
                c.ErrorEquals.includes("States.ALL")
            );
            expect(catcher).toBeDefined();
            expect(["$", "$.error", undefined]).not.toContain(catcher.ResultPath);

            const handler = (states.RunJob.Catch ?? []).find((c: any) =>
                c.ErrorEquals.includes("States.ALL")
            );
            const input = payloadReads(states.RunJob);
            input.add("error");
            const failed = walk(states, handler.Next, input, { [cleanupName]: "States.ALL" });
            const after = failed.findIndex((s) => s.name === cleanupName);
            expect(names(failed).slice(after + 1)).toEqual(["EndChoice", "Failure"]);
            expect(failed[after + 1].input.has("error")).toBe(true);
        });
    });

    describe("no other route goes through a Wait or a second PIPELINE_END", () => {
        const assertDirect = (label: string, route: Step[]) => {
            const ends = names(route).filter((n) => isPipelineEndInvoke(states[n]));
            const waits = names(route).filter((n) => states[n].Type === "Wait");
            expect({ label, ends: ends.length, waits }).toEqual({ label, ends: 1, waits: [] });
            expect({ label, tail: names(route).slice(-2) }).toEqual({
                label,
                tail: expect.arrayContaining(["EndChoice"]),
            });
        };

        test("ConstructPipeline's and CheckJob's catchers, and RunJob's States.Timeout catcher", () => {
            for (const [task, error] of [
                ["ConstructPipeline", "States.Timeout"],
                ["ConstructPipeline", "States.ALL"],
                ["CheckJob", "States.Timeout"],
                ["CheckJob", "States.ALL"],
                ["RunJob", "States.Timeout"],
            ]) {
                const route = catchRoute(states, task, error);
                assertDirect(`${task} ${error}`, route);
                expect(names(route).slice(-1)).toEqual(["Failure"]);
            }
        });

        test("the success route", () => {
            const completed = (states["Job Complete?"].Choices ?? []).find(
                (c: any) => c.StringEquals === "COMPLETED"
            )?.Next;
            expect(completed).toBeDefined();
            const route = walk(states, completed, new Set());
            assertDirect("success", route);
            expect(names(route).slice(-1)).toEqual(["Success"]);
        });

        test("every other state that hands a run to PipelineEnd", () => {
            const handler = (states.RunJob.Catch ?? []).find((c: any) =>
                c.ErrorEquals.includes("States.ALL")
            ).Next;
            const others = producers().filter((n) => n !== handler);
            // Control: the Choice-routed failures and the poll loop's exits are among them.
            expect(others).toEqual(
                expect.arrayContaining([
                    "HandleConstructPipelineError",
                    "HandleRunJobError",
                    "Timeout Job",
                    "Job Failed",
                    "Unknown Job Status",
                ])
            );
            for (const producer of others) {
                expect(states[producer].Type).toBe("Pass");
                assertDirect(producer, walk(states, producer, new Set()));
            }
        });
    });
});
