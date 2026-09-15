/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * No Fargate Batch job definition may name a container user, so the image's own `USER` is what runs.
 *
 * `BatchFargatePipelineConstruct` hardcoded `user: "root"` on its `EcsFargateContainerDefinition`. That
 * value becomes `ContainerProperties.User`, which REPLACES the user the image declares — so the
 * coordinateTransform Dockerfile's `USER coordxform` was inert at runtime while the Dockerfile and its
 * guard test in `containerBuildSources.test.ts` were both green. Removing the override is what makes
 * every one of those Dockerfiles take effect, and this file is what stops it coming back: a
 * reintroduced override would re-neutralise four images at once and break no other assertion.
 *
 * The construct is shared by five job definitions — conversion/coordinateTransform,
 * genAi/metadata3dLabeling (whose image declares no `USER`, so it keeps running as root either way),
 * preview/3dThumbnail, and both preview/pcPotreeViewer images — so the assertion is written over all of
 * them rather than over a named subset.
 *
 * Asserted on the emitted `AWS::Batch::JobDefinition`, because the user AWS Batch applies is the one it
 * receives. `ContainerProperties.User` is absent (rather than `"root"`) when no override is set, which is
 * what makes the absence assertion below meaningful.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate } from "../support/templateSynth";

/**
 * Enable the four pipelines that build Fargate Batch jobs.
 *
 * Duplicated from `fargateBatchAttemptDuration.test.ts` rather than shared: each suite owns its own
 * mutation and `mutateKey`, and a shared mutator would couple the two synth caches together.
 */
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

describe("Fargate Batch container user", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: fargatePipelines,
            mutateKey: "fargate-container-user",
        });
    });

    test("[control] Fargate job definitions ARE emitted in this synth", () => {
        // All four pipelines ship disabled, so the absence assertion below is otherwise satisfied by a
        // template that emitted nothing to inspect. Five are expected: coordinate transform, metadata
        // labeling, the 3D thumbnail, and PDAL plus Potree from the point-cloud viewer.
        expect(fargateJobDefinitions(synth).length).toBeGreaterThanOrEqual(5);
    });

    test("[control] the emitted job definitions carry ContainerProperties at all", () => {
        // Second control, and the one that matters for an absence assertion on a nested property: if
        // `ContainerProperties` were itself missing, "no User anywhere" would pass for the wrong reason.
        const withoutContainerProps = fargateJobDefinitions(synth)
            .filter((jd) => !(jd.properties as any).ContainerProperties)
            .map((jd) => `${jd.stack}/${jd.logicalId}`);
        expect(withoutContainerProps).toEqual([]);
    });

    test("no Fargate job definition names a container user", () => {
        // Any value is a regression, not only "root": the point is that the image decides. A future
        // pipeline that genuinely needs a named account should declare it in its Dockerfile.
        const overridden = fargateJobDefinitions(synth)
            .map((jd) => ({
                id: `${jd.stack}/${jd.logicalId}`,
                user: (jd.properties as any).ContainerProperties?.User,
            }))
            .filter((jd) => jd.user !== undefined)
            .map((jd) => `${jd.id} User=${JSON.stringify(jd.user)}`);
        expect(overridden).toEqual([]);
    });

    test("the shared construct sets no user on its container definition", () => {
        // The source-level half. The template assertion above covers the pipelines this synth enables;
        // this one covers the construct itself, so a new caller cannot reintroduce the override through a
        // pipeline no template turns on.
        const source = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../lib/nestedStacks/pipelines/constructs/batch-fargate-pipeline.ts"
            ),
            "utf-8"
        );
        // Control on the read: the container definition this assertion is about must be in the file.
        expect(source).toContain("new batch.EcsFargateContainerDefinition(");
        expect(source).not.toMatch(/^\s*user:/m);
    });
});
