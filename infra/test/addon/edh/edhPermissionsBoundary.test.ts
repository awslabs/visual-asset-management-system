/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `environments.aws.PermissionBoundaryArn` (infra/cdk.json) bounds every IAM role the app creates
 * (#431, test/security/iamPermissionsBoundary.test.ts). The shipped templates disable the EDH
 * Workstations add-on, so that test never sees the add-on's roles. This synth turns the add-on on with
 * session bootstrap, containers (one image build and one job definition) and AMI builds, the widest set
 * of add-on roles, and requires that every role in the add-on's nested template, and in the rest of
 * the app, carries the configured boundary.
 */

import cdkJson from "../../../cdk.json";
import { ACCOUNT, SynthResult, synthTemplate } from "../../support/templateSynth";
import { enableEdhAmiBuilds, enableEdhContainers, inEdhStack } from "./support/edhSynth";

// A full-app synth is ~20-30 s.
jest.setTimeout(600_000);

const BOUNDARY = "arn:aws:iam::123456789012:policy/vams-test-boundary";

/** cdk.json's `environments` with only the boundary ARN replaced. */
function environmentsWithBoundary(arn: string): Record<string, unknown> {
    const environments = JSON.parse(JSON.stringify((cdkJson as any).context.environments));
    environments.aws.PermissionBoundaryArn = arn;
    return { environments };
}

let synth: SynthResult | undefined;
const boundedSynth = (): SynthResult =>
    (synth ??= synthTemplate("commercial", {
        mutate: (c: any) => {
            // The containers hybrid re-applies the add-on base and leaves the AMI builds block alone,
            // so it comes second.
            enableEdhAmiBuilds(c);
            enableEdhContainers(c);
            const edh = c.app.addons.useEdhWorkstations;
            edh.sessionBootstrap.enabled = true;
            edh.sessionBootstrap.workstationInstanceRoleArns = [
                `arn:${c.env.partition}:iam::${ACCOUNT}:role/EdhDesktopInstanceRole`,
            ];
            // A tracked Dockerfile directory, so the source asset stages on a fresh checkout.
            edh.containers.buildAndPushImages = [
                {
                    name: "sim-worker",
                    dockerfileDir: "backendPipelines/conversion/coordinateTransform/container",
                },
            ];
            edh.containers.jobDefinitions = [
                { name: "edh-sim-train", imageRef: "sim-worker", vcpus: 4, memory: 16384 },
            ];
        },
        mutateKey: "edhPermissionsBoundaryBootstrapContainersAmiBuilds",
        context: environmentsWithBoundary(BOUNDARY),
    }));

const unboundedRoles = (s: SynthResult, onlyEdh: boolean): string[] =>
    s
        .ofType("AWS::IAM::Role")
        .filter((r) => !onlyEdh || inEdhStack(r))
        .filter((r) => r.properties.PermissionsBoundary !== BOUNDARY)
        .map(
            (r) => `${r.stack}/${r.logicalId}: ${JSON.stringify(r.properties.PermissionsBoundary)}`
        );

describe("IAM permissions boundary over the EDH Workstations add-on", () => {
    test("[control] the add-on's nested template emits its Lambda, image build and AMI build roles", () => {
        const edhRoles = boundedSynth()
            .ofType("AWS::IAM::Role")
            .filter(inEdhStack)
            .map((r) => r.logicalId);
        expect(edhRoles.length).toBeGreaterThan(10);
        expect(edhRoles.some((id) => /CodeBuild/i.test(id))).toBe(true);
        expect(edhRoles.some((id) => /AmiBuildInstanceRole/.test(id))).toBe(true);
    });

    test("every role in the add-on's nested template carries the configured boundary", () => {
        expect(unboundedRoles(boundedSynth(), true)).toEqual([]);
    });

    test("every other role of the app with the add-on on carries it too", () => {
        expect(unboundedRoles(boundedSynth(), false)).toEqual([]);
    });
});
