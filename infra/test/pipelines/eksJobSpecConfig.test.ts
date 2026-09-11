/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Five `useEks` options were documented, present in all three config templates, and read by no code:
 * `nodeInstanceType` reached only the node LABEL, and `jobMemory`, `jobCpu`, `jobBackoffLimit` and
 * `jobTTLSecondsAfterFinished` reached nothing at all — the handler hardcoded each one. Setting any of
 * them changed nothing, silently, which is worse than the option not existing: an operator who sized the
 * nodes up got the old size wearing a label claiming the new one.
 *
 * `jobTimeout`, the sixth, is covered by `eksJobTimeoutChain.test.ts`; that finding is what surfaced
 * these. `minNodes`/`maxNodes`/`desiredNodes` were already wired and are asserted here as the control —
 * they are what a "wired" option looks like in the emitted template.
 *
 * The synth mutates every value AWAY from its shipped default, because that is the only form of this
 * assertion that can fail. Asserting the shipped value reaches the template proves nothing: the
 * hardcoded literals were *equal* to the shipped values, so a completely unwired build passes such a
 * check. The mutated values are deliberately implausible (`31Gi`, `1750m`) so no future default
 * collides with them.
 *
 * The last test is the other half, and it is the one that makes this change safe to deploy: each
 * handler-side default must still equal the shipped config value, so a Lambda running code that predates
 * these environment variables — or a deployment that has not tuned them — produces the same job spec it
 * did before.
 */

import * as path from "path";
import * as fs from "fs";
import commercialTemplate from "../../config/config.template.commercial.json";
import { SynthResult, synthTemplate } from "../support/templateSynth";

const realReadFileSync = jest.requireActual("fs").readFileSync;

/** Values chosen so that no plausible future default equals one of them. */
const MUTATED = {
    nodeInstanceType: "c6i.8xlarge",
    minNodes: 3,
    desiredNodes: 4,
    maxNodes: 7,
    jobMemory: "31Gi",
    jobCpu: "1750m",
    jobBackoffLimit: 5,
    jobTTLSecondsAfterFinished: 1234,
};

const HANDLER = path.resolve(
    __dirname,
    "../../../backendPipelines/multi/rapidPipelineEKS/lambda/consolidated_handler.py"
);

const shipped = () => (commercialTemplate as any).app.pipelines.useRapidPipeline.useEks;

function enableEksPipelineWithMutatedJobSpec(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.useRapidPipeline.enabled = true;
    c.app.pipelines.useRapidPipeline.autoRegisterWithVAMS = false;
    c.app.pipelines.useRapidPipeline.useEks.enabled = true;
    c.app.pipelines.useRapidPipeline.useEks.ecrContainerImageURI =
        "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
    Object.assign(c.app.pipelines.useRapidPipeline.useEks, MUTATED);
}

