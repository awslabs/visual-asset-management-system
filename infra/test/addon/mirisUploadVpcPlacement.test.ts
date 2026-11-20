/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Miris upload pipeline runs its Fargate jobs in the private subnets, which reach Miris over the
 * NAT gateway, and needs the Batch and ECR endpoints but no ECS endpoint. Its vamsSchema registration
 * invokes the ApiBuilder2 import Lambda, so the add-on stack depends on ApiBuilder2 only while it is on.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";
import { mirisStaticWithUpload } from "./mirisUploadHarness";

/** The logical IDs the root template's add-on nested stack resource lists in its DependsOn. */
function addonStackDependsOn(synth: SynthResult): string[] {
    const addonStacks = synth.where("AWS::CloudFormation::Stack", (r) =>
        r.logicalId.startsWith("AddonBuilderNestedStack")
    );
    expect(addonStacks).toHaveLength(1);
    return ([] as string[]).concat(addonStacks[0].raw.DependsOn ?? []);
}

const onApiBuilder2 = (synth: SynthResult) =>
    addonStackDependsOn(synth).filter((id) => id.startsWith("ApiBuilder2NestedStack"));

describe("the Miris upload pipeline runs Fargate in private subnets", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: mirisStaticWithUpload,
            mutateKey: "miris-static-upload",
        });
    });

    test("the pipeline is a child of the add-on stack, with its execute Lambda output", () => {
        const jobDefinitions = synth
            .ofType("AWS::Batch::JobDefinition")
            .filter((jd) =>
                /MirisUploadJob_/.test(JSON.stringify(jd.properties.JobDefinitionName))
            );
        expect(jobDefinitions).toHaveLength(1);
        expect(jobDefinitions[0].stack).toMatch(/AddonBuilder.*MirisUploadBuilderNestedStack/);

        const outputs = Object.values(synth.templates as Record<string, any>)
            .flatMap((tpl) => Object.entries<any>(tpl.Outputs ?? {}))
            .filter(([id]) => id.startsWith("VamsPipelineExecuteLambdaFunctionOutput"));
        // No core pipeline is enabled, so the one output is the add-on's.
        expect(outputs).toHaveLength(1);
        expect(JSON.stringify(outputs[0][1].Value)).toMatch(/AddonBuilder/);
    });

    test("its Fargate compute is in private subnets behind NAT, with Batch and ECR endpoints", () => {
        const environments = synth
            .ofType("AWS::Batch::ComputeEnvironment")
            .filter((e) => /FARGATE/i.test(JSON.stringify((e.properties as any).ComputeResources)));
        expect(environments).toHaveLength(1);
        // Subnets cross into the nested stack as parameters named after the VPC builder's subnets.
        const subnets = JSON.stringify(environments[0].properties.ComputeResources.Subnets);
        expect(subnets).toMatch(/[Pp]rivate/);
        expect(subnets).not.toMatch(/[Ii]solated/);
        expect(synth.countOfType("AWS::EC2::NatGateway")).toBeGreaterThan(0);

        const services = synth
            .ofType("AWS::EC2::VPCEndpoint")
            .map((e) => SynthResult.flatten((e.properties as any).ServiceName));
        for (const service of [/\.batch$/, /\.ecr\.api$/, /\.ecr\.dkr$/]) {
            expect(services.some((s) => service.test(s))).toBe(true);
        }
        expect(services.filter((s) => /\.ecs$/.test(s))).toEqual([]);
    });

    test("the add-on stack depends on ApiBuilder2", () => {
        expect(onApiBuilder2(synth)).toHaveLength(1);
    });
});

test("with the Miris add-on off, the add-on stack does not depend on ApiBuilder2", () => {
    const synth = synthTemplate("commercial");
    // Control: the add-on stack's other dependencies are read from the same DependsOn.
    expect(
        addonStackDependsOn(synth).some((id) => id.startsWith("StorageResourcesBuilderNestedStack"))
    ).toBe(true);
    expect(onApiBuilder2(synth)).toEqual([]);
});
