/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier: every AWS Lambda function deployed from a container image declares the x86_64 architecture
 * (issue #513).
 *
 * The 3D Basic Conversion and CAD/Mesh Metadata Extraction pipelines are the only VAMS components that
 * ship as Lambda container images (`lambda.DockerImageFunction`). Their images are built for
 * `linux/amd64` twice over: `FROM --platform=linux/amd64` in each Dockerfile and `Platform.LINUX_AMD64`
 * on each CDK image asset. Lambda runs an image only on the architecture the function declares, and an
 * amd64 image on an arm64 function fails at the first invocation, not at deploy. The function default
 * is x86_64, so without an explicit setting the pairing is implicit; the builders set it, and this
 * suite fails if either function, or a future container image function, stops emitting it.
 *
 * ## Why a hybrid config
 *
 * `useConversionCadMeshMetadataExtraction` is disabled in every shipped template, so asserting against
 * the templates as shipped would never see that function. Both pipelines are switched on through a
 * keyed mutation, and a separate positive control proves both functions are emitted in each template
 * before the absence assertion is trusted.
 */

import {
    ALL_TEMPLATES,
    Resource,
    SynthResult,
    TemplateName,
    expectAbsent,
    synthTemplate,
} from "../support/templateSynth";

// Three full-app synths (one per shipped template).
jest.setTimeout(600_000);

/** The slice of the synth config this suite mutates. */
type PipelineFlags = { app: { pipelines: Record<string, { enabled: boolean }> } };

const enableLambdaImagePipelines = (c: PipelineFlags) => {
    c.app.pipelines.useConversion3dBasic.enabled = true;
    c.app.pipelines.useConversionCadMeshMetadataExtraction.enabled = true;
};

const synth = (name: TemplateName): SynthResult =>
    synthTemplate(name, {
        mutate: enableLambdaImagePipelines,
        mutateKey: "lambda-image-pipelines-enabled",
    });

/** `DockerImageFunction` emits `PackageType: Image` and no `Handler`, so select on the package type. */
const imageFunctions = (s: SynthResult): Resource[] =>
    s.where("AWS::Lambda::Function", (r) => r.properties.PackageType === "Image");

describe("#513: Lambda container image functions declare x86_64", () => {
    test.each(ALL_TEMPLATES)("%s emits both conversion container image functions", (name) => {
        // Positive control for the assertion below. Each function is created directly on its own
        // nested stack, so its logical id is the construct id plus CDK's hash suffix.
        const ids = imageFunctions(synth(name)).map((r) => r.logicalId);
        expect(ids.filter((id) => id.startsWith("vamsExecute3dBasicConversion"))).toHaveLength(1);
        expect(
            ids.filter((id) => id.startsWith("vamsExecuteMeshCadMetadataExtractionConversion"))
        ).toHaveLength(1);
    });

    test.each(ALL_TEMPLATES)(
        "%s: every PackageType Image function has Architectures [x86_64]",
        (name) => {
            const functions = imageFunctions(synth(name));
            const offenders = functions
                .filter(
                    (r) => JSON.stringify(r.properties.Architectures) !== JSON.stringify(["x86_64"])
                )
                .map(
                    (r) =>
                        `${r.stack}/${r.logicalId}: Architectures=${JSON.stringify(
                            r.properties.Architectures
                        )}`
                );
            expectAbsent(
                "a container image Lambda function without Architectures [x86_64]",
                offenders,
                {
                    description: "container image Lambda functions emitted",
                    count: functions.length,
                }
            );
        }
    );
});
