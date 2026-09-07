/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The RapidPipeline EKS cluster's Kubernetes API endpoint must not be reachable from the internet.
 *
 * The cluster shipped with `EndpointAccess.PUBLIC`, and the comment explaining it named the reason as
 * convenience: "PUBLIC only (not PUBLIC_AND_PRIVATE) forces Lambda to use NAT Gateway instead of VPC
 * endpoints". The `AwsSolutions-EKS1` Nag rule that exists to catch precisely this was suppressed with
 * the same convenience wording, so nothing surfaced it.
 *
 * Nothing outside VAMS ever calls that endpoint. Its only clients are this stack's own cluster-handler
 * and kubectl-provider functions and the node group, all of which run in the same private subnets — so a
 * public endpoint bought nothing and made the pipeline unusable in a VPC-isolated deployment.
 *
 * Asserted on the EMITTED resource rather than on the construct's props, because the two are not the
 * same claim: `EndpointAccess` is a CDK abstraction and what reaches AWS is
 * `resourcesVpcConfig.endpointPublicAccess` / `endpointPrivateAccess` on the cluster's custom resource.
 * The EKS1 warning count is asserted alongside it, so the suppression cannot quietly come back to hide a
 * regression.
 *
 * Three prerequisites are asserted too, because private access silently depends on all of them:
 *   * the cluster handler must be in the VPC — aws-cdk-lib says so outright, and without it the handler
 *     cannot reach the endpoint it just made private;
 *   * the VPC must have DNS support and DNS hostnames, which is how the endpoint's private hosted zone
 *     resolves. Without them the cluster deploys and then nothing can talk to it;
 *   * the pipeline Lambdas' security groups must be admitted on 443. This one was learned from a live
 *     deployment rather than from the source: a public endpoint is reached from outside the VPC, where
 *     no security group applies, so making the endpoint private moves the call onto the cluster's
 *     cross-account network interfaces and under their security groups for the first time. Neither of
 *     those groups admitted anything — the EKS-managed one allows only itself, the stack's own allows
 *     nothing — so every Kubernetes call ended in a connection timeout.
 *
 * The last of those is why the state machine is asserted here as well. The failure surfaced as
 * `States.Runtime` for a JSONPath "that could not be found", because RUN_JOB reported its 500 in a
 * response body — not as an invocation error, so no Catch saw it — and the next state read
 * `$.RunJobResult.Payload.body.k8sJobName` unguarded. That both misnamed the cause and bypassed
 * PipelineEnd, leaving the parent workflow's task token unreleased for hours.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate } from "../support/templateSynth";

/** Enables the EKS pipeline and the VPC it requires. Marketplace container URI is a placeholder. */
function enableEksPipeline(c: any) {
    c.app.useGlobalVpc.enabled = true;
    const eks = c.app.pipelines.useRapidPipeline.useEks;
    c.app.pipelines.useRapidPipeline.enabled = true;
    c.app.pipelines.useRapidPipeline.autoRegisterWithVAMS = false;
    eks.enabled = true;
    // The real config field. An earlier version of this harness set `containerUri`, which is
    // not a field at all, so the shipped placeholder stayed in place and the harness only
    // looked configured — hidden because this file shared a synth cache key with two others.
    eks.ecrContainerImageURI = "709825985650.dkr.ecr.us-east-1.amazonaws.com/vendor/product:0.0.1";
}

