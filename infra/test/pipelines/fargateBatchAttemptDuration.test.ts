/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every Fargate Batch job must carry an attempt duration, so a wedged container cannot run unbounded.
 *
 * `BatchFargatePipelineConstruct` set `retryAttempts` but no `timeout`, so its job definitions had no
 * `AttemptDurationSeconds`. A 16 vCPU / 64 GiB container that stops making progress then runs until
 * someone notices. The orchestration's own timeout is not a substitute for the one caller that matters:
 * the coordinate-transform pipeline submits its job from a Lambda under `WAIT_FOR_TASK_TOKEN`, so Step
 * Functions bounds only the token wait, not the container. (The other callers submit through
 * `BatchSubmitJob`, which defaults to `RUN_JOB` — there Step Functions owns the job lifecycle, so the
 * exposure is smaller but the backstop is still free.)
 *
 * VAMS does have a termination path — `stop_registered_sub_process` calls `batch:TerminateJob` for a
 * registered `batchJob` during failure reconciliation — so this is a missing backstop rather than a
 * total absence of cleanup. The backstop matters when reconciliation itself does not run.
 *
 * `attemptDuration` is a REQUIRED prop rather than an optional one with a default, and that earned its
 * keep immediately: making it required surfaced a fifth instantiation site (the Potree converter, a
 * second Fargate pipeline inside `pcPotreeViewer-construct.ts`) that a search for the construct's more
 * obvious call sites had missed.
 *
 * Asserted on the emitted `AWS::Batch::JobDefinition`, because the limit that stops a container is the
 * one AWS Batch receives.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate } from "../support/templateSynth";

/** Enable the four pipelines that build Fargate Batch jobs. */
function fargatePipelines(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.useGlobalVpc.addVpcEndpoints = true;
    for (const flag of [
        "useConversionCoordinateTransform",
        "useGenAiMetadata3dLabeling",
        "usePreview3dThumbnail",
        "usePreviewPcPotreeViewer",
    ]) {
        if (c.app.pipelines[flag]) {
            c.app.pipelines[flag].enabled = true;
            if (c.app.pipelines[flag].autoRegisterWithVAMS !== undefined) {
                c.app.pipelines[flag].autoRegisterWithVAMS = false;
            }
        }
    }
}

/** Fargate job definitions, identified by the platform capability Batch receives. */
function fargateJobDefinitions(synth: SynthResult) {
    return synth.ofType("AWS::Batch::JobDefinition").filter((jd) => {
        const capabilities = ((jd.properties as any).PlatformCapabilities ?? []) as string[];
        return capabilities.includes("FARGATE");
    });
}

describe("Fargate Batch job attempt duration", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: fargatePipelines,
            mutateKey: "fargate-attempt-duration",
        });
    });

    test("[control] Fargate job definitions ARE emitted in this synth", () => {
        // All four pipelines ship disabled, so every assertion below is otherwise vacuous. Five job
        // definitions are expected: one each for coordinate transform, metadata labeling and the 3D
        // thumbnail, plus PDAL and Potree from the point-cloud viewer.
        expect(fargateJobDefinitions(synth).length).toBeGreaterThanOrEqual(5);
    });

    test("every Fargate job definition declares an attempt duration", () => {
        const missing = fargateJobDefinitions(synth)
            .filter((jd) => {
                const seconds = (jd.properties as any).Timeout?.AttemptDurationSeconds;
                return typeof seconds !== "number" || seconds <= 0;
            })
            .map((jd) => `${jd.stack}/${jd.logicalId}`);
        expect(missing).toEqual([]);
    });

    test("no attempt duration exceeds the state machine timeout that encloses it", () => {
        // A backstop longer than the orchestration's own limit would never fire on a live execution and
        // would only delay cleanup of an orphan. Compared against the LARGEST state machine timeout in
        // the assembly rather than per pipeline: matching a job definition to its own state machine
        // across nested templates would rest on logical-id shape, which is exactly the kind of
        // inference that goes stale silently. The bound is still meaningful because it rejects a value
        // that outlives every orchestration in the deployment.
        const executionTimeouts = synth.ofType("AWS::StepFunctions::StateMachine").flatMap((sm) => {
            const text = JSON.stringify((sm.properties as any).DefinitionString);
            return [...text.matchAll(/TimeoutSeconds\\":(\d+)/g)].map((m) => Number(m[1]));
        });
        // The control: no state machine timeouts found would make the comparison vacuous.
        expect(executionTimeouts.length).toBeGreaterThan(0);
        const longestOrchestration = Math.max(...executionTimeouts);

        const tooLong = fargateJobDefinitions(synth)
            .map((jd) => ({
                id: `${jd.stack}/${jd.logicalId}`,
                seconds: (jd.properties as any).Timeout?.AttemptDurationSeconds as number,
            }))
            .filter((jd) => jd.seconds > longestOrchestration)
            .map(
                (jd) => `${jd.id}=${jd.seconds}s vs longest orchestration ${longestOrchestration}s`
            );
        expect(tooLong).toEqual([]);
    });

    test("the construct requires the caller to supply it", () => {
        // The property that prevents recurrence. An optional prop with a default would let a new
        // pipeline inherit a bound nobody chose; required means the compiler asks. Asserted on the
        // source because the type is erased from the synthesized output.
        const source = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../lib/nestedStacks/pipelines/constructs/batch-fargate-pipeline.ts"
            ),
            "utf-8"
        );
        // No `?:` — a required member of the props interface.
        expect(source).toMatch(/\n\s+attemptDuration: cdk\.Duration;/);
        expect(source).toMatch(/timeout: props\.attemptDuration,/);
    });
});
