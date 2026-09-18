/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Video SOP/BOM pipeline's Fargate jobs run in ISOLATED subnets and reach every service they call
 * through interface endpoints — so enabling it must create no public subnet and no NAT gateway, and MUST
 * create the endpoints the container calls: Batch, ECR (API and Docker), Amazon S3, CloudWatch Logs, Step
 * Functions (the task-token callback), Amazon Transcribe and Amazon Bedrock Runtime.
 *
 * The two Bedrock arms are the point of the file. Bedrock Runtime's endpoint used to be gated on
 * `useForAllLambdas && useGenAiMetadata3dLabeling.enabled`, a Lambda-centric condition; the container has
 * no NAT, so with that gate every Bedrock call from this pipeline would resolve the public hostname and
 * hang until the connect timeout. The gate is now the OR of two named booleans, and the control and the
 * negative arm pin that widening it for the container did not loosen the Lambda half: the labeling
 * pipeline still gets the endpoint only when its Lambda is in the VPC.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

/** Enable exactly one pipeline, and no other feature that would create public subnets. */
function onlyPipeline(flag: string, useForAllLambdas: boolean) {
    return (c: any) => {
        c.app.useGlobalVpc.enabled = true;
        c.app.useGlobalVpc.addVpcEndpoints = true;
        c.app.useGlobalVpc.useForAllLambdas = useForAllLambdas;
        // An ALB in a public subnet satisfies the same condition, which would mask the pipeline's own
        // contribution entirely.
        c.app.useAlb.enabled = false;
        c.app.useCloudFront.enabled = true;
        for (const name of Object.keys(c.app.pipelines)) {
            const entry = c.app.pipelines[name];
            if (entry && typeof entry === "object" && "enabled" in entry) {
                entry.enabled = name === flag;
                if (entry.autoRegisterWithVAMS !== undefined) {
                    entry.autoRegisterWithVAMS = false;
                }
            }
        }
        // RapidPipeline's two sub-flags are nested rather than a plain `enabled`.
        if (c.app.pipelines.useRapidPipeline) {
            c.app.pipelines.useRapidPipeline.useEcs.enabled = false;
            c.app.pipelines.useRapidPipeline.useEks.enabled = false;
        }
    };
}

function natGateways(synth: SynthResult) {
    return synth.ofType("AWS::EC2::NatGateway").map((n) => n.logicalId);
}

/** Subnets whose emitted tag marks them public, which is what CDK writes for a public subnet. */
function publicSubnets(synth: SynthResult) {
    return synth.ofType("AWS::EC2::Subnet").filter((s) => {
        const tags = ((s.properties as any).Tags ?? []) as Array<{ Key: string; Value: unknown }>;
        return tags.some((t) => t.Key === "aws-cdk:subnet-type" && String(t.Value) === "Public");
    });
}

/** Flattened service names of every VPC endpoint in the assembly. */
function endpointServices(synth: SynthResult): string[] {
    return synth
        .ofType("AWS::EC2::VPCEndpoint")
        .map((e) => SynthResult.flatten((e.properties as any).ServiceName));
}

/** The Fargate compute environments — the marker that a Fargate pipeline's resources are in the synth. */
function fargateComputeEnvironments(synth: SynthResult) {
    return synth
        .ofType("AWS::Batch::ComputeEnvironment")
        .filter((e) => /FARGATE/i.test(JSON.stringify((e.properties as any).ComputeResources)));
}

describe("the video SOP/BOM pipeline runs in isolated subnets and reaches its services by endpoint", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: onlyPipeline("useGenAiVideoSopBom", false),
            mutateKey: "vpc-only-video-sop-bom",
        });
    });

    test("[control] the pipeline's own resources ARE in this synth", () => {
        // Every "absent" assertion below is satisfied by a synth where the pipeline was never enabled,
        // and this template ships it disabled. Its Fargate compute environment is the marker.
        expect(fargateComputeEnvironments(synth).length).toBeGreaterThan(0);
    });

    test("[control] isolated subnets exist, so the pipeline has somewhere to run", () => {
        expect(synth.ofType("AWS::EC2::Subnet").length).toBeGreaterThan(0);
    });

    test("no NAT gateway is created", () => {
        expect(natGateways(synth)).toEqual([]);
    });

    test("no public subnet is created", () => {
        expect(publicSubnets(synth).map((s) => s.logicalId)).toEqual([]);
    });

    test("every service the container calls has an interface or gateway endpoint", () => {
        const services = endpointServices(synth);
        // Image pull and job control.
        expect(services.some((s) => /\.batch$/.test(s))).toBe(true);
        expect(services.some((s) => /\.ecr\.api$/.test(s))).toBe(true);
        expect(services.some((s) => /\.ecr\.dkr$/.test(s))).toBe(true);
        expect(services.some((s) => /\.s3$/.test(s) || /\.s3\./.test(s))).toBe(true);
        expect(services.some((s) => /\.logs$/.test(s))).toBe(true);
        // The task-token callback that completes the .sync task.
        expect(services.some((s) => /\.states$/.test(s))).toBe(true);
        // Transcription and model inference, called with lambdas OUTSIDE the VPC.
        expect(services.filter((s) => /\.transcribe$/.test(s))).toHaveLength(1);
        expect(services.filter((s) => /\.bedrock-runtime$/.test(s))).toHaveLength(1);
    });

    test("no ECS control-plane endpoint is created for it", () => {
        // Fargate tasks do not use com.amazonaws.<region>.ecs; it is the endpoint an EC2-launch-type
        // container instance's agent needs. One endpoint ENI per AZ is a recurring charge.
        expect(endpointServices(synth).filter((s) => /\.ecs$/.test(s))).toEqual([]);
    });
});

describe("[control] the labeling pipeline with in-VPC lambdas keeps Bedrock Runtime and gets no Transcribe", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: onlyPipeline("useGenAiMetadata3dLabeling", true),
            mutateKey: "vpc-only-metadata-labeling-lambdas-in-vpc",
        });
    });

    test("[control] the labeling pipeline's resources ARE in this synth", () => {
        expect(fargateComputeEnvironments(synth).length).toBeGreaterThan(0);
    });

    test("Bedrock Runtime is present and Transcribe is absent", () => {
        const services = endpointServices(synth);
        expect(services.filter((s) => /\.bedrock-runtime$/.test(s))).toHaveLength(1);
        expect(services.filter((s) => /\.transcribe$/.test(s))).toEqual([]);
    });
});

describe("the widened Bedrock gate leaves the labeling pipeline alone when lambdas are outside the VPC", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: onlyPipeline("useGenAiMetadata3dLabeling", false),
            mutateKey: "vpc-only-metadata-labeling-lambdas-outside-vpc",
        });
    });

    test("[control] the labeling pipeline's resources ARE in this synth", () => {
        expect(fargateComputeEnvironments(synth).length).toBeGreaterThan(0);
    });

    test("neither Bedrock Runtime nor Transcribe is created", () => {
        // The labeling Lambda is outside the VPC here and reaches Bedrock over the public endpoint; an
        // interface endpoint would bill one ENI per AZ with no consumer.
        const services = endpointServices(synth);
        expect(services.filter((s) => /\.bedrock-runtime$/.test(s))).toEqual([]);
        expect(services.filter((s) => /\.transcribe$/.test(s))).toEqual([]);
    });
});