describe("RapidPipeline EKS cluster endpoint", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableEksPipeline,
            mutateKey: "eks-private-endpoint",
        });
    });

    test("the cluster IS in this synth", () => {
        // The control. Every assertion below is satisfied by a synth containing no cluster, and the EKS
        // pipeline ships disabled — so without this the suite would pass having checked nothing.
        expect(synth.resources.filter((r) => r.type === "Custom::AWSCDK-EKS-Cluster").length).toBe(
            1
        );
    });

    test("the Kubernetes API endpoint is private and not public", () => {
        for (const cluster of synth.resources.filter(
            (r) => r.type === "Custom::AWSCDK-EKS-Cluster"
        )) {
            const vpcConfig = (cluster.properties as any).Config?.resourcesVpcConfig ?? {};
            expect(vpcConfig.endpointPublicAccess).toBe(false);
            expect(vpcConfig.endpointPrivateAccess).toBe(true);
        }
    });

    test("no suppression is left hiding an EKS1 finding", () => {
        // The suppression is asserted absent from the SOURCE, and deliberately not by counting CDK Nag
        // findings on the synth: `test/support/templateSynth.ts` sets `enableCdkNag = false`, so no Nag
        // rule runs in this tier and `warnings` carries none of them. An earlier version of this test
        // also asserted the EKS1 warning count was zero and described that as proving the rule satisfied
        // on its own terms — measured, that array is empty for every input, so the assertion held
        // whatever the endpoint was configured to. Removed rather than left in, because a vacuous
        // assertion beside a real one makes the real one look better supported than it is.
        //
        // What carries the claim instead: the emitted `endpointPublicAccess`/`endpointPrivateAccess`
        // above, which is the property the rule checks, plus the absence of the suppression here so the
        // rule is not being silenced in a real `cdk synth`.
        const source = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../lib/nestedStacks/pipelines/multi/rapidPipelineEKS/rapidPipelineEKS-nestedStack.ts"
            ),
            "utf-8"
        );
        expect(source).not.toContain("AwsSolutions-EKS1");
    });

    test("the cluster handler runs inside the VPC, which private access requires", () => {
        // aws-cdk-lib: private endpoint access "requires ... placeClusterHandlerInVpc to be set to
        // true". Without it the handler that creates and updates the cluster cannot reach the endpoint,
        // and the failure appears as a stuck or failed stack update rather than as a synth error.
        const source = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../lib/nestedStacks/pipelines/multi/rapidPipelineEKS/constructs/rapidPipelineEKS-construct.ts"
            ),
            "utf-8"
        );
        expect(source).toContain("placeClusterHandlerInVpc: true");
        expect(source).toContain("eks.EndpointAccess.PRIVATE");
    });

    test("the pipeline security groups are admitted to the API endpoint on 443", () => {
        // Asserted on the emitted ingress rules rather than on the construct call, because what has to
        // be true is that the rule lands on a group the cluster's network interfaces actually carry —
        // and there are two of those (the stack's own group and the EKS-managed cluster group), only
        // one of which appears in the source.
        const clusterIngress = synth.ofType("AWS::EC2::SecurityGroupIngress").filter((r) => {
            const p = r.properties as any;
            const target = JSON.stringify(p.GroupId ?? "");
            return (
                p.FromPort === 443 &&
                p.ToPort === 443 &&
                p.IpProtocol === "tcp" &&
                p.SourceSecurityGroupId !== undefined &&
                (/EksCluster/i.test(target) || /ClusterSecurityGroupId/.test(target))
            );
        });

        expect(clusterIngress.length).toBeGreaterThan(0);

        // The source must be a security group the pipeline Lambdas are attached to — a rule sourced
        // from the cluster's own group (which is what already existed) would satisfy a looser check
        // while admitting nothing new.
        const pipelineSecurityGroupIds = new Set(
            synth
                .ofType("AWS::Lambda::Function")
                .flatMap((f) => (f.properties.VpcConfig?.SecurityGroupIds ?? []) as unknown[])
                .map((id) => SynthResult.flatten(id))
        );
        expect(pipelineSecurityGroupIds.size).toBeGreaterThan(0);

        const admittedSources = clusterIngress.map((r) =>
            SynthResult.flatten((r.properties as any).SourceSecurityGroupId)
        );
        expect(admittedSources.some((s) => pipelineSecurityGroupIds.has(s))).toBe(true);
    });

    test("a RUN_JOB error response is routed rather than read straight through", () => {
        // Read out of the emitted ASL: the guard is a state-machine shape, and the same TypeScript can
        // produce a chain that still reaches InitializeCounter first.
        const definition = Object.values(synth.templates)
            .flatMap((t: any) => Object.values(t.Resources ?? {}))
            .filter((r: any) => r.Type === "AWS::StepFunctions::StateMachine")
            .map((r: any) => SynthResult.flatten(r.Properties?.DefinitionString))
            .find((d) => d.includes('"RunJob"') && d.includes("InitializeCounter"));
        expect(definition).toBeDefined();

        const asl = JSON.parse(definition!);
        const states = asl.States;

        // RunJob must hand off to a Choice, not to the state that dereferences its output.
        const afterRunJob = states.RunJob?.Next;
        expect(afterRunJob).toBeDefined();
        expect(afterRunJob).not.toBe("InitializeCounter");
        expect(states[afterRunJob].Type).toBe("Choice");

        // The guard must be IsPresent on the exact path InitializeCounter reads. A value comparison
        // against an absent path is what raised States.Runtime, so a status-code check written that
        // way would reintroduce the same failure while looking like a fix.
        const guarded = JSON.stringify(states[afterRunJob].Choices);
        expect(guarded).toContain("$.RunJobResult.Payload.body.k8sJobName");
        expect(guarded).toContain("IsPresent");

        // And the failure branch has to reach PipelineEnd, which is what releases the parent
        // workflow's task token. A branch ending in Fail leaves the parent waiting out its timeout.
        const reaches = (from: string, target: string, seen = new Set<string>()): boolean => {
            if (from === target) return true;
            if (!from || seen.has(from) || !states[from]) return false;
            seen.add(from);
            const next: string[] = [
                states[from].Next,
                states[from].Default,
                ...(states[from].Choices ?? []).map((c: any) => c.Next),
            ].filter(Boolean);
            return next.some((n) => reaches(n, target, seen));
        };
        for (const choice of states[afterRunJob].Choices ?? []) {
            expect(reaches(choice.Next, "PipelineEnd")).toBe(true);
        }
        expect(reaches(states[afterRunJob].Default, "PipelineEnd")).toBe(true);
    });

    test("the VPC resolves DNS, which is how the private endpoint is reached", () => {
        // The other silent prerequisite. A VPC without DNS hostnames deploys the cluster and then
        // cannot resolve its endpoint's private hosted zone.
        const vpcs = synth.ofType("AWS::EC2::VPC");
        expect(vpcs.length).toBeGreaterThan(0);
        for (const vpc of vpcs) {
            expect(vpc.properties.EnableDnsSupport).toBe(true);
            expect(vpc.properties.EnableDnsHostnames).toBe(true);
        }
    });
});
