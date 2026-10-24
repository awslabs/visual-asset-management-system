/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The RapidPipeline EKS failure exits that bypass a task Catch differ in whether a Kubernetes job can
 * exist to delete.
 *
 *   * `HandleConstructPipelineError` — CONSTRUCT_PIPELINE returned no manifest, so no job was ever
 *     created. The literal `failure-before-creation` is the truthful value there.
 *   * `Timeout Job` — the poll reached its ceiling while a job was running. A job DOES exist, and its
 *     name is the only handle `handle_pipeline_end` has for deleting it: it reads `$.k8sJobName` and
 *     keys `cleanup_completed_job(...)` / `delete_job(...)` off exactly that value.
 *
 * Carrying the placeholder through the timeout exit therefore asks Kubernetes to delete a job that
 * never existed. Both deletes fail, only a log line records it, and the real pod keeps running on its
 * node until `activeDeadlineSeconds` expires — billing a 2 vCPU / 16 GiB node for the remainder.
 * (`HandleRunJobError` carries the manifest's job name for the same reason; eksRunEndedMarker.test.ts
 * covers it.)
 *
 * Asserted on the emitted state machine definition rather than the TypeScript, because what matters is
 * that the field arrives at PipelineEnd as a JSONPath reference resolved by Step Functions. The
 * positive control is the sibling exit that legitimately keeps the literal: without it an assertion
 * that the literal is absent from the timeout state would also pass against an ASL in which neither
 * state was emitted at all.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import { SynthResult, synthTemplate } from "../support/templateSynth";

/** The literal that means "no Kubernetes job exists to clean up". */
const NO_JOB_PLACEHOLDER = "failure-before-creation";

function enableEksPipeline(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.useRapidPipeline.enabled = true;
    c.app.pipelines.useRapidPipeline.autoRegisterWithVAMS = false;
    c.app.pipelines.useRapidPipeline.useEks.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
        "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
}

/**
 * The EKS pipeline's state machine definition, parsed out of the emitted `DefinitionString`. The
 * definition is emitted as an `Fn::Join` of literal fragments around CloudFormation tokens, so the
 * fragments are concatenated with a placeholder standing in for each token before parsing.
 */
function eksDefinition(synth: SynthResult): any {
    const definitions = Object.values(synth.templates)
        .flatMap((t: any) => Object.values(t.Resources ?? {}))
        .filter((r: any) => r.Type === "AWS::StepFunctions::StateMachine")
        .map((r: any) => r.Properties?.DefinitionString);

    for (const definition of definitions) {
        const joined = SynthResult.flatten(definition);
        if (!joined.includes("Timeout Job")) continue;
        return JSON.parse(joined);
    }
    throw new Error("no emitted state machine carries a 'Timeout Job' state");
}

describe("EKS Timeout Job preserves the job name cleanup needs", () => {
    let synth: SynthResult;
    let definition: any;
    let states: Record<string, any>;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableEksPipeline,
            mutateKey: "eks-timeout-job-cleanup",
        });
        definition = eksDefinition(synth);
        states = definition.States;
    });

    test("the pipeline IS in this synth and both failure exits were emitted", () => {
        // The control for every assertion below: the EKS pipeline ships disabled, and a missing state
        // would satisfy an absence assertion just as well as a corrected one.
        expect(synth.resources.filter((r) => r.type === "Custom::AWSCDK-EKS-Cluster").length).toBe(
            1
        );
        expect(states["Timeout Job"]).toBeDefined();
        expect(states["HandleConstructPipelineError"]).toBeDefined();
    });

    test("the construct-failure exit keeps the placeholder", () => {
        // The positive control for the literal itself: it is still written somewhere in this ASL, so
        // its absence from the timeout state is a real difference rather than a search that matched
        // nothing. Deleting the k8s job is meaningless here because no manifest was ever built.
        expect(states["HandleConstructPipelineError"].Parameters.k8sJobName).toBe(
            NO_JOB_PLACEHOLDER
        );
    });

    test("the poll-timeout exit carries the real job name as a JSONPath reference", () => {
        const parameters = states["Timeout Job"].Parameters;
        expect(parameters["k8sJobName.$"]).toBe("$.k8sJobName");
        expect(parameters.k8sJobName).toBeUndefined();
    });

    test("the field the timeout exit reads is carried by every state that reaches it", () => {
        // `$.k8sJobName` resolves only if each state on the path to Timeout Job re-emits it. Step
        // Functions raises States.Runtime for a missing path, and in a Pass state that is raised
        // before the state is entered, so no Catch routes it and the parent's task token is never
        // released — the failure mode a plain literal avoided by not reading anything.
        for (const name of ["InitializeCounter", "IncrementCounter", "RecordJobStatus"]) {
            expect(states[name]).toBeDefined();
            expect(states[name].Parameters["k8sJobName.$"]).toBeDefined();
        }
    });

    test("the handler deletes whatever job name the state machine hands it", () => {
        // The other half of the pair: preserving the name only matters because PipelineEnd keys its
        // cleanup off that field, and skips cleanup only for the placeholder/unknown values.
        const source = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../../backendPipelines/multi/rapidPipelineEKS/lambda/consolidated_handler.py"
            ),
            "utf-8"
        );
        expect(source).toContain("k8s_job_name = event.get('k8sJobName', 'unknown')");
        expect(source).toContain("cleanup_completed_job(k8s_job_name");
    });

    test("the bundle taskTimeout still bounds the run", () => {
        // The parent workflow's pipeline task starts before this state machine does (vamsExecute and
        // openPipeline run first), so the bundle taskTimeout — the parent's wait, which pipeline.json
        // declares as this constant (eksJobTimeoutChain.test.ts) — must STRICTLY outlast the state
        // machine's own timeout: the parent is then still waiting when PipelineEnd reports, and its
        // taskTimeout is what releases it if States.Timeout stops the state machine. That timeout is
        // read from the definition this construct emitted, not from config.ts, so a construct that
        // stopped setting it, or set a longer one, fails here.
        const stateMachineTimeout = definition.TimeoutSeconds;
        // The control: an absent timeout must not make the comparison below pass vacuously.
        expect(typeof stateMachineTimeout).toBe("number");
        expect(stateMachineTimeout).toBeGreaterThan(0);
        expect(Config.RAPID_PIPELINE_EKS_BUNDLE_TASK_TIMEOUT_SECONDS).toBeGreaterThan(
            stateMachineTimeout
        );
    });
});
