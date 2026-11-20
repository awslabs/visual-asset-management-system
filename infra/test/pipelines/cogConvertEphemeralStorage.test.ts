/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The COG conversion Fargate job's size must cover a scene and its Web Mercator COG.
 *
 * The task volume holds the downloaded input and the COG the converter writes: the COG driver builds a
 * temporary file of about the output's size before the final one, and reprojecting into EPSG:3857 can
 * enlarge the pixel grid. The container refuses an input whose headroom (3.5x the input) does not fit, so
 * the largest Fargate volume (200 GiB) is used and the limit is the container's, not an `OSError` four hours
 * into a run. The 8 vCPU / 32 GiB task and `VAMS_TASK_MEMORY_MIB` (from which GDAL's block cache is sized,
 * because Fargate shows the process the micro-VM's memory) must agree.
 *
 * Asserted on the emitted `AWS::Batch::JobDefinition`, because that is what AWS Batch receives.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

const EPHEMERAL_GIB = 200;
const VCPUS = 8;
const MEMORY_MIB = 32768;

function cogConvertOnly(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.useGlobalVpc.addVpcEndpoints = true;
    c.app.pipelines.useCogConversion.enabled = true;
    c.app.pipelines.useCogConversion.autoRegisterWithVAMS = false;
}

function fargateJobDefinitions(synth: SynthResult) {
    return synth.ofType("AWS::Batch::JobDefinition").filter((jd) => {
        const capabilities = ((jd.properties as any).PlatformCapabilities ?? []) as string[];
        return capabilities.includes("FARGATE");
    });
}

describe("COG conversion Fargate job size", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: cogConvertOnly,
            mutateKey: "cog-convert-job-size",
        });
    });

    test("[control] the job definition IS emitted, and it is the only Fargate job in this synth", () => {
        expect(fargateJobDefinitions(synth)).toHaveLength(1);
    });

    test("the job declares the largest Fargate volume", () => {
        const container = (fargateJobDefinitions(synth)[0].properties as any).ContainerProperties;
        expect(container.EphemeralStorage.SizeInGiB).toBe(EPHEMERAL_GIB);
    });

    test("the job declares 8 vCPU and 32 GiB, a valid Fargate pair", () => {
        const container = (fargateJobDefinitions(synth)[0].properties as any).ContainerProperties;
        const resources = Object.fromEntries(
            container.ResourceRequirements.map((r: any) => [r.Type, r.Value])
        );
        expect(Number(resources.VCPU)).toBe(VCPUS);
        expect(Number(resources.MEMORY)).toBe(MEMORY_MIB);
    });

    test("the container is told the task memory it was given", () => {
        const container = (fargateJobDefinitions(synth)[0].properties as any).ContainerProperties;
        const env = Object.fromEntries(container.Environment.map((e: any) => [e.Name, e.Value]));
        expect(env.VAMS_TASK_MEMORY_MIB).toBe(String(MEMORY_MIB));
    });
});
