/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A pipeline whose compute runs in ISOLATED subnets must not pull public subnets and NAT into the VPC.
 *
 * The coordinate-transform pipeline's AWS Batch (Fargate) jobs are placed in the isolated subnets
 * (`pipelineBuilder-nestedStack.ts` passes `pipelineNetwork.isolatedSubnets.pipeline`), yet its flag was
 * listed in the VPC builder's public/private subnet condition. `subnetPrivateConfig` is
 * `PRIVATE_WITH_EGRESS` and the `ec2.Vpc` sets no `natGateways`, so CDK created one NAT gateway per
 * Availability Zone — roughly $66/month at two AZs, plus data processing, for subnets the pipeline's
 * ENIs never occupy. It was also in `needsEcsPrivate`, placing the ECS control-plane endpoint in those
 * same unused subnets (~$15/month more).
 *
 * The code followed the documented rule literally: both `backendPipelines/CLAUDE.md` step 9 and
 * `infra/lib/nestedStacks/pipelines/CLAUDE.md` said every Batch/ECS/Fargate pipeline goes into all three
 * VPC blocks. The rule was the defect — it did not distinguish isolated-subnet pipelines. Five peers
 * (3dBasic, CAD/mesh metadata extraction, Potree, 3D thumbnail, GenAI metadata labeling) run in isolated
 * subnets and appear in NEITHER block, so the pipeline was the sole inconsistency.
 *
 * Two of those peers are live proof the arrangement works: `preview-3d-thumbnail` and
 * `metadata-extraction-cad-mesh` executions completed successfully on a deployment with no public subnet,
 * no NAT gateway and no private ECS endpoint. That is why removing the flag is safe rather than merely
 * plausible — Fargate tasks need ECR, Amazon S3 and CloudWatch Logs, which the isolated-subnet endpoint
 * block supplies, not the ECS control-plane endpoint.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

/** Enable exactly one pipeline, and no other feature that would create public subnets. */
function onlyPipeline(flag: string) {
    return (c: any) => {
        c.app.useGlobalVpc.enabled = true;
        c.app.useGlobalVpc.addVpcEndpoints = true;
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

describe("coordinateTransform runs in isolated subnets only", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: onlyPipeline("useConversionCoordinateTransform"),
            mutateKey: "vpc-only-coordinate-transform",
        });
    });

    test("[control] the pipeline's own resources ARE in this synth", () => {
        // Every "absent" assertion below is satisfied by a synth where the pipeline was never enabled,
        // and this template ships it disabled. Its Fargate compute environment is the marker.
        const fargate = synth
            .ofType("AWS::Batch::ComputeEnvironment")
            .filter((e) => /FARGATE/i.test(JSON.stringify((e.properties as any).ComputeResources)));
        expect(fargate.length).toBeGreaterThan(0);
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

    test("the Batch and ECR endpoints it does need are still created", () => {
        // The other half: removing the flag from two blocks must not remove it from the isolated-subnet
        // endpoint block, or the Fargate tasks cannot pull their image and the pipeline silently stops
        // working — a far worse outcome than the wasted NAT gateway.
        const services = synth
            .ofType("AWS::EC2::VPCEndpoint")
            .map((e) => SynthResult.flatten((e.properties as any).ServiceName));
        expect(services.some((s) => /\.batch$/.test(s))).toBe(true);
        expect(services.some((s) => /\.ecr\.api$/.test(s))).toBe(true);
        expect(services.some((s) => /\.ecr\.dkr$/.test(s))).toBe(true);
        expect(services.some((s) => /\.s3$/.test(s) || /\.s3\./.test(s))).toBe(true);
        expect(services.some((s) => /\.logs$/.test(s))).toBe(true);
    });

    test("no ECS control-plane endpoint is created for it", () => {
        // Fargate tasks do not use com.amazonaws.<region>.ecs; it is the endpoint an EC2-launch-type
        // container instance's agent needs. Asserted so a future edit does not reintroduce it as a
        // "safe" addition — one endpoint ENI per AZ is a recurring charge.
        const services = synth
            .ofType("AWS::EC2::VPCEndpoint")
            .map((e) => SynthResult.flatten((e.properties as any).ServiceName));
        expect(services.filter((s) => /\.ecs$/.test(s))).toEqual([]);
    });
});

describe("a private-subnet pipeline still gets public subnets and NAT", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: onlyPipeline("useSplatToolbox"),
            mutateKey: "vpc-only-splat-toolbox",
        });
    });

    test("[control] the splat pipeline's resources ARE in this synth", () => {
        expect(synth.ofType("AWS::Batch::ComputeEnvironment").length).toBeGreaterThan(0);
    });

    test("NAT gateways are created", () => {
        // The positive control for the whole file. Without it, a change that stopped creating public
        // subnets for EVERY pipeline would satisfy the assertions above while breaking the four
        // pipelines that genuinely run in private subnets and need egress.
        expect(natGateways(synth).length).toBeGreaterThan(0);
    });

    test("public subnets are created", () => {
        expect(publicSubnets(synth).length).toBeGreaterThan(0);
    });

    test("the ECS control-plane endpoint IS created for it", () => {
        const services = synth
            .ofType("AWS::EC2::VPCEndpoint")
            .map((e) => SynthResult.flatten((e.properties as any).ServiceName));
        expect(services.some((s) => /\.ecs$/.test(s))).toBe(true);
    });
});
