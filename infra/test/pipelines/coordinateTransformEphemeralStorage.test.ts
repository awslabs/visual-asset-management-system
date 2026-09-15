/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The coordinate-transform Fargate job's ephemeral storage must cover the transform's disk spill.
 *
 * The container spills every transformed point to the task's volume before writing an output, which is
 * what bounds its peak memory to one chunk. The volume therefore has to hold three things at once: the
 * downloaded input, one spill copy of the point payload, and every requested output format. At roughly
 * 32 bytes per spilled point an 800M-point cloud spills ~26 GB, on top of an ~8 GB LAZ input and, with
 * `outputFormats: ["laz","las","e57","ply"]`, ~80 GB of output. The previous 60 GiB did not cover that,
 * and nothing pinned the value: a `grep` for `ephemeral` over `infra/test/pipelines/*.ts` returned
 * nothing before this file existed, so a later edit could quietly take it back down and the failure
 * would appear as an `OSError` errno 28 inside a four-hour Batch job.
 *
 * Asserted on the emitted `AWS::Batch::JobDefinition`, because the volume the container gets is the one
 * AWS Batch receives. The construct's own default is asserted separately, from the source: it is shared
 * by the other four Fargate pipelines, none of which spills, so the two figures are deliberately
 * different and a change that collapsed them would go unnoticed here otherwise.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate } from "../support/templateSynth";

/** GiB the coordinate-transform job must declare. Mirrored in the pipeline's documentation page. */
const COORD_TRANSFORM_EPHEMERAL_GIB = 120;

/** Fargate's own bounds, from the prop's doc comment on `batch-fargate-pipeline.ts`. */
const FARGATE_MIN_GIB = 21;
const FARGATE_MAX_GIB = 200;

const CONSTRUCT_SOURCE = path.resolve(
    __dirname,
    "../../lib/nestedStacks/pipelines/conversion/coordinateTransform/constructs/coordinateTransform-construct.ts"
);

/** Enable coordinate transform. It ships disabled in all three templates. */
function coordinateTransformOnly(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.useGlobalVpc.addVpcEndpoints = true;
    c.app.pipelines.useConversionCoordinateTransform.enabled = true;
    if (c.app.pipelines.useConversionCoordinateTransform.autoRegisterWithVAMS !== undefined) {
        c.app.pipelines.useConversionCoordinateTransform.autoRegisterWithVAMS = false;
    }
}

/** Fargate job definitions, identified by the platform capability Batch receives. */
function fargateJobDefinitions(synth: SynthResult) {
    return synth.ofType("AWS::Batch::JobDefinition").filter((jd) => {
        const capabilities = ((jd.properties as any).PlatformCapabilities ?? []) as string[];
        return capabilities.includes("FARGATE");
    });
}

describe("coordinate transform Fargate ephemeral storage", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: coordinateTransformOnly,
            mutateKey: "coord-transform-ephemeral",
        });
    });

    test("[control] the coordinate transform job definition IS emitted in this synth", () => {
        // The pipeline ships disabled, so without the hybrid every assertion below is vacuous. Only
        // this pipeline is enabled, so exactly one Fargate job definition is expected — which also
        // means the size assertion cannot accidentally read a sibling pipeline's volume.
        expect(fargateJobDefinitions(synth)).toHaveLength(1);
    });

    test("the job declares the ephemeral storage the spill needs", () => {
        const jd = fargateJobDefinitions(synth)[0];
        const sizeGiB = (jd.properties as any).ContainerProperties?.EphemeralStorage?.SizeInGiB;
        expect(sizeGiB).toBe(COORD_TRANSFORM_EPHEMERAL_GIB);
    });

    test("the value stays inside what Fargate accepts", () => {
        // A figure outside 21-200 is rejected at job-definition registration, i.e. mid-deploy, so it is
        // worth catching at synth. Asserted on the emitted value rather than on the constant, so a
        // future change made in the construct rather than here is still covered.
        const jd = fargateJobDefinitions(synth)[0];
        const sizeGiB = (jd.properties as any).ContainerProperties?.EphemeralStorage?.SizeInGiB;
        expect(sizeGiB).toBeGreaterThanOrEqual(FARGATE_MIN_GIB);
        expect(sizeGiB).toBeLessThanOrEqual(FARGATE_MAX_GIB);
    });

    test("the construct supplies the figure explicitly rather than taking the shared default", () => {
        // The shared `BatchFargatePipelineConstruct` default is sized for pipelines that do NOT spill.
        // If this pipeline stopped passing its own value it would silently inherit that default, and the
        // template assertion above would then be describing the default rather than a decision.
        const source = fs.readFileSync(CONSTRUCT_SOURCE, "utf-8");
        expect(source).toMatch(
            new RegExp(`ephemeralStorageGiB:\\s*${COORD_TRANSFORM_EPHEMERAL_GIB}\\b`)
        );
    });
});