describe("useEks job-spec and node options reach the deployment", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableEksPipelineWithMutatedJobSpec,
            mutateKey: "eks-job-spec-config",
        });
    });

    /** The one Lambda that builds the Kubernetes Job spec. */
    const consolidatedHandlerEnv = () => {
        const handlers = synth
            .ofType("AWS::Lambda::Function")
            .filter((f) => "EKS_CLUSTER_NAME" in (f.properties.Environment?.Variables ?? {}));
        expect(handlers.length).toBe(1);
        return handlers[0].properties.Environment.Variables as Record<string, string>;
    };

    const nodegroups = () => synth.ofType("AWS::EKS::Nodegroup");

    test("the pipeline IS in this synth", () => {
        // Control. The EKS pipeline ships disabled, so every assertion below would pass vacuously
        // against a template that contains none of its resources.
        expect(synth.resources.filter((r) => r.type === "Custom::AWSCDK-EKS-Cluster").length).toBe(
            1
        );
        expect(nodegroups().length).toBe(1);
    });

    test("the mutated values differ from the shipped ones", () => {
        // Control for the whole file: if a shipped default ever moved onto one of these values, the
        // corresponding assertion would stop discriminating between wired and hardcoded.
        for (const [key, value] of Object.entries(MUTATED)) {
            expect(shipped()[key]).not.toBe(value);
        }
    });

    test("nodeInstanceType selects the node group's instance type", () => {
        // The defect: this reached the node label only, so the nodes were m5.2xlarge whatever was set.
        expect(nodegroups()[0].properties.InstanceTypes).toEqual([MUTATED.nodeInstanceType]);
    });

    test("the node counts reach the same node group", () => {
        // These three were already wired. They are the reference for what the assertion above should
        // have looked like all along.
        expect(nodegroups()[0].properties.ScalingConfig).toMatchObject({
            MinSize: MUTATED.minNodes,
            DesiredSize: MUTATED.desiredNodes,
            MaxSize: MUTATED.maxNodes,
        });
    });

    test("the four job-spec options reach the handler that builds the Job", () => {
        const env = consolidatedHandlerEnv();
        expect(env.EKS_JOB_MEMORY).toBe(MUTATED.jobMemory);
        expect(env.EKS_JOB_CPU).toBe(MUTATED.jobCpu);
        expect(Number(env.EKS_JOB_BACKOFF_LIMIT)).toBe(MUTATED.jobBackoffLimit);
        expect(Number(env.EKS_JOB_TTL_SECONDS_AFTER_FINISHED)).toBe(
            MUTATED.jobTTLSecondsAfterFinished
        );
    });

    test("the handler builds the Job spec from those variables, not from literals", () => {
        // The env var can be delivered and ignored, which is exactly the state this finding described
        // for `nodeInstanceType`. Asserted on the substituted sites so a variable read into an unused
        // name would not satisfy it.
        const source = realReadFileSync(HANDLER, "utf-8");
        expect(source).toContain('"memory": JOB_MEMORY');
        expect(source).toContain('"cpu": JOB_CPU');
        expect(source).toContain('"backoffLimit": JOB_BACKOFF_LIMIT');
        expect(source).toContain('"ttlSecondsAfterFinished": JOB_TTL_SECONDS_AFTER_FINISHED');

        // requests and limits must stay equal — that equality is what gives the pod Guaranteed QoS, so
        // reading one side from config and leaving the other hardcoded would silently demote it.
        expect(source.match(/"memory": JOB_MEMORY/g)).toHaveLength(2);
        expect(source.match(/"cpu": JOB_CPU/g)).toHaveLength(2);

        // The literals these replaced, so a revert is caught rather than merely un-asserted.
        expect(source).not.toContain('"memory": "16Gi"');
        expect(source).not.toContain('"cpu": "2000m"');
    });

    test("each handler default still equals the shipped config value", () => {
        // Behaviour preservation, and the reason this change needs no migration note: a Lambda whose
        // code predates these variables, and a deployment that has not tuned them, both produce the job
        // spec that was hardcoded before. Read out of the handler source because the fallback lives
        // there, not in any template.
        const source = realReadFileSync(HANDLER, "utf-8");
        const fallbackOf = (envVar: string) => {
            const match = source.match(
                new RegExp(`os\\.environ\\.get\\('${envVar}'\\)\\s*or\\s*'?([^')\\n]+)'?`)
            );
            expect(match).not.toBeNull();
            return match![1].trim().replace(/'$/, "");
        };
        expect(fallbackOf("EKS_JOB_MEMORY")).toBe(shipped().jobMemory);
        expect(fallbackOf("EKS_JOB_CPU")).toBe(shipped().jobCpu);
        expect(Number(fallbackOf("EKS_JOB_BACKOFF_LIMIT"))).toBe(shipped().jobBackoffLimit);
        expect(Number(fallbackOf("EKS_JOB_TTL_SECONDS_AFTER_FINISHED"))).toBe(
            shipped().jobTTLSecondsAfterFinished
        );
    });
});
