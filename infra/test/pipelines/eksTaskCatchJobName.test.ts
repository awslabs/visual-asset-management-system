/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Each RapidPipeline EKS task's Catch handlers hand PipelineEnd the Kubernetes job that exists when
 * that task fails, and read nothing the task's input does not carry.
 *
 * ConstructPipeline, RunJob and CheckJob are Lambda tasks whose Catch routes to Pass states that go
 * on to PipelineEnd. Two properties of those handlers decide whether the failure is cleaned up:
 *
 *   * `k8sJobName` is the only handle `handle_pipeline_end` has for deleting the job. A placeholder
 *     there after a CheckJob failure, or after a RunJob failure once the job was created, deletes
 *     nothing, and the real pod runs on until `activeDeadlineSeconds`.
 *   * A Pass state that reads a JSONPath absent from its input fails with States.Runtime. That error
 *     is raised before the state is entered, so no Catch routes it and the run never reaches
 *     PipelineEnd; the parent workflow's task token is held until its taskTimeout. `$.k8sJobName` is
 *     first written by InitializeCounter, after RunJob, so a handler on ConstructPipeline or RunJob
 *     must not read it.
 *
 * What a handler may read is derived from the ASL rather than listed by hand. A catcher's input is
 * the failed task's own input with the caught error at its ResultPath, and every path the task's
 * payload reads is present in that input, because the task fails with States.Runtime, uncaught,
 * before it is invoked if one is missing. So a handler may read the catcher's ResultPath, the
 * Context object, and the paths (or descendants of the paths) its task's payload reads.
 *
 * Asserted on the emitted state machine definition, because what matters is the JSONPath Step
 * Functions resolves, not the TypeScript that produced it. The positive control is that every task
 * has catchers and every handler names a job: an absence assertion alone would also pass against
 * an ASL in which no handler was emitted.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate } from "../support/templateSynth";

/** The literal that means "no Kubernetes job exists to clean up". */
const NO_JOB_PLACEHOLDER = "failure-before-creation";

/** The literal the handlers carried for every task, whether or not a job existed. */
const STALE_PLACEHOLDER = "timeout-before-creation";

/** Where the job's name sits in the manifest CONSTRUCT_PIPELINE returns and RUN_JOB submits. */
const MANIFEST_JOB_NAME_PATH = "$.ConstructPipelineResult.Payload.jobManifest.metadata.name";

/** The tasks whose Catch handlers this suite covers, and the job name each must hand PipelineEnd. */
const EXPECTED_JOB_NAME: Record<string, { literal?: string; path?: string }> = {
    ConstructPipeline: { literal: NO_JOB_PLACEHOLDER },
    RunJob: { path: MANIFEST_JOB_NAME_PATH },
    CheckJob: { path: "$.k8sJobName" },
};

const LAMBDA_DIR = path.resolve(
    __dirname,
    "../../../backendPipelines/multi/rapidPipelineEKS/lambda"
);

function enableEksPipeline(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
        "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
}

/**
 * The EKS pipeline's state machine states, parsed out of the emitted `DefinitionString`. The
 * definition is emitted as an `Fn::Join` of literal fragments around CloudFormation tokens, so the
 * fragments are concatenated with a placeholder standing in for each token before parsing.
 */
