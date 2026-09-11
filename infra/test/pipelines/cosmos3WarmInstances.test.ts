/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A warm GPU floor must only be held for a tier that can receive a job.
 *
 * `useWarmInstances` keeps AWS Batch capacity running so a job starts without waiting for an instance to
 * launch, and the Cosmos 3 construct implemented that as `minvCpus = warmInstanceCount * 48` on the
 * **Nano** tier's compute environment — unconditionally. A deployment with warm instances on and no Nano
 * model enabled therefore held g6e instances running for a tier that can never be sent work. Nothing
 * reported it, because an idle warm instance is exactly what the feature produces when it is working.
 *
 * The Super tier hardcodes `minvCpus: 0`, so warm instances have never applied there. That is asserted
 * too, and documented in the configuration reference, rather than left as a difference a reader would
 * have to discover — the finding offered extending the feature or documenting it, and extending it would
 * raise the bill for every operator who already has the flag on.
 *
 * Asserted on the emitted `AWS::Batch::ComputeEnvironment`, not on the construct, because the value that
 * costs money is the one CloudFormation receives.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate } from "../support/templateSynth";

/**
 * Cosmos 3 with warm instances requested and a SUPER model always enabled.
 *
 * The Super model is what makes the Nano-disabled case meaningful. Measured: with every model off the
 * construct emits no compute environment at all, so "no environment holds a warm floor" was vacuously
 * true and passed with the defect restored. With a Super model on, the Nano-tier environment IS created
 * unconditionally — which is the state the finding describes — and its floor is the thing under test.
 */
function cosmos3With(nanoEnabled: boolean) {
    return (c: any) => {
        c.app.useGlobalVpc.enabled = true;
        const cosmos3 = c.app.pipelines.useNvidiaCosmos3;
        cosmos3.enabled = true;
        cosmos3.useWarmInstances = true;
        cosmos3.warmInstanceCount = 1;
        if (cosmos3.modelsOmni?.nano16B) {
            cosmos3.modelsOmni.nano16B.enabled = nanoEnabled;
            cosmos3.modelsOmni.nano16B.autoRegisterWithVAMS = false;
        }
        if (cosmos3.modelsOmni?.super64B) {
            cosmos3.modelsOmni.super64B.enabled = true;
            cosmos3.modelsOmni.super64B.autoRegisterWithVAMS = false;
        }
    };
}

/** Every Batch compute environment in the assembly, with its emitted vCPU floor. */
function computeEnvironments(synth: SynthResult) {
    return synth.ofType("AWS::Batch::ComputeEnvironment").map((e) => ({
        logicalId: e.logicalId,
        minvCpus: (e.properties as any).ComputeResources?.MinvCpus,
        desiredvCpus: (e.properties as any).ComputeResources?.DesiredvCpus,
        instanceTypes: (e.properties as any).ComputeResources?.InstanceTypes ?? [],
    }));
}

describe("Cosmos 3 warm-instance floor", () => {
    test("the Cosmos 3 compute environments ARE in this synth", () => {
        // The control. Every assertion about a vCPU floor is satisfied by a synth containing no compute
        // environment, and Cosmos 3 ships disabled.
        const synth = synthTemplate("commercial", {
            mutate: cosmos3With(true),
            mutateKey: "cosmos3-warm-nano-on-super-on",
        });
        const gpu = computeEnvironments(synth).filter((e) =>
            e.instanceTypes.some((t: string) => /^g\d|^p\d/.test(t))
        );
        expect(gpu.length).toBeGreaterThan(0);
    });

    test("with the Nano tier ENABLED, the warm floor is held", () => {
        // The positive control for the gating below: without it, a fix that set the floor to zero
        // unconditionally would pass the next test and silently remove the feature.
        const synth = synthTemplate("commercial", {
            mutate: cosmos3With(true),
            mutateKey: "cosmos3-warm-nano-on-super-on",
        });
        const withFloor = computeEnvironments(synth).filter((e) => Number(e.minvCpus) > 0);
        expect(withFloor.length).toBeGreaterThan(0);
        // And the floor is mirrored into DesiredvCpus, or Batch scales straight back to zero.
        for (const env of withFloor) {
            expect(Number(env.desiredvCpus)).toBe(Number(env.minvCpus));
        }
    });

    test("with the Nano tier DISABLED, no environment holds a warm floor", () => {
        // The defect: warm instances on, Nano off, GPU capacity running for a tier with no job to run.
        const synth = synthTemplate("commercial", {
            mutate: cosmos3With(false),
            mutateKey: "cosmos3-warm-nano-off-super-on",
        });
        const environments = computeEnvironments(synth);
        // The control that makes this non-vacuous: the Nano-tier environment is created even with the
        // tier off (unlike the Super one, which is guarded), so BOTH must be present here. Without this
        // the assertion below holds for a synth that emitted nothing.
        expect(environments.length).toBeGreaterThanOrEqual(2);

        const withFloor = environments
            .filter((e) => Number(e.minvCpus) > 0)
            .map((e) => `${e.logicalId}: minvCpus=${e.minvCpus}`);
        expect(withFloor).toEqual([]);
    });

    test("the Super tier never holds a warm floor, and that is documented", () => {
        // A difference worth pinning: an operator enabling warm instances for Super gets nothing, and
        // the reference now says so. Without the documentation assertion this reads as an oversight
        // rather than as stated behaviour.
        const reference = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../../documentation/docusaurus-site/docs/deployment/configuration-reference.md"
            ),
            "utf-8"
        );
        const row = reference
            .split("\n")
            .find((l: string) => l.includes("useNvidiaCosmos3.useWarmInstances"));
        expect(row).toBeDefined();
        expect(row).toMatch(/Nano tier only/i);

        const source = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../lib/nestedStacks/pipelines/genAi/nvidia/cosmos/constructs/cosmos3-construct.ts"
            ),
            "utf-8"
        );
        // The Super environment's floor is a literal zero, not the warm-instance expression.
        expect(source).toMatch(/minvCpus:\s*0,/);
    });
});