function eksStates(synth: SynthResult): Record<string, any> {
    const definitions = Object.values(synth.templates)
        .flatMap((t: any) => Object.values(t.Resources ?? {}))
        .filter((r: any) => r.Type === "AWS::StepFunctions::StateMachine")
        .map((r: any) => SynthResult.flatten(r.Properties?.DefinitionString));

    const definition = definitions.find(
        (d) => d.includes('"CheckJob"') && d.includes('"InitializeCounter"')
    );
    if (!definition) throw new Error("no emitted state machine carries the EKS CheckJob state");
    return JSON.parse(definition).States;
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

describe("EKS task Catch handlers carry the job name PipelineEnd deletes", () => {
    let synth: SynthResult;
    let states: Record<string, any>;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableEksPipeline,
            mutateKey: "eks-task-catch-job-name",
        });
        states = eksStates(synth);
    });

    /** The Pass states a task's Catch routes to, with the catcher that routes to each. */
    const handlersOf = (task: string): Array<{ catcher: any; name: string; state: any }> =>
        (states[task].Catch ?? []).map((catcher: any) => ({
            catcher,
            name: catcher.Next,
            state: states[catcher.Next],
        }));

    /** The state that runs PIPELINE_END with an `error`, which the failure exits converge on. */
    const failurePipelineEnd = (): string => {
        const ends = Object.keys(states).filter((name) => {
            const payload = states[name].Parameters?.Payload ?? {};
            return payload.operation === "PIPELINE_END" && "error.$" in payload;
        });
        expect(ends).toHaveLength(1);
        return ends[0];
    };

    test("the pipeline IS in this synth and each task routes its errors to handlers", () => {
        // The control for every assertion below: the EKS pipeline ships disabled, and a task with no
        // Catch would satisfy every per-handler assertion vacuously.
        expect(synth.resources.filter((r) => r.type === "Custom::AWSCDK-EKS-Cluster").length).toBe(
            1
        );
        for (const task of Object.keys(EXPECTED_JOB_NAME)) {
            expect(states[task]).toBeDefined();
            const handlers = handlersOf(task);
            expect(handlers.length).toBeGreaterThan(0);
            const caught = handlers.flatMap((h) => h.catcher.ErrorEquals);
            expect(caught).toContain("States.Timeout");
            expect(caught).toContain("States.ALL");
            for (const handler of handlers) {
                expect(handler.state).toBeDefined();
                expect(handler.state.Type).toBe("Pass");
            }
        }
    });

    test("every handler writes the caught error to $.error and goes to the failure PipelineEnd", () => {
        const end = failurePipelineEnd();
        for (const task of Object.keys(EXPECTED_JOB_NAME)) {
            for (const handler of handlersOf(task)) {
                expect(handler.catcher.ResultPath).toBe("$.error");
                expect(handler.state.Next).toBe(end);
            }
        }
    });

    test("every handler reads only paths present when its task fails", () => {
        // The general form of the defect: a handler reading $.k8sJobName on a task that runs before
        // InitializeCounter writes it. A path is present if the task's own payload reads it (or it
        // lies under one that does), or if the catcher wrote it.
        const violations: string[] = [];
        for (const task of Object.keys(EXPECTED_JOB_NAME)) {
            const taskReads = readPaths(states[task].Parameters?.Payload);
            expect(taskReads.length).toBeGreaterThan(0); // control: the payload was found
            for (const { catcher, name, state } of handlersOf(task)) {
                const available = [...taskReads, catcher.ResultPath];
                for (const read of readPaths(state.Parameters)) {
                    if (!available.some((root) => isAtOrUnder(read, root))) {
                        violations.push(`${task} -> ${name} reads ${read}`);
                    }
                }
            }
        }
        expect(violations).toEqual([]);
    });

    test("CheckJob's handlers carry the job name the poll loop holds", () => {
        for (const { state } of handlersOf("CheckJob")) {
            expect(state.Parameters["k8sJobName.$"]).toBe("$.k8sJobName");
            expect(state.Parameters.k8sJobName).toBeUndefined();
        }
    });

    test("RunJob's handlers carry the name of the job RunJob submits", () => {
        // The job may have been created before RunJob failed or timed out. Deleting one that was not
        // created is reported as already deleted, so carrying the name is safe either way.
        for (const { state } of handlersOf("RunJob")) {
            expect(state.Parameters["k8sJobName.$"]).toBe(MANIFEST_JOB_NAME_PATH);
            expect(state.Parameters.k8sJobName).toBeUndefined();
        }
    });

    test("ConstructPipeline's handlers say that no job exists", () => {
        for (const { state } of handlersOf("ConstructPipeline")) {
            expect(state.Parameters.k8sJobName).toBe(NO_JOB_PLACEHOLDER);
            expect(state.Parameters["k8sJobName.$"]).toBeUndefined();
        }
    });

    test("no handler on RunJob or CheckJob carries a placeholder job name", () => {
        // The tests above pin the field each handler sets, which is the control for this absence.
        for (const task of ["RunJob", "CheckJob"]) {
            for (const { name, state } of handlersOf(task)) {
                const rendered = JSON.stringify(state.Parameters);
                expect({ name, hasStale: rendered.includes(STALE_PLACEHOLDER) }).toEqual({
                    name,
                    hasStale: false,
                });
                expect({ name, hasNoJob: rendered.includes(NO_JOB_PLACEHOLDER) }).toEqual({
                    name,
                    hasNoJob: false,
                });
            }
        }
    });

    test("a timeout reports States.Timeout and any other error passes the caught error on", () => {
        for (const task of Object.keys(EXPECTED_JOB_NAME)) {
            for (const { catcher, state } of handlersOf(task)) {
                if (catcher.ErrorEquals.includes("States.Timeout")) {
                    expect(state.Parameters.error).toEqual({
                        Error: "States.Timeout",
                        Cause: "Lambda function execution timed out",
                    });
                } else {
                    expect(state.Parameters["error.$"]).toBe("$.error");
                }
                expect(state.Parameters.errorContext).toEqual({
                    "timestamp.$": "$$.State.EnteredTime",
                    "stateName.$": "$$.State.Name",
                    "executionName.$": "$$.Execution.Name",
                });
            }
        }
    });

    test("the manifest name is the name the job is created and deleted under", () => {
        // The other half of the RunJob path: CONSTRUCT_PIPELINE names the job in metadata.name,
        // create_job submits it under that name, and the PipelineEnd delete treats a job that is not
        // found as already deleted.
        const handler = fs.readFileSync(path.join(LAMBDA_DIR, "consolidated_handler.py"), "utf-8");
        const k8s = fs.readFileSync(path.join(LAMBDA_DIR, "kubernetes_utils.py"), "utf-8");
        expect(handler).toMatch(/"metadata": \{\s*"name": f"rapid-pipeline-job-\{job_id\}"/);
        expect(k8s).toContain("job_name = metadata.get('name')");
        expect(k8s).toContain('return True, "Job already deleted"');
    });
});
